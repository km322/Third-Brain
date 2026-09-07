"""OpenAI-compatible wire adapter (OpenAI, Azure OpenAI, Ollama, vLLM, gateways).

Speaks ``POST {base}/chat/completions`` and ``POST {base}/embeddings`` with bearer
auth and the OpenAI SSE streaming shape (``data:`` JSON chunks terminated by
``data: [DONE]``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

SUPPORTS_EMBEDDINGS = True


def headers(key: str | None) -> dict[str, str]:
    """Request headers, adding ``Authorization`` only when a key is present.

    A keyless connector (custom ``api_base``, no token) must send NO bearer token rather
    than the literal string ``"Bearer None"`` - and must never carry a platform key.
    """
    built = {"Content-Type": "application/json"}
    if key:
        built["Authorization"] = f"Bearer {key}"
    return built


def chat_url(base: str, model: str, *, stream: bool = False) -> str:
    return f"{base}/chat/completions"


def _to_wire_message(message: dict[str, Any]) -> dict[str, Any]:
    """Map a facade message to the OpenAI wire shape.

    Text-only messages pass through untouched (string ``content``). A message carrying
    ``images`` becomes the content-parts form: one ``text`` part plus one ``image_url``
    part per image as a base64 data URL.
    """
    images = message.get("images")
    if not images:
        return message
    parts: list[dict[str, Any]] = []
    if message.get("content"):
        parts.append({"type": "text", "text": message["content"]})
    for image in images:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image['media_type']};base64,{image['data']}"},
            }
        )
    return {"role": message["role"], "content": parts}


def chat_payload(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [_to_wire_message(m) for m in messages],
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if stream:
        payload["stream"] = True
    return payload


def parse_chat(data: dict[str, Any]) -> tuple[str, int, int, str | None]:
    """Return ``(text, tokens_in, tokens_out, finish_reason)``; zero tokens when the
    provider omits usage (the facade falls back to an estimate)."""
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    text = choice["message"].get("content") or ""
    return (
        text,
        usage.get("prompt_tokens") or 0,
        usage.get("completion_tokens") or 0,
        choice.get("finish_reason"),
    )


async def iter_chat_deltas(resp: Any) -> AsyncIterator[str]:
    """Yield content deltas from an OpenAI-style SSE response."""
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        chunk = line[len("data:") :].strip()
        if chunk == "[DONE]":
            break
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        delta = (obj["choices"][0].get("delta") or {}).get("content")
        if delta:
            yield delta


def embeddings_url(base: str, model: str) -> str:
    return f"{base}/embeddings"


def embeddings_payload(model: str, inputs: list[str] | list[list[int]]) -> dict[str, Any]:
    return {"model": model, "input": inputs}


def parse_embeddings(data: dict[str, Any]) -> tuple[list[list[float]], int]:
    """Return ``(vectors, total_tokens)``; zero tokens when usage is omitted."""
    vectors = [row["embedding"] for row in data["data"]]
    tokens = (data.get("usage") or {}).get("total_tokens") or 0
    return vectors, tokens
