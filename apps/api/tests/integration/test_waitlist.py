"""Integration: the public pre-launch waitlist.

Runs against the real app + Postgres. Turnstile verification is disabled (no secret in the
test settings), so submissions are accepted without a token - the widget is a production
concern, not part of the persistence contract under test here.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _email() -> str:
    return f"waitlist-{uuid.uuid4().hex[:12]}@example.com"


async def test_join_is_idempotent_and_counts(client, api) -> None:
    before = (await client.get(f"{api}/waitlist/stats")).json()["count"]

    email = _email()
    r1 = await client.post(f"{api}/waitlist", json={"email": email, "source": "landing"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "ok"

    after_first = (await client.get(f"{api}/waitlist/stats")).json()["count"]
    assert after_first == before + 1

    # Re-submitting the same address (any case) must not create a second row.
    r2 = await client.post(f"{api}/waitlist", json={"email": email.upper()})
    assert r2.status_code == 200, r2.text
    after_second = (await client.get(f"{api}/waitlist/stats")).json()["count"]
    assert after_second == after_first


async def test_honeypot_is_silently_dropped(client, api) -> None:
    before = (await client.get(f"{api}/waitlist/stats")).json()["count"]

    resp = await client.post(
        f"{api}/waitlist",
        json={"email": _email(), "company_website": "http://spam.example"},
    )
    # Looks successful to the bot...
    assert resp.status_code == 200, resp.text
    # ...but nothing was persisted.
    after = (await client.get(f"{api}/waitlist/stats")).json()["count"]
    assert after == before


async def test_rejects_invalid_email(client, api) -> None:
    resp = await client.post(f"{api}/waitlist", json={"email": "not-an-email"})
    assert resp.status_code == 422, resp.text


async def test_rate_limited_per_email(client, api) -> None:
    """Hammering the same address trips the limiter, but distinct addresses are unaffected.

    The bucket is keyed on (email, IP): the 11th submit of one address 429s, yet a different
    address still succeeds - so one abuser can never throttle the whole public waitlist.
    """
    email = _email()
    saw_429 = False
    for _ in range(12):
        r = await client.post(f"{api}/waitlist", json={"email": email})
        if r.status_code == 429:
            saw_429 = True
            break
        assert r.status_code == 200, r.text
    assert saw_429, "expected a 429 after repeated submissions of the same email"

    # A different visitor's email is in its own bucket and still goes through.
    other = await client.post(f"{api}/waitlist", json={"email": _email()})
    assert other.status_code == 200, other.text
