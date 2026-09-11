"""Locust scenarios for Third Brain at millions-of-chunks scale.

Drives the hot production paths against a corpus seeded by ``python -m
loadtests.seed_corpus``: permission-scoped hybrid and vector-only search (the
RetrievalScope ACL predicate pushed into SQL against the HNSW index is the honest
stress point), RAG chat (JSON and streamed SSE), text-ingestion enqueue, and the
OpenAI-compatible embeddings surface.

Each simulated user binds at start to a random org from the seeder manifest and calls
the API with that org's member API key (``X-API-Key``), so every search runs the real
ACL-scoped retrieval path. The ingest task uses the org's admin key because document
creation requires EDITOR on the collection; it measures enqueue latency, not indexing.

Environment:
* ``MANIFEST`` - path to the seeder manifest (default ``loadtests/.manifest.json``).
* ``API_BASE`` - base URL of the API under test (default ``http://localhost:8000``).

Run headless with thresholds via ``python -m loadtests.run_load_test`` (make load-test).
"""

from __future__ import annotations

import json
import os
import random
import uuid
from pathlib import Path

from locust import FastHttpUser, between, task

DEFAULT_MANIFEST = "loadtests/.manifest.json"
API_BASE = os.environ.get("API_BASE", "http://localhost:8000")


def _resolve_manifest_path() -> Path:
    """Resolve ``MANIFEST`` against the cwd, falling back to the apps/api root."""
    path = Path(os.environ.get("MANIFEST", DEFAULT_MANIFEST))
    if not path.is_absolute() and not path.exists():
        fallback = Path(__file__).resolve().parent.parent / path
        if fallback.exists():
            return fallback
    return path


def _load_manifest() -> dict:
    """Load and sanity-check the seeder manifest; abort with a clear error otherwise."""
    path = _resolve_manifest_path()
    try:
        with path.open(encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        raise SystemExit(
            f"Load-test manifest not found at {path}. Seed a corpus first: "
            "cd apps/api && python -m loadtests.seed_corpus --chunks N "
            "(or make load-seed), or point MANIFEST at an existing manifest."
        ) from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Load-test manifest at {path} is not valid JSON: {exc}") from None
    orgs = manifest.get("orgs") or []
    queries = manifest.get("queries") or []
    if not orgs or not queries:
        raise SystemExit(
            f"Load-test manifest at {path} has no orgs or no queries; re-run the seeder."
        )
    return manifest


MANIFEST = _load_manifest()
ORGS: list[dict] = MANIFEST["orgs"]
QUERIES: list[str] = MANIFEST["queries"]

_WORDS: list[str] = sorted({word for query in QUERIES for word in query.lower().split()})
"""Vocabulary for synthetic ingest bodies, drawn from the manifest queries so ingested
text resembles the seeded corpus."""


def _synthetic_content() -> str:
    """Roughly 100 words of filler, prefixed with a UUID so every document is unique."""
    words = [f"loadtest {uuid.uuid4().hex}"]
    words.extend(random.choice(_WORDS) for _ in range(100))
    return " ".join(words)


def _expect_json(response, required_key: str) -> dict | None:
    """Fail the sampled response unless it is 2xx JSON carrying ``required_key``.

    Returns the parsed body on success, ``None`` after marking a failure. Failure
    messages carry only the status code or key name, never the response body.
    """
    if not 200 <= response.status_code < 300:
        response.failure(f"HTTP {response.status_code}")
        return None
    try:
        body = json.loads(response.content)
    except (TypeError, ValueError):
        response.failure("empty or malformed JSON body")
        return None
    if not isinstance(body, dict) or required_key not in body:
        response.failure(f"JSON body missing '{required_key}'")
        return None
    return body


class ThirdBrainUser(FastHttpUser):
    """One org member issuing the weighted production request mix."""

    host = API_BASE
    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        """Bind this user to a random seeded org and prepare its request headers.

        Both member and admin keys are sent as ``X-API-Key``; the bearer variant exists
        because the /v1 surface authenticates the same API keys via a Bearer header.
        """
        org = random.choice(ORGS)
        self.collection_ids: list[str] = list(org.get("collection_ids") or [])
        self.member_headers = {"X-API-Key": org["member_api_key"]}
        self.admin_headers = {"X-API-Key": org["admin_api_key"]}
        self.bearer_headers = {"Authorization": f"Bearer {org['member_api_key']}"}

    @task(5)
    def search(self) -> None:
        payload = {"query": random.choice(QUERIES), "top_k": 8}
        with self.client.post(
            "/api/v1/search",
            json=payload,
            headers=self.member_headers,
            name="/api/v1/search",
            catch_response=True,
        ) as response:
            body = _expect_json(response, "hits")
            if body is not None and not isinstance(body["hits"], list):
                response.failure("'hits' is not a list")

    @task(2)
    def search_vector_only(self) -> None:
        payload = {"query": random.choice(QUERIES), "top_k": 8, "hybrid": False}
        with self.client.post(
            "/api/v1/search",
            json=payload,
            headers=self.member_headers,
            name="/api/v1/search:vector",
            catch_response=True,
        ) as response:
            body = _expect_json(response, "hits")
            if body is not None and not isinstance(body["hits"], list):
                response.failure("'hits' is not a list")

    @task(1)
    def chat(self) -> None:
        payload = {"query": random.choice(QUERIES), "stream": False}
        with self.client.post(
            "/api/v1/search/chat",
            json=payload,
            headers=self.member_headers,
            name="/api/v1/search/chat",
            catch_response=True,
        ) as response:
            _expect_json(response, "answer")

    @task(1)
    def chat_stream(self) -> None:
        """Streamed chat; the client buffers the whole SSE body, so the sampled time
        covers consuming the entire stream."""
        payload = {"query": random.choice(QUERIES), "stream": True}
        with self.client.post(
            "/api/v1/search/chat",
            json=payload,
            headers=self.member_headers,
            name="/api/v1/search/chat:stream",
            catch_response=True,
        ) as response:
            if not 200 <= response.status_code < 300:
                response.failure(f"HTTP {response.status_code}")
                return
            body = response.content or b""
            if b"data:" not in body or b"[DONE]" not in body:
                response.failure("SSE body missing data frames or [DONE] terminator")

    @task(1)
    def ingest_text(self) -> None:
        """Create a small unique text document; measures enqueue latency, not indexing."""
        if not self.collection_ids:
            return
        payload = {
            "collection_id": random.choice(self.collection_ids),
            "title": f"loadtest-{uuid.uuid4().hex[:12]}",
            "content": _synthetic_content(),
        }
        with self.client.post(
            "/api/v1/documents/text",
            json=payload,
            headers=self.admin_headers,
            name="/api/v1/documents/text",
            catch_response=True,
        ) as response:
            _expect_json(response, "id")

    @task(1)
    def embeddings(self) -> None:
        payload = {"input": random.choice(QUERIES), "model": "text-embedding-3-small"}
        with self.client.post(
            "/v1/embeddings",
            json=payload,
            headers=self.bearer_headers,
            name="/v1/embeddings",
            catch_response=True,
        ) as response:
            body = _expect_json(response, "data")
            if body is not None and not body["data"]:
                response.failure("'data' is empty")

    @task(1)
    def mcp_capture(self) -> None:
        """Agent write-back through /mcp with no explicit collection: measures the
        auto-routed capture path end-to-end (scan, route, chunk, embed, index)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "add_knowledge",
                "arguments": {
                    "title": f"loadtest-capture-{uuid.uuid4().hex[:12]}",
                    "content": _synthetic_content(),
                    "doc_type": "note",
                },
            },
        }
        with self.client.post(
            "/mcp",
            json=payload,
            headers=self.admin_headers,
            name="/mcp:add_knowledge",
            catch_response=True,
        ) as response:
            body = _expect_json(response, "result")
            if body is not None and body["result"].get("isError"):
                response.failure("tool result isError")
