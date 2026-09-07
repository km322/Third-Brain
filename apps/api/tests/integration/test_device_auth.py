"""Integration: the CLI device-authorization flow (start -> approve in browser -> redeem).

Drives the real endpoints end to end: the unauthenticated start/poll pair, the
admin-session approval/denial, the one-shot plaintext handover, and the minted key
actually authenticating against the MCP surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import datetime as real_datetime

import factories
import pytest
from sqlalchemy import select

from app.models.device_auth import DeviceAuthorization
from app.models.enums import DeviceAuthStatus, OrgRole

pytestmark = pytest.mark.integration


async def _start(client, api, client_name: str = "third-brain-mcp on testhost") -> dict:
    resp = await client.post(f"{api}/device-auth", json={"client_name": client_name})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _poll(client, api, device_code: str):
    return await client.post(f"{api}/device-auth/token", json={"device_code": device_code})


async def test_full_happy_path(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin_headers = token_headers(owner.id, org.id)

    started = await _start(client, api)
    assert started["device_code"].startswith("tbd_")
    assert started["verification_uri"].endswith("/activate")
    assert started["verification_uri_complete"].endswith(f"/activate?code={started['user_code']}")
    assert started["expires_in"] > 0 and started["interval"] >= 1

    # While nobody has approved, the CLI sees authorization_pending.
    pending = await _poll(client, api, started["device_code"])
    assert pending.status_code == 200, pending.text
    assert pending.json() == {"status": "authorization_pending"}

    # The approval page can show what is being approved.
    shown = await client.get(
        f"{api}/device-auth/pending/{started['user_code']}", headers=admin_headers
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["client_name"] == "third-brain-mcp on testhost"

    # Admin approves with the defaults (name derived from client_name, acts as approver).
    approved = await client.post(
        f"{api}/device-auth/approve",
        headers=admin_headers,
        json={"user_code": started["user_code"]},
    )
    assert approved.status_code == 200, approved.text
    key_meta = approved.json()
    assert key_meta["name"] == "CLI - third-brain-mcp on testhost"
    assert sorted(key_meta["scopes"]) == ["ingest", "read", "search"]
    assert "secret" not in key_meta and "hashed_key" not in key_meta

    # The next poll hands over the plaintext exactly once and flips to consumed.
    redeemed = await _poll(client, api, started["device_code"])
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    assert body["status"] == "approved"
    secret = body["api_key"]
    assert secret.startswith("tb_")
    assert body["key_prefix"] == secret[:12]
    assert sorted(body["scopes"]) == ["ingest", "read", "search"]
    # The redemption tells the CLI which org/member it bound to (so a wrong-org
    # approval is visible), and it acts as the approving owner by default.
    assert body["org_name"] == org.name
    assert body["acts_as_email"] == owner.email

    again = await _poll(client, api, started["device_code"])
    assert again.status_code == 400, again.text

    # The plaintext is gone from the row the moment it is handed over.
    row = (
        await db_session.execute(
            select(DeviceAuthorization).where(DeviceAuthorization.user_code == started["user_code"])
        )
    ).scalar_one()
    assert row.status == DeviceAuthStatus.CONSUMED
    assert row.encrypted_secret is None
    assert row.org_id == org.id

    # The minted key authenticates against the MCP surface.
    key_headers = {"Authorization": f"Bearer {secret}"}
    listed = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=key_headers,
    )
    assert listed.status_code == 200, listed.text
    assert {t["name"] for t in listed.json()["result"]["tools"]}
    called = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_knowledge", "arguments": {"query": "anything"}},
        },
        headers=key_headers,
    )
    assert called.status_code == 200, called.text
    assert called.json()["result"]["isError"] is False, called.json()


async def test_deny_path(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    started = await _start(client, api)

    denied = await client.post(
        f"{api}/device-auth/deny",
        headers=token_headers(owner.id, org.id),
        json={"user_code": started["user_code"]},
    )
    assert denied.status_code == 200, denied.text

    polled = await _poll(client, api, started["device_code"])
    assert polled.status_code == 200, polled.text
    assert polled.json() == {"status": "denied"}

    # A settled code can no longer be approved.
    approve = await client.post(
        f"{api}/device-auth/approve",
        headers=token_headers(owner.id, org.id),
        json={"user_code": started["user_code"]},
    )
    assert approve.status_code == 400, approve.text


async def test_expiry_is_lazy(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin_headers = token_headers(owner.id, org.id)
    started = await _start(client, api)

    row = (
        await db_session.execute(
            select(DeviceAuthorization).where(DeviceAuthorization.user_code == started["user_code"])
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    # The approval page 404s, approval is refused, and the CLI sees expired.
    shown = await client.get(
        f"{api}/device-auth/pending/{started['user_code']}", headers=admin_headers
    )
    assert shown.status_code == 404, shown.text
    approve = await client.post(
        f"{api}/device-auth/approve",
        headers=admin_headers,
        json={"user_code": started["user_code"]},
    )
    assert approve.status_code == 400, approve.text
    polled = await _poll(client, api, started["device_code"])
    assert polled.status_code == 200, polled.text
    assert polled.json() == {"status": "expired"}


async def test_approved_then_expired_revokes_orphan_key(
    client, db_session, token_headers, api
) -> None:
    """A flow approved but abandoned before redemption must not leave a live key.

    Lazy expiry on the next poll (the reaper cron is the same logic in bulk) must flip the
    row to EXPIRED, drop the encrypted plaintext, and revoke the minted-but-undelivered
    ApiKey - otherwise an approved-but-never-polled flow strands an org credential.
    """
    from app.models.api_key import ApiKey

    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin_headers = token_headers(owner.id, org.id)
    started = await _start(client, api)

    approved = await client.post(
        f"{api}/device-auth/approve",
        headers=admin_headers,
        json={"user_code": started["user_code"]},
    )
    assert approved.status_code == 200, approved.text
    key_id = approved.json()["id"]

    # Backdate the (now APPROVED) row past its expiry, then poll: the CLI sees expired.
    row = (
        await db_session.execute(
            select(DeviceAuthorization).where(DeviceAuthorization.user_code == started["user_code"])
        )
    ).scalar_one()
    assert row.status == DeviceAuthStatus.APPROVED
    assert row.encrypted_secret is not None and row.api_key_id is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    polled = await _poll(client, api, started["device_code"])
    assert polled.status_code == 200, polled.text
    assert polled.json() == {"status": "expired"}

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(DeviceAuthorization).where(DeviceAuthorization.user_code == started["user_code"])
        )
    ).scalar_one()
    assert row.status == DeviceAuthStatus.EXPIRED
    assert row.encrypted_secret is None
    key = await db_session.get(ApiKey, key_id)
    assert key is not None and key.revoked is True


async def test_approve_requires_admin_session(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    editor, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    started = await _start(client, api)

    for path, body in (
        ("approve", {"user_code": started["user_code"]}),
        ("deny", {"user_code": started["user_code"]}),
    ):
        resp = await client.post(
            f"{api}/device-auth/{path}",
            headers=token_headers(editor.id, org.id),
            json=body,
        )
        assert resp.status_code == 403, resp.text
    shown = await client.get(
        f"{api}/device-auth/pending/{started['user_code']}",
        headers=token_headers(editor.id, org.id),
    )
    assert shown.status_code == 403, shown.text

    # Even a manage-scoped API key cannot approve - only a human admin session can.
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["manage"], acts_as_user=owner
    )
    resp = await client.post(
        f"{api}/device-auth/approve",
        headers=factories.api_key_headers(secret),
        json={"user_code": started["user_code"]},
    )
    assert resp.status_code == 403, resp.text


async def test_acts_as_cannot_outrank_approver(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    started = await _start(client, api)

    resp = await client.post(
        f"{api}/device-auth/approve",
        headers=token_headers(admin.id, org.id),
        json={"user_code": started["user_code"], "acts_as_user_id": str(owner.id)},
    )
    assert resp.status_code == 403, resp.text

    # The request survives the rejection and can still be approved correctly.
    ok = await client.post(
        f"{api}/device-auth/approve",
        headers=token_headers(admin.id, org.id),
        json={"user_code": started["user_code"]},
    )
    assert ok.status_code == 200, ok.text


async def test_scopes_limited_to_grantable_subset(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin_headers = token_headers(owner.id, org.id)

    for scopes in (["manage"], ["*"], ["search", "bogus"], []):
        started = await _start(client, api)
        resp = await client.post(
            f"{api}/device-auth/approve",
            headers=admin_headers,
            json={"user_code": started["user_code"], "scopes": scopes},
        )
        assert resp.status_code == 400, (scopes, resp.text)

    # An explicit allowed subset narrows the key.
    started = await _start(client, api)
    resp = await client.post(
        f"{api}/device-auth/approve",
        headers=admin_headers,
        json={"user_code": started["user_code"], "scopes": ["search"], "name": "cli-search-only"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scopes"] == ["search"]
    assert resp.json()["name"] == "cli-search-only"


async def test_token_poll_is_rate_limited(client, db_session, api, monkeypatch) -> None:
    """Hammering the poll endpoint for one device code is throttled per (code, IP)."""
    import app.core.deps as deps

    started = await _start(client, api)

    # Freeze the limiter clock so every attempt lands in the same 60s window (no boundary flake).
    frozen = real_datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(deps, "datetime", _FrozenDatetime)

    statuses = [(await _poll(client, api, started["device_code"])).status_code for _ in range(12)]
    assert 429 in statuses, statuses
    assert statuses[-1] == 429, statuses


async def test_invalid_and_unknown_codes(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    polled = await _poll(client, api, "tbd_not-a-real-code")
    assert polled.status_code == 400, polled.text
    shown = await client.get(
        f"{api}/device-auth/pending/XXXX-XXXX", headers=token_headers(owner.id, org.id)
    )
    assert shown.status_code == 404, shown.text
