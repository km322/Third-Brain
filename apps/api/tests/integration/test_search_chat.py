"""Integration: permission-aware retrieval and grounded RAG chat over real pgvector.

Documents are embedded with the offline ``fake`` provider into genuine ``vector``
columns, so ``POST /search`` exercises a real cosine ANN query and ``POST /search/chat``
grounds a completion in the retrieved passages, returning citations that point back at
the source document.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import Visibility

pytestmark = pytest.mark.integration

_CONTENT = (
    "Photosynthesis is the process by which green plants convert sunlight into "
    "chemical energy stored as glucose, using carbon dioxide and water."
)


async def test_search_returns_real_cosine_hits(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Photosynthesis",
        content=_CONTENT,
    )
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/search",
        headers=headers,
        json={"query": "how do plants convert sunlight into chemical energy"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"]
    hits = body["hits"]
    assert len(hits) >= 1
    top = next(h for h in hits if h["document_id"] == str(document.id))
    assert top["document_title"] == "Photosynthesis"
    assert top["collection_id"] == str(collection.id)
    assert top["snippet"]
    assert isinstance(top["score"], (int, float))


async def test_search_can_narrow_to_a_collection(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    biology = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    history = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    bio_doc = await factories.create_document(
        db_session, org=org, collection=biology, title="Cells", content=_CONTENT
    )
    await factories.create_document(
        db_session,
        org=org,
        collection=history,
        content="The treaty was signed in a distant historical century.",
    )
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/search",
        headers=headers,
        json={"query": "sunlight energy glucose", "collection_ids": [str(biology.id)]},
    )
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]
    assert hits, "expected at least one hit from the biology collection"
    assert all(h["collection_id"] == str(biology.id) for h in hits)
    assert any(h["document_id"] == str(bio_doc.id) for h in hits)


async def test_chat_is_grounded_and_returns_citations(
    client, db_session, token_headers, api
) -> None:
    """The answer a user sees must never leak the internal RAG prompt scaffolding, even on the
    offline stub path (the default zero-key evaluation mode)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Photosynthesis",
        content=_CONTENT,
    )
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/search/chat",
        headers=headers,
        json={"query": "what do plants make from sunlight?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["answer"], str) and body["answer"]
    assert "<passage" not in body["answer"]
    assert "Answer the question using only" not in body["answer"]
    citations = body["citations"]
    assert len(citations) >= 1
    assert any(c["document_id"] == str(document.id) for c in citations)


async def test_streaming_chat_persists_usage_and_audit(
    client, db_session, token_headers, api
) -> None:
    """A fully-consumed streamed answer records its COMPLETION usage and a search_chat
    audit row (both committed in the stream's ``finally``), so streamed Asks are metered
    exactly like non-streamed ones.

    Citations ride the same stream, so the client needs no second ``/search``: a citations
    frame is present and carries the grounding document.
    """
    from sqlalchemy import func, select

    from app.models.audit import AuditLog
    from app.models.enums import UsageKind
    from app.models.usage import UsageRecord

    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    await factories.create_document(
        db_session, org=org, collection=collection, title="Photosynthesis", content=_CONTENT
    )
    headers = token_headers(owner.id, org.id)

    body = ""
    async with client.stream(
        "POST",
        f"{api}/search/chat",
        headers=headers,
        json={"query": "what do plants make from sunlight?", "stream": True},
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for chunk in resp.aiter_text():
            body += chunk
    assert body.strip(), "expected streamed answer tokens"
    assert '"type": "citations"' in body
    assert str(collection.id) in body
    assert "data: [DONE]" in body

    completions = (
        await db_session.execute(
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.org_id == org.id, UsageRecord.kind == UsageKind.COMPLETION)
        )
    ).scalar()
    assert completions >= 1
    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org.id, AuditLog.resource_type == "search_chat")
        )
    ).scalar()
    assert audits >= 1


async def test_search_on_empty_org_returns_no_hits(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    resp = await client.post(f"{api}/search", headers=headers, json={"query": "anything"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["hits"] == []


async def test_session_callers_are_rate_limited(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    """A dashboard (JWT) session is metered like an API key. These endpoints spend real
    provider tokens, so an unmetered signed-in caller could bill the operator without
    limit - the limiter must not be a no-op just because there is no API key."""
    from app.core.config import settings

    org, owner, _ = await factories.create_org_with_owner(db_session)
    monkeypatch.setattr(settings, "SESSION_RATE_LIMIT_PER_MINUTE", 3)
    headers = token_headers(owner.id, org.id)

    statuses = [
        (
            await client.post(f"{api}/search", json={"query": "anything at all"}, headers=headers)
        ).status_code
        for _ in range(6)
    ]
    assert 429 in statuses, statuses
    assert statuses[-1] == 429, statuses
