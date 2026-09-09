"""Integration: the SQL answer filter and ``can_read_answer`` must never disagree.

``list_answers`` pushes the visibility decision into SQL via
``answer_visibility_filters``; ``get_answer`` still grades a single row with
``can_read_answer``. Those are two enforcement sites for one rule, so this module is a
differential test: for a matrix of principals over a fixture set covering every branch of
the rule, the rows SQL selects must equal the rows ``can_read_answer`` admits.

Written as an equality rather than a list of expected ids on purpose - it pins the two
implementations to each other no matter which one someone edits later.
"""

from __future__ import annotations

import factories
import pytest
from sqlalchemy import select

from app.core.deps import AuthContext
from app.models.answer import Answer
from app.models.enums import OrgRole, PermissionLevel, ResourceType, Visibility
from app.services.answers import answer_visibility_filters, can_read_answer
from app.services.permissions import build_retrieval_scope

pytestmark = pytest.mark.integration


async def _sql_visible(db, ctx) -> set:
    """The ids ``list_answers`` would return for ``ctx``."""
    scope = await build_retrieval_scope(db, ctx)
    rows = (
        (await db.execute(select(Answer).where(*answer_visibility_filters(ctx, scope))))
        .scalars()
        .all()
    )
    return {a.id for a in rows}


async def _python_visible(db, ctx, answers) -> set:
    """The ids ``can_read_answer`` admits for ``ctx``."""
    visible = set()
    for answer in answers:
        if await can_read_answer(db, ctx, answer):
            visible.add(answer.id)
    return visible


async def _make_answer(db, *, org, collection, creator, visibility=Visibility.PRIVATE) -> Answer:
    answer = Answer(
        org_id=org.id,
        collection_id=collection.id if collection is not None else None,
        created_by_id=creator.id if creator is not None else None,
        question=f"Q {collection.name if collection else 'org'} {visibility.value}",
        answer="A",
        visibility=visibility,
    )
    db.add(answer)
    await db.flush()
    return answer


async def test_sql_filter_matches_can_read_answer_across_principals(db_session) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    editor, _ = await factories.add_member(db_session, org=org, role=OrgRole.EDITOR)
    viewer, _ = await factories.add_member(db_session, org=org, role=OrgRole.VIEWER)
    team = await factories.create_team(db_session, org=org)
    await factories.add_user_to_team(db_session, team=team, user=viewer)

    private = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE, name="Private"
    )
    org_wide = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.ORG, name="OrgWide"
    )
    team_owned = await factories.create_collection(
        db_session, org=org, owner=owner, owner_team=team, visibility=Visibility.TEAM, name="Team"
    )
    granted = await factories.create_collection(
        db_session, org=org, owner=owner, visibility=Visibility.PRIVATE, name="Granted"
    )
    await factories.grant_user(
        db_session,
        org=org,
        user=editor,
        resource_type=ResourceType.COLLECTION,
        resource_id=granted.id,
        permission=PermissionLevel.VIEWER,
    )

    answers = [
        # Collection-scoped: reachability comes from the collection, not the answer.
        await _make_answer(db_session, org=org, collection=private, creator=owner),
        await _make_answer(db_session, org=org, collection=org_wide, creator=owner),
        await _make_answer(db_session, org=org, collection=team_owned, creator=owner),
        await _make_answer(db_session, org=org, collection=granted, creator=owner),
        # An answer inside an invisible collection, authored by the non-privileged user:
        # the "your own answer" branch must win over the collection being unreachable.
        await _make_answer(db_session, org=org, collection=private, creator=viewer),
        # Org-level: governed by the answer's own visibility.
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.ORG
        ),
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.PUBLIC
        ),
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.PRIVATE
        ),
        # Org-level PRIVATE authored by the viewer - again the authorship branch.
        await _make_answer(
            db_session, org=org, collection=None, creator=viewer, visibility=Visibility.PRIVATE
        ),
    ]
    await db_session.commit()

    principals = {
        "owner (admin)": AuthContext(org_id=org.id, org_role=OrgRole.OWNER, user=owner),
        "editor": AuthContext(org_id=org.id, org_role=OrgRole.EDITOR, user=editor),
        "viewer (in team)": AuthContext(org_id=org.id, org_role=OrgRole.VIEWER, user=viewer),
        "bare api key": AuthContext(org_id=org.id, org_role=OrgRole.VIEWER, user=None),
    }

    for label, ctx in principals.items():
        sql = await _sql_visible(db_session, ctx)
        python = await _python_visible(db_session, ctx, answers)
        assert sql == python, (
            f"{label}: SQL filter and can_read_answer disagree. "
            f"only-in-SQL={sql - python} only-in-python={python - sql}"
        )

    # Guard against the two agreeing because both are trivially wrong.
    owner_ctx = principals["owner (admin)"]
    viewer_ctx = principals["viewer (in team)"]
    assert await _sql_visible(db_session, owner_ctx) == {a.id for a in answers}

    viewer_visible = await _sql_visible(db_session, viewer_ctx)
    assert answers[0].id not in viewer_visible  # private collection, someone else's answer
    assert answers[1].id in viewer_visible  # org-visible collection
    assert answers[2].id in viewer_visible  # team collection, viewer is on the team
    assert answers[3].id not in viewer_visible  # granted to the editor, not the viewer
    assert answers[4].id in viewer_visible  # own answer in an unreachable collection
    assert answers[7].id not in viewer_visible  # org-level PRIVATE by someone else
    assert answers[8].id in viewer_visible  # org-level PRIVATE, but their own

    editor_visible = await _sql_visible(db_session, principals["editor"])
    assert answers[3].id in editor_visible  # the explicit collection grant
    assert answers[2].id not in editor_visible  # team collection, editor is not on the team
