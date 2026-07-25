"""Integration: SCIM 2.0 provisioning (Users + Groups) + token auth."""

from __future__ import annotations

import uuid

import factories
import pytest

from app.models.enums import MembershipStatus, OrgRole
from app.services.identity import get_membership, get_user_by_email

pytestmark = pytest.mark.integration


async def _scim_token(client, api, headers) -> str:
    resp = await client.post(f"{api}/scim-tokens", headers=headers, json={"name": "Okta"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


async def test_scim_provisions_and_deactivates_user(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    raw = await _scim_token(client, api, token_headers(owner.id, org.id))
    scim_headers = {"Authorization": f"Bearer {raw}"}
    email = f"scim-{uuid.uuid4().hex[:8]}@example.com"

    created = await client.post(
        f"{api}/scim/v2/Users",
        headers=scim_headers,
        json={"userName": email, "name": {"givenName": "Sam", "familyName": "Ng"}, "active": True},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    assert created.json()["active"] is True

    # A real member now exists in the org.
    user = await get_user_by_email(db_session, email)
    assert user is not None
    membership = await get_membership(db_session, org.id, user.id)
    assert membership is not None and membership.status == MembershipStatus.ACTIVE

    # Filtered list finds them.
    listed = await client.get(
        f"{api}/scim/v2/Users", headers=scim_headers, params={"filter": f'userName eq "{email}"'}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["totalResults"] == 1

    # Deactivate via PATCH -> membership suspended.
    patched = await client.patch(
        f"{api}/scim/v2/Users/{user_id}",
        headers=scim_headers,
        json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["active"] is False
    await db_session.refresh(membership)
    assert membership.status == MembershipStatus.SUSPENDED


async def test_scim_creates_group(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    raw = await _scim_token(client, api, token_headers(owner.id, org.id))
    scim_headers = {"Authorization": f"Bearer {raw}"}

    member = await client.post(
        f"{api}/scim/v2/Users",
        headers=scim_headers,
        json={"userName": f"m-{uuid.uuid4().hex[:6]}@example.com"},
    )
    member_id = member.json()["id"]

    group = await client.post(
        f"{api}/scim/v2/Groups",
        headers=scim_headers,
        json={"displayName": "Engineering", "members": [{"value": member_id}]},
    )
    assert group.status_code == 201, group.text
    assert group.json()["displayName"] == "Engineering"
    assert [m["value"] for m in group.json()["members"]] == [member_id]


async def test_scim_rejects_bad_token(client, db_session, token_headers, api) -> None:
    resp = await client.get(f"{api}/scim/v2/Users", headers={"Authorization": "Bearer scim_nope"})
    assert resp.status_code == 401, resp.text


async def test_scim_token_admin_requires_admin(client, db_session, token_headers, api) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/scim-tokens", headers=token_headers(viewer.id, org.id), json={"name": "x"}
    )
    assert resp.status_code == 403, resp.text
