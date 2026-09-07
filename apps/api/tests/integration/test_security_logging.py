"""Integration: security-relevant auth events are logged so abuse is detectable.

Rejected credentials, permission denials and rate-limit hits must each emit a distinct
structured log event (never the secret itself), so credential stuffing, API-key probing
and cross-tenant permission scanning surface in the logs rather than only as access-log
status codes.
"""

from __future__ import annotations

import factories
import pytest
from structlog.testing import capture_logs

pytestmark = pytest.mark.integration


async def test_bad_api_key_logs_auth_failed_without_the_secret(client, api) -> None:
    secret = "tb_this_key_does_not_exist"
    with capture_logs() as logs:
        resp = await client.post(
            f"{api}/search", json={"query": "x"}, headers={"X-API-Key": secret}
        )

    assert resp.status_code == 401
    events = [e for e in logs if e.get("event") == "auth_failed"]
    assert events, f"expected an auth_failed event, got {logs}"
    assert events[0]["auth_kind"] == "apikey"
    assert events[0]["status"] == 401
    # The presented secret must never appear anywhere in the logs.
    assert not any(secret in str(v) for e in logs for v in e.values())


async def test_missing_scope_logs_permission_denied(client, db_session, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    # A key WITHOUT the "search" scope that /search requires.
    _key, secret = await factories.create_api_key(
        db_session, org=org, scopes=["read"], acts_as_user=owner
    )

    with capture_logs() as logs:
        resp = await client.post(
            f"{api}/search", json={"query": "x"}, headers=factories.api_key_headers(secret)
        )

    assert resp.status_code == 403
    denials = [e for e in logs if e.get("event") == "permission_denied"]
    assert denials, f"expected a permission_denied event, got {logs}"
    assert denials[0]["check"] == "scope"
    assert denials[0]["required"] == "search"
