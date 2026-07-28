# Contributing to Third Brain

Thanks for helping build Third Brain! This guide covers how to set up your environment,
the conventions we follow, and how to get a change merged.

- [Code of conduct](#code-of-conduct)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Development workflow](#development-workflow)
- [Coding standards](#coding-standards)
- [Backend conventions](#backend-conventions)
- [Frontend conventions](#frontend-conventions)
- [Tests](#tests)
- [Commits & pull requests](#commits--pull-requests)
- [Database migrations](#database-migrations)
- [Reporting bugs & security issues](#reporting-bugs--security-issues)

---

## Code of conduct

Be respectful and constructive. Assume good intent, review the code and not the person, and
keep discussions focused on the work.

---

## Getting started

Read [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for full setup. TL;DR:

```bash
cp .env.example .env          # runs offline with zero keys; set OPENAI_API_KEY for real answers
make up                       # db, redis, api, worker, web
make migrate && make seed     # schema + demo org
```

For host-native iteration (debuggers, fast reloads) see the "run without Docker" section of
the development guide.

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

1. **Branch** off `main`: `git checkout -b feat/short-description` (or `fix/…`, `docs/…`,
   `chore/…`).
2. **Make focused changes** with tests and docs where relevant.
3. **Run the checks locally** (see below) - they mirror CI.
4. **Open a PR** against `main` with a clear description; link any related issue.
5. Address review; keep the branch up to date with `main`.

Every push and PR runs [CI](.github/workflows/ci.yml): version consistency, backend lint +
tests (against real Postgres+pgvector and Redis), frontend lint + build, MCP CLI lint +
tests, Docker image builds, and a docker-compose end-to-end smoke test. PRs must be green.

---

## Coding standards

Editor defaults are enforced by [`.editorconfig`](.editorconfig): UTF-8, LF line endings, a
trailing newline, no trailing whitespace, 4-space indent for Python and 2-space for
TS/JS/JSON/CSS/YAML/Markdown.

Run the full local check before pushing:

```bash
# Backend
cd apps/api
ruff check app          # lint
ruff format app         # auto-format
pytest -q               # tests

# Frontend
cd apps/web
npm run lint
npm run build
npm run format          # prettier

# MCP CLI
cd packages/mcp-cli
npm run lint
npm test
```

`make lint` runs the backend, frontend and MCP CLI checks together; `make fmt` formats the
backend + frontend.

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
- **Metering & audit**: use `record_usage` / `record_audit`; they `add + flush`, so the
  request handler must `await db.commit()` to persist.
- **JSON metadata** on models is the attribute `meta` (column `"metadata"`).
- **Raise `HTTPException`** with a useful `detail`; validate input with Pydantic schemas.
- Add docstrings; match the surrounding style. Ruff config lives in
  [`apps/api/pyproject.toml`](apps/api/pyproject.toml) (line length 100).

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

- Backend tests use `pytest` + `pytest-asyncio` (async mode auto) and live in
  `apps/api/tests`. Aim to cover new services and routes, especially **permission and
  org-scoping** behavior - these are the product's core guarantees.
- Integration tests run against real Postgres+pgvector and Redis (there is no SQLite/mock
  tier); they self-skip when that infrastructure is unreachable, so a bare `pytest` stays
  green.
- Run with `pytest -q` (or `make test` against the running stack).

A change to permissions, retrieval scope, or auth **must** come with tests.

---

## Commits & pull requests

- Write imperative, present-tense commit subjects: *"Add document reprocess endpoint"*.
- We recommend [Conventional Commits](https://www.conventionalcommits.org/) prefixes
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) - they make history and
  release notes scannable.
- Keep PRs focused and reasonably small. Include: what changed and why, how you tested,
  and screenshots for UI changes.
- Update the docs when you change the API surface.
- Ensure CI is green before requesting review.

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
- **Security vulnerabilities**: do **not** open a public issue - email the project's
  security contact at **admin@third-brain.ai**. See [`docs/SECURITY.md`](docs/SECURITY.md)
  for our disclosure policy.

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0](LICENSE) license.
