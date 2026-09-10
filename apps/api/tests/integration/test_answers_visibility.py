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
    """The fixture set walks every branch of the rule, in list order.

    Answers 0-3 are collection-scoped, so reachability comes from the collection (private,
    org-wide, team-owned, explicitly granted) and not from the answer itself. Answer 4 lives
    inside an invisible collection but is authored by the non-privileged viewer: the "your own
    answer" branch must win over the collection being unreachable. Answers 5-7 are org-level
    and governed by the answer's own visibility (org, public, private). Answer 8 is org-level
    PRIVATE authored by the viewer - again the authorship branch.

    The per-id assertions at the end guard against the two implementations agreeing because
    both are trivially wrong. For the viewer: 0 is someone else's answer in a private
    collection, 1 is an org-visible collection, 2 is the team collection they are on, 3 is
    granted to the editor rather than to them, 4 is their own answer in an unreachable
    collection, 7 is org-level PRIVATE by someone else, and 8 is org-level PRIVATE but their
    own. For the editor: 3 is the explicit collection grant, and 2 is the team collection they
    are not on.
    """
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
        await _make_answer(db_session, org=org, collection=private, creator=owner),
        await _make_answer(db_session, org=org, collection=org_wide, creator=owner),
        await _make_answer(db_session, org=org, collection=team_owned, creator=owner),
        await _make_answer(db_session, org=org, collection=granted, creator=owner),
        await _make_answer(db_session, org=org, collection=private, creator=viewer),
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.ORG
        ),
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.PUBLIC
        ),
        await _make_answer(
            db_session, org=org, collection=None, creator=owner, visibility=Visibility.PRIVATE
        ),
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

    owner_ctx = principals["owner (admin)"]
    viewer_ctx = principals["viewer (in team)"]
    assert await _sql_visible(db_session, owner_ctx) == {a.id for a in answers}

    viewer_visible = await _sql_visible(db_session, viewer_ctx)
    assert answers[0].id not in viewer_visible
    assert answers[1].id in viewer_visible
    assert answers[2].id in viewer_visible
    assert answers[3].id not in viewer_visible
    assert answers[4].id in viewer_visible
    assert answers[7].id not in viewer_visible
    assert answers[8].id in viewer_visible

    editor_visible = await _sql_visible(db_session, principals["editor"])
    assert answers[3].id in editor_visible
    assert answers[2].id not in editor_visible
