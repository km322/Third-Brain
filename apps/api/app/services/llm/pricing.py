"""Rough per-1K-token USD pricing for cost estimation in usage records.

These are approximations used for dashboards/budgets; exact billing should reconcile
against provider invoices. Unknown models fall back to a small default.
"""

from __future__ import annotations

COMPLETION_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "claude-fable-5": (0.01, 0.05),
    "claude-mythos-5": (0.01, 0.05),
    "claude-opus-5": (0.005, 0.025),
    "claude-opus-4-8": (0.005, 0.025),
    "claude-opus-4-7": (0.005, 0.025),
    "claude-opus-4-6": (0.005, 0.025),
    "claude-sonnet-5": (0.003, 0.015),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-haiku-4-5": (0.001, 0.005),
    "gemini-2.5-pro": (0.00125, 0.01),
    "gemini-2.5-flash": (0.0003, 0.0025),
    "gemini-2.5-flash-lite": (0.0001, 0.0004),
}
"""Model -> ``(prompt_per_1k, completion_per_1k)``, grouped by vendor: OpenAI (gpt-*),
then Anthropic (claude-*), then Google (gemini-*)."""

EMBEDDING_PRICES: dict[str, float] = {
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
    "text-embedding-ada-002": 0.0001,
    "gemini-embedding-001": 0.00015,
}
"""Model -> price per 1k tokens."""

_DEFAULT_COMPLETION = (0.001, 0.002)
_DEFAULT_EMBEDDING = 0.00002

OFFLINE_PROVIDERS = frozenset({"offline", "fake"})
"""Provider labels emitted by the deterministic offline stub in ``app.services.llm.client``.

Usage produced by these providers costs nothing, so callers zero out its ``cost_usd``."""


def is_billable_provider(provider: str | None) -> bool:
    """True when ``provider`` denotes a real, paid provider (not the offline stub)."""
    return bool(provider) and provider not in OFFLINE_PROVIDERS


def _match(model: str, table: dict) -> object | None:
    """Look ``model`` up in a price table, falling back to substring matching.

    Prefers the longest (most specific) key that is a substring of the model name, so a
    dated/prefixed variant like "gpt-4o-mini-2024-07-18" matches "gpt-4o-mini" and not
    the shorter "gpt-4o" (whose price is ~25-33x higher).
    """
    if model in table:
        return table[model]
    for key in sorted(table, key=len, reverse=True):
        if key in model:
            return table[key]
    return None


def completion_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    prompt, completion = _match(model, COMPLETION_PRICES) or _DEFAULT_COMPLETION
    return (tokens_in / 1000) * prompt + (tokens_out / 1000) * completion


def embedding_cost(model: str, tokens: int) -> float:
    per_1k = _match(model, EMBEDDING_PRICES) or _DEFAULT_EMBEDDING
    return (tokens / 1000) * per_1k
