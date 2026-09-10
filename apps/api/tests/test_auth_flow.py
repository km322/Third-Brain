"""End-to-end integration tests against the real FastAPI app.

Driven through ``httpx.AsyncClient`` + ``ASGITransport`` and backed by a live
Postgres + pgvector database. The ``client`` fixture (see ``conftest.py``) skips these
automatically when the database is unreachable, so the suite stays green without
infrastructure. All LLM calls resolve to the deterministic offline provider.

Two journeys are covered:

* the happy path -- register, login, create a knowledge base, ingest a document and
  retrieve it via search; and
* the access-control guarantee -- a second user who is a member of the org but has no
  grant on a private collection cannot retrieve its document.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
"""Mark both journeys as integration-tier.

They drive the real app against Postgres+pgvector, so they belong there - they were
running unmarked, and ``pytest -m integration`` skipped them.
"""

CONTENT = (
    "Photosynthesis is the process by which green plants convert sunlight into "
    "chemical energy stored in glucose molecules produced from carbon dioxide and water."
)


async def test_register_login_ingest_and_search(client, register, ingest_now, api) -> None:
    """Full happy path: a user can register, add content and find it again.

    Login is driven explicitly so the credential path is exercised independently of
    registration; the resulting session then resolves to the caller as OWNER of their
    bootstrap org. The added text document starts life as ``pending`` and ingestion is
    driven inline (extract -> chunk -> embed -> index) before retrieval is asserted.
    """
    session = await register(client, full_name="Ada Lovelace")

    login = await client.post(
        f"{api}/auth/login",
        json={"email": session["email"], "password": session["password"]},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = await client.get(f"{api}/users/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == session["email"]
    assert me.json()["role"] == "owner"

    coll = await client.post(
        f"{api}/collections",
        headers=headers,
        json={"name": "Biology Notes", "visibility": "private", "default_permission": "viewer"},
    )
    assert coll.status_code == 201, coll.text
    collection_id = coll.json()["id"]

    doc = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": collection_id, "title": "Photosynthesis", "content": CONTENT},
    )
    assert doc.status_code == 201, doc.text
    document_id = doc.json()["id"]
    assert doc.json()["status"] == "pending"

    await ingest_now(document_id)

    detail = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "indexed"
    assert detail.json()["chunk_count"] >= 1

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=headers)
    assert chunks.status_code == 200, chunks.text
    assert len(chunks.json()) >= 1

    search = await client.post(
        f"{api}/search",
        headers=headers,
        json={"query": "how do plants convert sunlight into energy"},
    )
    assert search.status_code == 200, search.text
    hits = search.json()["hits"]
    assert len(hits) >= 1
    assert any(h["document_id"] == document_id for h in hits)
    assert search.json()["query"]


async def test_private_document_is_not_retrievable_by_outsider(
    client, register, ingest_now, api
) -> None:
    """A member without a grant on a private collection cannot retrieve its content.

    The outsider is registered first so they exist for the invite lookup. The owner's own
    search runs as a positive control - the org admin CAN retrieve their private document
    - before the outsider is added to the org as a plain viewer, with no grant on the
    private collection. That collection is then invisible in their listing, and their
    search surfaces nothing from it because the org holds only the private collection.
    """
    outsider = await register(client, full_name="Outsider")

    owner = await register(client, full_name="Collection Owner")
    owner_headers = owner["headers"]

    org = await client.get(f"{api}/orgs/current", headers=owner_headers)
    assert org.status_code == 200, org.text
    org_id = org.json()["id"]

    coll = await client.post(
        f"{api}/collections",
        headers=owner_headers,
        json={"name": "Confidential", "visibility": "private", "default_permission": "viewer"},
    )
    assert coll.status_code == 201, coll.text
    collection_id = coll.json()["id"]

    doc = await client.post(
        f"{api}/documents/text",
        headers=owner_headers,
        json={
            "collection_id": collection_id,
            "title": "Secret",
            "content": "The launch code for project aurora is kept in this confidential note.",
        },
    )
    assert doc.status_code == 201, doc.text
    document_id = doc.json()["id"]
    await ingest_now(document_id)

    query = {"query": "project aurora launch code"}

    owner_search = await client.post(f"{api}/search", headers=owner_headers, json=query)
    assert owner_search.status_code == 200, owner_search.text
    assert any(h["document_id"] == document_id for h in owner_search.json()["hits"])

    invite = await client.post(
        f"{api}/orgs/members/invite",
        headers=owner_headers,
        json={"email": outsider["email"], "role": "viewer"},
    )
    assert invite.status_code == 201, invite.text
    membership_id = invite.json()["id"]
    activate = await client.patch(
        f"{api}/orgs/members/{membership_id}",
        headers=owner_headers,
        json={"status": "active"},
    )
    assert activate.status_code == 200, activate.text

    switch = await client.post(
        f"{api}/orgs/switch", headers=outsider["headers"], json={"org_id": org_id}
    )
    assert switch.status_code == 200, switch.text
    outsider_headers = {"Authorization": f"Bearer {switch.json()['access_token']}"}

    listed = await client.get(f"{api}/collections", headers=outsider_headers)
    assert listed.status_code == 200, listed.text
    assert all(c["id"] != collection_id for c in listed.json())

    outsider_search = await client.post(f"{api}/search", headers=outsider_headers, json=query)
    assert outsider_search.status_code == 200, outsider_search.text
    assert outsider_search.json()["hits"] == []
