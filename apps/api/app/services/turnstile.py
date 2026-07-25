"""Server-side Cloudflare Turnstile verification.

Turnstile is Cloudflare's privacy-friendly CAPTCHA. The browser widget produces a
short-lived token which we verify **server-side** by calling the ``siteverify`` endpoint
with our secret key - a token is single-use and bound to our site, so verifying it here
stops a bot from POSTing straight to the API and skipping the widget.

Verification is a no-op (returns ``True``) when no secret key is configured, so the app
runs with zero setup in development, CI and tests. It is only enforced once an operator
sets ``TURNSTILE_SECRET_KEY``.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT = httpx.Timeout(5.0)


async def verify_turnstile(token: str | None, remote_ip: str | None = None) -> bool:
    """Return whether ``token`` is a valid Turnstile response.

    - No secret configured: verification is disabled, always ``True``.
    - Secret configured but no token: ``False`` (the widget did not run / was skipped).
    - Secret configured with a token: ``True`` only if Cloudflare confirms it.

    Fails **closed** on a verification error or Cloudflare outage: a bot-protection control
    that fell open on any network blip would be trivially bypassable by inducing one. The
    caller surfaces a friendly "please try again" on ``False``.
    """
    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        return True
    if not token:
        return False

    data = {"secret": secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_SITEVERIFY_URL, data=data)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # network error, timeout, non-2xx, bad JSON
        logger.warning("turnstile_verify_error", error=str(exc))
        return False

    success = bool(body.get("success"))
    if not success:
        # ``error-codes`` is metadata (e.g. "invalid-input-response"), safe to log.
        logger.info("turnstile_verify_failed", error_codes=body.get("error-codes"))
    return success
