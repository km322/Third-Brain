"""Unit tests for the Google Gemini wire adapter behind the LLM facade (no network, no DB).

These drive :func:`complete` / :func:`stream_complete` / :func:`embed_texts` with
``provider="google"`` by monkeypatching ``httpx.AsyncClient`` with deterministic
stand-ins that speak the real Gemini wire shape - ``:generateContent`` /
``:streamGenerateContent?alt=sse`` / ``:batchEmbedContents`` - plus the per-provider
key/base coupling invariant and the platform embedding fallback priority.
"""

from __future__ import annotations

import json
import math

import pytest

from app.core.config import settings
from app.services.llm import ChatMessage, complete, embed_texts, stream_complete
from app.services.llm import client as llm_client
from app.services.llm.client import _endpoint, effective_provider


@pytest.fixture
def provider_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the real-provider branch (``_offline`` returns False) for a test."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")


@pytest.fixture(autouse=True)
def _reset_shared_client() -> None:
    """Drop the process-wide shared httpx client around each test.

    Without resetting it a test's monkeypatched ``httpx.AsyncClient`` would never be used
    (a client cached by an earlier test survives), and a fake would leak into other test
    modules. Clearing it before and after each test restores per-test isolation.
    """
    llm_client._client = None
    yield
    llm_client._client = None


# --------------------------------------------------------------------------- #
# Non-streaming stand-ins
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _make_post_client(payload: dict, captured: dict | None = None):
    class _Client:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def post(self, url, headers=None, json=None, timeout=None):  # noqa: ANN001
            if captured is not None:
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
            return _FakeResponse(payload)

    return _Client


def _generate_content_payload(parts: list[str] | None = None, finish_reason: str = "STOP") -> dict:
    """A minimal, real-shaped ``:generateContent`` response."""
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": p} for p in (parts or ["ok"])]},
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


# --------------------------------------------------------------------------- #
# Key/base coupling - no platform key may ever reach an org-supplied base, and the
# Google platform key is only ever paired with the Google platform base.
# --------------------------------------------------------------------------- #
class TestEndpointCoupling:
    def test_custom_base_never_borrows_any_platform_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "OPENAI-PLATFORM-SECRET")
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "GOOGLE-PLATFORM-SECRET")
        key, base = _endpoint(None, "http://attacker.example", "google")
        assert key is None
        assert base == "http://attacker.example"

    def test_platform_key_pairs_only_with_its_own_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "OPENAI-PLATFORM-SECRET")
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "GOOGLE-PLATFORM-SECRET")
        monkeypatch.setattr(
            settings, "GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com"
        )
        key, base = _endpoint(None, None, "google")
        assert key == "GOOGLE-PLATFORM-SECRET"
        assert base == "https://generativelanguage.googleapis.com"

    def test_google_key_never_leaks_to_openai_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "GOOGLE-PLATFORM-SECRET")
        key, _base = _endpoint(None, None, "openai")
        assert key is None

    async def test_completion_to_custom_base_sends_no_platform_key(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "GOOGLE-PLATFORM-SECRET")
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_generate_content_payload(), captured),
        )

        # Keyless org connector pointed at an attacker-controlled base URL.
        await complete(
            [ChatMessage(role="user", content="hi")],
            "gemini-2.5-flash",
            api_key=None,
            api_base="http://attacker.example",
            provider="google",
        )

        assert captured["url"].startswith("http://attacker.example")
        assert "GOOGLE-PLATFORM-SECRET" not in json.dumps(captured["headers"])
        assert "x-goog-api-key" not in captured["headers"]


# --------------------------------------------------------------------------- #
# complete() - Gemini request/response wire shape
# --------------------------------------------------------------------------- #
class TestCompleteGoogle:
    async def test_request_and_response_shape(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_generate_content_payload(["Hello", " world"]), captured),
        )

        result = await complete(
            [
                ChatMessage(role="system", content="Be terse."),
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="hello"),
            ],
            "gemini-2.5-flash",
            api_key="org-key",
            api_base="https://gemini.test",
            provider="google",
            max_tokens=64,
        )

        assert (
            captured["url"] == "https://gemini.test/v1beta/models/gemini-2.5-flash:generateContent"
        )
        assert captured["headers"]["x-goog-api-key"] == "org-key"
        body = captured["json"]
        assert body["systemInstruction"] == {"parts": [{"text": "Be terse."}]}
        assert body["contents"] == [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "hello"}]},
        ]
        assert body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 64}

        assert result.provider == "google"
        assert result.text == "Hello world"
        assert result.tokens_in == 10
        assert result.tokens_out == 5
        assert result.finish_reason == "stop"

    async def test_max_tokens_finish_reason_maps_to_length(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_generate_content_payload(finish_reason="MAX_TOKENS")),
        )

        result = await complete(
            [ChatMessage(role="user", content="hi")],
            "gemini-2.5-flash",
            api_key="k",
            api_base="https://gemini.test",
            provider="google",
        )

        assert result.finish_reason == "length"

    async def test_provider_error_falls_back_to_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FailingResponse(_FakeResponse):
            def raise_for_status(self) -> None:
                raise RuntimeError("provider returned 500")

        class _Client:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def post(self, url, headers=None, json=None, timeout=None):  # noqa: ANN001
                return _FailingResponse({})

        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _Client)

        result = await complete(
            [ChatMessage(role="user", content="greet me")],
            "gemini-2.5-flash",
            api_key="k",
            api_base="https://gemini.test",
            provider="google",
        )

        assert result.provider == "offline"
        assert "[offline model]" in result.text


# --------------------------------------------------------------------------- #
# embed_texts() - batchEmbedContents shape, normalization and input validation
# --------------------------------------------------------------------------- #
class TestEmbedGoogle:
    async def test_request_shape_and_l2_normalization(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        # 3-4-5 triangle: normalizes to (0.6, 0.8).
        payload = {"embeddings": [{"values": [3.0, 4.0]}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        result = await embed_texts(
            ["hello"],
            "gemini-embedding-001",
            api_key="org-key",
            api_base="https://gemini.test",
            provider="google",
        )

        assert (
            captured["url"]
            == "https://gemini.test/v1beta/models/gemini-embedding-001:batchEmbedContents"
        )
        assert captured["headers"]["x-goog-api-key"] == "org-key"
        assert captured["json"] == {
            "requests": [
                {
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": "hello"}]},
                    "outputDimensionality": settings.EMBEDDING_DIM,
                }
            ]
        }
        # Truncated-dimension Gemini vectors are not unit-normalized on the wire; the
        # adapter must L2-normalize so pgvector cosine ranking stays sane.
        assert result.vectors == [[0.6, 0.8]]
        assert math.isclose(sum(v * v for v in result.vectors[0]), 1.0)
        assert result.provider == "google"
        assert result.tokens > 0  # estimated - Gemini reports no embedding usage

    async def test_token_id_arrays_raise_value_error(self, provider_mode: None) -> None:
        with pytest.raises(ValueError, match="token-id"):
            await embed_texts(
                [[1, 2, 3]],
                "gemini-embedding-001",
                api_key="k",
                api_base="https://gemini.test",
                provider="google",
            )

    async def test_provider_error_raises_runtime_error_never_fake_vectors(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FailingResponse(_FakeResponse):
            def raise_for_status(self) -> None:
                raise RuntimeError("provider returned 500")

        class _Client:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            async def post(self, url, headers=None, json=None, timeout=None):  # noqa: ANN001
                return _FailingResponse({})

        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _Client)

        with pytest.raises(RuntimeError, match="Embedding provider call failed"):
            await embed_texts(
                ["hello"],
                "gemini-embedding-001",
                api_key="k",
                api_base="https://gemini.test",
                provider="google",
            )


# --------------------------------------------------------------------------- #
# Platform fallback: embeddings skip Anthropic (no embeddings API) and substitute the
# Gemini default model for an OpenAI-family default.
# --------------------------------------------------------------------------- #
class TestPlatformFallback:
    async def test_google_key_serves_platform_embeddings(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthro-platform")
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google-platform")
        monkeypatch.setattr(settings, "EMBEDDING_MODEL", "text-embedding-3-small")
        captured: dict = {}
        payload = {"embeddings": [{"values": [1.0, 0.0]}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        result = await embed_texts(["hello"])

        # Anthropic (despite its key) is skipped for embeddings; the OpenAI-family
        # default embedding model is substituted with the Gemini one.
        assert captured["headers"]["x-goog-api-key"] == "google-platform"
        assert ":batchEmbedContents" in captured["url"]
        assert captured["url"].startswith(settings.GOOGLE_BASE_URL)
        assert "gemini-embedding-001" in captured["url"]
        assert result.provider == "google"
        assert result.model == "gemini-embedding-001"

    async def test_google_key_serves_platform_completions_last(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google-platform")
        monkeypatch.setattr(settings, "DEFAULT_COMPLETION_MODEL", "gpt-4o-mini")
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_generate_content_payload(), captured),
        )

        result = await complete([ChatMessage(role="user", content="hi")])

        assert "gemini-2.5-flash:generateContent" in captured["url"]
        assert result.provider == "google"

    def test_effective_provider_reflects_key_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthro-platform")
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google-platform")
        # An explicit provider always wins; otherwise the purpose-specific chain applies.
        assert effective_provider("openai", purpose="embedding") == "openai"
        assert effective_provider(purpose="embedding") == "google"
        assert effective_provider(purpose="completion") == "anthropic"
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
        assert effective_provider(purpose="embedding") == "offline"


# --------------------------------------------------------------------------- #
# stream_complete() - Gemini SSE shape
# --------------------------------------------------------------------------- #
def _sse_chunk(text: str) -> str:
    return "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]})


class _FakeStreamResponse:
    def __init__(self, lines: list[str], *, fail_on_status: bool, drop: bool) -> None:
        self._lines = lines
        self._fail_on_status = fail_on_status
        self._drop = drop

    def raise_for_status(self) -> None:
        if self._fail_on_status:
            raise RuntimeError("provider returned 500")

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._drop:
            # Simulate the provider connection dropping mid-stream.
            raise RuntimeError("connection dropped mid-stream")


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


def _make_stream_client(lines: list[str], *, fail_on_status: bool = False, drop: bool = False):
    captured: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def stream(self, method, url, headers=None, json=None, timeout=None):  # noqa: ANN001
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeStreamCtx(
                _FakeStreamResponse(lines, fail_on_status=fail_on_status, drop=drop)
            )

    return _Client, captured


class TestStreamCompleteGoogle:
    async def test_streams_candidate_text_parts_as_deltas(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_cls, captured = _make_stream_client([_sse_chunk("Hello "), _sse_chunk("world")])
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "gemini-2.5-flash",
                api_key="k",
                api_base="https://gemini.test",
                provider="google",
                meta_out=meta,
            )
        ]

        assert "".join(chunks) == "Hello world"
        assert (
            captured["url"]
            == "https://gemini.test/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
        )
        assert meta["provider"] == "google"
        assert meta["model"] == "gemini-2.5-flash"

    async def test_failure_before_any_content_yields_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_cls, _ = _make_stream_client([], fail_on_status=True)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "gemini-2.5-flash",
                api_key="k",
                api_base="https://gemini.test",
                provider="google",
                meta_out=meta,
            )
        ]
        out = "".join(chunks)

        assert "[offline model]" in out
        assert meta["provider"] == "offline"

    async def test_mid_stream_failure_does_not_append_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_cls, _ = _make_stream_client([_sse_chunk("Hello "), _sse_chunk("world")], drop=True)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "gemini-2.5-flash",
                api_key="k",
                api_base="https://gemini.test",
                provider="google",
                meta_out=meta,
            )
        ]
        out = "".join(chunks)

        assert out == "Hello world"
        assert "[offline model]" not in out
        assert meta["provider"] == "google"


class TestImageMessages:
    async def test_image_attachment_maps_to_inline_data_parts(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.llm import ImageAttachment

        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_generate_content_payload(), captured),
        )

        await complete(
            [
                ChatMessage(
                    role="user",
                    content="Describe this image.",
                    images=[ImageAttachment(media_type="image/png", data_b64="aGk=")],
                )
            ],
            "gemini-2.5-flash",
            api_key="k",
            api_base="https://gemini.test",
            provider="google",
        )

        assert captured["json"]["contents"] == [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": "aGk="}},
                    {"text": "Describe this image."},
                ],
            }
        ]
