"""Transactional email with a pluggable, offline-testable backend.

Mirrors the LLM/web philosophy: the default ``stub`` provider captures messages in-process
(never sends), so invite/notification flows are fully testable; ``console`` writes them to
the log; and ``smtp`` delivers via the SMTP_* settings. Tests read :func:`outbox`.

Only ``smtp`` actually delivers. The other two log at WARNING saying so, because a message
that silently goes nowhere - an org invite, say - looks identical to a delivered one from
the caller's side. Callers that mint a one-time link (see ``POST /invites``) should also
return it, so an operator on the default config can still hand it over.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CapturedEmail:
    to: str
    subject: str
    text: str


_OUTBOX: list[CapturedEmail] = []


def outbox() -> list[CapturedEmail]:
    """All captured emails (stub provider). Newest last."""
    return list(_OUTBOX)


def clear_outbox() -> None:
    _OUTBOX.clear()


def _send_smtp(to: str, subject: str, text: str) -> None:  # pragma: no cover - needs a server
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    host = settings.SMTP_HOST or "localhost"
    with smtplib.SMTP(host, settings.SMTP_PORT, timeout=15) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        server.send_message(msg)


async def send_email(to: str, subject: str, text: str) -> None:
    """Send (or capture) a plain-text email via the configured provider."""
    provider = settings.EMAIL_PROVIDER
    if provider == "stub":
        _OUTBOX.append(CapturedEmail(to=to, subject=subject, text=text))
        logger.warning(
            "email_not_delivered",
            to=to,
            subject=subject,
            provider=provider,
            reason="EMAIL_PROVIDER=stub captures mail in-process; set EMAIL_PROVIDER=smtp to send",
        )
        return
    if provider == "console":
        # The operator asked for the message in the log, so render the body too - otherwise
        # a link-bearing mail (an invite) is unrecoverable and the provider is a no-op.
        logger.warning(
            "email_not_delivered",
            to=to,
            subject=subject,
            provider=provider,
            body=text,
            reason="EMAIL_PROVIDER=console renders to the log; set EMAIL_PROVIDER=smtp to send",
        )
        return
    await asyncio.to_thread(_send_smtp, to, subject, text)
    logger.info("email_sent", to=to, subject=subject)
