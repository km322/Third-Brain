"""Integration: document/answer verification, freshness, and answer surfacing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import factories
import pytest

from app.models.document import Document
from app.models.enums import OrgRole, VerificationStatus, Visibility
from app.services.verification import flag_stale

pytestmark = pytest.mark.integration


async def test_document_verify_and_unverify(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    doc = await factories.create_document(db_session, org=org, collection=collection)
    headers = token_headers(owner.id, org.id)

    verified = await client.post(
        f"{api}/documents/{doc.id}/verify", headers=headers, json={"review_interval_days": 30}
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["verification_status"] == "verified"
    assert body["verified_at"] is not None
    assert body["expires_at"] is not None

    cleared = await client.post(f"{api}/documents/{doc.id}/unverify", headers=headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["verification_status"] == "unverified"
    assert cleared.json()["expires_at"] is None


async def test_verified_answer_surfaces_in_search(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/answers",
        headers=headers,
        json={
            "question": "What is the PTO policy?",
            "answer": "20 days per year.",
            "visibility": "org",
        },
    )
    assert created.status_code == 201, created.text
    answer_id = created.json()["id"]

    # Unverified answers are not surfaced.
    unverified_search = await client.post(
        f"{api}/search", headers=headers, json={"query": "PTO policy details"}
    )
    assert unverified_search.status_code == 200, unverified_search.text
    assert unverified_search.json()["answers"] == []

    verify = await client.post(f"{api}/answers/{answer_id}/verify", headers=headers, json={})
    assert verify.status_code == 200, verify.text
    assert verify.json()["verification_status"] == "verified"

    surfaced = await client.post(
        f"{api}/search", headers=headers, json={"query": "PTO policy details"}
    )
    answers = surfaced.json()["answers"]
    assert any(a["id"] == answer_id for a in answers)


async def test_answer_write_requires_editor(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/answers",
        headers=token_headers(viewer.id, org.id),
        json={"question": "q?", "answer": "a"},
    )
    assert resp.status_code == 403, resp.text


async def test_collection_scoped_answer_respects_permission(
    client, db_session, token_headers, api
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    bob, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    private = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    created = await client.post(
        f"{api}/answers",
        headers=token_headers(owner.id, org.id),
        json={"question": "internal?", "answer": "secret", "collection_id": str(private.id)},
    )
    assert created.status_code == 201, created.text
    answer_id = created.json()["id"]

    # Bob has no access to the private collection -> cannot read the scoped answer.
    bob_get = await client.get(f"{api}/answers/{answer_id}", headers=token_headers(bob.id, org.id))
    assert bob_get.status_code == 404, bob_get.text
    bob_list = await client.get(f"{api}/answers", headers=token_headers(bob.id, org.id))
    assert all(a["id"] != answer_id for a in bob_list.json())


async def test_flag_stale_marks_past_review(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    doc = await factories.create_document(db_session, org=org, collection=collection)
    headers = token_headers(owner.id, org.id)

    await client.post(
        f"{api}/documents/{doc.id}/verify", headers=headers, json={"review_interval_days": 30}
    )
    # Backdate the review date so the sweep considers it stale.
    fresh = await db_session.get(Document, doc.id)
    await db_session.refresh(fresh)
    fresh.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    result = await flag_stale(db_session)
    assert result["documents"] >= 1
    await db_session.refresh(fresh)
    assert fresh.verification_status == VerificationStatus.STALE
