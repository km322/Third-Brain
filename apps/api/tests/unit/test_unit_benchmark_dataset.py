"""Unit tests for the benchmark dataset loader and the golden dataset itself.

These run with no infrastructure: they load the on-disk golden dataset, confirm it is
internally consistent (``validate_dataset`` finds nothing wrong), and pin down the
ground-truth visibility that the harness will later reconcile against the real
permission engine. A permission-critical query - one whose relevant document the asking
principal is not entitled to read - is what proves the benchmark can catch leakage, so
the suite asserts at least one exists.
"""

from __future__ import annotations

import os

import benchmarks.dataset as dataset_mod
from benchmarks.dataset import load_dataset, validate_dataset

GOLDEN_DIR = os.path.join(os.path.dirname(dataset_mod.__file__), "golden")


def _load():
    return load_dataset(GOLDEN_DIR)


def _doc_ids_in(ds, collection_key: str) -> set[str]:
    return {d.doc_id for d in ds.documents if d.collection_key == collection_key}


def test_golden_dataset_parses_with_expected_shape() -> None:
    ds = _load()
    assert ds.name == "acme-second-brain"
    assert len(ds.teams) == 1
    assert len(ds.collections) == 3
    assert len(ds.principals) == 5
    assert len(ds.documents) == 36
    assert len(ds.queries) == 38
    # ids are unique within each kind
    assert len({d.doc_id for d in ds.documents}) == len(ds.documents)
    assert len({q.query_id for q in ds.queries}) == len(ds.queries)


def test_golden_dataset_is_internally_valid() -> None:
    problems = validate_dataset(_load())
    assert problems == [], f"golden dataset has validation problems: {problems}"


def test_visible_doc_ids_match_declared_visibility() -> None:
    ds = _load()
    eng = _doc_ids_in(ds, "engineering")
    people = _doc_ids_in(ds, "people-ops")
    exec_docs = _doc_ids_in(ds, "exec")

    # Sanity: every document belongs to one of the three collections.
    assert eng and people and exec_docs
    assert eng | people | exec_docs == {d.doc_id for d in ds.documents}

    # Org-visibility engineering is readable by everyone; the team and private
    # collections are not readable by an outsider.
    assert ds.visible_doc_ids("engineer") == eng
    # A people-team member additionally reads people-ops but never exec.
    assert ds.visible_doc_ids("people-admin") == eng | people
    # A plain org viewer only sees the org-wide collection.
    assert ds.visible_doc_ids("viewer") == eng
    # An explicit grant on the private exec collection raises just that collection.
    assert ds.visible_doc_ids("exec-analyst") == eng | exec_docs
    # The org owner (admin) sees everything.
    assert ds.visible_doc_ids("owner") == eng | people | exec_docs


def test_specific_cross_principal_visibility_facts() -> None:
    ds = _load()
    # The same document is invisible to one principal and visible to another.
    assert "ppl-parental-leave" not in ds.visible_doc_ids("engineer")
    assert "ppl-parental-leave" in ds.visible_doc_ids("people-admin")
    assert "exec-comp-bands" not in ds.visible_doc_ids("engineer")
    assert "exec-comp-bands" in ds.visible_doc_ids("exec-analyst")


def test_at_least_one_permission_critical_query() -> None:
    ds = _load()
    critical = [
        q for q in ds.queries if set(q.relevant_doc_ids) - ds.visible_doc_ids(q.principal_key)
    ]
    assert critical, "expected at least one permission-critical query"
    # The engineer asking about parental leave must be one of them (relevant doc lives
    # in people-ops, which the engineer cannot read).
    ids = {q.query_id for q in critical}
    assert "q18-parental-leave-engineer-blocked" in ids


def test_collection_of_and_principal_lookup() -> None:
    ds = _load()
    assert ds.collection_of("eng-oncall-rotation") == "engineering"
    assert ds.collection_of("exec-comp-bands") == "exec"
    assert ds.principal("owner").role == "owner"
