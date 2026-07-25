# CLAUDE.md - working in the Third Brain repo

Orientation for an LLM/agent seeing this repo for the first time. Keep it accurate; update
it when the architecture changes.

## What this is
**Third Brain** is a SaaS "company-wide second brain": ingest a company's knowledge once,
and the LLM tools your teams use (Claude Desktop, Cursor, any OpenAI-compatible client,
agents) can **search it, cite it, and write back to it**, governed by **document-level
permissions**. Think a clean gateway + dashboard, but for knowledge instead of tokens.

## Stack & layout
Monorepo.
- `apps/api` - **FastAPI** (async SQLAlchemy 2.0, Alembic). Python ≥ 3.11.
  - `app/core` - config, db engine, redis, security, **deps (auth/authz)**. *Spine - change carefully.*
  - `app/models` - SQLAlchemy models = the data contract. JSON attr is `meta` (column `"metadata"`).
  - `app/services` - `permissions.py` (**the** authz engine), `llm/` (any OpenAI-compatible
    endpoint via `httpx` + a deterministic offline stub provider), `vectorstore/`,
    `retrieval.py`, `rag.py`, `ingestion.py`,
    `chunking.py`, `extractors.py`, `storage.py`, `metering.py`. `vectorstore/` is pgvector-backed
    (abstracted behind a `VectorStore` interface so Qdrant/Pinecone can drop in later).
  - `app/api/routes/*` - one module per domain; each exports `router = APIRouter(prefix=..., tags=[...])`
    and is auto-included by `app/api/router.py`. `app/api/openai_compat.py` = OpenAI-compatible `/v1`.
  - `app/mcp` - Model Context Protocol server (`mount_mcp(app)`), mounted at `/mcp`.
  - `app/workers` - arq ingestion workers (`app.workers.settings.WorkerSettings`).
  - `alembic/` migrations, `tests/`.
- `apps/web` - **Next.js 14** (App Router, TS, Tailwind + shadcn/ui, TanStack Query).
  - `app/(marketing)` landing · `app/(auth)` login/signup · `app/dashboard/*` the app.
  - `components/ui/*` shadcn primitives · `components/dashboard/*` shell · `lib/api.ts` typed client ·
    `lib/types.ts` mirrors backend schemas · `lib/auth-context.tsx`.
- `docs/` - architecture, API, permissions, deploy.

## Run it
```bash
cp .env.example .env      # runs with ZERO keys: deterministic offline stub provider kicks in
make up                   # postgres+pgvector, redis, api, worker, web
make migrate && make seed # demo org -> admin@example.com; prints the generated password + an API key (once)
```
API: `localhost:8000` (`/docs`). Web: `localhost:3000`.

## The one invariant that matters most
`app/services/permissions.py` is the **single source of truth** for access. It is enforced in
**two** places that must never disagree:
1. route authorization - `effective_permission` / `require_permission`, and
2. retrieval - `build_retrieval_scope()` returns a `RetrievalScope` whose `.apply(stmt)` pushes the
   visibility predicate **into SQL**, so a chunk you can't see can never enter a search result/prompt.
Every query is org-scoped by `ctx.org_id`. If you touch retrieval or ACLs, add/adjust tests in
`tests/` for both call sites.

## Testing - production parity only (read before adding tests)
Tests run against the REAL stack - **Postgres 16 + pgvector + Redis** - never fakes. There is no
SQLite/mock tier. Three kinds:
- **Unit**: pure functions (permission math, chunking, RRF fusion, pricing, security) - no infra, always run.
- **Integration**: drive the real FastAPI app (httpx ASGI) against real Postgres+pgvector + Redis, after a
  real `alembic upgrade head`. They **skip** (never fail) when infra is unreachable, so `pytest` is green
  locally without a stack; **CI provides the infra and is the authoritative gate**. `make test` /
  `make test-integration`.
- **E2E**: Playwright drives the real docker-compose stack (browser flow). `make test-e2e`.
Point tests at infra with `TEST_DATABASE_URL` / `TEST_REDIS_URL` (CI sets these to service containers).
New integration/e2e tests MUST pass against real pgvector + Redis.

## Conventions / gotchas
- Add a route: create `app/api/routes/<name>.py` exporting `router`, then register the name in
  `app/api/router.py`. Add request/response schemas in `app/schemas/<name>.py`.
- Auth: JWT access token carries `sub`=user id and `org`=active org id (`create_access_token(subject, extra={"org": ...})`).
  API keys are stored only as SHA-256 hashes; connector secrets are Fernet-encrypted.
- LLM calls go through `app/services/llm` - never call providers directly. It falls back to a
  deterministic offline provider when no keys are set, so tests/dev never hit the network.
- `metering.record_usage` / `record_audit` add+flush only; the request must `await db.commit()`.
- Logging: `app.core.logging.get_logger(__name__)` (structlog; key=value fields), request/job
  context via `structlog.contextvars.bind_contextvars`. Spans: `app.core.telemetry.get_tracer` -
  a no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Never log prompt/document/query content
  or secrets; metadata only (see `docs/OBSERVABILITY.md`).
- Frontend: use `@/lib/api` (`api.get/post/...`, `streamChat`), `@/lib/types`, `@/components/ui/*`.
  Page files export only a default component (no other named exports - Next enforces this).
- Model annotations that SQLAlchemy resolves at runtime (e.g. `Mapped[datetime | None]`) must be
  importable at runtime, not under `TYPE_CHECKING`.
- The root `VERSION` file is the single source of truth for the product version - never edit
  version literals by hand; `make version VERSION=x.y.z` updates them all.
- Releases are cut with `make release VERSION=x.y.z` + a tag push, which triggers
  `.github/workflows/release.yml` (see `docs/RELEASING.md`).

## Commands
`make help` lists everything. Backend: `ruff check app`, `pytest`, `alembic upgrade head`.
Frontend: `npm run dev|build|lint` in `apps/web`.
