"""Unit tests pinning the MCP capture-etiquette instructions (pure, no DB/network).

Proactive capture stays the default, but the instructions the server hands to agents
carry two user-facing rules: ask the user for permission before saving personal,
private, or secret-looking content, and tell the user what was saved (title and
collection) after every capture or update. These tests fail if that language is
removed from the tool descriptions or the initialize instructions.
"""

from __future__ import annotations

from app.mcp.server import _initialize_result
from app.mcp.tools import TOOL_DEFINITIONS


def _description(tool_name: str) -> str:
    return next(t["description"] for t in TOOL_DEFINITIONS if t["name"] == tool_name)


class TestAddKnowledgeDescription:
    def test_keeps_proactive_capture_default(self) -> None:
        """The consent gate is an exception to proactive capture, not a replacement: the
        "without being asked" default must survive alongside the two rules."""
        assert "without being asked" in _description("add_knowledge")

    def test_carries_consent_gate(self) -> None:
        desc = _description("add_knowledge")
        assert "ask the user for permission before saving it" in desc
        assert "looks like a secret or credential" in desc

    def test_carries_capture_narration(self) -> None:
        assert "tell the user what you saved (title and collection)" in _description(
            "add_knowledge"
        )


class TestUpdateKnowledgeDescription:
    def test_carries_consent_gate(self) -> None:
        desc = _description("update_knowledge")
        assert (
            "ask the user for permission before saving personal, private, or "
            "secret-looking content" in desc
        )

    def test_carries_update_narration(self) -> None:
        assert "tell the user which document you updated" in _description("update_knowledge")


class TestInitializeInstructions:
    def test_carries_both_rules(self) -> None:
        """The server-level instructions must repeat the consent gate and the narration rule,
        without dropping the proactive-capture default."""
        instructions = _initialize_result({})["instructions"]
        assert "you do NOT need to be asked" in instructions
        assert "ask the user for permission before saving it" in instructions
        assert "tell the user what you saved (title and collection)" in instructions
