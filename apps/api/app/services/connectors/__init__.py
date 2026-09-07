"""Knowledge data-source connectors.

A connector knows how to pull documents - and their source-system ACLs - out of an
external system. The sync engine (:mod:`app.services.datasource_sync`) drives connectors
and materialises what they return into ``Document`` rows + ``AccessGrant`` ACLs, so the
permission engine enforces connector permissions unchanged.

The reference :class:`~app.services.connectors.local_folder.LocalFolderConnector` runs
entirely locally (a folder tree + a sidecar ACL file) and exercises the full path,
including source-ACL mapping and incremental re-sync, with no network or credentials.
Real SaaS connectors are scaffolded behind the same interface.
"""

from __future__ import annotations

from app.services.connectors.base import (
    BaseConnector,
    ConnectorError,
    ConnectorNotConfigured,
    RemoteDocument,
    RemotePrincipal,
    SyncBatch,
)
from app.services.connectors.registry import get_connector, registered_kinds

__all__ = [
    "BaseConnector",
    "ConnectorError",
    "ConnectorNotConfigured",
    "RemoteDocument",
    "RemotePrincipal",
    "SyncBatch",
    "get_connector",
    "registered_kinds",
]
