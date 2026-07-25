"""Unit tests for web-search stubbing and history-augmented retrieval."""

from __future__ import annotations

from app.core.config import settings
from app.services.llm import ChatMessage
from app.services.rag import _augment_query
from app.services.web_search import web_search, web_search_enabled


class TestWebSearchStub:
    async def test_stub_is_deterministic(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "stub")
        first = await web_search("climate policy overview", max_results=3)
        second = await web_search("climate policy overview", max_results=3)
        assert len(first) == 3
        assert [r.url for r in first] == [r.url for r in second]
        assert all(r.url.startswith("https://") for r in first)

    async def test_none_provider_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "none")
        assert not web_search_enabled()
        assert await web_search("anything") == []


class TestQueryAugmentation:
    def test_folds_prior_user_turns(self) -> None:
        history = [
            ChatMessage(role="user", content="Tell me about Alice Johnson"),
            ChatMessage(role="assistant", content="Alice leads a team."),
        ]
        augmented = _augment_query("What project does she run?", history)
        assert "Alice Johnson" in augmented
        assert "What project does she run?" in augmented

    def test_no_history_is_identity(self) -> None:
        assert _augment_query("hello world", None) == "hello world"
        assert _augment_query("hello world", []) == "hello world"
