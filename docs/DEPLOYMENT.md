# Deployment

How to run Third Brain in production. The stack is three long-running processes - the
**API**, one or more **workers**, and the **web** frontend - backed by **PostgreSQL 16
with `pgvector`** and **Redis 7**.

> **New here? Start with [`SELF_HOSTING.md`](./SELF_HOSTING.md).** Third Brain only runs
> self-hosted - there is no hosted service - and that guide is the one-command
> (`make selfhost`), all-on-your-host route: your documents, embeddings, keys, and audit log
> never leave datastores you run. This document is the deeper runbook it builds on, for when
> you want managed datastores, Kubernetes, or horizontal scale.

- [Architecture recap](#architecture-recap)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Managed datastores](#managed-datastores-recommended)
- [Production Docker Compose](#production-docker-compose)
- [Database migrations](#database-migrations)
- [Scaling](#scaling)
- [Object storage](#object-storage-for-uploads)
- [TLS, CORS and networking](#tls-cors-and-networking)
- [Observability](#observability)
- [Backups & DR](#backups--disaster-recovery)
- [Upgrade & rollback](#upgrade--rollback)
- [Production checklist](#production-checklist)

---

## Architecture recap

```
              ┌──────────┐        ┌──────────────┐
  clients ───►│  web     │        │  Postgres 16 │  data + vectors (pgvector) + ACLs
  (browser)   │ (Next.js)│        │  + pgvector  │
              └────┬─────┘        └──────▲───────┘
                   │ HTTPS               │
              ┌────▼─────┐   ┌───────────┴───┐
  API keys ──►│  api     │──►│    Redis 7    │  cache / rate limit / task queue
  MCP / API   │ (FastAPI)│   └───────▲───────┘
              └────┬─────┘           │
                   │ enqueue         │ dequeue
              ┌────▼───────────┐     │
              │ worker(s) (arq)│─────┘   embed + index ingestion jobs
              └────────────────┘
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design.

---

## Prerequisites

- A container runtime (Docker + Compose v2, or Kubernetes / ECS / Nomad).
- PostgreSQL **16** with the `pgvector` extension available.
- Redis **7** (persistence recommended).
- A domain + TLS termination at the edge (Cloudflare Tunnel as shipped, or your own
  reverse proxy / cloud load balancer).
- At least one LLM provider key (or a per-org **Connector** configured in-app).

---

## Configuration

All configuration is environment-based (see [`app/core/config.py`](../apps/api/app/core/config.py)).
Provide production values via your orchestrator's secret store - **never** bake secrets
into images.

Minimum production overrides on top of [`.env.example`](../.env.example):

```dotenv
ENVIRONMENT=production
LOG_LEVEL=INFO

# 32+ bytes of entropy. Rotating this invalidates all JWTs AND makes stored
# connector credentials undecryptable - see SECURITY.md before rotating.
SECRET_KEY=<openssl rand -hex 32>

# Point at your managed datastores (note the +asyncpg driver).
DATABASE_URL=postgresql+asyncpg://tb_app:<pw>@pg.internal:5432/thirdbrain
REDIS_URL=rediss://:<pw>@redis.internal:6380/0

# Lock CORS to your real web origin(s).
BACKEND_CORS_ORIGINS=https://your-domain.example

# Providers (or configure Connectors per-org in the dashboard).
OPENAI_API_KEY=sk-...

# Uploads on durable object storage (see below).
STORAGE_BACKEND=s3
S3_BUCKET=third-brain-prod
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...

# Frontend build-time API URL.
NEXT_PUBLIC_API_URL=https://api.your-domain.example

# Public origin of the API itself. Baked into image documents' capability links at
# ingestion (the URL an LLM fetches to view the original image); with the default
# (http://localhost:8000) every image indexed carries a dead link until reprocessed.
PUBLIC_API_URL=https://api.your-domain.example

# Public origin of the web app. Invite accept links, the CLI device-auth /activate
# URL and the default SSO redirect_uri are built from this; with the default
# (http://localhost:3000) every one of those links is dead.
APP_BASE_URL=https://your-domain.example

# Transactional email, needed to invite teammates. The default provider is `stub`,
# which captures messages in-process and never sends, and the accept link is never
# shown in the dashboard - so invites go nowhere until this is configured.
EMAIL_PROVIDER=smtp
EMAIL_FROM=Third Brain <no-reply@your-domain.example>
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_USE_TLS=true
```

Tuning knobs worth setting explicitly in production:

| Variable | Guidance |
|---|---|
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Defaults 10 / 20. Every API and worker **process** opens its own pool, so size to `(pool + overflow) × processes ≤ Postgres max_connections` - an API replica runs `UVICORN_WORKERS` of them. |
| `UVICORN_WORKERS` | Uvicorn worker processes per API container; `docker-compose.prod.yml` reads it from `.env` (default 4). |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Short (default 30). |
| `DEFAULT_RATE_LIMIT_PER_MINUTE` | Global default; per-key limits override it. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | Must match the model. **Changing the dimension requires a full re-index.** |

---

## Managed datastores (recommended)

Run Postgres and Redis as managed services rather than in-container.

### PostgreSQL + pgvector

Any Postgres 16 with `pgvector` works: **AWS RDS/Aurora** (`CREATE EXTENSION vector;`),
**GCP Cloud SQL** (enable the extension), **Azure Database for PostgreSQL**, **Supabase**,
**Neon**, or **Crunchy**.

```sql
-- Once, as a superuser on the target database:
CREATE EXTENSION IF NOT EXISTS vector;

-- Create a least-privilege application role:
CREATE ROLE tb_app LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE thirdbrain TO tb_app;
-- (schema/table grants are applied after `alembic upgrade head`)
```

Then set `DATABASE_URL=postgresql+asyncpg://tb_app:...@host:5432/thirdbrain`. Migrations
themselves use the sync psycopg URL, which the app derives automatically.

Sizing: the ANN index (HNSW on `document_chunks.embedding`) is memory-hungry - provision
RAM to keep the index resident. Enable automated storage growth; embeddings are the bulk
of the data.

### Redis

**AWS ElastiCache**, **GCP Memorystore**, **Azure Cache for Redis**, or **Upstash**. Redis
is used for caches (safe to lose), the API-key rate limiter, and the `arq` task queue -
enable persistence (AOF) so queued ingestion jobs survive a restart, and prefer TLS
(`rediss://`).

---

## Production Docker Compose

The repo's [`docker-compose.yml`](../docker-compose.yml) is dev-oriented (bind mounts,
`--reload`, bundled db/redis). For production, use an override that removes dev conveniences,
points at managed datastores, and runs the ASGI server with multiple workers.

For orientation, this is the dev topology the base file brings up with `make up`
(every service publishes its port on the host):

```mermaid
flowchart LR
    dev["Developer browser"]
    subgraph compose["docker compose dev stack (make up)"]
        web["web<br/>Next.js dev server - host :3000"]
        api["api<br/>FastAPI, uvicorn --reload - host :8000"]
        worker["worker<br/>arq (same image as api)"]
        db[("db<br/>pgvector/pgvector:pg16 - host :5432")]
        redis[("redis<br/>redis:7-alpine, AOF - host :6379")]
        uploads[/"uploads volume at /data/uploads"/]
    end
    dev -->|":3000"| web
    dev -->|":8000 via NEXT_PUBLIC_API_URL"| api
    api -->|"SQL + pgvector search"| db
    api -->|"cache, rate limit, enqueue"| redis
    worker -->|"dequeue ingestion jobs"| redis
    worker -->|"write chunks + embeddings"| db
    api -.-> uploads
    worker -.-> uploads
```

The repo **ships** a production topology at `docker-compose.prod.yml` - a self-contained
single-host stack, so there is no file to author. It runs the baked images (no source
mounts, no `--reload`) with `db`, `redis`, `api`, `worker` and `web` all on the internal
Compose network; the base file publishes **no** host ports, so how traffic gets in is an
overlay decision (below). A one-shot `migrate` service (`alembic upgrade head`) gates
`api`/`worker`, and `web` runs the Next.js standalone server (`node server.js`).

For a public deployment on a domain you own, layer on the **Cloudflare Tunnel** overlay: a
`cloudflared` container joins the internal network and dials **out** to Cloudflare, TLS
terminates at the edge, and requests come back down the tunnel to `web:3000` and `api:8000`
by service name, so the host opens no inbound ports at all:

```bash
cp .env.example .env
# Fill in a strong SECRET_KEY and your provider keys, then add the tunnel token
# (Cloudflare dashboard -> Zero Trust -> Networks -> Tunnels; see the header of
# docker-compose.cloudflare.yml for the Public Hostname mappings):
#   CLOUDFLARE_TUNNEL_TOKEN=<connector token>
#   NEXT_PUBLIC_API_URL=https://api.your-domain.example   # baked into the web build
docker compose -f docker-compose.prod.yml -f docker-compose.cloudflare.yml up -d --build
```

Two alternatives, depending on where the deployment lives:

- **Local / LAN self-host** - the `docker-compose.selfhost.yml` overlay (what `make selfhost`
  runs) publishes the `web`/`api` ports directly on the host, bound to loopback by default.
  See [`SELF_HOSTING.md`](./SELF_HOSTING.md).
- **Public, without Cloudflare** - put your own TLS-terminating proxy - nginx or a cloud
  load balancer - in front of `web:3000` / `api:8000`.

Migrations run automatically via the `migrate` one-shot before `api`/`worker` start; to run
them manually, `docker compose -f docker-compose.prod.yml run --rm migrate`.

> **Managed Postgres/Redis:** set `DATABASE_URL` (must use the `+asyncpg` driver) and
> `REDIS_URL` in `.env`, then drop the bundled `db` / `redis` services and their `depends_on`
> entries - as the file's header documents.

For **Kubernetes**, the same images become Deployments: `api` (HPA on CPU/RPS with a
readiness probe on `/api/v1/health/ready`), `worker` (HPA on queue depth), and `web`. Run
migrations as a pre-deploy `Job` (the equivalent of the compose `migrate` one-shot).

---

## Database migrations

Migrations are [Alembic](https://alembic.sqlalchemy.org/); the revisions live in
[`apps/api/alembic/versions`](../apps/api/alembic/versions).

```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

Run migrations **before** rolling new app code, as a single one-shot task (not per replica
or in the container `CMD`, which would race). The initial migration also enables the
`vector` extension where the role has permission; otherwise pre-create it (see above).

### Upgrading a database created before v2.0.0

`0001_initial` is the **only** revision: the v2.0.0 open-source release collapsed the entire
history into that one baseline. It is frozen from here on - every future schema change ships
as a new revision on top of it, so `alembic upgrade head` is what carries an existing
database forward and must run on every deploy, not only on fresh installs.

A 1.x database is already stamped `0001_initial`, so `alembic upgrade head` finds nothing to
do and the new code starts normally. What the fold changed is the baseline, not your
database: it no longer creates the commercial-era `waitlist_entries` table or the
`organizations.plan` column, and Alembic will not remove them from a database that already
has them. Nothing in 2.x reads either one, but the statements below bring an older database
back to baseline parity. They are idempotent and safe to run repeatedly, and they also cover
the pre-1.0 folds, when schema changes were merged into `0001_initial` while no deployment
existed that we did not control:

```sql
DROP TABLE IF EXISTS waitlist_entries;
ALTER TABLE organizations DROP COLUMN IF EXISTS plan;
CREATE INDEX IF NOT EXISTS ix_entities_org_normalized
    ON entities (org_id, normalized);
CREATE INDEX IF NOT EXISTS ix_documents_meta_file_token
    ON documents ((metadata ->> 'file_token'));
```

If the deploy instead fails with **`Can't locate revision identified by '0002_drop_waitlist'`**
(or `'0003_drop_org_plan'`, or a pre-1.0 id), the database ran a development build whose
revisions the fold removed. Those revisions did nothing beyond the two drops above, so once
the schema matches the baseline, realign the stamp and redeploy:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate alembic stamp --purge 0001_initial
```

`--purge` is load-bearing: plain `alembic stamp` resolves the *recorded* revision before it
writes the new one and fails with the same error.

---

## Scaling

- **API** - stateless. Scale horizontally behind a load balancer; increase `UVICORN_WORKERS`
  per replica for CPU parallelism. Watch the DB connection budget (`DB_POOL_SIZE`).
- **Workers** - the ingestion pipeline (extract → scan (secrets, DLP) → chunk → embed →
  index → enrich) is the throughput bottleneck. Scale `worker` replicas to raise concurrency;
  each pulls jobs from the shared Redis queue, so adding replicas is safe and linear.
  Embedding latency is provider-bound, so more workers mainly help under bursty uploads.
- **Postgres** - vertical first (RAM for the HNSW index, CPU for ANN). Add read replicas
  for analytics-heavy read load. Ensure the ANN index exists and is warm.
- **Redis** - usually not a bottleneck; size memory for cached embeddings + hot search
  results, and keep the rate-limit/queue keyspace headroom.

Rate limiting is enforced per API key in Redis, so it holds correctly across API replicas.

---

## Object storage for uploads

Set `STORAGE_BACKEND=s3` and the `S3_*` variables to store uploaded files on durable
object storage (S3, GCS via the S3 API, MinIO, R2, …). With `local`, files live on the
container's `/data/uploads` volume - fine for single-host, but not for multi-replica or
ephemeral hosts. Migrate to S3 before scaling the API horizontally.

---

## TLS, CORS and networking

The target production layout: only the TLS-terminating edge is public, and the app tiers
and datastores stay on the private network. On the repo's own tunnel path that edge is
Cloudflare's - `cloudflared` dials out from the internal network, so nothing on the host
publishes 80/443. The diagram shows the generic bring-your-own-proxy shape:

```mermaid
flowchart LR
    clients["Browsers, MCP + API clients"]
    proxy["Reverse proxy / load balancer<br/>TLS termination, :443 only"]
    s3[("S3 object storage<br/>(uploads, STORAGE_BACKEND=s3)")]
    subgraph private["Private network"]
        web["web (Next.js) :3000"]
        api["api replicas (FastAPI) :8000"]
        worker["worker replicas (arq)"]
        pg[("managed Postgres 16<br/>+ pgvector")]
        rd[("managed Redis 7<br/>AOF, rediss://")]
    end
    clients -->|"HTTPS"| proxy
    proxy -->|"your-domain.example"| web
    proxy -->|"api.your-domain.example"| api
    api --> pg
    api -->|"cache, rate limit, enqueue"| rd
    worker -->|"dequeue"| rd
    worker -->|"chunks + embeddings"| pg
    api --> s3
    worker --> s3
```

- Terminate TLS at the edge: Cloudflare's on the tunnel path, or your own reverse proxy /
  load balancer (nginx or a cloud load balancer) if you front `web:3000` / `api:8000`
  yourself.
- Set `BACKEND_CORS_ORIGINS` to your exact web origin(s) - never `*` in production
  (credentials are allowed).
- Keep Postgres and Redis on a private network. On the tunnel path the host opens no
  inbound ports at all; with your own proxy, expose only 443 for `web` and `api`.
- The API streams chat via `text/event-stream`; disable proxy buffering on `/api/v1/search/chat`
  (the app already sends `X-Accel-Buffering: no` for nginx).

---

## Observability

- **Health**: `GET /api/v1/health` returns `{"status": "ok"|"degraded", "checks": {...}}` - wire
  it to your load balancer and uptime monitor.
- **Logs**: structured logs go to stdout (`LOG_LEVEL`); ship them with your platform's log
  driver.
- **Usage & audit**: every metered call writes a `usage_records` row and security-relevant
  actions write `audit_logs`. Surface them via the dashboard's Usage/Audit pages or query
  the `/api/v1/analytics/*` endpoints.

---

## Backups & disaster recovery

- **Postgres** is the source of truth (documents, chunks, embeddings, ACLs, usage). Use
  managed automated backups + PITR; test restores regularly.
- **Object storage** holds original uploads - enable versioning.
- **Redis** is mostly reconstructible cache; with AOF on, in-flight ingestion jobs survive
  restarts. Losing Redis at worst re-runs some ingestion and cold-starts caches.
- Losing the DB requires re-ingestion of all documents to rebuild embeddings - back it up.

---

## Upgrade & rollback

Releases are cut, tagged and published per [`RELEASING.md`](./RELEASING.md); upgrading a
deployment is repointing at a released tag.

**Upgrade** - pin the new version in `.env` and roll the stack:

```dotenv
# .env
IMAGE_REGISTRY=ghcr.io/km322   # pull published images (unset = build from source)
IMAGE_TAG=vX.Y.Z
```

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Include the ingress overlay you deployed with (e.g. `-f docker-compose.cloudflare.yml`) in
both commands, so Compose recreates services from the full project definition.

The `migrate` one-shot runs `alembic upgrade head` and gates `api`/`worker`, so new code
only starts once the schema is current.

**Rollback** - repoint at the previous released tag:

```bash
make rollback IMAGE_TAG=vPREV
```

This restarts `api`/`worker`/`web` on the older images without re-running the `migrate`
gate. No schema downgrade is involved - it is release **policy** that every release's
migrations are backward-compatible with the previous release's code (expand/contract:
add columns/tables first, backfill, remove in a later release), so the previous images
run correctly against the newer schema. See [`RELEASING.md`](./RELEASING.md) for the
full runbook.

It defaults to the single-box file pair (`docker-compose.prod.yml` +
`docker-compose.selfhost.yml`). Name the ingress overlay you actually deployed with, so the
recreated services keep it:

```bash
TB_COMPOSE_FILES="-f docker-compose.prod.yml -f docker-compose.cloudflare.yml" \
  make rollback IMAGE_TAG=vPREV
```

---

## Production checklist

- [ ] `ENVIRONMENT=production`, long random `SECRET_KEY` from a secret store.
- [ ] Managed Postgres 16 with `vector` extension; least-privilege app role.
- [ ] Managed Redis with persistence + TLS.
- [ ] `alembic upgrade head` run as a one-shot before rollout.
- [ ] `IMAGE_TAG` pinned to a released version (`vX.Y.Z`) - never deploy `:latest`.
- [ ] `STORAGE_BACKEND=s3` with a durable bucket (versioning on).
- [ ] `BACKEND_CORS_ORIGINS` locked to real origins; TLS everywhere.
- [ ] `APP_BASE_URL` set to the real dashboard origin (invite + device-auth links).
- [ ] At least one LLM provider key or per-org Connector configured.
- [ ] `/api/v1/health` wired to the load balancer + uptime alerts.
- [ ] Worker replicas sized for ingestion load; `UVICORN_WORKERS` sized to the DB pool budget.
- [ ] Automated DB backups + tested restore; log shipping in place.
