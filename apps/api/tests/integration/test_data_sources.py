"""Integration: data-source sync mirrors documents AND source ACLs into retrieval.

Drives the real API against Postgres+pgvector+Redis. The reference local-folder connector
lets us assert the whole promise end-to-end: a user only retrieves synced documents whose
source-system ACL grants them access, permissions resolve through the SAME engine as
everything else, unmapped principals are backfilled when mapped, re-sync is idempotent, and
upstream deletions are pruned.
"""

from __future__ import annotations

import json
import uuid

import factories
import pytest
from sqlalchemy import func, select

from app.models.access import AccessGrant
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus, OrgRole, ResourceType, Visibility

pytestmark = pytest.mark.integration


async def _doc_ids(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {h["document_id"] for h in resp.json()["hits"]}


async def _create_source(client, api, headers, *, collection_id, root: str, name: str = "Folder"):
    resp = await client.post(
        f"{api}/data-sources",
        headers=headers,
        json={
            "name": name,
            "kind": "local_folder",
            "collection_id": str(collection_id),
            "config": {"root": root},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_sync_ingests_and_scopes_by_source_acl(
    client, db_session, token_headers, api, tmp_path
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    bob, _ = await factories.add_member(
        db_session, org=org, role=OrgRole.VIEWER, email="bob@example.com"
    )
    carol, _ = await factories.add_member(
        db_session, org=org, role=OrgRole.VIEWER, email="carol@example.com"
    )
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    owner_h = token_headers(owner.id, org.id)
    bob_h = token_headers(bob.id, org.id)
    carol_h = token_headers(carol.id, org.id)

    src = tmp_path / "src"
    src.mkdir()
    (src / "alice_doc.md").write_text("Aurora roadmap secret sauce for the alice project.")
    (src / "bob_doc.md").write_text("Warehouse logistics quarterly plan for bob operations.")
    (src / ".acl.json").write_text(
        json.dumps({"files": {"alice_doc.md": [owner.email], "bob_doc.md": ["bob@example.com"]}})
    )

    source_id = await _create_source(
        client, api, owner_h, collection_id=collection.id, root=str(src)
    )
    synced = await client.post(f"{api}/data-sources/{source_id}/sync", headers=owner_h)
    assert synced.status_code == 200, synced.text
    assert synced.json()["created"] == 2

    docs = (
        (
            await db_session.execute(
                select(Document).where(Document.source_id == uuid.UUID(source_id))
            )
        )
        .scalars()
        .all()
    )
    by_ext = {d.external_id: d for d in docs}
    assert set(by_ext) == {"alice_doc.md", "bob_doc.md"}
    for d in docs:
        assert d.status == DocumentStatus.INDEXED, d.status
        assert d.chunk_count > 0
        assert d.visibility == Visibility.PRIVATE  # mirrors source, not the collection
    alice_id, bob_id = str(by_ext["alice_doc.md"].id), str(by_ext["bob_doc.md"].id)

    # Synced ACLs are materialised as source-scoped AccessGrants.
    grants = (
        (
            await db_session.execute(
                select(AccessGrant).where(
                    AccessGrant.org_id == org.id,
                    AccessGrant.resource_type == ResourceType.DOCUMENT,
                    AccessGrant.source_id == uuid.UUID(source_id),
                )
            )
        )
        .scalars()
        .all()
    )
    granted = {(str(g.resource_id), g.principal_id) for g in grants}
    assert (alice_id, owner.id) in granted
    assert (bob_id, bob.id) in granted

    # Bob (email auto-mapped) sees only his document, never alice's.
    bob_alice = await _doc_ids(
        await client.post(
            f"{api}/search", headers=bob_h, json={"query": "Aurora roadmap secret sauce"}
        )
    )
    assert alice_id not in bob_alice
    bob_own = await _doc_ids(
        await client.post(
            f"{api}/search", headers=bob_h, json={"query": "warehouse logistics quarterly"}
        )
    )
    assert bob_id in bob_own

    # Carol is in no ACL: she sees neither document.
    carol_hits = await _doc_ids(
        await client.post(
            f"{api}/search", headers=carol_h, json={"query": "warehouse logistics Aurora roadmap"}
        )
    )
    assert alice_id not in carol_hits and bob_id not in carol_hits

    # Owner is an org admin (all-access): sees alice's document.
    owner_hits = await _doc_ids(
        await client.post(f"{api}/search", headers=owner_h, json={"query": "Aurora roadmap"})
    )
    assert alice_id in owner_hits


async def test_identity_mapping_backfills_grants(
    client, db_session, token_headers, api, tmp_path
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    carol, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    team = await factories.create_team(db_session, org=org, name="Engineering")
    await factories.add_user_to_team(db_session, team=team, user=carol)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    owner_h = token_headers(owner.id, org.id)
    carol_h = token_headers(carol.id, org.id)

    src = tmp_path / "src"
    src.mkdir()
    (src / "eng_doc.md").write_text("Engineering design review for the payments platform.")
    (src / ".acl.json").write_text(json.dumps({"files": {"eng_doc.md": ["group:engineering"]}}))

    source_id = await _create_source(
        client, api, owner_h, collection_id=collection.id, root=str(src)
    )
    assert (await client.post(f"{api}/data-sources/{source_id}/sync", headers=owner_h)).json()[
        "created"
    ] == 1

    # The 'engineering' group is unmapped -> carol has no access yet.
    before = await _doc_ids(
        await client.post(
            f"{api}/search", headers=carol_h, json={"query": "payments platform design"}
        )
    )
    assert before == set()

    # The principal was recorded so the admin can map it.
    principals = await client.get(f"{api}/data-sources/{source_id}/principals", headers=owner_h)
    assert principals.status_code == 200, principals.text
    assert any(p["external_id"] == "engineering" and not p["mapped"] for p in principals.json())

    # Map group -> team; grants backfill for carol's team.
    mapped = await client.post(
        f"{api}/data-sources/identities",
        headers=owner_h,
        json={
            "provider": "local_folder",
            "external_id": "engineering",
            "kind": "group",
            "team_id": str(team.id),
        },
    )
    assert mapped.status_code == 201, mapped.text
    assert mapped.json()["grants_backfilled"] == 1

    # Now carol retrieves the document via her team membership.
    after = await _doc_ids(
        await client.post(
            f"{api}/search", headers=carol_h, json={"query": "payments platform design"}
        )
    )
    assert len(after) == 1


async def test_resync_is_idempotent_and_prunes_deletions(
    client, db_session, token_headers, api, tmp_path
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    owner_h = token_headers(owner.id, org.id)

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("Alpha document about migration planning.")
    (src / "b.md").write_text("Beta document about incident response.")

    source_id = await _create_source(
        client, api, owner_h, collection_id=collection.id, root=str(src)
    )

    async def _counts() -> tuple[int, int]:
        docs = (
            await db_session.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.source_id == uuid.UUID(source_id))
            )
        ).scalar_one()
        chunks = (
            await db_session.execute(
                select(func.count())
                .select_from(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.source_id == uuid.UUID(source_id))
            )
        ).scalar_one()
        return docs, chunks

    first = await client.post(f"{api}/data-sources/{source_id}/sync", headers=owner_h)
    assert first.json()["created"] == 2
    docs1, chunks1 = await _counts()
    assert docs1 == 2 and chunks1 > 0

    # Re-sync unchanged content: nothing created, no duplicate chunks.
    second = await client.post(f"{api}/data-sources/{source_id}/sync", headers=owner_h)
    assert second.json()["created"] == 0
    docs2, chunks2 = await _counts()
    assert docs2 == 2
    assert chunks2 == chunks1

    # Delete a file upstream; a full sync prunes the vanished document.
    (src / "b.md").unlink()
    third = await client.post(f"{api}/data-sources/{source_id}/sync", headers=owner_h)
    assert third.json()["deleted"] == 1
    docs3, _ = await _counts()
    assert docs3 == 1
    remaining = (
        (
            await db_session.execute(
                select(Document).where(Document.source_id == uuid.UUID(source_id))
            )
        )
        .scalars()
        .all()
    )
    assert [d.external_id for d in remaining] == ["a.md"]


async def test_local_folder_root_outside_allowlist_is_rejected(
    client, db_session, token_headers, api
) -> None:
    """A tenant admin cannot point a local_folder source at arbitrary server paths.

    Without this the connector would read /etc, /proc/self/environ (leaking SECRET_KEY and
    every env secret), or another tenant's uploads into the caller's own collection.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    owner_h = token_headers(owner.id, org.id)
    for bad_root in ("/etc", "/proc/self/environ", "/"):
        resp = await client.post(
            f"{api}/data-sources",
            headers=owner_h,
            json={
                "name": "Escape",
                "kind": "local_folder",
                "collection_id": str(collection.id),
                "config": {"root": bad_root},
            },
        )
        assert resp.status_code == 400, f"{bad_root}: {resp.status_code} {resp.text}"
