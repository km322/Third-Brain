"""Integration: the authentication lifecycle against the real app + Postgres.

Register → login → refresh → logout, plus ``/users/me``, password change and the
negative paths (duplicate email, wrong password, replayed refresh tokens). Driven
end-to-end through the ASGI app; JWTs are minted and verified by the real security
layer and refresh rotation by the real Redis denylist.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime as real_datetime

import factories
import pytest

from app.core.security import create_access_token, decode_token

pytestmark = pytest.mark.integration


async def test_login_is_rate_limited(client, api, monkeypatch) -> None:
    """Repeated login attempts for one identifier from one IP are throttled (429), so the
    auth endpoints cannot be brute-forced / credential-stuffed at full request rate.

    The limiter clock is frozen so every attempt lands in the same 60s window (no boundary
    flake). The first attempts are ordinary 401s; once the per-minute budget is spent, 429
    kicks in.
    """
    import app.core.deps as deps

    frozen = real_datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(deps, "datetime", _FrozenDatetime)

    body = {"email": f"brute-{uuid.uuid4().hex}@example.com", "password": "wrong-password"}
    statuses = [(await client.post(f"{api}/auth/login", json=body)).status_code for _ in range(12)]
    assert 429 in statuses, statuses
    assert statuses[-1] == 429, statuses


async def test_register_returns_token_pair_and_bootstraps_owner_org(client, api) -> None:
    resp = await client.post(
        f"{api}/auth/register",
        json={
            "email": "ada@example.com",
            "password": "Sup3rSecret!",
            "full_name": "Ada Lovelace",
            "org_name": "Analytical Engines",
        },
    )
    assert resp.status_code == 201, resp.text
    tokens = resp.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"

    me = await client.get(
        f"{api}/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["role"] == "owner"
    assert body["active_org"]["name"] == "Analytical Engines"
    assert len(body["organizations"]) == 1


async def test_duplicate_email_is_conflict(client, api, register) -> None:
    session = await register(client)
    resp = await client.post(
        f"{api}/auth/register",
        json={
            "email": session["email"],
            "password": "Different1!",
            "full_name": "Twin",
            "org_name": "Second Org",
        },
    )
    assert resp.status_code == 409, resp.text


async def test_login_wrong_password_is_unauthorized(client, api, register) -> None:
    session = await register(client)
    resp = await client.post(
        f"{api}/auth/login",
        json={"email": session["email"], "password": "not-the-password"},
    )
    assert resp.status_code == 401, resp.text


async def test_refresh_issues_new_access_token(client, api, register) -> None:
    """A refresh mints an access token that authenticates."""
    session = await register(client)
    refresh_token = session["tokens"]["refresh_token"]

    resp = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    new_tokens = resp.json()
    assert new_tokens["access_token"]

    me = await client.get(
        f"{api}/users/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == session["email"]


async def test_refresh_preserves_active_org(client, api, register) -> None:
    """Passing the active org to /auth/refresh resumes into it, so a silent token refresh
    doesn't quietly switch the user back to their default org mid-session (finding 9).

    Registering bootstraps org A as the default, and the refresh below asks for org B.
    """
    session = await register(client)
    headers = session["headers"]

    created = await client.post(f"{api}/orgs", headers=headers, json={"name": "Second Org"})
    assert created.status_code == 201, created.text
    org_b = created.json()["id"]

    resp = await client.post(
        f"{api}/auth/refresh",
        json={"refresh_token": session["tokens"]["refresh_token"], "org_id": org_b},
    )
    assert resp.status_code == 200, resp.text
    me = await client.get(
        f"{api}/users/me",
        headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
    )
    assert me.json()["active_org"]["id"] == org_b


async def test_refresh_rejects_an_access_token(client, api, register) -> None:
    """Passing the *access* token where a refresh token is expected must fail."""
    session = await register(client)
    resp = await client.post(
        f"{api}/auth/refresh",
        json={"refresh_token": session["tokens"]["access_token"]},
    )
    assert resp.status_code == 401, resp.text


async def test_access_token_with_non_uuid_subject_is_unauthorized(client, api) -> None:
    """A validly-signed access token whose ``sub`` claim is not a UUID must yield 401,
    not a 500. The token is forged with the real signing key so it passes signature
    verification and only the malformed subject is exercised."""
    token = create_access_token("not-a-uuid", extra={"org": str(uuid.uuid4())})
    resp = await client.get(f"{api}/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Invalid token subject"


async def test_access_token_with_non_uuid_org_is_unauthorized(client, api, register) -> None:
    """A validly-signed access token whose ``org`` claim is not a UUID must yield 401,
    not a 500. A real, active user is used for ``sub`` so resolution reaches the org
    claim before the malformed value is parsed. ``ver`` matches the fresh user's
    token_version (0) so resolution passes the session-revocation check and reaches the
    malformed org claim under test."""
    session = await register(client)
    user_id = decode_token(session["tokens"]["access_token"])["sub"]
    token = create_access_token(user_id, extra={"org": "not-a-uuid", "ver": 0})
    resp = await client.get(f"{api}/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Invalid organization in token"


async def test_logout_and_unauthenticated_access(client, api, register) -> None:
    """After logout, no credentials at all -> 401 on a protected route."""
    session = await register(client)
    logout = await client.post(f"{api}/auth/logout", headers=session["headers"])
    assert logout.status_code == 204, logout.text

    anon = await client.get(f"{api}/users/me")
    assert anon.status_code == 401, anon.text


async def test_refresh_token_is_single_use(client, api, register) -> None:
    """Refresh tokens rotate: exchanging one denylists its ``jti``, so replaying it
    (e.g. a stolen token) is rejected while the rotated replacement keeps working."""
    session = await register(client)
    refresh_token = session["tokens"]["refresh_token"]

    first = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh_token})
    assert first.status_code == 200, first.text

    replay = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401, replay.text

    rotated = first.json()["refresh_token"]
    second = await client.post(f"{api}/auth/refresh", json={"refresh_token": rotated})
    assert second.status_code == 200, second.text


async def test_logout_with_refresh_token_revokes_it(client, api, register) -> None:
    """Surrendering the refresh token at logout denylists it server-side, so a token
    that leaks from storage after logout can no longer mint new sessions."""
    session = await register(client)
    refresh_token = session["tokens"]["refresh_token"]

    logout = await client.post(
        f"{api}/auth/logout",
        headers=session["headers"],
        json={"refresh_token": refresh_token},
    )
    assert logout.status_code == 204, logout.text

    resp = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 401, resp.text


async def test_logout_does_not_revoke_another_users_refresh_token(client, api, register) -> None:
    """The surrendered token must belong to the caller: user A presenting user B's
    refresh token at logout still gets 204, but B's token keeps working."""
    alice = await register(client)
    bob = await register(client)

    logout = await client.post(
        f"{api}/auth/logout",
        headers=alice["headers"],
        json={"refresh_token": bob["tokens"]["refresh_token"]},
    )
    assert logout.status_code == 204, logout.text

    resp = await client.post(
        f"{api}/auth/refresh", json={"refresh_token": bob["tokens"]["refresh_token"]}
    )
    assert resp.status_code == 200, resp.text


async def test_refresh_rate_limit_is_keyed_per_token(client, api, register, monkeypatch) -> None:
    """The refresh limiter keys on a hash of the whole token, not a shared JWT prefix:
    distinct tokens from one IP never contend for a bucket (12 rotations all succeed),
    while replaying one token from one IP is throttled like any brute-force attempt.

    The limiter clock is frozen so every attempt lands in the same 60s window (no boundary
    flake). The first half rotates through 12 refreshes, always presenting the newest token -
    under the old constant-prefix key those would share one bucket and start 429ing. The
    second half replays a single token 12 times, which shares one bucket and is throttled
    after the first exchange succeeds.
    """
    import app.core.deps as deps

    frozen = real_datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(deps, "datetime", _FrozenDatetime)

    session = await register(client)
    token = session["tokens"]["refresh_token"]
    for attempt in range(12):
        resp = await client.post(f"{api}/auth/refresh", json={"refresh_token": token})
        assert resp.status_code == 200, f"attempt {attempt}: {resp.status_code} {resp.text}"
        token = resp.json()["refresh_token"]

    other = await register(client)
    same = other["tokens"]["refresh_token"]
    statuses = [
        (await client.post(f"{api}/auth/refresh", json={"refresh_token": same})).status_code
        for _ in range(12)
    ]
    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses
    assert statuses[-1] == 429, statuses


async def test_change_password_wrong_current_is_rejected(client, api, register) -> None:
    """A wrong current password is rejected and the credentials are unchanged: the original
    password still logs in.
    """
    session = await register(client)
    resp = await client.post(
        f"{api}/auth/change-password",
        headers=session["headers"],
        json={"current_password": "not-the-password", "new_password": "N3w-Sup3rSecret!"},
    )
    assert resp.status_code == 400, resp.text

    login = await client.post(
        f"{api}/auth/login",
        json={"email": session["email"], "password": session["password"]},
    )
    assert login.status_code == 200, login.text


async def test_change_password_rotates_credentials_and_revokes_old_sessions(
    client, api, register
) -> None:
    """A successful password change returns a working token pair minted with the new
    ``token_version`` while both the old access AND old refresh tokens die (401).

    The fresh pair works, so the caller stays signed in without re-authenticating; every
    pre-change session is revoked, access and refresh alike; and only the new password
    authenticates afterwards.
    """
    session = await register(client)
    old_access = session["tokens"]["access_token"]
    old_refresh = session["tokens"]["refresh_token"]

    resp = await client.post(
        f"{api}/auth/change-password",
        headers=session["headers"],
        json={"current_password": session["password"], "new_password": "N3w-Sup3rSecret!"},
    )
    assert resp.status_code == 200, resp.text
    new_tokens = resp.json()

    me = await client.get(
        f"{api}/users/me", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert me.status_code == 200, me.text
    refreshed = await client.post(
        f"{api}/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text

    old_me = await client.get(f"{api}/users/me", headers={"Authorization": f"Bearer {old_access}"})
    assert old_me.status_code == 401, old_me.text
    old_ref = await client.post(f"{api}/auth/refresh", json={"refresh_token": old_refresh})
    assert old_ref.status_code == 401, old_ref.text

    new_login = await client.post(
        f"{api}/auth/login", json={"email": session["email"], "password": "N3w-Sup3rSecret!"}
    )
    assert new_login.status_code == 200, new_login.text
    old_login = await client.post(
        f"{api}/auth/login", json={"email": session["email"], "password": session["password"]}
    )
    assert old_login.status_code == 401, old_login.text


async def test_change_password_requires_user_session(client, db_session, api) -> None:
    """API keys - even wildcard keys acting as the user - cannot rotate a password."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    _, raw_key = await factories.create_api_key(
        db_session, org=org, scopes=("*",), acts_as_user=owner
    )
    resp = await client.post(
        f"{api}/auth/change-password",
        headers=factories.api_key_headers(raw_key),
        json={"current_password": "Sup3rSecret!", "new_password": "N3w-Sup3rSecret!"},
    )
    assert resp.status_code == 403, resp.text


async def test_signup_is_closed_when_disabled(client, api, monkeypatch) -> None:
    """Self-serve registration is gated: a deployment holding platform provider keys must
    opt in, otherwise anyone could create an org and bill the operator's account."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SIGNUP_ENABLED", False)
    resp = await client.post(
        f"{api}/auth/register",
        json={
            "email": f"gated-{uuid.uuid4().hex}@example.com",
            "password": "Sup3rSecret!",
            "full_name": "Gated User",
            "org_name": "Gated Org",
        },
    )
    assert resp.status_code == 403, resp.text
    assert "signup" in resp.json()["detail"].lower()


async def test_signup_gate_defaults_to_closed_in_production() -> None:
    """Unset means 'enabled outside production': a production deployment is closed unless
    the operator explicitly opts in."""
    from app.core.config import Settings

    prod = Settings(ENVIRONMENT="production", SECRET_KEY="x" * 32)
    dev = Settings(ENVIRONMENT="development", SECRET_KEY="x" * 32)
    assert prod.signup_enabled is False
    assert dev.signup_enabled is True
    assert (
        Settings(ENVIRONMENT="production", SECRET_KEY="x" * 32, SIGNUP_ENABLED=True).signup_enabled
        is True
    )
