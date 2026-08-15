.PHONY: help infra-up infra-down up down build test test-coverage test-api-flows test-api-flows-collect lint deploy-dev teardown-dev seed wait-for-db security-scan test-report build-report testdata-validate testdata-clean testdata-setup-schema batch-usage-rollup batch-usage-rollup-seed seed-legacy seed-legacy-validate dev-backend dev-web dev-admin dev-android dev-electron dast-list dast-scan dast-verify dast-baseline dast-zap procs-validate procs-up procs-down procs-record procs-list procs-parity procs-rules-gate insurance-up insurance-down insurance-test legacy-etl-list legacy-etl-run legacy-etl-gen-data legacy-sftp-up legacy-sftp-down oracle-billing-up oracle-billing-down oracle-billing-seed oracle-record oracle-parity tp-smoke dbx-init dbx-apply dbx-destroy dbx-inventory dbx-verify-teardown dbx-upload dbx-deploy-notebooks dbx-run dbx-recon aws-tp-plan aws-tp-apply aws-tp-run aws-tp-verify aws-tp-destroy aws-tp-scan

SHELL := /bin/bash

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

PROCS_COMPOSE = docker compose -f docker-compose.procs.yml -p otterworks-procs-$(NS)
PROCS_UV = uv run --with psycopg[binary]==3.2.9 --with pyyaml==6.0.2
PROCS_PORT_OFFSET = $(shell if command -v python3 >/dev/null 2>&1 && test -n "$(NS)"; then python3 -c "import zlib; print(zlib.crc32('$(NS)'.encode()) % 1000)"; fi)
PROCS_DB_PORT = $(shell test -n "$(PROCS_PORT_OFFSET)" && python3 -c "print(55432 + $(PROCS_PORT_OFFSET))")
PROCS_APP_PORT = $(shell test -n "$(PROCS_PORT_OFFSET)" && python3 -c "print(8096 + $(PROCS_PORT_OFFSET))")
PROCS_TARGET_DB_PORT = $(shell test -n "$(PROCS_PORT_OFFSET)" && python3 -c "print(56432 + $(PROCS_PORT_OFFSET))")
PROCS_TARGET_PORT = $(shell test -n "$(PROCS_PORT_OFFSET)" && python3 -c "print(12096 + $(PROCS_PORT_OFFSET))")
PROCS_ENV = NS=$(NS) PROCS_DB_PORT=$(PROCS_DB_PORT) PROCS_APP_PORT=$(PROCS_APP_PORT) PROCS_TARGET_DB_PORT=$(PROCS_TARGET_DB_PORT) PROCS_TARGET_PORT=$(PROCS_TARGET_PORT)

procs-validate:
	@test -n "$(NS)" || (echo "NS is required, e.g. make procs-up NS=dev" >&2; exit 2)
	@command -v python3 >/dev/null 2>&1 || (echo "python3 is required for namespace port derivation" >&2; exit 2)
	@test -n "$(PROCS_PORT_OFFSET)" || (echo "could not derive namespace port offset" >&2; exit 2)

procs-up: procs-validate ## Start the legacy billing stored-procedure stack (NS=<namespace>)
	$(PROCS_ENV) $(PROCS_COMPOSE) up -d --build --wait

procs-down: procs-validate ## Stop the legacy billing stored-procedure stack (NS=<namespace>)
	$(PROCS_ENV) $(PROCS_COMPOSE) down -v

procs-record: procs-validate ## Record legacy billing transcripts (NS=<namespace>, MODULE and OUTPUT_DIR optional)
	$(PROCS_ENV) DB_NAME=billing_$(NS) DB_PORT=$(PROCS_DB_PORT) $(PROCS_UV) procs/harness/record.py $(if $(MODULE),--module $(MODULE),) $(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),) $(if $(ALLOW_RERECORD),--allow-rerecord,) $(if $(RERECORD_REASON),--rerecord-reason $(RERECORD_REASON),)

procs-list: ## List stored-procedure modules and scenarios
	$(PROCS_UV) procs/harness/list.py $(if $(MODULE),--module $(MODULE),)

procs-parity: procs-validate ## Replay extracted billing scenarios (NS=<namespace>, MODULE and SCENARIO optional)
	$(PROCS_ENV) BILLING_SVC_URL=$${BILLING_SVC_URL:-http://localhost:$(PROCS_TARGET_PORT)} $(PROCS_UV) procs/harness/replay.py $(if $(MODULE),--module $(MODULE),) $(if $(SCENARIO),--scenario $(SCENARIO),)

procs-rules-gate: ## Validate the approved HITL rule ledger (MODULE=<module> or ALL=1)
	@test -n "$(MODULE)$(ALL)" || (echo "MODULE or ALL=1 is required" >&2; exit 2)
	uv run --with pyyaml==6.0.2 procs/harness/rules_gate.py $(if $(ALL),--all,--module $(MODULE))

# --- Industry Solutions: insurance Commission Pay (Oracle) ---

INSURANCE_COMPOSE = docker compose -f docker-compose.insurance.yml -p otterworks-insurance-$(NS)
INSURANCE_DB_PORT = $(shell test -n "$(PROCS_PORT_OFFSET)" && python3 -c "print(51521 + $(PROCS_PORT_OFFSET))")
INSURANCE_ENV = NS=$(NS) INSURANCE_DB_PORT=$(INSURANCE_DB_PORT)
INSURANCE_SQLPLUS = docker exec -i otterworks-insurance-$(NS)-insurance-oracle-1 sqlplus -s

insurance-up: procs-validate ## Start the Oracle insurance Commission Pay fixture (NS=<namespace>)
	$(INSURANCE_ENV) $(INSURANCE_COMPOSE) up -d --wait --wait-timeout 900

insurance-down: procs-validate ## Stop the Oracle insurance fixture and drop its data (NS=<namespace>)
	$(INSURANCE_ENV) $(INSURANCE_COMPOSE) down -v

insurance-test: procs-validate ## Run the Commission Pay OLTP + OLAP test suites (NS=<namespace>)
	$(INSURANCE_SQLPLUS) commission_pay/commission_pay@localhost:1521/FREEPDB1 @/opt/oracle/scripts/insurance/tests/run_tests.sql
	$(INSURANCE_SQLPLUS) commission_dw/commission_dw@localhost:1521/FREEPDB1 @/opt/oracle/scripts/insurance/tests/run_olap_tests.sql

# --- Legacy Billing: Oracle billing estate (before-state for modernization demos) ---

ORACLE_BILLING_COMPOSE = docker compose -f docker-compose.oracle-billing.yml -p otterworks-oracle-billing
ORACLE_BILLING_DB_PORT ?= 52521
ORACLE_BILLING_UV = uv run --with oracledb==2.5.1

oracle-billing-up: ## Start the Oracle billing estate fixture (localhost:$(ORACLE_BILLING_DB_PORT), PDB FREEPDB1, schema OW_BILLING)
	ORACLE_BILLING_DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_BILLING_COMPOSE) up -d --wait --wait-timeout 1200

oracle-billing-down: ## Stop the Oracle billing estate fixture and drop its data
	ORACLE_BILLING_DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_BILLING_COMPOSE) down -v

oracle-billing-seed: ## Seed the Oracle billing estate (NS=<namespace>, SCALE=demo|full; writes testdata/legacy/manifests/<NS>.json)
ifndef NS
	$(error NS is required, e.g. make oracle-billing-seed NS=dev)
endif
	$(call validate_ns)
	DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_BILLING_UV) testdata/legacy/oracle_billing_seed.py --ns $(NS) --scale $(or $(SCALE),demo)

ORACLE_PARITY_UV = uv run --with oracledb==2.5.1 --with pyyaml==6.0.2
ORACLE_PARITY_RUN = procs/reports/oracle-parity-run

oracle-record: ## Record immutable Oracle billing transcripts (requires oracle-billing-up; MODULE optional)
	DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_PARITY_UV) procs/harness/oracle_record.py $(if $(MODULE),--module $(MODULE),) $(if $(ALLOW_RERECORD),--allow-rerecord,)

oracle-parity: procs-validate ## Oracle vs Postgres parity run (NS=<namespace>; requires procs-up and oracle-billing-up)
	$(call validate_ns)
	DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_BILLING_UV) testdata/legacy/oracle_billing_seed.py --ns $(NS) --scale $(or $(SCALE),demo)
	rm -rf $(ORACLE_PARITY_RUN)/$(NS)
	$(PROCS_ENV) DB_NAME=billing_$(NS) DB_PORT=$(PROCS_DB_PORT) $(PROCS_UV) procs/harness/record.py --output-dir $(ORACLE_PARITY_RUN)/$(NS)/postgres
	DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_PARITY_UV) procs/harness/oracle_record.py --output-dir $(ORACLE_PARITY_RUN)/$(NS)/oracle
	uv run procs/harness/oracle_parity.py --postgres-dir $(ORACLE_PARITY_RUN)/$(NS)/postgres --oracle-dir $(ORACLE_PARITY_RUN)/$(NS)/oracle --namespace $(NS)

# --- Local Development ---

infra-up: ## Start local infrastructure (Postgres, Redis, LocalStack, MeiliSearch)
	docker compose -f docker-compose.infra.yml up -d

infra-down: ## Stop local infrastructure
	docker compose -f docker-compose.infra.yml down

up: ## Start all services (add seed=1 to seed after start)
	docker compose -f docker-compose.infra.yml -f docker-compose.yml up -d --build
 ifdef seed
	@$(MAKE) --no-print-directory wait-for-db seed
 endif

down: ## Stop all application services
	docker compose -f docker-compose.infra.yml -f docker-compose.yml down

build: ## Build all service images
	docker compose -f docker-compose.infra.yml -f docker-compose.yml build

seed: ## Seed development data (services must be running)
	uv run scripts/seed.py

wait-for-db: ## Wait for Postgres to accept connections
	@echo "Waiting for Postgres to be healthy..."
	@for i in $$(seq 1 30); do \
		docker exec otterworks-postgres pg_isready -q 2>/dev/null && exit 0; \
		sleep 1; \
	done; echo "Timed out waiting for Postgres" && exit 1

logs: ## Tail logs for all services
	docker compose -f docker-compose.infra.yml -f docker-compose.yml logs -f

# --- App Dev Targets ---
# One-command startup for each frontend app, running from source against the
# Dockerized backend. The compose web-app/admin-dashboard containers are stopped
# where they would clash with the dev servers on ports 3000/4200.

COMPOSE := docker compose -f docker-compose.infra.yml -f docker-compose.yml
# Collab websocket as exposed by docker-compose (host port 8084); source builds
# otherwise default to :8085, which only matches the k8s dev environment.
COLLAB_WS_URL := ws://localhost:8084

dev-backend: ## Start the Dockerized backend (all services except the frontend containers)
	$(COMPOSE) up -d $$($(COMPOSE) config --services | grep -vE '^(web-app|admin-dashboard)$$')
	@echo "Backend up - API gateway on http://localhost:8080 (fresh DB? run: make seed)"

dev-web: dev-backend ## Start the client-app web dev server (HMR) on :3000
	@$(COMPOSE) stop web-app 2>/dev/null || true
	cd frontend/client-app && { [ -d node_modules ] || npm ci; } && \
		VITE_COLLAB_WS_URL=$(COLLAB_WS_URL) npm run dev

dev-admin: dev-backend ## Start the admin dashboard dev server (HMR) on :4200
	@$(COMPOSE) stop admin-dashboard 2>/dev/null || true
	cd frontend/admin-dashboard && { [ -d node_modules ] || npm ci; } && npm start

dev-android: dev-backend ## Build the web bundle and run the Android app on an emulator/device
	cd frontend/client-app && { [ -d node_modules ] || npm ci; } && \
		VITE_COLLAB_WS_URL=ws://10.0.2.2:8084 npm run build && \
		npx cap sync android && npx cap run android

dev-electron: dev-backend ## Build the web bundle and launch the Electron desktop app
	cd frontend/client-app && { [ -d node_modules ] || npm ci; } && \
		VITE_COLLAB_WS_URL=$(COLLAB_WS_URL) npm run build
	cd frontend/client-app/desktop && { [ -d node_modules ] || npm ci; } && npm start

# --- Per-Service Builds ---

build-gateway: ## Build API Gateway
	cd services/api-gateway && go build -o bin/server ./cmd/server

build-auth: ## Build Auth Service
	cd services/auth-service && ./gradlew bootJar

build-file: ## Build File Service
	cd services/file-service && cargo build --release

build-document: ## Build Document Service
	cd services/document-service && pip install -e .

build-collab: ## Build Collaboration Service
	cd services/collab-service && npm run build

build-notification: ## Build Notification Service
	cd services/notification-service && ./gradlew build

build-search: ## Build Search Service
	cd services/search-service && pip install -e .

build-analytics: ## Build Analytics Service
	cd services/analytics-service && sbt compile

build-admin: ## Build Admin Service
	cd services/admin-service && bundle install

build-audit: ## Build Audit Service
	cd services/audit-service && dotnet build

build-web: ## Build Web Frontend
	cd frontend/web-app && npm run build

build-admin-dash: ## Build Admin Dashboard
	cd frontend/admin-dashboard && npm run build

# --- Testing ---

test: ## Run tests for all services
	@echo "=== API Gateway (Go) ===" && cd services/api-gateway && go test ./...
	@echo "=== Auth Service (Java) ===" && cd services/auth-service && ./gradlew test
	@echo "=== File Service (Rust) ===" && cd services/file-service && cargo test
	@echo "=== Document Service (Python) ===" && cd services/document-service && pytest
	@echo "=== Collab Service (Node.js) ===" && cd services/collab-service && npm test
	@echo "=== Notification Service (Kotlin) ===" && cd services/notification-service && ./gradlew test
	@echo "=== Search Service (Python) ===" && cd services/search-service && pytest
	@echo "=== Analytics Service (Scala) ===" && cd services/analytics-service && sbt test
	@echo "=== Admin Service (Ruby) ===" && cd services/admin-service && bundle exec rspec
	@echo "=== Audit Service (C#) ===" && cd services/audit-service && dotnet test
	@echo "=== Web Frontend ===" && cd frontend/web-app && npm test
	@echo "=== Admin Dashboard ===" && cd frontend/admin-dashboard && npm test

test-coverage: ## Run tests with coverage for all services
	@echo "=== Document Service ===" && cd services/document-service && pytest --cov=app --cov-report=term-missing || true
	@echo "=== Search Service ===" && cd services/search-service && pytest --cov=app --cov-report=term-missing || true
	@echo "=== Collab Service ===" && cd services/collab-service && npm test -- --coverage || true
	@echo "=== API Gateway ===" && cd services/api-gateway && go test -cover ./... || true
	@echo "=== Admin Service ===" && cd services/admin-service && bundle exec rspec --format documentation || true
	@echo "=== Auth Service ===" && cd services/auth-service && ./gradlew test jacocoTestReport || true
	@echo "=== File Service ===" && cd services/file-service && cargo test 2>&1 | tail -5 || true

tp-smoke: ## Golden-path smoke gate for tech-partnerships (mirrors .github/workflows/tp-golden-smoke.yml)
	@echo "=== Estate Make targets parse (dry-run) ==="
	@$(MAKE) -n oracle-billing-up > /dev/null
	@$(MAKE) -n seed-legacy NS=ci > /dev/null
	@$(MAKE) -n legacy-etl-list > /dev/null
	@$(MAKE) -n procs-parity NS=ci > /dev/null
	@echo "=== Oracle billing compose config lint ==="
	docker compose -f docker-compose.oracle-billing.yml config > /dev/null
	@echo "=== Golden 'make -n test' still parses ==="
	@$(MAKE) -n test > /dev/null
	@echo "=== API Gateway (Go) ==="
	cd services/api-gateway && go vet ./... && go test ./... && go build -o /dev/null ./cmd/server
	@echo "=== Collab Service (Node.js) ==="
	cd services/collab-service && { [ -d node_modules ] || npm ci; } && npm run lint && npm test && npm run build
	@echo "=== Search Service (Python) ==="
	cd services/search-service && uv run --no-project --with-requirements requirements-dev.txt python -m pytest
	@echo "tp-smoke: all checks passed"

test-api-flows: ## Run black-box API flow tests against the local API gateway
	UV_PROJECT_ENVIRONMENT=.venv uv run python -m pytest tests/api

test-api-flows-collect: ## Collect black-box API flow tests without running them
	UV_PROJECT_ENVIRONMENT=.venv uv run python -m pytest tests/api --collect-only -q

lint: ## Lint all services
	@echo "=== API Gateway ===" && cd services/api-gateway && golangci-lint run
	@echo "=== Auth Service ===" && cd services/auth-service && ./gradlew spotlessCheck
	@echo "=== File Service ===" && cd services/file-service && cargo clippy -- -D warnings
	@echo "=== Document Service ===" && cd services/document-service && ruff check .
	@echo "=== Collab Service ===" && cd services/collab-service && npm run lint
	@echo "=== Search Service ===" && cd services/search-service && ruff check .
	@echo "=== Web Frontend ===" && cd frontend/web-app && npm run lint
	@echo "=== Admin Dashboard ===" && cd frontend/admin-dashboard && npm run lint

# --- Synthetic Test Data ---

# Guard: NS must be alphanumeric/underscore only (prevents SQL injection)
define validate_ns
$(if $(filter ok,$(shell echo '$(NS)' | grep -qE '^[A-Za-z0-9_]+$$' && echo ok)),,$(error NS must contain only letters, digits, and underscores))
endef

testdata-validate: ## Validate generated test data (NS=<namespace>, CRITERIA=<file>)
ifndef NS
	$(error NS is required, e.g. make testdata-validate NS=dev)
endif
	$(call validate_ns)
	uv run testdata/harness/validate.py --ns $(NS) $(if $(CRITERIA),--criteria $(CRITERIA),)

testdata-clean: ## Drop a test-data namespace schema (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make testdata-clean NS=dev)
endif
	$(call validate_ns)
	@echo "Dropping schema otterworks_$(NS)..."
	PGPASSWORD=$${DB_PASSWORD:-otterworks_dev} psql \
		-h $${DB_HOST:-localhost} -p $${DB_PORT:-5432} \
		-U $${DB_USER:-otterworks} -d $${DB_NAME:-otterworks} \
		-c "DROP SCHEMA IF EXISTS otterworks_$(NS) CASCADE;"
	@echo "Done."

testdata-setup-schema: ## Create a namespaced schema (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make testdata-setup-schema NS=dev)
endif
	$(call validate_ns)
	@echo "Creating schema otterworks_$(NS)..."
	PGPASSWORD=$${DB_PASSWORD:-otterworks_dev} psql \
		-h $${DB_HOST:-localhost} -p $${DB_PORT:-5432} \
		-U $${DB_USER:-otterworks} -d $${DB_NAME:-otterworks} \
		-f testdata/harness/create_schema.sql \
		-v ns=$(NS)
	@echo "Done."

# --- Legacy Seed Data (tech-partnerships demo estates) ---

comma := ,
SEED_LEGACY_SCALE = $(if $(SCALE),$(SCALE),demo)
SEED_LEGACY_TARGETS = $(if $(TARGETS),$(TARGETS),postgres$(comma)dynamodb$(comma)s3)

seed-legacy: ## Seed legacy demo data (NS=<ns>, SCALE=demo|full, TARGETS=postgres,dynamodb,s3)
ifndef NS
	$(error NS is required, e.g. make seed-legacy NS=dev)
endif
	$(call validate_ns)
	uv run testdata/legacy/seed.py --ns $(NS) --scale $(SEED_LEGACY_SCALE) --targets "$(SEED_LEGACY_TARGETS)"

seed-legacy-validate: ## Re-derive counts/checksums from the stores and assert they match the manifest (NS=<ns>, TARGETS=...)
ifndef NS
	$(error NS is required, e.g. make seed-legacy-validate NS=dev)
endif
	$(call validate_ns)
	uv run testdata/legacy/validate.py --ns $(NS) --targets "$(SEED_LEGACY_TARGETS)"

# --- Infrastructure ---

tf-init: ## Initialize Terraform
	cd infrastructure/terraform && terraform init

tf-plan: ## Plan Terraform changes
	cd infrastructure/terraform && terraform plan -var-file=environments/dev.tfvars

tf-apply: ## Apply Terraform changes
	cd infrastructure/terraform && terraform apply -var-file=environments/dev.tfvars -auto-approve

tf-destroy: ## Destroy Terraform resources
	cd infrastructure/terraform && terraform destroy -var-file=environments/dev.tfvars -auto-approve

deploy-dev: ## Deploy all services to dev EKS
	./scripts/deploy-dev.sh

teardown-dev: ## Tear down dev environment
	./scripts/teardown-dev.sh

# --- Security ---

security-scan: ## Run security scans across all services
	@echo "=== Trivy Filesystem Scan ==="
	trivy fs --config security/scanning/trivy-config.yaml . || true
	@echo ""
	@echo "=== Node.js Audit (collab-service) ==="
	cd services/collab-service && npm audit 2>/dev/null || true
	@echo ""
	@echo "=== Python Audit (search-service) ==="
	cd services/search-service && pip-audit -r requirements.txt 2>/dev/null || true
	@echo ""
	@echo "=== Ruby Audit (admin-service) ==="
	cd services/admin-service && bundle-audit check 2>/dev/null || true
	@echo ""
	@echo "=== Report Service (skipped - legacy) ==="

# --- Dynamic Application Security Testing (DAST) ---
#
# DAST attacks the *running* application through the API gateway. TARGET can be
# the local stack (default), a tenant URL, or a preview environment.

DAST_TARGET ?= http://localhost:8080
DAST := uv run --with httpx --with tabulate security/dast/harness/dast_scan.py

dast-list: ## List the registered DAST attack probes
	$(DAST) --list

dast-scan: ## Run the DAST suite against a running app (DAST_TARGET=<url>), gated by the baseline
	$(DAST) --target $(DAST_TARGET) $(if $(FAIL_ON),--fail-on $(FAIL_ON),)

dast-verify: ## Prove one finding is remediated (FINDING=<id> DAST_TARGET=<url>); baseline is ignored
ifndef FINDING
	$(error FINDING is required, e.g. make dast-verify FINDING=DAST-MISSING-SECURITY-HEADERS)
endif
	$(DAST) --target $(DAST_TARGET) --only $(FINDING) --no-baseline --fail-on info

dast-baseline: ## Record current findings as accepted (REASON="...")
	$(DAST) --target $(DAST_TARGET) --reason "$${REASON:-recorded by make dast-baseline}" --update-baseline

dast-zap: ## Run the OWASP ZAP baseline sweep and merge it into the DAST report
	@mkdir -p security/dast/reports
# ZAP writes its report *and* its generated automation plan into its working
# directory, and the image runs as uid 1000 while a CI runner is 1001 (running the
# container as the host uid instead is not an option: ZAP needs /home/zap, which
# only exists for uid 1000). Rather than open up a host directory, the working
# directory is a throwaway docker volume chowned to the image's uid from inside a
# container, so nothing on the host is ever writable by another local user. The
# rule file goes in read-only, and the report is read back out through the volume.
# The stale report is removed first, so "a report exists" can only mean this run
# produced one.
	@rm -f security/dast/reports/zap-report.json
	@set -e; \
	vol="dast-zap-$$$$"; \
	trap 'docker volume rm -f "$$vol" >/dev/null 2>&1' EXIT INT TERM; \
	docker volume create "$$vol" >/dev/null; \
	docker run --rm --user 0 -v "$$vol:/zap/wrk" \
		ghcr.io/zaproxy/zaproxy:stable chown 1000:1000 /zap/wrk; \
	docker run --rm --network host \
		-v "$$vol:/zap/wrk" \
		-v "$(CURDIR)/security/dast/zap/zap-baseline.conf:/zap/wrk/zap-baseline.conf:ro" \
		ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
		-t $(DAST_TARGET) -c zap-baseline.conf -J zap-report.json -I || true; \
	docker run --rm --user 0 -v "$$vol:/zap/wrk" ghcr.io/zaproxy/zaproxy:stable \
		sh -c 'cat /zap/wrk/zap-report.json 2>/dev/null' \
		> security/dast/reports/zap-report.json || true; \
	[ -s security/dast/reports/zap-report.json ] || rm -f security/dast/reports/zap-report.json
# A ZAP failure must not cost the probe suite: the sweep still reports, without it.
	@if [ -f security/dast/reports/zap-report.json ]; then \
		$(DAST) --target $(DAST_TARGET) --zap-report security/dast/reports/zap-report.json; \
	else \
		echo "::warning::ZAP produced no report (see the log above); the passive sweep is"\
		     "missing from this run. Running the probe suite on its own."; \
		$(DAST) --target $(DAST_TARGET); \
	fi

test-report: ## Run report-service tests only
	cd services/report-service && mvn test

build-report: ## Build report-service
	cd services/report-service && mvn package -DskipTests

# --- Batch jobs (legacy scheduled processing) ---

batch-usage-rollup: ## Run the nightly usage-rollup batch job locally (OUT=<path> optional)
	ROLLUP_OUTPUT=$${OUT:-rollup-output.json} scripts/run-usage-rollup.sh

batch-usage-rollup-seed: ## Regenerate the deterministic usage-rollup seed events
	cd services/analytics-service && python3 scripts/generate_seed_events.py

# --- Legacy polyglot batch estate (etl/legacy-extra/, tech-partnerships demo) ---

legacy-etl-list: ## List the legacy polyglot batch jobs (etl/legacy-extra/)
	@echo "Legacy batch jobs (run with: make legacy-etl-run JOB=<name>):"
	@echo "  sftp_ingest_poll          ksh   poll SFTP drop dir, stage CUSTBILL files"
	@echo "  parse_custbill_fixedwidth bash  sed/awk/cut fixed-width parser -> .psv"
	@echo "  finance_excel_report      perl  CSV-renamed-to-.xls finance report + stub sendmail"
	@echo "  run_all                   bash  full chain, sleep-based 'dependency management'"
	@echo "Sample input: make legacy-etl-gen-data [NS=dev]"

legacy-etl-gen-data: ## Generate deterministic CUSTBILL sample input (NS=<ns>)
	perl etl/legacy-extra/tools/gen_sample_data.pl $${NS:-dev}

legacy-etl-run: ## Run one legacy batch job (JOB=<name>, see legacy-etl-list)
	@test -n "$(JOB)" || { echo "usage: make legacy-etl-run JOB=<name>"; exit 1; }
	@case "$(JOB)" in \
	  sftp_ingest_poll)           command -v ksh >/dev/null || { echo "ksh required (sudo apt-get install -y ksh)"; exit 1; }; etl/legacy-extra/jobs/sftp_ingest_poll.ksh ;; \
	  parse_custbill_fixedwidth)  etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh ;; \
	  finance_excel_report)       perl etl/legacy-extra/jobs/finance_excel_report.pl ;; \
	  run_all)                    command -v ksh >/dev/null || { echo "ksh required (sudo apt-get install -y ksh)"; exit 1; }; RUN_ALL_SLEEP=$${RUN_ALL_SLEEP:-0} etl/legacy-extra/run_all.sh ;; \
	  *) echo "unknown JOB '$(JOB)' (see: make legacy-etl-list)"; exit 1 ;; \
	esac

legacy-sftp-up: ## Start the optional localhost-only SFTP drop fixture
	mkdir -p $${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}/sftp-drop/upload
	LEGACY_SFTP_UID=$$(id -u) docker compose -f etl/legacy-extra/docker-compose.sftp.yml up -d

legacy-sftp-down: ## Stop the SFTP drop fixture
	docker compose -f etl/legacy-extra/docker-compose.sftp.yml down
# --- Databricks lakehouse (tech-partnerships migration target) ---
# Every object is ow_tp-prefixed and Terraform-managed: the demo workspace is
# shared, so the prefix is both the isolation boundary and the teardown filter.
# No target here creates compute; jobs and recon run on the existing serverless
# SQL warehouse.

DBX_DIR := infrastructure/terraform-databricks
DBX_ENV := DATABRICKS_HOST="$${DATABRICKS_HOST:-$$DATABRICKS_DEMO_HOST}" DATABRICKS_TOKEN="$${DATABRICKS_TOKEN:-$$DATABRICKS_DEMO_TOKEN}"
DBX = $(DBX_ENV) python3 scripts/tp_databricks/dbx.py
LEGACY_ROOT = $${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}

dbx-init: ## Initialize the Databricks Terraform stack
	cd $(DBX_DIR) && $(DBX_ENV) terraform init -input=false

dbx-apply: ## Create/update the ow_tp lakehouse estate (catalog, layers, volume, secret scope, jobs)
	cd $(DBX_DIR) && $(DBX_ENV) terraform apply -auto-approve

dbx-destroy: ## Tear the ow_tp estate down (verify afterwards with dbx-verify-teardown)
	cd $(DBX_DIR) && $(DBX_ENV) terraform destroy -auto-approve

dbx-inventory: ## List every ow_tp-prefixed object in the shared workspace
	@$(DBX) inventory

dbx-verify-teardown: ## Fail if any ow_tp-prefixed object survived teardown
	@$(DBX) teardown-check

dbx-upload: ## Upload the legacy CUSTBILL drops into the landing volume (NS=<ns>)
ifndef NS
	$(error NS is required, e.g. make dbx-upload NS=demo)
endif
	$(call validate_ns)
	@set -e; shopt -s nullglob nocaseglob; files=( "$(LEGACY_ROOT)"/sftp-drop/upload/CUSTBILL_$(NS)_*.dat ); \
	if (($${#files[@]} == 0)); then \
	  echo "dbx-upload: no .dat files found under $(LEGACY_ROOT)/sftp-drop/upload; nothing to upload"; \
	  exit 0; \
	fi; \
	for f in "$${files[@]}"; do \
	  $(DBX) upload "$$f" "$(NS)/custbill/$$(basename $$f)"; \
	done

dbx-deploy-notebooks: ## Import the converted pipeline notebooks into /Shared/ow_tp
	@set -e; shopt -s nullglob; files=( databricks/notebooks/*.py ); \
	if (($${#files[@]} == 0)); then \
	  echo "dbx-deploy-notebooks: no notebooks found under databricks/notebooks; nothing to deploy"; \
	  exit 0; \
	fi; \
	for f in "$${files[@]}"; do \
	  $(DBX) deploy-notebook "$$f" "$$(basename $$f .py)"; \
	done

dbx-run: ## Run a converted job and wait for it (JOB=<ow_tp_...>, NS=<ns>)
	@test -n "$(JOB)" || { echo "usage: make dbx-run JOB=ow_tp_<job> NS=<ns>"; exit 1; }
	@case "$(JOB)" in *[!A-Za-z0-9_-]*|'') echo "JOB must contain only letters, digits, underscores, and hyphens" >&2; exit 1;; esac
	$(if $(NS),$(call validate_ns),)
	@$(DBX) run-job "$(JOB)" ns=$${NS:-demo}

dbx-recon: ## Reconcile one converted unit against its legacy golden output (UNIT=<unit>, NS=<ns>)
	@test -n "$(UNIT)" || { echo "usage: make dbx-recon UNIT=<unit> NS=<ns>"; exit 1; }
	@case "$(UNIT)" in *[!A-Za-z0-9_-]*|'') echo "UNIT must contain only letters, digits, underscores, and hyphens" >&2; exit 1;; esac
	$(if $(NS),$(call validate_ns),)
	$(DBX_ENV) NS=$${NS:-demo} python3 scripts/tp_databricks/recon_$(UNIT).py

# --- AWS serverless CUSTBILL pipeline (infrastructure/terraform-tp-aws, tech-partnerships demo) ---

TP_AWS_DIR := infrastructure/terraform-tp-aws

aws-tp-plan: ## Plan the AWS serverless CUSTBILL stack
	terraform -chdir=$(TP_AWS_DIR) init -input=false
	terraform -chdir=$(TP_AWS_DIR) plan -input=false

aws-tp-apply: ## Apply the AWS serverless CUSTBILL stack (serverless/on-demand only)
	terraform -chdir=$(TP_AWS_DIR) init -input=false
	terraform -chdir=$(TP_AWS_DIR) apply -auto-approve -input=false

aws-tp-run: ## Drop the deterministic CUSTBILL sample files into landing/<ns>/ (NS=<ns>)
	scripts/aws-tp-run.sh $${NS:-demo}

aws-tp-verify: ## Recon serverless pipeline output against the legacy golden outputs (NS=<ns>)
	scripts/aws-tp-verify.sh $${NS:-demo} --wait $${WAIT:-180}

aws-tp-destroy: ## Destroy the AWS stack and verify zero Project=otterworks-tp resources remain
	scripts/aws-tp-teardown.sh

aws-tp-scan: ## Teardown verification only: scan for leftover ow-tp- / tagged resources
	scripts/aws-tp-teardown.sh --scan-only
