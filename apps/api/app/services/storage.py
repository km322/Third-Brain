"""Blob storage abstraction for original document bytes.

Two backends are supported, selected by ``settings.STORAGE_BACKEND``:

* ``local`` - writes under ``settings.STORAGE_LOCAL_PATH`` (good for dev + single node).
* ``s3``    - any S3-compatible object store (AWS S3, MinIO, R2, …) via boto3.

The public interface is intentionally tiny (``save``/``load``/``delete``/``exists``)
so the rest of the app never cares which backend is active. All blocking I/O is
off-loaded to a thread so the async event loop is never blocked. Heavy imports
(``boto3``) are done lazily so the module imports cleanly without them installed.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str | None) -> str:
    """Reduce an arbitrary filename to a safe, storable basename."""
    base = Path(name or "").name.strip() or "file"
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "file"
    return cleaned[:200]


def build_storage_key(org_id: uuid.UUID, document_id: uuid.UUID, filename: str | None) -> str:
    """Deterministic, collision-free object key namespaced by org + document."""
    return f"{org_id}/{document_id}/{sanitize_filename(filename)}"


class StorageBackend(Protocol):
    async def save(self, key: str, data: bytes, content_type: str | None = None) -> str: ...

    async def load(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


class LocalStorage:
    """Filesystem-backed storage rooted at a configurable base directory."""

    def __init__(self, base_path: str) -> None:
        self.base = Path(base_path)

    def _full(self, key: str) -> Path:
        """Resolve ``key`` under the base directory, guarding against path traversal."""
        target = (self.base / key).resolve()
        base = self.base.resolve()
        if base not in target.parents and target != base:
            raise ValueError(f"Refusing to access key outside storage root: {key!r}")
        return target

    def _save_sync(self, key: str, data: bytes) -> None:
        path = self._full(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        await asyncio.to_thread(self._save_sync, key, data)
        return key

    async def load(self, key: str) -> bytes:
        return await asyncio.to_thread(lambda: self._full(key).read_bytes())

    async def delete(self, key: str) -> None:
        def _rm() -> None:
            try:
                self._full(key).unlink(missing_ok=True)
            except FileNotFoundError:  # pragma: no cover
                pass

        await asyncio.to_thread(_rm)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(lambda: self._full(key).is_file())


class S3Storage:
    """S3-compatible object storage. ``boto3`` is imported lazily on first use."""

    def __init__(self) -> None:
        self.bucket = settings.S3_BUCKET
        self._client = None

    def _get_client(self):  # pragma: no cover - requires network/credentials
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                region_name=settings.S3_REGION,
            )
        return self._client

    async def save(self, key: str, data: bytes, content_type: str | None = None) -> str:
        def _put() -> None:  # pragma: no cover
            extra = {"ContentType": content_type} if content_type else {}
            self._get_client().put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

        await asyncio.to_thread(_put)
        return key

    async def load(self, key: str) -> bytes:
        def _get() -> bytes:  # pragma: no cover
            resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        def _del() -> None:  # pragma: no cover
            self._get_client().delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_del)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:  # pragma: no cover
            try:
                self._get_client().head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the process-wide storage backend selected by configuration."""
    global _storage
    if _storage is None:
        if settings.STORAGE_BACKEND == "s3":
            _storage = S3Storage()
        else:
            _storage = LocalStorage(settings.STORAGE_LOCAL_PATH)
        logger.info("Storage backend initialized: %s", settings.STORAGE_BACKEND)
    return _storage
