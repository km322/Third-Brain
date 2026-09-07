"""Integration: hard cross-tenant isolation between two organizations.

Every read/search/mutation is scoped to the caller's org. A user (or API key) in org A
must never see, retrieve, or touch org B's data - not via search, not via direct id
lookup, not by targeting B's collection. This is the multi-tenant safety net.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import Visibility

pytestmark = pytest.mark.integration


async def _tenant_with_content(db_session, term: str):
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title=f"{term} doc",
        content=f"This confidential note mentions {term} several times: {term} {term}.",
        created_by=owner,
    )
    return org, owner, collection, document


async def test_org_a_cannot_read_or_search_org_b(client, db_session, token_headers, api) -> None:
    org_a, user_a, coll_a, doc_a = await _tenant_with_content(db_session, "alpha")
    org_b, user_b, coll_b, doc_b = await _tenant_with_content(db_session, "bravo")

    a_headers = token_headers(user_a.id, org_a.id)

    # A's org-wide search never surfaces B's document, even querying B's term.
    search = await client.post(f"{api}/search", headers=a_headers, json={"query": "bravo"})
    assert search.status_code == 200, search.text
    assert all(h["document_id"] != str(doc_b.id) for h in search.json()["hits"])
    assert all(h["collection_id"] != str(coll_b.id) for h in search.json()["hits"])

    # Direct id lookups of B's resources from A are 404 (not 403 - existence is hidden).
    assert (
        await client.get(f"{api}/collections/{coll_b.id}", headers=a_headers)
    ).status_code == 404
    assert (await client.get(f"{api}/documents/{doc_b.id}", headers=a_headers)).status_code == 404
    assert (
        await client.get(f"{api}/documents/{doc_b.id}/chunks", headers=a_headers)
    ).status_code == 404

    # A cannot add a document into B's collection.
    intrude = await client.post(
        f"{api}/documents/text",
        headers=a_headers,
        json={"collection_id": str(coll_b.id), "title": "x", "content": "y"},
    )
    assert intrude.status_code == 404, intrude.text

    # B's own scoped collection listing never leaks A's collection.
    b_headers = token_headers(user_b.id, org_b.id)
    b_list = await client.get(f"{api}/collections", headers=b_headers)
    assert b_list.status_code == 200
    assert all(c["id"] != str(coll_a.id) for c in b_list.json())


async def test_api_key_is_confined_to_its_org(client, db_session, api) -> None:
    org_a, user_a, coll_a, doc_a = await _tenant_with_content(db_session, "alpha")
    org_b, user_b, coll_b, doc_b = await _tenant_with_content(db_session, "bravo")

    # An org-A API key acting as A's owner.
    _key, secret = await factories.create_api_key(
        db_session, org=org_a, scopes=["search", "read"], acts_as_user=user_a
    )
    headers = factories.api_key_headers(secret)

    # Searching for B's term through A's key yields nothing from B.
    search = await client.post(f"{api}/search", headers=headers, json={"query": "bravo"})
    assert search.status_code == 200, search.text
    assert all(h["document_id"] != str(doc_b.id) for h in search.json()["hits"])

    # And the key cannot fetch B's collection/document by id.
    assert (await client.get(f"{api}/collections/{coll_b.id}", headers=headers)).status_code == 404
    assert (await client.get(f"{api}/documents/{doc_b.id}", headers=headers)).status_code == 404
