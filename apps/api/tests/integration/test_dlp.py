"""Integration: DLP scan labels documents and the oversharing report surfaces risk.

Drives the real ingestion pipeline (the DLP stage runs during ``ingest_document``) and the
governance endpoint against Postgres+pgvector+Redis.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole, Visibility

pytestmark = pytest.mark.integration


async def _create_and_ingest(client, api, headers, ingest_now, collection_id, title, content):
    resp = await client.post(
        f"{api}/documents/text",
        headers=headers,
        json={"collection_id": str(collection_id), "title": title, "content": content},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    await ingest_now(doc_id)
    return doc_id


async def test_dlp_labels_sensitivity_on_ingest(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    headers = token_headers(owner.id, org.id)

    conf_id = await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection.id,
        "HR file",
        "Employee SSN 123-45-6789 and card 4111 1111 1111 1111 on record.",
    )
    pii_id = await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection.id,
        "Contact",
        "Please email jane.doe@example.com to coordinate the visit.",
    )
    clean_id = await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        collection.id,
        "Roadmap",
        "The roadmap prioritises latency, reliability and developer experience.",
    )

    async def _doc(doc_id):
        r = await client.get(f"{api}/documents/{doc_id}", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    conf = await _doc(conf_id)
    assert conf["sensitivity"] == "confidential"
    # DLP default action is "label": the document still indexes.
    assert conf["status"] == "indexed"
    assert (await _doc(pii_id))["sensitivity"] == "pii"
    assert (await _doc(clean_id))["sensitivity"] == "none"


async def test_oversharing_report_flags_broadly_visible_sensitive_docs(
    client, db_session, token_headers, api, ingest_now
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    org_coll = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    private_coll = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE
    )
    headers = token_headers(owner.id, org.id)

    # Sensitive + org-visible => oversharing.
    over_id = await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        org_coll.id,
        "Payroll",
        "Card 4111 1111 1111 1111 belongs to the finance team.",
    )
    # Sensitive but private => NOT oversharing.
    await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        private_coll.id,
        "Private note",
        "Reach jane.doe@example.com privately.",
    )
    # Non-sensitive + org-visible => NOT oversharing.
    await _create_and_ingest(
        client,
        api,
        headers,
        ingest_now,
        org_coll.id,
        "Public roadmap",
        "The roadmap prioritises latency and reliability.",
    )

    report = await client.get(f"{api}/governance/oversharing", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    flagged_ids = {item["document_id"] for item in body["items"]}
    assert over_id in flagged_ids
    assert len(body["items"]) == 1  # only the org-visible sensitive doc
    assert body["items"][0]["effective_visibility"] == "org"
    # Summary counts every sensitive doc regardless of visibility (1 confidential + 1 pii).
    assert body["summary"]["confidential"] >= 1
    assert body["summary"]["pii"] >= 1


async def test_oversharing_requires_admin(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.get(
        f"{api}/governance/oversharing", headers=token_headers(viewer.id, org.id)
    )
    assert resp.status_code == 403, resp.text
