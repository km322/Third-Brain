"""Integration: multi-turn conversations + web grounding via the chat surface."""

from __future__ import annotations

import factories
import pytest

from app.core.config import settings

pytestmark = pytest.mark.integration


async def test_multi_turn_persists_history(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(f"{api}/conversations", headers=headers, json={"title": "Chat"})
    assert created.status_code == 201, created.text
    conv_id = created.json()["id"]

    for turn in ("Who is Alice Johnson?", "What does she lead?"):
        resp = await client.post(
            f"{api}/search/chat",
            headers=headers,
            json={"query": turn, "conversation_id": conv_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["conversation_id"] == conv_id

    detail = await client.get(f"{api}/conversations/{conv_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "Who is Alice Johnson?"
    # Assistant turns carry their citation payload (possibly empty on an empty KB).
    assert "citations" in messages[1]


async def test_conversation_ownership_enforced(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    other, _ = await factories.add_member(db_session, org=org)
    created = await client.post(
        f"{api}/conversations", headers=token_headers(owner.id, org.id), json={}
    )
    conv_id = created.json()["id"]

    # A different user cannot read someone else's conversation.
    resp = await client.get(
        f"{api}/conversations/{conv_id}", headers=token_headers(other.id, org.id)
    )
    assert resp.status_code == 404, resp.text
    # Nor drive a chat turn against it.
    chat = await client.post(
        f"{api}/search/chat",
        headers=token_headers(other.id, org.id),
        json={"query": "hi", "conversation_id": conv_id},
    )
    assert chat.status_code == 404, chat.text


async def test_web_grounding_adds_sources(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "stub")
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    resp = await client.post(
        f"{api}/search/chat",
        headers=headers,
        json={"query": "latest on renewable energy", "web": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["web_sources"]) >= 1
    assert all(s["url"].startswith("https://") for s in body["web_sources"])


async def test_web_grounding_off_by_default(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    resp = await client.post(
        f"{api}/search/chat",
        headers=token_headers(owner.id, org.id),
        json={"query": "anything"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["web_sources"] == []
