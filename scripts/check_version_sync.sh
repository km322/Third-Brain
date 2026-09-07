#!/usr/bin/env bash
# Verify the version literals across the repo agree with the VERSION file.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FILE_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
API_VERSION="$(python3 -c 'import re,sys;print(re.search(r"__version__\s*=\s*\"([^\"]+)\"", open(sys.argv[1]).read()).group(1))' "$ROOT/apps/api/app/__init__.py")"
WEB_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$ROOT/apps/web/package.json")"
MCP_CLI_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$ROOT/packages/mcp-cli/package.json")"
ROADMAP_VERSION="$(python3 -c 'import re,sys;print(re.search(r"Current version: \*\*([^*]+)\*\*", open(sys.argv[1]).read()).group(1))' "$ROOT/docs/ROADMAP.md")"

if [[ "$FILE_VERSION" != "$API_VERSION" || "$FILE_VERSION" != "$WEB_VERSION" || "$FILE_VERSION" != "$MCP_CLI_VERSION" || "$FILE_VERSION" != "$ROADMAP_VERSION" ]]; then
  echo "::error::Version drift: VERSION=$FILE_VERSION apps/api/app/__init__.py=$API_VERSION apps/web/package.json=$WEB_VERSION packages/mcp-cli/package.json=$MCP_CLI_VERSION docs/ROADMAP.md=$ROADMAP_VERSION. Repair with: make version VERSION=<x>"
  exit 1
fi

echo "Version OK: $FILE_VERSION"
