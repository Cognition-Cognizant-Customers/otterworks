# Runbook: Search Autocomplete 500 Errors

**Severity:** Critical

## Alert

`SearchSuggestHighErrorRate` -- fires when search-service 5xx rate exceeds 5% over a 1-minute window.

## Symptoms

- Autocomplete suggestions stop appearing in the web app search bar.
- The Chaos Scenarios dashboard shows elevated error rates on the search-service panel.
- Application logs contain `KeyError: '_rankingScore'` in the suggest endpoint handler.

## Investigation Steps

1. Confirm the error in search-service logs:
   ```
   kubectl logs -l app=search-service --tail=100 -n otterworks | grep -i "KeyError\|rankingScore\|500"
   ```
2. Check whether the chaos flag `chaos:search-service:suggest_500` is set in Redis:
   ```
   redis-cli EXISTS chaos:search-service:suggest_500
   ```

3. Verify the alert condition against Prometheus. The `SearchSuggestHighErrorRate` rule
   (`observability/grafana/provisioning/alerting/alert-rules.yml`, uid `search-suggest-errors`,
   group `otterworks-chaos-scenarios`, evaluated every 30s) fires when this ratio exceeds
   **0.05** (5%):
   ```
   sum(rate(search_service_requests_total{job="search-service",status=~"5.."}[1m]))
   /
   sum(rate(search_service_requests_total{job="search-service"}[1m]))
   ```
   Run these queries directly against Prometheus to quantify the blast radius:
   ```
   # Overall 5xx ratio (the alert expression)
   sum(rate(search_service_requests_total{job="search-service",status=~"5.."}[1m])) / sum(rate(search_service_requests_total{job="search-service"}[1m]))

   # 5xx rate broken down by endpoint and status
   rate(search_service_requests_total{status=~"5.."}[1m])

   # Total request rate (the chaos probe generates synthetic /suggest traffic)
   rate(search_service_requests_total[1m])
   ```
   The `search_service_requests_total` counter is defined in
   `services/search-service/app/api/health.py` with labels `method`, `endpoint`, `status`.
4. Check the Grafana dashboards under `observability/grafana/dashboards/`:
   - **OtterWorks Chaos Scenarios** (`chaos-scenarios.json`): the "Search Service Chaos
     Active" stat panel turns red/ACTIVE when the 5xx ratio exceeds 5%, and the
     "Search Service - Autocomplete Suggest Errors" row shows the 5xx error rate, request
     rate, and errors-by-status panels.
   - **OtterWorks Incident Overview** (`incident-overview.json`): "All Services - 5xx Error
     Rate" shows search-service spiking relative to the other services, and the "Jaeger
     Trace Search" panel links to per-service trace search.
5. Inspect traces in Jaeger (`http://jaeger:16686/search?service=search-service`): filter
   for spans on `GET /api/v1/search/suggest` — failing requests show 500s from the handler
   rather than errors from MeiliSearch itself.
6. Distinguish chaos from a genuine regression:
   - **Chaos path** (flag `chaos:search-service:suggest_500` set): the handler in
     `services/search-service/app/api/search.py` runs the ranking-score enrichment branch,
     where `sorted(raw_suggestions, key=lambda s: s["_rankingScore"], reverse=True)` raises
     `KeyError: '_rankingScore'` because `MeiliSearchService.suggest()` only retrieves
     `title` and `name` via `attributesToRetrieve` — the uncaught exception surfaces as a 500.
   - **Non-chaos path**: any failure is caught, logged as the structlog event
     `suggest_failed`, and the endpoint still returns **200 with an empty suggestions
     list** — so genuine suggest failures will NOT trip this 5xx alert (see Post-Incident).

## Resolution Steps

1. If the chaos flag is set (this is the expected cause in workshop tenants), clear it:
   ```
   ./scripts/inject-bug.sh <ATTENDEE_ID> reset
   ```
   This deletes all `chaos:*` keys in the tenant's own Redis
   (`kubectl -n otterworks-<ATTENDEE_ID> exec deploy/redis -- redis-cli DEL ...`). Flags are
   also set with a TTL (default 3600s), so they auto-expire if not reset. Confirm:
   ```
   redis-cli EXISTS chaos:search-service:suggest_500   # should return 0
   ```
   Then watch the alert-expression query drop back below 0.05 and the "Search Service Chaos
   Active" panel return to INACTIVE.

   > **Note:** chaos injection is scoped to **ephemeral tenants only**. The perpetual tenant
   > (`t-main`, tracking `main`) refuses injection — the demo-platform dashboard's inject
   > endpoint (`demo-platform/dashboard/app/api/tenants/[id]/inject/route.ts`) returns
   > `409 tenant is persistent; inject a bug into an ephemeral tenant instead`.

2. Code-level fix (if the KeyError appears without the chaos flag): the sort in `suggest()`
   in `services/search-service/app/api/search.py` must not assume `_rankingScore` is
   present. Either request the score explicitly in `MeiliSearchService.suggest()`
   (`services/search-service/app/services/meilisearch_client.py`) by adding
   `"showRankingScore": True` to the search options, or make the sort tolerant:
   ```python
   ranked = sorted(raw_suggestions, key=lambda s: s.get("_rankingScore", 0.0), reverse=True)
   ```
3. Prove the fix with the search-service test suite (covers
   `GET /api/v1/search/suggest` in `TestSuggestEndpoint`):
   ```
   cd services/search-service && .venv/bin/pytest tests/test_search_api.py -k suggest
   ```

## Post-Incident

- **Silent-failure gap:** the non-chaos `except` branch in `suggest()` returns
  `200 {"suggestions": []}` on any exception, logging only `suggest_failed`. A real
  MeiliSearch outage therefore degrades autocomplete to empty results while remaining
  invisible to `SearchSuggestHighErrorRate` (a 5xx-ratio alert). Add an alert on the
  `suggest_failed` log event (via Fluent Bit) or a dedicated failure counter alongside
  `search_service_requests_total` in `services/search-service/app/api/health.py`.
- **Alert coverage:** the alert only watches aggregate 5xx ratio; consider an
  endpoint-labelled variant using the existing `endpoint` label on
  `search_service_requests_total` so a suggest-only failure is attributable at a glance.
- **Chaos hygiene:** verify the incident tenant's flag was set via
  `scripts/inject-bug.sh` / the demo-platform dashboard (audited by
  `demo-platform/runner/entrypoint.sh` `ctl_audit`) and confirm the TTL behaviour
  (`CHAOS_TTL`, default 3600s) matches the workshop schedule.
- **Runbook drill:** confirm attendees can trace the failure end-to-end: alert →
  Chaos Scenarios dashboard → Jaeger `/api/v1/search/suggest` spans →
  `KeyError: '_rankingScore'` in logs → `inject-bug.sh <ATTENDEE_ID> reset`.
