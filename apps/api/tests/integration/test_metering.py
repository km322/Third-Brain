"""Integration: metering must never break a user-facing request.

``record_usage`` / ``record_audit`` write inside a SAVEPOINT so a failed metering flush
(e.g. an ``api_key_id`` that was deleted mid-request) rolls back only the metering row and
leaves the caller's own transaction committable.
"""

from __future__ import annotations

import uuid

import factories
import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.deps import AuthContext
from app.models.api_key import ApiKey
from app.models.enums import OrgRole, UsageKind
from app.models.organization import Organization
from app.models.usage import UsageRecord
from app.services.metering import record_usage

pytestmark = pytest.mark.integration


async def test_failed_metering_does_not_break_the_request(db_session) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)

    async with SessionLocal() as s:
        # Caller's real work: rename the org (an UPDATE), left unflushed on purpose.
        org_row = await s.get(Organization, org.id)
        org_row.name = "Renamed By Caller"

        # Metering with an api key that does not exist -> FK violation on flush. The
        # savepoint must absorb it without poisoning the caller's transaction.
        ghost_key = ApiKey(
            id=uuid.uuid4(),
            org_id=org.id,
            name="ghost",
            key_prefix="tb_ghost",
            hashed_key="deadbeef",
            scopes=["search"],
        )
        ctx = AuthContext(org_id=org.id, org_role=OrgRole.ADMIN, api_key=ghost_key)
        await record_usage(s, ctx, UsageKind.SEARCH, units=1)

        # The caller's transaction still commits.
        await s.commit()

    async with SessionLocal() as verify:
        renamed = await verify.get(Organization, org.id)
        assert renamed.name == "Renamed By Caller"
        usage_rows = (
            await verify.execute(
                select(func.count()).select_from(UsageRecord).where(UsageRecord.org_id == org.id)
            )
        ).scalar()
        assert usage_rows == 0
