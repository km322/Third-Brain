#!/usr/bin/env bash
# Cut a release: preflight checks, version bump, changelog rotation, commit and
# annotated tag. Prints (never runs) the push commands; pushing the tag is what
# triggers the release pipeline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 1 ]]; then
  echo "usage: release.sh X.Y.Z" >&2
  exit 1
fi
V="$1"

# Strict semver, no build metadata - must stay in lockstep with bump_version.sh
# (npm normalizes looser forms, and '+' is invalid in Docker image tags).
SEMVER_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$'
if [[ ! "$V" =~ $SEMVER_RE ]]; then
  echo "error: '$V' is not a semver version (X.Y.Z with optional -prerelease)" >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then
  echo "error: releases are cut from main (currently on '$BRANCH')" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean - commit or stash your changes first" >&2
  exit 1
fi

if ! git fetch origin main; then
  echo "error: could not fetch origin/main - check your network/remote" >&2
  exit 1
fi
# Local commits ahead of origin/main are fine; behind or diverged is not.
if ! git merge-base --is-ancestor origin/main HEAD; then
  echo "error: local main is behind or has diverged from origin/main - pull/rebase first" >&2
  exit 1
fi

if git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
  echo "error: tag v$V already exists" >&2
  exit 1
fi

"$ROOT/scripts/bump_version.sh" "$V"

python3 - "$V" "$ROOT/CHANGELOG.md" <<'PY'
import datetime
import pathlib
import re
import sys

version, path = sys.argv[1], pathlib.Path(sys.argv[2])
text = path.read_text()

# A hand-written section for this version (e.g. the 1.0.0 entry) wins: leave
# the file untouched and let the commit pick it up as-is.
if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
    raise SystemExit(0)

if not re.search(r"^## \[Unreleased\]", text, re.M):
    sys.exit("error: CHANGELOG.md has no '## [Unreleased]' section")

body = re.search(
    r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]:|\Z)", text, re.M | re.S
).group(1)
if not body.strip():
    sys.exit("error: the '## [Unreleased]' section is empty - nothing to release")

date = datetime.date.today().isoformat()
text = text.replace(
    "## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {date}", 1
)

new_refs = (
    f"[Unreleased]: https://github.com/km322/Third-Brain/compare/v{version}...HEAD\n"
    f"[{version}]: https://github.com/km322/Third-Brain/releases/tag/v{version}"
)
unreleased_ref = re.compile(r"^\[Unreleased\]: .*$", re.M)
if unreleased_ref.search(text):
    text = unreleased_ref.sub(new_refs, text, count=1)
else:
    text = text.rstrip("\n") + "\n\n" + new_refs + "\n"

path.write_text(text)
PY

git add -A
git commit -m "release: v$V"
git tag -a "v$V" -m "Third Brain v$V"

cat <<EOF

Release v$V committed and tagged locally. To publish, run:

  git push origin main
  git push origin "v$V"

Pushing the tag triggers .github/workflows/release.yml (image build + publish, GitHub release).
EOF
