"""Real SaaS connector scaffolds (Google Drive, Slack, GitHub, Notion, Confluence).

Each declares the config/secret it needs and validates it, so a data source can be
configured and stored today. The live ``fetch`` (OAuth + network paging + ACL extraction)
is provider-specific and requires real credentials, so it raises
:class:`ConnectorNotConfigured` in this build rather than pretending to sync. Filling in a
``fetch`` is the only work needed to light one up - the sync engine, ACL mapping, identity
resolution and permission enforcement are all provider-agnostic and already done.
"""

from __future__ import annotations

from app.models.enums import DataSourceKind
from app.services.connectors.base import BaseConnector, ConnectorNotConfigured, SyncBatch


class _RemoteSaasConnector(BaseConnector):
    """Shared scaffold: validates required config/secret keys; live fetch not built."""

    provider_label: str = "remote"
    required_config: tuple[str, ...] = ()
    requires_secret: bool = True

    @classmethod
    def validate_config(cls, config: dict, secret: str | None) -> None:
        config = config or {}
        missing = [k for k in cls.required_config if not config.get(k)]
        if missing:
            raise ValueError(
                f"{cls.provider_label} connector requires config keys: {', '.join(missing)}"
            )
        if cls.requires_secret and not secret:
            raise ValueError(f"{cls.provider_label} connector requires a credential/token (secret)")

    async def fetch(self, cursor: str | None, secret: str | None) -> SyncBatch:
        raise ConnectorNotConfigured(
            f"The {self.provider_label} connector's live sync requires OAuth credentials and "
            "network access, which are not enabled in this build. The framework, ACL sync and "
            "identity mapping are complete; implement fetch() to activate it."
        )


class GoogleDriveConnector(_RemoteSaasConnector):
    """Needs a shared drive id (or 'my-drive') plus an OAuth refresh token as the secret."""

    kind = DataSourceKind.GOOGLE_DRIVE
    provider_label = "Google Drive"
    required_config = ("drive_id",)


class SlackConnector(_RemoteSaasConnector):
    """Needs which channels to index; bot token stored as the secret."""

    kind = DataSourceKind.SLACK
    provider_label = "Slack"
    required_config = ("channels",)


class GitHubConnector(_RemoteSaasConnector):
    """Needs owner/repo; a PAT or app installation token stored as the secret."""

    kind = DataSourceKind.GITHUB
    provider_label = "GitHub"
    required_config = ("repo",)


class NotionConnector(_RemoteSaasConnector):
    """Needs a Notion integration token as the secret; optional root page id in config."""

    kind = DataSourceKind.NOTION
    provider_label = "Notion"
    required_config = ()


class ConfluenceConnector(_RemoteSaasConnector):
    """Needs base_url + space key; API token stored as the secret."""

    kind = DataSourceKind.CONFLUENCE
    provider_label = "Confluence"
    required_config = ("base_url", "space")
