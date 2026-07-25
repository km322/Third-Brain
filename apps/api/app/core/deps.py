"""FastAPI dependencies: authentication, org context and authorization.

Two authentication paths converge on a single :class:`AuthContext`:

* **Session (JWT)** - used by the dashboard. The access token carries ``sub`` (user id)
  and ``org`` (active organization id).
* **API key** - used by programmatic clients, the OpenAI-compatible endpoint and MCP.
  A key belongs to an org, carries ``scopes`` and may ``act_as`` a user for ACL
  resolution.

Every downstream route depends on ``get_auth_context`` (or a stricter variant) so that
org isolation and role checks are enforced uniformly.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt
import structlog
from fastapi import Depends, Header, HTTPException, Request, status
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import decode_token, hash_api_key
from app.models.api_key import ApiKey
from app.models.enums import MembershipStatus, OrgRole
from app.models.user import Membership, User

logger = get_logger(__name__)

_ROLE_RANK = {OrgRole.VIEWER: 0, OrgRole.EDITOR: 1, OrgRole.ADMIN: 2, OrgRole.OWNER: 3}

# Only refresh ``ApiKey.last_used_at`` when it is this stale, so authenticating a busy key
# does not incur a write on every request.
_LAST_USED_THROTTLE = timedelta(minutes=5)


@dataclass
class AuthContext:
    """The authenticated caller, resolved to an org + effective role."""

    org_id: uuid.UUID
    org_role: OrgRole
    user: User | None = None
    api_key: ApiKey | None = None
    scopes: list[str] = field(default_factory=list)

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def is_api_key(self) -> bool:
        return self.api_key is not None

    @property
    def is_admin(self) -> bool:
        return self.org_role in (OrgRole.OWNER, OrgRole.ADMIN)

    def has_scope(self, scope: str) -> bool:
        # Session users act with their role; scope gating applies only to API keys.
        if self.api_key is None:
            return True
        return "*" in self.scopes or scope in self.scopes


def role_at_least(have: OrgRole, need: OrgRole) -> bool:
    return _ROLE_RANK[have] >= _ROLE_RANK[need]


def client_ip(request: Request) -> str | None:
    """The caller's IP for audit records, or None when unavailable."""
    return request.client.host if request.client else None


def ensure_session_not_revoked(payload: dict, user: User) -> None:
    """Raise 401 when a token's ``ver`` no longer matches ``user.token_version``.

    Bumping ``token_version`` on a credential event (password change/reset) revokes every
    token minted before it. Enforced identically on access tokens (every request) and on
    refresh via this one helper, so the two enforcement sites can never drift apart.
    """
    if payload.get("ver") != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")


def _extract_credential(
    request: Request, authorization: str | None, x_api_key: str | None
) -> tuple[str, str]:
    """Return (kind, token) where kind is 'apikey' or 'jwt'."""
    if x_api_key:
        return "apikey", x_api_key.strip()
    if authorization:
        parts = authorization.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 else parts[0].strip()
        if token.startswith("tb_"):
            return "apikey", token
        return "jwt", token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def _auth_from_jwt(token: str, db: AsyncSession) -> AuthContext:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")

    user_id = payload.get("sub")
    org_id = payload.get("org")
    user = None
    if user_id:
        try:
            user_uuid = uuid.UUID(user_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token subject") from exc
        user = await db.get(User, user_uuid)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    ensure_session_not_revoked(payload, user)
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization in token")
    try:
        org_uuid = uuid.UUID(org_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid organization in token") from exc

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == org_uuid,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    if membership.status != MembershipStatus.ACTIVE:
        # Enforced on every request so suspension takes effect immediately - even for
        # access tokens issued before the membership was suspended (or still only invited).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your membership of this organization is not active",
        )

    return AuthContext(org_id=membership.org_id, org_role=membership.role, user=user, scopes=["*"])


def _role_from_scopes(scopes: list[str]) -> OrgRole:
    if "manage" in scopes or "*" in scopes:
        return OrgRole.ADMIN
    if "ingest" in scopes or "write" in scopes:
        return OrgRole.EDITOR
    return OrgRole.VIEWER


async def _record_key_use(db: AsyncSession, api_key: ApiKey, now: datetime) -> None:
    """Throttled best-effort refresh of ``last_used_at`` (never breaks auth).

    Committed on its own here (auth runs before the route touches the session), so admins
    auditing key hygiene see a real "last used" instead of a permanent ``null``.
    """
    if api_key.last_used_at is not None and now - api_key.last_used_at < _LAST_USED_THROTTLE:
        return
    try:
        api_key.last_used_at = now
        await db.commit()
    except Exception as exc:  # pragma: no cover - never let bookkeeping break auth
        logger.warning("Failed to update API key last_used_at: %s", exc)
        await db.rollback()


async def _auth_from_api_key(raw_key: str, db: AsyncSession) -> AuthContext:
    hashed = hash_api_key(raw_key)
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.hashed_key == hashed))
    ).scalar_one_or_none()
    if api_key is None or api_key.revoked:
        raise HTTPException(status_code=401, detail="Invalid API key")
    now = datetime.now(UTC)
    if api_key.expires_at and api_key.expires_at < now:
        raise HTTPException(status_code=401, detail="API key expired")
    await _record_key_use(db, api_key, now)

    # Effective role: from the impersonated user if set, else derived from scopes.
    user: User | None = None
    org_role = _role_from_scopes(api_key.scopes)
    if api_key.acts_as_user_id:
        user = await db.get(User, api_key.acts_as_user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="The user this API key acts as is inactive",
            )
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.user_id == api_key.acts_as_user_id,
                    Membership.org_id == api_key.org_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The user this API key acts as is not an active member of the organization",
            )
        org_role = membership.role

    return AuthContext(
        org_id=api_key.org_id,
        org_role=org_role,
        user=user,
        api_key=api_key,
        scopes=list(api_key.scopes or []),
    )


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthContext:
    kind, token = _extract_credential(request, authorization, x_api_key)
    try:
        if kind == "apikey":
            ctx = await _auth_from_api_key(token, db)
        else:
            ctx = await _auth_from_jwt(token, db)
    except HTTPException as exc:
        # A presented credential was rejected. Log it as a security event (metadata only,
        # never the token) so credential stuffing / API-key probing is detectable.
        if exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            # Field is named ``auth_kind`` (not ``credential``) so the secret-redaction
            # backstop does not censor this non-secret credential type.
            logger.warning("auth_failed", auth_kind=kind, status=exc.status_code, reason=exc.detail)
        raise
    _bind_observability_context(ctx)
    return ctx


def _bind_observability_context(ctx: AuthContext) -> None:
    """Stamp the resolved caller onto log context and the current server span."""
    fields: dict[str, str] = {
        "org_id": str(ctx.org_id),
        "auth": "api_key" if ctx.is_api_key else "jwt",
    }
    if ctx.user_id is not None:
        fields["user_id"] = str(ctx.user_id)
    if ctx.api_key is not None:
        fields["api_key_id"] = str(ctx.api_key.id)
    structlog.contextvars.bind_contextvars(**fields)

    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("app.org_id", str(ctx.org_id))
        if ctx.user_id is not None:
            span.set_attribute("enduser.id", str(ctx.user_id))


async def get_session_context(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Restrict to human (dashboard) sessions."""
    if ctx.is_api_key:
        raise HTTPException(status_code=403, detail="This endpoint requires a user session")
    return ctx


def require_role(min_role: OrgRole):
    async def _dep(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not role_at_least(ctx.org_role, min_role):
            # org_id/user_id/api_key_id are already bound on the log context by auth.
            logger.warning(
                "permission_denied",
                check="role",
                required=min_role.value,
                actual=ctx.org_role.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires org role '{min_role.value}' or higher",
            )
        return ctx

    return _dep


def require_scope(*scopes: str):
    async def _dep(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        for scope in scopes:
            if not ctx.has_scope(scope):
                logger.warning("permission_denied", check="scope", required=scope)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API key missing required scope '{scope}'",
                )
        return ctx

    return _dep


def require_any_scope(*scopes: str):
    """Pass when the API key holds ANY of ``scopes`` (session/wildcard keys always pass).

    Use for capability classes where several scopes are equivalent, e.g. reading knowledge
    (``read`` or ``search``) or mutating it (``write`` or ``ingest``). This gates API-key
    callers only; human sessions carry an implicit ``*`` and are unaffected.
    """

    async def _dep(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not any(ctx.has_scope(scope) for scope in scopes):
            required = " or ".join(f"'{s}'" for s in scopes)
            logger.warning("permission_denied", check="scope", required="|".join(scopes))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing a required scope ({required})",
            )
        return ctx

    return _dep


# Capability-scope policy for the knowledge surface, enforced on API-key callers (human
# sessions carry an implicit ``*`` and always pass). Writing requires a write/ingest scope;
# reading requires any recognized capability (write implies read), so an empty-scoped key can
# neither read nor write - closing the gap where scopes were a no-op on the read surface.
WRITE_SCOPES = ("write", "ingest")
READ_SCOPES = ("read", "search", "write", "ingest", "manage")


def require_read_scope():
    """Dependency: the API key may read knowledge content."""
    return require_any_scope(*READ_SCOPES)


def require_write_scope():
    """Dependency: the API key may create/modify/delete knowledge."""
    return require_any_scope(*WRITE_SCOPES)


# A brute-force / credential-stuffing guard for the unauthenticated auth endpoints, which
# carry no API key and so are not covered by ``enforce_rate_limit``. Keyed by the presented
# identifier (email) and client IP so one attacker cannot both hammer a single account and
# spray many accounts from one host.
_LOGIN_RATE_LIMIT_PER_MINUTE = 10
_LOGIN_RATE_WINDOW_SECONDS = 60


def _login_rate_bucket(request: Request, identifier: str) -> str:
    """The fixed-window Redis key throttling ``identifier`` for this client IP."""
    client_ip = request.client.host if request.client else "unknown"
    ident = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()[:16]
    window = int(datetime.now(UTC).timestamp()) // _LOGIN_RATE_WINDOW_SECONDS
    return f"rl:auth:{ident}:{client_ip}:{window}"


def _raise_login_rate_limited() -> None:
    logger.warning("login_rate_limit_exceeded", limit=_LOGIN_RATE_LIMIT_PER_MINUTE)
    # Seconds until the fixed window rolls over, so a polling client (the CLI device
    # flow) can back off exactly long enough rather than guessing.
    retry_after = _LOGIN_RATE_WINDOW_SECONDS - (
        int(datetime.now(UTC).timestamp()) % _LOGIN_RATE_WINDOW_SECONDS
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait a minute and try again.",
        headers={"Retry-After": str(retry_after)},
    )


async def enforce_login_rate_limit(request: Request, identifier: str) -> None:
    """Throttle repeated auth attempts per (identifier, client IP).

    Fails OPEN on a Redis outage - a limiter backend being down must not lock every user
    out of logging in - matching the API-key limiter's philosophy.
    """
    try:
        redis = get_redis()
        bucket = _login_rate_bucket(request, identifier)
        current = await redis.incr(bucket)
        if current == 1:
            await redis.expire(bucket, _LOGIN_RATE_WINDOW_SECONDS)
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Login rate limiter unavailable (%s); allowing request", exc)
        return
    if current > _LOGIN_RATE_LIMIT_PER_MINUTE:
        _raise_login_rate_limited()


async def check_login_rate_limit(request: Request, identifier: str) -> None:
    """Reject (429) a caller already over the miss budget WITHOUT counting this request.

    Lets an unauthenticated endpoint consult the limiter BEFORE doing any per-request
    work (e.g. a DB probe), so a throttled scanner stops driving that work entirely;
    only actual misses feed the counter, via ``enforce_login_rate_limit``. Fails OPEN
    on a Redis outage, matching ``enforce_login_rate_limit``.
    """
    try:
        redis = get_redis()
        current = int(await redis.get(_login_rate_bucket(request, identifier)) or 0)
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Login rate limiter unavailable (%s); allowing request", exc)
        return
    if current > _LOGIN_RATE_LIMIT_PER_MINUTE:
        _raise_login_rate_limited()


async def enforce_rate_limit(ctx: AuthContext) -> None:
    """Sliding-window-ish per-principal limiter using a fixed 60s Redis bucket.

    Covers BOTH programmatic callers (per API key, at that key's configured budget) and
    dashboard sessions (per user, at ``SESSION_RATE_LIMIT_PER_MINUTE``). Sessions must be
    metered too: several of the endpoints guarded here spend real provider tokens, so an
    unmetered signed-in caller could bill the operator without limit.

    Fails OPEN on a Redis outage: a limiter backend being down must not turn every
    request into a 500. This matches the MCP surface, which already catches and degrades
    gracefully; the alternative (propagating the raw ConnectionError) took down the whole
    REST/`/v1` surface whenever Redis blipped.
    """
    if ctx.api_key is not None:
        principal = f"key:{ctx.api_key.id}"
        limit = ctx.api_key.rate_limit_per_minute
    elif ctx.user_id is not None:
        principal = f"user:{ctx.user_id}"
        limit = settings.SESSION_RATE_LIMIT_PER_MINUTE
    else:  # pragma: no cover - every authenticated context has a key or a user
        return
    try:
        redis = get_redis()
        bucket = f"rl:{principal}:{int(datetime.now(UTC).timestamp()) // 60}"
        current = await redis.incr(bucket)
        if current == 1:
            await redis.expire(bucket, 60)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Rate limiter unavailable (%s); allowing request", exc)
        return
    if current > limit:
        logger.warning("rate_limit_exceeded", limit=limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )
