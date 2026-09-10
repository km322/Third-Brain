"""Integration: the production first-admin bootstrap (``app.scripts.bootstrap_admin``).

Drives the command's async entrypoint against the real app + Postgres, then verifies the
created owner through the real HTTP surface: the user can log in, the account is an OWNER,
and an optionally-minted API key authenticates. Also covers idempotency (a second run
creates nothing) and the config-resolution contract (which needs no infrastructure).
"""

from __future__ import annotations

import pytest

from app.scripts import bootstrap_admin
from app.scripts.bootstrap_admin import BootstrapError, resolve_config

pytestmark = pytest.mark.integration

OWNER_EMAIL = "owner@example.com"


async def test_bootstrap_creates_owner_who_can_log_in(client, api) -> None:
    """No password was supplied, so a strong one is generated and shown exactly once.

    The generated credentials actually authenticate against the real login route.
    """
    lines: list[str] = []
    config = resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL, "ADMIN_ORG_NAME": "Acme Self-Host"})

    result = await bootstrap_admin.execute(config, out=lines.append)

    assert result.created is True
    assert result.email == OWNER_EMAIL
    assert result.org_name == "Acme Self-Host"
    assert result.password
    assert sum(1 for line in lines if result.password in line) == 1

    login = await client.post(
        f"{api}/auth/login", json={"email": OWNER_EMAIL, "password": result.password}
    )
    assert login.status_code == 200, login.text

    me = await client.get(
        f"{api}/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["email"] == OWNER_EMAIL
    assert body["role"] == "owner"
    assert body["active_org"]["name"] == "Acme Self-Host"
    assert len(body["organizations"]) == 1


async def test_bootstrap_honors_supplied_password_without_echoing_it(client, api) -> None:
    """An operator-supplied password is never echoed back in the result/output."""
    supplied = "Sup3rSecret-selfhost!"
    config = resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL, "ADMIN_PASSWORD": supplied})

    result = await bootstrap_admin.execute(config, out=lambda _: None)

    assert result.created is True
    assert result.password is None

    login = await client.post(
        f"{api}/auth/login", json={"email": OWNER_EMAIL, "password": supplied}
    )
    assert login.status_code == 200, login.text


async def test_bootstrap_is_idempotent(client, api) -> None:
    """A second run must create nothing and print no secret.

    That is the exit-0, no-op path main() returns when execute() raises nothing and reports
    created=False. The original account is intact afterwards - the first password still logs
    in and there is exactly one organization (no duplicate was created).
    """
    config = resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL})
    first = await bootstrap_admin.execute(config, out=lambda _: None)
    assert first.created is True
    assert first.password

    second_lines: list[str] = []
    second = await bootstrap_admin.execute(config, out=second_lines.append)
    assert second.created is False
    assert second.password is None
    assert any("already bootstrapped" in line for line in second_lines)

    login = await client.post(
        f"{api}/auth/login", json={"email": OWNER_EMAIL, "password": first.password}
    )
    assert login.status_code == 200, login.text
    me = await client.get(
        f"{api}/users/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert len(me.json()["organizations"]) == 1


async def test_bootstrap_with_api_key_mints_a_working_key(client, api) -> None:
    """The minted key must carry admin-plus authority, not merely authenticate.

    /orgs/members is gated by require_role(ADMIN) and accepts API keys, so a scope-less or
    viewer key would get 403. The bootstrap owner appears in the returned membership list.
    """
    config = resolve_config(["--with-api-key"], {"ADMIN_EMAIL": OWNER_EMAIL})
    result = await bootstrap_admin.execute(config, out=lambda _: None)

    assert result.created is True
    assert result.api_key and result.api_key.startswith("tb_")

    headers = {"X-API-Key": result.api_key}
    members = await client.get(f"{api}/orgs/members", headers=headers)
    assert members.status_code == 200, members.text
    assert any(m["user"]["email"] == OWNER_EMAIL for m in members.json())


def test_resolve_config_requires_email(monkeypatch) -> None:
    """main() maps the missing-email config error to exit 2.

    The ambient env is cleared first so a stray ADMIN_EMAIL cannot let it fall through to
    execute() (real DB side effects).
    """
    with pytest.raises(BootstrapError, match="ADMIN_EMAIL is required"):
        resolve_config([], {})
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    assert bootstrap_admin.main([]) == 2


def test_resolve_config_rejects_a_bad_email() -> None:
    with pytest.raises(BootstrapError, match="not a valid email"):
        resolve_config([], {"ADMIN_EMAIL": "not-an-email"})


def test_resolve_config_rejects_a_short_password() -> None:
    with pytest.raises(BootstrapError, match="between 8 and 128"):
        resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL, "ADMIN_PASSWORD": "short"})


def test_resolve_config_rejects_an_overlong_password() -> None:
    """A password over 128 chars is refused before anything is created.

    Such a password would be rejected by the login route (422), so bootstrap must refuse it up
    front rather than create an admin that can never sign in.
    """
    with pytest.raises(BootstrapError, match="between 8 and 128"):
        resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL, "ADMIN_PASSWORD": "x" * 129})


def test_flags_override_env_and_defaults() -> None:
    config = resolve_config(
        ["--email", "flag@example.com", "--org-name", "Flag Org", "--with-api-key"],
        {"ADMIN_EMAIL": "env@example.com", "ADMIN_ORG_NAME": "Env Org"},
    )
    assert config.email == "flag@example.com"
    assert config.org_name == "Flag Org"
    assert config.with_api_key is True
    assert config.password is None


def test_defaults_and_env_flag() -> None:
    """With no org name given the config falls back to DEFAULT_ORG_NAME ("My Company"), and
    BOOTSTRAP_API_KEY in the environment turns key minting on.
    """
    config = resolve_config([], {"ADMIN_EMAIL": OWNER_EMAIL, "BOOTSTRAP_API_KEY": "1"})
    assert config.org_name == "My Company"
    assert config.with_api_key is True
    assert config.email == OWNER_EMAIL
