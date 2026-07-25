"""Anthropic Messages API wire adapter (chat only - Anthropic has no embeddings API).

Speaks ``POST {base}/v1/messages`` with ``x-api-key`` auth and the Anthropic SSE
streaming shape (``content_block_delta``/``text_delta`` events, ended by
``message_stop``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

SUPPORTS_EMBEDDINGS = False

_API_VERSION = "2023-06-01"

# ``max_tokens`` is REQUIRED by the Messages API; used when the caller passes ``None``.
# Kept generous (every current Claude model supports far more) because on the Claude 5
# family thinking is on by default and ``max_tokens`` caps thinking PLUS visible text -
# the budget must leave reasoning headroom or a long grounded answer gets silently
# truncated (or emptied) on the Anthropic path when no explicit cap is set.
_DEFAULT_MAX_TOKENS = 16384

# Model families that REJECT the ``temperature`` parameter with a 400 (sampling
# parameters were removed from Opus 4.7+ and the whole Claude 5 family - Opus 5,
# Sonnet 5, Fable and Mythos). ``temperature`` is sent only to models outside these
# prefixes.
_NO_TEMPERATURE_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
)

_STOP_REASONS = {
    "end_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    # Claude 4.5+/5 safety classifiers decline with HTTP 200 + ``refusal``; normalize to
    # the OpenAI-style vocabulary the facade exposes.
    "refusal": "content_filter",
}


def headers(key: str | None) -> dict[str, str]:
    """Request headers; a keyless call omits ``x-api-key`` entirely."""
    built = {
        "Content-Type": "application/json",
        "anthropic-version": _API_VERSION,
    }
    if key:
        built["x-api-key"] = key
    return built


def chat_url(base: str, model: str, *, stream: bool = False) -> str:
    return f"{base}/v1/messages"


def chat_payload(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
) -> dict[str, Any]:
    # System-role messages become the top-level ``system`` string; every other message
    # maps to user/assistant ("tool" content is treated as user text - the facade's
    # chat surface has no native tool-result blocks). A message carrying ``images``
    # becomes content blocks (images first, per Anthropic's guidance, then the text);
    # text-only content stays a plain string.
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "system":
            system_parts.append(content)
            continue
        images = message.get("images") or []
        blocks: str | list[dict[str, Any]]
        if images:
            blocks = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image["media_type"],
                        "data": image["data"],
                    },
                }
                for image in images
            ]
            if content:
                blocks.append({"type": "text", "text": content})
        else:
            blocks = content
        turns.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens or _DEFAULT_MAX_TOKENS,
        "messages": turns,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if not model.startswith(_NO_TEMPERATURE_PREFIXES):
        payload["temperature"] = temperature
    if stream:
        payload["stream"] = True
    return payload


def parse_chat(data: dict[str, Any]) -> tuple[str, int, int, str | None]:
    """Return ``(text, tokens_in, tokens_out, finish_reason)``."""
    text = "".join(
        block.get("text") or ""
        for block in data.get("content") or []
        if block.get("type") == "text"
    )
    usage = data.get("usage") or {}
    stop_reason = data.get("stop_reason")
    if stop_reason:
        stop_reason = _STOP_REASONS.get(stop_reason, stop_reason)
    return (
        text,
        usage.get("input_tokens") or 0,
        usage.get("output_tokens") or 0,
        stop_reason,
    )


async def iter_chat_deltas(resp: Any) -> AsyncIterator[str]:
    """Yield text deltas from an Anthropic SSE response.

    Text arrives as ``content_block_delta`` events whose ``delta.type`` is
    ``text_delta``; ``message_stop`` ends the stream. A ``{"type": "error"}`` event is a
    provider failure - raise so the facade applies its normal fallback rules.
    """
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        chunk = line[len("data:") :].strip()
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        kind = obj.get("type")
        if kind == "message_stop":
            break
        if kind == "error":
            error = obj.get("error") or {}
            raise RuntimeError(
                f"Anthropic stream error: {error.get('type')}: {error.get('message')}"
            )
        if kind == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield delta["text"]
