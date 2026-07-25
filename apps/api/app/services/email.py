"""Transactional email with a pluggable, offline-testable backend.

Mirrors the LLM/web philosophy: the default ``stub`` provider captures messages in-process
(never sends), so invite/notification flows are fully testable; ``console`` logs them; and
``smtp`` delivers via the SMTP_* settings. Tests read :func:`outbox`.
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
        logger.info("email_captured", to=to, subject=subject)
        return
    if provider == "console":
        logger.info("email_console", to=to, subject=subject)
        return
    await asyncio.to_thread(_send_smtp, to, subject, text)
    logger.info("email_sent", to=to, subject=subject)
