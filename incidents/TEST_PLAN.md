# PR #811 Incident Harness — Independent Confirmation Test Plan

Target: local stack at http://localhost:8080 (already running, all containers healthy).
CLI-only; no recording. Do NOT run docker compose build. Chaos flags TTL 600s.

For each scenario S in:
1. search-service:suggest_500
2. file-service:upload_s3_error
3. document-service:slow_queries
4. notification-service:consumer_strict_schema (probe takes ~60s while injected)

Loop (capture mtimes of incidents/reports/incident-report.{json,md} before each probe/verify):
- `make incident-inject SCENARIO=S` → exit 0.
- `make incident-probe SCENARIO=S` → **exit code 1**, output reports FAIL for S; both report files mtimes updated; JSON report references S with failing status.
- `make incident-reset` → exit 0.
- `make incident-verify SCENARIO=S` → **exit code 0**, output reports PASS; both report files rewritten (mtimes updated again).

Additional assertions:
- `make incident-list` shows all four scenario ids.
- For document-service:slow_queries, incident-report.json contains `measured_ms` and `threshold_ms` numeric fields in both FAIL and PASS runs.
- `uv run --with ruff ruff check incidents/harness --config incidents/harness/ruff.toml` (or `ruff check` if installed) exits 0 with no findings.

Fail criteria: any probe exiting 0 while injected, any verify exiting nonzero after reset, report files not rewritten, or missing latency fields.
