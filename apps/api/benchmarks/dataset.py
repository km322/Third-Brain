"""The benchmark dataset: schema, loader, and validator.

A :class:`BenchmarkDataset` is the ground truth the whole harness measures against.
It describes a small, self-consistent company: teams, collections (each with a
visibility and default permission), principals (users with an org role, team
memberships and explicit grants), documents, and natural-language queries labelled
with the documents that genuinely answer them.

The dataset lives on disk as two JSON files in a directory (see :func:`load_dataset`):

* ``corpus.json`` - the static world: ``name``, ``teams``, ``collections``,
  ``principals`` and ``documents``.
* ``queries.json`` - a flat list of queries.

This module is deliberately import-light (only :mod:`dataclasses`, :mod:`json` and
:mod:`os`): it carries no dependency on the application or the database, so tests and
tooling can load and validate a dataset without any infrastructure. The harness maps
these declarations onto real database rows; :func:`validate_dataset` catches the
internal contradictions (dangling references, duplicate ids, a team collection with
no team) that would otherwise surface only as confusing runtime failures.

The critical invariant is the honesty of the labels. ``Principal.visible_collection_keys``
is a *declared* ground truth: the collections a principal must be able to read given
their role, team membership, and grants under the real permission engine. The harness
materialises those roles and grants and lets the system independently resolve
visibility; a mismatch is a benchmark bug (a wrong label silently blessing wrong
behaviour), which is exactly what the harness's leakage check exists to catch.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class CollectionSpec:
    """A knowledge collection with its default reach.

    ``visibility`` is one of ``"org"``, ``"team"`` or ``"private"``;
    ``default_permission`` is ``"viewer"`` or ``"none"`` (the baseline permission the
    visibility confers). ``team_key`` names the owning team and is required exactly
    when ``visibility == "team"``.
    """

    key: str
    name: str
    visibility: str
    default_permission: str
    team_key: str | None = None


@dataclass
class Principal:
    """A user in the benchmark org, together with the ground-truth collections they
    must be able to read.

    ``role`` is the org role (``"owner"``/``"admin"``/``"editor"``/``"viewer"``),
    ``team_keys`` the teams they belong to, and ``grants`` a list of explicit ACL
    entries shaped as ``{"collection_key": str, "permission": "viewer"|"editor"|"manager"}``.
    ``visible_collection_keys`` is the declared set of collection keys this principal
    is entitled to read once role, team and grants are all taken into account.
    """

    key: str
    email: str
    role: str
    team_keys: list[str] = field(default_factory=list)
    grants: list[dict] = field(default_factory=list)
    visible_collection_keys: list[str] = field(default_factory=list)


@dataclass
class DocumentSpec:
    """A single document: a stable dataset ``doc_id``, its ``title``, the
    ``collection_key`` it belongs to, and its plain-text ``content``."""

    doc_id: str
    title: str
    collection_key: str
    content: str


@dataclass
class QuerySpec:
    """A natural-language query and its ground-truth labels.

    ``relevant_doc_ids`` are the documents that genuinely answer the query (judged by
    meaning), ``principal_key`` is who asks it, ``reference_answer`` is an optional
    gold answer, and ``reference_keywords`` are key facts a good answer should mention
    (an offline correctness proxy). A query may be *permission-critical*: a relevant
    document that the asking principal is not entitled to see.
    """

    query_id: str
    text: str
    principal_key: str
    relevant_doc_ids: list[str] = field(default_factory=list)
    reference_answer: str | None = None
    reference_keywords: list[str] | None = None


@dataclass
class BenchmarkDataset:
    """The whole world: teams, collections, principals, documents and queries."""

    name: str
    teams: list[dict] = field(default_factory=list)
    collections: list[CollectionSpec] = field(default_factory=list)
    principals: list[Principal] = field(default_factory=list)
    documents: list[DocumentSpec] = field(default_factory=list)
    queries: list[QuerySpec] = field(default_factory=list)

    def collection_of(self, doc_id: str) -> str:
        """Return the ``collection_key`` of the document with the given ``doc_id``."""
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc.collection_key
        raise KeyError(f"unknown doc_id: {doc_id!r}")

    def visible_doc_ids(self, principal_key: str) -> set[str]:
        """Ground-truth set of doc ids the principal may read: documents whose
        ``collection_key`` is in that principal's ``visible_collection_keys``."""
        visible = set(self.principal(principal_key).visible_collection_keys)
        return {doc.doc_id for doc in self.documents if doc.collection_key in visible}

    def principal(self, key: str) -> Principal:
        """Return the principal with the given ``key``."""
        for principal in self.principals:
            if principal.key == key:
                return principal
        raise KeyError(f"unknown principal: {key!r}")


def _collection_from_dict(raw: dict) -> CollectionSpec:
    return CollectionSpec(
        key=raw["key"],
        name=raw["name"],
        visibility=raw["visibility"],
        default_permission=raw["default_permission"],
        team_key=raw.get("team_key"),
    )


def _principal_from_dict(raw: dict) -> Principal:
    return Principal(
        key=raw["key"],
        email=raw["email"],
        role=raw["role"],
        team_keys=list(raw.get("team_keys", [])),
        grants=[dict(g) for g in raw.get("grants", [])],
        visible_collection_keys=list(raw.get("visible_collection_keys", [])),
    )


def _document_from_dict(raw: dict) -> DocumentSpec:
    return DocumentSpec(
        doc_id=raw["doc_id"],
        title=raw["title"],
        collection_key=raw["collection_key"],
        content=raw["content"],
    )


def _query_from_dict(raw: dict) -> QuerySpec:
    return QuerySpec(
        query_id=raw["query_id"],
        text=raw["text"],
        principal_key=raw["principal_key"],
        relevant_doc_ids=list(raw.get("relevant_doc_ids", [])),
        reference_answer=raw.get("reference_answer"),
        reference_keywords=raw.get("reference_keywords"),
    )


def load_dataset(path: str) -> BenchmarkDataset:
    """Load a :class:`BenchmarkDataset` from a directory holding ``corpus.json`` and
    ``queries.json``."""
    with open(os.path.join(path, "corpus.json"), encoding="utf-8") as fh:
        corpus = json.load(fh)
    with open(os.path.join(path, "queries.json"), encoding="utf-8") as fh:
        queries = json.load(fh)

    return BenchmarkDataset(
        name=corpus["name"],
        teams=[dict(t) for t in corpus.get("teams", [])],
        collections=[_collection_from_dict(c) for c in corpus.get("collections", [])],
        principals=[_principal_from_dict(p) for p in corpus.get("principals", [])],
        documents=[_document_from_dict(d) for d in corpus.get("documents", [])],
        queries=[_query_from_dict(q) for q in queries],
    )


def _duplicates(values: list[str]) -> list[str]:
    """Return the values that appear more than once, in first-seen order."""
    seen: set[str] = set()
    dupes: list[str] = []
    for value in values:
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return dupes


def validate_dataset(ds: BenchmarkDataset) -> list[str]:
    """Return a list of human-readable problems with ``ds``; an empty list means valid.

    Checks: duplicate ids (teams, collections, principals, documents, queries);
    documents referencing an unknown collection; a team-visibility collection with no
    ``team_key`` (and any collection naming an unknown team); principals whose team
    memberships, grants or ``visible_collection_keys`` reference unknown teams or
    collections; and queries whose ``principal_key`` or ``relevant_doc_ids`` reference
    unknown principals or documents.
    """
    problems: list[str] = []

    team_keys = {t.get("key") for t in ds.teams}
    collection_keys = {c.key for c in ds.collections}
    principal_keys = {p.key for p in ds.principals}
    doc_ids = {d.doc_id for d in ds.documents}

    for label, values in (
        ("team", [t.get("key") for t in ds.teams]),
        ("collection", [c.key for c in ds.collections]),
        ("principal", [p.key for p in ds.principals]),
        ("document", [d.doc_id for d in ds.documents]),
        ("query", [q.query_id for q in ds.queries]),
    ):
        for dupe in _duplicates([v for v in values if v is not None]):
            problems.append(f"duplicate {label} id: {dupe!r}")

    for collection in ds.collections:
        if collection.visibility == "team" and not collection.team_key:
            problems.append(f"collection {collection.key!r} has team visibility but no team_key")
        if collection.team_key is not None and collection.team_key not in team_keys:
            problems.append(
                f"collection {collection.key!r} references unknown team {collection.team_key!r}"
            )

    for doc in ds.documents:
        if doc.collection_key not in collection_keys:
            problems.append(
                f"document {doc.doc_id!r} references unknown collection {doc.collection_key!r}"
            )

    for principal in ds.principals:
        for team_key in principal.team_keys:
            if team_key not in team_keys:
                problems.append(f"principal {principal.key!r} references unknown team {team_key!r}")
        for grant in principal.grants:
            grant_key = grant.get("collection_key")
            if grant_key not in collection_keys:
                problems.append(
                    f"principal {principal.key!r} grants unknown collection {grant_key!r}"
                )
        for coll_key in principal.visible_collection_keys:
            if coll_key not in collection_keys:
                problems.append(
                    f"principal {principal.key!r} declares unknown visible collection {coll_key!r}"
                )

    for query in ds.queries:
        if query.principal_key not in principal_keys:
            problems.append(
                f"query {query.query_id!r} references unknown principal {query.principal_key!r}"
            )
        for doc_id in query.relevant_doc_ids:
            if doc_id not in doc_ids:
                problems.append(f"query {query.query_id!r} references unknown document {doc_id!r}")

    return problems
