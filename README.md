# Third Brain

### The documentation writes itself.

<!-- Badges - replace placeholders once repo/CI/registry URLs are public -->
[![CI](https://github.com/km322/Third-Brain/actions/workflows/ci.yml/badge.svg)](https://github.com/km322/Third-Brain/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](apps/api)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-000000.svg)](apps/web)

**Third Brain turns the work your team already does with LLMs into documentation.** Connect
its MCP server to the tools you already use - Claude Desktop, Claude Code, Cursor, your own
agents - and the decisions, answers, and notes that would normally evaporate at the end of a
chat get captured into a governed, searchable company brain. A dashboard shows the team what
the agents wrote. Retrieval is permission-aware and works with every model.

---

## The problem

Most of a company's real knowledge is created in conversations - a decision made in a Claude
thread, an answer an agent worked out, a runbook a teammate reasoned through with Cursor. And
almost none of it gets written down. The session ends, the tab closes, and the knowledge is
gone. Nobody writes the doc, because writing the doc is the boring part nobody has time for.

So the same questions get re-answered, decisions get re-litigated, and every new hire re-learns
what someone already figured out last quarter. The generation is easy now; the hard part is
that nothing captures it, nothing governs who can see it, and nothing lets the next person find
it.

## What it does

**Third Brain captures the work as it happens and makes it a governed, searchable brain.**

- **Documentation writes itself.** Point your agents' MCP client at Third Brain and, as they
  work, they call `add_knowledge` / `update_knowledge` to capture decisions, answers, and notes
  into the right collection. The write-back path runs through the same secret/DLP quarantine and
  the same permission model as everything else, so agents can only write where they're allowed
  to and never persist a leaked credential.
- **Governed retrieval you can trust.** org -> team -> user RBAC plus per-collection and
  per-document ACLs, enforced *at retrieval time* and pushed into the SQL `WHERE` clause. A chunk
  you can't see can never enter a search result or a prompt - not even indirectly. This is the
  trust pillar under everything the agents write.
- **Works with every model.** Any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama,
  vLLM, or any compatible gateway) plus native connectors for **Anthropic** and **Google
  Gemini**, all over plain `httpx`, plus a deterministic **offline stub** so the whole stack runs
  with zero keys. Bring your own keys; we never mark up tokens.
- **A dashboard that tracks what agents wrote.** Usage analytics, API keys, teams, connectors,
  audit logs, and a Documents view with an **Agent-written** filter so you can see exactly what
  your agents have been capturing.
- **Built for speed.** Postgres + `pgvector` for permission-aware semantic (and hybrid) search,
  Redis caching hot queries and embeddings, and arq workers handling ingestion.

Think of it as gateway + dashboard ergonomics, but for **knowledge** instead of tokens - and the
knowledge fills itself in as your team works.

---

## 30-second quickstart

```bash
# 1. Copy environment templates (works fully offline - an included deterministic
#    offline stub provider means you don't even need a real API key to try it)
cp .env.example .env

# 2. Bring up Postgres(+pgvector), Redis, API, worker and web
make up            # or: docker compose up --build

# 3. Run migrations + seed a demo org and knowledge base
make migrate && make seed

# API      -> http://localhost:8000  (interactive docs at /docs)
# Web/app  -> http://localhost:3000
```

Then sign in to the dashboard with the seeded demo login: `admin@example.com`, plus the
password `make seed` just printed (randomly generated per seed - set `DEMO_PASSWORD` and the
`DEMO_ADMIN_EMAIL` / `DEMO_ENGINEER_EMAIL` / `DEMO_VIEWER_EMAIL` addresses in `.env` before
seeding to choose your own).

> [!TIP]
> Add a real `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` to `.env` for real
> embeddings and answers - but everything builds, seeds, and passes tests with zero keys thanks
> to the offline stub provider. The stub's embeddings are deterministic placeholders, so
> **offline** search rankings and answers exercise the full pipeline end-to-end but aren't a
> measure of retrieval *quality* - set a key to judge relevance.

<!-- TODO: replace with a dashboard screenshot / asciicast -->
<p align="center"><img alt="Third Brain dashboard - screenshot / asciicast placeholder" src="https://placehold.co/1200x680/0b0b0f/e5e5e5?text=Third+Brain+dashboard+%E2%80%94+demo+coming+soon" width="820"></p>

---

## Self-hosting (on the roadmap)

Managed Third Brain is the product today. This repo does include a one-command compose
overlay (`make selfhost`, see [SELF_HOSTING.md](docs/SELF_HOSTING.md)) that stands the full
stack up on your own box for evaluation, but self-hosting isn't a supported offering yet.
Running it in your own infrastructure - your documents, embeddings, keys, and audit log
never leaving your perimeter - is planned as a supported product once we're resourced to do
it well. See the [roadmap](docs/ROADMAP.md).

---

## Connect your tools in one command

Third Brain ships a small CLI, **`third-brain-mcp`**, that wires the tools your team already
uses into the brain. No config files to hand-edit.

```bash
# 1. Connect this machine to your Third Brain. Prompts for the server URL (the API origin,
#    e.g. https://api.third-brain.ai), then runs a device flow: it prints a short code
#    (e.g. KTPB-3947), opens https://third-brain.ai/activate in your browser, an admin
#    approves it, and a scoped API key is minted and saved to ~/.third-brain/config.json.
#    In CI, pass --api-key instead of the browser step.
npx third-brain-mcp connect

# 2. Wire it into the client you use. `install claude` writes the Claude Desktop config;
#    `install cursor` does the same for Cursor; `install claude-code` prints the
#    `claude mcp add` one-liner to paste.
npx third-brain-mcp install claude

# (status sanity-checks the saved connection; serve is the stdio-to-HTTP bridge the MCP
#  client launches for you - you rarely run it by hand.)
npx third-brain-mcp status
```

Once connected, your agents get the brain's tools - `search_knowledge`, `get_document`,
`list_collections`, `add_knowledge`, `update_knowledge` - so they can both **answer from** and
**write to** the company brain, always within the caller's permissions. Ask your assistant to
*"write up the decision we just made into Engineering / Decisions"* and it lands in the brain,
scanned and scoped, for the next teammate to find.

---

## The trust pillar: governed retrieval

Everything an agent writes is only as safe as what it can read. Third Brain enforces access in
**one** place, [`apps/api/app/services/permissions.py`](apps/api/app/services/permissions.py),
used by both route authorization and retrieval. At query time the visibility predicate is pushed
into the SQL `WHERE` clause, so a chunk the caller can't see never enters a search result, an LLM
prompt, or a citation. Content that looks like it contains secrets is parked for human review
instead of being indexed. See [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md) and
[`docs/SECURITY.md`](docs/SECURITY.md).

## Works with every model

The same permission-aware brain is reachable through interfaces your tools already speak, so you
rarely write glue code.

**MCP server - for agents and desktop clients** (Claude Desktop, Claude Code, Cursor, custom
agents). The `third-brain-mcp` CLI above is the fastest path; the raw endpoint is a single
JSON-RPC surface at `POST /mcp` if you'd rather configure a client by hand
(see [`docs/API.md`](docs/API.md)).

**OpenAI-compatible `/v1` - for any OpenAI SDK or tool.** Point `base_url` at Third Brain and
your existing code gets **grounded, permission-filtered** answers with inline citations:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="tb_your_api_key")
resp = client.chat.completions.create(
    model="third-brain",
    messages=[{"role": "user", "content": "What's our on-call escalation policy?"}],
)
print(resp.choices[0].message.content)  # answered from *your* knowledge, within *your* ACLs
```

On the generation side, orgs register **Connectors** for any OpenAI-compatible endpoint plus
native **Anthropic** and **Google Gemini**, all through one plain-`httpx` LLM layer - no provider
SDKs - with the offline stub covering the zero-key case. Prefer raw HTTP? The native REST API
under `/api/v1` gives you full CRUD plus `/search` and `/search/chat`. See
[`docs/API.md`](docs/API.md).

---

## Releases

Third Brain is versioned with [SemVer](https://semver.org) - one product version covers
the API, worker, and web app (see [`VERSION`](VERSION) and [`CHANGELOG.md`](CHANGELOG.md)).
Released images are published to `ghcr.io/km322/third-brain-api` and
`ghcr.io/km322/third-brain-web`; the release + rollback runbook is
[`docs/RELEASING.md`](docs/RELEASING.md).

---

## Repository layout

```
third-brain/
├── apps/
│   ├── api/            # FastAPI backend (REST + OpenAI-compatible + MCP)
│   │   ├── app/
│   │   │   ├── core/         # config, db, redis, security, dependencies
│   │   │   ├── models/       # SQLAlchemy models (the data contract)
│   │   │   ├── schemas/      # Pydantic request/response schemas
│   │   │   ├── api/routes/   # HTTP route modules
│   │   │   ├── services/     # business logic (auth, rbac, ingestion, rag, llm, ...)
│   │   │   ├── workers/      # arq background workers (ingestion pipeline)
│   │   │   └── mcp/          # Model Context Protocol server
│   │   ├── alembic/          # migrations
│   │   └── tests/
│   └── web/            # Next.js 14 frontend (marketing site + dashboard)
│       ├── app/
│       │   ├── (marketing)/  # landing, ...
│       │   ├── (auth)/       # login, signup
│       │   └── dashboard/    # the app
│       ├── components/
│       └── lib/
├── docs/               # architecture, API, permissions, security, vision & roadmap
├── .github/workflows/  # CI (lint, unit + real-infra integration tests, e2e)
└── docker-compose.yml  # one-command local stack (prod topology: docker-compose.prod.yml)
```
(Dockerfiles live next to each app: `apps/api/Dockerfile`, `apps/web/Dockerfile`.)

## Documentation

| Doc | What's inside |
|---|---|
| [`docs/VISION.md`](docs/VISION.md) | Why Third Brain exists, market, business model & moat |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Near / mid / long-term plans |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full system design |
| [`docs/API.md`](docs/API.md) | REST, OpenAI-compatible & MCP surface, plus the CLI + device auth |
| [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md) | The permission model in depth |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model & disclosure policy |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Running it in production |
| [`docs/SELF_HOSTING.md`](docs/SELF_HOSTING.md) | Running it on your own infrastructure - your data stays yours |
| [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) | Structured logging, tracing & debugging recipes |
| [`docs/SCALING.md`](docs/SCALING.md) | Scaling the stack under load |
| [`docs/LOAD_TESTING.md`](docs/LOAD_TESTING.md) | Proving the stack at millions-of-chunks scale |
| [`docs/BENCHMARKING.md`](docs/BENCHMARKING.md) | Measuring retrieval, answer, and permission-correctness quality |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Running pieces without Docker |
| [`docs/DEMO.md`](docs/DEMO.md) | A 3-minute scripted walkthrough |

## License

Apache-2.0 - see [LICENSE](LICENSE).
