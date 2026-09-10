"""The connector interface every data source implements.

A connector is a thin adapter: given a :class:`DataSource`'s config/secret and an opaque
cursor, ``fetch`` returns a :class:`SyncBatch` of :class:`RemoteDocument` objects (content
+ the source-system ACL) plus the next cursor. The sync engine owns everything else
(persistence, chunking/embedding, ACL materialisation, deletion). Connectors do no
database work and hold no Third Brain concepts beyond the two small dataclasses below.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime

from app.models.datasource import DataSource
from app.models.enums import DataSourceKind, ExternalPrincipalKind


class ConnectorError(Exception):
    """A connector failed to fetch (bad config, upstream error, …)."""


class ConnectorNotConfigured(ConnectorError):
    """The connector needs credentials/network that are not available in this deployment."""


@dataclass
class RemotePrincipal:
    """A principal in the source system's ACL for a document."""

    external_id: str
    kind: ExternalPrincipalKind = ExternalPrincipalKind.USER


@dataclass
class RemoteDocument:
    """One document pulled from a source system, with its source-system ACL.

    ``external_id`` is a stable id within the source (path, file id, message ts, …).
    ``deleted`` is how incremental connectors signal removals.
    """

    external_id: str
    title: str
    content: bytes = b""
    mime_type: str | None = None
    source_uri: str | None = None
    updated_at: datetime | None = None
    acl: list[RemotePrincipal] = field(default_factory=list)
    deleted: bool = False


@dataclass
class SyncBatch:
    """A batch of changes plus the cursor to resume from next time.

    ``full_sync`` marks the batch as the AUTHORITATIVE full set for the source: the sync
    engine then treats any previously-synced document not present here as deleted. An
    incremental connector sets it ``False`` and signals removals via ``RemoteDocument.deleted``.
    """

    documents: list[RemoteDocument] = field(default_factory=list)
    cursor: str | None = None
    full_sync: bool = False


class BaseConnector(abc.ABC):
    """Base class for all data-source connectors."""

    kind: DataSourceKind

    def __init__(self, source: DataSource) -> None:
        self.source = source
        self.config = source.config or {}

    @classmethod
    @abc.abstractmethod
    def validate_config(cls, config: dict, secret: str | None) -> None:
        """Raise ``ValueError`` if ``config``/``secret`` are insufficient for this connector.

        Called on data-source create/update before anything is persisted. Every connector
        implements it (the reference connector only requires a ``root``; SaaS connectors
        require their provider keys + a secret).
        """

    @abc.abstractmethod
    async def fetch(self, cursor: str | None, secret: str | None) -> SyncBatch:
        """Return the changes since ``cursor`` (or a full snapshot when unsupported)."""
        raise NotImplementedError
