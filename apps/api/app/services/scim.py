"""SCIM 2.0 helpers: bearer-token auth + resource serialization.

Provider (Okta/Azure AD) provisioning maps onto Third Brain users/teams within the org the
SCIM token belongs to. Deactivation is org-scoped: it suspends the membership, never
globally deletes a user who may belong to other orgs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_api_key
from app.models.sso import ScimToken
from app.models.user import Membership, User

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


async def authenticate_scim(authorization: str | None, db: AsyncSession) -> ScimToken:
    """Resolve the SCIM bearer token to its org, or raise 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing SCIM token")
    raw = authorization.split(" ", 1)[1].strip()
    token = (
        await db.execute(select(ScimToken).where(ScimToken.hashed_token == hash_api_key(raw)))
    ).scalar_one_or_none()
    if token is None or token.revoked:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SCIM token")
    token.last_used_at = datetime.now(UTC)
    return token


def user_to_scim(user: User, membership: Membership) -> dict:
    given, _, family = (user.full_name or "").partition(" ")
    return {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "userName": user.email,
        "name": {"givenName": given, "familyName": family},
        "displayName": user.full_name,
        "emails": [{"value": user.email, "primary": True}],
        "active": membership.status.value == "active" and user.is_active,
        "meta": {"resourceType": "User"},
    }


def group_to_scim(team, member_ids: list[str]) -> dict:
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(team.id),
        "displayName": team.name,
        "members": [{"value": mid} for mid in member_ids],
        "meta": {"resourceType": "Group"},
    }


def list_response(resources: list[dict]) -> dict:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def _scim_bool(value: object) -> bool | None:
    """Coerce a SCIM ``active`` value to a real bool, or None when unparseable.

    SCIM 2.0 defines ``active`` as a JSON boolean, but several IdPs (Azure AD, some Okta
    configs) serialize it as the STRING ``"true"``/``"false"``. ``bool("false")`` is ``True``,
    so the previous truthiness coercion turned a deprovisioning PATCH into a no-op that left an
    offboarded user ACTIVE. Parse strings explicitly and ignore anything we cannot interpret.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def extract_active(patch_body: dict) -> bool | None:
    """Pull the target ``active`` value out of a SCIM PatchOp (or a plain replace body)."""
    for op in patch_body.get("Operations", []):
        if op.get("op", "").lower() != "replace":
            continue
        value = op.get("value")
        path = (op.get("path") or "").lower()
        if path == "active":
            return _scim_bool(value)
        if isinstance(value, dict) and "active" in value:
            return _scim_bool(value["active"])
    return None
