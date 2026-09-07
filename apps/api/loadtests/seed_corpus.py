"""Bulk corpus seeder for load testing permission-scoped retrieval at scale.

Builds a small entity graph (orgs, users, teams, collections with mixed
visibility, access grants, API keys) through the app's own async SQLAlchemy
models, then bulk-loads millions of embedded chunks with psycopg COPY. The
embeddings are byte-identical to the app's offline provider so live search
queries embedded at request time rank against the seeded vectors correctly.

Run from apps/api:

    python -m loadtests.seed_corpus --chunks 1000000 [--orgs 20]
        [--collections-per-org 5] [--chunks-per-doc 8]
        [--manifest loadtests/.manifest.json] [--keep-index]

Must never import locust: the seeder runs with core API dependencies alone.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.security import generate_api_key, hash_password
from app.models.access import AccessGrant
from app.models.api_key import ApiKey
from app.models.collection import Collection
from app.models.enums import (
    DocumentStatus,
    OrgRole,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    SourceType,
    Visibility,
)
from app.models.organization import Organization
from app.models.team import Team, TeamMember
from app.models.user import Membership, User
from app.services.llm.client import _fake_embedding

ORG_SLUG_PREFIX = "loadtest-org-"
USER_EMAIL_DOMAIN = "loadtest.local"
PASSWORD = "loadtest-changeme"

COPY_BATCH_SIZE = 5_000
QUERY_COUNT = 200

# SQLAlchemy persists a non-native Enum by its member *name*, so the raw COPY must write the
# same uppercase form the ORM would ("INDEXED"/"TEXT", not "indexed"/"text"); otherwise the
# seeded rows can never be read back through the ORM or matched by a status filter.
DOC_STATUS_DB_VALUE = DocumentStatus.INDEXED.name
DOC_SOURCE_TYPE_DB_VALUE = SourceType.TEXT.name

HNSW_INDEX_NAME = "ix_document_chunks_embedding_hnsw"
HNSW_CREATE_SQL = (
    f"CREATE INDEX {HNSW_INDEX_NAME} ON document_chunks "
    "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
)


# --------------------------------------------------------------------------- #
# Synthetic vocabulary + chunk text
# --------------------------------------------------------------------------- #

_PREFIXES = [
    "acc",
    "bal",
    "cur",
    "dat",
    "eng",
    "fis",
    "gra",
    "hel",
    "inv",
    "jur",
    "ker",
    "lat",
    "mig",
    "nor",
    "orb",
    "pol",
    "quo",
    "ret",
    "sig",
    "tel",
    "ult",
    "ven",
    "war",
    "xen",
    "yar",
    "zon",
    "arc",
    "bri",
    "cle",
    "dro",
    "eve",
    "fla",
    "gri",
    "hor",
    "ide",
    "jol",
    "kin",
    "lum",
    "mon",
    "nav",
    "ope",
    "pra",
    "qui",
    "riv",
    "sto",
]
_SUFFIXES = [
    "band",
    "cord",
    "dane",
    "erst",
    "fold",
    "gate",
    "hive",
    "iron",
    "junc",
    "kell",
    "line",
    "mont",
    "nest",
    "opal",
    "port",
    "quay",
    "rand",
    "sett",
    "tone",
    "unit",
    "vale",
    "wick",
    "xact",
    "yond",
    "zeal",
    "arch",
    "berg",
    "cast",
    "dorn",
    "edge",
    "ford",
    "grid",
    "helm",
    "isle",
    "jack",
    "keel",
    "lock",
    "mark",
    "node",
    "over",
    "pike",
    "quest",
    "reef",
    "spar",
    "turf",
]

VOCABULARY = [f"{p}{s}" for p in _PREFIXES for s in _SUFFIXES][:2000]


def chunk_content(index: int) -> str:
    """Deterministic keyword-searchable text for the chunk at a global index."""
    rng = random.Random(index)
    n_words = rng.randint(40, 80)
    return " ".join(rng.choice(VOCABULARY) for _ in range(n_words))


def make_queries(count: int = QUERY_COUNT) -> list[str]:
    """Short 2-4 word phrases drawn from the same vocabulary as the chunks."""
    rng = random.Random("loadtest-queries")
    return [
        " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(2, 4))) for _ in range(count)
    ]


# --------------------------------------------------------------------------- #
# Fast composed embedding, byte-identical to the offline provider
# --------------------------------------------------------------------------- #

_token_offsets_cache: dict[str, tuple[int, ...]] = {}


def _token_offsets(token: str) -> tuple[int, ...]:
    """The dim-independent 16-bit hash offsets a token contributes, memoized per token.

    ``_fake_embedding`` maps each offset into the vector with ``% dim``; caching the raw
    offsets (not the post-modulo indices) keeps the memo correct for any ``dim`` while still
    hashing each distinct token only once.
    """
    cached = _token_offsets_cache.get(token)
    if cached is None:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        cached = tuple((h[i] << 8 | h[i + 1]) for i in range(0, len(h), 2))
        _token_offsets_cache[token] = cached
    return cached


def fast_embedding(text: str, dim: int) -> list[float]:
    """Compose memoized per-token contributions; matches ``_fake_embedding`` exactly.

    Each token adds exact +1.0 increments at the same hash-derived indices, in the same
    order, as the reference implementation, and the normalization below is the same
    expression, so the result is byte-identical to the app's offline provider.
    """
    vec = [0.0] * dim
    tokens = text.lower().split() or [text.lower()]
    for tok in tokens:
        for offset in _token_offsets(tok):
            vec[offset % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def self_check_embeddings(dim: int) -> None:
    """Assert byte-identical equality with the app's offline embedding provider."""
    samples = [
        "single",
        "two tokens",
        chunk_content(0),
        chunk_content(123_457),
        "Repeated repeated REPEATED tokens tokens",
    ]
    for text in samples:
        if fast_embedding(text, dim) != _fake_embedding(text, dim):
            raise AssertionError(
                f"fast_embedding diverges from _fake_embedding for text {text[:60]!r}"
            )


def embed_and_serialize(start_index: int, count: int, dim: int) -> list[tuple[str, int, str]]:
    """Worker: (content, token_count, pgvector text literal) for a chunk index range."""
    rows = []
    for index in range(start_index, start_index + count):
        content = chunk_content(index)
        vec = fast_embedding(content, dim)
        vec_text = "[" + ",".join(repr(v) for v in vec) + "]"
        rows.append((content, len(content.split()), vec_text))
    return rows


# --------------------------------------------------------------------------- #
# Entity graph (small, via the app's async models)
# --------------------------------------------------------------------------- #


@dataclass
class SeededOrg:
    name: str
    org_id: uuid.UUID
    member_api_key: str
    admin_api_key: str
    collection_ids: list[uuid.UUID] = field(default_factory=list)


async def _delete_previous_loadtest_data(db) -> None:
    """Delete-and-recreate idempotency: wipe any prior loadtest orgs and users."""
    org_ids = (
        (
            await db.execute(
                select(Organization.id).where(Organization.slug.like(f"{ORG_SLUG_PREFIX}%"))
            )
        )
        .scalars()
        .all()
    )
    if org_ids:
        print(f"Deleting {len(org_ids)} existing loadtest org(s) (cascades to chunks)...")
        await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.execute(delete(User).where(User.email.like(f"%@{USER_EMAIL_DOMAIN}")))
    await db.commit()


async def _seed_org(db, index: int, collections_per_org: int) -> SeededOrg:
    slug = f"{ORG_SLUG_PREFIX}{index:03d}"
    org = Organization(name=f"Loadtest Org {index:03d}", slug=slug)
    db.add(org)
    await db.flush()

    hashed_password = hash_password(PASSWORD)

    def make_user(handle: str) -> User:
        return User(
            email=f"{handle}-{index:03d}@{USER_EMAIL_DOMAIN}",
            hashed_password=hashed_password,
            full_name=f"{handle.title()} {index:03d}",
            is_active=True,
        )

    owner = make_user("owner")
    editor_a = make_user("editor-a")
    editor_b = make_user("editor-b")
    viewer_a = make_user("viewer-a")
    viewer_b = make_user("viewer-b")
    users = [owner, editor_a, editor_b, viewer_a, viewer_b]
    db.add_all(users)
    await db.flush()

    db.add_all(
        [
            Membership(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER),
            Membership(org_id=org.id, user_id=editor_a.id, role=OrgRole.EDITOR),
            Membership(org_id=org.id, user_id=editor_b.id, role=OrgRole.EDITOR),
            Membership(org_id=org.id, user_id=viewer_a.id, role=OrgRole.VIEWER),
            Membership(org_id=org.id, user_id=viewer_b.id, role=OrgRole.VIEWER),
        ]
    )

    team = Team(org_id=org.id, name="Loadtest Team", slug="loadtest-team")
    db.add(team)
    await db.flush()
    db.add_all(
        [
            TeamMember(team_id=team.id, user_id=owner.id),
            TeamMember(team_id=team.id, user_id=editor_a.id),
            TeamMember(team_id=team.id, user_id=viewer_a.id),
        ]
    )

    visibilities = [Visibility.ORG, Visibility.TEAM, Visibility.PRIVATE]
    collection_ids: list[uuid.UUID] = []
    for c in range(collections_per_org):
        visibility = visibilities[c % len(visibilities)]
        collection = Collection(
            org_id=org.id,
            owner_id=owner.id,
            owner_team_id=team.id if visibility == Visibility.TEAM else None,
            name=f"Loadtest Collection {c:02d}",
            slug=f"loadtest-collection-{c:02d}",
            description=f"Synthetic corpus, visibility={visibility.value}.",
            visibility=visibility,
            default_permission=PermissionLevel.VIEWER,
        )
        db.add(collection)
        await db.flush()
        collection_ids.append(collection.id)

        if visibility == Visibility.PRIVATE:
            db.add(
                AccessGrant(
                    org_id=org.id,
                    resource_type=ResourceType.COLLECTION,
                    resource_id=collection.id,
                    principal_type=PrincipalType.USER,
                    principal_id=viewer_b.id,
                    permission=PermissionLevel.VIEWER,
                    granted_by_id=owner.id,
                )
            )
            db.add(
                AccessGrant(
                    org_id=org.id,
                    resource_type=ResourceType.COLLECTION,
                    resource_id=collection.id,
                    principal_type=PrincipalType.TEAM,
                    principal_id=team.id,
                    permission=PermissionLevel.VIEWER,
                    granted_by_id=owner.id,
                )
            )

    admin_key, admin_prefix, admin_hash = generate_api_key()
    member_key, member_prefix, member_hash = generate_api_key()
    db.add_all(
        [
            ApiKey(
                org_id=org.id,
                created_by_id=owner.id,
                acts_as_user_id=owner.id,
                name="Loadtest key (admin)",
                key_prefix=admin_prefix,
                hashed_key=admin_hash,
                scopes=["*"],
                rate_limit_per_minute=1_000_000,
            ),
            ApiKey(
                org_id=org.id,
                created_by_id=owner.id,
                acts_as_user_id=editor_a.id,
                name="Loadtest key (member)",
                key_prefix=member_prefix,
                hashed_key=member_hash,
                # Least-privilege search scope: enough for search/chat/embeddings, which all
                # gate on require_scope("search"), while acts_as editor_a keeps the caller a
                # NON-admin so retrieval exercises the real ACL predicate, not the admin
                # short-circuit.
                scopes=["search"],
                rate_limit_per_minute=1_000_000,
            ),
        ]
    )

    return SeededOrg(
        name=org.name,
        org_id=org.id,
        member_api_key=member_key,
        admin_api_key=admin_key,
        collection_ids=collection_ids,
    )


async def seed_entity_graph(orgs: int, collections_per_org: int) -> list[SeededOrg]:
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        await _delete_previous_loadtest_data(db)
        seeded = [await _seed_org(db, i, collections_per_org) for i in range(orgs)]
        await db.commit()
    return seeded


# --------------------------------------------------------------------------- #
# Bulk documents + chunks via psycopg COPY
# --------------------------------------------------------------------------- #


def _psycopg_dsn() -> str:
    return settings.alembic_database_uri.replace("+psycopg", "")


def _distribute(total: int, buckets: int) -> list[int]:
    base, remainder = divmod(total, buckets)
    return [base + (1 if i < remainder else 0) for i in range(buckets)]


@dataclass
class DocPlan:
    doc_id: uuid.UUID
    org_id: uuid.UUID
    collection_id: uuid.UUID
    chunk_count: int


def plan_documents(
    seeded: list[SeededOrg], total_chunks: int, chunks_per_doc: int
) -> list[DocPlan]:
    plans: list[DocPlan] = []
    for org, org_chunks in zip(seeded, _distribute(total_chunks, len(seeded)), strict=True):
        per_collection = _distribute(org_chunks, len(org.collection_ids))
        for collection_id, coll_chunks in zip(org.collection_ids, per_collection, strict=True):
            remaining = coll_chunks
            while remaining > 0:
                n = min(chunks_per_doc, remaining)
                plans.append(DocPlan(uuid.uuid4(), org.org_id, collection_id, n))
                remaining -= n
    return plans


def insert_documents(conn: psycopg.Connection, plans: list[DocPlan]) -> None:
    now = datetime.now(UTC)
    with conn.cursor() as cur:
        with cur.copy(
            "COPY documents (id, org_id, collection_id, title, source_type, status, "
            "chunk_count, size_bytes, metadata, indexed_at) FROM STDIN"
        ) as copy:
            for i, plan in enumerate(plans):
                copy.write_row(
                    (
                        plan.doc_id,
                        plan.org_id,
                        plan.collection_id,
                        f"Loadtest document {i:07d}",
                        DOC_SOURCE_TYPE_DB_VALUE,
                        DOC_STATUS_DB_VALUE,
                        plan.chunk_count,
                        0,
                        "{}",
                        now,
                    )
                )
    conn.commit()


def copy_chunks(
    conn: psycopg.Connection, plans: list[DocPlan], total_chunks: int, dim: int, workers: int
) -> None:
    """Stream chunk rows in bounded batches: embed in worker processes, COPY in main."""

    def row_targets():
        for plan in plans:
            for chunk_index in range(plan.chunk_count):
                yield plan, chunk_index

    targets = row_targets()
    written = 0
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = []
        next_start = 0

        def submit_next():
            nonlocal next_start
            if next_start < total_chunks:
                n = min(COPY_BATCH_SIZE, total_chunks - next_start)
                futures.append(pool.submit(embed_and_serialize, next_start, n, dim))
                next_start += n

        for _ in range(workers + 1):
            submit_next()

        with conn.cursor() as cur:
            while futures:
                batch = futures.pop(0).result()
                submit_next()
                with cur.copy(
                    "COPY document_chunks (id, org_id, collection_id, document_id, "
                    "chunk_index, content, token_count, embedding, metadata) FROM STDIN"
                ) as copy:
                    for content, token_count, vec_text in batch:
                        plan, chunk_index = next(targets)
                        copy.write_row(
                            (
                                uuid.uuid4(),
                                plan.org_id,
                                plan.collection_id,
                                plan.doc_id,
                                chunk_index,
                                content,
                                token_count,
                                vec_text,
                                "{}",
                            )
                        )
                conn.commit()
                written += len(batch)
                elapsed = time.monotonic() - started
                rate = written / elapsed if elapsed > 0 else 0.0
                eta = (total_chunks - written) / rate if rate > 0 else float("inf")
                print(
                    f"  chunks {written}/{total_chunks} rate={rate:,.0f}/s eta={eta:,.0f}s",
                    flush=True,
                )


def drop_hnsw_index(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {HNSW_INDEX_NAME}")
    conn.commit()


def rebuild_hnsw_index(
    conn: psycopg.Connection, maintenance_mem: str, parallel_workers: int
) -> None:
    """Build the HNSW index.

    The whole graph should fit in ``maintenance_work_mem`` (roughly the raw vector bytes:
    chunks * embedding_dim * 4). If it does not, pgvector spills to disk and the build
    slows by an order of magnitude - which is itself the single-node scaling boundary the
    load test exists to surface (see docs/SCALING.md). Size ``--maintenance-mem`` accordingly.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET maintenance_work_mem = '{maintenance_mem}'")
        cur.execute(f"SET max_parallel_maintenance_workers = {parallel_workers}")
        cur.execute(HNSW_CREATE_SQL)
    conn.commit()


def analyze_chunks(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("ANALYZE document_chunks")
    conn.commit()


def print_sizes(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_table_size('document_chunks'))")
        table_size = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(pg_size_pretty(pg_relation_size(to_regclass(%s))), 'absent')",
            (HNSW_INDEX_NAME,),
        )
        index_size = cur.fetchone()[0]
    print(f"document_chunks table size: {table_size}, hnsw index size: {index_size}")


# --------------------------------------------------------------------------- #
# Manifest + main
# --------------------------------------------------------------------------- #


def write_manifest(path: str, seeded: list[SeededOrg], chunk_count: int, dim: int) -> None:
    manifest = {
        "chunk_count": chunk_count,
        "embedding_dim": dim,
        "orgs": [
            {
                "name": org.name,
                "org_id": str(org.org_id),
                "member_api_key": org.member_api_key,
                "admin_api_key": org.admin_api_key,
                "collection_ids": [str(c) for c in org.collection_ids],
            }
            for org in seeded
        ],
        "queries": make_queries(),
    }
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Manifest written to {path}")


def _parse_mem_to_bytes(value: str) -> int | None:
    """Parse a Postgres memory string like '6GB'/'512MB' into bytes; None if unparseable."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgGtT]?)[bB]?\s*", value)
    if not match:
        return None
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    return int(float(match.group(1)) * scale[match.group(2).lower()])


def _warn_if_build_memory_undersized(
    chunks: int, dim: int, maintenance_mem: str, keep_index: bool
) -> None:
    """Warn loudly when the HNSW graph will not fit in maintenance_work_mem.

    The build holds the whole graph in memory; if it does not fit it spills to disk and
    slows by an order of magnitude (a 1M x 1536 build can go from minutes to ~an hour).
    """
    if keep_index:
        return
    graph_bytes = chunks * dim * 4  # float4 vectors dominate the graph size
    mem_bytes = _parse_mem_to_bytes(maintenance_mem)
    if mem_bytes is not None and graph_bytes > mem_bytes:
        need_gb = graph_bytes / 1024**3
        print(
            f"WARNING: ~{need_gb:.1f} GB of vectors exceeds --maintenance-mem {maintenance_mem}; "
            "the HNSW build will spill to disk and be very slow. Raise --maintenance-mem "
            f"to >= {need_gb:.0f}GB (must fit in RAM) or seed fewer --chunks.",
            file=sys.stderr,
        )


def _stage(timings: list[tuple[str, float]], name: str, started: float) -> None:
    timings.append((name, time.monotonic() - started))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=int, required=True)
    parser.add_argument("--orgs", type=int, default=20)
    parser.add_argument("--collections-per-org", type=int, default=5)
    parser.add_argument("--chunks-per-doc", type=int, default=8)
    parser.add_argument("--manifest", default="loadtests/.manifest.json")
    parser.add_argument("--keep-index", action="store_true")
    parser.add_argument("--workers", type=int, default=None, help="embedding worker processes")
    parser.add_argument(
        "--maintenance-mem",
        default="2GB",
        help="maintenance_work_mem for the HNSW build; size it >= chunks * dim * 4 bytes "
        "so the graph fits in memory (else the build spills to disk and slows sharply)",
    )
    parser.add_argument(
        "--index-parallel-workers",
        type=int,
        default=4,
        help="max_parallel_maintenance_workers for the HNSW build",
    )
    parser.add_argument(
        "--force-production",
        action="store_true",
        help="required to run when ENVIRONMENT=production (this seeder writes synthetic data "
        "and rebuilds the shared HNSW index; never point it at a real database)",
    )
    args = parser.parse_args(argv)

    if settings.is_production and not args.force_production:
        raise SystemExit(
            "Refusing to seed with ENVIRONMENT=production. This writes synthetic orgs/keys/"
            "chunks and drops+rebuilds the document_chunks HNSW index against "
            f"{settings.POSTGRES_DB!r}. Point DATABASE_URL at a dev/staging database, or pass "
            "--force-production if you are certain this is a disposable load-test target."
        )

    dim = settings.EMBEDDING_DIM
    self_check_embeddings(dim)
    _warn_if_build_memory_undersized(args.chunks, dim, args.maintenance_mem, args.keep_index)
    print(f"Embedding self-check passed (dim={dim}).")

    timings: list[tuple[str, float]] = []

    t0 = time.monotonic()
    seeded = asyncio.run(seed_entity_graph(args.orgs, args.collections_per_org))
    _stage(timings, "entity graph", t0)
    print(f"Seeded {len(seeded)} orgs.")

    plans = plan_documents(seeded, args.chunks, args.chunks_per_doc)
    workers = args.workers or max(1, (os_cpu_count() or 2) - 1)

    with psycopg.connect(_psycopg_dsn()) as conn:
        if not args.keep_index:
            t0 = time.monotonic()
            drop_hnsw_index(conn)
            _stage(timings, "drop hnsw index", t0)

        t0 = time.monotonic()
        insert_documents(conn, plans)
        _stage(timings, f"insert {len(plans)} documents", t0)

        t0 = time.monotonic()
        copy_chunks(conn, plans, args.chunks, dim, workers)
        copy_seconds = time.monotonic() - t0
        _stage(timings, f"copy {args.chunks} chunks", t0)
        rate = args.chunks / copy_seconds if copy_seconds > 0 else 0.0
        print(f"COPY complete: {args.chunks} chunks in {copy_seconds:,.1f}s ({rate:,.0f} rows/s)")

        if not args.keep_index:
            t0 = time.monotonic()
            rebuild_hnsw_index(conn, args.maintenance_mem, args.index_parallel_workers)
            _stage(timings, "rebuild hnsw index", t0)

        t0 = time.monotonic()
        analyze_chunks(conn)
        _stage(timings, "analyze", t0)

        print_sizes(conn)

    write_manifest(args.manifest, seeded, args.chunks, dim)

    print("Stage timings:")
    for name, seconds in timings:
        print(f"  {name}: {seconds:,.1f}s")
    return 0


def os_cpu_count() -> int | None:
    return os.cpu_count()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - a seeder failure must exit non-zero
        print(f"Seeding failed: {exc}", file=sys.stderr)
        sys.exit(1)
