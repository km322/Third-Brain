# Releasing

How Third Brain is versioned and how a release is cut, verified, and rolled back. The
audience is a maintainer shipping a release; for operating a released stack, see
[`DEPLOYMENT.md`](./DEPLOYMENT.md).

- [Versioning policy](#versioning-policy)
- [Cutting a release](#cutting-a-release)
- [Rolling back](#rolling-back)
- [Hotfixes](#hotfixes)
- [Published web image caveat](#published-web-image-caveat)
- [One-time GHCR setup](#one-time-ghcr-setup)
- [npm publishing (third-brain-mcp)](#npm-publishing-third-brain-mcp)

---

## Versioning policy

Third Brain follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **Major** - breaking changes to the REST/OpenAI-compatible/MCP surface, the permission
  model, or required configuration.
- **Minor** - backward-compatible features.
- **Patch** - backward-compatible fixes.

There is **one product version** across the API, the worker, and the web app - they are
built, tagged, and released together.

The root [`VERSION`](../VERSION) file is the **single source of truth**. The same version
also appears as literals in `apps/api/app/__init__.py`, `apps/web/package.json`, and the
"Current version" line of [`ROADMAP.md`](./ROADMAP.md) - never edit any of them by hand.
Run:

```bash
make version VERSION=x.y.z    # scripts/bump_version.sh updates every literal
```

`scripts/check_version_sync.sh` runs in CI and fails the build if the literals ever drift
from `VERSION`.

---

## Cutting a release

Preconditions: you are on `main`, the working tree is clean, and CI is green for `HEAD`.

```bash
make release VERSION=X.Y.Z
```

This runs `scripts/release.sh`, which bumps every version literal, rolls the CHANGELOG's
`[Unreleased]` notes into a `## [X.Y.Z] - YYYY-MM-DD` section, commits, and creates the
annotated tag `vX.Y.Z`. Then:

1. Review the release commit and tag (`git show vX.Y.Z`).
2. `git push origin main` and wait for CI to go green on the release commit.
3. `git push origin vX.Y.Z` - the tag triggers the **Release** workflow
   (`.github/workflows/release.yml`), which builds and publishes
   `ghcr.io/km322/third-brain-api` and `ghcr.io/km322/third-brain-web` tagged `vX.Y.Z`.
4. Watch the Release workflow to completion.
5. Verify: both images are visible on GHCR, and a deployed API reports the new version at
   `GET /api/v1/version` (`version`, `git_commit`, `build_time`, `environment`).

> **The tag-before-CI race.** The Release workflow's verify job fails if CI has not
> completed successfully for the tagged SHA. If you push the tag too early, wait for CI
> to go green and re-run the Release workflow - the tag does not need to be recreated.

---

## Rolling back

```bash
make rollback IMAGE_TAG=vX.Y.Z    # the previous released tag
```

This repoints the production compose stack at the older images and restarts
`api`/`worker`/`web` with `--no-deps`, which skips the `migrate` one-shot. `--no-deps` is
essential: the older image's alembic tree cannot resolve the newer database revision, so
re-running the migrate gate would fail and hold `api`/`worker` down.

The script also pins `IMAGE_TAG=vX.Y.Z` in the host's `.env`, so later routine
`docker compose up -d` runs stay on the rolled-back version. Point `IMAGE_TAG` at the new
tag when you next upgrade.

That works because of the **database policy**: every release's migrations must be
backward-compatible with the previous release's code (expand/contract - add columns and
tables first, migrate readers, remove in a later release). Rolling back is therefore
always an image rollback, never a schema downgrade.

`make db-downgrade` (`alembic downgrade`) exists as **break-glass only** - it can destroy
data and is never part of a normal rollback.

---

## Hotfixes

Fix on `main` (cherry-pick the fix onto `main` if it landed elsewhere), then release from
`main` with a patch bump. `scripts/release.sh` enforces that releases are cut from `main`,
so there are no long-lived release branches to maintain.

---

## Published web image caveat

`NEXT_PUBLIC_API_URL` is baked into the web bundle **at build time**. The GHCR web image
is therefore built for the canonical deployment - the repo Actions variable
`RELEASE_NEXT_PUBLIC_API_URL` supplies its API origin. Self-hosters with a different API
origin must build the web image themselves, which `docker-compose.prod.yml` does by
default when `IMAGE_REGISTRY` is unset. Moving this to runtime configuration is on the
[roadmap](./ROADMAP.md).

---

## One-time GHCR setup

GHCR packages are created **private** on first push. After the first release, in the
GitHub UI make `third-brain-api` and `third-brain-web` public and link them to the
[repository](https://github.com/km322/Third-Brain) so anonymous `docker pull` works and
the packages appear on the repo page.

---

## npm publishing (third-brain-mcp)

The release workflow publishes `packages/mcp-cli` to npm with
[trusted publishing](https://docs.npmjs.com/trusted-publishers) (OIDC) - no token,
secret, or one-time password involved. One-time setup on npmjs.com: the
`third-brain-mcp` package's **Settings -> Trusted Publisher** must list GitHub Actions
with owner `km322`, repository `Third-Brain`, and workflow filename `release.yml`. The
publish step is idempotent: an already-published version is skipped, never overwritten.

A tag-push run executes the workflow as of the tagged commit. To re-run a release for an
existing tag with the **current** workflow definition (e.g. after fixing `release.yml`),
run `gh workflow run release.yml -f tag=vX.Y.Z` - every job checks out and builds the
tagged commit either way.
