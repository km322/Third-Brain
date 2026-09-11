"""Unit tests for the Anthropic wire adapter behind the LLM facade (no network, no DB).

These drive :func:`complete` / :func:`stream_complete` / :func:`embed_texts` with
``provider="anthropic"`` by monkeypatching ``httpx.AsyncClient`` with deterministic
stand-ins that speak the real Anthropic Messages API shape - request headers/payload,
response parsing, SSE streaming - plus the per-provider key/base coupling invariant and
the platform completion fallback priority.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services.llm import ChatMessage, complete, embed_texts, stream_complete
from app.services.llm import client as llm_client
from app.services.llm.client import _endpoint


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


class _FakeResponse:
    """A non-streaming stand-in for the ``httpx.Response`` the client reads."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _make_post_client(payload: dict, captured: dict | None = None):
    """An ``httpx.AsyncClient`` stand-in whose ``post`` always answers with ``payload``.

    When ``captured`` is passed, the request's url/headers/json are recorded into it.
    """

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


def _messages_payload(text: str = "ok", stop_reason: str = "end_turn") -> dict:
    """A minimal, real-shaped ``POST /v1/messages`` response."""
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 12, "output_tokens": 34},
        "stop_reason": stop_reason,
    }


class TestEndpointCoupling:
    """Key/base coupling.

    No platform key may ever reach an org-supplied base, and the Anthropic platform key is
    only ever paired with the Anthropic platform base.
    """

    def test_custom_base_never_borrows_any_platform_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "OPENAI-PLATFORM-SECRET")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ANTHROPIC-PLATFORM-SECRET")
        key, base = _endpoint(None, "http://attacker.example", "anthropic")
        assert key is None
        assert base == "http://attacker.example"

    def test_platform_key_pairs_only_with_its_own_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "OPENAI-PLATFORM-SECRET")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ANTHROPIC-PLATFORM-SECRET")
        monkeypatch.setattr(settings, "ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        key, base = _endpoint(None, None, "anthropic")
        assert key == "ANTHROPIC-PLATFORM-SECRET"
        assert base == "https://api.anthropic.com"

    def test_anthropic_key_never_leaks_to_openai_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ANTHROPIC-PLATFORM-SECRET")
        key, _base = _endpoint(None, None, "openai")
        assert key is None

    async def test_completion_to_custom_base_sends_no_platform_key(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A keyless org connector pointed at an attacker-controlled base URL sends no key."""
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "ANTHROPIC-PLATFORM-SECRET")
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        await complete(
            [ChatMessage(role="user", content="hi")],
            "claude-haiku-4-5",
            api_key=None,
            api_base="http://attacker.example",
            provider="anthropic",
        )

        assert captured["url"].startswith("http://attacker.example")
        assert "ANTHROPIC-PLATFORM-SECRET" not in json.dumps(captured["headers"])
        assert "x-api-key" not in captured["headers"]


class TestCompleteAnthropic:
    """:func:`complete` - the Anthropic request/response wire shape."""

    async def test_request_and_response_shape(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """System messages hoist into the top-level system string; tool maps to user.

        ``max_tokens`` is REQUIRED by the API, so the module default fills a ``None``.
        """
        captured: dict = {}
        payload = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": " world"},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 34},
            "stop_reason": "end_turn",
        }
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        result = await complete(
            [
                ChatMessage(role="system", content="Be terse."),
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="hello"),
                ChatMessage(role="tool", content="tool output"),
            ],
            "claude-haiku-4-5",
            api_key="org-key",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert captured["url"] == "https://anthropic.test/v1/messages"
        assert captured["headers"]["x-api-key"] == "org-key"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        body = captured["json"]
        assert body["system"] == "Be terse."
        assert body["messages"] == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "tool output"},
        ]
        assert body["max_tokens"] == 16384
        assert body["temperature"] == 0.2

        assert result.provider == "anthropic"
        assert result.text == "Hello world"
        assert result.tokens_in == 12
        assert result.tokens_out == 34
        assert result.finish_reason == "stop"

    @pytest.mark.parametrize(
        "model",
        [
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-opus-5",
            "claude-opus-5-20260301",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-mythos-5",
        ],
    )
    async def test_temperature_omitted_for_models_that_reject_it(
        self, model: str, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Models that reject ``temperature`` never receive it.

        ``claude-opus-5-20260301`` is a dated variant, which pins prefix (not exact)
        matching.
        """
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        await complete(
            [ChatMessage(role="user", content="hi")],
            model,
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert "temperature" not in captured["json"]

    @pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-haiku-4-5-20251001"])
    async def test_temperature_still_sent_for_models_that_accept_it(
        self, model: str, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        await complete(
            [ChatMessage(role="user", content="hi")],
            model,
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert captured["json"]["temperature"] == 0.2

    async def test_explicit_max_tokens_and_length_stop_reason(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_messages_payload(stop_reason="max_tokens"), captured),
        )

        result = await complete(
            [ChatMessage(role="user", content="hi")],
            "claude-haiku-4-5",
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
            max_tokens=8,
        )

        assert captured["json"]["max_tokens"] == 8
        assert result.finish_reason == "length"

    async def test_refusal_stop_reason_maps_to_content_filter(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claude 4.5+/5 safety classifiers decline with HTTP 200.

        The response carries ``stop_reason`` "refusal" and empty content, which the
        adapter normalizes rather than passing through raw.
        """
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            _make_post_client(_messages_payload(text="", stop_reason="refusal")),
        )

        result = await complete(
            [ChatMessage(role="user", content="hi")],
            "claude-opus-5",
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert result.finish_reason == "content_filter"
        assert result.text == ""
        assert result.provider == "anthropic"

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
            "claude-haiku-4-5",
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert result.provider == "offline"
        assert "[offline model]" in result.text


class TestPlatformFallback:
    """The platform completion fallback: OpenAI key -> Anthropic key -> Google key -> offline."""

    async def test_anthropic_key_serves_platform_completions(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The OpenAI-family default model can't be served by Anthropic, so it is substituted."""
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthro-platform")
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
        monkeypatch.setattr(settings, "DEFAULT_COMPLETION_MODEL", "gpt-4o-mini")
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        result = await complete([ChatMessage(role="user", content="hi")])

        assert captured["url"] == f"{settings.ANTHROPIC_BASE_URL}/v1/messages"
        assert captured["headers"]["x-api-key"] == "anthro-platform"
        assert captured["json"]["model"] == "claude-opus-5"
        assert result.provider == "anthropic"

    async def test_openai_key_wins_over_anthropic_key(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "openai-platform")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "anthro-platform")
        payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        captured: dict = {}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        result = await complete([ChatMessage(role="user", content="hi")])

        assert captured["url"].endswith("/chat/completions")
        assert result.provider == "openai"

    async def test_no_keys_anywhere_uses_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)

        result = await complete([ChatMessage(role="user", content="hi")])

        assert result.provider == "offline"


class TestEmbeddingsUnsupported:
    """:func:`embed_texts` against Anthropic, which has no embeddings API.

    It must fail loudly, and never fake vectors.
    """

    async def test_embed_texts_raises_runtime_error(self, provider_mode: None) -> None:
        with pytest.raises(RuntimeError, match="no embeddings API"):
            await embed_texts(
                ["some text"],
                "claude-haiku-4-5",
                api_key="k",
                api_base="https://anthropic.test",
                provider="anthropic",
            )


def _sse(obj: dict) -> str:
    """One Anthropic SSE event line."""
    return "data: " + json.dumps(obj)


def _text_delta(text: str) -> str:
    """A ``content_block_delta`` SSE event carrying ``text``."""
    return _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


class _FakeStreamResponse:
    """A streaming stand-in for the ``httpx.Response`` the client iterates."""

    def __init__(self, lines: list[str], *, fail_on_status: bool) -> None:
        self._lines = lines
        self._fail_on_status = fail_on_status

    def raise_for_status(self) -> None:
        if self._fail_on_status:
            raise RuntimeError("provider returned 500")

    async def aiter_lines(self):
        """Yield the canned lines, then simulate the connection dropping mid-stream.

        The drop happens after any real deltas, which is the case the client has to
        recover from.
        """
        for line in self._lines:
            yield line
        raise RuntimeError("connection dropped mid-stream")


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


def _make_stream_client(lines: list[str], *, fail_on_status: bool = False):
    """A streaming ``httpx.AsyncClient`` stand-in, plus the dict its request is recorded in."""
    captured: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def stream(self, method, url, headers=None, json=None, timeout=None):  # noqa: ANN001
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeStreamCtx(_FakeStreamResponse(lines, fail_on_status=fail_on_status))

    return _Client, captured


class TestStreamCompleteAnthropic:
    """:func:`stream_complete` - the Anthropic SSE event shape."""

    async def test_streams_text_deltas_until_message_stop(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lines = [
            _sse({"type": "message_start", "message": {}}),
            _text_delta("Hello "),
            _text_delta("world"),
            _sse({"type": "message_stop"}),
        ]
        client_cls, captured = _make_stream_client(lines)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "claude-haiku-4-5",
                api_key="k",
                api_base="https://anthropic.test",
                provider="anthropic",
                meta_out=meta,
            )
        ]

        assert "".join(chunks) == "Hello world"
        assert captured["url"] == "https://anthropic.test/v1/messages"
        assert captured["json"]["stream"] is True
        assert meta["provider"] == "anthropic"
        assert meta["model"] == "claude-haiku-4-5"
        assert meta["latency_ms"] >= 0
        assert meta["ttft_ms"] >= 0

    async def test_error_event_before_content_falls_back_offline(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lines = [_sse({"type": "error", "error": {"type": "overloaded", "message": "busy"}})]
        client_cls, _ = _make_stream_client(lines)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "claude-haiku-4-5",
                api_key="k",
                api_base="https://anthropic.test",
                provider="anthropic",
                meta_out=meta,
            )
        ]
        out = "".join(chunks)

        assert "[offline model]" in out
        assert meta["provider"] == "offline"

    async def test_mid_stream_failure_does_not_append_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real deltas, then the connection drops with no ``message_stop``.

        Keep the partial answer and the real provider attribution; never splice in the
        canned stub.
        """
        lines = [_text_delta("Hello "), _text_delta("world")]
        client_cls, _ = _make_stream_client(lines)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                [ChatMessage(role="user", content="greet me")],
                "claude-haiku-4-5",
                api_key="k",
                api_base="https://anthropic.test",
                provider="anthropic",
                meta_out=meta,
            )
        ]
        out = "".join(chunks)

        assert out == "Hello world"
        assert "[offline model]" not in out
        assert meta["provider"] == "anthropic"


class TestImageMessages:
    async def test_image_attachment_maps_to_content_blocks(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.llm import ImageAttachment

        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        await complete(
            [
                ChatMessage(
                    role="user",
                    content="Describe this image.",
                    images=[ImageAttachment(media_type="image/png", data_b64="aGk=")],
                )
            ],
            "claude-opus-5",
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        body = captured["json"]
        assert body["messages"] == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "aGk=",
                        },
                    },
                    {"type": "text", "text": "Describe this image."},
                ],
            }
        ]

    async def test_text_only_messages_keep_plain_string_content(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setattr(
            llm_client.httpx, "AsyncClient", _make_post_client(_messages_payload(), captured)
        )

        await complete(
            [ChatMessage(role="user", content="hi")],
            "claude-opus-5",
            api_key="k",
            api_base="https://anthropic.test",
            provider="anthropic",
        )

        assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]
