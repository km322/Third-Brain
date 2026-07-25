"""Unit test pinning the single product version across the monorepo.

The repo-root ``VERSION`` file is the source of truth; ``app.__version__``, the web
``package.json`` and the MCP CLI ``package.json`` must always agree with it
(``scripts/bump_version.sh`` rewrites them all together).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import __version__

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VERSION_FILE = _REPO_ROOT / "VERSION"

pytestmark = pytest.mark.skipif(
    not _VERSION_FILE.exists(),
    reason="repo-root VERSION file not present (containerized/installed context)",
)


def test_version_is_synchronized_across_the_monorepo() -> None:
    version = _VERSION_FILE.read_text(encoding="utf-8").strip()
    assert __version__ == version
    package_json = json.loads(
        (_REPO_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    )
    assert package_json["version"] == version
    mcp_cli_package_json = json.loads(
        (_REPO_ROOT / "packages" / "mcp-cli" / "package.json").read_text(encoding="utf-8")
    )
    assert mcp_cli_package_json["version"] == version
