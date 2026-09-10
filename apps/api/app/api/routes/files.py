"""Capability-URL file serving for image documents.

An image document's summary chunk embeds an absolute link to its original bytes so any
LLM that legitimately retrieved the chunk can fetch the image for its own vision
context. The link is a capability URL: the path carries an unguessable 256-bit token
(stamped into ``Document.meta`` at ingestion) instead of requiring auth headers, which
generic LLM fetch tools cannot attach. Anyone who can read the chunk was already
permitted to see the image, so possession of the token IS the authorization - the same
model as Slack/Notion attachment links.

Safety: responses are served with a raster-image media-type whitelist, ``nosniff`` and
an inline/attachment split so a non-raster upload (e.g. SVG, which can script) can
never execute in a browser context. Quarantined documents 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import check_login_rate_limit, enforce_login_rate_limit
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.services.ingestion import sniffed_image_mime
from app.services.storage import get_storage, sanitize_filename

router = APIRouter(prefix="/files", tags=["files"])

_INLINE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
"""Raster formats safe to render inline; anything else downloads as opaque bytes."""

_MIN_TOKEN_LEN = 32
"""``token_urlsafe(32)`` yields 43 chars; bound the path segment to cheap-reject junk."""

_MAX_TOKEN_LEN = 128
"""Upper bound on the capability-token path segment (see :data:`_MIN_TOKEN_LEN`)."""


@router.get("/{token}")
async def get_file(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve an image document's original bytes by capability token (no auth headers).

    Uniform 404s: a bad token, a non-image document, a quarantined document and a
    missing/unreadable blob are indistinguishable, so the endpoint leaks nothing about what
    exists.

    The miss budget is consulted (without counting) BEFORE the DB probe: once a scanner is
    throttled it must stop driving lookups entirely, not just receive a different status
    code. Only actual misses increment the counter, and the bucket is keyed per client (not
    per token - a per-token bucket would give every guess a fresh budget and never throttle
    a scan).

    Authorization is on the BYTES, never on the client-supplied ``mime_type`` recorded at
    upload. Trusting that field would turn this public endpoint into an unauthenticated
    server for arbitrary unscanned content (upload any file labelled image/png).
    """
    if not (_MIN_TOKEN_LEN <= len(token) <= _MAX_TOKEN_LEN):
        raise HTTPException(status_code=404, detail="Not found")
    await check_login_rate_limit(request, "files")
    stmt = select(Document).where(Document.meta["file_token"].as_string() == token).limit(1)
    document = (await db.execute(stmt)).scalars().first()
    if (
        document is None
        or not document.storage_key
        or document.status == DocumentStatus.QUARANTINED
    ):
        await enforce_login_rate_limit(request, "files")
        raise HTTPException(status_code=404, detail="Not found")

    try:
        data = await get_storage().load(document.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc

    sniffed = sniffed_image_mime(data)
    if sniffed is None or sniffed not in _INLINE_IMAGE_MIMES:
        await enforce_login_rate_limit(request, "files")
        raise HTTPException(status_code=404, detail="Not found")

    filename = sanitize_filename(document.title) or "image"
    return Response(
        content=data,
        media_type=sniffed,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )
