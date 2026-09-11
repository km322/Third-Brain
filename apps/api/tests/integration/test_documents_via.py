"""Integration: the ``via`` provenance filter on GET /documents and DocumentItem.via.

The MCP ``add_knowledge`` tool stamps ``Document.meta = {"via": "mcp", ...}`` on creation,
which surfaces the document as "written by an agent". These tests exercise that end to end:
the marker is created through the real MCP JSON-RPC path (not hand-stamped), and the filter
is verified to narrow the ACL-scoped listing rather than bypass it.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole, PermissionLevel, ResourceType, Visibility

pytestmark = pytest.mark.integration


async def _add_knowledge(client, *, collection_id, title, content, headers) -> dict:
    """Create an agent-written document through the MCP add_knowledge tool."""
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "add_knowledge",
                "arguments": {
                    "collection": str(collection_id),
                    "title": title,
                    "content": content,
                },
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def test_via_filter_returns_only_agent_docs(client, db_session, token_headers, api) -> None:
    """``via=mcp`` narrows the listing to agent-written docs, and DocumentItem.via reflects
    provenance (``"mcp"`` for MCP-created, ``None`` for dashboard/REST-created).

    A human/REST-created document carries no provenance marker. Unfiltered, both appear, each
    tagged with its true provenance; under ``via=mcp`` only the agent-written document does.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    key_headers = factories.api_key_headers(secret)
    user_headers = token_headers(owner.id, org.id)

    agent = await _add_knowledge(
        client,
        collection_id=collection.id,
        title="Agent note",
        content="A decision the agent captured while working on the task.",
        headers=key_headers,
    )
    agent_id = agent["id"]

    human = await client.post(
        f"{api}/documents/text",
        headers=user_headers,
        json={
            "collection_id": str(collection.id),
            "title": "Human note",
            "content": "This document was written by a person through the dashboard.",
        },
    )
    assert human.status_code == 201, human.text
    human_id = human.json()["id"]
    assert human.json()["via"] is None

    listing = await client.get(f"{api}/documents", headers=user_headers)
    assert listing.status_code == 200, listing.text
    by_id = {item["id"]: item for item in listing.json()["items"]}
    assert by_id[agent_id]["via"] == "mcp"
    assert by_id[human_id]["via"] is None

    filtered = await client.get(f"{api}/documents", headers=user_headers, params={"via": "mcp"})
    assert filtered.status_code == 200, filtered.text
    items = filtered.json()["items"]
    ids = {item["id"] for item in items}
    assert agent_id in ids
    assert human_id not in ids
    assert items and all(item["via"] == "mcp" for item in items)


async def test_via_filter_combines_with_status_and_collection(
    client, db_session, token_headers, api
) -> None:
    """The provenance filter ANDs with the existing status and collection filters.

    An MCP write indexes synchronously, so both agent documents are INDEXED, while a REST
    document in Alpha stays PENDING (the test client neutralises the ingest enqueue). So
    ``via=mcp + status=indexed`` returns both agent docs and no human/pending doc,
    ``via=mcp + status=pending`` matches nothing, and ``via=mcp + collection_id=Alpha``
    returns only Alpha's agent doc.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    coll_a = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG, name="Alpha"
    )
    coll_b = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG, name="Beta"
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    key_headers = factories.api_key_headers(secret)
    user_headers = token_headers(owner.id, org.id)

    agent_a = await _add_knowledge(
        client,
        collection_id=coll_a.id,
        title="Agent A",
        content="Something the agent learned about collection Alpha.",
        headers=key_headers,
    )
    agent_a_id = agent_a["id"]
    assert agent_a["status"] == "indexed"

    agent_b = await _add_knowledge(
        client,
        collection_id=coll_b.id,
        title="Agent B",
        content="Something the agent learned about collection Beta.",
        headers=key_headers,
    )
    agent_b_id = agent_b["id"]

    pending = await client.post(
        f"{api}/documents/text",
        headers=user_headers,
        json={
            "collection_id": str(coll_a.id),
            "title": "Human pending",
            "content": "A human-authored document that has not been indexed yet.",
        },
    )
    assert pending.status_code == 201, pending.text

    indexed = await client.get(
        f"{api}/documents",
        headers=user_headers,
        params={"via": "mcp", "status": "indexed"},
    )
    assert indexed.status_code == 200, indexed.text
    indexed_ids = {item["id"] for item in indexed.json()["items"]}
    assert {agent_a_id, agent_b_id} <= indexed_ids

    still_pending = await client.get(
        f"{api}/documents",
        headers=user_headers,
        params={"via": "mcp", "status": "pending"},
    )
    assert still_pending.status_code == 200, still_pending.text
    assert all(item["via"] != "mcp" for item in still_pending.json()["items"])
    assert agent_a_id not in {item["id"] for item in still_pending.json()["items"]}

    in_alpha = await client.get(
        f"{api}/documents",
        headers=user_headers,
        params={"via": "mcp", "collection_id": str(coll_a.id)},
    )
    assert in_alpha.status_code == 200, in_alpha.text
    alpha_ids = {item["id"] for item in in_alpha.json()["items"]}
    assert agent_a_id in alpha_ids
    assert agent_b_id not in alpha_ids


async def test_via_filter_never_bypasses_acl(client, db_session, token_headers, api) -> None:
    """A document the caller cannot see never appears, even when filtering via=mcp: the
    provenance predicate ANDs onto the ACL scope rather than replacing it.

    The owner seeing their own agent doc under the filter is the positive control; the
    outsider - a plain viewer member of the same org with no grant on the private collection -
    cannot see it. Granting that outsider VIEWER on the collection makes the agent doc visible
    again, proving it was the ACL, not the via filter, that hid it.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    private = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        visibility=Visibility.PRIVATE,
        default_permission=PermissionLevel.VIEWER,
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search", "ingest"], acts_as_user=owner
    )
    key_headers = factories.api_key_headers(secret)

    agent = await _add_knowledge(
        client,
        collection_id=private.id,
        title="Secret agent note",
        content="Knowledge captured by an agent inside a private collection.",
        headers=key_headers,
    )
    agent_id = agent["id"]

    outsider, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    outsider_headers = token_headers(outsider.id, org.id)

    owner_view = await client.get(
        f"{api}/documents", headers=token_headers(owner.id, org.id), params={"via": "mcp"}
    )
    assert owner_view.status_code == 200, owner_view.text
    assert agent_id in {item["id"] for item in owner_view.json()["items"]}

    outsider_view = await client.get(
        f"{api}/documents", headers=outsider_headers, params={"via": "mcp"}
    )
    assert outsider_view.status_code == 200, outsider_view.text
    assert agent_id not in {item["id"] for item in outsider_view.json()["items"]}

    await factories.grant_user(
        db_session,
        org=org,
        user=outsider,
        resource_type=ResourceType.COLLECTION,
        resource_id=private.id,
        permission=PermissionLevel.VIEWER,
    )
    granted_view = await client.get(
        f"{api}/documents", headers=outsider_headers, params={"via": "mcp"}
    )
    assert granted_view.status_code == 200, granted_view.text
    assert agent_id in {item["id"] for item in granted_view.json()["items"]}
