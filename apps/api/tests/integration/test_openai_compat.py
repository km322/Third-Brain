"""Integration: the OpenAI-compatible surface (/v1), authenticated by an API key."""

from __future__ import annotations

import base64
import struct

import factories
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.audit import AuditLog
from app.models.enums import Visibility

pytestmark = pytest.mark.integration


async def test_v1_errors_use_openai_shape(client, db_session, api) -> None:
    """Errors on /v1 use OpenAI's {"error": {...}} envelope (not Third Brain's flat one)
    and validation failures map to 400, so OpenAI SDKs/wrappers parse them (finding 30).

    Unauthenticated is a 401 authentication_error in the OpenAI shape, and a malformed body
    is 400 (never 422) in the same shape.
    """
    unauth = await client.post("/v1/chat/completions", json={"messages": []})
    assert unauth.status_code == 401, unauth.text
    err = unauth.json()["error"]
    assert err["type"] == "authentication_error" and err["message"]

    _org, _owner, headers = await _org_with_key(db_session, ["search"])
    bad = await client.post("/v1/chat/completions", headers=headers, json={"stream": "nope"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["type"] == "invalid_request_error"


async def _org_with_key(db_session, scopes):
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG
    )
    await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Photosynthesis",
        content="Plants convert sunlight into chemical energy stored as glucose.",
        created_by=owner,
    )
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=list(scopes), acts_as_user=owner
    )
    return org, owner, factories.api_key_headers(secret)


async def test_models_embeddings_and_chat(client, db_session, api) -> None:
    """The three /v1 surfaces work end-to-end for a scoped key.

    "third-brain" - the product-facing model the docs tell developers to use - must be
    discoverable in the model list, and the chat response carries the Third Brain extension:
    the retrieval citations behind the grounded answer.
    """
    _org, _owner, headers = await _org_with_key(db_session, ["search", "read"])

    models = await client.get("/v1/models", headers=headers)
    assert models.status_code == 200, models.text
    ids = {m["id"] for m in models.json()["data"]}
    assert settings.EMBEDDING_MODEL in ids
    assert settings.DEFAULT_COMPLETION_MODEL in ids
    assert "third-brain" in ids

    emb = await client.post("/v1/embeddings", headers=headers, json={"input": "hello world"})
    assert emb.status_code == 200, emb.text
    vector = emb.json()["data"][0]["embedding"]
    assert len(vector) == settings.EMBEDDING_DIM

    chat = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "What do plants make from sunlight?"}]},
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"]
    assert isinstance(body["citations"], list)


async def test_search_scope_is_enforced(client, db_session, api) -> None:
    """Every /v1 surface requires the "search" scope - including the two that actually return
    org knowledge, not just the harmless model list. The key here holds only "read".
    """
    _org, _owner, headers = await _org_with_key(db_session, ["read"])
    models = await client.get("/v1/models", headers=headers)
    assert models.status_code == 403, models.text
    emb = await client.post("/v1/embeddings", headers=headers, json={"input": "hi"})
    assert emb.status_code == 403, emb.text
    chat = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 403, chat.text


async def test_embeddings_base64_encoding_format(client, db_session, api) -> None:
    """The encoded string decodes to EMBEDDING_DIM little-endian float32s, exactly like
    OpenAI's base64 format.
    """
    _org, _owner, headers = await _org_with_key(db_session, ["search"])
    resp = await client.post(
        "/v1/embeddings",
        headers=headers,
        json={"input": "hello", "encoding_format": "base64"},
    )
    assert resp.status_code == 200, resp.text
    encoded = resp.json()["data"][0]["embedding"]
    assert isinstance(encoded, str)
    raw = base64.b64decode(encoded)
    floats = struct.unpack(f"<{settings.EMBEDDING_DIM}f", raw)
    assert len(floats) == settings.EMBEDDING_DIM


async def test_embeddings_accepts_tokenized_input(client, db_session, api) -> None:
    """LangChain's default OpenAIEmbeddings sends a list of token-id arrays; this must not
    400.
    """
    _org, _owner, headers = await _org_with_key(db_session, ["search"])
    resp = await client.post(
        "/v1/embeddings",
        headers=headers,
        json={"input": [[9906, 1917], [15339]]},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 2
    assert len(resp.json()["data"][0]["embedding"]) == settings.EMBEDDING_DIM


async def test_embeddings_rejects_too_many_inputs(client, db_session, api) -> None:
    _org, _owner, headers = await _org_with_key(db_session, ["search"])
    resp = await client.post(
        "/v1/embeddings",
        headers=headers,
        json={"input": ["x"] * 3000},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["type"] == "invalid_request_error"


async def test_chat_completion_writes_search_audit(client, db_session, api) -> None:
    """The grounded chat surface leaves a search.performed audit trail like /search and MCP."""
    org, _owner, headers = await _org_with_key(db_session, ["search"])
    before = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org.id, AuditLog.action == "search.performed")
        )
    ).scalar_one()

    chat = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "What do plants make?"}]},
    )
    assert chat.status_code == 200, chat.text

    after = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org.id, AuditLog.action == "search.performed")
        )
    ).scalar_one()
    assert after == before + 1
