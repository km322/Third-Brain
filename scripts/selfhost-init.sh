#!/usr/bin/env bash
# One-command self-host bring-up for Third Brain.
#
# Idempotent and safe to re-run. On a fresh host it:
#   1. generates a hardened .env (strong SECRET_KEY + DB password, ENVIRONMENT=production,
#      concrete CORS/URLs, and the bind address/ports the overlay publishes on) from
#      .env.example - NEVER overwriting an existing .env,
#   2. builds and starts the production compose stack (docker-compose.prod.yml plus the
#      single-box docker-compose.selfhost.yml overlay, which publishes the web/API ports),
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
#   ADMIN_PASSWORD       pin the first admin password (default: generated and printed once)
#   WEB_PORT, API_PORT   published host ports (default 3000, 8000)
#   TB_BIND_IP           interface they are published on (default 127.0.0.1; --host uses
#                        0.0.0.0). Recorded in .env, so a plain `docker compose up -d` on the
#                        same file pair keeps the binding this script chose.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The single-box overlay publishes the web/api ports, so a localhost or bare-IP self-host
# is reachable in a browser without a domain. For a public TLS domain, use the Cloudflare
# Tunnel overlay (docker-compose.cloudflare.yml) or your own proxy (see docs/SELF_HOSTING.md).
COMPOSE_FILES=(-f docker-compose.prod.yml -f docker-compose.selfhost.yml)
COMPOSE_DISPLAY="docker-compose.prod.yml + docker-compose.selfhost.yml"
# Compose project name of this topology - keep in sync with `name:` in docker-compose.prod.yml.
# It is deliberately NOT the development stack's `third-brain`: Compose namespaces volumes by
# project, and both files declare db_data/redis_data/uploads, so a shared name meant `make up`
# and `make selfhost` fought over the same datastores.
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-third-brain-prod}"
LEGACY_PROJECT_NAME="third-brain"
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

# The publishing knobs (bind address + host ports) are read by docker-compose.selfhost.yml,
# which Compose interpolates from the project .env - NOT from what this script exports into
# its own process. They are therefore persisted into the generated .env by generate_env, so a
# later bare `docker compose -f docker-compose.prod.yml -f docker-compose.selfhost.yml up -d`
# reproduces this run's binding instead of silently reverting to the overlay defaults.
# Resolution order matches Compose's own: an explicit environment value wins, then an existing
# .env, then the default computed here.
TB_BIND_IP_EXPLICIT=0
WEB_PORT_EXPLICIT=0
API_PORT_EXPLICIT=0
[[ -n "${TB_BIND_IP:-}" || "$HOST_OVERRIDDEN" -eq 1 ]] && TB_BIND_IP_EXPLICIT=1
[[ -n "${WEB_PORT:-}" ]] && WEB_PORT_EXPLICIT=1
[[ -n "${API_PORT:-}" ]] && API_PORT_EXPLICIT=1

# Publish the app ports on loopback only for a localhost deploy (not exposed to the LAN);
# bind all interfaces only when an explicit host/IP was given. Docker publishing bypasses the
# host firewall, so a 0.0.0.0 bind is internet-reachable - keep it opt-in.
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
# or the Cloudflare Tunnel overlay - see docs/SELF_HOSTING.md). Called after generate_env, so
# it sees the EFFECTIVE binding and URL, including the ones recorded in an existing .env.
warn_when_public_without_tls() {
  if [[ "$TB_BIND_IP" == "0.0.0.0" && "$WEB_URL" == http://* ]]; then
    err "WARNING: publishing on all interfaces (0.0.0.0) over plain HTTP - no TLS."
    err "         Admin credentials, session tokens and API keys will be sent in cleartext and"
    err "         are exposed to anyone on the network path. Before exposing Third Brain publicly,"
    err "         put it behind TLS (a TLS-terminating proxy or the Cloudflare Tunnel overlay -"
    err "         see docs/SELF_HOSTING.md)."
  fi
}

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

# read_env_var KEY FILE - print the value of the first `KEY=...` line (empty when absent).
read_env_var() {
  local key="$1" file="$2"
  __k="$key" awk '
    index($0, ENVIRON["__k"] "=") == 1 { print substr($0, length(ENVIRON["__k"]) + 2); exit }
  ' "$file"
}

# Mirror of the deployed-environment boot guards in apps/api/app/main.py
# (validate_startup_config). The prod compose forces ENVIRONMENT=production, so a .env that
# trips any of them makes api and web crash-loop - after a full image build and a 300s health
# wait. Catch them here instead, with the same wording the API would have used.
check_env_boot_guards() {
  local secret db_url db_password cors
  secret="$(read_env_var SECRET_KEY "$ENV_FILE")"
  if [[ -z "$secret" || "$secret" == change-me* || "$secret" == changeme* || ${#secret} -lt 32 ]]; then
    err "$ENV_FILE: SECRET_KEY is unset, a shipped placeholder, or shorter than 32 characters."
    err "The production stack refuses to start with it. Set a strong, unique SECRET_KEY (for"
    err "example \`openssl rand -hex 32\`), or remove $ENV_FILE and re-run to generate one."
    exit 1
  fi

  # Only meaningful when the app derives the DB URL from the POSTGRES_* parts; an explicit
  # DATABASE_URL carries its own credentials.
  db_url="$(read_env_var DATABASE_URL "$ENV_FILE")"
  db_password="$(read_env_var POSTGRES_PASSWORD "$ENV_FILE")"
  if [[ -z "$db_url" && "$db_password" == "thirdbrain" ]]; then
    err "$ENV_FILE: POSTGRES_PASSWORD is the shipped default 'thirdbrain'."
    err "The production stack refuses to start with it. Set a strong, unique database password"
    err "for the bundled Postgres (or point DATABASE_URL at a database with its own"
    err "credentials). A password change only takes effect on a fresh db volume, so do this"
    err "before the first boot."
    exit 1
  fi

  # The API splits BACKEND_CORS_ORIGINS on commas and refuses to boot when any entry is a
  # bare '*', because allow_credentials=True then lets any site make credentialed calls.
  cors="$(read_env_var BACKEND_CORS_ORIGINS "$ENV_FILE")"
  if [[ ",${cors//[[:space:]]/}," == *",*,"* ]]; then
    err "$ENV_FILE: BACKEND_CORS_ORIGINS allows all origins ('*') together with credentialed"
    err "requests - this is permissive and unsafe, and the production stack refuses to start."
    err "Set an explicit allowlist, e.g. BACKEND_CORS_ORIGINS=${WEB_URL},${API_URL}"
    exit 1
  fi
}

# resolve_published KEY CURRENT EXPLICIT - print the effective value of a publishing knob in
# an existing .env and leave the file agreeing with it: a value recorded there wins unless it
# was explicitly overridden on this run, in which case the override is written back.
resolve_published() {
  local key="$1" current="$2" explicit="$3" recorded
  recorded="$(read_env_var "$key" "$ENV_FILE")"
  if [[ -n "$recorded" && "$explicit" -eq 0 ]]; then
    printf '%s' "$recorded"
    return 0
  fi
  set_env_var "$key" "$current" "$ENV_FILE"
  printf '%s' "$current"
}

generate_env() {
  if [[ -f "$ENV_FILE" ]]; then
    log "Keeping existing $ENV_FILE (secrets left untouched)."
    check_env_boot_guards
    # URL/host overrides can't be applied without touching secrets, so warn rather than lie.
    if [[ "$HOST_OVERRIDDEN" -eq 1 || -n "${TB_WEB_URL:-}" || -n "${TB_API_URL:-}" ]]; then
      err "note: a host/URL override was given but $ENV_FILE already exists; its APP_BASE_URL /"
      err "      NEXT_PUBLIC_API_URL / PUBLIC_API_URL / BACKEND_CORS_ORIGINS are authoritative."
      err "      Edit $ENV_FILE (and rebuild with 'make selfhost') to change them."
    fi
    # The banner and the no-TLS check must describe what will actually serve, and the note
    # above says this file wins, so take the advertised URL from it.
    local recorded
    recorded="$(read_env_var APP_BASE_URL "$ENV_FILE")"
    [[ -n "$recorded" ]] && WEB_URL="$recorded"

    # Reproduce the recorded binding rather than recomputing it: without this, a re-run (or
    # any bare `docker compose ... up -d`) would fall back to the overlay's loopback default
    # and take an instance published with --host off the network.
    TB_BIND_IP="$(resolve_published TB_BIND_IP "$TB_BIND_IP" "$TB_BIND_IP_EXPLICIT")"
    WEB_PORT="$(resolve_published WEB_PORT "$WEB_PORT" "$WEB_PORT_EXPLICIT")"
    API_PORT="$(resolve_published API_PORT "$API_PORT" "$API_PORT_EXPLICIT")"
    log "  web=${WEB_URL}  published on ${TB_BIND_IP} (web ${WEB_PORT}, api ${API_PORT})"
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

  # .env.example carries no publishing knobs (they mean nothing to the dev stack), so append
  # them with a header. docker-compose.selfhost.yml interpolates all three from this file.
  cat >>"$tmp" <<'EOF'

# ---- Self-host publishing (read by docker-compose.selfhost.yml) ----
# Host interface and ports the dashboard/API are published on. 127.0.0.1 keeps the instance
# off the network; `./scripts/selfhost-init.sh --host <host-or-ip>` records 0.0.0.0 here.
# Docker publishes past the host firewall, so 0.0.0.0 is internet-reachable - front it with
# TLS before you use it in anger (docs/SELF_HOSTING.md).
EOF
  set_env_var TB_BIND_IP "$TB_BIND_IP" "$tmp"
  set_env_var WEB_PORT "$WEB_PORT" "$tmp"
  set_env_var API_PORT "$API_PORT" "$tmp"

  # Lock down permissions before it holds real secrets.
  chmod 600 "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
  log "Generated $ENV_FILE with a fresh SECRET_KEY and POSTGRES_PASSWORD (values not shown)."
  log "  ENVIRONMENT=production  web=${WEB_URL}  api=${API_URL}"
  log "  published on ${TB_BIND_IP} (web ${WEB_PORT}, api ${API_PORT})"
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

# Before v2.0.0 this stack and the development stack shared the Compose project name
# `third-brain`, so they also shared the `third-brain_*` volumes. This topology is now its own
# project, which leaves any data in the old volumes behind: say so plainly rather than coming
# up on an empty database and letting the operator discover it after signing in.
warn_about_legacy_volumes() {
  if [[ "$PROJECT_NAME" == "$LEGACY_PROJECT_NAME" ]]; then
    return 0
  fi
  docker volume inspect "${LEGACY_PROJECT_NAME}_db_data" >/dev/null 2>&1 || return 0
  # Already migrated or already running here: this project has its own database volume.
  if docker volume inspect "${PROJECT_NAME}_db_data" >/dev/null 2>&1; then
    return 0
  fi
  err ""
  err "NOTE: this host has a Docker volume '${LEGACY_PROJECT_NAME}_db_data'."
  err "      Up to v1.x the development stack ('make up') and the self-hosted production stack"
  err "      shared one Compose project, so they shared that volume. This stack is now the"
  err "      '${PROJECT_NAME}' project and will create its own '${PROJECT_NAME}_db_data', so it"
  err "      starts with an EMPTY database - the old volume is left untouched, not deleted."
  err ""
  err "      If it is leftover development data, discard it once the dev stack is down:"
  err "          docker compose down -v"
  err "      If it holds a pre-v2.0.0 self-host you want to keep, re-attach the old volumes by"
  err "      pinning the old project name (it also holds that install's Postgres password):"
  err "          COMPOSE_PROJECT_NAME=${LEGACY_PROJECT_NAME} make selfhost"
  err "      Do not run the development stack on that host afterwards - it would fight over"
  err "      the same volumes again, which is exactly what the rename fixes."
  err ""
}

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
  # Forwarded so an operator (or CI) can pin the first password instead of taking the
  # generated one; bootstrap_admin still enforces the 8-128 policy. Never logged.
  [[ -n "${ADMIN_PASSWORD:-}" ]] && args+=(-e "ADMIN_PASSWORD=${ADMIN_PASSWORD}")
  args+=(api python -m app.scripts.bootstrap_admin)

  log ""
  log "Creating the first admin account..."
  compose "${args[@]}"
}

main() {
  generate_env
  warn_when_public_without_tls
  if [[ "$ENV_ONLY" -eq 1 ]]; then
    log "--env-only: skipping Docker. Review $ENV_FILE, then run without --env-only to boot."
    return 0
  fi

  require_docker
  warn_about_legacy_volumes

  log "Building and starting the stack ($COMPOSE_DISPLAY)..."
  compose up -d --build

  wait_for_api
  bootstrap_admin

  cat <<EOF

------------------------------------------------------------------
Third Brain is running.

  Web:  ${WEB_URL}

All data stays in YOUR Postgres, Redis and uploads volume - it is
never sent anywhere. The offline stub model means zero outbound calls
until you configure your own LLM provider.

Next steps:
  - Sign in with the admin credentials printed above and connect a model.
  - Wire up an MCP client:  npx third-brain-mcp connect
  - Stop the stack:         make selfhost-down
------------------------------------------------------------------
EOF
}

main
