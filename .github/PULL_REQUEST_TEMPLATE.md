## What changed and why

<!-- The problem this solves, and the approach you took. Link any related issue. -->

Closes #

## How this was tested

<!-- Commands you ran and what you observed. Screenshots or a short clip for UI changes. -->

## Checklist

- [ ] `ruff check app` and `ruff format --check app` pass in `apps/api` (CI runs both over
      `app tests`; `make lint` covers everything at once)
- [ ] `npm run lint` and `npm run build` pass in `apps/web`
- [ ] `npm run lint` and `npm test` pass in `packages/mcp-cli` (if the CLI changed)
- [ ] Tests added or updated for the behavior this PR changes
- [ ] Tests run against **real Postgres 16 + pgvector and real Redis** - never fakes, mocks,
      or SQLite. Integration tests self-skip when that infrastructure is unreachable, so a
      green local `pytest` is not proof; CI is the authoritative gate
- [ ] An Alembic migration is included if the schema changed, reviewed by hand and
      forward-only (add before remove)
- [ ] Docs updated if behavior changed (`README.md`, `docs/`, `.env.example`, `CHANGELOG.md`)
- [ ] No secrets, API keys, tokens, or real document content anywhere in the diff
- [ ] Version literals untouched by hand (releases go through `make version VERSION=x.y.z`)
- [ ] CI is green

## Permissions invariant

`apps/api/app/services/permissions.py` is the single source of truth for access, and it is
enforced in **two** places that must never disagree.

- [ ] This PR does not touch `permissions.py`, retrieval, or ACLs.
- [ ] It does, and I added or adjusted tests for **both** call sites:
  - **route authorization** - `effective_permission` / `require_permission`, and
  - **retrieval** - `build_retrieval_scope()`, whose `.apply(stmt)` pushes the visibility
    predicate into the SQL `WHERE` clause.

  A chunk the caller cannot see must never enter a search result, an LLM prompt, or a
  citation. Every query stays scoped by `ctx.org_id`, and cross-org access returns `404`.
