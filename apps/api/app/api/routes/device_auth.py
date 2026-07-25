"""Device authorization: connect a CLI from the terminal, approve in the browser.

The flow (an admin-gated cousin of the OAuth device-code grant):

1. ``POST /device-auth`` (unauthenticated) - the CLI receives a human ``user_code`` to
   read out and a secret ``device_code`` to poll with.
2. An **admin session** reviews the request on ``/activate`` and approves it, which mints
   an :class:`ApiKey` exactly like ``POST /api-keys`` (same scope + acts-as validation).
3. ``POST /device-auth/token`` - the CLI's poll turns ``authorization_pending`` into a
   one-shot handover of the key's plaintext; the row is atomically flipped to CONSUMED
   so the secret can never be redeemed twice.

Only hashes of device codes are stored. Between approval and redemption the key's
plaintext waits Fernet-encrypted on the row and is nulled at handover. Expiry is lazy:
every read resolves ``expires_at`` through :func:`resolve_status`, and a periodic worker
sweep (:func:`app.workers.tasks.expire_device_authorizations_task`) is the backstop for a
flow that is approved but then abandoned before the CLI redeems it - so no orphaned key or
encrypted plaintext lingers past ``expires_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Route modules may import from each other (both are wired independently by the API
# router); the admin-session gate and the acts-as escalation guard live with API keys.
from app.api.routes.api_keys import require_admin_session, validate_acts_as_user
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, client_ip, enforce_login_rate_limit
from app.core.security import decrypt_secret, encrypt_secret, generate_api_key, hash_api_key
from app.models.api_key import ApiKey
from app.models.device_auth import DeviceAuthorization
from app.models.enums import AuditAction, DeviceAuthStatus
from app.models.organization import Organization
from app.models.user import User
from app.schemas.api_key import ApiKeyRead
from app.schemas.common import Message
from app.schemas.device_auth import (
    DEFAULT_DEVICE_SCOPES,
    DEVICE_GRANTABLE_SCOPES,
    DeviceAuthApprove,
    DeviceAuthDeny,
    DeviceAuthPendingRead,
    DeviceAuthStart,
    DeviceAuthStarted,
    DeviceAuthTokenRequest,
    DeviceAuthTokenResponse,
)
from app.services.device_auth import (
    generate_device_code,
    generate_user_code,
    normalize_user_code,
    resolve_status,
)
from app.services.metering import record_audit

router = APIRouter(prefix="/device-auth", tags=["device_auth"])

# The whole flow - approval AND redemption - must finish inside this window.
_EXPIRES_MINUTES = 15
# Suggested seconds between CLI polls of /device-auth/token. Kept above the window that
# the shared login limiter (10 requests / 60s) allows, so a well-behaved poller does not
# throttle itself mid-flow (60 / 7 ~= 8.5 polls per window < 10).
_POLL_INTERVAL_SECONDS = 7
# Collisions on a fresh 8-char code among *pending* rows are ~impossible; the retry
# loop (backed by the partial unique index) is defense in depth, not a hot path.
_USER_CODE_ATTEMPTS = 5


async def _mint_user_code(db: AsyncSession) -> str:
    for _ in range(_USER_CODE_ATTEMPTS):
        code = generate_user_code()
        taken = (
            await db.execute(
                select(DeviceAuthorization.id).where(
                    DeviceAuthorization.user_code == code,
                    DeviceAuthorization.status == DeviceAuthStatus.PENDING,
                )
            )
        ).first()
        if taken is None:
            return code
    raise HTTPException(  # pragma: no cover - astronomically unlikely
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not allocate a device code; try again",
    )


async def _lazy_expire(db: AsyncSession, row: DeviceAuthorization) -> None:
    """Persist a lazy PENDING/APPROVED -> EXPIRED transition (commits).

    An approved-but-unredeemed row still holds the encrypted key plaintext and a live
    ApiKey nobody will ever receive - drop the ciphertext and revoke the orphan key so
    an expired flow leaves no usable credential behind. The transition is a guarded
    rowcount CAS on the status we observed, so it can never clobber a concurrent approve
    (which flips PENDING -> APPROVED): if the row moved under us the UPDATE matches zero
    rows and we leave it to the other writer.
    """
    claimed = await db.execute(
        update(DeviceAuthorization)
        .where(
            DeviceAuthorization.id == row.id,
            DeviceAuthorization.status == row.status,
        )
        .values(status=DeviceAuthStatus.EXPIRED, encrypted_secret=None)
    )
    if claimed.rowcount == 1 and row.api_key_id is not None:
        key = await db.get(ApiKey, row.api_key_id)
        if key is not None:
            key.revoked = True
    await db.commit()


@router.post("", response_model=DeviceAuthStarted)
async def start_device_auth(
    payload: DeviceAuthStart,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DeviceAuthStarted:
    """Begin the flow. Unauthenticated and org-less: the org binds at approval time."""
    # No caller identity exists yet, so the brute-force guard keys on the client IP alone.
    await enforce_login_rate_limit(request, "device-auth:start")

    user_code = await _mint_user_code(db)
    device_code, prefix, hashed = generate_device_code()
    expires_at = datetime.now(UTC) + timedelta(minutes=_EXPIRES_MINUTES)
    row = DeviceAuthorization(
        client_name=(payload.client_name or "").strip() or "Unnamed CLI",
        user_code=user_code,
        device_code_prefix=prefix,
        hashed_device_code=hashed,
        requested_scopes=list(DEFAULT_DEVICE_SCOPES),
        status=DeviceAuthStatus.PENDING,
        expires_at=expires_at,
        interval_seconds=_POLL_INTERVAL_SECONDS,
    )
    db.add(row)
    await db.commit()
    return DeviceAuthStarted(
        device_code=device_code,
        user_code=user_code,
        verification_uri=f"{settings.APP_BASE_URL}/activate",
        verification_uri_complete=f"{settings.APP_BASE_URL}/activate?code={user_code}",
        expires_in=_EXPIRES_MINUTES * 60,
        interval=_POLL_INTERVAL_SECONDS,
    )


@router.post("/token", response_model=DeviceAuthTokenResponse, response_model_exclude_none=True)
async def poll_device_auth(
    payload: DeviceAuthTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DeviceAuthTokenResponse:
    """The CLI's poll. Hands over the minted API key's plaintext exactly once."""
    # Keyed by the presented device code, so polling one flow cannot starve another
    # from the same host (mirrors the refresh-token limiter). A throttled poll is a
    # plain 429 the CLI treats as "slow down".
    await enforce_login_rate_limit(request, payload.device_code)

    row = (
        await db.execute(
            select(DeviceAuthorization).where(
                DeviceAuthorization.hashed_device_code == hash_api_key(payload.device_code)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid device code")

    now = datetime.now(UTC)
    effective = resolve_status(row.status, row.expires_at, now)
    if effective == DeviceAuthStatus.EXPIRED:
        if row.status != DeviceAuthStatus.EXPIRED:
            await _lazy_expire(db, row)
        return DeviceAuthTokenResponse(status="expired")
    if effective == DeviceAuthStatus.PENDING:
        return DeviceAuthTokenResponse(status="authorization_pending")
    if effective == DeviceAuthStatus.DENIED:
        return DeviceAuthTokenResponse(status="denied")
    if effective == DeviceAuthStatus.CONSUMED:
        raise HTTPException(status_code=400, detail="Device authorization already redeemed")

    # APPROVED: atomic one-shot claim. Decrypt the held plaintext BEFORE the consume CAS,
    # so a decryption failure (rotated SECRET_KEY / corrupt blob) does not burn the claim
    # and strand a live key nobody ever received - on failure we expire the row (which
    # revokes the orphan key) and tell the CLI to reconnect. The status CAS then makes
    # concurrent polls safe: only the request that flips APPROVED -> CONSUMED (in this same
    # transaction) returns the plaintext; everyone else sees the row as already redeemed.
    ciphertext = row.encrypted_secret
    key = await db.get(ApiKey, row.api_key_id) if row.api_key_id else None
    if ciphertext is None or key is None:  # pragma: no cover - approval always sets both
        raise HTTPException(status_code=400, detail="Device authorization already redeemed")
    try:
        plaintext = decrypt_secret(ciphertext)
    except Exception:  # rotated key / corrupt blob - do not consume; revoke and expire
        await _lazy_expire(db, row)
        return DeviceAuthTokenResponse(status="expired")
    claimed = await db.execute(
        update(DeviceAuthorization)
        .where(
            DeviceAuthorization.id == row.id,
            DeviceAuthorization.status == DeviceAuthStatus.APPROVED,
        )
        .values(status=DeviceAuthStatus.CONSUMED, encrypted_secret=None)
    )
    if claimed.rowcount != 1:
        raise HTTPException(status_code=400, detail="Device authorization already redeemed")
    org = await db.get(Organization, row.org_id) if row.org_id else None
    acts_as = await db.get(User, key.acts_as_user_id) if key.acts_as_user_id else None
    await db.commit()
    return DeviceAuthTokenResponse(
        status="approved",
        api_key=plaintext,
        key_prefix=key.key_prefix,
        scopes=list(key.scopes or []),
        org_name=org.name if org else None,
        acts_as_email=acts_as.email if acts_as else None,
    )


async def _get_pending(
    db: AsyncSession, user_code: str, *, error_status: int
) -> DeviceAuthorization:
    """Load a PENDING, unexpired row by user code, applying lazy expiry."""
    row = (
        await db.execute(
            select(DeviceAuthorization).where(
                DeviceAuthorization.user_code == normalize_user_code(user_code),
                DeviceAuthorization.status == DeviceAuthStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=error_status, detail="Device code not found or expired")
    if row.expires_at < datetime.now(UTC):
        await _lazy_expire(db, row)
        raise HTTPException(status_code=error_status, detail="Device code not found or expired")
    return row


@router.get("/pending/{user_code}", response_model=DeviceAuthPendingRead)
async def get_pending_device_auth(
    user_code: str,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> DeviceAuthorization:
    """What the /activate page shows the approving admin. 404 for unknown/expired."""
    return await _get_pending(db, user_code, error_status=status.HTTP_404_NOT_FOUND)


@router.post("/approve", response_model=ApiKeyRead)
async def approve_device_auth(
    payload: DeviceAuthApprove,
    request: Request,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyRead:
    """Approve a pending request: mint the API key and bind it to the approver's org.

    Mirrors ``POST /api-keys`` (admin session, acts-as outrank guard) with one extra
    restriction: a terminal-initiated key may only carry read/write/search/ingest -
    never ``manage`` or ``*``.
    """
    scopes = list(payload.scopes) if payload.scopes is not None else list(DEFAULT_DEVICE_SCOPES)
    if not scopes:
        raise HTTPException(status_code=400, detail="Select at least one scope")
    unknown = [s for s in scopes if s not in DEVICE_GRANTABLE_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Scope(s) not grantable to a CLI key: {', '.join(sorted(set(unknown)))}. "
            f"Allowed: {', '.join(sorted(DEVICE_GRANTABLE_SCOPES))}",
        )

    # Default the key to act as the approver; an explicit member still may not outrank them.
    acts_as_user_id = payload.acts_as_user_id or ctx.user_id
    await validate_acts_as_user(db, ctx, acts_as_user_id)

    row = await _get_pending(db, payload.user_code, error_status=status.HTTP_400_BAD_REQUEST)

    full_key, prefix, hashed = generate_api_key()
    key = ApiKey(
        org_id=ctx.org_id,
        created_by_id=ctx.user_id,
        acts_as_user_id=acts_as_user_id,
        name=payload.name or f"CLI - {row.client_name}",
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=scopes,
        rate_limit_per_minute=settings.DEFAULT_RATE_LIMIT_PER_MINUTE,
    )
    db.add(key)
    await db.flush()

    # CAS PENDING -> APPROVED inside this transaction, so a concurrent approve (or deny)
    # cannot double-mint: the loser's flush is rolled back with its HTTPException.
    claimed = await db.execute(
        update(DeviceAuthorization)
        .where(
            DeviceAuthorization.id == row.id,
            DeviceAuthorization.status == DeviceAuthStatus.PENDING,
        )
        .values(
            status=DeviceAuthStatus.APPROVED,
            org_id=ctx.org_id,
            approved_by_id=ctx.user_id,
            api_key_id=key.id,
            encrypted_secret=encrypt_secret(full_key),
        )
    )
    if claimed.rowcount != 1:
        raise HTTPException(status_code=400, detail="Device code is no longer pending")

    ip = client_ip(request)
    ua = request.headers.get("user-agent")
    await record_audit(
        db,
        ctx,
        AuditAction.API_KEY_CREATED.value,
        resource_type="api_key",
        resource_id=key.id,
        ip_address=ip,
        user_agent=ua,
        meta={"name": key.name, "scopes": key.scopes, "via": "device_auth"},
    )
    await record_audit(
        db,
        ctx,
        AuditAction.DEVICE_AUTH_APPROVED.value,
        resource_type="device_authorization",
        resource_id=row.id,
        ip_address=ip,
        user_agent=ua,
        meta={"client_name": row.client_name, "scopes": scopes},
    )
    await db.commit()
    await db.refresh(key)
    return ApiKeyRead.model_validate(key)


@router.post("/deny", response_model=Message)
async def deny_device_auth(
    payload: DeviceAuthDeny,
    request: Request,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> Message:
    """Deny a pending request; the CLI's next poll sees ``denied``."""
    row = await _get_pending(db, payload.user_code, error_status=status.HTTP_400_BAD_REQUEST)
    claimed = await db.execute(
        update(DeviceAuthorization)
        .where(
            DeviceAuthorization.id == row.id,
            DeviceAuthorization.status == DeviceAuthStatus.PENDING,
        )
        .values(status=DeviceAuthStatus.DENIED)
    )
    if claimed.rowcount != 1:
        raise HTTPException(status_code=400, detail="Device code is no longer pending")
    await record_audit(
        db,
        ctx,
        AuditAction.DEVICE_AUTH_DENIED.value,
        resource_type="device_authorization",
        resource_id=row.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta={"client_name": row.client_name},
    )
    await db.commit()
    return Message(detail="Device authorization denied")
