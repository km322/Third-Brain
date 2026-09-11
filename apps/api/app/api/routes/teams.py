"""Team CRUD, membership and nested-team hierarchy.

Teams live inside an organization and act as ACL principals. Reading is open to any
authenticated member of the org. *Creating* a team requires at least the ``editor`` org
role; creating a nested team additionally requires management authority over the parent
(see :func:`can_admin_team`). Every other mutation - rename/re-parent, add/remove members,
set a member's role, create sub-teams, delete - is gated by :func:`can_admin_team`:

    the caller is an org owner/admin, OR a **lead** of the team, OR a **lead** of any
    ANCESTOR of the team (a lead of a parent team administers the whole subtree).

This is *management* authority only. It never confers document visibility - that stays in
:mod:`app.services.permissions`, and granting a team access to a resource still goes through
the ``/permissions`` route and its "manager on the resource" rule. Every query is scoped to
``ctx.org_id`` so teams never leak across organizations.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context, require_role
from app.core.logging import get_logger
from app.models.access import AccessGrant
from app.models.enums import OrgRole, PrincipalType, TeamRole
from app.models.team import Team, TeamMember
from app.models.user import Membership
from app.schemas.team import (
    TeamCreate,
    TeamDetail,
    TeamMemberAdd,
    TeamMemberRoleUpdate,
    TeamRead,
    TeamUpdate,
)

router = APIRouter(prefix="/teams", tags=["teams"])

logger = get_logger(__name__)

MAX_TEAM_DEPTH = 6
"""The maximum number of levels (root inclusive) a team tree may reach."""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "team"


async def _unique_slug(db: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    """Derive a slug from ``name`` that is unique within the organization."""
    base = _slugify(name)
    existing = set(
        (await db.execute(select(Team.slug).where(Team.org_id == org_id))).scalars().all()
    )
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


async def _member_counts(db: AsyncSession, team_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not team_ids:
        return {}
    rows = await db.execute(
        select(TeamMember.team_id, func.count())
        .where(TeamMember.team_id.in_(team_ids))
        .group_by(TeamMember.team_id)
    )
    return dict(rows.all())


async def _lead_count(db: AsyncSession, team_id: uuid.UUID) -> int:
    """Number of members holding the ``lead`` role on ``team_id``."""
    return (
        await db.execute(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team_id, TeamMember.role == TeamRole.LEAD)
        )
    ).scalar_one()


async def _get_team(
    db: AsyncSession, ctx: AuthContext, team_id: uuid.UUID, *, with_members: bool = False
) -> Team:
    stmt = select(Team).where(Team.id == team_id, Team.org_id == ctx.org_id)
    if with_members:
        stmt = stmt.options(selectinload(Team.members).selectinload(TeamMember.user))
    team = (await db.execute(stmt)).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _load_tree(
    db: AsyncSession, ctx: AuthContext
) -> tuple[dict[uuid.UUID, uuid.UUID | None], dict[uuid.UUID, list[uuid.UUID]]]:
    """Return ``(parent_of, children_of)`` for every team in the caller's org."""
    rows = (
        await db.execute(select(Team.id, Team.parent_team_id).where(Team.org_id == ctx.org_id))
    ).all()
    parent_of: dict[uuid.UUID, uuid.UUID | None] = {}
    children_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for tid, pid in rows:
        parent_of[tid] = pid
        if pid is not None:
            children_of[pid].append(tid)
    return parent_of, children_of


def _depth(team_id: uuid.UUID, parent_of: dict[uuid.UUID, uuid.UUID | None]) -> int:
    """Levels from the root down to ``team_id`` inclusive (a root team is depth 1)."""
    depth = 0
    current: uuid.UUID | None = team_id
    visited: set[uuid.UUID] = set()
    while current is not None and current not in visited:
        visited.add(current)
        depth += 1
        current = parent_of.get(current)
    return depth


def _descendants(
    team_id: uuid.UUID, children_of: dict[uuid.UUID, list[uuid.UUID]]
) -> set[uuid.UUID]:
    """Every transitive descendant of ``team_id`` (excluding itself). Cycle-safe."""
    result: set[uuid.UUID] = set()
    stack = list(children_of.get(team_id, []))
    while stack:
        node = stack.pop()
        if node in result or node == team_id:
            continue
        result.add(node)
        stack.extend(children_of.get(node, []))
    return result


def _subtree_height(team_id: uuid.UUID, children_of: dict[uuid.UUID, list[uuid.UUID]]) -> int:
    """Number of levels in the subtree rooted at ``team_id`` (a leaf has height 1)."""
    height = 0
    frontier = [team_id]
    visited: set[uuid.UUID] = set()
    while frontier:
        frontier = [n for n in frontier if n not in visited]
        if not frontier:
            break
        height += 1
        visited.update(frontier)
        nxt: list[uuid.UUID] = []
        for node in frontier:
            nxt.extend(children_of.get(node, []))
        frontier = nxt
    return height


async def can_admin_team(db: AsyncSession, ctx: AuthContext, team_id: uuid.UUID) -> bool:
    """Whether the caller may *manage* ``team_id`` (rename/re-parent/members/sub-teams).

    True for org owners/admins, for a **lead** of the team itself, and for a **lead** of any
    ANCESTOR of the team - so a lead of a parent team administers the entire subtree beneath
    it. This is management authority only; it deliberately does NOT grant document
    visibility (that lives in :mod:`app.services.permissions`).
    """
    if ctx.is_admin:
        return True
    if ctx.user_id is None:
        return False
    lead_ids = set(
        (
            await db.execute(
                select(TeamMember.team_id)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    TeamMember.user_id == ctx.user_id,
                    TeamMember.role == TeamRole.LEAD,
                    Team.org_id == ctx.org_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not lead_ids:
        return False
    parent_of, _ = await _load_tree(db, ctx)
    current: uuid.UUID | None = team_id
    visited: set[uuid.UUID] = set()
    while current is not None and current not in visited:
        if current in lead_ids:
            return True
        visited.add(current)
        current = parent_of.get(current)
    return False


async def _require_can_admin(db: AsyncSession, ctx: AuthContext, team_id: uuid.UUID) -> None:
    """403 unless the caller may manage ``team_id`` (see :func:`can_admin_team`).

    A denial is logged with the team only - org_id/user_id are already bound on the log
    context by auth.
    """
    if not await can_admin_team(db, ctx, team_id):
        logger.warning("permission_denied", check="team_admin", team_id=str(team_id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires org admin or team lead to manage this team",
        )


async def _validate_parent(db: AsyncSession, ctx: AuthContext, parent_team_id: uuid.UUID) -> Team:
    """Return the parent team, or 400 if it is not a team in the caller's org."""
    parent = (
        await db.execute(select(Team).where(Team.id == parent_team_id, Team.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=400, detail="Parent team not found in this organization")
    return parent


def _to_read(team: Team, count: int) -> TeamRead:
    return TeamRead(
        id=team.id,
        org_id=team.org_id,
        name=team.name,
        slug=team.slug,
        description=team.description,
        parent_team_id=team.parent_team_id,
        member_count=count,
    )


async def _to_detail(db: AsyncSession, ctx: AuthContext, team: Team) -> TeamDetail:
    """Build a detail response from a team with its ``members`` relationship loaded."""
    children = (
        (
            await db.execute(
                select(Team)
                .where(Team.org_id == ctx.org_id, Team.parent_team_id == team.id)
                .order_by(Team.name)
            )
        )
        .scalars()
        .all()
    )
    counts = await _member_counts(db, [c.id for c in children])
    return TeamDetail(
        id=team.id,
        org_id=team.org_id,
        name=team.name,
        slug=team.slug,
        description=team.description,
        parent_team_id=team.parent_team_id,
        member_count=len(team.members),
        members=team.members,
        children=[_to_read(c, counts.get(c.id, 0)) for c in children],
    )


@router.get("", response_model=list[TeamRead])
async def list_teams(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[TeamRead]:
    """List all teams in the caller's organization."""
    teams = (
        (await db.execute(select(Team).where(Team.org_id == ctx.org_id).order_by(Team.name)))
        .scalars()
        .all()
    )
    counts = await _member_counts(db, [t.id for t in teams])
    return [_to_read(t, counts.get(t.id, 0)) for t in teams]


@router.post("", response_model=TeamRead, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreate,
    ctx: AuthContext = Depends(require_role(OrgRole.EDITOR)),
    db: AsyncSession = Depends(get_db),
) -> TeamRead:
    """Create a team. Requires the ``editor`` org role or higher.

    When ``parent_team_id`` is set the caller must additionally be able to manage the parent
    (:func:`can_admin_team`), and the resulting tree must stay within ``MAX_TEAM_DEPTH``. The
    creator is enrolled as the team's first ``lead``.

    \f

    Nested creation first serializes against concurrent re-parents in this org (the same lock
    ``update_team`` takes) so the depth check validates against a stable tree - otherwise a
    create and a re-parent can interleave and together exceed ``MAX_TEAM_DEPTH``.
    """
    if payload.parent_team_id is not None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"team-reparent:{ctx.org_id}"},
        )
        parent = await _validate_parent(db, ctx, payload.parent_team_id)
        await _require_can_admin(db, ctx, parent.id)
        parent_of, _ = await _load_tree(db, ctx)
        if _depth(parent.id, parent_of) + 1 > MAX_TEAM_DEPTH:
            raise HTTPException(
                status_code=400,
                detail=f"Team nesting may not exceed {MAX_TEAM_DEPTH} levels",
            )

    team = Team(
        org_id=ctx.org_id,
        name=payload.name,
        slug=await _unique_slug(db, ctx.org_id, payload.name),
        description=payload.description,
        parent_team_id=payload.parent_team_id,
    )
    db.add(team)
    await db.flush()

    member_count = 0
    if ctx.user_id is not None:
        db.add(TeamMember(team_id=team.id, user_id=ctx.user_id, role=TeamRole.LEAD))
        member_count = 1
    await db.commit()
    await db.refresh(team)
    return _to_read(team, member_count)


@router.get("/{team_id}", response_model=TeamDetail)
async def get_team(
    team_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> TeamDetail:
    """Fetch a single team with its full membership roster and direct sub-teams."""
    team = await _get_team(db, ctx, team_id, with_members=True)
    return await _to_detail(db, ctx, team)


@router.patch("/{team_id}", response_model=TeamRead)
async def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdate,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> TeamRead:
    """Rename/re-describe a team and optionally re-parent it. Requires team-admin rights.

    Re-parenting validates same-org membership, rejects a cycle (the new parent may not be
    the team itself or any of its descendants) and keeps the moved subtree within
    ``MAX_TEAM_DEPTH``. Sending ``parent_team_id: null`` re-parents the team to the root.

    An unchanged ``parent_team_id`` is not an actual move, so the destination-admin / cycle
    / depth checks are skipped for it - a sub-team lead who is not an admin of the parent
    can still rename/edit their own team.

    \f

    Treating an unchanged parent as a no-op matters because the dashboard always sends the
    current parent on a rename, so requiring destination-admin there would block a sub-team
    lead from editing their own team.

    A real move first serializes concurrent re-parents in this org so the cycle/depth guard
    validates against a stable tree. Without it two moves could interleave between reading
    the tree and committing and together form a cycle. The transaction-scoped advisory lock
    auto-releases at commit. Grafting under a parent inherits its downward-flowing grants, so
    the caller must also manage the destination - not just the team being moved.
    """
    team = await _get_team(db, ctx, team_id)
    await _require_can_admin(db, ctx, team.id)

    if payload.name is not None:
        team.name = payload.name
    if payload.description is not None:
        team.description = payload.description

    if "parent_team_id" in payload.model_fields_set:
        new_parent_id = payload.parent_team_id
        if new_parent_id == team.parent_team_id:
            pass
        elif new_parent_id is None:
            team.parent_team_id = None
        else:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"team-reparent:{ctx.org_id}"},
            )
            parent = await _validate_parent(db, ctx, new_parent_id)
            await _require_can_admin(db, ctx, parent.id)
            parent_of, children_of = await _load_tree(db, ctx)
            if parent.id == team.id or parent.id in _descendants(team.id, children_of):
                raise HTTPException(
                    status_code=409,
                    detail="A team cannot be re-parented under itself or a descendant",
                )
            deepest = _depth(parent.id, parent_of) + _subtree_height(team.id, children_of)
            if deepest > MAX_TEAM_DEPTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Team nesting may not exceed {MAX_TEAM_DEPTH} levels",
                )
            team.parent_team_id = parent.id

    await db.commit()
    await db.refresh(team)
    counts = await _member_counts(db, [team.id])
    return _to_read(team, counts.get(team.id, 0))


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a team. Requires team-admin rights.

    Direct sub-teams are re-parented to the root (their ``parent_team_id`` becomes ``null``)
    rather than cascade-deleted, so a team's sub-groups survive its removal.

    \f

    The detach is
    issued explicitly - the ``ON DELETE SET NULL`` FK also guarantees it - so the intent is
    clear and independent of the database's behavior. ``TeamMember`` rows cascade via FK;
    grants where the team is the ACL principal have no FK, so purge them explicitly to avoid
    stranded ACL rows.
    """
    team = await _get_team(db, ctx, team_id)
    await _require_can_admin(db, ctx, team.id)

    await db.execute(
        update(Team)
        .where(Team.org_id == ctx.org_id, Team.parent_team_id == team.id)
        .values(parent_team_id=None)
    )
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.org_id == ctx.org_id,
            AccessGrant.principal_type == PrincipalType.TEAM,
            AccessGrant.principal_id == team.id,
        )
    )
    await db.delete(team)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{team_id}/members", response_model=TeamDetail)
async def add_team_member(
    team_id: uuid.UUID,
    payload: TeamMemberAdd,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> TeamDetail:
    """Add an organization member to a team (idempotent). Requires team-admin rights.

    Team membership confers the team's grants, so this is gated by :func:`can_admin_team`
    rather than being self-service. ``role`` defaults to ``member``. The user must belong to
    this organization.
    """
    team = await _get_team(db, ctx, team_id)
    await _require_can_admin(db, ctx, team.id)

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.org_id == ctx.org_id,
                Membership.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=400, detail="User is not a member of this organization")

    existing = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(TeamMember(team_id=team_id, user_id=payload.user_id, role=payload.role))
        await db.commit()

    team = await _get_team(db, ctx, team_id, with_members=True)
    return await _to_detail(db, ctx, team)


@router.patch("/{team_id}/members/{user_id}", response_model=TeamDetail)
async def set_team_member_role(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: TeamMemberRoleUpdate,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> TeamDetail:
    """Set a member's role within a team (``lead``/``member``). Requires team-admin rights.

    \f

    Demoting the last lead is refused. That guard is serialized on a per-team advisory lock so
    two concurrent demotes/removals can't each observe ">1 lead" and together strip the team
    of every lead.
    """
    team = await _get_team(db, ctx, team_id)
    await _require_can_admin(db, ctx, team.id)

    member = (
        await db.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="User is not a member of this team")
    if member.role == TeamRole.LEAD and payload.role != TeamRole.LEAD and not ctx.is_admin:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"team-leads:{team_id}"},
        )
        if await _lead_count(db, team_id) <= 1:
            raise HTTPException(status_code=409, detail="A team must keep at least one lead")
    member.role = payload.role
    await db.commit()

    team = await _get_team(db, ctx, team_id, with_members=True)
    return await _to_detail(db, ctx, team)


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove a user from a team. Requires team-admin rights (see ``add_team_member``).

    Removing the last lead is refused.

    \f

    That guard runs under the same per-team advisory lock :func:`set_team_member_role`
    takes, so concurrent demote/remove requests can't both slip past it and leave the team
    lead-less.
    """
    team = await _get_team(db, ctx, team_id)
    await _require_can_admin(db, ctx, team.id)
    member = (
        await db.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="User is not a member of this team")
    if member.role == TeamRole.LEAD and not ctx.is_admin:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"team-leads:{team_id}"},
        )
        if await _lead_count(db, team_id) <= 1:
            raise HTTPException(status_code=409, detail="A team must keep at least one lead")
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
