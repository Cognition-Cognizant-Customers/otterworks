# Runtime validation of security-scan findings

This directory contains a harness that **empirically validates** the security
findings from the code scan (`scan-65ac9752831f48eba793df6b5efc16d1`) against a
**running** OtterWorks stack. Static findings tell you a vulnerability *probably*
exists; this proves which ones actually reproduce at runtime, with raw HTTP
evidence.

## Files

| File | Purpose |
|---|---|
| `validate_findings.py` | The harness. Forges JWTs with the shared secret, creates fixtures, and probes each finding. Writes `results.json`. |
| `gen_report.py` | Renders `REPORT.md` from `results.json`. |
| `results.json` | Machine-readable results (regenerated each run). |
| `REPORT.md` | Human-readable evidence report (regenerated from results). |

## How to run

```bash
# 1. bring up the stack (see caveat below)
make up seed=1

# 2. run the validation
uv run security/runtime-validation/validate_findings.py
#   (or: python3 security/runtime-validation/validate_findings.py)

# 3. regenerate the markdown report
python3 security/runtime-validation/gen_report.py
```

The harness reads `JWT_SECRET` (default = the docker-compose dev default) and
per-service URLs from the environment; override them to point at a different
deployment, e.g. `GATEWAY_URL=https://api-t-foo.example.com`.

## Exploitation primitives

1. **Forged JWT** — every service shares the hardcoded default
   `JWT_SECRET` (`otterworks-local-dev-jwt-secret-change-me-in-production`), so
   we mint our own admin / low-privilege tokens. A tampered-signature control
   request is included to prove it's the *secret* that's trusted, not "no
   verification at all".
2. **`X-User-ID` spoofing** — backend services trust the `X-User-ID` header the
   gateway is supposed to derive from a validated JWT. Talking to a service
   directly on its own port lets us set it to any user.

## Caveat for this run

The JVM services (`auth`, `report`, `notification`, `analytics`) could not be
built in the CI/dev box used for this run because upstream Maven/Gradle
repositories returned a persistent `HTTP 429 Too Many Requests`. Their findings
are marked `skipped (service down)` in the report — re-run the harness once those
images build and they will be exercised automatically.

## Safety

This is a **defensive** validation tool for a deliberately-vulnerable demo app.
It only exercises the app's own endpoints, creates throwaway fixtures, and does
not modify application code. Do not point it at systems you are not authorized to
test.
