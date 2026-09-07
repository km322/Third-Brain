# Third Brain - developer convenience targets
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Build and start the full stack for development, with live reload (db, redis, api, worker, web)
	$(COMPOSE) up --build

.PHONY: up-d
up-d: ## Start the stack detached
	$(COMPOSE) up --build -d

.PHONY: up-obs
up-obs: ## Start the stack + local Grafana/Tempo/Loki (LGTM) for traces/logs
	$(COMPOSE) --profile observability up --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: selfhost
selfhost: ## Run Third Brain for real (the supported way): hardened .env, production stack, first admin (ADMIN_EMAIL=you@example.com)
	./scripts/selfhost-init.sh

.PHONY: selfhost-down
selfhost-down: ## Stop the self-hosted production stack
	$(COMPOSE) -f docker-compose.prod.yml -f docker-compose.selfhost.yml down

.PHONY: logs
logs: ## Tail all logs
	$(COMPOSE) logs -f

.PHONY: migrate
migrate: ## Apply database migrations
	$(COMPOSE) exec api alembic upgrade head

REV ?= -1

.PHONY: db-downgrade
db-downgrade: ## Roll back migrations (break-glass; usage: make db-downgrade REV=-1|base); REV=-1 rolls back one revision, REV=base drops the whole schema
	$(COMPOSE) exec api alembic downgrade $(REV)

.PHONY: makemigration
makemigration: ## Autogenerate a migration (usage: make makemigration m="message")
	# Run as the host uid so the generated file under the bind-mounted alembic/versions is
	# writable on Linux (the image's non-root appuser cannot write host-owned files there).
	$(COMPOSE) exec -u $$(id -u):$$(id -g) api alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Seed a demo organization, user and knowledge base
	$(COMPOSE) exec api python -m app.scripts.seed

.PHONY: test
test: ## Run the backend unit tier locally (integration/e2e self-skip without infra)
	cd apps/api && python -m pytest -q

.PHONY: test-integration
test-integration: ## Run the backend integration tier vs REAL Postgres+pgvector+Redis (docker)
	$(COMPOSE) -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from tests; \
		status=$$?; $(COMPOSE) -f docker-compose.test.yml down -v >/dev/null 2>&1; exit $$status

.PHONY: test-e2e
test-e2e: ## Boot the full stack and run the Playwright end-to-end suite
	# Run in an ISOLATED compose project so the closing `down -v` tears down only the e2e
	# stack's volumes - never the developer's `make up` Postgres/Redis/uploads data.
	$(COMPOSE) -p third-brain-e2e up -d --build
	# The dev compose stack has no migrate one-shot and the app never auto-creates the schema,
	# so migrate the freshly-started (empty, because of `down -v`) DB before the browser
	# journeys write to it. Retry while the API container finishes booting.
	@for i in $$(seq 1 30); do \
		$(COMPOSE) -p third-brain-e2e exec -T api alembic upgrade head && break || sleep 2; \
	done
	cd apps/web && npm run test:e2e; \
		status=$$?; $(COMPOSE) -p third-brain-e2e down -v >/dev/null 2>&1; exit $$status

SCALE ?= 1000000

.PHONY: load-seed
load-seed: ## Seed a synthetic load-test corpus of SCALE chunks (default 1000000) into the configured database
	cd apps/api && python -m loadtests.seed_corpus --chunks $(SCALE)

.PHONY: load-test
load-test: ## Run the locust load test with pass/fail thresholds (LOAD_USERS=50 LOAD_SPAWN_RATE=10 LOAD_DURATION=3m LOAD_MAX_FAIL_PCT=1.0 LOAD_P95_SEARCH_MS=750 LOAD_P95_CHAT_MS=1500 API_BASE=http://localhost:8000)
	cd apps/api && python -m loadtests.run_load_test

.PHONY: benchmark
benchmark: ## Measure retrieval, answer and permission-correctness quality on the golden dataset (reads the same DATABASE_URL/REDIS_URL as the app)
	cd apps/api && python -m benchmarks.run_benchmark

.PHONY: fmt
fmt: ## Format backend (ruff) and frontend (prettier)
	# Run as the host uid so ruff can rewrite the bind-mounted source on Linux (see makemigration).
	$(COMPOSE) exec -u $$(id -u):$$(id -g) api ruff format app tests
	cd apps/web && npm run format

.PHONY: lint
lint: ## Lint backend, frontend and the MCP CLI (+ its tests)
	$(COMPOSE) exec api ruff check app tests
	cd apps/web && npm run lint && npm run format:check
	cd packages/mcp-cli && npm run lint && npm test

.PHONY: shell
shell: ## Open a shell in the api container
	$(COMPOSE) exec api bash

.PHONY: version
version: ## Set the product version everywhere (usage: make version VERSION=1.2.0)
	./scripts/bump_version.sh $(VERSION)

.PHONY: release
release: ## Cut a release: bump + changelog + commit + tag; prints push commands (usage: make release VERSION=1.2.0)
	./scripts/release.sh $(VERSION)

.PHONY: rollback
rollback: ## Roll prod compose back to a prior image tag (usage: make rollback IMAGE_TAG=v1.0.0)
	./scripts/rollback.sh $(IMAGE_TAG)
