"""Integration: analytics overview, usage series and the audit log."""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole, Visibility

pytestmark = pytest.mark.integration


async def test_overview_usage_and_audit(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        content="Analytics aggregates usage records and audit events per organization.",
        created_by=owner,
    )
    await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    headers = token_headers(owner.id, org.id)

    # A real search generates a SEARCH usage record and a search.performed audit entry.
    search = await client.post(f"{api}/search", headers=headers, json={"query": "usage records"})
    assert search.status_code == 200, search.text

    overview = await client.get(f"{api}/analytics/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    ov = overview.json()
    assert ov["documents"] >= 1
    assert ov["collections"] >= 1
    assert ov["members"] >= 2  # owner + invited editor
    assert ov["searches_7d"] >= 1

    usage = await client.get(f"{api}/analytics/usage", headers=headers, params={"days": 30})
    assert usage.status_code == 200, usage.text
    us = usage.json()
    assert us["total_requests"] >= 1
    assert len(us["by_day"]) == 30
    assert any(k["kind"] == "search" for k in us["by_kind"])

    audit = await client.get(f"{api}/analytics/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    actions = {entry["action"] for entry in audit.json()["items"]}
    assert "search.performed" in actions


async def test_audit_is_admin_only(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.get(f"{api}/analytics/audit", headers=token_headers(viewer.id, org.id))
    assert resp.status_code == 403, resp.text
