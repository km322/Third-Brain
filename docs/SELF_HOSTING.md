# Self-hosting

> **This is how Third Brain runs.** It is free, Apache-2.0, and self-hosted by design -
> there is no hosted service to sign up for and nothing is held back for a paid edition.
> This guide is the supported path from a clean host to a running instance.

Third Brain runs entirely on your infrastructure. Your documents, embeddings, keys, and
audit log live only in your Postgres, Redis, and upload directory. The project ships
software; nobody else ever receives your data.

For the deeper production runbook (managed datastores, Kubernetes, scaling), see
[`DEPLOYMENT.md`](./DEPLOYMENT.md); this guide is the fast, all-on-your-host route.

- [Why self-host](#why-self-host)
- [Prerequisites](#prerequisites)
- [Quick start: `make selfhost`](#quick-start-make-selfhost)
- [Manual setup, step by step](#manual-setup-step-by-step)
- [What data lives where](#what-data-lives-where)
- [Nothing phones home](#nothing-phones-home)
- [Bring your own models](#bring-your-own-models)
- [Secrets and backups](#secrets-and-backups)
- [Upgrades and rollback](#upgrades-and-rollback)
- [TLS and production hardening](#tls-and-production-hardening)
- [Deploy behind Cloudflare (public domain)](#deploy-behind-cloudflare-public-domain)
- [Compliance and data residency](#compliance-and-data-residency)

---

## Why self-host

Third Brain is a governed layer over your company's knowledge, so the data it holds is
usually the data you most want to keep in-house. That is why the product is built this way
round rather than as a service someone else runs:

- **Your data stays yours.** Documents, chunks, embeddings, permissions, and the audit log
  live only in datastores you run. None of your knowledge leaves your perimeter.
- **No third party in the data path.** With the offline stub provider and local storage,
  a default deployment makes zero outbound calls. The only egress that ever happens is to
  endpoints *you* configure (your own LLM provider, your own data sources).
- **Your perimeter, your controls.** You choose the network policy, the backup regime, and
  the region. Compliance posture follows data custody, and here the custody is yours.
- **No bill and no gate.** The whole product is in the repository under Apache-2.0: no
  licence key, no seat count, no feature flag that unlocks with a payment. The only money
  involved is your infrastructure and whatever your own provider keys spend.

The whole stack is open and runs on commodity infrastructure: Postgres 16 with `pgvector`,
Redis 7, and the API / worker / web images. The default single-box `make selfhost` path
publishes the dashboard and API directly on the host; for a public domain, run it behind the
Cloudflare Tunnel overlay (outbound-only, TLS at the edge) or your own TLS-terminating
reverse proxy (see [TLS and production hardening](#tls-and-production-hardening)).

---

## Prerequisites

- **Docker + Docker Compose v2** on a single host (a small VM is enough to start).
- A **host** you control (bare metal, a VM, or your own VPC).
- Optional but recommended for real use: a **domain and DNS** you control, so the
  deployment can sit behind TLS (a Cloudflare Tunnel, or your own reverse proxy).
- Optional: an **LLM provider key** (OpenAI, Anthropic, or Google). Without one, the
  deterministic offline stub runs the full pipeline, so you can stand the product up and use
  it before committing a key. The stub's embeddings are placeholders, so offline answers
  exercise the pipeline but are not a measure of retrieval quality.

Everything else (Postgres, `pgvector`, Redis - and `cloudflared` on the Cloudflare Tunnel
path) comes up as containers from `docker-compose.prod.yml` and its overlays. No external
services are required.

---

## Quick start: `make selfhost`

One command brings up a hardened, production-mode stack on your host and creates your first
admin:

```bash
make selfhost
```

`make selfhost` wraps `scripts/selfhost-init.sh` and does the following:

1. **Generates a `.env`** with a strong random `SECRET_KEY` and `POSTGRES_PASSWORD`, sets
   `ENVIRONMENT=production`, and fills in explicit CORS origins and app URLs. It **never
   overwrites an existing `.env`** - your secrets are written once and then left alone.
2. **Builds and starts the stack** with
   `docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml up -d --build`.
   The `docker-compose.selfhost.yml` overlay publishes the dashboard and API directly to the
   host, so a single-box deployment is reachable at `http://localhost:3000` (or your
   `--host`) with no domain or reverse proxy. Building locally is what bakes your
   `NEXT_PUBLIC_API_URL` into the web bundle correctly (see
   [the web image caveat](./RELEASING.md#published-web-image-caveat)).
3. **Applies migrations** (the `migrate` one-shot runs `alembic upgrade head` and gates the
   API and worker, so app code only starts once the schema is current).
4. **Bootstraps your first admin** and prints the dashboard URL along with the admin email
   and password.

To bind to a public host or IP instead of `localhost`, pass `make selfhost` a host:
`./scripts/selfhost-init.sh --host 203.0.113.10 --email you@yourcompany.com`. Note this
publishes the dashboard and API over plaintext HTTP with no TLS. Before exposing a public
host or IP to the internet, put TLS in front - use the Cloudflare Tunnel overlay in
[Deploy behind Cloudflare](#deploy-behind-cloudflare-public-domain) (outbound-only, no
inbound ports at all), or your own TLS-terminating reverse proxy (nginx or a cloud load
balancer). On `localhost` (the default) the ports bind to loopback only.

When it finishes it prints something like:

```
Third Brain is running.

  Web:  http://localhost:3000

  Admin login:
    email:    you@yourcompany.com
    password: <generated once - copy it now>
```

Sign in with those credentials, then change the password from the dashboard. Store the
`SECRET_KEY` from `.env` somewhere safe (see [Secrets and backups](#secrets-and-backups)).

Stop the stack with:

```bash
make selfhost-down
```

Your data persists in Docker volumes across restarts; `selfhost-down` stops the containers
without deleting those volumes.

---

## Manual setup, step by step

If you would rather see each step, the manual equivalent of `make selfhost` is:

```bash
# 1. Copy the environment template (never commit the resulting .env).
cp .env.example .env
```

Then edit `.env` and set, at minimum. Use the plain `http://<host>:<port>` values for a
single-box deployment, or the `https://<domain>` values for a public domain (Cloudflare
Tunnel, or your own reverse proxy):

```dotenv
# Refuse-to-boot in production unless SECRET_KEY is real: 32+ random chars,
# not a "change-me"/"changeme" placeholder. It signs sessions AND encrypts
# connector credentials - back it up (see Secrets and backups).
SECRET_KEY=<openssl rand -hex 32>

# A strong database password (the prod compose reads POSTGRES_PASSWORD).
POSTGRES_PASSWORD=<openssl rand -hex 32>

# Lock CORS to your real web + api origin(s). Never "*" in production - boot is refused.
BACKEND_CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# Invite links and the CLI device-auth flow are built from this (the dashboard) origin.
APP_BASE_URL=http://localhost:3000

# Baked into the web bundle at BUILD time; must be the API origin the browser can reach.
NEXT_PUBLIC_API_URL=http://localhost:8000

# Public origin of the API itself; baked into image documents' capability links at
# ingestion. Keep it in step with NEXT_PUBLIC_API_URL (selfhost-init sets both).
PUBLIC_API_URL=http://localhost:8000

# Public-domain path only: flip the four origins above to your https:// domain, e.g.
# BACKEND_CORS_ORIGINS and APP_BASE_URL -> https://your-domain.example, and
# NEXT_PUBLIC_API_URL and PUBLIC_API_URL -> https://api.your-domain.example.
# On the Cloudflare Tunnel path, also set the tunnel's connector token (see
# Deploy behind Cloudflare below):
# CLOUDFLARE_TUNNEL_TOKEN=<connector token>
```

The production compose forces `ENVIRONMENT=production` regardless of what `.env` says, so
the boot-time config guard is always active: it refuses to start with a placeholder or
short `SECRET_KEY`, or with wildcard (`*`) CORS.

```bash
# 2a. Single-box (localhost / LAN / bare IP): the selfhost overlay publishes the web and
#     API ports directly, so http://localhost:3000 and :8000 work with no reverse proxy.
docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml up -d --build

# 2b. Public domain instead: set the https:// URLs above, then swap the selfhost overlay
#     for the Cloudflare Tunnel overlay (outbound-only, no inbound ports; see Deploy
#     behind Cloudflare below):
#     docker compose -f docker-compose.prod.yml -f docker-compose.cloudflare.yml up -d --build
#     Or keep the selfhost overlay and put your own TLS-terminating reverse proxy (nginx or
#     a cloud load balancer) in front of the published web and API ports.

# 3. Migrations run automatically via the `migrate` one-shot. To run them by hand:
docker compose -f docker-compose.prod.yml run --rm migrate

# 4. Bootstrap your first admin (there is no demo seeder in production). Pass ADMIN_EMAIL
#    into the container with `exec -e` (a shell prefix would not reach the container).
#    ADMIN_PASSWORD is optional: add `-e ADMIN_PASSWORD=...`, or omit it to have one
#    generated and printed once.
docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml \
  exec -T -e ADMIN_EMAIL=you@yourcompany.com api \
  python -m app.scripts.bootstrap_admin
```

`bootstrap_admin` reads:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ADMIN_EMAIL` | yes | - | The owner account's email. |
| `ADMIN_PASSWORD` | no | generated | If unset, a strong password is generated and printed **once**. |
| `ADMIN_ORG_NAME` | no | `My Company` | The organization created for the owner. |
| `--with-api-key` | no | off | Also mints an API key, printed once, for immediate programmatic use. |

It is **idempotent**: re-running it does nothing if the admin already exists, so it is safe
to include in your provisioning scripts.

> The bundled `db` and `redis` are fine for a single host. To use **managed Postgres/Redis**
> instead, set `DATABASE_URL` (with the `+asyncpg` driver) and `REDIS_URL` in `.env`, then
> drop the `db` / `redis` services and their `depends_on` entries as documented at the top
> of `docker-compose.prod.yml`. Your data still lives only in datastores you run.

---

## What data lives where

Every byte of your knowledge lives in storage you operate:

| Store | What it holds |
|---|---|
| **Postgres 16** | The source of truth: organizations, users, documents, permissions/ACLs, usage metering, and the audit log. |
| **Postgres + `pgvector`** | Chunk **embeddings** and the ANN (HNSW) index that powers semantic and hybrid search. |
| **Redis 7** | Ephemeral state: the search/embedding cache, the API-key rate limiter, and the `arq` ingestion queue. Reconstructible; with AOF on, queued jobs survive a restart. |
| **Upload directory** | The original uploaded source files. Local by default (`STORAGE_LOCAL_PATH`, the `/data/uploads` volume); optionally an S3-compatible bucket you control. |

Secrets are transformed before they ever reach Postgres: passwords are bcrypt-hashed, API
keys are stored only as a SHA-256 hash plus a short prefix, and **connector provider
credentials are Fernet-encrypted at rest, with the encryption key derived from your
`SECRET_KEY`**. None of these are ever returned by the API. See
[`SECURITY.md`](./SECURITY.md#secret-handling) for the full matrix.

---

## Nothing phones home

A default self-host is silent. Grounded in how the code is wired:

- **Telemetry is opt-in.** Tracing installs nothing and makes zero network calls unless you
  set `OTEL_EXPORTER_OTLP_ENDPOINT`. Left blank (the default), there is no exporter at all.
- **Zero LLM calls without keys.** With no provider key configured, the deterministic
  offline stub answers everything locally. Nothing leaves the host.
- **Storage is local by default.** Uploads land on the `/data/uploads` volume unless you opt
  into S3.
- **The only outbound calls are ones you configure.** Your own LLM provider connectors (BYO
  keys, optional), your own data-source connectors, and user-triggered URL ingestion (which
  is SSRF-gated). There is no project-operated endpoint in the data path - none exists.

**How to verify:** put the `api` and `worker` containers behind an egress-deny network
policy (allow only your database, Redis, and any LLM/data-source hosts you explicitly use).
With the offline stub and local storage, the product still works fully - ingest, search,
permission-aware answers, and agent write-back all run offline. If you later add a provider
key, the only new egress is to that provider's API.

---

## Bring your own models

Nothing sits between you and your model provider: you bring your own keys, they stay inside
your deployment, and calls go straight to the endpoint you configured. Three options:

- **Platform keys in `.env`.** Set any of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
  `GOOGLE_API_KEY` and every org uses them by default. When a call names no provider,
  completions try OpenAI, then Anthropic, then Google, then the stub; embeddings try OpenAI,
  then Google, then the stub (Anthropic has no embeddings API).
- **Per-org connectors.** Add a Connector on the dashboard to configure providers per
  organization. Credentials are Fernet-encrypted at rest and never returned. This includes
  self-hosted / OpenAI-compatible endpoints (Ollama, vLLM, any gateway) that never leave
  your network - point `OPENAI_BASE_URL` or a `custom` connector at them.
- **Zero-key offline mode.** With no key at all, the offline stub keeps the whole stack
  working end to end. Add a real key when you want real answer and retrieval quality.

Whatever you pick, the dashboard's usage and cost figures report what **your** keys spent
with **your** provider. Nothing bills you for Third Brain itself.

---

## Secrets and backups

`SECRET_KEY` is the master secret. It signs every session token **and** derives the Fernet
key that encrypts connector credentials.

- **Rotating `SECRET_KEY` logs everyone out and makes stored connector credentials
  unrecoverable.** After a rotation you must re-enter every connector's provider key. Treat
  rotation as a planned event; back the key up in a secret manager so you never lose it.
- **Back up Postgres.** It is the source of truth for everything except the original upload
  files. Use `pg_dump` (or your managed provider's automated backups + PITR) and test
  restores. Losing the database means re-ingesting every document to rebuild embeddings.
- **Back up the upload directory.** The `/data/uploads` volume (or your S3 bucket) holds the
  original source files. Snapshot it alongside the database, or enable bucket versioning.
- **Redis is mostly reconstructible.** With AOF enabled (the compose default), in-flight
  ingestion jobs survive a restart; losing Redis at worst re-runs some ingestion and
  cold-starts caches.

A consistent backup is therefore: the `SECRET_KEY`, a Postgres dump, and the upload
directory. See [`SECURITY.md`](./SECURITY.md#secret-handling) and
[`DEPLOYMENT.md`](./DEPLOYMENT.md#backups--disaster-recovery) for detail.

---

## Upgrades and rollback

Releases are versioned together across the API, worker, and web app, and published as
tagged images. Upgrading is repointing at a newer tag (or rebuilding), then letting the
`migrate` one-shot bring the schema forward.

**Upgrade** - build a new version locally, or pull a released tag:

```bash
# Build locally from a newer checkout:
git pull
docker compose -f docker-compose.prod.yml up -d --build

# Or run a published image tag: set IMAGE_REGISTRY + IMAGE_TAG in .env, then:
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

The `migrate` one-shot runs `alembic upgrade head` and gates the API and worker, so new
code starts only once the schema is current.

**Rollback** - repoint at the previous released tag:

```bash
make rollback IMAGE_TAG=vPREV
```

This restarts `api` / `worker` / `web` on the older images without re-running the migrate
gate. It works because every release's migrations are backward-compatible with the previous
release's code (expand/contract). The full runbook is in
[`RELEASING.md`](./RELEASING.md#rolling-back) and
[`DEPLOYMENT.md`](./DEPLOYMENT.md#upgrade--rollback).

---

## TLS and production hardening

The production compose is built to run safely on a single host:

- **TLS at the edge (public domain path).** The stack terminates no TLS itself, and with
  `docker-compose.prod.yml` on its own no service publishes a port at all: `db`, `redis`,
  `api`, `worker`, and `web` stay on the internal network, reachable only by service name.
  For a public domain, either add the Cloudflare Tunnel overlay
  (`docker-compose.cloudflare.yml`) - `cloudflared` dials out to Cloudflare, the host opens
  no inbound ports, and TLS terminates at the edge (see
  [Deploy behind Cloudflare](#deploy-behind-cloudflare-public-domain)) - or bring your own
  TLS-terminating reverse proxy (nginx or a cloud load balancer) in front of `web:3000` /
  `api:8000`. The `make selfhost` single-box overlay (`docker-compose.selfhost.yml`)
  publishes the web and API ports directly, loopback by default - convenient for a
  localhost/LAN trial, but put a TLS terminator in front before exposing it to the internet.
- **Explicit CORS.** `BACKEND_CORS_ORIGINS` must list your real web origin(s). A wildcard
  (`*`) is refused at boot in production because credentials are allowed.
- **Strong `POSTGRES_PASSWORD`.** `make selfhost` generates one; if you set it by hand, use
  real entropy rather than the `thirdbrain` default.
- **Network egress policy.** Restrict outbound traffic from `api` / `worker` to only the
  hosts you use (your database, Redis, and any LLM or data-source endpoints). See
  [Nothing phones home](#nothing-phones-home).
- **Secret scanning stays on.** Ingested content is scanned for embedded credentials before
  indexing and quarantined for review if flagged (`SECRET_SCAN_ENABLED=true` by default).

The full hardening checklist lives in [`SECURITY.md`](./SECURITY.md#hardening-checklist).
For the full public-domain walkthrough, see
[Deploy behind Cloudflare](#deploy-behind-cloudflare-public-domain).

---

## Deploy behind Cloudflare (public domain)

Running Third Brain behind Cloudflare puts the app on a domain you own with edge TLS and DDoS
protection in front of a single box. The examples below use `your-domain.example` for the
dashboard and `api.your-domain.example` for the API - substitute your own. This section is
grounded in how the app is wired; it complements
[TLS and production hardening](#tls-and-production-hardening).

### DNS and the zone

Point both hostnames at the deployment:

- `your-domain.example` -> the web frontend
- `api.your-domain.example` -> the API

The simplest setup is to run the whole zone on Cloudflare: at your registrar, change the
domain's nameservers to the pair Cloudflare assigns. Once the zone is active you manage both
records (and the paths below) from the Cloudflare dashboard. A `www` -> apex redirect (a
Cloudflare Redirect Rule sending `www.your-domain.example` to `https://your-domain.example`)
is a nice-to-have.

### Two viable paths

**(a) Cloudflare Tunnel - recommended for a single box.** `cloudflared` dials *out* to
Cloudflare, so the host opens **no inbound ports** (no 80/443 at all). Cloudflare terminates
TLS at the edge and forwards requests down the tunnel to `web:3000` and `api:8000` by service
name. This is the simplest and most secure option, and the repo ships the overlay:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.cloudflare.yml up -d --build
```

In the Cloudflare dashboard (Zero Trust -> Networks -> Tunnels) create a tunnel, copy its
connector token into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, and add two Public Hostnames on the
tunnel:

| Public hostname | Service |
|---|---|
| `your-domain.example` | `http://web:3000` |
| `api.your-domain.example` | `http://api:8000` |

Those service URLs are plain `http://` on purpose: TLS ends at Cloudflare's edge, and the
`cloudflared` -> web/api hop never leaves the internal Compose network. There is no local
reverse proxy in this setup - Cloudflare is the edge, and nothing on the host listens for
inbound traffic. See the header of
[`docker-compose.cloudflare.yml`](../docker-compose.cloudflare.yml).

**(b) Cloudflare proxy in front of your own reverse proxy.** There is no bundled reverse
proxy, so on this path you publish the origin yourself: put a TLS-terminating reverse proxy
you operate (nginx or a cloud load balancer) in front of `web:3000` / `api:8000`, and let
Cloudflare sit in front of that. Two sub-options:

- **Orange-cloud (proxied) with an Origin Certificate.** Set the zone's SSL/TLS mode to **Full
  (Strict)**, issue a Cloudflare **Origin Certificate**, and install it on your proxy for
  `your-domain.example` / `api.your-domain.example`. The browser trusts Cloudflare's edge cert;
  Cloudflare trusts your origin cert.
- **Grey-cloud (DNS-only) with publicly trusted certificates.** Turn the proxy **off**
  (DNS-only) and have your reverse proxy obtain and renew Let's Encrypt certificates itself
  (HTTP-01 is the usual route). You give up Cloudflare's proxy features (WAF, caching, IP
  hiding) but keep automatic TLS.

Pitfalls specific to the proxied path:

- **Do not use SSL/TLS mode "Flexible".** It sends plaintext HTTP from Cloudflare to your
  origin; a proxy that upgrades HTTP to HTTPS (most configurations do) then produces an
  endless redirect loop. Use **Full (Strict)** with an origin cert (or DNS-only).
- **The TLS-ALPN-01 certificate challenge does not work through the Cloudflare proxy** - it
  terminates at Cloudflare's edge, not at your origin. If your proxy obtains its own
  certificates behind the orange cloud, use **HTTP-01** or **DNS-01**, or skip challenges
  entirely with a Cloudflare **Origin Certificate** - one more reason the **Tunnel** or
  **origin-certificate** route is preferable.

### Build-time and CORS gotchas (read this)

These are identical across every Cloudflare setup and are the most common cause of a broken
deployment:

- **`NEXT_PUBLIC_API_URL` is baked into the web image at BUILD time.** It must equal the
  **browser-facing** API origin (`https://api.your-domain.example`), and you must **rebuild**
  the web image after changing it (`--build`). A stale value points the dashboard at the wrong
  API.
- **`BACKEND_CORS_ORIGINS=https://your-domain.example`.** List your exact web origin(s); a
  wildcard (`*`) is **refused at boot** in production because credentialed requests are allowed.
- **`APP_BASE_URL=https://your-domain.example`.** Invite links and the CLI device-auth
  `/activate` flow are built from this origin.
- **`NEXT_PUBLIC_SITE_URL=https://your-domain.example`** (also baked at build time) sets the
  marketing pages' canonical / OpenGraph URLs.

### Cloudflare settings that matter

- **Do not enable "Cache Everything"** on the app or API. It would cache authenticated
  responses and break sign-in. Cloudflare's **default** cache level (cache static assets by file
  extension) is correct: it safely caches Next.js content-hashed assets under `/_next/static`
  and leaves dynamic, authenticated responses uncached.
- **Mind the ~100s edge timeout.** On the free plan Cloudflare drops a **non-streamed** HTTP
  request that runs longer than about 100 seconds. Long answer/chat generations must therefore
  **stream** - which the app does by default (the chat endpoint emits `text/event-stream`), so
  tokens flow continuously and the request never idles past the limit. Keep streaming on and do
  not buffer the SSE response at any hop.

For the rest of the production hardening checklist, see
[TLS and production hardening](#tls-and-production-hardening) and
[`SECURITY.md`](./SECURITY.md#hardening-checklist).

---

## Compliance and data residency

This section is architectural fact, not legal advice; confirm specifics with your own
counsel and security team.

Because you host and control all data, nobody else holds, processes, or transmits your
knowledge. There is no service operator behind Third Brain: in data-protection terms the
project is **not a processor or sub-processor of your data** - which is the usual driver of a
SaaS vendor's SOC 1 / SOC 2 obligations for customer data. There is no shared cloud tenancy
and no third-party data component to audit.

What that means in practice:

- **Data residency is yours to set.** Your data lives wherever you run Postgres, Redis, and
  the upload directory. Pick the region and jurisdiction that fit your requirements.
- **Your compliance posture is yours to define.** Controls such as encryption at rest, access
  reviews, backup and retention policies, and audit-log shipping are configured and owned by
  you. Third Brain gives you the primitives (RBAC, an append-only audit log, encrypted
  secrets, tenant isolation); the operating posture is your deployment's.
- **No certification is claimed for your deployment.** Third Brain does not assert that an
  install is "SOC 2 certified" or otherwise compliant on your behalf. Compliance obligations
  follow data custody, and the custody - and therefore the responsibility - is yours.
- **The software is provided as-is under Apache-2.0.** There is no warranty and no support
  contract behind it; see [`LICENSE`](../LICENSE). Issues and pull requests are the support
  channel.

For the security model behind these controls, see [`SECURITY.md`](./SECURITY.md).
