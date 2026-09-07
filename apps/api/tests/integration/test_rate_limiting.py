"""Integration: rate limiting enforced through real Redis.

The API-key limiter increments a fixed 60-second bucket keyed by the key id, so a key
with a small per-minute budget starts returning HTTP 429 once the budget is spent. The
unauthenticated ``/files`` capability endpoint shares the login limiter's fixed-window
buckets, counting only misses but refusing already-throttled clients before any DB work.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime as real_datetime

import factories
import pytest

pytestmark = pytest.mark.integration


async def test_key_over_its_budget_gets_429(client, db_session, api, monkeypatch) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    _key, secret = await factories.create_api_key(
        db_session,
        org=org,
        scopes=["search"],
        rate_limit_per_minute=3,
        acts_as_user=owner,
    )
    headers = factories.api_key_headers(secret)
    payload = {"query": "anything"}

    # Freeze the limiter clock so all four requests land in the SAME fixed 60s bucket.
    # Otherwise a minute-boundary crossing mid-test resets the counter and the 4th request
    # would pass - a ~1-2% flake on the authoritative CI job.
    import app.core.deps as deps

    frozen = real_datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(deps, "datetime", _FrozenDatetime)

    # The first three requests fit within the budget.
    for i in range(3):
        ok = await client.post(f"{api}/search", headers=headers, json=payload)
        assert ok.status_code == 200, f"request {i + 1}: {ok.text}"

    # The fourth exceeds it and is rejected by the limiter.
    limited = await client.post(f"{api}/search", headers=headers, json=payload)
    assert limited.status_code == 429, limited.text


async def test_session_users_are_not_rate_limited(client, db_session, token_headers, api) -> None:
    # Human (JWT) sessions have no api_key, so the limiter is a no-op for them.
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    for _ in range(6):
        resp = await client.post(f"{api}/search", headers=headers, json={"query": "x"})
        assert resp.status_code == 200, resp.text


async def test_files_token_scan_is_rejected_before_the_db_probe(
    client, db_session, api, monkeypatch
) -> None:
    """Once a client exhausts the ``/files`` miss budget, further requests are refused
    up front - even one presenting a token that WOULD resolve - proving the limiter is
    consulted BEFORE the DB probe. A post-probe-only limiter would let a throttled
    scanner keep driving one lookup per guess (and would serve the valid token here)."""
    import app.core.deps as deps

    frozen = real_datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(deps, "datetime", _FrozenDatetime)
    monkeypatch.setattr(deps, "_LOGIN_RATE_LIMIT_PER_MINUTE", 2)

    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(db_session, org=org, owner=owner)
    document = await factories.create_document(
        db_session, org=org, collection=collection, created_by=owner
    )
    token = "t" * 43
    document.meta = {"file_token": token}
    document.storage_key = f"{org.id}/{document.id}/image.png"
    await db_session.commit()

    # Misses inside the budget probe and 404; the first one past it is throttled.
    for i in range(2):
        missed = await client.get(f"{api}/files/{'x' * 43}")
        assert missed.status_code == 404, f"miss {i + 1}: {missed.text}"
    limited = await client.get(f"{api}/files/{'x' * 43}")
    assert limited.status_code == 429, limited.text

    # The throttled client is refused before the lookup: even the real token 429s.
    refused = await client.get(f"{api}/files/{token}")
    assert refused.status_code == 429, refused.text
