"""Google Gemini wire adapter (chat + embeddings).

Speaks ``POST {base}/v1beta/models/{model}:generateContent`` (and the
``:streamGenerateContent?alt=sse`` SSE variant) plus ``:batchEmbedContents`` with
``x-goog-api-key`` auth.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import settings

SUPPORTS_EMBEDDINGS = True

_FINISH_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}


def _model_id(model: str) -> str:
    """Bare model id for URLs; tolerate a configured ``models/…`` resource name."""
    return model.removeprefix("models/")


def headers(key: str | None) -> dict[str, str]:
    """Request headers; a keyless call omits ``x-goog-api-key`` entirely."""
    built = {"Content-Type": "application/json"}
    if key:
        built["x-goog-api-key"] = key
    return built


def chat_url(base: str, model: str, *, stream: bool = False) -> str:
    if stream:
        return f"{base}/v1beta/models/{_model_id(model)}:streamGenerateContent?alt=sse"
    return f"{base}/v1beta/models/{_model_id(model)}:generateContent"


def chat_payload(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
) -> dict[str, Any]:
    """Build the ``generateContent`` request body.

    System-role messages become ``systemInstruction``; assistant maps to "model" and
    any "tool" content is treated as user text. The model is addressed in the URL,
    not the body, and streaming is selected by the endpoint, not a body flag.

    Images map to inline_data parts ahead of the text; text-only messages keep their
    exact single-text-part shape.
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "system":
            system_parts.append(content)
        else:
            parts: list[dict[str, Any]] = [
                {"inline_data": {"mime_type": image["media_type"], "data": image["data"]}}
                for image in (message.get("images") or [])
            ]
            if content or not parts:
                parts.append({"text": content})
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": parts,
                }
            )
    generation_config: dict[str, Any] = {"temperature": temperature}
    if max_tokens:
        generation_config["maxOutputTokens"] = max_tokens
    payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return payload


def _candidate_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text") or "" for part in parts)


def parse_chat(data: dict[str, Any]) -> tuple[str, int, int, str | None]:
    """Return ``(text, tokens_in, tokens_out, finish_reason)``."""
    usage = data.get("usageMetadata") or {}
    candidates = data.get("candidates") or []
    finish_reason = candidates[0].get("finishReason") if candidates else None
    if finish_reason:
        finish_reason = _FINISH_REASONS.get(finish_reason, finish_reason)
    return (
        _candidate_text(data),
        usage.get("promptTokenCount") or 0,
        usage.get("candidatesTokenCount") or 0,
        finish_reason,
    )


async def iter_chat_deltas(resp: Any) -> AsyncIterator[str]:
    """Yield text deltas from a ``streamGenerateContent?alt=sse`` response.

    Each ``data:`` line carries a JSON chunk shaped like a non-streaming response; the
    candidate's text parts are the delta. The stream simply ends - no sentinel event.
    """
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        chunk = line[len("data:") :].strip()
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        delta = _candidate_text(obj)
        if delta:
            yield delta


def embeddings_url(base: str, model: str) -> str:
    return f"{base}/v1beta/models/{_model_id(model)}:batchEmbedContents"


def embeddings_payload(model: str, inputs: list[str] | list[list[int]]) -> dict[str, Any]:
    """Build the ``batchEmbedContents`` request body.

    Pre-tokenized input is rejected as ``ValueError`` (not ``RuntimeError``) so the
    OpenAI-compat surface can report it as a 400 client error rather than a 502 provider
    failure.
    """
    if any(not isinstance(item, str) for item in inputs):
        raise ValueError(
            "Pre-tokenized (token-id array) inputs are not supported by the Google "
            "embedding provider; send plain text strings."
        )
    return {
        "requests": [
            {
                "model": f"models/{_model_id(model)}",
                "content": {"parts": [{"text": item}]},
                "outputDimensionality": settings.EMBEDDING_DIM,
            }
            for item in inputs
        ]
    }


def parse_embeddings(data: dict[str, Any]) -> tuple[list[list[float]], int]:
    """Return ``(vectors, total_tokens)`` - Gemini reports no embedding usage (0).

    When ``outputDimensionality`` is below the model's native size the returned vectors
    are NOT unit-normalized; L2-normalize them here so pgvector cosine ranking stays
    comparable across documents and queries.
    """
    vectors: list[list[float]] = []
    for row in data.get("embeddings") or []:
        values = [float(v) for v in row.get("values") or []]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        vectors.append([v / norm for v in values])
    return vectors, 0
