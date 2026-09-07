"""Integration: answer feedback + knowledge-gap analytics (retention opt-in)."""

from __future__ import annotations

import uuid

import factories
import pytest

from app.models.enums import OrgRole

pytestmark = pytest.mark.integration


async def test_feedback_and_knowledge_gaps(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    # A zero-result search is a knowledge gap and returns an insight id to rate.
    search = await client.post(
        f"{api}/search", headers=headers, json={"query": "nonexistent topic xyz"}
    )
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["hits"] == []
    insight_id = body["insight_id"]
    assert insight_id is not None

    fb = await client.post(
        f"{api}/feedback", headers=headers, json={"insight_id": insight_id, "rating": "down"}
    )
    assert fb.status_code == 200, fb.text

    gaps = await client.get(f"{api}/feedback/gaps", headers=headers)
    assert gaps.status_code == 200, gaps.text
    report = gaps.json()
    assert report["total_queries"] >= 1
    assert report["unanswered"] >= 1
    assert report["negative"] >= 1
    # Retention is OFF by default: no raw query text is exposed.
    assert report["query_text_retained"] is False
    assert report["top_gaps"] == []


async def test_query_text_retention_opt_in(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    enabled = await client.put(f"{api}/feedback/retention", headers=headers, json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True

    await client.post(f"{api}/search", headers=headers, json={"query": "unique-gap-phrase-42"})
    gaps = await client.get(f"{api}/feedback/gaps", headers=headers)
    report = gaps.json()
    assert report["query_text_retained"] is True
    assert any("unique-gap-phrase-42" in g for g in report["top_gaps"])


async def test_feedback_unknown_insight_404(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    resp = await client.post(
        f"{api}/feedback",
        headers=token_headers(owner.id, org.id),
        json={"insight_id": str(uuid.uuid4()), "rating": "up"},
    )
    assert resp.status_code == 404, resp.text


async def test_knowledge_gaps_requires_admin(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.get(f"{api}/feedback/gaps", headers=token_headers(viewer.id, org.id))
    assert resp.status_code == 403, resp.text
