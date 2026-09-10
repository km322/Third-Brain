"""Unit tests for the LLM client's *provider* path (no network, no DB).

These drive :func:`complete` / :func:`stream_complete` through their real-provider
branch by forcing a non-offline ``EMBEDDING_PROVIDER`` and passing an ``api_key``, then
monkeypatching ``httpx.AsyncClient`` with a deterministic stand-in. They cover metering
fall-backs and the mid-stream error handling that the offline-only tests can't reach.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services.llm import ChatMessage, complete, embed_texts, stream_complete
from app.services.llm import client as llm_client
from app.services.llm.client import _auth_headers, _endpoint, _estimate_tokens


@pytest.fixture
def provider_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the real-provider branch (``_offline`` returns False) for a test."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")


@pytest.fixture(autouse=True)
def _reset_shared_client() -> None:
    """Drop the process-wide shared httpx client around each test.

    ``client`` now caches one ``httpx.AsyncClient`` in a module global, so without
    resetting it a test's monkeypatched ``httpx.AsyncClient`` would never be used
    (a client cached by an earlier test survives), and a fake would leak into other
    test modules. Clearing it before and after each test restores per-test isolation.
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

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
            return False

        async def post(self, url, headers=None, json=None, timeout=None):  # noqa: ANN001
            if captured is not None:
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
            return _FakeResponse(payload)

    return _Client


class TestEndpointCoupling:
    """Endpoint / auth coupling.

    The platform key must never leak to an org-supplied base URL.
    """

    def test_custom_base_never_borrows_platform_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An org connector supplying its own endpoint but no key must send NO key.

        Never the platform's, so the org cannot exfiltrate the platform credential.
        """
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "PLATFORM-SECRET")
        monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        key, base = _endpoint(None, "http://attacker.example/v1")
        assert key is None
        assert base == "http://attacker.example/v1"

    def test_custom_base_keeps_its_own_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "PLATFORM-SECRET")
        key, base = _endpoint("org-key", "https://prov.test/v1")
        assert key == "org-key"
        assert base == "https://prov.test/v1"

    def test_no_custom_base_uses_platform_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "PLATFORM-SECRET")
        monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        key, base = _endpoint(None, None)
        assert key == "PLATFORM-SECRET"
        assert base == "https://api.openai.com/v1"

    def test_auth_headers_omitted_without_key(self) -> None:
        assert "Authorization" not in _auth_headers(None)
        assert "Authorization" not in _auth_headers("")
        assert _auth_headers("k")["Authorization"] == "Bearer k"

    async def test_completion_to_custom_base_sends_no_platform_key(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A keyless org connector pointed at an attacker-controlled base URL sends no key."""
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "PLATFORM-SECRET")
        captured: dict = {}
        payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        await complete(
            [ChatMessage(role="user", content="hi")],
            api_key=None,
            api_base="http://attacker.example/v1",
        )

        assert captured["url"].startswith("http://attacker.example/v1")
        assert "PLATFORM-SECRET" not in json.dumps(captured["headers"])
        assert "Authorization" not in captured["headers"]


def _delta_line(content: str) -> str:
    """One SSE delta line as the provider would emit it."""
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


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

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
            return False

        def stream(self, method, url, headers=None, json=None, timeout=None):  # noqa: ANN001
            """Record the per-request timeout and hand back the canned stream.

            The timeout is passed per-request on the shared client, not to its constructor.
            """
            captured["timeout"] = timeout
            return _FakeStreamCtx(_FakeStreamResponse(lines, fail_on_status=fail_on_status))

    return _Client, captured


class TestCompleteTokenMetering:
    """:func:`complete` - the token-metering fall-back."""

    async def test_estimates_tokens_when_usage_omitted(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = [ChatMessage(role="user", content="What is the capital of France?")]
        answer = "Paris is the capital of France."
        payload = {"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload))

        result = await complete(messages, api_key="test-key", api_base="https://prov.test/v1")

        assert result.provider == "openai"
        assert result.text == answer
        assert result.tokens_in == _estimate_tokens([m.content for m in messages])
        assert result.tokens_out == _estimate_tokens([answer])
        assert result.tokens_in > 0
        assert result.tokens_out > 0

    async def test_estimates_tokens_when_usage_reports_zero(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = [ChatMessage(role="user", content="How tall is Everest?")]
        answer = "About 8,849 metres."
        payload = {
            "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload))

        result = await complete(messages, api_key="test-key", api_base="https://prov.test/v1")

        assert result.tokens_in == _estimate_tokens([m.content for m in messages])
        assert result.tokens_out == _estimate_tokens([answer])

    async def test_keeps_real_reported_token_counts(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = [ChatMessage(role="user", content="hi")]
        payload = {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45},
        }
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload))

        result = await complete(messages, api_key="test-key", api_base="https://prov.test/v1")

        assert result.tokens_in == 123
        assert result.tokens_out == 45


class TestStreamComplete:
    """:func:`stream_complete` - bounded timeout plus the mid-stream fallback."""

    async def test_uses_a_bounded_timeout(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_cls, captured = _make_stream_client([_delta_line("hi"), "data: [DONE]"])
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        messages = [ChatMessage(role="user", content="ping")]
        [d async for d in stream_complete(messages, api_key="k", api_base="https://p.test/v1")]

        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 10.0
        assert timeout.read == 60.0

    async def test_mid_stream_failure_does_not_append_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real content came from the provider, so attribution must stay non-offline."""
        lines = [_delta_line("Hello "), _delta_line("world")]
        client_cls, _ = _make_stream_client(lines)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        messages = [ChatMessage(role="user", content="greet me")]
        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                messages, api_key="k", api_base="https://p.test/v1", meta_out=meta
            )
        ]
        out = "".join(chunks)

        assert out == "Hello world"
        assert "[offline model]" not in out
        assert meta["provider"] == "openai"

    async def test_failure_before_any_content_yields_offline_stub(
        self, provider_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_cls, _ = _make_stream_client([], fail_on_status=True)
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", client_cls)

        messages = [ChatMessage(role="user", content="greet me")]
        meta: dict = {}
        chunks = [
            d
            async for d in stream_complete(
                messages, api_key="k", api_base="https://p.test/v1", meta_out=meta
            )
        ]
        out = "".join(chunks)

        assert "[offline model]" in out
        assert "greet me" in out
        assert meta["provider"] == "offline"


class TestEmbeddingMisconfig:
    """:func:`embed_texts` must never silently fake vectors.

    Specifically when the platform is live but has no embeddings-capable key (e.g. only
    ``ANTHROPIC_API_KEY``, which has no embeddings API).
    """

    async def test_live_completion_without_embeddings_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-live")
        with pytest.raises(RuntimeError, match="embeddings-capable"):
            await embed_texts(["corpus text"])

    async def test_genuinely_keyless_still_fakes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fully-offline dev/CI (no keys at all) is legitimate: return the deterministic stub."""
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
        result = await embed_texts(["corpus text"])
        assert result.provider == "offline"

    async def test_explicit_fake_provider_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``EMBEDDING_PROVIDER=fake`` is an explicit opt-in to the stub, even with a live key."""
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "fake")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-live")
        result = await embed_texts(["corpus text"])
        assert result.provider == "offline"

    async def test_org_connector_bypasses_the_platform_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A per-connector call carries its own creds and never hits the platform fallback.

        So even with only ANTHROPIC set at the platform level it must not raise: the
        keyless local endpoint uses the offline stub without a platform key leaking to it.
        A provider explicitly supplied by the org connector is a real target, not the
        platform fallback - it must not trip the misconfig guard.
        """
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-live")
        assert llm_client._embedding_offline_is_misconfig(None, None, "openai") is False


class TestOpenAIImageMessages:
    """The OpenAI wire format: image attachments become content parts.

    Text-only messages pass through unchanged.
    """

    async def test_image_attachment_maps_to_content_parts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.llm import ImageAttachment

        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
        captured: dict = {}
        payload = {"choices": [{"message": {"content": "a chart"}, "finish_reason": "stop"}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        await complete(
            [
                ChatMessage(
                    role="user",
                    content="Describe this image.",
                    images=[ImageAttachment(media_type="image/png", data_b64="aGk=")],
                )
            ],
            "gpt-4o",
            api_key="k",
            api_base="https://openai.test/v1",
        )

        assert captured["json"]["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aGk="},
                    },
                ],
            }
        ]

    async def test_text_only_message_stays_plain_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
        captured: dict = {}
        payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        monkeypatch.setattr(llm_client.httpx, "AsyncClient", _make_post_client(payload, captured))

        await complete(
            [ChatMessage(role="user", content="hi")],
            "gpt-4o",
            api_key="k",
            api_base="https://openai.test/v1",
        )

        assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]
