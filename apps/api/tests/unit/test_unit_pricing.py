"""Unit: token cost estimation and offline-provider billability.

Two regressions are pinned here: substring model matching that priced ``gpt-4o-mini`` as
the ~25-33x pricier ``gpt-4o``, and the offline stub (labelled ``offline``, not ``fake``)
accruing phantom USD because cost guards only checked for ``fake``.
"""

from __future__ import annotations

import pytest

from app.services.llm.pricing import (
    completion_cost,
    embedding_cost,
    is_billable_provider,
)


class TestModelMatching:
    def test_gpt_4o_mini_variant_matches_mini_not_gpt_4o(self) -> None:
        """Priced as gpt-4o-mini, not the far pricier gpt-4o whose name is a substring."""
        mini_variant = completion_cost("gpt-4o-mini-2024-07-18", 1_000_000, 0)
        assert mini_variant == pytest.approx(completion_cost("gpt-4o-mini", 1_000_000, 0))
        assert mini_variant < completion_cost("gpt-4o", 1_000_000, 0)

    def test_exact_key_match(self) -> None:
        assert completion_cost("gpt-4o", 1000, 1000) == pytest.approx(0.02)

    def test_unknown_model_falls_back_to_default(self) -> None:
        assert embedding_cost("some-unknown-model", 1000) == pytest.approx(0.00002)

    def test_dated_opus_5_variant_matches_opus_5(self) -> None:
        """A dated id like "claude-opus-5-20260301" must price as claude-opus-5, never fall
        through to the (10x cheaper) unknown-model default."""
        dated = completion_cost("claude-opus-5-20260301", 1_000_000, 1_000_000)
        assert dated == pytest.approx(completion_cost("claude-opus-5", 1_000_000, 1_000_000))

    def test_gemini_flash_lite_matches_lite_not_flash(self) -> None:
        """Longest-substring matching: the -lite variant must not price as the ~3x pricier
        gemini-2.5-flash whose name is a substring."""
        lite = completion_cost("gemini-2.5-flash-lite-preview", 1_000_000, 0)
        assert lite == pytest.approx(completion_cost("gemini-2.5-flash-lite", 1_000_000, 0))
        assert lite < completion_cost("gemini-2.5-flash", 1_000_000, 0)


class TestAnthropicPricing:
    def test_claude_models_price_per_million_tokens(self) -> None:
        """Per-MTok list prices: fable/mythos 10/50, opus 5/25, sonnet 3/15, haiku 1/5."""
        assert completion_cost("claude-fable-5", 1_000_000, 1_000_000) == pytest.approx(60.0)
        assert completion_cost("claude-mythos-5", 1_000_000, 1_000_000) == pytest.approx(60.0)
        assert completion_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)
        assert completion_cost("claude-opus-4-8", 1_000_000, 1_000_000) == pytest.approx(30.0)
        assert completion_cost("claude-opus-4-7", 1_000_000, 1_000_000) == pytest.approx(30.0)
        assert completion_cost("claude-opus-4-6", 1_000_000, 1_000_000) == pytest.approx(30.0)
        assert completion_cost("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(18.0)
        assert completion_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)
        assert completion_cost("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


class TestGooglePricing:
    def test_gemini_models_price_per_million_tokens(self) -> None:
        """Per-MTok list prices: pro 1.25/10, flash 0.30/2.50, flash-lite 0.10/0.40."""
        assert completion_cost("gemini-2.5-pro", 1_000_000, 1_000_000) == pytest.approx(11.25)
        assert completion_cost("gemini-2.5-flash", 1_000_000, 1_000_000) == pytest.approx(2.8)
        assert completion_cost("gemini-2.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(0.5)

    def test_gemini_embedding_price(self) -> None:
        """0.15 per MTok, input only."""
        assert embedding_cost("gemini-embedding-001", 1_000_000) == pytest.approx(0.15)


class TestBillableProvider:
    def test_offline_stub_labels_are_free(self) -> None:
        assert is_billable_provider("offline") is False
        assert is_billable_provider("fake") is False
        assert is_billable_provider("") is False
        assert is_billable_provider(None) is False

    def test_real_providers_are_billable(self) -> None:
        assert is_billable_provider("openai") is True
        assert is_billable_provider("anthropic") is True
        assert is_billable_provider("google") is True
