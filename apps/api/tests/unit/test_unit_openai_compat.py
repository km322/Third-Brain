"""Unit tests for OpenAI-compat helpers (pure, no DB/network)."""

from __future__ import annotations

from app.api.openai_compat import VIRTUAL_COMPLETION_MODEL, _resolve_completion_model


class TestResolveCompletionModel:
    def test_virtual_alias_resolves_to_configured_default(self) -> None:
        """The product-facing "third-brain" model must NOT be sent to the provider verbatim
        (no such model exists there); it resolves to None so the client uses the org's
        configured completion model. This is what makes the documented model="third-brain"
        call work once a real key is set instead of 404ing into the offline stub."""
        assert _resolve_completion_model(VIRTUAL_COMPLETION_MODEL) is None

    def test_unset_model_resolves_to_default(self) -> None:
        assert _resolve_completion_model(None) is None
        assert _resolve_completion_model("") is None

    def test_concrete_model_passes_through(self) -> None:
        assert _resolve_completion_model("gpt-4o-mini") == "gpt-4o-mini"
        assert _resolve_completion_model("azure/my-deployment") == "azure/my-deployment"
