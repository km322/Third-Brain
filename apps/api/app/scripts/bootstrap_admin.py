"""Create the first admin account for a fresh, self-hosted Third Brain.

This is the PRODUCTION bootstrap - not the demo seeder (``app.scripts.seed``). It creates a
single OWNER user and their organization, and optionally mints a first API key, then prints
the credentials to stdout exactly once. Nothing here is ever written to the logs.

    python -m app.scripts.bootstrap_admin

Configuration comes from the environment, with argparse flags taking precedence:

    ADMIN_EMAIL      (required)  the owner's email address
    ADMIN_PASSWORD   (optional)  omit to generate a strong random password (printed once)
    ADMIN_ORG_NAME   (optional)  organization name (default "My Company")
    BOOTSTRAP_API_KEY=1          also mint a first admin API key (or pass --with-api-key)

Idempotent: if a user with ``ADMIN_EMAIL`` already exists the command prints an
"already bootstrapped" line and exits 0 without creating or printing anything.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import generate_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import auth_service

DEFAULT_ORG_NAME = "My Company"
DEFAULT_OWNER_NAME = "Admin"
API_KEY_NAME = "Bootstrap admin key"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
"""Matches the RegisterRequest/LoginRequest password policy (min 8, max 128).

The upper bound matters: the login route rejects >128 chars with a 422, so a longer
password would create an admin that can never sign in.
"""

_GENERATED_PASSWORD_BYTES = 24
"""Bytes of entropy for a generated password; token_urlsafe(24) yields a 32-char secret."""

_EMAIL_ADAPTER: TypeAdapter[str] = TypeAdapter(EmailStr)
_TRUTHY = {"1", "true", "yes", "on"}


class BootstrapError(Exception):
    """A user-facing configuration error (bad email, missing password policy, ...)."""


@dataclass(frozen=True)
class BootstrapConfig:
    """Validated bootstrap inputs.

    A ``password`` of ``None`` means "generate a strong random one".
    """

    email: str
    password: str | None
    org_name: str
    with_api_key: bool


@dataclass
class BootstrapResult:
    """Outcome of a bootstrap run.

    ``password`` is only set when a password was generated (so it can be shown once); an
    operator-supplied password is never echoed back.
    """

    email: str
    org_name: str
    created: bool
    password: str | None = None
    api_key: str | None = None
    sign_in_url: str | None = None


def _env_flag(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in _TRUTHY


def _normalize_email(raw: str) -> str:
    """Validate ``raw`` as an email address and return it lowercased for storage."""
    try:
        _EMAIL_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise BootstrapError(f"ADMIN_EMAIL is not a valid email address: {raw!r}") from exc
    return raw.strip().lower()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.bootstrap_admin",
        description="Create the first admin account for a self-hosted Third Brain.",
    )
    parser.add_argument("--email", help="Owner email (overrides ADMIN_EMAIL)")
    parser.add_argument(
        "--password",
        help="Owner password (overrides ADMIN_PASSWORD; omit to generate a strong one)",
    )
    parser.add_argument(
        "--org-name", dest="org_name", help="Organization name (overrides ADMIN_ORG_NAME)"
    )
    parser.add_argument(
        "--with-api-key",
        dest="with_api_key",
        action="store_true",
        default=None,
        help="Also mint a first admin API key (or set BOOTSTRAP_API_KEY=1)",
    )
    return parser


def resolve_config(argv: list[str], environ: Mapping[str, str]) -> BootstrapConfig:
    """Merge CLI flags over environment variables into a validated config.

    Raises :class:`BootstrapError` on missing/invalid input.
    """
    args = _build_parser().parse_args(argv)

    email_raw = args.email or environ.get("ADMIN_EMAIL")
    if not email_raw or not email_raw.strip():
        raise BootstrapError(
            "ADMIN_EMAIL is required. Set the ADMIN_EMAIL environment variable or pass --email."
        )
    email = _normalize_email(email_raw)

    password_raw = args.password if args.password is not None else environ.get("ADMIN_PASSWORD")
    password = password_raw if password_raw and password_raw.strip() else None
    if password is not None and not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise BootstrapError(
            f"ADMIN_PASSWORD must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} "
            "characters (omit it to generate a strong random password)."
        )

    org_name_raw = args.org_name or environ.get("ADMIN_ORG_NAME") or DEFAULT_ORG_NAME
    org_name = org_name_raw.strip() or DEFAULT_ORG_NAME

    with_api_key = (
        args.with_api_key
        if args.with_api_key is not None
        else _env_flag(environ, "BOOTSTRAP_API_KEY")
    )

    return BootstrapConfig(
        email=email, password=password, org_name=org_name, with_api_key=with_api_key
    )


def _generate_password() -> str:
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


async def execute(
    config: BootstrapConfig,
    *,
    session_factory: Callable[[], Any] = SessionLocal,
    out: Callable[[str], None] = print,
) -> BootstrapResult:
    """Create the owner + org (idempotently) and print the credentials once.

    Returns a :class:`BootstrapResult`; ``created`` is ``False`` when an account with this
    email already existed (nothing is written and no secret is printed).
    """
    generated = config.password is None
    password = config.password or _generate_password()

    async with session_factory() as db:
        existing = await auth_service.get_user_by_email(db, config.email)
        if existing is not None:
            out(f'Admin "{config.email}" already exists - already bootstrapped, nothing to do.')
            return BootstrapResult(email=config.email, org_name=config.org_name, created=False)

        user = User(
            email=config.email,
            hashed_password=hash_password(password),
            full_name=DEFAULT_OWNER_NAME,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        org, _ = await auth_service.create_org_with_owner(db, config.org_name, user)

        api_key_secret: str | None = None
        if config.with_api_key:
            full_key, prefix, hashed = generate_api_key()
            db.add(
                ApiKey(
                    org_id=org.id,
                    created_by_id=user.id,
                    acts_as_user_id=user.id,
                    name=API_KEY_NAME,
                    key_prefix=prefix,
                    hashed_key=hashed,
                    scopes=["*"],
                )
            )
            api_key_secret = full_key

        await db.commit()

    result = BootstrapResult(
        email=config.email,
        org_name=config.org_name,
        created=True,
        password=password if generated else None,
        api_key=api_key_secret,
        sign_in_url=settings.APP_BASE_URL,
    )
    _print_credentials(result, out)
    return result


def _print_credentials(result: BootstrapResult, out: Callable[[str], None]) -> None:
    rule = "=" * 64
    out("")
    out(rule)
    out("  Third Brain - first admin account created")
    out(rule)
    if result.sign_in_url:
        out(f"  Sign in:   {result.sign_in_url}")
    out(f'  Email:     "{result.email}"')
    out(f'  Org:       "{result.org_name}"')
    if result.password:
        out(f'  Password:  "{result.password}"')
        out("             ^ generated - store it now; it is not shown again.")
    if result.api_key:
        out(f"  API key:   {result.api_key}")
        out("             ^ store it now; it is not shown again.")
    out(rule)
    out("")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code (2 = bad config, 1 = failed run).

    An unexpected failure prints only the exception type, never ``str(exc)``: a SQLAlchemy
    error renders the failing INSERT and its bound parameters (email, bcrypt hash), which
    must not leak.
    """
    try:
        config = resolve_config(sys.argv[1:] if argv is None else argv, os.environ)
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        asyncio.run(execute(config))
    except Exception as exc:  # pragma: no cover - surfaced to the operator, never logged
        print(
            f"error: bootstrap failed ({type(exc).__name__}). "
            "Check the database is reachable and migrated, then retry.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
