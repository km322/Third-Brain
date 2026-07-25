"""LLM client facade for embeddings + chat completions.

Talks to OpenAI-compatible endpoints (OpenAI, Azure OpenAI, Ollama, vLLM, gateways),
the Anthropic Messages API, and Google Gemini using a plain ``httpx`` client - no
third-party SDKs. Wire-shape specifics live in :mod:`app.services.llm.providers`; this
module owns the shared httpx pool, spans, metering logs, the offline stub and the
key/base coupling rules. The ``provider`` keyword selects the wire adapter; when it is
omitted the platform provider is chosen by configured-key priority (completions:
OpenAI -> Anthropic -> Google; embeddings: OpenAI -> Google - Anthropic has no
embeddings API).

When no API key is configured anywhere (or ``EMBEDDING_PROVIDER=fake``), a
deterministic **offline** provider is used so the whole product - ingestion, retrieval,
chat - runs end-to-end without network access. Offline answers are clearly labelled and
are only meant for local dev, CI and demos; configure a real key for production.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.core.config import settings
from app.core.logging import get_logger
from app.core.telemetry import get_tracer
from app.services.llm.providers import PROVIDERS
from app.services.llm.providers import openai as openai_provider

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Streaming keeps the same connect/write/pool budget as the non-streaming path but a
# generous per-read timeout for inter-chunk gaps. Never ``None``: an unbounded timeout
# lets a hung provider connection block forever and pin the request/DB connection.
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# Keep-alive pool for the shared client below. Bounded so a burst of concurrent requests
# can't open unbounded provider connections.
_HTTPX_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=100)

# One process-wide httpx client (connection pool) reused across every provider call.
# Rebuilding a client per call tears down and re-establishes the TCP+TLS pool on each
# request - e.g. once per embedding batch during ingestion - so the pool is shared and
# created lazily on first use inside the running event loop.
_client: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it on first use.

    No ``base_url``/auth is configured: every call builds the full URL and passes its own
    per-request headers and timeout, so the same pooled client serves any provider. Lazy
    init is safe with a plain module global because it runs in one event loop and has no
    ``await`` point between the check and the assignment.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(limits=_HTTPX_LIMITS)
    return _client


async def aclose_llm_client() -> None:
    """Close the shared httpx client and its connection pool (call on app shutdown)."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


@dataclass
class ImageAttachment:
    """An image handed to a vision-capable chat model (base64 payload + media type)."""

    media_type: str
    data_b64: str


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # Optional vision inputs; each provider adapter maps these to its wire shape and
    # the offline stub ignores them (its deterministic answers stay text-only).
    images: list[ImageAttachment] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.images:
            out["images"] = [
                {"media_type": image.media_type, "data": image.data_b64} for image in self.images
            ]
        return out


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    tokens: int = 0
    provider: str = ""
    latency_ms: int = 0


@dataclass
class CompletionResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = ""
    finish_reason: str | None = None
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _platform_pair(provider: str) -> tuple[str | None, str]:
    """The platform ``(api_key, base_url)`` for a wire provider.

    Each platform key is only ever paired with its OWN provider's platform base - the
    OpenAI key can never be sent to an Anthropic/Google endpoint or vice versa.
    """
    if provider == "anthropic":
        return settings.ANTHROPIC_API_KEY, settings.ANTHROPIC_BASE_URL
    if provider == "google":
        return settings.GOOGLE_API_KEY, settings.GOOGLE_BASE_URL
    return settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL


def _platform_provider(purpose: str) -> str | None:
    """The platform fallback provider chosen by configured-key priority.

    Completions: OpenAI -> Anthropic -> Google; embeddings: OpenAI -> Google (Anthropic
    has no embeddings API). ``None`` means no key is configured -> offline stub.
    """
    if settings.OPENAI_API_KEY:
        return "openai"
    if purpose == "completion" and settings.ANTHROPIC_API_KEY:
        return "anthropic"
    if settings.GOOGLE_API_KEY:
        return "google"
    return None


def _is_openai_family(model: str) -> bool:
    """Whether ``model`` names an OpenAI-family model no other provider could serve."""
    return model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt", "text-embedding-"))


# Default models used when the platform fallback lands on a non-OpenAI provider while
# the configured default model is still an OpenAI-family name.
_PLATFORM_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "completion": {"anthropic": "claude-opus-5", "google": "gemini-2.5-flash"},
    "embedding": {"google": "gemini-embedding-001"},
}


def _resolve_target(
    model: str, api_key: str | None, api_base: str | None, provider: str | None, purpose: str
) -> tuple[str, str]:
    """Resolve the ``(model, wire_provider)`` a call will use.

    An explicit ``provider`` (an org connector's type) wins. ``provider=None`` with
    explicit endpoint params keeps the historical OpenAI-compatible wire shape.
    Otherwise the platform provider is chosen by key priority and, when it is not
    OpenAI-compatible, its own default model is substituted for a configured
    OpenAI-family default (which that provider could never serve).
    """
    if provider is not None:
        return model, provider
    if api_key or api_base:
        return model, "openai"
    wire = _platform_provider(purpose)
    if wire and wire != "openai":
        default = _PLATFORM_DEFAULT_MODELS[purpose].get(wire)
        if default and _is_openai_family(model):
            model = default
    return model, wire or "openai"


def effective_provider(provider: str | None = None, *, purpose: str = "completion") -> str:
    """Public: the wire provider a call would use (``"offline"`` when no key exists).

    Callers that cache embeddings include this in the cache key so vectors from two
    different providers can never share an entry.
    """
    return provider or _platform_provider(purpose) or "offline"


def _endpoint(
    api_key: str | None, api_base: str | None, provider: str = "openai"
) -> tuple[str | None, str]:
    """Resolve the (api_key, base_url) to use.

    ``api_key`` and ``api_base`` are treated as a COUPLED pair, never resolved with two
    independent ``or`` fallbacks: a per-call / per-connector ``api_base`` is used only with
    its OWN key (or with no key at all for a keyless local provider such as Ollama/vLLM), and
    NEVER paired with ANY platform key (OpenAI, Anthropic or Google). Otherwise an org that
    points a connector at a server it controls would have a platform provider key sent to
    that server in an auth header - a credential-exfiltration hole. Each platform key is
    used only with its own provider's platform base URL (see :func:`_platform_pair`).
    """
    if api_base:
        return api_key, api_base.rstrip("/")
    platform_key, platform_base = _platform_pair(provider)
    return (api_key or platform_key), platform_base.rstrip("/")


def _offline(
    api_key: str | None,
    api_base: str | None = None,
    provider: str | None = None,
    *,
    purpose: str = "completion",
) -> bool:
    """Use the deterministic offline provider when forced, or when no real endpoint exists.

    A custom ``api_base`` or per-call ``api_key`` (an org connector) is a real target -
    keyless local providers are legitimate - so we go online and use only that connector's
    own credentials rather than substituting a platform key (see :func:`_endpoint`). With
    an explicit ``provider`` the call is online iff that provider's platform key is set;
    otherwise the platform fallback chain for ``purpose`` decides.
    """
    if settings.EMBEDDING_PROVIDER in ("fake", "offline"):
        return True
    if api_base or api_key:
        return False
    if provider:
        return not _platform_pair(provider)[0]
    return _platform_provider(purpose) is None


def is_offline(
    api_key: str | None = None,
    api_base: str | None = None,
    provider: str | None = None,
    *,
    purpose: str = "embedding",
) -> bool:
    """Public predicate: will a call with these params use the deterministic offline stub?

    Callers that cache embeddings key on this so an offline (fake) vector and a live-provider
    vector for the same (model, endpoint) can never share a cache entry and poison each other.
    """
    return _offline(api_key, api_base, provider, purpose=purpose)


def _embedding_offline_is_misconfig(
    api_key: str | None, api_base: str | None, provider: str | None
) -> bool:
    """True when an embedding call would fake vectors ONLY because no embeddings-capable key
    is configured while the platform is otherwise live (e.g. only ``ANTHROPIC_API_KEY`` set).

    Faking there silently corrupts the vector store, so the caller raises instead. A genuinely
    keyless deployment (offline dev/CI) and an explicit ``EMBEDDING_PROVIDER=fake/offline`` are
    both legitimate and return ``False``, as does any per-connector call (its own creds decide).
    """
    if settings.EMBEDDING_PROVIDER in ("fake", "offline"):
        return False
    if api_key or api_base or provider:
        return False
    return _platform_provider("embedding") is None and _platform_provider("completion") is not None


def _auth_headers(key: str | None) -> dict[str, str]:
    """OpenAI-compatible request headers (kept as the facade-level name; the per-provider
    header builders live in :mod:`app.services.llm.providers`)."""
    return openai_provider.headers(key)


# --------------------------------------------------------------------------- #
# Deterministic offline provider
# --------------------------------------------------------------------------- #
def _fake_embedding(text: str, dim: int) -> list[float]:
    """A stable, unit-normalized pseudo-embedding derived from token hashes.

    Not semantically meaningful, but consistent and non-degenerate, so retrieval,
    caching and the full pipeline exercise correctly offline.
    """
    vec = [0.0] * dim
    tokens = text.lower().split() or [text.lower()]
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, len(h), 2):
            idx = (h[i] << 8 | h[i + 1]) % dim
            vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# Matches the ``<passage id="N" title="…">body</passage>`` blocks that
# ``app.services.rag`` assembles into the grounding prompt, so the offline stub can
# answer *from* the retrieved passages instead of echoing the prompt scaffolding.
_PASSAGE_RE = re.compile(r'<passage id="(\d+)" title="([^"]*)">\s*(.*?)\s*</passage>', re.DOTALL)


def _first_sentences(text: str, limit: int = 240) -> str:
    """A short, clean lead extract: collapse whitespace, then clip at a sentence
    boundary near ``limit`` - never mid-word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if cut >= 80:
        return window[: cut + 1]
    space = window.rfind(" ")
    return (window[:space] if space >= 80 else window[:limit]).rstrip() + "…"


def _offline_completion(messages: list[ChatMessage]) -> str:
    """A deterministic, clearly-labelled stub answer.

    For a retrieval-grounded prompt (the RAG ``user`` message carries ``<passage>``
    blocks) it synthesizes a readable, *cited* extract from the retrieved passages -
    never echoing the internal prompt scaffolding - so the zero-key demo still shows
    grounded, permission-filtered, citable answers. For a plain chat it simply
    acknowledges the question. Either way it says up front that it is an offline stub.
    """
    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    passages = _PASSAGE_RE.findall(last_user)
    if passages:
        # The real user question is the trailing "Question: …" line the RAG prompt appends.
        question = ""
        if "\nQuestion:" in last_user:
            question = last_user.rsplit("\nQuestion:", 1)[-1].strip()
        lines = [
            "[offline model] No language model is configured, so Third Brain can't write a "
            "synthesized answer - but retrieval and permissions ran for real. Here is what "
            "your accessible sources say (set OPENAI_API_KEY, or add a connector, for a fully "
            "written answer):",
            "",
        ]
        if question:
            lines.append(f"On “{question}”:")
            lines.append("")
        for pid, title, body in passages[:3]:
            lines.append(f"[{pid}] {title.strip() or 'Untitled'} - {_first_sentences(body)}")
        extra = len(passages) - 3
        if extra > 0:
            lines.append("")
            lines.append(
                f"(+{extra} more permitted source{'s' if extra != 1 else ''} in the sources panel.)"
            )
        return "\n".join(lines)
    # Plain (non-RAG) chat: acknowledge the question without fabricating an answer.
    return (
        "[offline model] No live LLM provider is configured, so this is a stub response. "
        "Set OPENAI_API_KEY (or a connector) to enable real generation. "
        f"You asked: {last_user[:280]}"
    )


def _approx_tokens_from_chars(n: int) -> int:
    """Approximate token count from a character count (~4 chars/token, floor of 1)."""
    return max(1, n // 4)


def _estimate_tokens(texts: list[str]) -> int:
    """Cheap token estimate (~4 chars/token) used when a provider omits usage."""
    return _approx_tokens_from_chars(sum(len(t) for t in texts))


def _estimate_tokens_any(items: list[str] | list[list[int]]) -> int:
    """Token estimate over mixed embedding inputs: ~4 chars/token for strings, and one token
    per element for pre-tokenized (int-array) inputs."""
    total = 0
    for item in items:
        if isinstance(item, str):
            total += len(item)
        else:
            total += len(item) * 4  # counted as whole tokens by _approx_tokens_from_chars
    return _approx_tokens_from_chars(total)


def _set_server_address(span: trace.Span, base: str) -> None:
    """Record the provider host only (never the full URL, which may carry credentials)."""
    host = urlparse(base).hostname
    if host:
        span.set_attribute("server.address", host)


def _mark_offline_fallback(span: trace.Span) -> None:
    """Flag on the span that a provider error forced the deterministic offline fallback."""
    span.add_event("llm.fallback_offline")
    span.set_attribute("app.llm_fallback", True)


def _log_llm_call(
    op: str,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    fallback: bool,
    ttft_ms: int | None = None,
) -> None:
    """Emit the single structured ``llm_call`` metrics line (metadata only, never content)."""
    fields: dict[str, Any] = {
        "op": op,
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": latency_ms,
        "fallback": fallback,
    }
    if ttft_ms is not None:
        fields["ttft_ms"] = ttft_ms
    logger.info("llm_call", **fields)


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
def _embedding_input_key(item: str | list[int]) -> str:
    """Stable string key for one embedding input (a string, or an OpenAI token-id array)."""
    if isinstance(item, str):
        return item
    return " ".join(str(t) for t in item)


async def embed_texts(
    texts: list[str] | list[list[int]],
    model: str | None = None,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    provider: str | None = None,
) -> EmbeddingResult:
    model = model or settings.EMBEDDING_MODEL
    model, wire = _resolve_target(model, api_key, api_base, provider, "embedding")
    dim = settings.EMBEDDING_DIM
    key, base = _endpoint(api_key, api_base, wire)
    t0 = time.perf_counter()

    with tracer.start_as_current_span(f"embeddings {model}") as span:
        span.set_attribute("gen_ai.operation.name", "embeddings")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("app.text_count", len(texts))

        if _offline(api_key, api_base, provider, purpose="embedding"):
            if _embedding_offline_is_misconfig(api_key, api_base, provider):
                # Same loud-failure contract as a provider error: never silently fake vectors
                # into the store when the platform is live but has no embeddings-capable key.
                raise RuntimeError(
                    "No embeddings-capable provider is configured: set OPENAI_API_KEY or "
                    "GOOGLE_API_KEY (Anthropic has no embeddings API), or set "
                    "EMBEDDING_PROVIDER=fake for offline development."
                )
            span.set_attribute("gen_ai.system", "offline")
            # Token-id arrays (OpenAI/LangChain tokenized input) are keyed by their stringified
            # tokens so the deterministic stub still returns one vector per input.
            vectors = [_fake_embedding(_embedding_input_key(t), dim) for t in texts]
            result = EmbeddingResult(vectors, "offline", _estimate_tokens_any(texts), "offline")
        else:
            adapter = PROVIDERS[wire]
            span.set_attribute("gen_ai.system", wire)
            _set_server_address(span, base)
            if not adapter.SUPPORTS_EMBEDDINGS:
                # Same loud-failure contract as a provider error below: never fake vectors.
                raise RuntimeError(
                    f"The '{wire}' provider has no embeddings API; use an OpenAI-compatible "
                    "or Google connector for embeddings."
                )
            # Build the request before the try: input-shape errors (e.g. token-id arrays
            # sent to Gemini) raise ValueError for the caller to surface as a 400 client
            # error, distinct from the RuntimeError of a failed provider call.
            url = adapter.embeddings_url(base, model)
            payload = adapter.embeddings_payload(model, texts)
            try:
                resp = await _http_client().post(
                    url,
                    headers=adapter.headers(key),
                    json=payload,
                    timeout=_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                vectors, tokens = adapter.parse_embeddings(resp.json())
                result = EmbeddingResult(
                    vectors, model, tokens or _estimate_tokens_any(texts), wire
                )
            except Exception as exc:  # pragma: no cover - network/credentials dependent
                # Do NOT silently substitute offline pseudo-vectors: unlike a chat answer, a bad
                # embedding is persisted into the vector store (ingestion) or handed back to an
                # embeddings client, corrupting retrieval while looking successful. Fail loudly so
                # ingestion records the document as FAILED and the caller sees the real error.
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                logger.warning("Embedding request failed (%s)", exc)
                raise RuntimeError(f"Embedding provider call failed: {exc}") from exc

        result.latency_ms = round((time.perf_counter() - t0) * 1000)
        span.set_attribute("gen_ai.usage.input_tokens", result.tokens)
        _log_llm_call(
            "embeddings",
            result.provider,
            result.model,
            result.tokens,
            0,
            result.latency_ms,
            False,
        )
        return result


# --------------------------------------------------------------------------- #
# Completions
# --------------------------------------------------------------------------- #
async def complete(
    messages: list[ChatMessage],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    provider: str | None = None,
) -> CompletionResult:
    model = model or settings.DEFAULT_COMPLETION_MODEL
    model, wire = _resolve_target(model, api_key, api_base, provider, "completion")
    key, base = _endpoint(api_key, api_base, wire)
    t0 = time.perf_counter()

    with tracer.start_as_current_span(f"chat {model}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", model)

        fallback = False
        if _offline(api_key, api_base, provider, purpose="completion"):
            span.set_attribute("gen_ai.system", "offline")
            text = _offline_completion(messages)
            result = CompletionResult(
                text=text,
                model="offline",
                provider="offline",
                tokens_in=_estimate_tokens([m.content for m in messages]),
                tokens_out=_estimate_tokens([text]),
                finish_reason="stop",
            )
        else:
            adapter = PROVIDERS[wire]
            span.set_attribute("gen_ai.system", wire)
            _set_server_address(span, base)
            payload = adapter.chat_payload(
                model,
                [m.as_dict() for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )

            try:
                resp = await _http_client().post(
                    adapter.chat_url(base, model),
                    headers=adapter.headers(key),
                    json=payload,
                    timeout=_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                text, tokens_in, tokens_out, finish_reason = adapter.parse_chat(resp.json())
                # Fall back to an estimate (as embed_texts does) when the provider omits
                # usage or reports 0, but never overwrite a real reported non-zero count.
                result = CompletionResult(
                    text=text,
                    model=model,
                    provider=wire,
                    tokens_in=tokens_in or _estimate_tokens([m.content for m in messages]),
                    tokens_out=tokens_out or _estimate_tokens([text]),
                    finish_reason=finish_reason,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Completion request failed (%s); using offline provider",
                    exc,
                    provider=wire,
                    model=model,
                    error_class=type(exc).__name__,
                )
                fallback = True
                _mark_offline_fallback(span)
                text = _offline_completion(messages)
                result = CompletionResult(
                    text=text, model="offline", provider="offline", finish_reason="stop"
                )

        result.latency_ms = round((time.perf_counter() - t0) * 1000)
        span.set_attribute("gen_ai.usage.input_tokens", result.tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", result.tokens_out)
        if result.finish_reason:
            span.set_attribute("gen_ai.response.finish_reasons", [result.finish_reason])
        _log_llm_call(
            "chat",
            result.provider,
            result.model,
            result.tokens_in,
            result.tokens_out,
            result.latency_ms,
            fallback,
        )
        return result


async def stream_complete(
    messages: list[ChatMessage],
    model: str | None = None,
    *,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    provider: str | None = None,
    meta_out: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Yield content deltas from the provider's streaming response.

    Falls back to chunked offline text when no provider is configured or on error. When
    ``meta_out`` is supplied it is populated with the actual ``provider``/``model`` used
    (``offline`` for the deterministic stub, including the mid-stream error fallback) so
    the caller can meter cost correctly - the offline provider is free - plus
    ``latency_ms`` (total wall time) and ``ttft_ms`` (time to first yielded chunk).
    """
    model = model or settings.DEFAULT_COMPLETION_MODEL
    model, wire = _resolve_target(model, api_key, api_base, provider, "completion")
    key, base = _endpoint(api_key, api_base, wire)
    t0 = time.perf_counter()

    provider_used = "offline"
    model_used = "offline"
    fallback = False
    ttft_ms: int | None = None
    out_chars = 0

    def _set_meta(provider: str, used_model: str) -> None:
        nonlocal provider_used, model_used
        provider_used, model_used = provider, used_model
        if meta_out is not None:
            meta_out["provider"] = provider
            meta_out["model"] = used_model

    # Default to offline; overwritten to the real provider only once a response streams.
    _set_meta("offline", "offline")

    # An async generator's span can't use the start_as_current_span context manager (its
    # detach would fire in whatever task resumes the generator). Instead the span is started,
    # attached manually so the provider HTTP call and any child spans nest under it, and both
    # ended and detached explicitly in the finally.
    span = tracer.start_span(f"chat {model}")
    span.set_attribute("gen_ai.operation.name", "chat")
    span.set_attribute("gen_ai.request.model", model)
    _ctx_token = otel_context.attach(trace.set_span_in_context(span))
    try:
        if _offline(api_key, api_base, provider, purpose="completion"):
            span.set_attribute("gen_ai.system", "offline")
            for word in _offline_completion(messages).split(" "):
                if ttft_ms is None:
                    ttft_ms = round((time.perf_counter() - t0) * 1000)
                out_chars += len(word) + 1
                yield word + " "
            return

        adapter = PROVIDERS[wire]
        span.set_attribute("gen_ai.system", wire)
        _set_server_address(span, base)

        payload = adapter.chat_payload(
            model,
            [m.as_dict() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        try:
            async with _http_client().stream(
                "POST",
                adapter.chat_url(base, model, stream=True),
                headers=adapter.headers(key),
                json=payload,
                timeout=_STREAM_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                _set_meta(wire, model)
                async for delta in adapter.iter_chat_deltas(resp):
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - t0) * 1000)
                    out_chars += len(delta)
                    yield delta
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Streaming request failed (%s)",
                exc,
                provider=wire,
                model=model,
                error_class=type(exc).__name__,
            )
            if out_chars == 0:
                # No real content streamed yet: fall back to the deterministic offline
                # stub so the caller still receives a usable answer.
                fallback = True
                _mark_offline_fallback(span)
                _set_meta("offline", "offline")
                for word in _offline_completion(messages).split(" "):
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - t0) * 1000)
                    out_chars += len(word) + 1
                    yield word + " "
            else:
                # Real deltas were already emitted; appending the full offline stub would
                # splice a canned answer onto the partial real one. Stop cleanly instead,
                # keeping the real provider attribution so metering stays correct.
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
    finally:
        latency_ms = round((time.perf_counter() - t0) * 1000)
        if ttft_ms is None:
            ttft_ms = latency_ms
        if meta_out is not None:
            meta_out["latency_ms"] = latency_ms
            meta_out["ttft_ms"] = ttft_ms
        span.set_attribute("app.latency_ms", latency_ms)
        span.set_attribute("app.ttft_ms", ttft_ms)
        # Log while the span is still current so the line carries this span's trace/span ids.
        _log_llm_call(
            "chat_stream",
            provider_used,
            model_used,
            _estimate_tokens([m.content for m in messages]),
            _approx_tokens_from_chars(out_chars),
            latency_ms,
            fallback,
            ttft_ms=ttft_ms,
        )
        span.end()
        try:
            otel_context.detach(_ctx_token)
        except Exception:
            pass
