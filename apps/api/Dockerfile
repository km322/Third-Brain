# syntax=docker/dockerfile:1

# ============================================================================
# Third Brain - API (FastAPI) production image.
#
# Three stages:
#   uv      -> the pinned uv binary (a stage of its own so Dependabot tracks the digest)
#   builder -> installs the locked dependencies into a virtualenv, compiling any wheels
#   runtime -> slim image that only carries the venv + application source
#
# The same image runs the API (uvicorn), the arq worker, and one-shot
# `alembic upgrade head` migrations - the process is chosen via the command.
# ============================================================================

# ---- uv: pinned by digest; the builder copies the binary out of it ----
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# ---- builder: install the locked dependencies into an isolated virtualenv ----
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Toolchain for any packages without a manylinux wheel (bcrypt, cryptography, …).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# uv installs the exact hash-checked set in uv.lock.
COPY --from=uv /uv /usr/local/bin/uv

# Self-contained virtualenv we can copy wholesale into the runtime stage, built with the
# image's own interpreter (never a downloaded one). Bytecode is compiled here because the
# runtime sets PYTHONDONTWRITEBYTECODE: without it every process start recompiles the
# whole dependency tree.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Runtime dependencies only, exactly as locked: no dev or load extras, and no build of the
# project itself - the runtime stage runs the source from /app (see PYTHONPATH below).
# --locked fails the build if uv.lock no longer matches pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

# ---- runtime: minimal image, non-root ----
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
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
