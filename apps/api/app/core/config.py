"""Application configuration, loaded from environment variables.

Single source of truth for settings. Import the module-level ``settings`` object
everywhere rather than reading ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---- Core ----
    PROJECT_NAME: str = "Third Brain"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    # Baked into Docker images at build time; "unknown" outside a built image.
    GIT_COMMIT: str = "unknown"
    BUILD_TIME: str = "unknown"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "change-me"
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    # ---- Database ----
    POSTGRES_USER: str = "thirdbrain"
    POSTGRES_PASSWORD: str = "thirdbrain"
    POSTGRES_DB: str = "thirdbrain"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str | None = None  # explicit override wins over the parts above
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    SQL_ECHO: bool = False

    # ---- Redis ----
    REDIS_URL: str = "redis://redis:6379/0"

    # ---- Auth / JWT ----
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    JWT_ALGORITHM: str = "HS256"

    # ---- Embeddings / retrieval ----
    EMBEDDING_PROVIDER: str = "openai"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536
    DEFAULT_COMPLETION_MODEL: str = "gpt-4o-mini"
    # Chunking: TARGET is the preferred size - the chunker closes a chunk at the next
    # natural boundary (heading > paragraph > sentence) once it is reached. SIZE is the
    # hard ceiling (must stay under every embedding provider's input limit; Gemini's
    # gemini-embedding-001 caps at 2048 input tokens). OVERLAP applies only to the
    # forced split of a single unpunctuated run longer than the whole ceiling.
    CHUNK_SIZE_TOKENS: int = 1024
    CHUNK_TARGET_TOKENS: int = 768
    CHUNK_OVERLAP_TOKENS: int = 64
    RETRIEVAL_TOP_K: int = 8
    EMBED_CACHE_TTL_SECONDS: int = 86400

    # ---- LLM providers ----
    # Platform fallback keys used when an org has no connector configured. Completion
    # priority: OpenAI key -> Anthropic key -> Google key -> offline stub. Embedding
    # priority: OpenAI key -> Google key -> offline (Anthropic has no embeddings API).
    # OpenAI-compatible endpoint (OpenAI, Azure, Ollama, vLLM…):
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    # Anthropic Messages API (chat only):
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"
    # Google Gemini (chat + embeddings):
    GOOGLE_API_KEY: str | None = None
    GOOGLE_BASE_URL: str = "https://generativelanguage.googleapis.com"

    # ---- Storage ----
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_PATH: str = "/data/uploads"
    S3_ENDPOINT_URL: str | None = None
    S3_BUCKET: str = "third-brain"
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_REGION: str = "us-east-1"

    # ---- Secret scanning ----
    # Scan ingested content for credentials (API keys, private keys, tokens) and
    # quarantine flagged documents for human review before they are indexed.
    SECRET_SCAN_ENABLED: bool = True

    # ---- Rate limiting ----
    DEFAULT_RATE_LIMIT_PER_MINUTE: int = 120
    # Per-user budget for dashboard (JWT session) callers. Sessions carry no API key, so
    # without this a signed-in browser could drive provider-billed endpoints unmetered.
    SESSION_RATE_LIMIT_PER_MINUTE: int = 60

    # ---- Self-serve signup ----
    # Whether ``POST /auth/register`` is open. Unset means "enabled outside production":
    # a deployment holding real provider keys must opt in explicitly, so an open
    # registration form can never quietly bill the operator's account.
    SIGNUP_ENABLED: bool | None = None

    # Hard cap on any single request body, enforced by middleware before a handler buffers it,
    # so an unauthenticated caller cannot exhaust memory with one huge body. Must stay >= the
    # 25 MiB document-upload cap (multipart uploads spool to disk, but JSON bodies buffer in
    # memory), with headroom for multipart framing.
    MAX_REQUEST_BODY_BYTES: int = 32 * 1024 * 1024

    # ---- Public waitlist (pre-launch signups on the marketing site) ----
    WAITLIST_ENABLED: bool = True
    # Cloudflare Turnstile secret key. When set, waitlist submissions must carry a valid
    # Turnstile token (bot protection). When unset (dev/CI), verification is skipped so the
    # form works with no configuration. The matching *site* key is a NEXT_PUBLIC_ var on web.
    TURNSTILE_SECRET_KEY: str | None = None
    WAITLIST_RATE_LIMIT_PER_MINUTE: int = 10

    # ---- Data-source connectors (ingestion + source-ACL sync) ----
    # When a connector syncs a document it materialises the source system's ACL as
    # AccessGrant rows, so the existing permission engine enforces them unchanged.
    DATA_SOURCE_SYNC_ENABLED: bool = True
    DATA_SOURCE_SYNC_INTERVAL_MINUTES: int = 30  # arq cron cadence for scheduled syncs
    # Filesystem roots the reference local_folder connector may read from (comma-separated
    # absolute paths). EMPTY by default, which DISABLES the connector: it reads raw files off
    # the server's own disk, so without an explicit operator allow-list a tenant admin could
    # otherwise point it at arbitrary paths (/etc, /proc/self/environ, another tenant's
    # uploads) and ingest them into their own knowledge base. A configured 'root' must resolve
    # (symlinks + '..' collapsed) to inside one of these directories.
    LOCAL_CONNECTOR_ROOTS: str = ""

    # ---- DLP / PII scanning (extends the secret scanner) ----
    DLP_ENABLED: bool = True
    # What to do with documents that contain PII/confidential content:
    #   label      - index normally but tag ``sensitivity`` (default)
    #   quarantine - park for review before indexing (like a secret hit)
    #   warn       - index and record a finding, no label change
    DLP_DEFAULT_ACTION: Literal["label", "quarantine", "warn"] = "label"

    # ---- Entity extraction (NER enrichment) ----
    ENTITY_EXTRACTION_ENABLED: bool = True
    # Which extractor builds the entity index:
    #   heuristic - deterministic, offline, zero provider cost (always available)
    #   llm       - ask the org's completion model; automatically falls back to
    #               ``heuristic`` when no billable provider is configured, the call
    #               fails, or the reply will not parse (so CI/keyless deploys are unchanged)
    ENTITY_EXTRACTION_MODE: Literal["heuristic", "llm"] = "llm"
    ENTITY_EXTRACTION_MAX_CHARS: int = 20000  # cap the text sent to the extractor per doc

    # ---- Verified answers / content freshness ----
    DEFAULT_REVIEW_INTERVAL_DAYS: int = 180

    # ---- Answer feedback / knowledge-gap analytics ----
    FEEDBACK_ENABLED: bool = True

    # ---- Web grounding for the assistant (pluggable, offline by default) ----
    # none  - web grounding unavailable (default; fully offline)
    # stub  - deterministic canned results (dev/CI/tests, no network)
    # tavily/bing/brave - a real provider (requires WEB_SEARCH_API_KEY)
    WEB_SEARCH_PROVIDER: str = "none"
    WEB_SEARCH_API_KEY: str | None = None
    WEB_SEARCH_BASE_URL: str | None = None
    WEB_SEARCH_MAX_RESULTS: int = 3

    # ---- Enterprise identity: SSO (OIDC + SAML) + SCIM ----
    SSO_ENABLED: bool = True
    SCIM_ENABLED: bool = True

    # ---- Transactional email (invites, notifications) ----
    # stub    - capture in-process, never send (default; dev/CI/tests)
    # console - render to the logs
    # smtp    - deliver via the SMTP_* settings below
    EMAIL_PROVIDER: Literal["stub", "console", "smtp"] = "stub"
    EMAIL_FROM: str = "Third Brain <no-reply@thirdbrain.local>"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    # Public origin of THIS API, used to build absolute links the outside world can
    # fetch (e.g. the capability URLs embedded in image summary chunks).
    PUBLIC_API_URL: str = "http://localhost:8000"
    # Public URL of the web app, used to build invite/SSO links in emails.
    APP_BASE_URL: str = "http://localhost:3000"
    # How long an org invite stays valid.
    INVITE_EXPIRE_HOURS: int = 168

    # ---- Observability ----
    LOG_FORMAT: Literal["auto", "console", "json"] = "auto"
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None
    OTEL_EXPORTER_OTLP_HEADERS: str | None = None  # comma-separated key=value pairs
    OTEL_SERVICE_NAME: str | None = None
    OTEL_TRACES_SAMPLE_RATIO: float = 1.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver)."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def alembic_database_uri(self) -> str:
        """Sync URL for Alembic migrations (psycopg driver not needed at runtime)."""
        return self.sqlalchemy_database_uri.replace("+asyncpg", "+psycopg")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.BACKEND_CORS_ORIGINS.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def signup_enabled(self) -> bool:
        """Whether self-serve registration is open (secure-by-default in production)."""
        if self.SIGNUP_ENABLED is not None:
            return self.SIGNUP_ENABLED
        return not self.is_production

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_deployed(self) -> bool:
        """Any non-development environment (staging, production).

        Hardening that must not be limited to production alone - refusing a placeholder
        SECRET_KEY and sanitizing 500 responses - keys off this, so a first-class ``staging``
        deployment is protected too, not just ``production``.
        """
        return self.ENVIRONMENT != "development"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def log_format_resolved(self) -> str:
        """Concrete log renderer: ``auto`` means JSON in production, console elsewhere."""
        if self.LOG_FORMAT == "auto":
            return "json" if self.is_production else "console"
        return self.LOG_FORMAT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def otel_enabled(self) -> bool:
        return bool(self.OTEL_EXPORTER_OTLP_ENDPOINT)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
