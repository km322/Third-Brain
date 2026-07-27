#!/usr/bin/env bash
# One-command self-host bring-up for Third Brain.
#
# Idempotent and safe to re-run. On a fresh host it:
#   1. generates a hardened .env (strong SECRET_KEY + DB password, ENVIRONMENT=production,
#      concrete CORS/URLs) from .env.example - NEVER overwriting an existing .env,
#   2. builds and starts the production compose stack (docker compose -f docker-compose.prod.yml),
#   3. waits for the API to become healthy (the migrate one-shot gates it),
#   4. creates the first admin account inside the api container, and
#   5. prints how to sign in - all data stays in your own Postgres/Redis/uploads.
#
#   ADMIN_EMAIL=you@example.com ./scripts/selfhost-init.sh
#
# Flags / env:
#   --email <addr>       admin email        (or ADMIN_EMAIL; prompted when interactive)
#   --host <host>        public host/IP     (or TB_HOST; default "localhost")
#   --org-name <name>    organization name  (or ADMIN_ORG_NAME; default "My Company")
#   --with-api-key       also mint a first admin API key (or BOOTSTRAP_API_KEY=1)
#   --env-only           just generate .env and exit (no Docker needed)
#   -h, --help           show this help
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The single-box overlay publishes the web/api ports, so a localhost or bare-IP self-host
# is reachable in a browser without a domain. For a public TLS domain, use the Cloudflare
# Tunnel overlay (docker-compose.cloudflare.yml) or your own proxy (see docs/SELF_HOSTING.md).
COMPOSE_FILES=(-f docker-compose.prod.yml -f docker-compose.selfhost.yml)
COMPOSE_DISPLAY="docker-compose.prod.yml + docker-compose.selfhost.yml"
ENV_FILE=".env"
EXAMPLE_FILE=".env.example"
HEALTH_TIMEOUT_SECONDS=300

usage() {
  # Print the contiguous comment header (skip the shebang), stopping at the first code line.
  awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
  exit "${1:-0}"
}

log() { printf '%s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

# A value is "truthy" for flag env vars if it is one of these (matches bootstrap_admin.py).
is_truthy() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac; }

# ---- args ----
TB_HOST="${TB_HOST:-localhost}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_ORG_NAME="${ADMIN_ORG_NAME:-My Company}"
WITH_API_KEY=0
is_truthy "${BOOTSTRAP_API_KEY:-}" && WITH_API_KEY=1
HOST_OVERRIDDEN=0
ENV_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) ADMIN_EMAIL="${2:?--email needs a value}"; shift 2 ;;
    --host) TB_HOST="${2:?--host needs a value}"; HOST_OVERRIDDEN=1; shift 2 ;;
    --org-name) ADMIN_ORG_NAME="${2:?--org-name needs a value}"; shift 2 ;;
    --with-api-key) WITH_API_KEY=1; shift ;;
    --env-only) ENV_ONLY=1; shift ;;
    -h|--help) usage 0 ;;
    *) err "unknown argument: $1"; usage 1 ;;
  esac
done

# Publish the app ports on loopback only for a localhost deploy (not exposed to the LAN);
# bind all interfaces only when an explicit host/IP was given. TB_BIND_IP is read by the
# docker-compose.selfhost.yml overlay. Docker publishing bypasses the host firewall, so a
# 0.0.0.0 bind is internet-reachable - keep it opt-in.
if [[ "$HOST_OVERRIDDEN" -eq 1 && "$TB_HOST" != "localhost" && "$TB_HOST" != "127.0.0.1" && "$TB_HOST" != "::1" ]]; then
  export TB_BIND_IP="${TB_BIND_IP:-0.0.0.0}"
else
  export TB_BIND_IP="${TB_BIND_IP:-127.0.0.1}"
fi

# Published host ports; keep the advertised/baked URLs in lockstep with the overlay knobs.
export WEB_PORT="${WEB_PORT:-3000}"
export API_PORT="${API_PORT:-8000}"

# Public URLs. Defaults keep the documented localhost endpoints working; override the whole
# URL with TB_WEB_URL / TB_API_URL for a custom scheme, else substitute the host + port.
WEB_URL="${TB_WEB_URL:-http://${TB_HOST}:${WEB_PORT}}"
API_URL="${TB_API_URL:-http://${TB_HOST}:${API_PORT}}"

# Loud warning when publishing on all interfaces without TLS: the login password, session JWT
# and API key would then traverse the network in cleartext. This bare-IP/HTTP mode is fine for
# a trusted LAN only; for public exposure put Third Brain behind TLS (a TLS-terminating proxy
# or the Cloudflare Tunnel overlay - see docs/SELF_HOSTING.md).
if [[ "$TB_BIND_IP" == "0.0.0.0" && "$WEB_URL" == http://* ]]; then
  err "WARNING: publishing on all interfaces (0.0.0.0) over plain HTTP - no TLS."
  err "         Admin credentials, session tokens and API keys will be sent in cleartext and"
  err "         are exposed to anyone on the network path. Before exposing Third Brain publicly,"
  err "         put it behind TLS (a TLS-terminating proxy or the Cloudflare Tunnel overlay -"
  err "         see docs/SELF_HOSTING.md)."
fi

# ---- secret generation (openssl preferred; /dev/urandom fallback) ----
gen_secret() {
  # URL-safe, sed-safe token >= 32 chars: no '+', '/', '=' or other metacharacters.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_' | tr -d '='
  else
    LC_ALL=C tr -dc 'A-Za-z0-9-_' </dev/urandom | head -c 64
  fi
}

# set_env_var KEY VALUE FILE - replace the KEY=... line in place, or append it if absent.
# Uses awk with the value passed via the environment so no character in the value is
# interpreted (no sed/awk metacharacter or escape pitfalls).
set_env_var() {
  local key="$1" val="$2" file="$3" tmp
  tmp="$(mktemp "${file}.XXXXXX")"
  __k="$key" __v="$val" awk '
    index($0, ENVIRON["__k"] "=") == 1 { print ENVIRON["__k"] "=" ENVIRON["__v"]; found = 1; next }
    { print }
    END { if (!found) print ENVIRON["__k"] "=" ENVIRON["__v"] }
  ' "$file" >"$tmp"
  mv "$tmp" "$file"
}

generate_env() {
  if [[ -f "$ENV_FILE" ]]; then
    log "Keeping existing $ENV_FILE (secrets left untouched)."
    # The prod compose forces ENVIRONMENT=production, whose boot guard refuses a placeholder
    # SECRET_KEY. A leftover dev/example .env would fail to boot in a confusing loop, so catch
    # it here with a clear message instead.
    local existing
    existing="$(sed -n 's/^SECRET_KEY=//p' "$ENV_FILE" | head -n1)"
    if [[ -z "$existing" || "$existing" == change-me* || "$existing" == changeme* || ${#existing} -lt 32 ]]; then
      err "$ENV_FILE has a placeholder/short SECRET_KEY; the production stack will refuse to boot."
      err "Set a strong SECRET_KEY (>=32 random chars), or remove $ENV_FILE and re-run to generate one."
      exit 1
    fi
    # URL/host overrides can't be applied without touching secrets, so warn rather than lie.
    if [[ "$HOST_OVERRIDDEN" -eq 1 || -n "${TB_WEB_URL:-}" || -n "${TB_API_URL:-}" ]]; then
      err "note: a host/URL override was given but $ENV_FILE already exists; its APP_BASE_URL /"
      err "      NEXT_PUBLIC_API_URL / PUBLIC_API_URL / BACKEND_CORS_ORIGINS are authoritative."
      err "      Edit $ENV_FILE (and rebuild with 'make selfhost') to change them."
    fi
    return 0
  fi
  [[ -f "$EXAMPLE_FILE" ]] || { err "$EXAMPLE_FILE not found in $ROOT"; exit 1; }

  # Write to a temp file in the destination directory so the final move is an atomic
  # same-filesystem rename, never a cross-device copy that could leave a half-written .env.
  local tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  cp "$EXAMPLE_FILE" "$tmp"

  set_env_var SECRET_KEY "$(gen_secret)" "$tmp"
  set_env_var POSTGRES_PASSWORD "$(gen_secret)" "$tmp"
  set_env_var ENVIRONMENT production "$tmp"
  set_env_var BACKEND_CORS_ORIGINS "${WEB_URL},${API_URL}" "$tmp"
  set_env_var APP_BASE_URL "$WEB_URL" "$tmp"
  set_env_var NEXT_PUBLIC_API_URL "$API_URL" "$tmp"
  # Baked into image documents' capability links at ingestion; a wrong value here means
  # every image indexed before the fix carries a dead link until reprocessed.
  set_env_var PUBLIC_API_URL "$API_URL" "$tmp"

  # Lock down permissions before it holds real secrets.
  chmod 600 "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
  log "Generated $ENV_FILE with a fresh SECRET_KEY and POSTGRES_PASSWORD (values not shown)."
  log "  ENVIRONMENT=production  web=${WEB_URL}  api=${API_URL}"
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    err "Docker is not installed or not on PATH."
    manual_steps
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    err "'docker compose' (v2) is unavailable. Install the Compose plugin."
    manual_steps
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    err "The Docker daemon is not running or is unreachable. Start Docker and re-run."
    exit 1
  fi
}

manual_steps() {
  cat >&2 <<EOF

Manual steps (once Docker is available):
  1. ADMIN_EMAIL=you@example.com ./scripts/selfhost-init.sh   # regenerates nothing if .env exists
  2. docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml up -d --build
  3. docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml exec -T api python -m app.scripts.bootstrap_admin
EOF
}

compose() { docker compose "${COMPOSE_FILES[@]}" "$@"; }

wait_for_api() {
  log "Waiting for the API to become healthy (up to ${HEALTH_TIMEOUT_SECONDS}s)..."
  local deadline=$(( SECONDS + HEALTH_TIMEOUT_SECONDS ))
  while (( SECONDS < deadline )); do
    # Probe from inside the container, so this works whether or not the api port is
    # published to the host (the prod topology does not publish the api port).
    if compose exec -T api python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status == 200 else 1)" \
      >/dev/null 2>&1; then
      log "API is healthy."
      return 0
    fi
    sleep 3
  done
  err "API did not become healthy within ${HEALTH_TIMEOUT_SECONDS}s."
  err "Inspect the logs with: docker compose ${COMPOSE_FILES[*]} logs api migrate"
  exit 1
}

bootstrap_admin() {
  if [[ -z "$ADMIN_EMAIL" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "Admin email: " ADMIN_EMAIL
    fi
  fi
  if [[ -z "$ADMIN_EMAIL" ]]; then
    err "ADMIN_EMAIL is required (set ADMIN_EMAIL, pass --email, or run interactively)."
    exit 1
  fi

  local -a args=(exec -T
    -e "ADMIN_EMAIL=${ADMIN_EMAIL}"
    -e "ADMIN_ORG_NAME=${ADMIN_ORG_NAME}")
  [[ "$WITH_API_KEY" -eq 1 ]] && args+=(-e "BOOTSTRAP_API_KEY=1")
  args+=(api python -m app.scripts.bootstrap_admin)

  log ""
  log "Creating the first admin account..."
  compose "${args[@]}"
}

main() {
  generate_env
  if [[ "$ENV_ONLY" -eq 1 ]]; then
    log "--env-only: skipping Docker. Review $ENV_FILE, then run without --env-only to boot."
    return 0
  fi

  require_docker

  log "Building and starting the stack ($COMPOSE_DISPLAY)..."
  compose up -d --build

  wait_for_api
  bootstrap_admin

  cat <<EOF

------------------------------------------------------------------
Third Brain is running.

  Web:  ${WEB_URL}

All data stays in YOUR Postgres, Redis and uploads volume - nothing
is sent to us. The offline stub model means zero outbound calls until
you configure your own LLM provider.

Next steps:
  - Sign in with the admin credentials printed above and connect a model.
  - Wire up an MCP client:  npx third-brain-mcp connect
  - Stop the stack:         make selfhost-down
------------------------------------------------------------------
EOF
}

main
