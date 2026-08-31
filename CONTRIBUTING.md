# Contributing to Third Brain

Third Brain is free and open source under [Apache-2.0](LICENSE), and contributions are
welcome from anyone - bug reports, documentation, and code alike. This guide covers how to
set up your environment, the conventions we follow, and how to get a change merged.

- [Code of conduct](#code-of-conduct)
- [Ways to contribute](#ways-to-contribute)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Development workflow](#development-workflow)
- [Coding standards](#coding-standards)
- [Backend conventions](#backend-conventions)
- [Frontend conventions](#frontend-conventions)
- [Tests](#tests)
- [The permission invariant](#the-permission-invariant)
- [Commits & pull requests](#commits--pull-requests)
- [Licensing of contributions](#licensing-of-contributions)
- [Database migrations](#database-migrations)
- [Reporting bugs & security issues](#reporting-bugs--security-issues)

---

## Code of conduct

Everyone taking part in this project - issues, pull requests, discussions - is expected to
follow [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). In short: be respectful and constructive,
assume good intent, review the code and not the person.

---

## Ways to contribute

- **Report a bug**: open an issue at
  [github.com/km322/Third-Brain/issues](https://github.com/km322/Third-Brain/issues) with
  reproduction steps, expected vs. actual behavior, and how you were running the stack.
- **Improve the docs**: everything in [`docs/`](docs) is fair game, and doc-only PRs are as
  welcome as code.
- **Fix or build something**: small, focused PRs get reviewed fastest. For anything large or
  hard to reverse - a new route family, a schema change, a swap of a core dependency - open an
  issue first and agree on the approach before writing the code.

---

## Getting started

Fork the repository, clone your fork, and bring the stack up. Read
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for full setup. TL;DR:

```bash
git clone https://github.com/<you>/Third-Brain.git
cd Third-Brain
cp .env.example .env          # runs offline with zero keys; set OPENAI_API_KEY for real answers
make up-d                     # db, redis, api, worker, web (detached; 'make logs' to follow)
make migrate && make seed     # schema + demo org (prints a generated password once)
```

The API is then on `http://localhost:8000` (`/docs` for the interactive schema) and the
dashboard on `http://localhost:3000`. You need Docker with the Compose v2 plugin; for
host-native iteration (debuggers, fast reloads) see the "run without Docker" section of the
development guide.

You never need a provider API key to develop: with every key blank, a deterministic offline
stub answers completions and produces embeddings, so the whole stack builds, seeds, and passes
its tests with no network access at all.

---

## Repository layout

```
apps/api          FastAPI backend + arq workers   (Python 3.11, async SQLAlchemy 2.0)
apps/web          Next.js 15 dashboard            (TypeScript, React 19, Tailwind, shadcn/ui)
packages/mcp-cli  third-brain-mcp npm CLI         (Node >= 18, zero runtime deps)
docs/             architecture, API, permissions, security, deployment
scripts/          version bump, release, rollback, self-host bootstrap
.github/          CI workflows
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design.

---

## Development workflow

1. **Branch** off `main` in your fork: `git checkout -b feat/short-description` (or `fix/…`,
   `docs/…`, `chore/…`).
2. **Make focused changes** with tests and docs where relevant.
3. **Run the checks locally** (see below) - they mirror CI.
4. **Open a PR** against `main` with a clear description; link any related issue.
5. Address review; keep the branch up to date with `main`.

Every push and PR runs [CI](.github/workflows/ci.yml): version consistency, backend lint +
tests (against real Postgres+pgvector and Redis), frontend lint + build, MCP CLI lint +
tests, Docker image builds, and a docker-compose end-to-end smoke test. PRs must be green.

Do not edit version literals by hand. The root [`VERSION`](VERSION) file is the single source
of truth and `scripts/check_version_sync.sh` fails CI on drift; `make version VERSION=x.y.z`
updates every declaration at once. Releases are the maintainers' job (see
[`docs/RELEASING.md`](docs/RELEASING.md)) - a feature PR should not bump the version.

---

## Coding standards

Editor defaults are enforced by [`.editorconfig`](.editorconfig): UTF-8, LF line endings, a
trailing newline, no trailing whitespace, 4-space indent for Python and 2-space for
TS/JS/JSON/CSS/YAML/Markdown.

Python is linted and formatted by **ruff** (config in
[`apps/api/pyproject.toml`](apps/api/pyproject.toml), line length 100). TypeScript is linted
by **eslint** through `next lint` and formatted by **prettier**. The MCP CLI has its own
dependency-free lint script. Run the full local check before pushing:

```bash
# Backend
cd apps/api
ruff check app tests    # lint
ruff format app tests   # auto-format
pytest -q               # tests

# Frontend
cd apps/web
npm run lint
npm run build
npm run format          # prettier, auto-format
npm run format:check    # prettier, the check CI runs

# MCP CLI
cd packages/mcp-cli
npm run lint
npm test
```

`make lint` runs the backend, frontend and MCP CLI checks together; `make fmt` formats the
backend + frontend. Lint errors and test failures are not negotiable - fix them rather than
suppressing them.

Two house rules that apply to code and prose alike: **no em dashes** (use a plain `-`) and
**no emojis**. New code should read like the code around it - match the existing conventions,
naming, and idioms rather than importing a personal style. Prefer docstrings over inline
comments.

---

## Backend conventions

The backend has a stable **spine** - `app/core`, `app/models`, `app/schemas`,
`app/services` (permissions, llm, vectorstore, metering) - that feature modules build on.
When adding routes or services:

- **Import `settings`** from `app.core.config`; never read `os.environ` directly.
- **Depend on the auth context** - every route uses `get_auth_context` (or a stricter
  variant like `get_session_context` / `require_role` / `require_scope`).
- **Scope every query by `ctx.org_id`.** Cross-org access must return `404`, not leak.
- **Authorize through the permission engine** (`app.services.permissions`), never ad-hoc
  checks. Read [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md).
- **Route module convention**: a route file at `app/api/routes/<name>.py` must export
  `router = APIRouter(prefix="/<x>", tags=["<x>"])`; register the name in
  `app/api/router.py`. The whole router mounts under `/api/v1`.
- **Call models through `app/services/llm`**, never a provider SDK or a raw provider URL, so
  the offline stub and per-org connectors keep working.
- **Metering & audit**: use `record_usage` / `record_audit`; they `add + flush`, so the
  request handler must `await db.commit()` to persist.
- **JSON metadata** on models is the attribute `meta` (column `"metadata"`).
- **Raise `HTTPException`** with a useful `detail`; validate input with Pydantic schemas.
- **Never log document, prompt or query content, or any secret** - metadata only
  (see [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)).

Type hints are expected; `mypy` is configured for gradual typing.

---

## Frontend conventions

- Use the shared client in `lib/api.ts` (`api.get/post/...`, `streamChat`) - its base URL
  already includes `/api/v1`. Don't hand-roll `fetch`.
- Shared types live in `lib/types.ts`; keep them in sync with the backend schemas.
- Use the design tokens (Tailwind CSS vars: `bg-background`, `text-foreground`,
  `bg-primary`, …) and the `@/components/ui/*` primitives; support light + dark mode.
- Data fetching goes through TanStack Query. Prefer server-provided shapes over reshaping
  in components.
- Dashboard routes and sidebar entries must stay in sync with `NAV_GROUPS` in
  `components/dashboard/sidebar.tsx` - that array is the canonical navigation: Knowledge
  (Overview, Ask, Knowledge Bases, Documents, Data Sources, Answers, Entities, Graph),
  Governance (Members, Invites, Teams, Access, Oversharing, Audit Log), and Platform
  (Connectors, SSO & SCIM, API Keys, Knowledge Gaps, Usage, Settings).

---

## Tests

Tests run against the **real stack - Postgres 16 + pgvector and Redis - never fakes**. There
is no SQLite or mock tier, and adding one is not an acceptable shortcut: the permission
predicate this product is built on is pushed into SQL, so a fake would bless behavior that
does not exist in production. Three tiers:

| Tier | What it is | How to run |
|---|---|---|
| **Unit** | Pure functions (permission math, chunking, RRF fusion, pricing, security). No infrastructure, always run. | `cd apps/api && pytest -q` |
| **Integration** | Drives the real FastAPI app over httpx ASGI against real Postgres+pgvector and Redis, after a real `alembic upgrade head`. | `make test-integration` |
| **E2E** | Playwright drives the full docker-compose stack in a browser. | `make test-e2e` |

Integration tests **skip** (they never fail) when that infrastructure is unreachable, so a
bare `pytest` stays green on a laptop with nothing running - but CI provides the infrastructure
and is the authoritative gate. Point the suite at your own infrastructure with
`TEST_DATABASE_URL` / `TEST_REDIS_URL`. Any new integration or e2e test must pass against real
pgvector and Redis.

Write tests from the intended behavior, not from the implementation you just wrote, and give
them the same review attention as the code: a wrong test silently blesses wrong behavior.
Backend tests use `pytest` + `pytest-asyncio` (async mode auto) and live in `apps/api/tests`.
Cover new services and routes, especially their **permission and org-scoping** behavior.

---

## The permission invariant

[`apps/api/app/services/permissions.py`](apps/api/app/services/permissions.py) is the single
source of truth for who can see what, and it is enforced in **two** places that must never
disagree:

1. **Route authorization** - `effective_permission` / `require_permission`.
2. **Retrieval** - `build_retrieval_scope()` returns a `RetrievalScope` whose `.apply(stmt)`
   pushes the visibility predicate into the SQL `WHERE` clause, so a chunk the caller cannot
   see can never enter a search result, an LLM prompt, or a citation.

A PR that touches retrieval, ACLs, roles, scopes, or auth **must** add or adjust tests for
both call sites, and must not introduce a code path that filters permissions in Python after
the query. This is the guarantee the whole product rests on; it is the one place where "it
works" is not enough.

---

## Commits & pull requests

- Write imperative, present-tense commit subjects: *"Add document reprocess endpoint"*.
- We recommend [Conventional Commits](https://www.conventionalcommits.org/) prefixes
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) - they make history and
  release notes scannable.
- Keep PRs focused and reasonably small; unrelated refactors belong in their own PR.
- Include: what changed and why, how you tested it, and screenshots for UI changes.
- Update the docs when you change the API surface, and add a `## [Unreleased]` entry in
  [`CHANGELOG.md`](CHANGELOG.md) for anything a user would notice.
- Ensure CI is green before requesting review.

Maintainers review on a best-effort basis. A PR that is focused, tested, and explains its
reasoning gets merged much faster than one that is not.

---

## Licensing of contributions

By submitting a contribution you agree that it is licensed under the project's
[Apache-2.0](LICENSE) license, per section 5 of that license. There is **no CLA** and no
DCO sign-off requirement - inbound equals outbound. Only contribute code you have the right
to license this way, and do not paste in code whose provenance or license you are unsure of.

---

## Database migrations

Schema changes require an Alembic migration:

```bash
cd apps/api
alembic revision --autogenerate -m "add <thing>"
# review the generated file, then:
alembic upgrade head
```

Review autogenerated migrations by hand (autogen misses some changes). Prefer
backward-compatible, forward-only migrations (add before remove) so deploys stay
zero-downtime - see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Reporting bugs & security issues

- **Bugs / features**: open a GitHub issue with clear reproduction steps, expected vs.
  actual behavior, and environment details.
- **Security vulnerabilities**: do **not** open a public issue, discussion or PR. Report them
  privately through GitHub private vulnerability reporting at
  <https://github.com/km322/Third-Brain/security/advisories/new>. The policy lives in
  [`.github/SECURITY.md`](.github/SECURITY.md), and [`docs/SECURITY.md`](docs/SECURITY.md)
  describes the threat model in depth. Test only against an instance you run yourself.
