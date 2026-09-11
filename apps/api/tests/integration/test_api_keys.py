"""Integration: API-key lifecycle - secret shown once, authenticates, then revoked."""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole

pytestmark = pytest.mark.integration


async def test_api_key_records_last_used(client, db_session, api) -> None:
    """Authenticating with a key stamps last_used_at, so admins auditing key hygiene see a
    real "last used" instead of a permanent null (finding 33)."""
    from app.models.api_key import ApiKey

    org, owner, _ = await factories.create_org_with_owner(db_session)
    key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["search"], acts_as_user=owner
    )
    assert key.last_used_at is None

    resp = await client.get("/v1/models", headers=factories.api_key_headers(secret))
    assert resp.status_code == 200, resp.text

    refreshed = await db_session.get(ApiKey, key.id)
    await db_session.refresh(refreshed)
    assert refreshed.last_used_at is not None


async def test_key_minted_once_then_authenticates_and_is_revocable(
    client, db_session, token_headers, api
) -> None:
    """The secret exists only in the mint response: the nested key record never carries it and
    the listing endpoint returns metadata without any secret material. The raw secret does
    authenticate against a data endpoint, but managing keys stays a human-admin-session
    privilege that an API key cannot exercise - and once revoked the same secret stops
    working."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin_headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/api-keys",
        headers=admin_headers,
        json={"name": "ci-key", "scopes": ["search", "read"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    secret = body["secret"]
    assert secret.startswith("tb_")
    assert "secret" not in body["api_key"]
    key_id = body["api_key"]["id"]

    listing = await client.get(f"{api}/api-keys", headers=admin_headers)
    assert listing.status_code == 200, listing.text
    row = next(k for k in listing.json() if k["id"] == key_id)
    assert "secret" not in row and "hashed_key" not in row
    assert row["key_prefix"] == secret[:12]

    key_headers = factories.api_key_headers(secret)
    search = await client.post(f"{api}/search", headers=key_headers, json={"query": "hello"})
    assert search.status_code == 200, search.text

    forbidden = await client.get(f"{api}/api-keys", headers=key_headers)
    assert forbidden.status_code == 403, forbidden.text

    revoked = await client.post(f"{api}/api-keys/{key_id}/revoke", headers=admin_headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True

    after = await client.post(f"{api}/search", headers=key_headers, json={"query": "hello"})
    assert after.status_code == 401, after.text


async def test_only_admins_can_mint_keys(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/api-keys",
        headers=token_headers(viewer.id, org.id),
        json={"name": "nope", "scopes": ["search"]},
    )
    assert resp.status_code == 403, resp.text


async def test_unknown_scope_is_rejected(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    resp = await client.post(
        f"{api}/api-keys",
        headers=token_headers(owner.id, org.id),
        json={"name": "bad", "scopes": ["not-a-real-scope"]},
    )
    assert resp.status_code == 400, resp.text
