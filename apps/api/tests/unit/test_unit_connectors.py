"""Unit tests for the data-source connector framework (no infra).

Exercises the reference LocalFolderConnector's parsing/ACL logic, the registry, and the
real-SaaS scaffolds' config validation - all against the intended contract, not the
implementation.
"""

from __future__ import annotations

import json

import pytest

from app.models.enums import DataSourceKind, ExternalPrincipalKind
from app.services.connectors import registered_kinds
from app.services.connectors.base import ConnectorNotConfigured
from app.services.connectors.local_folder import LocalFolderConnector, _parse_principal
from app.services.connectors.registry import connector_class
from app.services.connectors.remote import GoogleDriveConnector, SlackConnector


class _FakeSource:
    """Minimal stand-in for a DataSource (connectors only read .config)."""

    def __init__(self, config: dict) -> None:
        self.config = config


class TestPrincipalParsing:
    def test_email_is_user(self) -> None:
        p = _parse_principal("alice@example.com")
        assert p.external_id == "alice@example.com"
        assert p.kind == ExternalPrincipalKind.USER

    def test_group_prefix(self) -> None:
        assert _parse_principal("group:engineering").kind == ExternalPrincipalKind.GROUP
        assert _parse_principal("group:engineering").external_id == "engineering"

    def test_team_prefix_is_group(self) -> None:
        p = _parse_principal("team:platform")
        assert p.kind == ExternalPrincipalKind.GROUP
        assert p.external_id == "platform"


class TestLocalFolderConnector:
    def test_validate_config_requires_root(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            LocalFolderConnector.validate_config({}, None)
        # A root inside an allow-listed base is accepted (the suite allow-lists the tmp tree).
        LocalFolderConnector.validate_config({"root": str(tmp_path)}, None)

    def test_validate_config_rejects_root_outside_allowlist(self) -> None:
        # /etc exists but is not under the allow-list: a data source must not be creatable
        # that would read arbitrary server files or another tenant's uploads (path traversal).
        with pytest.raises(ValueError):
            LocalFolderConnector.validate_config({"root": "/etc"}, None)
        with pytest.raises(ValueError):
            LocalFolderConnector.validate_config({"root": "/proc/self/environ"}, None)

    def test_validate_config_disabled_when_no_allowlist(self, tmp_path, monkeypatch) -> None:
        # With no LOCAL_CONNECTOR_ROOTS the connector is disabled outright (safe default).
        from app.core.config import settings

        monkeypatch.setattr(settings, "LOCAL_CONNECTOR_ROOTS", "")
        with pytest.raises(ValueError):
            LocalFolderConnector.validate_config({"root": str(tmp_path)}, None)

    async def test_scan_skips_symlink_escaping_root(self, tmp_path) -> None:
        # A symlink inside the root that points outside it must never be followed/ingested.
        secret = tmp_path / "outside_secret.md"
        secret.write_text("cross-boundary secret")
        root = tmp_path / "root"
        root.mkdir()
        (root / "ok.md").write_text("legit content")
        (root / "escape.md").symlink_to(secret)
        connector = LocalFolderConnector(_FakeSource({"root": str(root)}))
        batch = await connector.fetch(None, None)
        ids = {d.external_id for d in batch.documents}
        assert "ok.md" in ids
        assert "escape.md" not in ids

    async def test_scan_maps_files_and_acls(self, tmp_path) -> None:
        (tmp_path / "handbook.md").write_text("company handbook")
        (tmp_path / "eng").mkdir()
        (tmp_path / "eng" / "design.md").write_text("design doc")
        (tmp_path / "unlisted.md").write_text("no acl entry")
        (tmp_path / ".hidden.md").write_text("should be skipped")
        (tmp_path / ".acl.json").write_text(
            json.dumps(
                {
                    "default": ["default@example.com"],
                    "files": {
                        "handbook.md": ["alice@example.com", "group:engineering"],
                        "eng/design.md": ["group:engineering"],
                    },
                }
            )
        )
        connector = LocalFolderConnector(_FakeSource({"root": str(tmp_path)}))
        batch = await connector.fetch(None, None)

        assert batch.full_sync is True
        by_id = {d.external_id: d for d in batch.documents}
        # Hidden files and the ACL sidecar are excluded.
        assert set(by_id) == {"handbook.md", "eng/design.md", "unlisted.md"}
        assert by_id["handbook.md"].content == b"company handbook"

        handbook_acl = {(p.external_id, p.kind) for p in by_id["handbook.md"].acl}
        assert handbook_acl == {
            ("alice@example.com", ExternalPrincipalKind.USER),
            ("engineering", ExternalPrincipalKind.GROUP),
        }
        # A file with no explicit entry inherits ``default``.
        assert [p.external_id for p in by_id["unlisted.md"].acl] == ["default@example.com"]

    async def test_missing_root_raises(self, tmp_path) -> None:
        connector = LocalFolderConnector(_FakeSource({"root": str(tmp_path / "nope")}))
        from app.services.connectors.base import ConnectorError

        with pytest.raises(ConnectorError):
            await connector.fetch(None, None)


class TestRegistry:
    def test_all_kinds_registered(self) -> None:
        assert set(registered_kinds()) == set(DataSourceKind)

    def test_local_folder_resolves(self) -> None:
        assert connector_class(DataSourceKind.LOCAL_FOLDER) is LocalFolderConnector


class TestRemoteScaffolds:
    def test_validate_config_requires_keys_and_secret(self) -> None:
        with pytest.raises(ValueError):
            GoogleDriveConnector.validate_config({}, "token")  # missing drive_id
        with pytest.raises(ValueError):
            GoogleDriveConnector.validate_config({"drive_id": "d"}, None)  # missing secret
        GoogleDriveConnector.validate_config({"drive_id": "d"}, "token")  # ok

    async def test_fetch_not_configured(self) -> None:
        connector = SlackConnector(_FakeSource({"channels": ["general"]}))
        with pytest.raises(ConnectorNotConfigured):
            await connector.fetch(None, "xoxb-token")
