"""Maps a :class:`DataSourceKind` to its connector implementation."""

from __future__ import annotations

from app.models.datasource import DataSource
from app.models.enums import DataSourceKind
from app.services.connectors.base import BaseConnector
from app.services.connectors.local_folder import LocalFolderConnector
from app.services.connectors.remote import (
    ConfluenceConnector,
    GitHubConnector,
    GoogleDriveConnector,
    NotionConnector,
    SlackConnector,
)

_REGISTRY: dict[DataSourceKind, type[BaseConnector]] = {
    DataSourceKind.LOCAL_FOLDER: LocalFolderConnector,
    DataSourceKind.GOOGLE_DRIVE: GoogleDriveConnector,
    DataSourceKind.SLACK: SlackConnector,
    DataSourceKind.GITHUB: GitHubConnector,
    DataSourceKind.NOTION: NotionConnector,
    DataSourceKind.CONFLUENCE: ConfluenceConnector,
}


def connector_class(kind: DataSourceKind) -> type[BaseConnector]:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:  # pragma: no cover - enum keeps this exhaustive
        raise ValueError(f"No connector registered for kind {kind}") from exc


def get_connector(source: DataSource) -> BaseConnector:
    """Instantiate the connector for a data source."""
    return connector_class(source.kind)(source)


def registered_kinds() -> list[DataSourceKind]:
    return list(_REGISTRY)
