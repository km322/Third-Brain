"""Integration: collections CRUD and permission-filtered listing."""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole

pytestmark = pytest.mark.integration


async def test_collection_crud_lifecycle(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/collections",
        headers=headers,
        json={"name": "Engineering Docs", "visibility": "org", "default_permission": "viewer"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"]
    assert body["permission"] == "manager"
    collection_id = body["id"]

    fetched = await client.get(f"{api}/collections/{collection_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["name"] == "Engineering Docs"

    updated = await client.patch(
        f"{api}/collections/{collection_id}",
        headers=headers,
        json={"name": "Renamed", "visibility": "private"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["visibility"] == "private"

    listed = await client.get(f"{api}/collections", headers=headers)
    assert listed.status_code == 200
    assert any(c["id"] == collection_id for c in listed.json())

    deleted = await client.delete(f"{api}/collections/{collection_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    gone = await client.get(f"{api}/collections/{collection_id}", headers=headers)
    assert gone.status_code == 404


async def test_create_requires_editor_role(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/collections",
        headers=token_headers(viewer.id, org.id),
        json={"name": "Nope", "visibility": "org"},
    )
    assert resp.status_code == 403, resp.text


async def test_divergent_embedding_model_is_rejected(
    client, db_session, token_headers, api
) -> None:
    """All chunks share one global vector space, so a per-collection embedding model in a
    different space is rejected rather than silently corrupting retrieval."""
    from app.core.config import settings

    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    rejected = await client.post(
        f"{api}/collections",
        headers=headers,
        json={"name": "Custom Embeds", "embedding_model": "text-embedding-3-large"},
    )
    assert rejected.status_code == 422, rejected.text

    # The platform model itself (an explicit no-op override) is accepted.
    ok = await client.post(
        f"{api}/collections",
        headers=headers,
        json={"name": "Default Embeds", "embedding_model": settings.EMBEDDING_MODEL},
    )
    assert ok.status_code == 201, ok.text


async def test_list_is_permission_filtered(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    org_visible = await factories.create_collection(
        db_session, org=org, owner=owner, visibility="org"
    )
    private = await factories.create_collection(
        db_session, org=org, owner=owner, visibility="private"
    )
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)

    listed = await client.get(f"{api}/collections", headers=token_headers(viewer.id, org.id))
    assert listed.status_code == 200, listed.text
    ids = {c["id"] for c in listed.json()}
    assert str(org_visible.id) in ids
    assert str(private.id) not in ids
