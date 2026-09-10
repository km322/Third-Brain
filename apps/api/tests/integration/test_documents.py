"""Integration: document ingestion lifecycle (text + upload) to INDEXED with chunks."""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole, Visibility

pytestmark = pytest.mark.integration

_PNG_1PX = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
"""A canonical valid 1x1 PNG (magic bytes + IHDR/IDAT/IEND), small enough to inline."""

_TEXT = (
    "Retrieval augmented generation grounds a language model in an organization's own "
    "documents so answers stay accurate, current and attributable to a source."
)


async def test_text_document_ingests_to_indexed_with_chunks(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """``ingest_now`` drives ingestion inline, since the enqueue is neutralised by the client
    fixture."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection.id), "title": "RAG", "content": _TEXT},
    )
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["status"] == "pending"
    document_id = doc["id"]

    await ingest_now(document_id)

    detail = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "indexed"
    assert detail.json()["chunk_count"] >= 1

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=headers)
    assert chunks.status_code == 200, chunks.text
    payload = chunks.json()
    assert len(payload) >= 1
    assert payload[0]["content"]
    assert payload[0]["chunk_index"] == 0

    listing = await client.get(
        f"{api}/documents",
        headers=headers,
        params={"collection_id": str(collection.id)},
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] >= 1
    assert any(item["id"] == document_id for item in listing.json()["items"])


async def test_reprocess_and_delete(client, db_session, token_headers, api, ingest_now) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection.id), "title": "Doc", "content": _TEXT},
    )
    document_id = created.json()["id"]
    await ingest_now(document_id)

    reprocessed = await client.post(f"{api}/documents/{document_id}/reprocess", headers=headers)
    assert reprocessed.status_code == 200, reprocessed.text
    assert reprocessed.json()["status"] == "pending"
    await ingest_now(document_id)

    deleted = await client.delete(f"{api}/documents/{document_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    gone = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert gone.status_code == 404


async def test_file_upload_ingests(client, db_session, token_headers, api, ingest_now) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/documents/upload",
        headers=headers,
        data={"collection_id": str(collection.id), "visibility": Visibility.ORG.value},
        files={"file": ("notes.txt", _TEXT.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    document_id = resp.json()["id"]
    assert resp.json()["source_type"] == "file"

    await ingest_now(document_id)
    detail = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "indexed"
    assert detail.json()["chunk_count"] >= 1


async def test_oversized_upload_is_rejected(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    """The upload is streamed with a running size cap, so a body over the limit is rejected
    with 413 rather than being fully buffered into memory first."""
    import app.api.routes.documents as documents_route

    monkeypatch.setattr(documents_route, "MAX_UPLOAD_BYTES", 1024)
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/documents/upload",
        headers=headers,
        data={"collection_id": str(collection.id)},
        files={"file": ("big.txt", b"x" * 4096, "text/plain")},
    )
    assert resp.status_code == 413, resp.text


async def test_content_read_and_edit_roundtrip(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection.id), "title": "Editable", "content": _TEXT},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    await ingest_now(document_id)

    got = await client.get(f"{api}/documents/{document_id}/content", headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["content"].strip() == _TEXT
    assert body["editable"] is True
    assert body["chunk_count"] >= 1

    new_content = (
        "# Edited\n\nThe document was edited in the dashboard editor and re-indexed inline."
    )
    saved = await client.put(
        f"{api}/documents/{document_id}/content",
        headers=headers,
        json={"content": new_content},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "indexed"
    assert saved.json()["verification_status"] == "unverified"

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=headers)
    joined = "\n\n".join(c["content"] for c in chunks.json())
    assert "edited in the dashboard editor" in joined

    reread = await client.get(f"{api}/documents/{document_id}/content", headers=headers)
    assert "# Edited" in reread.json()["content"]


async def test_content_save_with_secret_is_rejected_and_document_untouched(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """The stored document must be left untouched by the rejected save."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection.id), "title": "Clean", "content": _TEXT},
    )
    document_id = created.json()["id"]
    await ingest_now(document_id)

    leaky = 'Deploy notes.\n\naws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n'
    saved = await client.put(
        f"{api}/documents/{document_id}/content",
        headers=headers,
        json={"content": leaky},
    )
    assert saved.status_code == 422, saved.text
    assert "secrets" in saved.json()["detail"]

    reread = await client.get(f"{api}/documents/{document_id}/content", headers=headers)
    assert reread.json()["content"].strip() == _TEXT


async def test_content_edit_requires_editor(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    owner_headers = token_headers(owner.id, org.id)
    viewer_headers = token_headers(viewer.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=owner_headers,
        json={"collection_id": str(collection.id), "title": "Gated", "content": _TEXT},
    )
    document_id = created.json()["id"]
    await ingest_now(document_id)

    read = await client.get(f"{api}/documents/{document_id}/content", headers=viewer_headers)
    assert read.status_code == 200, read.text

    denied = await client.put(
        f"{api}/documents/{document_id}/content",
        headers=viewer_headers,
        json={"content": "viewer tries to edit"},
    )
    assert denied.status_code == 403, denied.text


async def test_image_upload_indexes_summary_chunk_with_capability_link(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """The summary chunk names the image and carries the absolute capability link, and a wrong
    token of plausible shape 404s without leaking anything."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/documents/upload",
        headers=headers,
        data={"collection_id": str(collection.id), "visibility": Visibility.ORG.value},
        files={"file": ("diagram.png", _PNG_1PX, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    document_id = resp.json()["id"]

    await ingest_now(document_id)
    detail = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "indexed", detail.json().get("error")

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=headers)
    assert chunks.status_code == 200
    content = chunks.json()[0]["content"]
    assert content.startswith("[Image] diagram.png")
    assert "/api/v1/files/" in content

    token = content.rsplit("/files/", 1)[1].split()[0].strip()
    served = await client.get(f"{api}/files/{token}")
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == _PNG_1PX

    missing = await client.get(f"{api}/files/{'x' * len(token)}")
    assert missing.status_code == 404


async def test_non_image_bytes_labelled_as_image_are_still_scanned_and_not_served(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """A client-supplied image/* mime must not become a scan bypass or a public
    byte-server: the ingest path sniffs the real bytes, so a secret-bearing file
    mislabelled as a PNG still quarantines and never gets a capability link. The secret gate
    saw the REAL bytes, which is why the document ends up quarantined rather than indexed."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    leaky = b'runbook\n\naws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n'
    resp = await client.post(
        f"{api}/documents/upload",
        headers=headers,
        data={"collection_id": str(collection.id), "visibility": Visibility.ORG.value},
        files={"file": ("runbook.txt", leaky, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    document_id = resp.json()["id"]

    await ingest_now(document_id)
    detail = await client.get(f"{api}/documents/{document_id}", headers=headers)
    assert detail.json()["status"] == "quarantined"
    assert detail.json()["image_url"] is None

    chunks = await client.get(f"{api}/documents/{document_id}/chunks", headers=headers)
    assert chunks.json() == []


async def test_image_document_cannot_be_text_edited(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """PUT /content on an image would overwrite the original image blob - refuse it, and prove
    the original bytes are still served intact afterwards."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/documents/upload",
        headers=headers,
        data={"collection_id": str(collection.id), "visibility": Visibility.ORG.value},
        files={"file": ("diagram.png", _PNG_1PX, "image/png")},
    )
    document_id = resp.json()["id"]
    await ingest_now(document_id)

    read = await client.get(f"{api}/documents/{document_id}/content", headers=headers)
    assert read.status_code == 200
    assert read.json()["editable"] is False

    saved = await client.put(
        f"{api}/documents/{document_id}/content",
        headers=headers,
        json={"content": "overwrite the image with text"},
    )
    assert saved.status_code == 409, saved.text

    doc = await client.get(f"{api}/documents/{document_id}", headers=headers)
    token = doc.json()["image_url"].rsplit("/files/", 1)[1]
    served = await client.get(f"{api}/files/{token}")
    assert served.status_code == 200
    assert served.content == _PNG_1PX


async def test_deleting_a_document_reaps_its_orphaned_entities(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """Entity links cascade on delete, but the entity rows themselves must not linger:
    an index full of zero-link entities is invisible in the API yet never cleaned up."""
    from sqlalchemy import select

    from app.models.entity import Entity

    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={
            "collection_id": str(collection.id),
            "title": "Entities",
            "content": "Anthropic and Redis power the Zenithal Quorum rollout.",
        },
    )
    document_id = created.json()["id"]
    await ingest_now(document_id)

    before = (
        (await db_session.execute(select(Entity).where(Entity.org_id == org.id))).scalars().all()
    )
    assert before, "expected the ingest to create entities"

    deleted = await client.delete(f"{api}/documents/{document_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    after = (
        (await db_session.execute(select(Entity).where(Entity.org_id == org.id))).scalars().all()
    )
    assert after == [], f"orphaned entities left behind: {[e.name for e in after]}"
