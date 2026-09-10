"""Integration: connector CRUD with credentials encrypted at rest and never returned.

The second half covers the native providers - Anthropic (completion only) and Google Gemini.
"""

from __future__ import annotations

import json

import factories
import pytest
from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models.connector import Connector
from app.models.enums import ConnectorPurpose, ConnectorType, OrgRole

pytestmark = pytest.mark.integration


async def test_credentials_are_encrypted_and_never_serialized(
    client, db_session, token_headers, api
) -> None:
    """The secret is never echoed back in any form.

    Not by the create response, and not by listing or detail either. At rest the stored blob
    is ciphertext that decrypts back to the original.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/connectors",
        headers=headers,
        json={
            "name": "OpenAI Embeddings",
            "type": "openai",
            "purpose": "embedding",
            "model": "text-embedding-3-small",
            "credentials": {"api_key": "sk-super-secret-value"},
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    connector_id = body["id"]
    assert body["has_credentials"] is True
    assert "credentials" not in body
    assert "encrypted_credentials" not in body
    assert "sk-super-secret-value" not in created.text

    listing = await client.get(f"{api}/connectors", headers=headers)
    assert "sk-super-secret-value" not in listing.text
    detail = await client.get(f"{api}/connectors/{connector_id}", headers=headers)
    assert detail.status_code == 200
    assert "sk-super-secret-value" not in detail.text

    row = (
        await db_session.execute(select(Connector).where(Connector.id == connector_id))
    ).scalar_one()
    assert row.encrypted_credentials
    assert "sk-super-secret-value" not in row.encrypted_credentials
    assert json.loads(decrypt_secret(row.encrypted_credentials)) == {
        "api_key": "sk-super-secret-value"
    }


async def test_connector_test_endpoint_round_trips(client, db_session, token_headers, api) -> None:
    """The connector has no credentials, so the offline provider is a legitimate success
    path for the test endpoint.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    connector = await factories.create_connector(
        db_session, org=org, credentials=None, is_default=True
    )
    resp = await client.post(f"{api}/connectors/{connector.id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)


async def test_embedding_dimension_mismatch_fails(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    """An embedding connector whose vectors are the wrong width fails the test loudly:
    the fixed document_chunks.embedding column would reject it during ingestion/search."""
    import app.api.routes.connectors as connectors_route
    from app.core.config import settings
    from app.services.llm import EmbeddingResult

    async def _wrong_dim(*_args, **_kwargs) -> EmbeddingResult:
        return EmbeddingResult([[0.1] * 8], "offline", tokens=1, provider="offline")

    monkeypatch.setattr(connectors_route, "embed_texts", _wrong_dim)

    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    connector = await factories.create_connector(
        db_session, org=org, credentials=None, is_default=True
    )
    resp = await client.post(f"{api}/connectors/{connector.id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == (
        f"Embedding dimension 8 does not match the configured {settings.EMBEDDING_DIM}."
    )


async def test_embedding_correct_dimension_succeeds(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    """A connector whose embeddings match the configured dimension still passes the test."""
    import app.api.routes.connectors as connectors_route
    from app.core.config import settings
    from app.services.llm import EmbeddingResult

    async def _right_dim(*_args, **_kwargs) -> EmbeddingResult:
        vector = [0.1] * settings.EMBEDDING_DIM
        return EmbeddingResult([vector], "offline", tokens=1, provider="offline")

    monkeypatch.setattr(connectors_route, "embed_texts", _right_dim)

    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    connector = await factories.create_connector(
        db_session, org=org, credentials=None, is_default=True
    )
    resp = await client.post(f"{api}/connectors/{connector.id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


async def test_mutations_require_admin(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/connectors",
        headers=token_headers(viewer.id, org.id),
        json={"name": "x", "type": "openai", "purpose": "embedding", "model": "m"},
    )
    assert resp.status_code == 403, resp.text


async def test_anthropic_completion_connector_crud(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/connectors",
        headers=headers,
        json={
            "name": "Claude",
            "type": "anthropic",
            "purpose": "completion",
            "model": "claude-opus-4-8",
            "credentials": {"api_key": "sk-ant-secret"},
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["type"] == "anthropic"
    assert body["purpose"] == "completion"
    assert "sk-ant-secret" not in created.text

    updated = await client.patch(
        f"{api}/connectors/{body['id']}",
        headers=headers,
        json={"model": "claude-sonnet-5"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "claude-sonnet-5"


async def test_google_connector_crud_both_purposes(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    for purpose, model in (
        ("embedding", "gemini-embedding-001"),
        ("completion", "gemini-2.5-flash"),
    ):
        created = await client.post(
            f"{api}/connectors",
            headers=headers,
            json={
                "name": f"Gemini {purpose}",
                "type": "google",
                "purpose": purpose,
                "model": model,
                "credentials": {"api_key": "google-secret"},
                "is_default": True,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["type"] == "google"
        assert body["purpose"] == purpose
        assert "google-secret" not in created.text


async def test_anthropic_embedding_connector_is_rejected(
    client, db_session, token_headers, api
) -> None:
    """Anthropic has no embeddings API: creation, update-into and testing an
    anthropic+embedding connector all fail with a clear message.

    An existing anthropic completion connector cannot be flipped to embedding, and an
    embedding connector cannot be flipped to anthropic.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/connectors",
        headers=headers,
        json={
            "name": "Claude embeddings",
            "type": "anthropic",
            "purpose": "embedding",
            "model": "claude-opus-4-8",
        },
    )
    assert created.status_code == 422, created.text
    assert "no embeddings API" in created.json()["detail"]

    ok = await client.post(
        f"{api}/connectors",
        headers=headers,
        json={
            "name": "Claude",
            "type": "anthropic",
            "purpose": "completion",
            "model": "claude-opus-4-8",
        },
    )
    assert ok.status_code == 201, ok.text
    flipped = await client.patch(
        f"{api}/connectors/{ok.json()['id']}",
        headers=headers,
        json={"purpose": "embedding"},
    )
    assert flipped.status_code == 422, flipped.text

    emb = await factories.create_connector(
        db_session, org=org, purpose=ConnectorPurpose.EMBEDDING, is_default=False
    )
    flipped_type = await client.patch(
        f"{api}/connectors/{emb.id}",
        headers=headers,
        json={"type": "anthropic"},
    )
    assert flipped_type.status_code == 422, flipped_type.text


async def test_anthropic_embedding_test_endpoint_rejected(
    client, db_session, token_headers, api
) -> None:
    """A legacy anthropic+embedding row (predating the purpose gate) fails the test
    endpoint with the same clear message instead of producing fake vectors."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    connector = await factories.create_connector(
        db_session,
        org=org,
        type=ConnectorType.ANTHROPIC,
        purpose=ConnectorPurpose.EMBEDDING,
        model="claude-opus-4-8",
        is_default=True,
    )
    resp = await client.post(f"{api}/connectors/{connector.id}/test", headers=headers)
    assert resp.status_code == 422, resp.text
    assert "no embeddings API" in resp.json()["detail"]


async def test_anthropic_completion_connector_test_round_trips(
    client, db_session, token_headers, api
) -> None:
    """The connector has no credentials, so the offline provider is a legitimate success
    path for the test endpoint.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    connector = await factories.create_connector(
        db_session,
        org=org,
        type=ConnectorType.ANTHROPIC,
        purpose=ConnectorPurpose.COMPLETION,
        model="claude-opus-4-8",
        credentials=None,
        is_default=True,
    )
    resp = await client.post(f"{api}/connectors/{connector.id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
