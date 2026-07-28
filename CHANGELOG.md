# Changelog

All notable changes to Third Brain are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). One product
version covers the API, the worker, and the web app - they release together, and the root
[`VERSION`](VERSION) file is the source of truth.

## [Unreleased]

### Added

- A recorded three-minute product walkthrough, embedded on the marketing landing page as a
  new `#demo` section and reachable from a "Watch the demo" hero button and a "Demo" nav
  link. It is built not to cost anything until it is wanted: `preload="none"` so a visitor
  who never presses play never fetches the video, a 50 KB WebP poster as the only byte the
  page spends on it, and two renditions - 720p for viewports under 1024px, 1080p above -
  so a phone, whose frame is about 1020 device pixels wide, is not handed 1080p to decode
  for three minutes. The element is `muted` because the recording has no audio track at
  all. A second encode of the same walkthrough ships in the repo at
  [`docs/assets/third-brain-demo.mp4`](docs/assets/third-brain-demo.mp4); it replaces the
  placeholder image in the README, and [`docs/DEMO.md`](docs/DEMO.md) links to it.
### Changed

- **Documentation corrected against the code.** A full accuracy pass over the README and
  every file in `docs/`. The largest gaps: [`docs/API.md`](docs/API.md) documented 71 of
  the 127 registered REST routes, so ten shipped domains (invites, SSO, SCIM, answers,
  entities, conversations, data sources, governance, feedback, health) now have sections;
  the ingestion diagrams in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) omitted the
  secret-scan, DLP and entity-extraction stages, and the document status machine omitted
  `quarantined`; SSO/SAML and SCIM were listed as roadmap while shipped; and the stack was
  still described as Next.js 14 rather than 15. Two corrections change what a client should
  expect rather than only how it is described: the `third-brain` model alias is understood
  by `/v1/chat/completions` only (`/search/chat` forwards the id verbatim), and a
  conversation's `collection_ids` / `web_enabled` are stored preferences that do not scope
  retrieval.
- **`.env.example` documents nine settings it previously omitted**, including
  `APP_BASE_URL` (without which invite links point at `localhost:3000`), the transactional
  email/SMTP block that gates invite delivery, and the DLP knobs. `DATA_SOURCE_SYNC_ENABLED`
  is now described accurately: it gates on-demand syncs only, not the scheduled sweep.
- **Both `LICENSE` files carry the full Apache-2.0 terms.** The root file and the one
  published inside the `third-brain-mcp` npm package previously contained only the short
  copyright and AS-IS notice, not the license text. The license is unchanged - Apache-2.0 -
  but a copy of it is now actually distributed, as section 4(a) requires.
- **Public site corrections.** SSO and SCIM are presented as a shipped security control
  rather than roadmap; images are listed among the sources Third Brain can capture; and the
  public docs page no longer says PII content is quarantined by default (the secret scanner
  quarantines credentials; DLP classifies sensitivity and only quarantines when configured
  to).
- `public/media/*` is served `Cache-Control: public, max-age=31536000, immutable`. Next
  serves `public/` as `max-age=0`, which made the CDN in front of production revalidate,
  and re-stream the demo from the origin, on every play. The rendition is in the filename,
  so freezing it is safe.
- `make seed` prints plain text; the two emoji in its first-run output are gone.

### Fixed

- The web production image now ships `public/`. `next build`'s standalone output omits
  that directory, so without the extra `COPY` anything served from the site root - the
  demo video and its poster included - would have 404'd in production. The copy is the
  first layer in the runner stage, so the media is not re-materialised on every release.

## [1.0.3] - 2026-07-26

### Security

- Dependency upgrades to pick up upstream security fixes: Next.js 15.5.21, and the
  `sharp` image library pinned to >= 0.35.3 via an override.

## [1.0.2] - 2026-07-26

### Changed

- The marketing site and public docs page no longer link to the private GitHub
  repository: the footer points at the site's own docs, and the public quick start now
  follows the managed flow (join the waitlist, then `npx third-brain-mcp connect`)
  instead of a self-host clone visitors cannot perform.
- The `third-brain-mcp` npm package's homepage points at https://third-brain.ai/docs;
  its `repository` field (which rendered a dead link on npmjs.com) is gone.

### Removed

- Four inert settings nothing read: `SSO_ENABLED`, `SCIM_ENABLED`, `FEEDBACK_ENABLED`,
  and `DATA_SOURCE_SYNC_INTERVAL_MINUTES`. The SSO, SCIM, feedback, and connector-sync
  features themselves are unchanged.
- Unused dependencies: `mcp`, `tenacity`, and `markdown-it-py` from the API runtime,
  `faker` from the dev extras.

## [1.0.1] - 2026-07-26

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
- **Ingestion pipeline** - extract → scan (secrets, DLP) → chunk → embed → index → enrich,
  running in arq workers; accepts files (PDF, DOCX, MD, HTML, TXT, CSV), raw text, and
  URLs. Uploaded images (PNG/JPEG/GIF/WebP) are described, and their legible text
  transcribed, by a vision model, then indexed with a capability link back to the original
  bytes.
- **Data sources** - external sync that mirrors source-system ACLs into Third Brain
  grants, so the permission gate holds end to end. Ships a reference `local_folder`
  connector; Google Drive, Slack, GitHub, Notion and Confluence can be configured but
  their live fetch is not built yet.
- **Hybrid retrieval** on Postgres + pgvector - vector ANN fused with keyword search via
  reciprocal rank fusion, with Redis-cached query embeddings.
- **Curated Answers + verification** - authoritative Q&A surfaced above raw retrieval
  hits, with review intervals and an hourly sweep that flips anything past its review-by
  date to stale.
- **Entity extraction** - named entities indexed at ingestion and browsable, with the
  permission scope applied so an entity only appears when the caller can see a document
  mentioning it.
- **Knowledge graph** - a permission-scoped document-similarity graph, plus per-document
  nearest neighbors.
- **Conversations** - multi-turn chat history that grounds follow-up questions without
  widening what the caller can retrieve.
- **Optional web grounding** - a pluggable web-search provider (off by default) whose
  results are cited alongside internal passages.
- **Dashboard** (Next.js) - usage analytics, API keys, teams, connectors, document
  management, and audit logs.
- **Authentication** - JWT access/refresh tokens for the dashboard and SHA-256-hashed API
  keys with scopes for programmatic access. Every authenticated principal is rate limited:
  API keys per key, dashboard sessions per user (`SESSION_RATE_LIMIT_PER_MINUTE`).
  Self-serve signup is closed by default in production (`SIGNUP_ENABLED`), since a new org
  without its own connector would otherwise bill the deployment's platform provider keys.
- **SSO + SCIM** - OIDC and signature-verified SAML 2.0 sign-in with just-in-time
  provisioning, plus SCIM 2.0 provisioning of users and of identity-provider groups as
  teams, authenticated by per-org SCIM tokens.
- **Email invites** - an admin invites an address, the recipient accepts through a
  tokenised link, and delivery goes through a pluggable email provider that defaults to
  an offline stub.
- **No well-known demo credential** - `make seed` generates a random password per seed and
  prints it once; the three demo addresses are configurable (`DEMO_ADMIN_EMAIL`,
  `DEMO_ENGINEER_EMAIL`, `DEMO_VIEWER_EMAIL`).
- **Secret quarantine** - uploaded documents and agent captures are scanned for
  credentials (and optionally PII via the DLP scanner) and parked for human review instead
  of being indexed.
- **Oversharing report** - an admin view of the documents the DLP scan classified as
  sensitive that are nonetheless visible org-wide or publicly.
- **Knowledge gaps and answer feedback** - thumbs up/down on an answer, and an admin
  report aggregating zero-result and thumbs-down queries. Retaining the raw query text is
  opt-in and off by default.
- **Observability** - structured logging via structlog and opt-in OpenTelemetry tracing.
- **Offline deterministic LLM stub** - the whole stack builds, seeds, and passes tests
  with zero provider keys.

[Unreleased]: https://github.com/km322/Third-Brain/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/km322/Third-Brain/releases/tag/v1.0.3
[1.0.2]: https://github.com/km322/Third-Brain/releases/tag/v1.0.2
[1.0.1]: https://github.com/km322/Third-Brain/releases/tag/v1.0.1
[1.0.0]: https://github.com/km322/Third-Brain/releases/tag/v1.0.0
