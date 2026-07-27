#!/usr/bin/env bash
# Roll the production compose stack back to a previously published image tag.
# Run from the repo root on the production host (reads .env for compose vars).
set -euo pipefail

usage() {
  echo "usage: rollback.sh vX.Y.Z [--yes]" >&2
  exit 1
}

TAG=""
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    -*) usage ;;
    *)
      [[ -n "$TAG" ]] && usage
      TAG="$arg"
      ;;
  esac
done
[[ -n "$TAG" ]] || usage

# Accept every tag shape release.sh can publish, prereleases included.
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "error: '$TAG' is not a release tag (expected vX.Y.Z or vX.Y.Z-prerelease)" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "error: no .env in $(pwd) - run from the repo root on the prod host" >&2
  exit 1
fi

export IMAGE_TAG="$TAG"

echo "Rolling back to images tagged $TAG:"
IMAGES="$(docker compose -f docker-compose.prod.yml config --images | sort -u)"
echo "$IMAGES"
if echo "$IMAGES" | grep -q '^third-brain-'; then
  echo "WARNING: the third-brain images above are not registry-qualified, so compose"
  echo "cannot pull them - set IMAGE_REGISTRY (e.g. ghcr.io/km322) in .env or the environment."
fi

echo
echo "Currently running:"
docker compose -f docker-compose.prod.yml ps || true

echo
docker compose -f docker-compose.prod.yml pull api worker web

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Restart api, worker and web on $TAG? [y/N] " reply || reply=""
  case "$reply" in
    y | Y | yes | YES) ;;
    *)
      echo "aborted"
      exit 1
      ;;
  esac
fi

# Persist the tag in .env: compose interpolates ${IMAGE_TAG:-latest} on every
# invocation, so without this the next routine `docker compose up -d` would
# quietly recreate the services from `latest` (or a newer tag left in .env),
# undoing the rollback.
python3 - "$TAG" <<'PY'
import pathlib
import re
import sys

tag = sys.argv[1]
env = pathlib.Path(".env")
text = env.read_text()
new, n = re.subn(r"(?m)^IMAGE_TAG=.*$", f"IMAGE_TAG={tag}", text, count=1)
if n == 0:
    new = text + ("" if text.endswith("\n") else "\n") + f"IMAGE_TAG={tag}\n"
env.write_text(new)
PY
echo "Pinned IMAGE_TAG=$TAG in .env"

# --no-deps is load-bearing: it skips the `migrate` one-shot. The rolled-back
# image ships an OLDER alembic tree that cannot resolve the revision the DB is
# already stamped with ("Can't locate revision ..."), so migrate would fail and
# its service_completed_successfully gate would keep api and worker down
# forever. The schema stays where it is - see the NOTE below.
docker compose -f docker-compose.prod.yml up -d --no-deps --no-build api worker web

echo
docker compose -f docker-compose.prod.yml ps

cat <<EOF

NOTE: IMAGE_TAG=$TAG is now pinned in .env, so subsequent compose runs stay on
this version. Point it at the new tag when you next upgrade.

NOTE: the database schema was NOT downgraded. Policy: every migration must be
backward-compatible one release back (expand/contract), so the $TAG images run
fine against the current schema. Downgrading the schema (alembic downgrade /
make db-downgrade) is break-glass only - see docs/RELEASING.md.

Verify the rollback took:
  curl -fsS https://api.<your-domain>/api/v1/version   # version should read ${TAG#v}
EOF
