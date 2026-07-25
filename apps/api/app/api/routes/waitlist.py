"""Public pre-launch waitlist.

Two unauthenticated endpoints used by the marketing site:

- ``POST /waitlist``      - join the waitlist (email + optional name/company).
- ``GET  /waitlist/stats`` - total signups, for light social proof on the form.

These are the only public *write* endpoints outside auth, so they defend themselves:
a hidden honeypot field, a Redis rate limit (fail-open) and optional Cloudflare Turnstile
verification. Submissions are idempotent per (lower-cased) email.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.waitlist import WaitlistEntry
from app.schemas.waitlist import WaitlistJoinRequest, WaitlistJoinResponse, WaitlistStats
from app.services.turnstile import verify_turnstile

logger = get_logger(__name__)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

_RATE_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(request: Request, email: str) -> None:
    """Fixed-window limiter keyed on (email, client IP). Fails OPEN on a Redis outage.

    Keyed on both the (lower-cased) email and the IP - mirroring the login limiter
    (:func:`app.core.deps.enforce_login_rate_limit`) - rather than on IP alone. In the shipped
    production topology the API sits behind a reverse proxy on an internal network, so
    ``request.client.host`` is the proxy's single IP for every visitor; an IP-only bucket
    would collapse to one global counter and a handful of requests would 429 the whole public
    waitlist. Including the email keeps the throttle per-signup, so distinct visitors never
    share a bucket. Turnstile + the honeypot remain the primary bot defenses.
    """
    ip = _client_ip(request)
    ident = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
    try:
        redis = get_redis()
        window = int(datetime.now(UTC).timestamp()) // _RATE_WINDOW_SECONDS
        bucket = f"rl:waitlist:{ident}:{ip}:{window}"
        current = await redis.incr(bucket)
        if current == 1:
            await redis.expire(bucket, _RATE_WINDOW_SECONDS)
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Waitlist rate limiter unavailable (%s); allowing request", exc)
        return
    if current > settings.WAITLIST_RATE_LIMIT_PER_MINUTE:
        logger.warning(
            "waitlist_rate_limit_exceeded", limit=settings.WAITLIST_RATE_LIMIT_PER_MINUTE
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a minute and try again.",
        )


@router.post("", response_model=WaitlistJoinResponse)
async def join_waitlist(
    payload: WaitlistJoinRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WaitlistJoinResponse:
    """Add an email to the waitlist. Idempotent: re-submitting the same address is a no-op."""
    if not settings.WAITLIST_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waitlist is closed")

    # Silently swallow honeypot hits: a bot that filled the hidden field gets a 200 and no
    # persistence, so it can't distinguish success from rejection and won't retry differently.
    if payload.company_website:
        logger.info("waitlist_honeypot_tripped")
        return WaitlistJoinResponse()

    email = payload.email.strip().lower()
    await _enforce_rate_limit(request, email)

    if not await verify_turnstile(payload.turnstile_token, _client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't verify you're human. Please complete the check and try again.",
        )

    existing = (
        await db.execute(select(WaitlistEntry.id).where(WaitlistEntry.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return WaitlistJoinResponse()

    entry = WaitlistEntry(
        email=email,
        name=(payload.name or None),
        company=(payload.company or None),
        source=(payload.source or None),
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent signup of the same address; the unique index held,
        # so the address is on the list either way - treat as success.
        await db.rollback()
        return WaitlistJoinResponse()

    logger.info("waitlist_signup", source=entry.source)
    return WaitlistJoinResponse()


@router.get("/stats", response_model=WaitlistStats)
async def waitlist_stats(db: AsyncSession = Depends(get_db)) -> WaitlistStats:
    """Total number of signups (a single count) for social proof on the form."""
    count = (await db.execute(select(func.count()).select_from(WaitlistEntry))).scalar_one()
    return WaitlistStats(count=int(count))
