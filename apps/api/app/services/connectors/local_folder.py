"""Reference connector: a local folder tree + a sidecar ACL file.

This is a fully-functional connector that needs no network or credentials, so it exercises
the entire sync path (ingestion + source-ACL mapping + incremental re-sync + deletion
detection) end-to-end and is the connector the test suite drives. It also documents the
contract a real SaaS connector must satisfy.

Layout::

    <root>/
        handbook.md
        eng/design.md
        .acl.json        # optional sidecar mapping paths -> source principals

``.acl.json``::

    {
      "default": ["alice@example.com"],          # applied to files with no explicit entry
      "files": {
        "handbook.md": ["alice@example.com", "group:engineering"],
        "eng/design.md": ["group:engineering"]
      }
    }

A principal is an email (or bare name) for a USER, or ``group:NAME`` / ``team:NAME`` for a
GROUP. Files with no entry and no ``default`` are synced with an empty ACL (visible only to
admins/owner under the source's default PRIVATE visibility).
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.models.enums import DataSourceKind, ExternalPrincipalKind
from app.services.connectors.base import (
    BaseConnector,
    ConnectorError,
    RemoteDocument,
    RemotePrincipal,
    SyncBatch,
)

ACL_FILENAME = ".acl.json"
MAX_FILE_BYTES = 25 * 1024 * 1024


def _allowed_roots() -> list[Path]:
    """Operator-approved base directories the connector may read from (fully resolved)."""
    roots: list[Path] = []
    for raw in (settings.LOCAL_CONNECTOR_ROOTS or "").split(","):
        raw = raw.strip()
        if raw:
            roots.append(Path(raw).expanduser().resolve())
    return roots


def _resolve_within_allowed(root: str) -> Path:
    """Resolve ``root`` and confirm it sits inside an operator-allowed base directory.

    The local_folder connector reads arbitrary files off the server's own filesystem, so it
    must never be pointed at a path an operator has not explicitly allow-listed. With no
    allow-list configured the connector is disabled outright. ``Path.resolve()`` collapses
    ``..`` and follows symlinks, so neither can be used to escape a base. Raises
    ``ValueError`` (surfaced as a 400 on data-source create/update) when out of bounds.
    """
    allowed = _allowed_roots()
    if not allowed:
        raise ValueError(
            "The local_folder connector is disabled on this deployment. An operator must set "
            "LOCAL_CONNECTOR_ROOTS to the directories it is permitted to ingest."
        )
    resolved = Path(root).expanduser().resolve()
    for base in allowed:
        if resolved == base or base in resolved.parents:
            return resolved
    raise ValueError(
        "local_folder 'root' is outside the directories this deployment permits the connector "
        "to read (see LOCAL_CONNECTOR_ROOTS)."
    )


def _parse_principal(spec: str) -> RemotePrincipal:
    spec = spec.strip()
    for prefix in ("group:", "team:"):
        if spec.startswith(prefix):
            return RemotePrincipal(spec[len(prefix) :].strip(), ExternalPrincipalKind.GROUP)
    return RemotePrincipal(spec, ExternalPrincipalKind.USER)


class LocalFolderConnector(BaseConnector):
    kind = DataSourceKind.LOCAL_FOLDER

    @classmethod
    def validate_config(cls, config: dict, secret: str | None) -> None:
        root = (config or {}).get("root")
        if not root or not isinstance(root, str):
            raise ValueError("local_folder connector requires a 'root' path in config")
        # Confine the root to an operator-allowed directory so a source can never be
        # configured to read /etc, /proc, another tenant's uploads, etc.
        _resolve_within_allowed(root)

    async def fetch(self, cursor: str | None, secret: str | None) -> SyncBatch:
        # File I/O is blocking; do the whole scan in a worker thread.
        return await asyncio.to_thread(self._scan)

    def _scan(self) -> SyncBatch:
        # Re-check confinement at scan time, not just on create: the allow-list (or the
        # stored config) could have changed since the source was created.
        root = _resolve_within_allowed(self.config.get("root", ""))
        if not root.is_dir():
            raise ConnectorError(f"local_folder root does not exist or is not a directory: {root}")

        acl_map, default_acl = self._load_acl(root)
        documents: list[RemoteDocument] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            # A symlink inside the tree can point outside the allowed root; never follow one
            # out of bounds (rglob may descend into symlinked directories on some platforms).
            try:
                real = path.resolve()
            except OSError:
                continue
            if real != root and root not in real.parents:
                continue
            relpath = path.relative_to(root)
            rel = relpath.as_posix()
            # Skip hidden files/dirs and the ACL sidecar.
            if rel == ACL_FILENAME or any(part.startswith(".") for part in relpath.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                content = path.read_bytes()
            except OSError:
                continue
            specs = acl_map.get(rel, default_acl)
            documents.append(
                RemoteDocument(
                    external_id=rel,
                    title=path.name,
                    content=content,
                    mime_type=mimetypes.guess_type(path.name)[0] or "text/plain",
                    source_uri=str(path),
                    updated_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                    acl=[_parse_principal(s) for s in specs],
                )
            )
        # A full directory scan is authoritative: anything previously synced but no longer
        # present has been deleted upstream.
        return SyncBatch(documents=documents, cursor=None, full_sync=True)

    def _load_acl(self, root: Path) -> tuple[dict[str, list[str]], list[str]]:
        acl_path = root / ACL_FILENAME
        if not acl_path.is_file():
            return {}, []
        try:
            data = json.loads(acl_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ConnectorError(f"invalid {ACL_FILENAME}: {exc}") from exc
        files = data.get("files") or {}
        default = data.get("default") or []
        if not isinstance(files, dict) or not isinstance(default, list):
            raise ConnectorError(
                f"invalid {ACL_FILENAME}: expected a 'files' object and a 'default' list"
            )
        return {k: list(v) for k, v in files.items()}, list(default)
