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

3. Reproduce the user-visible symptom through the API gateway (never by reading the flag):
   ```
   make incident-probe SCENARIO=search-service:suggest_500 INCIDENT_TARGET=http://localhost:8080
   ```
   The probe drives `GET /api/v1/search/suggest?q=<prefix>` as an authenticated user.
   While the incident is live it reports **FAIL** (the endpoint answers 500) and exits 1;
   the evidence — request, status, and response excerpt — is written to
   `incidents/reports/incident-report.{json,md}`. A 502/503/504 from the gateway means the
   backend is down, and the probe reports **INCONCLUSIVE** — investigate the service itself
   before drawing any conclusion about the suggest handler.

## Resolution Steps

1. If the chaos flag is what activated the failing code path, clear it:
   ```
   make incident-reset INCIDENT_TARGET=http://localhost:8080
   ```
   (`DELETE /api/v1/admin/chaos` — clears every chaos flag; on a deployed tenant,
   `scripts/inject-bug.sh <ATTENDEE_ID> reset`.)
2. If the failing code path is live without the flag, fix the suggest handler in
   `services/search-service/app/api/search.py`: the `_rankingScore` enrichment reads a field
   MeiliSearch only returns when requested via `attributesToRetrieve`, so either request the
   field or guard the lookup. Redeploy the service.
3. Prove the incident is resolved:
   ```
   make incident-verify SCENARIO=search-service:suggest_500 INCIDENT_TARGET=http://localhost:8080
   ```
   Verification only reports **PASS** when the suggest endpoint returns 200 *with a
   suggestion list* for a legitimate authenticated caller — a fix that refuses everybody
   (401/403 for all users) reports INCONCLUSIVE and does not count as resolved.

## Post-Incident

1. Attach `incidents/reports/incident-report.{json,md}` from the failing probe run and the
   passing verify run to the incident record — together they are the machine-checkable
   before/after evidence.
2. Confirm the `SearchSuggestHighErrorRate` alert has cleared and the search-service panel
   on the Chaos Scenarios dashboard (`observability/grafana/dashboards/chaos-scenarios.json`)
   has returned to baseline.
3. If the incident was chaos-injected for a demo, note that flags auto-expire (10-minute TTL
   from the admin endpoint) — an incident that "self-resolved" mid-investigation is usually
   the TTL, not a fix.
