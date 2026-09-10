"""Integration: ingestion robustness (the stuck-document reaper)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import factories
import pytest

from app.models.document import Document
from app.models.enums import DocumentStatus, SourceType
from app.services.ingestion import reap_stuck_documents

pytestmark = pytest.mark.integration


def _document(org, collection, *, status, age: timedelta) -> Document:
    stamp = datetime.now(UTC) - age
    return Document(
        org_id=org.id,
        collection_id=collection.id,
        title="Doc",
        source_type=SourceType.TEXT,
        mime_type="text/plain",
        status=status,
        size_bytes=1,
        created_at=stamp,
        updated_at=stamp,
    )


async def test_reaper_fails_stuck_documents_but_spares_fresh_ones(db_session) -> None:
    """The long-stranded document is failed and carries a human-readable reason; a document
    that only just started processing is left alone so a slow-but-live job is never killed."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)

    stuck = _document(org, collection, status=DocumentStatus.PROCESSING, age=timedelta(hours=2))
    fresh = _document(org, collection, status=DocumentStatus.PROCESSING, age=timedelta(minutes=1))
    db_session.add_all([stuck, fresh])
    await db_session.commit()

    reaped = await reap_stuck_documents(db_session)
    assert reaped >= 1

    await db_session.refresh(stuck)
    await db_session.refresh(fresh)
    assert stuck.status == DocumentStatus.FAILED
    assert stuck.error
    assert fresh.status == DocumentStatus.PROCESSING


async def test_reaper_spares_pending_documents(db_session) -> None:
    """A PENDING document is only waiting in the queue, not crashed: the reaper must not
    fail it, even when it has waited a long time under a backlog."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)

    queued = _document(org, collection, status=DocumentStatus.PENDING, age=timedelta(hours=2))
    db_session.add(queued)
    await db_session.commit()

    await reap_stuck_documents(db_session)

    await db_session.refresh(queued)
    assert queued.status == DocumentStatus.PENDING
    assert queued.error is None
