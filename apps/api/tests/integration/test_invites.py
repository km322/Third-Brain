"""Integration: email-based org invitations."""

from __future__ import annotations

import re
import uuid

import factories
import pytest

from app.models.enums import OrgRole
from app.services.email import clear_outbox, outbox
from app.services.identity import get_membership, get_user_by_email

pytestmark = pytest.mark.integration


def _token_from_outbox(email: str) -> str:
    mails = [m for m in outbox() if m.to == email]
    assert mails, f"no invite email captured for {email}"
    match = re.search(r"token=([A-Za-z0-9_\-]+)", mails[-1].text)
    assert match, "no token in invite email"
    return match.group(1)


async def test_invite_and_accept(client, db_session, token_headers, api) -> None:
    clear_outbox()
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"

    created = await client.post(
        f"{api}/invites", headers=headers, json={"email": email, "role": "editor"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"

    token = _token_from_outbox(email)
    accepted = await client.post(
        f"{api}/invites/accept",
        json={"token": token, "full_name": "New User", "password": "Sup3rSecret!"},
    )
    assert accepted.status_code == 200, accepted.text
    access = accepted.json()["access_token"]
    assert access

    # The provisioned session works, and the user is an ACTIVE member with the invited role.
    me = await client.get(f"{api}/users/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200, me.text
    user = await get_user_by_email(db_session, email)
    assert user is not None and user.full_name == "New User"
    membership = await get_membership(db_session, org.id, user.id)
    assert membership is not None
    assert membership.role == OrgRole.EDITOR
    assert membership.status.value == "active"

    # The invitation is single-use.
    again = await client.post(
        f"{api}/invites/accept",
        json={"token": token, "full_name": "x", "password": "Sup3rSecret!"},
    )
    assert again.status_code == 400, again.text


async def test_create_returns_the_acceptance_link(client, db_session, token_headers, api) -> None:
    """The default EMAIL_PROVIDER=stub never delivers, so the creator must get the link.

    Without this the whole feature is a dead end on a self-host that has not set up SMTP:
    the token is stored hashed and exists nowhere an operator can reach it.
    """
    clear_outbox()
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"

    created = await client.post(
        f"{api}/invites", headers=headers, json={"email": email, "role": "viewer"}
    )
    assert created.status_code == 201, created.text
    accept_url = created.json()["accept_url"]
    assert accept_url and f"token={_token_from_outbox(email)}" in accept_url

    # Listing invites must never re-expose a link (the token only exists at creation time).
    listed = await client.get(f"{api}/invites", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [i["email"] for i in listed.json()] == [email]
    assert listed.json()[0]["accept_url"] is None

    # The returned link really is usable on its own - no mail delivery involved.
    token = accept_url.split("token=", 1)[1]
    accepted = await client.post(
        f"{api}/invites/accept",
        json={"token": token, "full_name": "Linked User", "password": "Sup3rSecret!"},
    )
    assert accepted.status_code == 200, accepted.text


async def test_invite_existing_user_conflicts(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    existing, _ = await factories.add_member(db_session, org=org, email="dup@example.com")
    resp = await client.post(
        f"{api}/invites",
        headers=token_headers(owner.id, org.id),
        json={"email": "dup@example.com", "role": "viewer"},
    )
    assert resp.status_code == 409, resp.text


async def test_accept_refuses_when_email_registered_after_invite(
    client, db_session, token_headers, api
) -> None:
    """If the invitee registers their own account AFTER the invite is created, accepting the
    invite must not sign in as that pre-existing account (token-holder account takeover)."""
    clear_outbox()
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    email = f"racer-{uuid.uuid4().hex[:8]}@example.com"
    created = await client.post(
        f"{api}/invites", headers=headers, json={"email": email, "role": "editor"}
    )
    assert created.status_code == 201, created.text
    token = _token_from_outbox(email)

    # The invitee independently registers their own account + org in the meantime.
    reg = await client.post(
        f"{api}/auth/register",
        json={
            "email": email,
            "password": "Own3rPass!",
            "full_name": "Real Owner",
            "org_name": "Their Org",
        },
    )
    assert reg.status_code == 201, reg.text

    # Accepting the invite now must be refused, never mint a session for the existing account.
    accepted = await client.post(
        f"{api}/invites/accept",
        json={"token": token, "full_name": "Someone Else", "password": "different-pass"},
    )
    assert accepted.status_code == 409, accepted.text


async def test_accept_invalid_token(client, db_session, token_headers, api) -> None:
    resp = await client.post(
        f"{api}/invites/accept",
        json={"token": "not-a-real-token-xxxxxxxx", "full_name": "X", "password": "Sup3rSecret!"},
    )
    assert resp.status_code == 400, resp.text


async def test_invite_requires_admin(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/invites",
        headers=token_headers(viewer.id, org.id),
        json={"email": "x@example.com"},
    )
    assert resp.status_code == 403, resp.text


async def test_admin_cannot_invite_owner(client, db_session, token_headers, api) -> None:
    """An admin (non-owner) must not be able to mint an OWNER via email invite; an owner can."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    denied = await client.post(
        f"{api}/invites",
        headers=token_headers(admin.id, org.id),
        json={"email": "newowner@example.com", "role": "owner"},
    )
    assert denied.status_code == 403, denied.text
    allowed = await client.post(
        f"{api}/invites",
        headers=token_headers(owner.id, org.id),
        json={"email": "newowner2@example.com", "role": "owner"},
    )
    assert allowed.status_code == 201, allowed.text
