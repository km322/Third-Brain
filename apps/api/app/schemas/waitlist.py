"""Request/response schemas for the public pre-launch waitlist."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class WaitlistJoinRequest(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    # Which surface the signup came from ("landing", "pricing", ...); attribution only.
    source: str | None = Field(default=None, max_length=64)
    # Cloudflare Turnstile response token, verified server-side when a secret is configured.
    turnstile_token: str | None = Field(default=None, max_length=2048)
    # Honeypot: a hidden field real users never fill. A non-empty value marks a bot; the
    # endpoint silently accepts (200) without persisting so the bot sees no difference.
    company_website: str | None = Field(default=None, max_length=255)


class WaitlistJoinResponse(BaseModel):
    """Returned for every accepted submission.

    Deliberately identical whether the address was newly added or already present, so the
    endpoint can't be used to enumerate who is on the list.
    """

    status: str = "ok"


class WaitlistStats(BaseModel):
    count: int
