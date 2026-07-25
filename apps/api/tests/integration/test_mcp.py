"""Integration: the MCP JSON-RPC surface at /mcp (tools/list + tools/call)."""

from __future__ import annotations

import factories
import pytest
from sqlalchemy import select

from app.mcp import tools as mcp_tools
from app.models.collection import Collection
from app.models.enums import OrgRole, Visibility
from app.services.llm import CompletionResult

pytestmark = pytest.mark.integration

UNAUTHORIZED = -32001


async def _rpc(client, *, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    resp = await client.post("/mcp", json=body, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_initialize_and_tools_list(client, api) -> None:
    init = await _rpc(client, method="initialize", params={"protocolVersion": "2024-11-05"})
    assert init["result"]["serverInfo"]["name"] == "third-brain"

    listed = await _rpc(client, method="tools/list")
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {
        "search_knowledge",
        "get_document",
        "list_collections",
        "add_knowledge",
        "update_knowledge",
    } <= names


async def test_tools_call_search_and_write(client, db_session, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Sunlight",
        content="Plants convert sunlight into chemical energy stored as glucose.",
        created_by=owner,
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)

    called = await _rpc(
        client,
        method="tools/call",
        params={"name": "search_knowledge", "arguments": {"query": "sunlight energy"}},
        headers=headers,
    )
    result = called["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["count"] >= 1

    # A write tool with an ingest-scoped key creates + indexes a document.
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "collection": str(collection.id),
                "title": "Added via MCP",
                "content": "Knowledge inserted through the MCP add_knowledge tool.",
            },
        },
        headers=headers,
    )
    assert written["result"]["isError"] is False
    assert written["result"]["structuredContent"]["created"] is True


async def test_add_knowledge_auto_routes_to_editable_collection(
    client, db_session, api, monkeypatch
) -> None:
    """add_knowledge with no 'collection' files the capture into the caller's single
    editable collection directly - a one-option routing decision never consults the
    classifier (no model latency or spend on the common capture path)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    classifier_calls: list[object] = []

    async def _recording_complete(*args, **kwargs):
        classifier_calls.append(args)
        return CompletionResult(text="1", model="stub", provider="fake")

    monkeypatch.setattr(mcp_tools, "complete", _recording_complete)
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "title": "Chose Postgres over Mongo",
                "content": "We picked Postgres for its relational guarantees and pgvector search.",
                "doc_type": "decision",
            },
        },
        headers=factories.api_key_headers(secret),
    )
    sc = written["result"]["structuredContent"]
    assert written["result"]["isError"] is False, written["result"]
    assert sc["created"] is True
    assert sc["auto_routed"] is True
    assert sc["collection_id"] == str(collection.id)
    assert classifier_calls == []


async def test_add_knowledge_auto_creates_decisions_collection(client, db_session, api) -> None:
    """With no editable collection to route to, add_knowledge files the capture into a shared
    'Decisions' collection, created on first use."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "title": "Weekly on-call rotation",
                "content": "On-call moves to a weekly rotation with a secondary engineer.",
                "doc_type": "decision",
            },
        },
        headers=factories.api_key_headers(secret),
    )
    sc = written["result"]["structuredContent"]
    assert written["result"]["isError"] is False, written["result"]
    assert sc["created"] is True
    assert sc["auto_routed"] is True
    assert sc["collection"] == "Decisions"


async def test_auto_route_consults_classifier_then_falls_back_deterministically(
    client, db_session, api, monkeypatch
) -> None:
    """With several editable collections the classifier is consulted exactly once; when it
    abstains, the capture falls back to the most-populated editable collection."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    await factories.create_collection(db_session, org=org, owner=owner, visibility=Visibility.ORG)
    popular = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    popular.document_count = 5
    await db_session.commit()
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    classifier_calls: list[object] = []

    async def _abstaining_complete(*args, **kwargs):
        classifier_calls.append(args)
        return CompletionResult(text="0", model="stub", provider="fake")

    monkeypatch.setattr(mcp_tools, "complete", _abstaining_complete)
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "title": "Retry budget for the ingest queue",
                "content": "We cap ingestion retries at five with exponential backoff.",
            },
        },
        headers=factories.api_key_headers(secret),
    )
    sc = written["result"]["structuredContent"]
    assert written["result"]["isError"] is False, written["result"]
    assert len(classifier_calls) == 1
    assert sc["auto_routed"] is True
    assert sc["collection_id"] == str(popular.id)


async def test_auto_route_flagged_content_never_reaches_classifier(
    client, db_session, api, monkeypatch
) -> None:
    """The quarantine invariant holds during routing: content the secret scanner flags is
    never included in the routing classifier's prompt (it must not leave the box), and the
    capture still quarantines instead of indexing."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    await factories.create_collection(db_session, org=org, owner=owner, visibility=Visibility.ORG)
    await factories.create_collection(db_session, org=org, owner=owner, visibility=Visibility.ORG)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    classifier_calls: list[object] = []

    async def _must_not_run(*args, **kwargs):
        classifier_calls.append(args)
        return CompletionResult(text="1", model="stub", provider="fake")

    monkeypatch.setattr(mcp_tools, "complete", _must_not_run)
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "title": "Deploy key rotation notes",
                "content": "Rotate the CI credential aws_access_key_id=AKIAIOSFODNN7EXAMPLE now.",
            },
        },
        headers=factories.api_key_headers(secret),
    )
    sc = written["result"]["structuredContent"]
    assert written["result"]["isError"] is False, written["result"]
    assert sc["status"] == "quarantined"
    assert sc["chunk_count"] == 0
    assert classifier_calls == []


async def test_auto_route_dlp_flagged_content_never_reaches_classifier(
    client, db_session, api, monkeypatch
) -> None:
    """The DLP half of the same invariant: PII that flags the DLP scanner (but not the
    secret scanner) also never enters the routing classifier's prompt - even under the
    default 'label' action, where the capture is indexed rather than quarantined."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    await factories.create_collection(db_session, org=org, owner=owner, visibility=Visibility.ORG)
    await factories.create_collection(db_session, org=org, owner=owner, visibility=Visibility.ORG)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    classifier_calls: list[object] = []

    async def _must_not_run(*args, **kwargs):
        classifier_calls.append(args)
        return CompletionResult(text="1", model="stub", provider="fake")

    monkeypatch.setattr(mcp_tools, "complete", _must_not_run)
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "title": "Payroll correction",
                "content": "Employee SSN 123-45-6789 paid via card 4111 1111 1111 1111.",
            },
        },
        headers=factories.api_key_headers(secret),
    )
    sc = written["result"]["structuredContent"]
    assert written["result"]["isError"] is False, written["result"]
    assert sc["created"] is True
    assert sc["auto_routed"] is True
    assert classifier_calls == []


async def test_add_knowledge_rejects_malformed_collection_argument(client, db_session, api) -> None:
    """A present-but-malformed 'collection' is a hard error, never silently auto-routed."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)
    for bad in (123, "", "   "):
        written = await _rpc(
            client,
            method="tools/call",
            params={
                "name": "add_knowledge",
                "arguments": {"collection": bad, "title": "x", "content": "y"},
            },
            headers=headers,
        )
        assert written["result"]["isError"] is True, written["result"]
        message = " ".join(
            block.get("text", "") for block in written["result"].get("content", [])
        ).lower()
        assert "collection" in message and "non-empty" in message, written["result"]


async def test_viewer_role_cannot_auto_create_decisions_collection(client, db_session, api) -> None:
    """Parity with REST's POST /collections: creating the shared Decisions collection
    requires an org role of editor or higher. A viewer-role caller with nothing editable
    gets a clear error, and no org-writable collection appears."""
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _membership = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=viewer
    )
    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {"title": "A note", "content": "Captured by a viewer's agent."},
        },
        headers=factories.api_key_headers(secret),
    )
    assert written["result"]["isError"] is True, written["result"]
    message = " ".join(
        block.get("text", "") for block in written["result"].get("content", [])
    ).lower()
    assert "admin" in message, written["result"]
    created = (
        (await db_session.execute(select(Collection).where(Collection.org_id == org.id)))
        .scalars()
        .all()
    )
    assert created == []


async def test_mcp_write_survives_rest_reprocess(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """MCP writes persist the source blob, so a later REST reprocess re-ingests the latest
    content instead of silently reverting to a stale original (findings 17/34)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    key_headers = factories.api_key_headers(secret)

    added = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {
                "collection": str(collection.id),
                "title": "Doc",
                "content": "Original content about penguins.",
            },
        },
        headers=key_headers,
    )
    doc_id = added["result"]["structuredContent"]["id"]

    updated = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "update_knowledge",
            "arguments": {"document_id": doc_id, "content": "Revised content about dolphins."},
        },
        headers=key_headers,
    )
    assert updated["result"]["structuredContent"]["updated"] is True

    # Reprocess re-ingests from the stored blob, which must now hold the revised content.
    reprocessed = await client.post(
        f"{api}/documents/{doc_id}/reprocess", headers=token_headers(owner.id, org.id)
    )
    assert reprocessed.status_code == 200, reprocessed.text
    await ingest_now(doc_id)

    chunks = await client.get(
        f"{api}/documents/{doc_id}/chunks", headers=token_headers(owner.id, org.id)
    )
    assert chunks.status_code == 200, chunks.text
    body = " ".join(c["content"] for c in chunks.json())
    assert "dolphins" in body
    assert "penguins" not in body


async def test_write_requires_ingest_scope(client, db_session, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    _key, secret = await factories.create_api_key(
        db_session,
        org=org,
        scopes=["search"],
        acts_as_user=owner,  # no ingest
    )
    headers = factories.api_key_headers(secret)

    written = await _rpc(
        client,
        method="tools/call",
        params={
            "name": "add_knowledge",
            "arguments": {"collection": str(collection.id), "title": "x", "content": "y"},
        },
        headers=headers,
    )
    # Domain failure surfaces as a tool result with isError (not a transport error), and
    # specifically because the key lacks the ingest/write scope - not some unrelated error,
    # which a bare ``isError is True`` would also accept.
    assert written["result"]["isError"] is True
    message = " ".join(
        block.get("text", "") for block in written["result"].get("content", [])
    ).lower()
    assert "ingest" in message or "scope" in message, written["result"]


async def test_search_requires_search_scope(client, db_session, api) -> None:
    """Parity with REST ``/search``/``/v1``: an API key lacking the 'search' scope cannot
    search via MCP, while a wildcard key clears the same gate."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Sunlight",
        content="Plants convert sunlight into chemical energy stored as glucose.",
        created_by=owner,
    )

    _key, no_search_secret = await factories.create_api_key(
        db_session,
        org=org,
        scopes=["ingest"],
        acts_as_user=owner,  # no search
    )
    denied = await _rpc(
        client,
        method="tools/call",
        params={"name": "search_knowledge", "arguments": {"query": "sunlight energy"}},
        headers=factories.api_key_headers(no_search_secret),
    )
    # Domain failure surfaces as a tool result with isError (not a transport error), and
    # specifically because the key lacks the search scope, not some unrelated error.
    assert denied["result"]["isError"] is True
    message = " ".join(
        block.get("text", "") for block in denied["result"].get("content", [])
    ).lower()
    assert "search" in message or "scope" in message, denied["result"]

    # A wildcard key clears the scope gate and searches successfully.
    _wkey, wildcard_secret = await factories.create_api_key(
        db_session, org=org, scopes=["*"], acts_as_user=owner
    )
    allowed = await _rpc(
        client,
        method="tools/call",
        params={"name": "search_knowledge", "arguments": {"query": "sunlight energy"}},
        headers=factories.api_key_headers(wildcard_secret),
    )
    assert allowed["result"]["isError"] is False, allowed["result"]
    assert allowed["result"]["structuredContent"]["count"] >= 1


async def test_tools_call_without_auth_is_unauthorized(client, api) -> None:
    resp = await _rpc(
        client,
        method="tools/call",
        params={"name": "search_knowledge", "arguments": {"query": "hi"}},
    )
    assert "error" in resp
    assert resp["error"]["code"] == UNAUTHORIZED
