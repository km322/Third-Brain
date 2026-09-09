# Changelog

All notable changes to Third Brain are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). One product
version covers the API, the worker, and the web app - they release together, and the root
[`VERSION`](VERSION) file is the source of truth.

## [Unreleased]

### Added

- **`robots.txt` and `sitemap.xml`**, generated at build time and deliberately opposite in
  the two builds. The published project site invites crawlers and points them at the
  sitemap; a self-hosted instance answers `Disallow: /`, because a company's private
  knowledge base should never be indexed if its origin is ever reachable. Neither build
  emitted a `robots.txt` at all before, which a crawler reads as "no rules".
- **The SIL Open Font License text now ships with the fonts it covers.**
  `apps/web/app/fonts` vendored Inter and JetBrains Mono but carried only a link to the
  licence, while the font files are redistributed three ways - in this repository, as
  `_next/static/media/*.woff2` on the published site, and inside the web image. Each
  licence now lives in `apps/web/public/fonts/`, the one directory that reaches all three
  (it is copied verbatim into the static export and into the image), so the site serves
  them at `/fonts/*-LICENSE.txt`. [`NOTICE`](NOTICE) lists both.
- **CI builds the static project site.** `npm run build:site` is the command Cloudflare
  Pages runs on every push, but nothing ran it in CI - so a page missing from the
  `PUBLISHED_PAGES` allowlist, or any component that cannot be statically exported, passed
  CI green and failed at the host instead, with no signal in the repository.
- **Dependabot watches the container base images.** `python:3.11-slim` and `node:22-alpine`
  are what every self-hoster pulls, and a CVE in either produced no pull request.
- **CI builds both images for `linux/arm64`** on a native arm64 runner. The release
  publishes a two-architecture manifest and hard-fails if a leg is missing, but CI built
  only the runner's native amd64 - so an arm64-only break first surfaced mid-release. Not
  yet a required status check; add `Docker build (arm64)` to branch protection to enforce it.
- **The MCP CLI is tested on Node 18**, the floor its `engines` field and its npm page
  promise. Nothing exercised it, so an API that throws on 18 would have shipped green.

### Fixed

- The security policy's supported-versions table still named `1.x` as the supported line
  after the 2.0 release, so the file GitHub surfaces told readers the current release was
  unsupported.
- **A failed release could burn an npm version permanently.** `npm-publish` depended only
  on `verify`, so it ran alongside the image build: if either architecture failed, the CLI
  was already live at a version that had no images and no GitHub release, and npm versions
  cannot be reused. It now runs after the manifests are merged, last in the pipeline.
- **A second push to `main` could block a release.** CI cancelled superseded runs on the
  same ref, but the release gate requires the tagged commit's CI run to have concluded
  `success` - a cancelled run concludes `cancelled` and no amount of re-running the release
  would clear it. Pushes to `main` are now grouped by commit SHA and never cancel each
  other; pull requests still cancel superseded runs.
- Two CI comments claimed the published GHCR packages are private. They are public and
  pullable; the actual reason that job builds from source is that the published tags are
  the *previous* release.
- **A quarantined document's extracted entities stayed browsable.** Every other surface
  withholds a document held for secret review - both retrievers, `/documents/{id}/chunks`,
  `/documents/{id}/content` and the MCP `get_document` tool - but the entity index applied
  only the ACL predicate. Entity names are lifted verbatim from document text, and
  quarantining an already-indexed document leaves its rows in place (the scanner gate
  returns before the resync), so `/entities` and `/entities/{id}/documents` still exposed
  them. Both now inherit the same `!= QUARANTINED` guard, pinned by an integration test.
- **`GET /answers` ran a permission resolution per answer.** Each call cost 2-3 queries,
  so listing answers was a query storm linear in the number of answers. The collection
  lookup is now memoised per request - answers cluster into far fewer collections than
  there are answers. The route still scans unbounded; see below.

### Changed

- **Linting runs the ESLint CLI instead of `next lint`**, which Next deprecates and removes
  in 16. Coverage is unchanged or wider, and a new `.eslintignore` mirroring
  `.prettierignore` keeps generated output from ever failing the lint job.
- **Next's build assets are frozen at the edge.** Their filenames are content hashes, but
  `_headers` carried no rule for them, so the host's short default TTL made every repeat
  visitor revalidate every script and stylesheet.
- **The release workflow drops to least privilege.** It granted `contents: write` and
  `packages: write` to every job; each job now takes only what it needs, so the third-party
  `docker/*` actions can no longer inherit the ability to push to the repository.

## [2.1.0] - 2026-09-07

Adds a static build of the project site so third-brain.ai can be served with no server
behind it, and removes code and configuration that nothing reached.

### Added

- `npm run build:site` in `apps/web` emits the marketing landing page and the docs page as
  plain HTML into `apps/web/out`, for any static host. A staged temp-directory build
  excludes the dashboard and auth routes, which would be dead shells without an API behind
  them, so the published site offers no affordance that cannot work. See
  [`docs/WEBSITE.md`](docs/WEBSITE.md). The standalone app build is unaffected.
- `POST /api/v1/invites` now returns `accept_url`. On a default self-host `EMAIL_PROVIDER`
  is a non-delivering stub, so the only copy of the invite token went nowhere while the
  dashboard claimed it had been emailed. The non-delivering providers now also log a
  warning saying so.
- Six operator-facing settings that were absent from `.env.example`: `WEB_SEARCH_BASE_URL`,
  `WEB_SEARCH_MAX_RESULTS`, `ENTITY_EXTRACTION_ENABLED`, `ENTITY_EXTRACTION_MAX_CHARS`,
  `MAX_REQUEST_BODY_BYTES` and `CLOUDFLARE_TUNNEL_TOKEN`.

### Fixed

- **Silent third-party egress in web search.** An unsupported `WEB_SEARCH_PROVIDER` fell
  through to the Tavily branch, POSTing a Tavily-shaped body carrying the operator's API
  key to `api.tavily.com`. Setting `WEB_SEARCH_PROVIDER=brave` with a Brave key sent that
  key to Tavily. The provider is now a closed `Literal` rejected at configuration load,
  with a loud failure as defence in depth, and two tests pin the behaviour. This matters
  for software that promises nothing phones home.

### Changed

- **Release images are built on native runners instead of under QEMU.** The multi-arch
  build emulated `linux/arm64` on an x86 runner, which died with SIGILL in July and then
  timed out three times on 2026-09-07, each time after 30 minutes. Each architecture now
  builds on its own runner - GitHub provides free native arm64 runners for public
  repositories - and the per-architecture digests are merged into one manifest, which is
  then checked to contain both architectures before the release is published.
- **Fonts are vendored instead of fetched from Google at build time.** `next/font/google`
  downloaded Inter and JetBrains Mono during `next build`, so every image build required
  reaching `fonts.googleapis.com`. That fails outright on an air-gapped or egress-restricted
  network, and it timed out and failed the arm64 release build. The latin-subset variable
  files now live in `apps/web/app/fonts` under the SIL OFL and load through
  `next/font/local`. The emitted font files are byte-identical, so rendering is unchanged.

### Removed

- Dead optional-RAG adapter scaffolding in the OpenAI-compatible layer, whose fallback
  branches were all unreachable.
- The `/backend/:path*` development proxy rewrite and its `API_URL` constant. Nothing
  called it.
- The `mypy` dependency and its `[tool.mypy]` section. Nothing ran it, and it did not run
  as configured. `CONTRIBUTING.md` no longer claims otherwise.
- Five unreferenced symbols: `cache_delete`, `ErrorResponse`, the `INTERNAL_ERROR`
  JSON-RPC constant, the SCIM `PATCH_SCHEMA` constant, and an unused audit action.

## [2.0.0] - 2026-09-03

Third Brain is free, open-source software. The whole product is in this repository under
Apache-2.0, self-hosting is the supported way to run it, and no part of it sits behind a
tier, a seat count or a payment. This release removes the pre-launch commercial surface that
implied otherwise, which is what makes it a major version: published API responses, the
migration history and the Compose topology all change.

### Breaking

- **One migration revision, `0001_initial`.** The history is folded into a single baseline
  whose `down_revision` is `None`; there is no chain to replay. A database created by a
  *released* 1.x still upgrades cleanly - every 1.x tag shipped only `0001_initial`, so such
  a database is already stamped there and `alembic upgrade head` finds nothing to do - but
  the fold removed the DDL that created `waitlist_entries` and `organizations.plan`, and
  Alembic will not drop objects a database already has. Nothing in 2.x reads either one; the
  idempotent SQL that brings an older database back to baseline parity is in
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). The case that actually fails is a database
  built from a **pre-2.0 development checkout**: it is stamped at a revision that no longer
  exists, and Alembic aborts with `Can't locate revision identified by '0002_drop_waitlist'`
  (or `'0003_drop_org_plan'`). Recreate that database, or - after verifying its schema
  matches the baseline - realign it with `alembic stamp --purge 0001_initial`.
- **The production Compose project is renamed to `third-brain-prod`**
  ([`docker-compose.prod.yml`](docker-compose.prod.yml)), so the production stack can no
  longer collide with the development stack's `third-brain` project. Compose namespaces
  volumes by project and both files declare `db_data` / `redis_data` / `uploads`, so up to
  1.x a `make up` followed by `make selfhost` on one host fought over the same datastores.
  Existing `third-brain_*` volumes are left untouched but orphaned, and the self-hosted stack
  comes up on an **empty** database. `scripts/selfhost-init.sh` detects exactly that case and
  prints the two ways out: discard leftover development data with `docker compose down -v`,
  or keep a pre-2.0 self-host by pinning the old project name with
  `COMPOSE_PROJECT_NAME=third-brain make selfhost`.
- **Every port in the development Compose file is published on loopback** - Postgres, Redis,
  the API, the web app, and the observability profile's Grafana and OTLP ports. That stack
  runs the `.env.example` defaults (a published `SECRET_KEY` and database password, and
  `ENVIRONMENT=development`, so the production boot guards never fire), and Docker's NAT
  rules are evaluated ahead of the host firewall, so a bare `5432:5432` on a machine with a
  public IP put an open database on the internet regardless of ufw/iptables. Reaching a
  development stack from another machine now needs an SSH tunnel; to publish deliberately,
  use `make selfhost`, which generates real secrets first.
- **The waitlist API is removed**: `POST /api/v1/waitlist` and `GET /api/v1/waitlist/stats`,
  with the model, schemas, tests and marketing form behind them, and the two third-party
  services that backed it - Cloudflare Turnstile bot verification (`TURNSTILE_SECRET_KEY`,
  `NEXT_PUBLIC_TURNSTILE_SITE_KEY`) and EmailJS submission notifications
  (`NEXT_PUBLIC_EMAILJS_*`). Those settings are gone from `.env.example` and the
  corresponding build arguments are gone from the web image, so a self-host build no longer
  carries either integration. The static `/demo` dashboard route is removed too: the recorded
  walkthrough in the landing page's `#demo` section is the demo, and there is no hosted
  workspace to preview.
- **`plan` is removed from the organization API.** There are no tiers, so an org has no plan:
  `GET /api/v1/orgs/*` no longer returns a `plan` field, and the `PlanTier` enum, the
  `Organization.plan` attribute, the `organizations.plan` column and the dashboard settings
  page's plan badge are all gone. Any client that read `plan` off an org response must stop.

### Added

- **The files an open-source project is expected to carry**: a
  [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), a [`NOTICE`](NOTICE) alongside the
  (unchanged) Apache-2.0 `LICENSE`, a [`.github/SECURITY.md`](.github/SECURITY.md) policy
  that routes vulnerability reports through GitHub private advisories instead of an email
  address, and issue / pull-request templates.
- **CI runs the self-host bring-up.** A `Self-host (prod compose + bootstrap)` job executes
  the real entry point - `ADMIN_EMAIL=... ./scripts/selfhost-init.sh`, which is what
  `make selfhost` invokes - building both images from source and asserting against the
  deployment it produces, then tearing it down. The supported install path had never been
  executed by CI before; it now runs on every push and pull request to `main`.

### Changed

- **Third Brain is now a fully open-source project.** The whole product is in this
  repository under Apache-2.0, free for any use: there is no hosted service, no waitlist, no
  tiers, no seat counts and no usage limits. **Self-hosting is the supported way to run it** -
  `make selfhost` stands up the production topology on your own box, and your documents,
  embeddings, provider keys and audit log never leave your perimeter. The README, the compose
  files and [`CONTRIBUTING.md`](CONTRIBUTING.md) were rewritten to match, the last one for
  outside contributors: how to bring the stack up, how to run the three test tiers, the
  "tests run against the real stack, never fakes" rule, the permission invariant any PR
  touching retrieval or ACLs must respect, and inbound-equals-outbound licensing (no CLA).
- **[`docs/VISION.md`](docs/VISION.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md) are project
  docs, not a pitch.** They keep why Third Brain exists and the technical intent, and drop
  the pricing tiers, business model, moat and monetization material.
- The usage and analytics `cost_usd` figures stay, and mean what a self-hoster needs them to
  mean: what your OWN OpenAI / Anthropic / Google keys are spending. Nothing in Third Brain
  ever charges anyone.
- **Documentation corrected against what the code actually does.** The README quickstart
  carries a warning that it runs on published development secrets and belongs on a laptop
  only. [`docs/SELF_HOSTING.md`](docs/SELF_HOSTING.md) keeps the "nothing phones home" claim
  but states it precisely - no server-side egress, telemetry, analytics, update check or
  licence check - and then names the two real exceptions: `/docs` and `/redoc` load their UI
  from a public CDN in the *operator's browser*, and `next/font/google` fetches webfonts once
  at image **build** time. [`docs/SECURITY.md`](docs/SECURITY.md) now says that audit entries
  record the peer address of the connection, which behind either documented public topology
  is the proxy rather than the user, and that the dashboard keeps both tokens in
  `localStorage` with no `script-src` CSP.
- **`apps/web` is prettier-formatted, and the format check is enforced** by `make lint` and
  by the frontend CI job, so the documented `make fmt` no longer greets a contributor with a
  125-file diff.
- **The five scaffolded connectors are labelled "Not syncing yet"** in the data-source
  picker, so the UI stops implying integrations that raise `ConnectorNotConfigured`.
- The signup 403 and its docstring are reworded. `SIGNUP_ENABLED` stays closed by default in
  production because an open instance lets strangers spend the operator's provider keys, not
  as an access gate.
- **The recorded demo is redacted**: the terminal exposed a local home directory, the end
  card pointed at a domain being decommissioned, and a caption claimed five connectors that
  do not sync.
- **The production Compose file sizes the database pools for one box.** `DB_POOL_SIZE` and
  `DB_MAX_OVERFLOW` default to 5 and 10 there (overridable from `.env`), because every
  uvicorn worker and the arq worker build their own pool: the app defaults of 10 + 20 peak at
  `(4 + 1) x 30 = 150` connections against the bundled Postgres's stock limit of 100.

### Fixed

- The quickstart in the README, [`CONTRIBUTING.md`](CONTRIBUTING.md) and the docs site uses
  `make up-d`, so the copy-pasted bring-up no longer blocks in the foreground before
  `make migrate`.
- **`scripts/selfhost-init.sh` survives a second run and a first mistake.** The publishing
  knobs it chooses (`TB_BIND_IP`, `WEB_PORT`, `API_PORT`) are written into the generated
  `.env` and read back from an existing one, so a later bare
  `docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml up -d` reproduces
  the same binding instead of silently reverting to the overlay defaults - and the no-TLS
  warning now fires on the effective bind rather than the one this run computed. A new
  pre-flight mirrors all three production boot guards in `app/main.py` (placeholder or short
  `SECRET_KEY`, the shipped `thirdbrain` database password, a bare `*` in
  `BACKEND_CORS_ORIGINS`) using the API's own wording, so an unusable `.env` fails in a
  second rather than after two image builds and a 300s health wait.
- **`make rollback` keeps the ingress it rolled back.** `scripts/rollback.sh` ran
  `docker compose -f docker-compose.prod.yml up -d` with the prod file alone, which recreates
  `api` and `web` from a definition that publishes no host ports - a rollback took the box
  off the network while `docker compose ps` still reported healthy containers. It now uses
  both files by default and takes `TB_COMPOSE_FILES` to name a different ingress overlay
  (`docker-compose.cloudflare.yml`).

### Security

- Four high-severity advisories in transitive web dependencies, fixed in the lockfile only
  (`brace-expansion`, `js-yaml`, `nanoid`); no direct dependency version changed.

## [1.0.4] - 2026-07-27

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
- The `third-brain-mcp` npm package's homepage points at `https://third-brain.ai/docs`;
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

[Unreleased]: https://github.com/km322/Third-Brain/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/km322/Third-Brain/releases/tag/v2.1.0
[2.0.0]: https://github.com/km322/Third-Brain/releases/tag/v2.0.0
[1.0.4]: https://github.com/km322/Third-Brain/releases/tag/v1.0.4
[1.0.3]: https://github.com/km322/Third-Brain/releases/tag/v1.0.3
[1.0.2]: https://github.com/km322/Third-Brain/releases/tag/v1.0.2
[1.0.1]: https://github.com/km322/Third-Brain/releases/tag/v1.0.1
[1.0.0]: https://github.com/km322/Third-Brain/releases/tag/v1.0.0
