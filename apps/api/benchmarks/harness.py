"""The benchmark harness: materialize a dataset into real rows, then drive the real
retrieval and RAG services against it.

This module never reimplements retrieval, permissions or ingestion. It builds a fresh,
throwaway organization from a :class:`~benchmarks.dataset.BenchmarkDataset` -- teams,
collections (with the right visibility/default-permission), users with org roles + team
memberships + explicit ACL grants, and documents ingested through the SAME pipeline the
product uses (:func:`app.services.ingestion.ingest_document`, so chunking and embedding
are real) -- and then, for each (query, retrieval-config) pair, asks the querying
principal's question through :func:`app.services.retrieval.retrieve` (and optionally
:func:`app.services.rag.answer`). The permission scope is therefore the ONLY thing that
limits which chunks a principal can retrieve: the harness never passes an explicit
collection filter, so a chunk a principal cannot see can only be excluded by the real
permission engine. That is what makes the leakage check in the report meaningful.

Provider mode ("offline" vs "live") is reported, not chosen here: it mirrors the LLM
layer's own decision for an org with no connector, so a run is always labelled with the
embedding/completion backend that actually produced its numbers.

The structural builders below deliberately mirror the patterns in ``tests/factories.py``
(commit-through, per-object rows) rather than importing them, so the benchmark package
stays self-contained and does not depend on the test tree.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.storage as storage_mod
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.models.access import AccessGrant
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    MembershipStatus,
    OrgRole,
    PermissionLevel,
    PlanTier,
    PrincipalType,
    ResourceType,
    SourceType,
    Visibility,
)
from app.models.organization import Organization
from app.models.team import Team, TeamMember
from app.models.user import Membership, User
from app.services import rag
from app.services.ingestion import ingest_document
from app.services.retrieval import retrieve
from app.services.storage import build_storage_key, get_storage
from benchmarks.dataset import BenchmarkDataset, DocumentSpec

logger = get_logger(__name__)

# Dataset string values -> the real domain enums. Kept as explicit tables so an unknown
# value fails loudly (KeyError) instead of silently mislabelling a collection's reach.
_VISIBILITY: dict[str, Visibility] = {
    "private": Visibility.PRIVATE,
    "team": Visibility.TEAM,
    "org": Visibility.ORG,
    "public": Visibility.PUBLIC,
}
_DEFAULT_PERMISSION: dict[str, PermissionLevel] = {
    "none": PermissionLevel.NONE,
    "viewer": PermissionLevel.VIEWER,
    "editor": PermissionLevel.EDITOR,
    "manager": PermissionLevel.MANAGER,
}
_ORG_ROLE: dict[str, OrgRole] = {
    "owner": OrgRole.OWNER,
    "admin": OrgRole.ADMIN,
    "editor": OrgRole.EDITOR,
    "viewer": OrgRole.VIEWER,
}
_GRANT_PERMISSION: dict[str, PermissionLevel] = {
    "viewer": PermissionLevel.VIEWER,
    "editor": PermissionLevel.EDITOR,
    "manager": PermissionLevel.MANAGER,
}


@dataclass
class RetrievalConfig:
    """A retrieval variant to benchmark: a label plus the ``retrieve`` knobs it drives."""

    key: str
    hybrid: bool
    top_k: int


@dataclass
class QueryRun:
    """The faithful result of running one query under one retrieval config."""

    query_id: str
    config_key: str
    principal_key: str
    ranked_doc_ids: list[str]
    retrieved_chunk_ids: list[str]
    citations: list[str] = field(default_factory=list)
    answer: str | None = None
    total_latency_ms: float = 0.0


@dataclass
class BenchmarkRun:
    """Everything a scorer/report needs: the runs plus the ground-truth visibility map."""

    dataset_name: str
    provider_mode: str
    configs: list[RetrievalConfig]
    runs: list[QueryRun]
    visible_doc_ids: dict[str, list[str]]


@dataclass
class Handles:
    """Live handles to the materialized benchmark org, returned by :func:`setup_dataset`.

    ``doc_map`` maps each dataset ``doc_id`` to the database ``Document.id`` its content
    was ingested into; ``users``/``roles`` carry the real user + org role per principal
    key so a session-style :class:`AuthContext` can be rebuilt for retrieval.
    """

    org: Organization
    doc_map: dict[str, uuid.UUID] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    roles: dict[str, OrgRole] = field(default_factory=dict)
    collections: dict[str, Collection] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)


def _slug(value: str) -> str:
    """A collision-free slug: a sanitized base plus a short random suffix."""
    base = "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-") or "x"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _provider_mode() -> str:
    """Report whether the deterministic offline stub or a live provider backs the run.

    Mirrors the LLM client's own offline decision for an org with no connector: the stub
    is active when ``EMBEDDING_PROVIDER`` forces it or no platform key is configured. A
    fresh benchmark org configures no connector, so this is exactly what its embeddings
    and completions resolve to.
    """
    offline = settings.EMBEDDING_PROVIDER in ("fake", "offline") or not settings.OPENAI_API_KEY
    return "offline" if offline else "live"


def _use_temp_storage() -> None:
    """Point blob storage at a fresh writable temp directory for this run.

    ``ingest_document`` reads each document's bytes back from the storage backend, so the
    harness needs a working, writable one. Rooting the real ``LocalStorage`` at a throwaway
    temp dir keeps every run hermetic and sidesteps the configured ``/data`` path (which is
    often not writable outside the container).
    """
    tmp = tempfile.mkdtemp(prefix="tb-benchmark-storage-")
    storage_mod._storage = storage_mod.LocalStorage(tmp)


async def _reset_org(db: AsyncSession, ds: BenchmarkDataset, org_name: str) -> None:
    """Idempotency: drop any prior benchmark org (cascade) and its principal users.

    Every org-scoped table has an ``ON DELETE CASCADE`` back to ``organizations``, so a
    single delete of the org row clears its teams, collections, documents, chunks, grants,
    memberships and usage. Users are global (email is unique), so the declared principal
    emails are deleted separately -- otherwise recreating them would collide.
    """
    await db.execute(delete(Organization).where(Organization.name == org_name))
    emails = [p.email.lower() for p in ds.principals]
    if emails:
        await db.execute(delete(User).where(User.email.in_(emails)))
    await db.commit()


async def _ingest_document_spec(
    db: AsyncSession, org: Organization, collection: Collection, spec: DocumentSpec
) -> uuid.UUID:
    """Persist one document + its bytes, then drive it through real ingestion.

    Returns the database ``Document.id``. Raises when ingestion does not reach
    ``indexed`` so a broken corpus fails the run loudly instead of silently scoring zero.
    """
    data = spec.content.encode("utf-8")
    document = Document(
        org_id=org.id,
        collection_id=collection.id,
        title=spec.title[:1024],
        source_type=SourceType.TEXT,
        mime_type="text/plain",
        status=DocumentStatus.PENDING,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
    )
    db.add(document)
    await db.flush()
    doc_uuid = document.id

    key = build_storage_key(org.id, doc_uuid, f"{spec.doc_id}.txt")
    await get_storage().save(key, data, "text/plain")
    document.storage_key = key
    await db.commit()

    status = await ingest_document(db, doc_uuid)
    if status != DocumentStatus.INDEXED.value:
        raise RuntimeError(f"ingestion for document {spec.doc_id!r} ended in status {status!r}")
    return doc_uuid


async def setup_dataset(db: AsyncSession, ds: BenchmarkDataset) -> Handles:
    """Materialize ``ds`` into a fresh, idempotent ``benchmark-<name>`` organization.

    Creates the org, teams, collections (mapped to the real visibility/permission enums),
    principal users with memberships + team links + ACL grants, then ingests every document
    through the real pipeline. Returns :class:`Handles`, including the dataset-id ->
    database-id document map retrieval results are resolved against.
    """
    _use_temp_storage()
    org_name = f"benchmark-{ds.name}"
    await _reset_org(db, ds, org_name)

    org = Organization(name=org_name, slug=_slug(org_name), plan=PlanTier.FREE)
    db.add(org)
    await db.flush()

    teams: dict[str, Team] = {}
    for team_spec in ds.teams:
        team = Team(org_id=org.id, name=team_spec["name"], slug=_slug(team_spec["key"]))
        db.add(team)
        teams[team_spec["key"]] = team

    collections: dict[str, Collection] = {}
    for cspec in ds.collections:
        owner_team = teams.get(cspec.team_key) if cspec.team_key else None
        collection = Collection(
            org_id=org.id,
            owner_team_id=owner_team.id if owner_team else None,
            name=cspec.name,
            slug=_slug(cspec.key),
            visibility=_VISIBILITY[cspec.visibility],
            default_permission=_DEFAULT_PERMISSION[cspec.default_permission],
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_dim=settings.EMBEDDING_DIM,
        )
        db.add(collection)
        collections[cspec.key] = collection
    await db.flush()

    users: dict[str, User] = {}
    roles: dict[str, OrgRole] = {}
    for principal in ds.principals:
        user = User(email=principal.email.lower(), full_name=principal.key, is_active=True)
        db.add(user)
        await db.flush()
        role = _ORG_ROLE[principal.role]
        db.add(
            Membership(org_id=org.id, user_id=user.id, role=role, status=MembershipStatus.ACTIVE)
        )
        for team_key in principal.team_keys:
            db.add(TeamMember(team_id=teams[team_key].id, user_id=user.id))
        users[principal.key] = user
        roles[principal.key] = role

    for principal in ds.principals:
        for grant in principal.grants:
            collection = collections[grant["collection_key"]]
            db.add(
                AccessGrant(
                    org_id=org.id,
                    resource_type=ResourceType.COLLECTION,
                    resource_id=collection.id,
                    principal_type=PrincipalType.USER,
                    principal_id=users[principal.key].id,
                    permission=_GRANT_PERMISSION[grant["permission"]],
                )
            )
    await db.commit()

    doc_map: dict[str, uuid.UUID] = {}
    for spec in ds.documents:
        doc_map[spec.doc_id] = await _ingest_document_spec(
            db, org, collections[spec.collection_key], spec
        )

    logger.info(
        "benchmark_setup",
        org=org_name,
        teams=len(teams),
        collections=len(collections),
        principals=len(users),
        documents=len(doc_map),
        provider_mode=_provider_mode(),
    )
    return Handles(
        org=org, doc_map=doc_map, users=users, roles=roles, collections=collections, teams=teams
    )


def _rank_docs(hits, id_to_dataset: dict[uuid.UUID, str]) -> list[str]:
    """Map ranked chunk hits to dataset doc ids, dedup keeping first occurrence.

    A chunk whose document is NOT part of the dataset must never occur - retrieval is scoped
    to the benchmark org - so it can only mean an org/tenant-isolation regression leaked a
    foreign chunk. Surface it as a synthetic ``unknown:<uuid>`` id (which no principal can
    "see") so the leakage gate counts it and the run fails, instead of silently dropping it.
    """
    ranked: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        doc_id = id_to_dataset.get(hit.document_id) or f"unknown:{hit.document_id}"
        if doc_id in seen:
            continue
        seen.add(doc_id)
        ranked.append(doc_id)
    return ranked


async def _execute(
    db: AsyncSession,
    ds: BenchmarkDataset,
    handles: Handles,
    configs: list[RetrievalConfig],
    *,
    with_answers: bool,
) -> BenchmarkRun:
    """Run every (query, config) pair against the materialized org and collect the results."""
    id_to_dataset = {db_id: doc_id for doc_id, db_id in handles.doc_map.items()}
    runs: list[QueryRun] = []

    for config in configs:
        for query in ds.queries:
            ctx = AuthContext(
                org_id=handles.org.id,
                org_role=handles.roles[query.principal_key],
                user=handles.users[query.principal_key],
                scopes=["*"],
            )

            started = time.perf_counter()
            hits = await retrieve(db, ctx, query.text, top_k=config.top_k, hybrid=config.hybrid)
            latency_ms = (time.perf_counter() - started) * 1000

            answer_text: str | None = None
            citations: list[str] = []
            if with_answers:
                answer_text, cited = await rag.answer(
                    db, ctx, query.text, top_k=config.top_k, hybrid=config.hybrid
                )
                citations = [str(hit.chunk_id) for hit in cited]

            # Retrieval + answer only flush usage records; the caller owns the commit.
            await db.commit()

            runs.append(
                QueryRun(
                    query_id=query.query_id,
                    config_key=config.key,
                    principal_key=query.principal_key,
                    ranked_doc_ids=_rank_docs(hits, id_to_dataset),
                    retrieved_chunk_ids=[str(hit.chunk_id) for hit in hits],
                    citations=citations,
                    answer=answer_text,
                    total_latency_ms=latency_ms,
                )
            )

    visible_doc_ids = {p.key: sorted(ds.visible_doc_ids(p.key)) for p in ds.principals}
    provider_mode = _provider_mode()
    logger.info(
        "benchmark_run",
        dataset=ds.name,
        provider_mode=provider_mode,
        configs=len(configs),
        queries=len(ds.queries),
        with_answers=with_answers,
        runs=len(runs),
    )
    return BenchmarkRun(
        dataset_name=ds.name,
        provider_mode=provider_mode,
        configs=configs,
        runs=runs,
        visible_doc_ids=visible_doc_ids,
    )


async def run(
    ds: BenchmarkDataset, configs: list[RetrievalConfig], *, with_answers: bool
) -> BenchmarkRun:
    """Materialize ``ds`` and run every ``config`` over its queries, returning a
    :class:`BenchmarkRun`.

    Opens its own session, so it is a complete end-to-end driver: set up the benchmark org,
    ask each principal's query through the real retrieval (and, when ``with_answers``, the
    real RAG answer), and record faithful per-run data for the metrics/scorers to consume.
    """
    async with SessionLocal() as db:
        handles = await setup_dataset(db, ds)
        return await _execute(db, ds, handles, configs, with_answers=with_answers)
