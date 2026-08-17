.PHONY: help infra-up infra-down up down build test test-coverage test-api-flows test-api-flows-collect lint deploy-dev teardown-dev seed wait-for-db security-scan test-report build-report testdata-validate testdata-clean testdata-setup-schema batch-usage-rollup batch-usage-rollup-seed seed-legacy seed-legacy-validate dev-backend dev-web dev-admin dev-android dev-electron dast-list dast-scan dast-verify dast-baseline dast-zap procs-validate procs-down procs-record procs-list procs-parity procs-rules-gate insurance-up insurance-down insurance-test legacy-etl-list legacy-etl-run legacy-etl-gen-data legacy-sftp-up legacy-sftp-down oracle-billing-up oracle-billing-down oracle-billing-seed oracle-record oracle-parity tp-smoke tp-run-branch tp-preflight tp-preflight-databricks tp-preflight-atlas tp-preflight-aws tp-validate-schemas tp-validate-contracts tp-validate-recon tp-fixture-land tp-fixture-verify tp-fixture-clean tp-atlas-teardown tp-mongo-fixture-up tp-mongo-fixture-down tp-mongo-customers tp-mongo-customers-recon tp-mongo-test tp-mongo-migrate-files tp-mongo-recon-files tp-mongo-invoices tp-mongo-invoices-recon tp-mongo-documents tp-mongo-documents-recon

SHELL := /bin/bash

define validate_ns
$(if $(filter ok,$(shell echo '$(NS)' | grep -qE '^[A-Za-z0-9_]+$$' && echo ok)),,$(error NS must contain only letters, digits, and underscores))
endef

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

tp-preflight: ## Check platform capabilities (PLATFORM=databricks|atlas|aws)
	@test "$(PLATFORM)" = databricks -o "$(PLATFORM)" = atlas -o "$(PLATFORM)" = aws || { echo "PLATFORM must be databricks, atlas, or aws" >&2; exit 2; }
	@case "$(PLATFORM)" in \
		databricks) $(MAKE) tp-preflight-databricks ;; \
		atlas) $(MAKE) tp-preflight-atlas ;; \
		aws) $(MAKE) tp-preflight-aws ;; \
	esac

tp-preflight-databricks: ## Check Databricks capability paths and emit a manifest
	scripts/tp-preflight-databricks.sh

tp-preflight-atlas: ## Check MongoDB Atlas capability paths and emit a manifest
ifneq ($(strip $(NS)),)
	$(call validate_ns)
endif
	@set -euo pipefail; \
	tf_dir="infrastructure/terraform/tp-mongodb"; \
	previous_workspace="$$(terraform -chdir=$$tf_dir workspace show 2>/dev/null || true)"; \
	restore_workspace() { \
		current_workspace="$$(terraform -chdir=$$tf_dir workspace show 2>/dev/null || true)"; \
		if [ -n "$$previous_workspace" ] && [ "$$current_workspace" != "$$previous_workspace" ]; then \
			terraform -chdir=$$tf_dir workspace select "$$previous_workspace" >/dev/null 2>&1 || true; \
		fi; \
	}; \
	trap restore_workspace EXIT; \
	if [ -n "$(NS)" ] && { [ -z "$${OW_TP_PREFLIGHT_MONGO_URI:-}" ] || [ -z "$${OW_TP_PREFLIGHT_DB:-}" ]; }; then \
		if ! terraform -chdir=$$tf_dir workspace select "$(NS)" >/dev/null 2>&1; then \
			echo "tp-preflight-atlas: failed to select Terraform workspace '$(NS)'" >&2; \
			exit 2; \
		fi; \
		if [ -z "$${OW_TP_PREFLIGHT_MONGO_URI:-}" ]; then \
			if ! probe_user="$$(terraform -chdir=$$tf_dir output -raw database_username 2>/dev/null)"; then \
				echo "tp-preflight-atlas: failed to read Terraform output database_username for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$$probe_user" ]; then \
				echo "tp-preflight-atlas: Terraform output database_username is empty for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if ! probe_password="$$(terraform -chdir=$$tf_dir output -raw database_password 2>/dev/null)"; then \
				echo "tp-preflight-atlas: failed to read Terraform output database_password for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$$probe_password" ]; then \
				echo "tp-preflight-atlas: Terraform output database_password is empty for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$${MONGODB_ATLAS_URI:-}" ]; then \
				echo "tp-preflight-atlas: MONGODB_ATLAS_URI is required to derive the namespace probe URI for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if ! probe_host="$$(MONGO_BASE_URI="$$MONGODB_ATLAS_URI" python3 -c 'import os; from urllib.parse import urlsplit; host = urlsplit(os.environ["MONGO_BASE_URI"]).hostname; import sys; sys.exit("cluster host missing") if not host else None; print(host)' 2>/dev/null)"; then \
				echo "tp-preflight-atlas: failed to parse a host from MONGODB_ATLAS_URI for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$$probe_host" ]; then \
				echo "tp-preflight-atlas: parsed host is empty in MONGODB_ATLAS_URI for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if ! derived_uri="$$(printf %s "$$probe_password" | MONGO_USER="$$probe_user" MONGO_HOST="$$probe_host" python3 -c 'import os, sys; from urllib.parse import quote; password = sys.stdin.read(); print("mongodb+srv://{}:{}@{}/?retryWrites=true&w=majority".format(quote(os.environ["MONGO_USER"], safe=""), quote(password, safe=""), os.environ["MONGO_HOST"]))' 2>/dev/null)"; then \
				echo "tp-preflight-atlas: failed to build the namespace probe URI for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$$derived_uri" ]; then \
				echo "tp-preflight-atlas: namespace probe URI build returned empty for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			export OW_TP_PREFLIGHT_MONGO_URI="$$derived_uri"; \
		fi; \
		if [ -z "$${OW_TP_PREFLIGHT_DB:-}" ]; then \
			if ! probe_database="$$(terraform -chdir=$$tf_dir output -raw database_name 2>/dev/null)"; then \
				echo "tp-preflight-atlas: failed to read Terraform output database_name for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			if [ -z "$$probe_database" ]; then \
				echo "tp-preflight-atlas: Terraform output database_name is empty for NS=$(NS)" >&2; \
				exit 2; \
			fi; \
			export OW_TP_PREFLIGHT_DB="$$probe_database"; \
		fi; \
	fi; \
	scripts/tp-preflight-atlas.sh

tp-preflight-aws: ## Check AWS capability paths and leftovers
	scripts/tp-preflight-aws.sh

tp-atlas-teardown: ## Drop one MongoDB namespace and destroy its scoped Atlas objects (NS=<ns>)
ifndef NS
	$(error NS is required, e.g. make tp-atlas-teardown NS=sidekicktest)
endif
	$(call validate_ns)
	./scripts/tp-atlas-teardown.sh "$(NS)"

tp-validate-schemas: ## Validate the contract/recon schemas themselves against their metaschema
	uv run --no-project --with check-jsonschema==0.38.0 check-jsonschema --check-metaschema docs/tech-partnerships/contracts/schema/*.schema.json

tp-validate-contracts: ## Validate JSON contracts (intentionally fails until prose contracts are migrated)
	uv run --no-project --with jsonschema==4.25.1 --with rfc3339-validator==0.1.4 python3 scripts/tp_validate.py contracts

tp-validate-recon: ## Validate recon reports (FILE=<path>; no reports is valid, other JSON is informational)
	uv run --no-project --with jsonschema==4.25.1 --with rfc3339-validator==0.1.4 python3 scripts/tp_validate.py recon $(FILE)

tp-fixture-land: ## Land source artifacts in the local Databricks transport fixture (NS=<ns>)
	python3 scripts/tp_databricks/local_fixture.py land --ns $${NS:-fixture} --source $${FIXTURE_SOURCE:-etl/legacy-extra}

tp-fixture-verify: ## Verify local fixture bytes and checksums (NS=<ns>)
	python3 scripts/tp_databricks/local_fixture.py verify --ns $${NS:-fixture}

tp-fixture-clean: ## Remove local Databricks transport fixture (NS=<ns>)
	python3 scripts/tp_databricks/local_fixture.py clean --ns $${NS:-fixture}

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
	TZ=UTC LC_ALL=C $(PROCS_ENV) DB_NAME=billing_$(NS) DB_PORT=$(PROCS_DB_PORT) $(PROCS_UV) procs/harness/record.py $(if $(MODULE),--module $(MODULE),) $(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),) $(if $(ALLOW_RERECORD),--allow-rerecord,) $(if $(RERECORD_REASON),--rerecord-reason $(RERECORD_REASON),)

procs-list: ## List stored-procedure modules and scenarios
	$(PROCS_UV) procs/harness/list.py $(if $(MODULE),--module $(MODULE),)

procs-parity: procs-validate ## Replay extracted billing scenarios (NS=<namespace>, MODULE and SCENARIO optional)
	TZ=UTC LC_ALL=C $(PROCS_ENV) BILLING_SVC_URL=$${BILLING_SVC_URL:-http://localhost:$(PROCS_TARGET_PORT)} $(PROCS_UV) procs/harness/replay.py $(if $(MODULE),--module $(MODULE),) $(if $(SCENARIO),--scenario $(SCENARIO),)

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

# --- MongoDB migration units (local mongo:7 fixture stands in for Atlas) ---

MONGO_FIXTURE_COMPOSE = docker compose -f docker-compose.mongo-fixture.yml -p otterworks-mongo-fixture
MONGO_FIXTURE_PORT ?= 27017
MONGO_UV = uv run --no-project --with pymongo==4.10.1 --with oracledb==2.5.1
MONGO_FILES_UV = uv run --no-project --with pymongo==4.10.1 --with boto3==1.35.36
MONGO_DOCUMENTS_UV = $(MONGO_UV) --with psycopg2-binary==2.9.10
MONGO_DOCUMENTS_RECON_OUT = $(if $(OUT),$(OUT),$(if $(filter fixture,$(or $(RUN_MODE),fixture)),docs/tech-partnerships/recon/mongo_documents.recon.json,build/tp-recon/mongo_documents.$(NS).recon.json))
MONGO_DOCUMENTS_RECON_RERUN = $(if $(or $(filter fixture,$(or $(RUN_MODE),fixture)),$(filter 1 true yes,$(RERUN))),--rerun-migration,)
MONGO_ENV = MONGO_URI=$(or $(MONGO_URI),mongodb://localhost:$(MONGO_FIXTURE_PORT)) MONGO_DB=$(or $(MONGO_DB),ow_tp_$(NS)) DB_PORT=$(ORACLE_BILLING_DB_PORT)
MONGO_DOCUMENTS_RECON_OUT = $(if $(OUT),$(OUT),$(if $(filter fixture,$(or $(RUN_MODE),fixture)),docs/tech-partnerships/recon/mongo_documents.recon.json,build/tp-recon/mongo_documents.$(NS).recon.json))
MONGO_DOCUMENTS_RECON_RERUN = $(if $(or $(filter fixture,$(or $(RUN_MODE),fixture)),$(filter 1 true yes,$(RERUN))),--rerun-migration,)

tp-mongo-fixture-up: ## Start the local MongoDB fixture (mongo:7 on localhost:$(MONGO_FIXTURE_PORT))
	MONGO_FIXTURE_PORT=$(MONGO_FIXTURE_PORT) $(MONGO_FIXTURE_COMPOSE) up -d --wait --wait-timeout 180

tp-mongo-fixture-down: ## Stop the local MongoDB fixture and drop its data
	MONGO_FIXTURE_PORT=$(MONGO_FIXTURE_PORT) $(MONGO_FIXTURE_COMPOSE) down -v

tp-mongo-test: ## Unit-test the MongoDB migration document models (no services needed)
	$(MONGO_UV) --with boto3==1.35.36 --with pytest==8.3.3 python3 -m pytest scripts/tp_mongo -q

tp-mongo-customers: ## Migrate Oracle CUSTOMER_MASTER + EAV into <db>.customers (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-customers NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_UV) python3 scripts/tp_mongo/migrate_customers.py --ns $(NS)

tp-mongo-customers-recon: ## Recon customers by reading the target back (NS=<namespace>, RUN_MODE=fixture|live)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-customers-recon NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_UV) python3 scripts/tp_mongo/recon_customers.py --ns $(NS) \
		--run-mode $(or $(RUN_MODE),fixture) --rerun-migration \
		--out docs/tech-partnerships/recon/mongo_customers.recon.json

tp-mongo-migrate-files: ## Migrate DynamoDB file metadata into <db>.files (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-migrate-files NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_FILES_UV) python3 scripts/tp_mongo/migrate_files.py --ns $(NS)

tp-mongo-recon-files: ## Recon files by reading the target back (NS=<namespace>, RUN_MODE=fixture|live)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-recon-files NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_FILES_UV) python3 scripts/tp_mongo/recon_files.py --ns $(NS) \
		--run-mode $(or $(RUN_MODE),fixture)

tp-mongo-invoices: ## Migrate Oracle invoices into <db>.invoices (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-invoices NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_UV) --with boto3==1.35.36 python3 scripts/tp_mongo/migrate_invoices.py --ns $(NS)

tp-mongo-invoices-recon: ## Recon invoices by reading the target back (NS=<namespace>, RUN_MODE=fixture|live)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-invoices-recon NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) $(MONGO_UV) --with boto3==1.35.36 python3 scripts/tp_mongo/recon_invoices.py --ns $(NS) \
		--run-mode $(or $(RUN_MODE),fixture) \
		--out $(or $(REPORT),docs/tech-partnerships/recon/mongo_invoices.recon.json)

tp-mongo-documents: ## Migrate Postgres documents into <db>.documents (NS=<namespace>)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-documents NS=demo)
endif
	$(call validate_ns)
	$(MONGO_ENV) DB_PORT=$(or $(DB_PORT),5432) $(MONGO_DOCUMENTS_UV) python3 scripts/tp_mongo/migrate_documents.py --ns $(NS)

tp-mongo-documents-recon: ## Recon documents by reading the target back (NS=<namespace>, RUN_MODE=fixture|live)
ifndef NS
	$(error NS is required, e.g. make tp-mongo-documents-recon NS=demo)
endif
	$(call validate_ns)
	@mkdir -p "$(dir $(MONGO_DOCUMENTS_RECON_OUT))"
	$(MONGO_ENV) DB_PORT=$(or $(DB_PORT),5432) $(MONGO_DOCUMENTS_UV) python3 scripts/tp_mongo/recon_documents.py --ns $(NS) \
		--run-mode $(or $(RUN_MODE),fixture) \
		$(MONGO_DOCUMENTS_RECON_RERUN) \
		--out "$(MONGO_DOCUMENTS_RECON_OUT)"

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
	TZ=UTC LC_ALL=C DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_PARITY_UV) procs/harness/oracle_record.py $(if $(MODULE),--module $(MODULE),) $(if $(ALLOW_RERECORD),--allow-rerecord,)

oracle-parity: procs-validate ## Oracle vs Postgres parity run (NS=<namespace>; requires procs-up and oracle-billing-up)
	$(call validate_ns)
	DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_BILLING_UV) testdata/legacy/oracle_billing_seed.py --ns $(NS) --scale $(or $(SCALE),demo)
	rm -rf $(ORACLE_PARITY_RUN)/$(NS)
	TZ=UTC LC_ALL=C $(PROCS_ENV) DB_NAME=billing_$(NS) DB_PORT=$(PROCS_DB_PORT) $(PROCS_UV) procs/harness/record.py --output-dir $(ORACLE_PARITY_RUN)/$(NS)/postgres
	TZ=UTC LC_ALL=C DB_PORT=$(ORACLE_BILLING_DB_PORT) $(ORACLE_PARITY_UV) procs/harness/oracle_record.py --output-dir $(ORACLE_PARITY_RUN)/$(NS)/oracle
	TZ=UTC LC_ALL=C uv run procs/harness/oracle_parity.py --postgres-dir $(ORACLE_PARITY_RUN)/$(NS)/postgres --oracle-dir $(ORACLE_PARITY_RUN)/$(NS)/oracle --namespace $(NS)

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

tp-run-branch: ## Cut and push the per-run working branch for a rehearsal (TRACK=mongodb|databricks|aws)
	@scripts/tp-run-branch.sh $(TRACK)

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

# Deterministic-run wrapper: pins TZ/LC_ALL (and, with TP_FAKETIME set, the
# clock) so golden recordings and byte-identical parity claims are stable
# across machines and reruns.
TP_DET := scripts/tp-run-deterministic.sh

legacy-etl-gen-data: ## Generate deterministic CUSTBILL sample input (NS=<ns>)
	$(TP_DET) perl etl/legacy-extra/tools/gen_sample_data.pl $${NS:-dev}

legacy-etl-run: ## Run one legacy batch job (JOB=<name>, see legacy-etl-list)
	@test -n "$(JOB)" || { echo "usage: make legacy-etl-run JOB=<name>"; exit 1; }
	@case "$(JOB)" in \
	  sftp_ingest_poll)           command -v ksh >/dev/null || { echo "ksh required (sudo apt-get install -y ksh)"; exit 1; }; $(TP_DET) etl/legacy-extra/jobs/sftp_ingest_poll.ksh ;; \
	  parse_custbill_fixedwidth)  $(TP_DET) etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh ;; \
	  finance_excel_report)       $(TP_DET) perl etl/legacy-extra/jobs/finance_excel_report.pl ;; \
	  run_all)                    command -v ksh >/dev/null || { echo "ksh required (sudo apt-get install -y ksh)"; exit 1; }; RUN_ALL_SLEEP=$${RUN_ALL_SLEEP:-0} $(TP_DET) etl/legacy-extra/run_all.sh ;; \
	  *) echo "unknown JOB '$(JOB)' (see: make legacy-etl-list)"; exit 1 ;; \
	esac

legacy-sftp-up: ## Start the optional localhost-only SFTP drop fixture
	mkdir -p $${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}/sftp-drop/upload
	LEGACY_SFTP_UID=$$(id -u) docker compose -f etl/legacy-extra/docker-compose.sftp.yml up -d

legacy-sftp-down: ## Stop the SFTP drop fixture
	docker compose -f etl/legacy-extra/docker-compose.sftp.yml down
