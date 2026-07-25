"""Integration: API-key scopes gate the REST document surface (parity with MCP + /v1).

A key's scopes must cap what it can do even when it ``acts_as`` a privileged user:
a search-only key bound to an owner must not be able to create or delete documents, and a
key with no capability scope must not be able to read knowledge content. These pin the fix
for the "search-only key can delete documents" and "read scope is a no-op" findings.
"""

from __future__ import annotations

import factories
import pytest

pytestmark = pytest.mark.integration


async def _collection_and_doc(db_session):
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    document = await factories.create_document(db_session, org=org, collection=collection)
    return org, owner, collection, document


async def test_search_scope_key_cannot_write_or_delete(client, db_session, api) -> None:
    org, owner, collection, document = await _collection_and_doc(db_session)
    # A "read-only" integration key: search scope, acting as the owner (full ACL rights).
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search"], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"title": "x", "content": "hello", "collection_id": str(collection.id)},
    )
    assert created.status_code == 403, created.text

    deleted = await client.delete(f"{api}/documents/{document.id}", headers=headers)
    assert deleted.status_code == 403, deleted.text

    reprocessed = await client.post(f"{api}/documents/{document.id}/reprocess", headers=headers)
    assert reprocessed.status_code == 403, reprocessed.text

    # ...but the same key can still read (search implies read).
    listed = await client.get(f"{api}/documents", headers=headers)
    assert listed.status_code == 200, listed.text


async def test_ingest_scope_key_can_write(client, db_session, api) -> None:
    org, owner, collection, _document = await _collection_and_doc(db_session)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["ingest"], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"title": "x", "content": "hello", "collection_id": str(collection.id)},
    )
    assert created.status_code == 201, created.text


async def test_scopeless_key_cannot_read_knowledge(client, db_session, api) -> None:
    org, owner, _collection, document = await _collection_and_doc(db_session)
    # An empty-scoped key acting as the owner has the ACLs but no capability scope: it must
    # not be able to read knowledge content, so ``read`` is not a silent no-op.
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=[], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)

    listed = await client.get(f"{api}/documents", headers=headers)
    assert listed.status_code == 403, listed.text

    detail = await client.get(f"{api}/documents/{document.id}", headers=headers)
    assert detail.status_code == 403, detail.text

    chunks = await client.get(f"{api}/documents/{document.id}/chunks", headers=headers)
    assert chunks.status_code == 403, chunks.text


async def test_read_scope_key_can_read_but_not_write(client, db_session, api) -> None:
    org, owner, collection, document = await _collection_and_doc(db_session)
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["read"], acts_as_user=owner
    )
    headers = factories.api_key_headers(secret)

    detail = await client.get(f"{api}/documents/{document.id}", headers=headers)
    assert detail.status_code == 200, detail.text

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"title": "x", "content": "hello", "collection_id": str(collection.id)},
    )
    assert created.status_code == 403, created.text
