"""Rotate the shared password for the seeded demo accounts, to take a public demo private.

The demo seeder (``app.scripts.seed``) creates three Acme Inc. accounts that share one
password (randomly generated unless ``DEMO_PASSWORD`` was set at seed time). A demo whose
password may have leaked - or that was seeded by an older release with a documented default -
needs its password rotated in place; that is what this does. It re-points the three demo
accounts at a new secret so the old password no longer works.

    DEMO_PASSWORD='<a strong secret>' python -m app.scripts.rotate_demo_password

The new password comes from ``DEMO_PASSWORD`` (or ``--password``); the command refuses to run
without one. It never prints the password, and is safe to re-run. If the demo was seeded with
custom ``DEMO_*_EMAIL`` addresses, run this with the SAME values set - the accounts are found
by their seeded emails.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User
from app.scripts.seed import (
    ADMIN_EMAIL,
    ENGINEER_EMAIL,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    VIEWER_EMAIL,
)

DEMO_EMAILS = (ADMIN_EMAIL, ENGINEER_EMAIL, VIEWER_EMAIL)


class RotateError(Exception):
    """A user-facing configuration error (missing/invalid password)."""


def resolve_password(argv: list[str], environ: Mapping[str, str]) -> str:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.rotate_demo_password",
        description="Rotate the shared password of the seeded demo accounts.",
    )
    parser.add_argument(
        "--password", help="New shared demo password (overrides the DEMO_PASSWORD env var)"
    )
    args = parser.parse_args(argv)

    raw = args.password if args.password is not None else environ.get("DEMO_PASSWORD")
    password = raw.strip() if raw else ""
    if not password:
        raise RotateError(
            "Set a new secret in DEMO_PASSWORD (or pass --password), e.g.\n"
            '  DEMO_PASSWORD="$(openssl rand -base64 18)" '
            "python -m app.scripts.rotate_demo_password"
        )
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise RotateError(
            f"DEMO_PASSWORD must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} "
            "characters."
        )
    return password


async def execute(
    password: str,
    *,
    session_factory: Callable[[], Any] = SessionLocal,
    out: Callable[[str], None] = print,
) -> int:
    """Set every demo account's password to ``password``. Returns the number rotated."""
    hashed = hash_password(password)
    async with session_factory() as db:
        users = (await db.execute(select(User).where(User.email.in_(DEMO_EMAILS)))).scalars().all()
        for user in users:
            user.hashed_password = hashed
        await db.commit()
        found = {user.email for user in users}

    for email in DEMO_EMAILS:
        out(f"  - {email}: {'rotated' if email in found else 'not found (skipped)'}")
    rotated = len(found)
    if rotated == len(DEMO_EMAILS):
        out("\nDemo password rotated. The old shared password no longer works.")
    elif rotated == 0:
        out("\nNo demo accounts found - run `python -m app.scripts.seed` first.")
    else:
        out(f"\nRotated {rotated}/{len(DEMO_EMAILS)} demo accounts.")
    return rotated


def main(argv: list[str] | None = None) -> int:
    try:
        password = resolve_password(sys.argv[1:] if argv is None else argv, os.environ)
    except RotateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        asyncio.run(execute(password))
    except Exception as exc:  # pragma: no cover - surfaced to the operator, never logged
        # Print only the exception type, never str(exc): a SQLAlchemy error renders the
        # failing statement and its bound parameters (the bcrypt hash), which must not leak.
        print(
            f"error: rotate failed ({type(exc).__name__}). "
            "Check the database is reachable and migrated, then retry.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
