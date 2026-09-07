"""Integration: team CRUD, per-team leads, and nested-team management authority.

These pin the *management* semantics of teams (who may rename/re-parent/add-remove members/
set roles/create sub-teams/delete): the creator becomes a lead, a lead manages their team
and every sub-team beneath it, a lead of a parent team administers the whole subtree, and an
org admin administers everything. Plain members and unrelated leads are refused. Document
*visibility* is exercised separately in ``test_permissions_acl.py``.
"""

from __future__ import annotations

import factories
import pytest

from app.models.enums import OrgRole

pytestmark = pytest.mark.integration


async def _create_team(client, api, headers, name, *, parent_team_id=None):
    body: dict = {"name": name}
    if parent_team_id is not None:
        body["parent_team_id"] = str(parent_team_id)
    resp = await client.post(f"{api}/teams", headers=headers, json=body)
    return resp


async def test_team_crud_and_membership(client, db_session, token_headers, api) -> None:
    """The creator is enrolled as a lead; CRUD + membership round-trips for an admin."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    headers = token_headers(owner.id, org.id)

    created = await client.post(
        f"{api}/teams",
        headers=headers,
        json={"name": "Platform", "description": "Infra crew"},
    )
    assert created.status_code == 201, created.text
    team = created.json()
    assert team["slug"]
    assert team["parent_team_id"] is None
    # The creator is added as the first member with the lead role.
    assert team["member_count"] == 1
    team_id = team["id"]

    detail = await client.get(f"{api}/teams/{team_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    creator_row = next(m for m in detail.json()["members"] if m["user_id"] == str(owner.id))
    assert creator_row["role"] == "lead"
    assert detail.json()["children"] == []

    added = await client.post(
        f"{api}/teams/{team_id}/members",
        headers=headers,
        json={"user_id": str(member.id)},
    )
    assert added.status_code == 200, added.text
    assert added.json()["member_count"] == 2
    member_row = next(m for m in added.json()["members"] if m["user_id"] == str(member.id))
    assert member_row["role"] == "member"

    listed = await client.get(f"{api}/teams", headers=headers)
    assert any(t["id"] == team_id and t["member_count"] == 2 for t in listed.json())

    updated = await client.patch(
        f"{api}/teams/{team_id}", headers=headers, json={"name": "Platform Team"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Platform Team"

    removed = await client.delete(f"{api}/teams/{team_id}/members/{member.id}", headers=headers)
    assert removed.status_code == 204

    deleted = await client.delete(f"{api}/teams/{team_id}", headers=headers)
    assert deleted.status_code == 204


async def test_team_mutation_requires_editor(client, db_session, token_headers, api) -> None:
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    resp = await client.post(
        f"{api}/teams", headers=token_headers(viewer.id, org.id), json={"name": "Nope"}
    )
    assert resp.status_code == 403, resp.text


async def test_team_membership_changes_require_team_admin(
    client, db_session, token_headers, api
) -> None:
    """Membership changes confer the team's grants, so they require management authority. A
    plain editor (neither org admin nor a lead) must not add themselves to a team; an org
    admin can.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    team = await factories.create_team(db_session, org=org)
    editor, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    # A plain editor cannot escalate by joining a team that may own resources / hold grants.
    self_add = await client.post(
        f"{api}/teams/{team.id}/members",
        headers=token_headers(editor.id, org.id),
        json={"user_id": str(editor.id)},
    )
    assert self_add.status_code == 403, self_add.text

    # An org admin can manage membership.
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    added = await client.post(
        f"{api}/teams/{team.id}/members",
        headers=token_headers(admin.id, org.id),
        json={"user_id": str(editor.id)},
    )
    assert added.status_code == 200, added.text

    removed = await client.delete(
        f"{api}/teams/{team.id}/members/{editor.id}",
        headers=token_headers(editor.id, org.id),
    )
    assert removed.status_code == 403, removed.text


async def test_team_lead_manages_their_own_team(client, db_session, token_headers, api) -> None:
    """A lead (here the creator) can rename, add/remove members, set roles and spin up a
    sub-team on the team they lead."""
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    other, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    lead_headers = token_headers(lead.id, org.id)

    created = await _create_team(client, api, lead_headers, "Squad")
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]

    renamed = await client.patch(
        f"{api}/teams/{team_id}", headers=lead_headers, json={"name": "Squad Renamed"}
    )
    assert renamed.status_code == 200, renamed.text

    added = await client.post(
        f"{api}/teams/{team_id}/members", headers=lead_headers, json={"user_id": str(other.id)}
    )
    assert added.status_code == 200, added.text

    promoted = await client.patch(
        f"{api}/teams/{team_id}/members/{other.id}", headers=lead_headers, json={"role": "lead"}
    )
    assert promoted.status_code == 200, promoted.text
    other_row = next(m for m in promoted.json()["members"] if m["user_id"] == str(other.id))
    assert other_row["role"] == "lead"

    sub = await _create_team(client, api, lead_headers, "Sub Squad", parent_team_id=team_id)
    assert sub.status_code == 201, sub.text
    assert sub.json()["parent_team_id"] == team_id

    removed = await client.delete(f"{api}/teams/{team_id}/members/{other.id}", headers=lead_headers)
    assert removed.status_code == 204, removed.text


async def test_parent_lead_administers_descendant_subteam(
    client, db_session, token_headers, api
) -> None:
    """A lead of a PARENT team administers a descendant sub-team it does not directly lead."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    owner_headers = token_headers(owner.id, org.id)
    parent_lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    outsider_member, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    # parent_lead creates and thus leads P; owner (admin) creates S under P so parent_lead is
    # NOT a member/lead of S -- authority over S must flow purely from leading the ancestor.
    parent = await _create_team(client, api, token_headers(parent_lead.id, org.id), "Parent")
    assert parent.status_code == 201, parent.text
    parent_id = parent.json()["id"]
    sub = await _create_team(client, api, owner_headers, "Child", parent_team_id=parent_id)
    assert sub.status_code == 201, sub.text
    sub_id = sub.json()["id"]

    pl_headers = token_headers(parent_lead.id, org.id)
    detail = await client.get(f"{api}/teams/{sub_id}", headers=pl_headers)
    assert detail.status_code == 200, detail.text
    assert all(m["user_id"] != str(parent_lead.id) for m in detail.json()["members"])

    renamed = await client.patch(
        f"{api}/teams/{sub_id}", headers=pl_headers, json={"name": "Child Renamed"}
    )
    assert renamed.status_code == 200, renamed.text
    added = await client.post(
        f"{api}/teams/{sub_id}/members",
        headers=pl_headers,
        json={"user_id": str(outsider_member.id), "role": "member"},
    )
    assert added.status_code == 200, added.text
    role_set = await client.patch(
        f"{api}/teams/{sub_id}/members/{outsider_member.id}",
        headers=pl_headers,
        json={"role": "lead"},
    )
    assert role_set.status_code == 200, role_set.text


async def test_subteam_lead_can_rename_with_unchanged_parent(
    client, db_session, token_headers, api
) -> None:
    """A sub-team lead who does NOT administer the parent can still rename their own team.

    The dashboard always re-sends the current ``parent_team_id`` on a rename; that must be
    treated as "no move" and skip the destination-parent admin check, not 403 the lead.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    owner_headers = token_headers(owner.id, org.id)
    sub_lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    # owner creates parent P; owner creates child C under P and makes sub_lead its lead.
    parent = await _create_team(client, api, owner_headers, "Parent")
    parent_id = parent.json()["id"]
    child = await _create_team(client, api, owner_headers, "Child", parent_team_id=parent_id)
    assert child.status_code == 201, child.text
    child_id = child.json()["id"]
    added = await client.post(
        f"{api}/teams/{child_id}/members",
        headers=owner_headers,
        json={"user_id": str(sub_lead.id), "role": "lead"},
    )
    assert added.status_code == 200, added.text

    # sub_lead leads C but is not a member/lead of P and is not an org admin.
    lead_headers = token_headers(sub_lead.id, org.id)
    renamed = await client.patch(
        f"{api}/teams/{child_id}",
        headers=lead_headers,
        json={"name": "Child Renamed", "parent_team_id": parent_id},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Child Renamed"
    assert renamed.json()["parent_team_id"] == parent_id

    # But actually MOVING it under a team they don't administer is still refused.
    other = await _create_team(client, api, owner_headers, "Other Parent")
    moved = await client.patch(
        f"{api}/teams/{child_id}",
        headers=lead_headers,
        json={"parent_team_id": other.json()["id"]},
    )
    assert moved.status_code == 403, moved.text


async def test_lead_cannot_administer_unrelated_team(
    client, db_session, token_headers, api
) -> None:
    """A lead's authority is confined to their subtree: an unrelated team is off-limits."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    owner_headers = token_headers(owner.id, org.id)
    lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    victim, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    my_team = await _create_team(client, api, token_headers(lead.id, org.id), "Mine")
    assert my_team.status_code == 201, my_team.text
    unrelated = await _create_team(client, api, owner_headers, "Theirs")
    assert unrelated.status_code == 201, unrelated.text
    unrelated_id = unrelated.json()["id"]

    lead_headers = token_headers(lead.id, org.id)
    renamed = await client.patch(
        f"{api}/teams/{unrelated_id}", headers=lead_headers, json={"name": "Hijacked"}
    )
    assert renamed.status_code == 403, renamed.text
    added = await client.post(
        f"{api}/teams/{unrelated_id}/members",
        headers=lead_headers,
        json={"user_id": str(victim.id)},
    )
    assert added.status_code == 403, added.text
    deleted = await client.delete(f"{api}/teams/{unrelated_id}", headers=lead_headers)
    assert deleted.status_code == 403, deleted.text


async def test_plain_member_cannot_manage_team(client, db_session, token_headers, api) -> None:
    """A plain (non-lead, non-org-admin) member cannot manage the team it belongs to."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    owner_headers = token_headers(owner.id, org.id)
    plain, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    someone, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    created = await _create_team(client, api, owner_headers, "Team")
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]
    joined = await client.post(
        f"{api}/teams/{team_id}/members",
        headers=owner_headers,
        json={"user_id": str(plain.id), "role": "member"},
    )
    assert joined.status_code == 200, joined.text

    plain_headers = token_headers(plain.id, org.id)
    assert (
        await client.patch(f"{api}/teams/{team_id}", headers=plain_headers, json={"name": "X"})
    ).status_code == 403
    assert (
        await client.post(
            f"{api}/teams/{team_id}/members",
            headers=plain_headers,
            json={"user_id": str(someone.id)},
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"{api}/teams/{team_id}/members/{plain.id}",
            headers=plain_headers,
            json={"role": "lead"},
        )
    ).status_code == 403
    assert (
        await _create_team(client, api, plain_headers, "Sneaky Sub", parent_team_id=team_id)
    ).status_code == 403


async def test_org_admin_administers_any_team(client, db_session, token_headers, api) -> None:
    """An org admin can manage a team it neither created nor belongs to."""
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    creator, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    newcomer, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    created = await _create_team(client, api, token_headers(creator.id, org.id), "Owned")
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]

    admin_headers = token_headers(admin.id, org.id)
    assert (
        await client.patch(f"{api}/teams/{team_id}", headers=admin_headers, json={"name": "R"})
    ).status_code == 200
    assert (
        await client.post(
            f"{api}/teams/{team_id}/members",
            headers=admin_headers,
            json={"user_id": str(newcomer.id)},
        )
    ).status_code == 200
    assert (await client.delete(f"{api}/teams/{team_id}", headers=admin_headers)).status_code == 204


async def test_reparent_cycle_is_rejected(client, db_session, token_headers, api) -> None:
    """A team may not be re-parented under itself or one of its descendants."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    parent = await _create_team(client, api, headers, "P")
    parent_id = parent.json()["id"]
    child = await _create_team(client, api, headers, "S", parent_team_id=parent_id)
    child_id = child.json()["id"]

    self_parent = await client.patch(
        f"{api}/teams/{parent_id}", headers=headers, json={"parent_team_id": parent_id}
    )
    assert self_parent.status_code in (400, 409), self_parent.text

    under_descendant = await client.patch(
        f"{api}/teams/{parent_id}", headers=headers, json={"parent_team_id": child_id}
    )
    assert under_descendant.status_code in (400, 409), under_descendant.text


async def test_reparent_requires_admin_on_new_parent(
    client, db_session, token_headers, api
) -> None:
    """Re-parenting requires management authority over the DESTINATION, not just the moved
    team. Otherwise a lead could graft their team under a privileged team to inherit its
    downward-flowing grants. An org admin (or a lead of the destination) may still move it.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    owner_headers = token_headers(owner.id, org.id)
    lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    lead_headers = token_headers(lead.id, org.id)

    # `lead` leads their own team; `owner` owns a separate team `lead` does not administer.
    mine = await _create_team(client, api, lead_headers, "Mine")
    assert mine.status_code == 201, mine.text
    mine_id = mine.json()["id"]
    dest = await _create_team(client, api, owner_headers, "Privileged")
    assert dest.status_code == 201, dest.text
    dest_id = dest.json()["id"]

    escalate = await client.patch(
        f"{api}/teams/{mine_id}", headers=lead_headers, json={"parent_team_id": dest_id}
    )
    assert escalate.status_code == 403, escalate.text

    moved = await client.patch(
        f"{api}/teams/{mine_id}", headers=owner_headers, json={"parent_team_id": dest_id}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_team_id"] == dest_id


async def test_reparent_allowed_when_lead_administers_destination(
    client, db_session, token_headers, api
) -> None:
    """A lead who administers BOTH the moved team and the destination may re-parent."""
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    lead_headers = token_headers(lead.id, org.id)

    mine = await _create_team(client, api, lead_headers, "Mine")
    dest = await _create_team(client, api, lead_headers, "Dest")
    mine_id, dest_id = mine.json()["id"], dest.json()["id"]

    moved = await client.patch(
        f"{api}/teams/{mine_id}", headers=lead_headers, json={"parent_team_id": dest_id}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_team_id"] == dest_id


async def test_last_lead_cannot_be_removed_or_demoted(
    client, db_session, token_headers, api
) -> None:
    """A team must keep at least one lead: removing/demoting the final lead is 409 for a
    non-admin, while a plain member or a non-last lead may be removed/demoted, and an org
    admin may override and drop the last lead.
    """
    org, _owner, _ = await factories.create_org_with_owner(db_session)
    lead, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    lead_headers = token_headers(lead.id, org.id)
    second, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    plain, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)

    created = await _create_team(client, api, lead_headers, "Crew")
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]

    assert (
        await client.post(
            f"{api}/teams/{team_id}/members", headers=lead_headers, json={"user_id": str(plain.id)}
        )
    ).status_code == 200
    assert (
        await client.post(
            f"{api}/teams/{team_id}/members",
            headers=lead_headers,
            json={"user_id": str(second.id), "role": "lead"},
        )
    ).status_code == 200

    # A plain member may always be removed; the lead count is untouched.
    assert (
        await client.delete(f"{api}/teams/{team_id}/members/{plain.id}", headers=lead_headers)
    ).status_code == 204
    # With two leads, demoting one is fine because a lead remains.
    demote_non_last = await client.patch(
        f"{api}/teams/{team_id}/members/{second.id}", headers=lead_headers, json={"role": "member"}
    )
    assert demote_non_last.status_code == 200, demote_non_last.text

    # `lead` is now the sole lead: neither demoting nor removing them is allowed for a non-admin.
    self_demote = await client.patch(
        f"{api}/teams/{team_id}/members/{lead.id}", headers=lead_headers, json={"role": "member"}
    )
    assert self_demote.status_code == 409, self_demote.text
    self_remove = await client.delete(
        f"{api}/teams/{team_id}/members/{lead.id}", headers=lead_headers
    )
    assert self_remove.status_code == 409, self_remove.text

    # An org admin may override and remove the last lead.
    admin, _ = await factories.add_member(db_session, org=org, role=OrgRole.ADMIN)
    admin_remove = await client.delete(
        f"{api}/teams/{team_id}/members/{lead.id}", headers=token_headers(admin.id, org.id)
    )
    assert admin_remove.status_code == 204, admin_remove.text


async def test_depth_limit_is_enforced(client, db_session, token_headers, api) -> None:
    """Team nesting is capped at six levels; a seventh under a depth-6 leaf is rejected."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    parent_id = None
    leaf_id = None
    for i in range(6):
        resp = await _create_team(client, api, headers, f"Level {i + 1}", parent_team_id=parent_id)
        assert resp.status_code == 201, resp.text
        parent_id = leaf_id = resp.json()["id"]

    too_deep = await _create_team(client, api, headers, "Level 7", parent_team_id=leaf_id)
    assert too_deep.status_code == 400, too_deep.text


async def test_deleting_parent_reparents_children_to_root(
    client, db_session, token_headers, api
) -> None:
    """Deleting a parent detaches its sub-teams to the root rather than cascade-deleting."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)

    parent = await _create_team(client, api, headers, "Parent")
    parent_id = parent.json()["id"]
    s1 = await _create_team(client, api, headers, "Sub 1", parent_team_id=parent_id)
    s2 = await _create_team(client, api, headers, "Sub 2", parent_team_id=parent_id)
    s1_id, s2_id = s1.json()["id"], s2.json()["id"]

    deleted = await client.delete(f"{api}/teams/{parent_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    for child_id in (s1_id, s2_id):
        got = await client.get(f"{api}/teams/{child_id}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["parent_team_id"] is None
