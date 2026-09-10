"""Integration: end-to-end ACL enforcement and grant/revoke dynamics.

The security-critical guarantee: a member of the org with **no** grant on a private
collection can neither retrieve its content (zero search hits) nor mutate it (403), and
granting/revoking an explicit permission flips both the *effective permission* and what
*retrieval* returns - proving the route-authorization path and the SQL retrieval scope
stay in lockstep.

The second half of the file covers nested teams, where grants flow strictly DOWNWARD
(upward-only expansion of a user's effective team set). Both enforcement sites - route
authorization and the SQL retrieval scope - must agree for every one of those cases too.
"""

from __future__ import annotations

import factories
import pytest

from app.core.deps import AuthContext
from app.models.enums import OrgRole, PermissionLevel, PrincipalType, ResourceType, Visibility
from app.services.permissions import user_team_ids

pytestmark = pytest.mark.integration

_SECRET = "The launch code for project aurora is stored in this confidential note."
_QUERY = {"query": "project aurora launch code"}


async def _set_parent(db_session, *, child, parent) -> None:
    """Attach ``child`` under ``parent`` in the team hierarchy (single-parent nesting)."""
    child.parent_team_id = parent.id
    await db_session.commit()


async def _grant_team_collection(db_session, *, org, team, collection):
    """Grant ``team`` VIEWER on ``collection`` (a team-principal AccessGrant)."""
    return await factories.create_access_grant(
        db_session,
        org=org,
        resource_type=ResourceType.COLLECTION,
        resource_id=collection.id,
        principal_type=PrincipalType.TEAM,
        principal_id=team.id,
        permission=PermissionLevel.VIEWER,
    )


async def _viewer_collection_with_doc(
    db_session, *, org, owner, title, owner_team=None, team_visibility=False
):
    """A VIEWER-default collection holding one indexed ``_SECRET`` document."""
    collection = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        owner_team=owner_team,
        visibility=Visibility.TEAM if team_visibility else Visibility.PRIVATE,
        default_permission=PermissionLevel.VIEWER,
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title=title,
        content=_SECRET,
        created_by=owner,
    )
    return collection, document


async def _setup_private_collection(db_session):
    org, owner, _ = await factories.create_org_with_owner(db_session)
    collection = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        visibility=Visibility.PRIVATE,
        default_permission=PermissionLevel.VIEWER,
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Confidential",
        content=_SECRET,
        created_by=owner,
    )
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    return org, owner, collection, document, viewer


async def test_member_without_grant_is_denied_everywhere(
    client, db_session, token_headers, api
) -> None:
    """An org member with no grant on a private collection is denied on every surface.

    The owner (admin) is the positive control and CAN see it. For the ungranted viewer the
    effective permission is NONE, retrieval returns ZERO hits from the private collection, the
    collection is invisible in the list, reading it directly is 403, and a protected mutation
    (adding a document) is 403.
    """
    org, owner, collection, document, viewer = await _setup_private_collection(db_session)
    viewer_headers = token_headers(viewer.id, org.id)
    query = {"query": "project aurora launch code"}

    owner_hits = await client.post(
        f"{api}/search", headers=token_headers(owner.id, org.id), json=query
    )
    assert owner_hits.status_code == 200
    assert any(h["document_id"] == str(document.id) for h in owner_hits.json()["hits"])

    eff = await client.get(
        f"{api}/permissions/effective",
        headers=viewer_headers,
        params={"resource_type": "collection", "resource_id": str(collection.id)},
    )
    assert eff.status_code == 200, eff.text
    assert eff.json()["permission"] == "none"

    viewer_search = await client.post(f"{api}/search", headers=viewer_headers, json=query)
    assert viewer_search.status_code == 200, viewer_search.text
    assert viewer_search.json()["hits"] == []

    listed = await client.get(f"{api}/collections", headers=viewer_headers)
    assert listed.status_code == 200
    assert all(c["id"] != str(collection.id) for c in listed.json())

    read = await client.get(f"{api}/collections/{collection.id}", headers=viewer_headers)
    assert read.status_code == 403, read.text

    mutate = await client.post(
        f"{api}/documents/text",
        headers=viewer_headers,
        json={"collection_id": str(collection.id), "title": "Sneaky", "content": "nope"},
    )
    assert mutate.status_code == 403, mutate.text


async def test_grant_then_revoke_flips_retrieval_and_effective(
    client, db_session, token_headers, api
) -> None:
    """A grant flips both enforcement sites on; a revoke collapses them back to nothing.

    The owner acts as manager to GRANT viewer permission on the private collection: the
    effective permission is then VIEWER, retrieval surfaces the document, and the collection
    appears in the viewer's list. After the REVOKE, access collapses back to nothing.
    """
    org, owner, collection, document, viewer = await _setup_private_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    viewer_headers = token_headers(viewer.id, org.id)
    query = {"query": "project aurora launch code"}

    grant = await client.post(
        f"{api}/permissions",
        headers=owner_headers,
        json={
            "resource_type": "collection",
            "resource_id": str(collection.id),
            "principal_type": "user",
            "principal_id": str(viewer.id),
            "permission": "viewer",
        },
    )
    assert grant.status_code == 201, grant.text
    grant_id = grant.json()["id"]

    eff = await client.get(
        f"{api}/permissions/effective",
        headers=viewer_headers,
        params={"resource_type": "collection", "resource_id": str(collection.id)},
    )
    assert eff.json()["permission"] == "viewer"

    after_grant = await client.post(f"{api}/search", headers=viewer_headers, json=query)
    assert after_grant.status_code == 200
    assert any(h["document_id"] == str(document.id) for h in after_grant.json()["hits"])

    listed = await client.get(f"{api}/collections", headers=viewer_headers)
    assert any(c["id"] == str(collection.id) for c in listed.json())

    revoke = await client.delete(f"{api}/permissions/{grant_id}", headers=owner_headers)
    assert revoke.status_code == 204, revoke.text

    eff2 = await client.get(
        f"{api}/permissions/effective",
        headers=viewer_headers,
        params={"resource_type": "collection", "resource_id": str(collection.id)},
    )
    assert eff2.json()["permission"] == "none"

    after_revoke = await client.post(f"{api}/search", headers=viewer_headers, json=query)
    assert after_revoke.status_code == 200
    assert after_revoke.json()["hits"] == []


async def test_editor_grant_allows_the_mutation_that_was_forbidden(
    client, db_session, token_headers, api, ingest_now
) -> None:
    """An editor grant on the collection lets the member add a document."""
    org, owner, collection, _document, viewer = await _setup_private_collection(db_session)
    owner_headers = token_headers(owner.id, org.id)
    viewer_headers = token_headers(viewer.id, org.id)

    grant = await client.post(
        f"{api}/permissions",
        headers=owner_headers,
        json={
            "resource_type": "collection",
            "resource_id": str(collection.id),
            "principal_type": "user",
            "principal_id": str(viewer.id),
            "permission": "editor",
        },
    )
    assert grant.status_code == 201, grant.text

    created = await client.post(
        f"{api}/documents/text",
        headers=viewer_headers,
        json={
            "collection_id": str(collection.id),
            "title": "Now Allowed",
            "content": "A member with an editor grant may add content.",
        },
    )
    assert created.status_code == 201, created.text


async def test_team_restricted_document_agrees_across_route_and_retrieval(
    client, db_session, token_headers, api
) -> None:
    """A document restricted to a team (``visibility=TEAM``) inside an ORG-visible
    collection must be invisible to a non-team org member on BOTH the route (403) and in
    retrieval (no hit) - the two enforcement sites must never disagree - while a member of
    the owning team sees it on both. This pins the leak where retrieval only denied
    PRIVATE docs and let TEAM-restricted chunks reach the whole org.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    team = await factories.create_team(db_session, org=org)
    collection = await factories.create_collection(
        db_session,
        org=org,
        owner=owner,
        owner_team=team,
        visibility=Visibility.ORG,
        default_permission=PermissionLevel.VIEWER,
    )
    document = await factories.create_document(
        db_session,
        org=org,
        collection=collection,
        title="Team Secret",
        content=_SECRET,
        visibility=Visibility.TEAM,
        created_by=owner,
    )

    insider, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.add_user_to_team(db_session, team=team, user=insider)
    outsider, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    query = {"query": "project aurora launch code"}

    out_headers = token_headers(outsider.id, org.id)
    read = await client.get(f"{api}/documents/{document.id}", headers=out_headers)
    assert read.status_code == 403, read.text
    out_search = await client.post(f"{api}/search", headers=out_headers, json=query)
    assert out_search.status_code == 200, out_search.text
    assert all(h["document_id"] != str(document.id) for h in out_search.json()["hits"])

    in_headers = token_headers(insider.id, org.id)
    read_in = await client.get(f"{api}/documents/{document.id}", headers=in_headers)
    assert read_in.status_code == 200, read_in.text
    in_search = await client.post(f"{api}/search", headers=in_headers, json=query)
    assert in_search.status_code == 200, in_search.text
    assert any(h["document_id"] == str(document.id) for h in in_search.json()["hits"])


async def _eff(client, api, headers, collection_id) -> str:
    resp = await client.get(
        f"{api}/permissions/effective",
        headers=headers,
        params={"resource_type": "collection", "resource_id": str(collection_id)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["permission"]


async def _search_doc_ids(client, api, headers) -> set[str]:
    resp = await client.post(f"{api}/search", headers=headers, json=_QUERY)
    assert resp.status_code == 200, resp.text
    return {h["document_id"] for h in resp.json()["hits"]}


async def test_subteam_member_inherits_parent_grant_and_team_visibility(
    client, db_session, token_headers, api
) -> None:
    """A member of only a SUB-team inherits BOTH a grant made to the parent team and a
    parent-owned TEAM-visibility collection, and the route (effective permission + listing)
    agrees with retrieval (/search) on every one. U belongs to the SUB-team only, so it is
    ancestor expansion that must lift the parent's access to U.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    parent = await factories.create_team(db_session, org=org)
    sub = await factories.create_team(db_session, org=org)
    await _set_parent(db_session, child=sub, parent=parent)

    u, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.add_user_to_team(db_session, team=sub, user=u)

    granted, granted_doc = await _viewer_collection_with_doc(
        db_session, org=org, owner=owner, title="Granted to parent"
    )
    await _grant_team_collection(db_session, org=org, team=parent, collection=granted)
    team_vis, team_vis_doc = await _viewer_collection_with_doc(
        db_session,
        org=org,
        owner=owner,
        title="Owned by parent",
        owner_team=parent,
        team_visibility=True,
    )

    headers = token_headers(u.id, org.id)

    assert await _eff(client, api, headers, granted.id) == "viewer"
    assert await _eff(client, api, headers, team_vis.id) == "viewer"
    listed = await client.get(f"{api}/collections", headers=headers)
    listed_ids = {c["id"] for c in listed.json()}
    assert str(granted.id) in listed_ids
    assert str(team_vis.id) in listed_ids

    hit_docs = await _search_doc_ids(client, api, headers)
    assert str(granted_doc.id) in hit_docs
    assert str(team_vis_doc.id) in hit_docs


async def test_parent_member_is_denied_child_only_resources_downward_isolation(
    client, db_session, token_headers, api
) -> None:
    """Downward isolation: a member of only the PARENT team must NOT see a grant or a
    TEAM-visibility collection scoped to the child SUB-team - on the route (effective NONE,
    403 read, absent from listing) AND in retrieval (no hit). A sub-team member is the
    positive control, proving the deny is isolation and not an empty fixture.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    parent = await factories.create_team(db_session, org=org)
    sub = await factories.create_team(db_session, org=org)
    await _set_parent(db_session, child=sub, parent=parent)

    v, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.add_user_to_team(db_session, team=parent, user=v)
    u, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.add_user_to_team(db_session, team=sub, user=u)

    child_grant, child_grant_doc = await _viewer_collection_with_doc(
        db_session, org=org, owner=owner, title="Granted to child"
    )
    await _grant_team_collection(db_session, org=org, team=sub, collection=child_grant)
    child_vis, child_vis_doc = await _viewer_collection_with_doc(
        db_session,
        org=org,
        owner=owner,
        title="Owned by child",
        owner_team=sub,
        team_visibility=True,
    )

    v_headers = token_headers(v.id, org.id)

    for coll in (child_grant, child_vis):
        assert await _eff(client, api, v_headers, coll.id) == "none"
        read = await client.get(f"{api}/collections/{coll.id}", headers=v_headers)
        assert read.status_code == 403, read.text
    listed = await client.get(f"{api}/collections", headers=v_headers)
    v_ids = {c["id"] for c in listed.json()}
    assert str(child_grant.id) not in v_ids
    assert str(child_vis.id) not in v_ids

    v_docs = await _search_doc_ids(client, api, v_headers)
    assert str(child_grant_doc.id) not in v_docs
    assert str(child_vis_doc.id) not in v_docs

    u_headers = token_headers(u.id, org.id)
    assert await _eff(client, api, u_headers, child_grant.id) == "viewer"
    assert await _eff(client, api, u_headers, child_vis.id) == "viewer"
    u_docs = await _search_doc_ids(client, api, u_headers)
    assert str(child_grant_doc.id) in u_docs
    assert str(child_vis_doc.id) in u_docs


async def test_third_org_sees_no_nested_team_resources(
    client, db_session, token_headers, api
) -> None:
    """Cross-tenant isolation: a user in a different org sees none of the nested-team
    resources, on the route (NONE / 404 / absent) and in retrieval (no hit)."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    parent = await factories.create_team(db_session, org=org)
    sub = await factories.create_team(db_session, org=org)
    await _set_parent(db_session, child=sub, parent=parent)
    granted, granted_doc = await _viewer_collection_with_doc(
        db_session, org=org, owner=owner, title="Granted to parent"
    )
    await _grant_team_collection(db_session, org=org, team=parent, collection=granted)

    other_org, _other_owner, _ = await factories.create_org_with_owner(db_session)
    stranger, _ = await factories.add_member(db_session, org=other_org, role=OrgRole.VIEWER)
    s_headers = token_headers(stranger.id, other_org.id)

    assert await _eff(client, api, s_headers, granted.id) == "none"
    read = await client.get(f"{api}/collections/{granted.id}", headers=s_headers)
    assert read.status_code == 404, read.text
    listed = await client.get(f"{api}/collections", headers=s_headers)
    assert all(c["id"] != str(granted.id) for c in listed.json())
    s_docs = await _search_doc_ids(client, api, s_headers)
    assert str(granted_doc.id) not in s_docs


async def test_three_level_nesting_inherits_grandparent_grant(
    client, db_session, token_headers, api
) -> None:
    """Grants flow down every level: with GP -> P -> S, a member of only S inherits a grant
    made to the grandparent GP, agreeing across route and retrieval."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    grandparent = await factories.create_team(db_session, org=org)
    parent = await factories.create_team(db_session, org=org)
    sub = await factories.create_team(db_session, org=org)
    await _set_parent(db_session, child=parent, parent=grandparent)
    await _set_parent(db_session, child=sub, parent=parent)

    w, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    await factories.add_user_to_team(db_session, team=sub, user=w)

    gp_coll, gp_doc = await _viewer_collection_with_doc(
        db_session, org=org, owner=owner, title="Granted to grandparent"
    )
    await _grant_team_collection(db_session, org=org, team=grandparent, collection=gp_coll)

    headers = token_headers(w.id, org.id)
    assert await _eff(client, api, headers, gp_coll.id) == "viewer"
    hit_docs = await _search_doc_ids(client, api, headers)
    assert str(gp_doc.id) in hit_docs


async def test_team_membership_in_another_org_does_not_leak_into_acting_org(
    client, db_session, token_headers, api
) -> None:
    """Org-scope isolation of team membership: a user who is on a team in org B must NOT
    pick up that team's id - nor any of its resources - while acting in org A, and their
    org-A team access is unaffected. This pins the ``user_team_ids`` invariant that every
    query is scoped to ``ctx.org_id`` (a membership in another org must never widen the
    effective team set of the acting org). It is pinned at the engine level - ``user_team_ids``
    called directly - and end-to-end through the route and retrieval.
    """
    org_a, owner_a, _ = await factories.create_org_with_owner(db_session)
    org_b, owner_b, _ = await factories.create_org_with_owner(db_session)

    user = await factories.create_user(db_session)
    await factories.create_membership(db_session, org=org_a, user=user, role=OrgRole.VIEWER)
    await factories.create_membership(db_session, org=org_b, user=user, role=OrgRole.VIEWER)
    team_a = await factories.create_team(db_session, org=org_a)
    team_b = await factories.create_team(db_session, org=org_b)
    await factories.add_user_to_team(db_session, team=team_a, user=user)
    await factories.add_user_to_team(db_session, team=team_b, user=user)

    ctx_a = AuthContext(org_id=org_a.id, org_role=OrgRole.VIEWER, user=user)
    assert await user_team_ids(db_session, ctx_a) == {team_a.id}
    ctx_b = AuthContext(org_id=org_b.id, org_role=OrgRole.VIEWER, user=user)
    assert await user_team_ids(db_session, ctx_b) == {team_b.id}

    coll_a, doc_a = await _viewer_collection_with_doc(
        db_session,
        org=org_a,
        owner=owner_a,
        title="Owned by org A team",
        owner_team=team_a,
        team_visibility=True,
    )
    _coll_b, doc_b = await _viewer_collection_with_doc(
        db_session,
        org=org_b,
        owner=owner_b,
        title="Owned by org B team",
        owner_team=team_b,
        team_visibility=True,
    )

    headers_a = token_headers(user.id, org_a.id)
    assert await _eff(client, api, headers_a, coll_a.id) == "viewer"
    a_docs = await _search_doc_ids(client, api, headers_a)
    assert str(doc_a.id) in a_docs
    assert str(doc_b.id) not in a_docs
