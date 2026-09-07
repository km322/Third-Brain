# syntax=docker/dockerfile:1

# ============================================================================
# Third Brain - web (Next.js 15) production image.
#
# Four stages:
#   deps    -> reproducible install from the lockfile
#   dev     -> hot-reloading dev server (docker-compose dev target)
#   builder -> `next build` producing standalone output (see next.config.mjs)
#   runner  -> minimal runtime that runs `node server.js` as a non-root user
#
# The standalone build traces exactly the node_modules it needs and emits a
# self-contained server bundle, so the final image ships neither the full
# dependency tree nor the source.
# ============================================================================

# ---- deps: install node_modules from the lockfile ----
FROM node:22-alpine AS deps
WORKDIR /app
# libc6-compat helps some native deps run on Alpine's musl.
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* ./
# Use the lockfile when present for reproducible installs; fall back otherwise.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ---- dev: hot-reloading dev server with the FULL dependency tree ----
# docker-compose (dev) targets this stage. The production `runner` stage ships only the
# standalone trace (no `next` CLI, no devDependencies), so running `npm run dev` against it
# fails with `next: not found`; this stage keeps the complete node_modules from `deps`.
FROM node:22-alpine AS dev
WORKDIR /app
RUN apk add --no-cache libc6-compat
ENV NODE_ENV=development
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---- builder: compile the app ----
FROM node:22-alpine AS builder
WORKDIR /app

# NEXT_PUBLIC_* values are inlined into the client bundle at build time, so the
# API URL must be supplied here (not just at runtime).
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
# Public site origin, used for the canonical/OpenGraph URLs. Read at build time like the
# API URL, so a self-hosted deployment passes its own origin here.
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# next.config.mjs inlines this into the client bundle as NEXT_PUBLIC_GIT_COMMIT.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

RUN npm run build

# ---- runner: minimal runtime ----
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# The standalone server reads PORT/HOSTNAME; bind all interfaces in a container.
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Run as the unprivileged `node` user that ships with the base image.
# The standalone bundle (server.js + traced node_modules), the static assets and
# the runtime API URL are all this image needs.
# `next build` does not fold public/ into the standalone output, so the files
# served from the site root (the demo video and its poster) are copied in
# explicitly - without this they 404 in production. Kept FIRST: it is by far the
# largest layer and the least volatile, so it must sit below the bundle layers
# that change on every commit, or every release re-materialises ~17 MB of media
# it already had (twice, once per target architecture).
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

# Build metadata. Kept last so the per-build values never invalidate the
# standalone bundle layers above.
ARG APP_VERSION=0.0.0-dev
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME}
LABEL org.opencontainers.image.title="Third Brain Web" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/km322/Third-Brain"

USER node

EXPOSE 3000

# Liveness: the marketing route ('/') must answer with a non-5xx status.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:'+(process.env.PORT||3000)+'/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
