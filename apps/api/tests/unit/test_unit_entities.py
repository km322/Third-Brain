"""Unit tests for the heuristic entity extractor (deterministic, offline)."""

from __future__ import annotations

from app.models.enums import EntityKind
from app.services.entities import extract_entities, parse_llm_entities


def _by_name(text: str) -> dict[str, EntityKind]:
    return {e.name: e.kind for e in extract_entities(text)}


class TestExtraction:
    def test_person_org_project(self) -> None:
        found = _by_name("Alice Johnson leads Project Aurora at Acme Corporation.")
        assert found.get("Alice Johnson") == EntityKind.PERSON
        assert found.get("Project Aurora") == EntityKind.PROJECT
        assert found.get("Acme Corporation") == EntityKind.ORG

    def test_counts_accumulate(self) -> None:
        text = "Acme Corporation shipped it. Later, Acme Corporation shipped again."
        entities = {e.name: e.count for e in extract_entities(text)}
        assert entities["Acme Corporation"] == 2

    def test_sentence_initial_single_word_ignored(self) -> None:
        """A single capitalised sentence start such as ``The`` is not an entity."""
        assert extract_entities("The team shipped the release on time.") == []

    def test_leading_article_stripped(self) -> None:
        names = {e.name for e in extract_entities("We met with The Acme Corporation today.")}
        assert "Acme Corporation" in names
        assert "The Acme Corporation" not in names

    def test_deterministic(self) -> None:
        text = "Bob Smith joined Globex Industries and Project Titan."
        first = [(e.name, e.kind, e.count) for e in extract_entities(text)]
        second = [(e.name, e.kind, e.count) for e in extract_entities(text)]
        assert first == second


class TestBoundaries:
    """A proper noun may never span a line, heading, label or bracket boundary - the
    defect that produced entities like "Founder Profile What" and "TO ANSWER What"."""

    def test_heading_does_not_glue_onto_next_line(self) -> None:
        names = {e.name for e in extract_entities("1. Founder Profile\nWhat is your title?")}
        assert not any("What" in n for n in names)

    def test_form_label_does_not_glue_onto_answer(self) -> None:
        text = "Company URL: https://example.com\nWhat is your company going to make?"
        assert not any("What" in e.name for e in extract_entities(text))

    def test_bracketed_placeholder_is_not_an_entity(self) -> None:
        text = "Are people using your product? No\n[ TO ANSWER ] What batch?"
        names = {e.name for e in extract_entities(text)}
        assert not any("ANSWER" in n or "TO" in n.split() for n in names)

    def test_trailing_verb_is_trimmed(self) -> None:
        names = {e.name for e in extract_entities("Third Brain Describe your product.")}
        assert "Third Brain" in names
        assert "Third Brain Describe" not in names

    def test_sentence_boundary_does_not_glue(self) -> None:
        names = {e.name for e in extract_entities("We shipped Acme Corp. Later work continued.")}
        assert "Acme Corp" in names
        assert not any("Later" in n for n in names)


class TestSingleTokenEntities:
    """Single-token names were dropped entirely, losing every one-word company/tool."""

    def test_known_company_and_tool_are_found(self) -> None:
        found = _by_name("We deployed on Anthropic and Redis with FastAPI.")
        assert found.get("Anthropic") == EntityKind.ORG
        assert found.get("Redis") == EntityKind.PRODUCT
        assert found.get("FastAPI") == EntityKind.PRODUCT

    def test_lowercase_gazetteer_term_is_found(self) -> None:
        assert _by_name("Vectors live in pgvector.").get("pgvector") == EntityKind.PRODUCT

    def test_internal_caps_admits_unknown_product(self) -> None:
        assert "TurboWidget" in _by_name("We evaluated TurboWidget last week.")

    def test_bare_capitalised_word_is_not_an_entity(self) -> None:
        assert extract_entities("Production runs nightly.") == []


class TestClassification:
    def test_all_six_kinds_are_reachable(self) -> None:
        found = _by_name(
            "Ketan Mittal met the Y Combinator partners in San Francisco.\n"
            "Project Aurora uses Postgres.\nThe Widget Exchange opened."
        )
        assert found.get("Ketan Mittal") == EntityKind.PERSON
        assert found.get("Y Combinator") == EntityKind.ORG
        assert found.get("San Francisco") == EntityKind.LOCATION
        assert found.get("Project Aurora") == EntityKind.PROJECT
        assert found.get("Postgres") == EntityKind.PRODUCT

    def test_unknown_first_name_still_reads_as_person(self) -> None:
        assert _by_name("Tomas Lindqvist reviewed it.").get("Tomas Lindqvist") == EntityKind.PERSON

    def test_job_titles_are_not_people(self) -> None:
        found = _by_name("Engineering Manager and Staff Engineer roles are open.")
        assert EntityKind.PERSON not in found.values()

    def test_place_word_reads_as_location(self) -> None:
        assert _by_name("The office is on Market Street.").get("Market Street") == (
            EntityKind.LOCATION
        )

    def test_kind_is_stable_across_contexts(self) -> None:
        """Kind must be name-intrinsic: the (org, kind, normalized) unique index would
        otherwise fork one name into duplicate rows across documents."""
        a = _by_name("Anthropic shipped a model.")["Anthropic"]
        b = _by_name("We evaluated Anthropic against alternatives.")["Anthropic"]
        assert a == b


class TestNoiseRejection:
    def test_screaming_case_constants_are_rejected(self) -> None:
        text = "Set OTEL EXPORTER OTLP ENDPOINT and pick ORG TEAM PRIVATE."
        assert extract_entities(text) == []

    def test_filenames_are_rejected(self) -> None:
        assert not any("md" in e.name for e in extract_entities("See SELF HOSTING.md for more."))

    def test_generic_heading_words_are_rejected(self) -> None:
        assert extract_entities("Summary\nOverview\nNext Steps\nAccomplishments") == []

    def test_name_length_is_bounded(self) -> None:
        """The name column is String(512); nothing sentence-length may reach it."""
        text = " ".join(f"Word{i}" for i in range(40))
        assert all(len(e.name) <= 120 for e in extract_entities(text))


class TestLlmParser:
    """The LLM reply is untrusted input: the parser must never raise and never let
    malformed output through."""

    def test_parses_plain_object(self) -> None:
        out = parse_llm_entities('{"entities":[{"name":"Acme Corp","kind":"org","count":3}]}')
        assert [(e.name, e.kind, e.count) for e in out] == [("Acme Corp", EntityKind.ORG, 3)]

    def test_parses_through_code_fence_and_prose(self) -> None:
        reply = (
            "Here you go:\n```json\n"
            '{"entities":[{"name":"Redis","kind":"product"}]}'
            "\n```\nHope that helps!"
        )
        assert [e.name for e in parse_llm_entities(reply)] == ["Redis"]

    def test_bare_array_is_accepted(self) -> None:
        assert [e.name for e in parse_llm_entities('[{"name":"Stripe","kind":"org"}]')] == [
            "Stripe"
        ]

    def test_unknown_kind_falls_back_to_other(self) -> None:
        out = parse_llm_entities('{"entities":[{"name":"Thing","kind":"spaceship"}]}')
        assert out[0].kind == EntityKind.OTHER

    def test_garbage_returns_empty_without_raising(self) -> None:
        for bad in ["", "   ", "not json at all", "{broken", '{"entities": "nope"}', "null"]:
            assert parse_llm_entities(bad) == []

    def test_offline_stub_prose_yields_nothing(self) -> None:
        stub = "[offline model] No live LLM provider is configured. You asked: extract entities"
        assert parse_llm_entities(stub) == []

    def test_duplicates_and_bad_items_are_dropped(self) -> None:
        reply = (
            '{"entities":[{"name":"Redis","kind":"product"},null,"junk",'
            '{"name":"redis","kind":"product"},{"noname":1}]}'
        )
        assert len(parse_llm_entities(reply)) == 1

    def test_oversized_names_are_rejected(self) -> None:
        long_name = "A" * 400
        assert parse_llm_entities(f'{{"entities":[{{"name":"{long_name}","kind":"org"}}]}}') == []
