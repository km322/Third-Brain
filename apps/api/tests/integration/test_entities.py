"""Integration: entity enrichment during ingestion + permission-scoped browse."""

from __future__ import annotations

import uuid

import factories
import pytest

from app.models.document import Document
from app.models.enums import DocumentStatus, OrgRole, Visibility

pytestmark = pytest.mark.integration

_CONTENT = "Alice Johnson leads Project Aurora at Acme Corporation for the platform team."


async def _ingest(client, api, headers, ingest_now, collection_id, title, content):
    resp = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection_id), "title": title, "content": content},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    await ingest_now(doc_id)
    return doc_id


async def test_entities_extracted_and_browsable(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    doc_id = await _ingest(client, api, headers, ingest_now, collection.id, "Memo", _CONTENT)

    listed = await client.get(f"{api}/entities", headers=headers)
    assert listed.status_code == 200, listed.text
    entities = listed.json()
    names = {e["name"] for e in entities}
    assert {"Alice Johnson", "Project Aurora", "Acme Corporation"} <= names
    assert all(e["document_count"] >= 1 for e in entities)

    # Filter by kind.
    orgs = await client.get(f"{api}/entities?kind=org", headers=headers)
    assert any(e["name"] == "Acme Corporation" for e in orgs.json())

    # Browse documents mentioning an entity.
    acme = next(e for e in entities if e["name"] == "Acme Corporation")
    docs = await client.get(f"{api}/entities/{acme['id']}/documents", headers=headers)
    assert docs.status_code == 200, docs.text
    assert [d["id"] for d in docs.json()] == [doc_id]


async def test_content_edit_resyncs_entities(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """An editor save re-syncs the entity index: names removed by the rewrite disappear,
    names it introduces are linked, and untouched ones survive - the same contract as a
    worker re-ingest, since ``index_content`` is the shared write path."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    doc_id = await _ingest(client, api, headers, ingest_now, collection.id, "Memo", _CONTENT)

    updated = await client.put(
        f"{api}/documents/{doc_id}/content",
        headers=headers,
        json={"content": "Bob Stone now leads Project Aurora at Initech Corporation."},
    )
    assert updated.status_code == 200, updated.text

    listed = await client.get(f"{api}/entities", headers=headers)
    assert listed.status_code == 200, listed.text
    names = {e["name"] for e in listed.json()}
    assert {"Bob Stone", "Initech Corporation", "Project Aurora"} <= names
    # These appeared only in the pre-edit text, so keeping them would be stale index.
    assert "Alice Johnson" not in names
    assert "Acme Corporation" not in names


async def test_entity_index_is_permission_scoped(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    outsider, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    private = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    await _ingest(
        client, api, token_headers(owner.id, org.id), ingest_now, private.id, "Secret", _CONTENT
    )

    # The outsider cannot see the private collection, so its entities are invisible.
    listed = await client.get(f"{api}/entities", headers=token_headers(outsider.id, org.id))
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


async def test_entities_of_quarantined_document_are_not_browsable(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """Quarantining a previously indexed document withdraws its entities too.

    Entity names are lifted verbatim out of document text, and quarantining an
    already-indexed document leaves its ``DocumentEntity`` rows in place (the scanner
    gate returns before the resync). Without a status filter the entity index would be
    the one surface still exposing content the rest of the app withholds pending review.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    doc_id = await _ingest(client, api, headers, ingest_now, collection.id, "Memo", _CONTENT)

    # Positive control: while INDEXED the entity and its document are both browsable.
    entities = (await client.get(f"{api}/entities", headers=headers)).json()
    acme = next(e for e in entities if e["name"] == "Acme Corporation")
    docs = await client.get(f"{api}/entities/{acme['id']}/documents", headers=headers)
    assert [d["id"] for d in docs.json()] == [doc_id]

    document = await db_session.get(Document, uuid.UUID(doc_id))
    document.status = DocumentStatus.QUARANTINED
    await db_session.commit()

    # The only document mentioning it is withheld, so the entity leaves the index...
    after = (await client.get(f"{api}/entities", headers=headers)).json()
    assert "Acme Corporation" not in {e["name"] for e in after}

    # ...and it cannot be reached by asking for that entity's documents directly.
    docs_after = await client.get(f"{api}/entities/{acme['id']}/documents", headers=headers)
    assert docs_after.status_code == 200, docs_after.text
    assert docs_after.json() == []
