"""Wire-protocol adapters for the LLM providers the facade can talk to.

Each module exposes the same thin, stateless surface consumed by
:mod:`app.services.llm.client` (the facade keeps ownership of the shared httpx client,
spans, metering and the offline stub):

- ``SUPPORTS_EMBEDDINGS`` - whether the provider has an embeddings API.
- ``headers(key)`` - auth/content headers; a keyless call sends no credential at all.
- ``chat_url(base, model, stream=...)`` / ``chat_payload(...)`` / ``parse_chat(data)``.
- ``iter_chat_deltas(resp)`` - text deltas from the provider's streaming response.
- ``embeddings_url(...)`` / ``embeddings_payload(...)`` / ``parse_embeddings(data)``
  (only when ``SUPPORTS_EMBEDDINGS``).

Adapters never log or raise message/document content - provider errors carry wire
metadata only.
"""

from app.services.llm.providers import anthropic, google, openai

PROVIDERS = {
    "openai": openai,
    "anthropic": anthropic,
    "google": google,
}

__all__ = ["PROVIDERS", "anthropic", "google", "openai"]
