# Third Brain - Roadmap

> **The documentation writes itself.** This is the "what next." For the "why," see
> [`VISION.md`](./VISION.md); for the "how," see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

This roadmap is directional, not a commitment of dates. Items ship when they clear the bar for
correctness - and, for anything in the retrieval path, the permission gate stays provably intact.
Current version: **2.1.0**.

Third Brain is free and Apache-2.0. Everything listed as shipped is in this repository and
available to everyone: there are no tiers, no paid edition, and nothing is held back for a hosted
version. Items tagged *(contributions welcome)* below are self-contained enough to pick up - see
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

The whole roadmap at a glance - each column is a section below:

```mermaid
timeline
    title Third Brain roadmap
    Where we are today (shipped)
        : Agentic write-back (agents auto-document via MCP)
        : Permission-aware retrieval
        : Multi-provider LLM layer (OpenAI-compatible + Anthropic + Gemini)
        : Three ways in (REST, OpenAI-compatible /v1, MCP)
        : third-brain-mcp CLI + device-code auth
        : Agent-written tracking (dashboard)
        : Ingestion (text, URL, file upload, images)
        : Data sources (local_folder + ACL mirroring)
        : Hybrid search
        : Dashboard
        : SSO / SAML + SCIM
        : One-command self-host (make selfhost)
    Near-term (next ~2 quarters)
        : Harden for production
        : Live data-source connectors (Slack, Google Drive, Notion)
        : Reranking
        : Gate releases on the eval harness
        : Email verification and self-serve password reset
    Mid-term (~2-4 quarters out)
        : Connector ecosystem
        : Advanced governance
        : Deployment recipes beyond Compose (Helm / Kubernetes)
        : Reviewable agent write-back (suggested edits, provenance, approval)
        : Richer analytics
        : Client SDKs
    Long-term (vision-scale)
        : The compounding brain
        : Cross-org knowledge federation
        : Automatic knowledge maintenance
        : Multimodal knowledge
        : Region/edge deployment and data-plane isolation
        : Vertical governance packs
```

---

## Where we are today (shipped)

The baseline the roadmap builds on - all of this is in the repo and covered by tests:

- **Agentic write-back (the documentation writes itself)** - agents connected over MCP call
  `add_knowledge` / `update_knowledge` to capture decisions, answers, and notes as they work.
  Writes pass through the same secret/DLP quarantine and the same permission model as everything
  else, so an agent only writes where it's allowed and never persists a leaked credential.
- **Permission-aware retrieval** - org → team → user RBAC plus per-collection and per-document
  ACLs, computed once per request and pushed into the SQL `WHERE` clause. A chunk you can't see
  never enters a prompt. This is the trust pillar the write-back path stands on.
- **Multi-provider LLM layer** - BYO-keys connectors for any OpenAI-compatible endpoint (OpenAI,
  Azure OpenAI, Ollama, vLLM, or any compatible gateway) plus native **Anthropic** and **Google
  Gemini**, all over plain `httpx` with no provider SDKs; a deterministic offline stub provider so
  the whole stack runs with zero keys.
- **Three ways in** - native REST API under `/api/v1`, an OpenAI-compatible `/v1` endpoint, and a
  first-class **MCP server** at `/mcp` with read *and* write tools (`search_knowledge`,
  `get_document`, `add_knowledge`, `update_knowledge`, `list_collections`).
- **`third-brain-mcp` CLI + device-code auth** - `npx third-brain-mcp connect` runs an OAuth-style
  device flow (short user code, admin approval at `/activate`, a scoped key minted once), and
  `install claude` / `cursor` / `claude-code` wire the brain into those clients in one command.
- **Agent-written tracking** - the dashboard Documents page carries an **Agent-written** filter and
  badge, and the Overview shows a **Written by agents** card, so a team can see exactly what its
  agents captured.
- **Ingestion** - text, URL, and file upload, chunked and embedded by arq workers into
  Postgres + `pgvector`. Raster images are described and transcribed by a vision model, then
  indexed as text carrying a capability link back to the original bytes.
- **Data sources** - external sync that mirrors a source system's ACLs into Third Brain grants, so
  the one permission engine still enforces them. The reference `local_folder` connector is live;
  Google Drive, Slack, GitHub, Notion and Confluence validate and store their configuration, but
  their live fetch is not built yet.
- **Hybrid search** - vector similarity fused with keyword search via reciprocal rank fusion.
- **Dashboard** - usage analytics, spend, scoped API keys, teams, connectors, document management,
  and audit logs (Next.js 15).
- **SSO / SAML + SCIM** - OIDC and SAML 2.0 sign-in with just-in-time provisioning (SAML
  assertions are signature-verified against the connection's IdP certificate), plus SCIM 2.0
  provisioning of users and of IdP groups as teams. Both are set up from the dashboard's
  **SSO & SCIM** page.
- **Sensitivity classification + oversharing report** - a DLP scan labels ingested content
  `pii` / `confidential` (`DLP_ENABLED`, on by default; `DLP_DEFAULT_ACTION` picks label,
  quarantine, or warn), and an admin-only report lists sensitive documents that are nonetheless
  org- or public-visible.
- **Verification & freshness** - documents and curated answers can be marked verified with a
  review-by date (the interval defaults to `DEFAULT_REVIEW_INTERVAL_DAYS`); an hourly worker flips
  anything past that date to *stale* so the dashboard can prompt a re-review.
- **Knowledge gaps** - zero-result and thumbs-down queries are aggregated into a dashboard
  Knowledge Gaps report, so an admin can see what the brain cannot answer yet. Retaining the raw
  query text is opt-in and off by default.
- **Evaluation & load-test harnesses** - a golden-dataset retrieval + permission-correctness
  benchmark (`make benchmark`; exits non-zero on **any** permission leak - see
  `docs/BENCHMARKING.md`) and a Locust load test with pass/fail latency/error thresholds
  (`make load-test` / `make load-seed` - see `docs/LOAD_TESTING.md`). They run on demand today;
  wiring them into CI as release gates is near-term.
- **One-command self-host** - `make selfhost` generates a hardened `.env`, brings the production
  stack up on a single box, runs migrations, and bootstraps the first admin. Published images
  (`ghcr.io/km322/third-brain-api` and `-web`) plus Cloudflare Tunnel and single-box Compose
  overlays cover the common ingress shapes. See [`SELF_HOSTING.md`](./SELF_HOSTING.md).

---

## Near-term (next ~2 quarters)

The focus: harden what exists, then unlock the two things that gate retrieval quality - live
connectors and better ranking.

- **Harden for production.** Tighten rate-limit and quota enforcement, expand test
  coverage on the permission boundary, structured audit events for every write, background-job
  retries/dead-lettering, and running the existing load/soak harness on a schedule.
- **Finish SCIM group sync.** SSO and SCIM ship today (see above), but there is no
  `PATCH /scim/v2/Groups/{id}` - the call an IdP makes to push membership changes after a group
  already exists - so a team only tracks its IdP group as of creation time.
- **Live data-source connectors - Slack, Google Drive, Notion.** Incremental sync that ingests
  from source systems and, critically, **maps source-side permissions onto Third Brain ACLs** so
  the permission gate holds end-to-end. The sync engine, source-ACL mapping and identity
  resolution already ship behind the reference `local_folder` connector (see **Data sources** in
  [`API.md`](./API.md)); this work adds the live fetch for each source. *(Contributions welcome -
  one connector at a time is a natural unit of work.)*
- **Reranking.** A cross-encoder / LLM reranking stage on top of hybrid retrieval to lift
  precision@k on the shortlist before it reaches the model.
- **Gate releases on the eval harness.** The retrieval-quality *and* permission-correctness eval
  already exists (`make benchmark`); the near-term work is wiring it into CI so every release is
  blocked on it - the permission suite is non-negotiable, since a single leaked chunk destroys
  trust - and publishing the permission-correctness benchmarks.
- **Email verification and self-serve password reset** - both things an operator needs so
  members can recover their own accounts instead of asking an admin. Neither is shipped; only
  an admin can reset a member's password today. The delivery pipeline underneath them *does*
  ship: org invites send a tokenised acceptance link through the pluggable `EMAIL_PROVIDER`,
  which defaults to a `stub` that captures mail instead of sending it, so a deployment that
  wants invites delivered sets `EMAIL_PROVIDER=smtp` (see [`DEPLOYMENT.md`](./DEPLOYMENT.md)).
- **Runtime web configuration.** Move the web client's API base URL from the build-time
  `NEXT_PUBLIC_API_URL` to server-provided runtime config, so one published web image can serve
  any deployment instead of being baked per-origin. *(Contributions welcome - it would let every
  self-hoster run the published web image unmodified; see the
  [caveat](./RELEASING.md#published-web-image-caveat).)*

## Mid-term (~2-4 quarters out)

- **Connector ecosystem.** GitHub, Confluence, Jira, Linear, Zendesk, Salesforce, and a stable
  connector SDK so anyone can add sources - every connector carries its permission mapping.
- **Advanced governance.** Retention/expiry policies, data-residency controls, PII **redaction**,
  per-collection encryption, and legal-hold. Detection and the oversharing report already ship;
  what is missing is rewriting the sensitive spans out of what gets indexed.
- **Deployment recipes beyond Compose.** A Helm chart / Kubernetes manifests alongside today's
  Compose overlays, so a self-host scales past one box without hand-writing the topology from
  [`DEPLOYMENT.md`](./DEPLOYMENT.md). *(Contributions welcome.)*
- **Reviewable agent write-back.** Agents already write back today (`add_knowledge` /
  `update_knowledge`, gated by permissions and the secret scanner). The mid-term layer adds
  structure on top: suggested edits, richer provenance, and human-in-the-loop approval queues so
  higher-stakes contributions can be reviewed before they land.
- **Richer analytics.** Retrieval-quality dashboards, provider-spend attribution by team and
  collection, and corpus-level coverage analysis on top of today's query-driven Knowledge Gaps
  report.
- **Client SDKs.** First-class Python and TypeScript SDKs with typed clients, streaming, and MCP
  helpers. The `third-brain-mcp` CLI shipped first as the fastest way to wire agents in; typed
  SDKs are the next layer for teams building directly against the REST/OpenAI-compatible surfaces.

## Long-term (vision-scale)

- **The compounding brain.** A deduplicated, permission-tagged knowledge graph per deployment that
  gets more useful the longer it runs, because every agent session leaves something behind.
- **Cross-org knowledge federation.** Opt-in, permission-preserving sharing between organizations
  (e.g. a company and its vendors) without collapsing either side's ACLs.
- **Automatic knowledge maintenance.** Conflict resolution across sources and proactive "this doc
  contradicts that one" surfacing, on top of today's review-date staleness flagging.
- **Multimodal knowledge.** Audio and video ingested, embedded, and retrieved under the same
  permission gate. Images already ship.
- **Region/edge deployment & data-plane isolation** for the most regulated operators (fintech,
  health, legal, public sector).
- **Vertical governance packs.** Prebuilt policy, retention, and compliance templates (HIPAA, SOC 2,
  FedRAMP-aligned) that shorten security review from months to days.

---

## How work is prioritized

1. **Permission correctness first.** Anything touching retrieval ships behind the eval harness;
   the permission gate never regresses.
2. **Then whatever a self-hoster is blocked on.** Setup, upgrade, and operational papercuts beat
   new surface area: the project is only useful if a stranger can stand it up and keep it running.
3. **Retrieval quality compounds.** Connectors and reranking make the brain worth plugging every
   model into - which drives the write-back loop that makes it better still.

## Contributing

Third Brain is developed in the open and every part of it is fair game. Especially welcome:

- **Data-source connectors.** The sync engine, source-ACL mapping and identity resolution already
  exist behind `local_folder`; a new source is mostly a fetch implementation plus its permission
  mapping.
- **Extractors and chunkers** for file types the ingestion pipeline does not handle well yet.
- **Deployment recipes** - Helm, Kubernetes manifests, Terraform, or a one-click template for a
  cloud you use.
- **Retrieval evaluation.** New golden-dataset cases for `make benchmark`, particularly adversarial
  permission cases: a case that catches a leak is the single most valuable contribution here.
- **Documentation and rough edges** you hit while standing your own instance up.

Have a request, a source you need connected, or a deployment that fought you? Open an issue at
<https://github.com/km322/Third-Brain/issues> - the roadmap is shaped by what people actually run
into. Start with [`CONTRIBUTING.md`](../CONTRIBUTING.md) and
[`DEVELOPMENT.md`](./DEVELOPMENT.md).
