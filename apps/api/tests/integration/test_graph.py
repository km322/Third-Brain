"""Integration: the permission-scoped document-similarity graph over real pgvector.

Documents are embedded with the offline ``fake`` provider into genuine ``vector`` columns,
so ``GET /graph`` exercises real ``avg(vector)`` centroids and a real ``cosine_distance``
self-join. The security-critical guarantee mirrors search: a document the caller cannot
view never appears as a node and never as an edge endpoint, on either surface.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole, PermissionLevel, Visibility

pytestmark = pytest.mark.integration

# Two documents that share almost all of their vocabulary embed to nearly-identical
# hashed vectors under the offline provider, so their centroids are highly similar and an
# edge forms; the unrelated document uses a disjoint vocabulary and stays below threshold.
_CLOUD_A = (
    "Cloud infrastructure autoscaling clusters manage container orchestration and "
    "kubernetes deployment pipelines across regions."
)
_CLOUD_B = (
    "Cloud infrastructure autoscaling clusters manage container orchestration and "
    "kubernetes deployment pipelines across zones."
)
_UNRELATED = "Baroque violin sonatas explore counterpoint melody harmony and tempo rubato."


async def test_graph_returns_nodes_and_connects_similar_documents(
    client, db_session, token_headers, api
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    doc_a = await factories.create_document(
        db_session, org=org, collection=collection, title="Cloud A", content=_CLOUD_A
    )
    doc_b = await factories.create_document(
        db_session, org=org, collection=collection, title="Cloud B", content=_CLOUD_B
    )
    await factories.create_document(
        db_session, org=org, collection=collection, title="Music", content=_UNRELATED
    )
    headers = token_headers(owner.id, org.id)

    resp = await client.get(f"{api}/graph", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_visible"] == 3
    assert body["truncated"] is False
    assert body["limit"] == 400
    ids = {n["id"] for n in body["nodes"]}
    assert {str(doc_a.id), str(doc_b.id)} <= ids
    assert len(body["nodes"]) == 3

    node_a = next(n for n in body["nodes"] if n["id"] == str(doc_a.id))
    assert node_a["title"] == "Cloud A"
    assert node_a["collection_id"] == str(collection.id)
    assert node_a["collection_name"] == collection.name
    assert node_a["chunk_count"] >= 1
    assert node_a["source_type"] == "text"
    assert node_a["created_at"]

    assert body["edges"], "expected the two topically-similar documents to be connected"
    for edge in body["edges"]:
        assert 0.0 <= edge["weight"] <= 1.0
        assert edge["source"] < edge["target"]
    # The near-duplicate cloud documents must be directly connected.
    pair = tuple(sorted([str(doc_a.id), str(doc_b.id)]))
    assert any((e["source"], e["target"]) == pair for e in body["edges"])
    # Degree is the count of surviving incident edges.
    for node in body["nodes"]:
        incident = sum(1 for e in body["edges"] if node["id"] in (e["source"], e["target"]))
        assert node["degree"] == incident


async def test_graph_excludes_documents_the_caller_cannot_see(
    client, db_session, token_headers, api
) -> None:
    """A non-admin without a grant on a private collection gets NO node and NO edge for its
    documents, and its neighbors endpoint 404s for them -- while the owner sees them."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    public = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    private = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        visibility=Visibility.PRIVATE,
        default_permission=PermissionLevel.VIEWER,
    )
    public_doc = await factories.create_document(
        db_session, org=org, collection=public, title="Shared", content=_CLOUD_A
    )
    secret_a = await factories.create_document(
        db_session, org=org, collection=private, title="Secret A", content=_CLOUD_A
    )
    secret_b = await factories.create_document(
        db_session, org=org, collection=private, title="Secret B", content=_CLOUD_B
    )
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)

    owner_headers = token_headers(owner.id, org.id)
    viewer_headers = token_headers(viewer.id, org.id)

    # The owner (admin) sees all three documents and the two secret ones are connected.
    owner_body = (await client.get(f"{api}/graph", headers=owner_headers)).json()
    owner_ids = {n["id"] for n in owner_body["nodes"]}
    assert {str(public_doc.id), str(secret_a.id), str(secret_b.id)} <= owner_ids
    assert owner_body["total_visible"] == 3
    secret_pair = tuple(sorted([str(secret_a.id), str(secret_b.id)]))
    assert any((e["source"], e["target"]) == secret_pair for e in owner_body["edges"])

    # The ungranted viewer sees ONLY the org-visible document: no private node, no edge
    # touching a private document.
    resp = await client.get(f"{api}/graph", headers=viewer_headers)
    assert resp.status_code == 200, resp.text
    viewer_body = resp.json()
    viewer_ids = {n["id"] for n in viewer_body["nodes"]}
    assert viewer_ids == {str(public_doc.id)}
    assert viewer_body["total_visible"] == 1
    private_ids = {str(secret_a.id), str(secret_b.id)}
    for edge in viewer_body["edges"]:
        assert edge["source"] not in private_ids
        assert edge["target"] not in private_ids

    # Neighbors of a private document: 404 for the viewer, 200 for the owner.
    viewer_neighbors = await client.get(
        f"{api}/graph/documents/{secret_a.id}/neighbors", headers=viewer_headers
    )
    assert viewer_neighbors.status_code == 404, viewer_neighbors.text

    owner_neighbors = await client.get(
        f"{api}/graph/documents/{secret_a.id}/neighbors", headers=owner_headers
    )
    assert owner_neighbors.status_code == 200, owner_neighbors.text
    nbody = owner_neighbors.json()
    assert nbody["center_id"] == str(secret_a.id)
    node_ids = {n["id"] for n in nbody["nodes"]}
    assert str(secret_a.id) in node_ids
    assert str(secret_b.id) in node_ids
    assert any(str(secret_b.id) in (e["source"], e["target"]) for e in nbody["edges"])


async def test_graph_grant_makes_private_documents_visible(
    client, db_session, token_headers, api
) -> None:
    """An explicit grant on the private collection surfaces its documents in the graph."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    private = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        visibility=Visibility.PRIVATE,
        default_permission=PermissionLevel.VIEWER,
    )
    secret = await factories.create_document(
        db_session, org=org, collection=private, title="Secret", content=_CLOUD_A
    )
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    from app.models.enums import ResourceType

    await factories.grant_user(
        db_session,
        org=org,
        user=viewer,
        resource_type=ResourceType.COLLECTION,
        resource_id=private.id,
        permission=PermissionLevel.VIEWER,
    )
    viewer_headers = token_headers(viewer.id, org.id)

    body = (await client.get(f"{api}/graph", headers=viewer_headers)).json()
    assert {n["id"] for n in body["nodes"]} == {str(secret.id)}
    neighbors = await client.get(
        f"{api}/graph/documents/{secret.id}/neighbors", headers=viewer_headers
    )
    assert neighbors.status_code == 200, neighbors.text


async def test_graph_collection_filter_restricts_nodes(
    client, db_session, token_headers, api
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    cloud = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    music = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    cloud_doc = await factories.create_document(
        db_session, org=org, collection=cloud, title="Cloud", content=_CLOUD_A
    )
    await factories.create_document(
        db_session, org=org, collection=music, title="Music", content=_UNRELATED
    )
    headers = token_headers(owner.id, org.id)

    resp = await client.get(
        f"{api}/graph", headers=headers, params={"collection_id": str(cloud.id)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {n["id"] for n in body["nodes"]} == {str(cloud_doc.id)}
    assert all(n["collection_id"] == str(cloud.id) for n in body["nodes"])
    assert body["total_visible"] == 1


async def test_graph_limit_caps_nodes_and_sets_truncated(
    client, db_session, token_headers, api
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    for i in range(3):
        await factories.create_document(
            db_session, org=org, collection=collection, title=f"Doc {i}", content=_CLOUD_A
        )
    headers = token_headers(owner.id, org.id)

    resp = await client.get(f"{api}/graph", headers=headers, params={"limit": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["nodes"]) == 2
    assert body["total_visible"] == 3
    assert body["truncated"] is True
    assert body["limit"] == 2


async def test_graph_empty_org_returns_empty(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    resp = await client.get(f"{api}/graph", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["total_visible"] == 0
    assert body["truncated"] is False
