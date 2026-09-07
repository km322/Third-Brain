"""Response schemas for the knowledge-graph surface.

These mirror the frontend ``GraphNode``, ``GraphEdge``, ``GraphResponse`` and
``GraphNeighbors`` types (``apps/web/lib/types.ts``) exactly, so the API and dashboard
stay in lockstep. A node is a document (a memory / file); an edge connects two documents
whose content is similar. Everything here is permission-scoped upstream: only documents
the caller may view are ever emitted, and an edge only ever joins two visible documents.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A single document rendered as a graph node."""

    id: uuid.UUID
    title: str
    collection_id: uuid.UUID
    collection_name: str
    chunk_count: int
    source_type: str
    created_at: datetime
    degree: int = Field(description="Number of surviving edges incident to this node.")


class GraphEdge(BaseModel):
    """An undirected similarity edge between two documents.

    Emitted once per pair with the lexicographically smaller id as ``source``.
    """

    source: uuid.UUID
    target: uuid.UUID
    weight: float = Field(description="Cosine similarity in [0, 1] between the two documents.")


class GraphResponse(BaseModel):
    """Response for ``GET /graph`` - the caller's visible document-similarity graph."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = Field(description="True when the visible indexed-doc count exceeded limit.")
    total_visible: int = Field(description="Visible indexed documents before the cap.")
    limit: int
    min_similarity: float


class GraphNeighbors(BaseModel):
    """Response for ``GET /graph/documents/{document_id}/neighbors``."""

    center_id: uuid.UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]
