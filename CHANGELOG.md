# Changelog

All notable changes to Third Brain are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). One product
version covers the API, the worker, and the web app - they release together, and the root
[`VERSION`](VERSION) file is the source of truth.

## [Unreleased]

### Added

- **Capture consent and narration** - the MCP `add_knowledge` / `update_knowledge` tool
  descriptions and the server's `initialize` instructions now direct agents to ask the
  user for permission before saving personal, private, or secret-looking content, and to
  tell the user what was saved (title and collection) after every capture or update.
  Pinned by unit tests on every surface.
- **CLI agent-write disclosure** - `third-brain-mcp connect` and `install` print a
  heads-up that connected agents can write to the organization's knowledge base, with a
  pointer to the dashboard's *Written by agents* view.

### Changed

- `docker-compose.prod.yml` on its own now publishes no host ports; ingress comes from an
  overlay - `docker-compose.cloudflare.yml` (Cloudflare Tunnel, what third-brain.ai runs)
  or `docker-compose.selfhost.yml` (direct local ports) - or your own reverse proxy.
  Deployment and self-hosting docs rewritten to match.
- `GIT_COMMIT` is a plain setting; the `RENDER_GIT_COMMIT` alias is gone.

### Removed

- The **Render blueprint** (`infra/render.yaml`) and the **Caddy reverse proxy** path
  (`infra/Caddyfile`, the `caddy` service and `with-caddy` profile, and the
  `WEB_DOMAIN` / `API_DOMAIN` / `ACME_EMAIL` settings). Neither was used by any
  supported deployment.

## [1.0.0] - 2026-07-25

Initial release.

### Added

- **Proactive agent capture** - the MCP `add_knowledge` tool takes an optional
  `collection`: agents record decisions and answers as they work, and Third Brain files
  each capture into the best-matching editable collection automatically (an org-wide
  "Decisions" collection is created on first use when nothing else fits). Captures carry
  an optional `doc_type` (`decision`, `solution`, `answer`, `note`, `reference`).
- **Agent-written tracking** - an *Agent-written* filter and badge on the dashboard
  Documents page and a *Written by agents* card on the Overview, so teams can see what
  their agents captured.
- **`third-brain-mcp` CLI** - `npx third-brain-mcp connect` wires a machine to a Third
  Brain via a device-code sign-in; `install claude` / `cursor` / `claude-code` configure
  those clients; `serve` bridges stdio MCP clients to the `POST /mcp` endpoint; `status`
  sanity-checks the connection.
- **Device-code authorization** - `POST /api/v1/device-auth` and `/device-auth/token` for
  the CLI, plus admin-only `pending` / `approve` / `deny` session endpoints. Codes are
  hashed, expiring, and single-use; the minted key is admin-approved, scoped, and returned
  exactly once.
- **Native Anthropic and Google Gemini providers** - alongside any OpenAI-compatible
  endpoint and the deterministic offline stub. Connector types are `openai`,
  `azure_openai`, `anthropic`, `google`, `ollama`, and `custom`; the LLM layer adapts each
  provider's native REST protocol behind one plain-`httpx` interface (no provider SDKs).
  Anthropic is completions-only; Gemini does completions and embeddings
  (`gemini-embedding-001`).
- **Permission-governed retrieval** - org → team → user RBAC plus per-collection and
  per-document ACLs, computed once per request and pushed into the SQL `WHERE` clause, so
  a chunk the caller cannot see never enters a search result or prompt.
- **Three integration surfaces** - native REST API under `/api/v1`, an OpenAI-compatible
  `/v1` endpoint (chat completions + embeddings), and an MCP server at `/mcp` with read
  and write tools.
- **Ingestion pipeline** - extract → chunk → embed → index, running in arq workers;
  accepts files (PDF, DOCX, MD, HTML, TXT, CSV), raw text, and URLs.
- **Hybrid retrieval** on Postgres + pgvector - vector ANN fused with keyword search via
  reciprocal rank fusion, with Redis-cached query embeddings.
- **Dashboard** (Next.js) - usage analytics, API keys, teams, connectors, document
  management, and audit logs.
- **Authentication** - JWT access/refresh tokens for the dashboard and SHA-256-hashed API
  keys with scopes for programmatic access. Every authenticated principal is rate limited:
  API keys per key, dashboard sessions per user (`SESSION_RATE_LIMIT_PER_MINUTE`).
  Self-serve signup is closed by default in production (`SIGNUP_ENABLED`), since a new org
  without its own connector would otherwise bill the deployment's platform provider keys.
- **No well-known demo credential** - `make seed` generates a random password per seed and
  prints it once; the three demo addresses are configurable (`DEMO_ADMIN_EMAIL`,
  `DEMO_ENGINEER_EMAIL`, `DEMO_VIEWER_EMAIL`).
- **Secret quarantine** - uploaded documents and agent captures are scanned for
  credentials (and optionally PII via the DLP scanner) and parked for human review instead
  of being indexed.
- **Observability** - structured logging via structlog and opt-in OpenTelemetry tracing.
- **Offline deterministic LLM stub** - the whole stack builds, seeds, and passes tests
  with zero provider keys.

[Unreleased]: https://github.com/km322/Third-Brain/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/km322/Third-Brain/releases/tag/v1.0.0
