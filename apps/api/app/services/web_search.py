"""Pluggable web search for grounding the assistant in world knowledge.

Mirrors the LLM philosophy: a deterministic offline ``stub`` provider (no network) is used
in dev/CI so web grounding is fully testable, while the live ``tavily`` provider can be
dropped in with an API key. ``none`` (the default) disables web grounding entirely.
Web results are returned as citeable sources alongside internal passages; they never widen
document access - they are external content.

``tavily`` is the only live provider implemented. Anything else is refused rather than
guessed at, so a provider this module cannot speak can never be handed to whichever
vendor happens to be wired up.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_PROVIDERS = ("none", "stub", "tavily")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


def web_search_enabled() -> bool:
    return settings.WEB_SEARCH_PROVIDER != "none"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "result"


def _stub_results(query: str, n: int) -> list[WebResult]:
    """Deterministic canned results derived from the query (no network)."""
    slug = _slug(query)
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    return [
        WebResult(
            title=f"{query[:60]} - reference {i + 1}",
            url=f"https://example.org/{slug}/{digest}-{i + 1}",
            snippet=(
                f"Overview covering '{query[:80]}'. This is deterministic offline web "
                f"grounding result {i + 1}; configure WEB_SEARCH_PROVIDER for live results."
            ),
        )
        for i in range(n)
    ]


async def _tavily_results(query: str, n: int) -> list[WebResult]:  # pragma: no cover - needs key
    api_key = settings.WEB_SEARCH_API_KEY
    if not api_key:
        return []
    base = settings.WEB_SEARCH_BASE_URL or "https://api.tavily.com"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{base}/search",
            json={"api_key": api_key, "query": query, "max_results": n},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        WebResult(
            title=r.get("title") or r.get("url", ""),
            url=r.get("url", ""),
            snippet=(r.get("content") or "")[:600],
        )
        for r in (data.get("results") or [])[:n]
    ]


async def web_search(query: str, *, max_results: int | None = None) -> list[WebResult]:
    """Return web results for ``query`` from the configured provider (``[]`` when disabled).

    Raises :class:`RuntimeError` for a provider this module cannot speak. ``WEB_SEARCH_PROVIDER``
    is validated as a closed set at config load, so this is defence in depth - it exists so an
    unsupported value can never be answered by sending the operator's key somewhere else.
    """
    provider = settings.WEB_SEARCH_PROVIDER
    n = max_results or settings.WEB_SEARCH_MAX_RESULTS
    if not web_search_enabled() or n <= 0 or not query.strip():
        return []
    if provider == "stub":
        return _stub_results(query, n)
    if provider != "tavily":
        raise RuntimeError(
            f"Unsupported WEB_SEARCH_PROVIDER {provider!r}; supported values are "
            f"{', '.join(SUPPORTED_PROVIDERS)}."
        )
    try:
        return await _tavily_results(query, n)
    except Exception as exc:  # pragma: no cover - network/provider failure is non-fatal
        logger.warning("web_search_failed", provider=provider, error=str(exc))
        return []
