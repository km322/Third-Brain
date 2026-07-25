#!/usr/bin/env bash
# Set the product version everywhere it is declared, then verify the sync.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -ne 1 ]]; then
  echo "usage: bump_version.sh X.Y.Z" >&2
  exit 1
fi
V="$1"

# Strict semver, no build metadata: npm normalizes anything looser (stripping
# +build, rewriting -01 to -1), which would leave the version files disagreeing.
SEMVER_RE='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$'
if [[ ! "$V" =~ $SEMVER_RE ]]; then
  echo "error: '$V' is not a semver version (X.Y.Z with optional -prerelease)" >&2
  exit 1
fi

printf '%s\n' "$V" > "$ROOT/VERSION"

python3 - "$V" "$ROOT" <<'PY'
import pathlib
import re
import sys

version, root = sys.argv[1], pathlib.Path(sys.argv[2])

init = root / "apps/api/app/__init__.py"
text = init.read_text()
new, n = re.subn(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text)
if n != 1:
    sys.exit(f"error: expected exactly one __version__ assignment in {init}, found {n}")
init.write_text(new)

roadmap = root / "docs/ROADMAP.md"
text = roadmap.read_text()
# The line ends with a period outside the bold markers; only the bold part changes.
new, n = re.subn(r"Current version: \*\*[^*]+\*\*", f"Current version: **{version}**", text)
if n != 1:
    sys.exit(f"error: expected exactly one 'Current version' line in {roadmap}, found {n}")
roadmap.write_text(new)
PY

# npm updates package.json AND package-lock.json so the lockfile stays in sync.
(cd "$ROOT/apps/web" && npm version "$V" --no-git-tag-version --allow-same-version >/dev/null)
(cd "$ROOT/packages/mcp-cli" && npm version "$V" --no-git-tag-version --allow-same-version >/dev/null)

exec "$ROOT/scripts/check_version_sync.sh"
