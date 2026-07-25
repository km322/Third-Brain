"""Integration: organizations, org switching, and membership administration."""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole

pytestmark = pytest.mark.integration


async def test_suspended_member_loses_access_immediately(
    client, db_session, token_headers, api
) -> None:
    """Suspending a member revokes access on the very next request - even with a token
    minted while they were active (enforced per-request, not just at token issue)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    owner_headers = token_headers(owner.id, org.id)
    member_headers = token_headers(member.id, org.id)

    # An active member can use their session.
    ok = await client.get(f"{api}/users/me", headers=member_headers)
    assert ok.status_code == 200, ok.text

    # The owner suspends the member.
    patched = await client.patch(
        f"{api}/orgs/members/{membership.id}",
        headers=owner_headers,
        json={"status": "suspended"},
    )
    assert patched.status_code == 200, patched.text

    # The member's already-issued access token is now rejected everywhere.
    denied = await client.get(f"{api}/users/me", headers=member_headers)
    assert denied.status_code == 403, denied.text


async def test_removing_member_purges_their_grants(client, db_session, token_headers, api) -> None:
    """Removing a member deletes their ACL grants, so re-inviting the same user later does
    not silently restore access an admin never re-granted (finding 21)."""
    from sqlalchemy import func, select

    from app.models.access import AccessGrant
    from app.models.enums import PermissionLevel, PrincipalType, ResourceType

    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    collection = await factories.create_collection(
        db_session, org=org, owner=owner, visibility="private"
    )
    await factories.grant_user(
        db_session,
        org=org,
        user=member,
        resource_type=ResourceType.COLLECTION,
        resource_id=collection.id,
        permission=PermissionLevel.MANAGER,
    )

    removed = await client.delete(
        f"{api}/orgs/members/{membership.id}", headers=token_headers(owner.id, org.id)
    )
    assert removed.status_code == 204, removed.text

    remaining = (
        await db_session.execute(
            select(func.count())
            .select_from(AccessGrant)
            .where(
                AccessGrant.org_id == org.id,
                AccessGrant.principal_type == PrincipalType.USER,
                AccessGrant.principal_id == member.id,
            )
        )
    ).scalar()
    assert remaining == 0


async def test_current_list_create_and_switch(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session, org_name="First Org")
    headers = token_headers(owner.id, org.id)

    current = await client.get(f"{api}/orgs/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["id"] == str(org.id)

    listed = await client.get(f"{api}/orgs", headers=headers)
    assert listed.status_code == 200
    assert any(o["id"] == str(org.id) for o in listed.json())

    created = await client.post(f"{api}/orgs", headers=headers, json={"name": "Second Org"})
    assert created.status_code == 201, created.text
    second_id = created.json()["id"]

    switch = await client.post(f"{api}/orgs/switch", headers=headers, json={"org_id": second_id})
    assert switch.status_code == 200, switch.text
    new_headers = {"Authorization": f"Bearer {switch.json()['access_token']}"}

    me = await client.get(f"{api}/users/me", headers=new_headers)
    assert me.json()["active_org"]["id"] == second_id


async def test_invite_activate_and_list_members(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    invitee = await factories.create_user(db_session, full_name="Invitee")
    headers = token_headers(owner.id, org.id)

    invited = await client.post(
        f"{api}/orgs/members/invite",
        headers=headers,
        json={"email": invitee.email, "role": "editor"},
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["status"] == "invited"
    membership_id = invited.json()["id"]

    activated = await client.patch(
        f"{api}/orgs/members/{membership_id}",
        headers=headers,
        json={"status": "active"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    members = await client.get(f"{api}/orgs/members", headers=headers)
    assert members.status_code == 200
    emails = {m["user"]["email"] for m in members.json() if m.get("user")}
    assert invitee.email in emails


async def test_invite_response_does_not_leak_invitee_profile(
    client, db_session, token_headers, api
) -> None:
    """Inviting must not echo back the (possibly cross-tenant) invitee's profile.

    The route accepts an arbitrary email and is reachable by any org admin, so returning the
    target's name/avatar/last_login/created_at would turn it into a cross-tenant PII oracle.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    victim = await factories.create_user(
        db_session, full_name="Victim Name", email="victim-profile@corp.com"
    )
    invited = await client.post(
        f"{api}/orgs/members/invite",
        headers=token_headers(owner.id, org.id),
        json={"email": victim.email, "role": "viewer"},
    )
    assert invited.status_code == 201, invited.text
    body = invited.json()
    # The membership fields are present, but the embedded user profile is not.
    assert body["status"] == "invited"
    assert body.get("user") is None
    assert "Victim Name" not in invited.text


async def test_members_admin_only(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.get(f"{api}/orgs/members", headers=token_headers(viewer.id, org.id))
    assert resp.status_code == 403, resp.text


async def test_cannot_demote_last_owner(client, db_session, token_headers, api) -> None:
    org, owner, membership = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    resp = await client.patch(
        f"{api}/orgs/members/{membership.id}",
        headers=headers,
        json={"role": "viewer"},
    )
    assert resp.status_code == 400, resp.text


async def test_admin_cannot_change_owner_status(client, db_session, token_headers, api) -> None:
    """A non-owner admin may not suspend/deactivate an owner - owner-only, like role changes."""
    org, _owner, owner_membership = await factories.create_org_with_owner(db_session)
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    resp = await client.patch(
        f"{api}/orgs/members/{owner_membership.id}",
        headers=token_headers(admin.id, org.id),
        json={"status": "suspended"},
    )
    assert resp.status_code == 403, resp.text


async def test_owner_can_change_another_owners_status(
    client, db_session, token_headers, api
) -> None:
    """An owner may suspend a fellow owner while another owner remains to run the org."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    second_owner, second_membership = await factories.add_member(
        db_session, org=org, role=OrgRole.OWNER
    )
    resp = await client.patch(
        f"{api}/orgs/members/{second_membership.id}",
        headers=token_headers(owner.id, org.id),
        json={"status": "suspended"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


async def test_cannot_suspend_last_owner(client, db_session, token_headers, api) -> None:
    """The last-owner protection still applies: suspending the only owner returns 400."""
    org, owner, membership = await factories.create_org_with_owner(db_session)
    resp = await client.patch(
        f"{api}/orgs/members/{membership.id}",
        headers=token_headers(owner.id, org.id),
        json={"status": "suspended"},
    )
    assert resp.status_code == 400, resp.text


async def test_cannot_suspend_last_active_owner_when_other_owner_is_suspended(
    client, db_session, token_headers, api
) -> None:
    """A suspended co-owner does not count toward the owner guard: leaving the org with no
    ACTIVE owner (and thus locked out, since suspended users cannot sign in) is refused."""
    from app.models.enums import MembershipStatus

    org, owner, owner_membership = await factories.create_org_with_owner(db_session)
    # A second owner exists but is already suspended, so they cannot run the org.
    _second, _second_membership = await factories.add_member(
        db_session, org=org, role=OrgRole.OWNER, status=MembershipStatus.SUSPENDED
    )
    resp = await client.patch(
        f"{api}/orgs/members/{owner_membership.id}",
        headers=token_headers(owner.id, org.id),
        json={"status": "suspended"},
    )
    assert resp.status_code == 400, resp.text


async def test_admin_reset_password_happy_path(client, db_session, token_headers, api) -> None:
    """An admin resets a member's password: the temporary password (shown exactly once)
    logs in, the old password dies, and every session the member had is revoked."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    member_headers = token_headers(member.id, org.id)

    before = await client.get(f"{api}/users/me", headers=member_headers)
    assert before.status_code == 200, before.text

    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=token_headers(owner.id, org.id),
    )
    assert resp.status_code == 200, resp.text
    temp_password = resp.json()["temporary_password"]

    login = await client.post(
        f"{api}/auth/login", json={"email": member.email, "password": temp_password}
    )
    assert login.status_code == 200, login.text

    old_login = await client.post(
        f"{api}/auth/login", json={"email": member.email, "password": "Sup3rSecret!"}
    )
    assert old_login.status_code == 401, old_login.text

    # The member's pre-reset session is revoked by the token_version bump.
    after = await client.get(f"{api}/users/me", headers=member_headers)
    assert after.status_code == 401, after.text


async def test_admin_cannot_reset_owner_password(client, db_session, token_headers, api) -> None:
    """The owner shield mirrors role changes: only an owner may reset an owner."""
    org, _owner, owner_membership = await factories.create_org_with_owner(db_session)
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    resp = await client.post(
        f"{api}/orgs/members/{owner_membership.id}/reset-password",
        headers=token_headers(admin.id, org.id),
    )
    assert resp.status_code == 403, resp.text


async def test_cannot_reset_own_password_via_admin_endpoint(
    client, db_session, token_headers, api
) -> None:
    """Resetting yourself is refused - change-password (which verifies the current
    password) is the only self-service path."""
    org, owner, membership = await factories.create_org_with_owner(db_session)
    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=token_headers(owner.id, org.id),
    )
    assert resp.status_code == 400, resp.text


async def test_reset_password_multi_org_member_is_conflict(
    client, db_session, token_headers, api
) -> None:
    """A target with an ACTIVE membership in another org is refused (409): an admin who
    learns the temporary password could otherwise take over the user's other tenants."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    other_org = await factories.create_org(db_session)
    await factories.create_membership(db_session, org=other_org, user=member, role=OrgRole.VIEWER)

    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=token_headers(owner.id, org.id),
    )
    assert resp.status_code == 409, resp.text


async def test_reset_password_refuses_member_of_other_org_even_when_suspended(
    client, db_session, token_headers, api
) -> None:
    """A suspended membership elsewhere can be reactivated by that org alone, so the reset
    (which sets a GLOBAL password) is refused (409): otherwise an admin who learned the
    temporary password could take over the user's other tenant the moment it reactivates
    them."""
    from app.models.enums import MembershipStatus

    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    other_org = await factories.create_org(db_session)
    await factories.create_membership(
        db_session, org=other_org, user=member, status=MembershipStatus.SUSPENDED
    )

    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=token_headers(owner.id, org.id),
    )
    assert resp.status_code == 409, resp.text


async def test_reset_password_refuses_member_of_other_org_when_invited(
    client, db_session, token_headers, api
) -> None:
    """An invited (not yet accepted) membership elsewhere is activated by that org alone,
    so the reset is likewise refused (409)."""
    from app.models.enums import MembershipStatus

    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    other_org = await factories.create_org(db_session)
    await factories.create_membership(
        db_session, org=other_org, user=member, status=MembershipStatus.INVITED
    )

    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=token_headers(owner.id, org.id),
    )
    assert resp.status_code == 409, resp.text


async def test_reset_password_requires_user_session(client, db_session, api) -> None:
    """Admin-scoped API keys cannot reset passwords - it is a human-session endpoint."""
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    _member, membership = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    _, raw_key = await factories.create_api_key(db_session, org=org, scopes=("manage",))
    resp = await client.post(
        f"{api}/orgs/members/{membership.id}/reset-password",
        headers=factories.api_key_headers(raw_key),
    )
    assert resp.status_code == 403, resp.text
