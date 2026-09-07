# syntax=docker/dockerfile:1

# ============================================================================
# Third Brain - API (FastAPI) production image.
#
# Two stages:
#   builder -> compiles any wheels and installs the project into a virtualenv
#   runtime -> slim image that only carries the venv + application source
#
# The same image runs the API (uvicorn), the arq worker, and one-shot
# `alembic upgrade head` migrations - the process is chosen via the command.
# ============================================================================

# ---- builder: build wheels + install into an isolated virtualenv ----
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Toolchain for any packages without a manylinux wheel (bcrypt, cryptography, …).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Self-contained virtualenv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install runtime dependencies + the project itself (no dev extras).
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# ---- runtime: minimal image, non-root ----
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    UVICORN_WORKERS=4

# Unprivileged runtime user; fixed uid/gid for predictable volume ownership.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --create-home appuser

WORKDIR /app

# Bring the built virtualenv across; no build toolchain in the final image.
COPY --from=builder /opt/venv /opt/venv

# Application source + Alembic migrations (the package resolves from CWD).
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini pyproject.toml ./

# Local-storage upload dir; owned by the app user so a fresh named volume
# mounted here inherits writable ownership.
RUN mkdir -p /data/uploads && chown -R appuser:appuser /data

# Build metadata (ENV feeds Settings -> GET /api/v1/version). Kept last so the
# per-build values never invalidate the venv/source layers above.
ARG APP_VERSION=0.0.0-dev
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME}
LABEL org.opencontainers.image.title="Third Brain API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/km322/Third-Brain"

USER appuser

EXPOSE 8000

# Liveness/readiness: the app must serve a 2xx on its health endpoint.
# (The route lives under the versioned prefix: /api/v1/health.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status == 200 else 1)"

# Production default: multiple uvicorn workers (override count via UVICORN_WORKERS).
# `exec` so uvicorn receives SIGTERM/SIGINT directly for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4}"]
