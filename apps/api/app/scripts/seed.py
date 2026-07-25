"""Seed a story-driven demo organization so the app is usable - and *demoable* -
immediately after `make migrate`.

The dataset is designed to show off **permission-aware retrieval**: three collections
with contrasting visibility and three users with different roles, so the same question
returns different answers depending on who is asking.

Idempotent: re-running does nothing if the demo org already exists. Prints the demo
logins, their shared password (randomly generated unless ``DEMO_PASSWORD`` is set), and
a freshly minted API key - each shown once.

    python -m app.scripts.seed
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import generate_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    OrgRole,
    PermissionLevel,
    SourceType,
    Visibility,
)
from app.models.organization import Organization
from app.models.team import Team, TeamMember
from app.models.user import Membership, User
from app.services.llm import embed_texts

DEMO_ORG_SLUG = "acme"
# There is deliberately NO default demo password: when DEMO_PASSWORD is unset, seeding
# generates a fresh random secret and prints it once, so no well-known credential exists
# anywhere (code, docs, or git history). ``or None`` (not a ``get`` default) so an empty
# var from compose env plumbing means "unset". Rotate an already-seeded demo anytime with
# `DEMO_PASSWORD=... python -m app.scripts.rotate_demo_password`.
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD") or None
# Mirror the RegisterRequest/LoginRequest policy (min 8, max 128): the login route rejects
# anything outside this range, so seeding with one would create accounts that cannot log in.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def resolve_demo_password() -> tuple[str, bool]:
    """The shared password for the seeded demo accounts.

    An explicit ``DEMO_PASSWORD`` is validated against the login policy; otherwise a fresh
    random secret is generated for this seed. Returns ``(password, generated)``.
    """
    if DEMO_PASSWORD is not None:
        if not (MIN_PASSWORD_LENGTH <= len(DEMO_PASSWORD) <= MAX_PASSWORD_LENGTH):
            raise SystemExit(
                f"DEMO_PASSWORD must be between {MIN_PASSWORD_LENGTH} and "
                f"{MAX_PASSWORD_LENGTH} characters - the login route rejects anything "
                "outside that range."
            )
        return DEMO_PASSWORD, False
    return secrets.token_urlsafe(16), True


def _demo_email(var: str, default: str) -> str:
    return (os.environ.get(var) or default).strip().lower()


# The seeded demo logins. Each address is independently overridable, so a deployment can
# seed the demo on whatever addresses it likes; the open-source defaults are the neutral
# reserved example.com. Rotation (``rotate_demo_password``) must run with the SAME values
# the demo was seeded with, since it finds the accounts by these emails.
ADMIN_EMAIL = _demo_email("DEMO_ADMIN_EMAIL", "admin@example.com")  # OWNER - sees everything
ENGINEER_EMAIL = _demo_email("DEMO_ENGINEER_EMAIL", "engineer@example.com")  # Engineering team
VIEWER_EMAIL = _demo_email("DEMO_VIEWER_EMAIL", "viewer@example.com")  # only org-wide content


def _naive_chunks(text: str, size: int = 120) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)] or [text]


# (title, body) tuples per collection.
HANDBOOK_DOCS = [
    (
        "Onboarding Guide",
        "Welcome to Acme. New hires set up their laptop via the IT portal, request repo "
        "access from the platform team, and read the engineering handbook. Standups are "
        "daily at 10am. Deploys go out on Tuesdays and Thursdays. Ask in #help for anything.",
    ),
    (
        "PTO & Benefits",
        "Acme offers unlimited PTO with a three-week minimum. Health, dental and vision are "
        "fully covered. The 401(k) match is 4%. Submit expenses in the finance portal within "
        "30 days. Parental leave is 16 weeks fully paid.",
    ),
    (
        "Refund Policy",
        "Customers may request a refund within 30 days of purchase. Annual plans are refundable "
        "pro-rata. Refunds are processed to the original payment method within 5-10 business "
        "days. Escalate disputed charges to the finance team.",
    ),
]

ENGINEERING_DOCS = [
    (
        "Production Runbook",
        "If the API is down, check the load balancer health checks first, then Postgres "
        "connections and the Redis queue depth. Roll back the last deploy with `make rollback`. "
        "The on-call engineer owns incident comms in #incidents.",
    ),
    (
        "On-call Rotation",
        "On-call rotates weekly, handing off Mondays at 10am. Page severity: SEV1 within 5 "
        "minutes, SEV2 within 30. The escalation path is on-call engineer, then the team lead, "
        "then the VP of Engineering. Keep the runbook open during your shift.",
    ),
    (
        "Architecture Overview",
        "The platform is a FastAPI service backed by Postgres with pgvector for retrieval and "
        "Redis for caching and the ingestion queue. Background workers embed and index "
        "documents. The frontend is a Next.js dashboard.",
    ),
]

BOARD_FINANCE_DOCS = [
    (
        "Board Minutes - Q2",
        "The board approved the Series B raise of 40 million dollars at a 320 million "
        "post-money valuation, led by Northwind Capital. Runway now extends to 34 months. "
        "The board discussed a potential acquisition of a competitor, Cortex AI, for 18 million.",
    ),
    (
        "Compensation Bands",
        "The CEO's total compensation is 480,000 dollars plus equity. VP bands run 260,000 to "
        "310,000. Senior engineers are banded 190,000 to 240,000. All figures are strictly "
        "confidential and restricted to the board and finance.",
    ),
]


async def _make_user(db, email: str, name: str, password: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=name,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_collection(
    db,
    *,
    org_id,
    owner_id,
    name: str,
    slug: str,
    description: str,
    visibility: Visibility,
    docs: list[tuple[str, str]],
    owner_team_id=None,
) -> Collection:
    collection = Collection(
        org_id=org_id,
        owner_id=owner_id,
        owner_team_id=owner_team_id,
        name=name,
        slug=slug,
        description=description,
        visibility=visibility,
        default_permission=PermissionLevel.VIEWER,
    )
    db.add(collection)
    await db.flush()

    for title, body in docs:
        doc = Document(
            org_id=org_id,
            collection_id=collection.id,
            created_by_id=owner_id,
            title=title,
            source_type=SourceType.TEXT,
            status=DocumentStatus.INDEXED,
            indexed_at=datetime.now(UTC),
            size_bytes=len(body.encode()),
        )
        db.add(doc)
        await db.flush()

        chunks = _naive_chunks(body)
        embeddings = (await embed_texts(chunks)).vectors
        for i, (chunk_text, vec) in enumerate(zip(chunks, embeddings, strict=False)):
            db.add(
                DocumentChunk(
                    org_id=org_id,
                    collection_id=collection.id,
                    document_id=doc.id,
                    chunk_index=i,
                    content=chunk_text,
                    token_count=len(chunk_text.split()),
                    embedding=vec,
                )
            )
        doc.chunk_count = len(chunks)
    collection.document_count = len(docs)
    return collection


async def seed() -> None:
    emails = {
        "DEMO_ADMIN_EMAIL": ADMIN_EMAIL,
        "DEMO_ENGINEER_EMAIL": ENGINEER_EMAIL,
        "DEMO_VIEWER_EMAIL": VIEWER_EMAIL,
    }
    for var, address in emails.items():
        if not re.fullmatch(r"[^@\s]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", address):
            raise SystemExit(
                f"{var} {address!r} is not a valid email address - the seeded login "
                "would be unusable."
            )
    if len(set(emails.values())) != len(emails):
        raise SystemExit(
            "The three demo logins must be distinct addresses "
            "(DEMO_ADMIN_EMAIL / DEMO_ENGINEER_EMAIL / DEMO_VIEWER_EMAIL)."
        )
    password, generated = resolve_demo_password()
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if existing:
            print(f"Demo org '{DEMO_ORG_SLUG}' already exists - nothing to do.")
            return

        org = Organization(name="Acme Inc.", slug=DEMO_ORG_SLUG)
        db.add(org)
        await db.flush()

        # Three people with three different levels of access.
        admin = await _make_user(db, ADMIN_EMAIL, "Ada Admin", password)
        engineer = await _make_user(db, ENGINEER_EMAIL, "Evan Engineer", password)
        viewer = await _make_user(db, VIEWER_EMAIL, "Vera Viewer", password)
        db.add_all(
            [
                Membership(org_id=org.id, user_id=admin.id, role=OrgRole.OWNER),
                Membership(org_id=org.id, user_id=engineer.id, role=OrgRole.EDITOR),
                Membership(org_id=org.id, user_id=viewer.id, role=OrgRole.VIEWER),
            ]
        )

        # The Engineering team contains the admin + the engineer, but NOT the viewer.
        eng_team = Team(org_id=org.id, name="Engineering", slug="engineering")
        db.add(eng_team)
        await db.flush()
        db.add_all(
            [
                TeamMember(team_id=eng_team.id, user_id=admin.id),
                TeamMember(team_id=eng_team.id, user_id=engineer.id),
            ]
        )

        # An API key that acts as the admin (full read across the org).
        full_key, prefix, hashed = generate_api_key()
        db.add(
            ApiKey(
                org_id=org.id,
                created_by_id=admin.id,
                acts_as_user_id=admin.id,
                name="Seed key (admin)",
                key_prefix=prefix,
                hashed_key=hashed,
                scopes=["*"],
            )
        )

        # 1) Everyone in the org can read the handbook.
        await _make_collection(
            db,
            org_id=org.id,
            owner_id=admin.id,
            owner_team_id=eng_team.id,
            name="Company Handbook",
            slug="company-handbook",
            description="Policies, onboarding and how we work. Visible to the whole org.",
            visibility=Visibility.ORG,
            docs=HANDBOOK_DOCS,
        )

        # 2) Only the Engineering team can read the engineering space.
        await _make_collection(
            db,
            org_id=org.id,
            owner_id=engineer.id,
            owner_team_id=eng_team.id,
            name="Engineering",
            slug="engineering",
            description="Runbooks and architecture. Visible to the Engineering team only.",
            visibility=Visibility.TEAM,
            docs=ENGINEERING_DOCS,
        )

        # 3) Only the owner (admin) + org admins can read Board & Finance.
        await _make_collection(
            db,
            org_id=org.id,
            owner_id=admin.id,
            name="Board & Finance",
            slug="board-finance",
            description="Board minutes and compensation. Private - restricted to leadership.",
            visibility=Visibility.PRIVATE,
            docs=BOARD_FINANCE_DOCS,
        )

        await db.commit()

    print("\n✅ Seed complete - Acme Inc. is ready to demo.\n")
    print("   Sign in (all share the same password):")
    print(f"     • {ADMIN_EMAIL}     (Owner)   - sees everything")
    print(f"     • {ENGINEER_EMAIL}  (Editor)  - handbook + engineering, NOT board/finance")
    print(f"     • {VIEWER_EMAIL}    (Viewer)  - only the org-wide handbook")
    print(f"     password: {password}")
    if generated:
        print("     ^ randomly generated for this seed - store it now; it is not shown again.")
        print("       (Set DEMO_PASSWORD before seeding to choose your own.)")
    print()
    print(f"   Admin API key: {full_key}")
    print("     ^ store this now; it is not shown again.\n")
    print("   👉 Try the same question as different users in the Ask playground:")
    print('        "What is the CEO\'s compensation and are we planning an acquisition?"')
    print("      Ada (admin) gets the answer from Board & Finance; Vera (viewer) gets nothing -")
    print("      permission-aware retrieval never leaks a chunk the asker can't see.\n")


if __name__ == "__main__":
    asyncio.run(seed())
