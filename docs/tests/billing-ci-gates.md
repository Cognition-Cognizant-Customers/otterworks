# Billing CI Gates

OtterWorks has a general CI gate for each billing service. These gates keep
service-level checks independent from the stored-procedure parity workflow.

## `billing-service`

The `billing-service` gate runs when a pull request or push changes a file
under `services/billing-service/**`. It uses Python 3.12 and `uv` to:

1. Install the locked project and development dependencies with `uv sync
   --locked`.
2. Run Ruff against `app`, `scripts`, and `tests`.
3. Run the billing service pytest suite.

Run the same checks locally:

```bash
cd services/billing-service
uv sync --locked
uv run ruff check app scripts tests
uv run pytest
```

## `legacy-billing`

The `legacy-billing` gate runs when a pull request or push changes a file
under `services/legacy-billing/**`. It uses Python 3.12 to install
`requirements-dev.txt` and run the pytest suite.

Run it locally in an isolated environment:

```bash
cd services/legacy-billing
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The smoke test in `tests/test_app.py` imports the Flask application and calls
`/health` through Flask's test client. The health route normally calls the
module-level `select()` helper, which would connect to Postgres. The test
monkeypatches that helper to return an empty result, so the check is
hermetic and does not require a database.

## Relationship to stored-procedure parity

`.github/workflows/procs-parity.yml` is a coupled integration workflow. It
runs the billing-service pytest suite when billing/procedure-related paths
change, then starts the Compose stack for rules and stored-procedure parity
checks. The gates described here are general per-service checks: they run
directly from each service's own path filter and do not require the parity
Compose stack.
