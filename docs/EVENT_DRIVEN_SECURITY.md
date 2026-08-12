# Event-Driven Security Remediation Architecture

## Overview

OtterWorks uses an event-driven SAST pipeline where security findings detected
in pull requests are automatically routed to Devin for remediation. The pipeline
supports **two scanner paths** — Trivy (dependency CVEs) and SonarCloud (code
quality gate) — both feeding into the same Devin v3 API for autonomous fix
sessions. The pipeline runs without manual intervention for straightforward
fixes and escalates to human reviewers when automated remediation is
insufficient.

## Flow

```
Developer opens PR against main
        │
        ▼
┌───────────────────────────────────────────────────────┐
│                TWO PARALLEL SCAN PATHS                │
├─────────────────────┬─────────────────────────────────┤
│                     │                                 │
│  PATH 1: Trivy      │  PATH 2: SonarCloud             │
│  (pull_request)      │  (check_run: completed)         │
│                     │                                 │
│  ├ Is PR author     │  ├ check_run from                │
│  │ devin-bot?       │  │ sonarqubecloud app?           │
│  │  YES → skip      │  │  NO → skip                    │
│  │  NO  → scan      │  │                                │
│  │                  │  ├ Quality gate FAILED?           │
│  ├ Trivy fs scan    │  │  NO → skip                   │
│  │ HIGH+CRITICAL    │  │                                │
│  │                  │  ├ PR open + human-authored?      │
│  ├ Findings = 0?    │  │  NO → skip                    │
│  │  YES → pass      │  │                                │
│  │                  │  ├ Already attempted fix?         │
│  ├ attempts < MAX?  │  │  YES → skip (one-time)        │
│  │  NO → escalate   │  │                                │
│  │                  │  │                                │
│  └── Devin v3 API ──┴──┴─── Devin v3 API ──────────────┤
│                                                       │
│       Devin checks out branch,                        │
│       fixes vulnerabilities,                          │
│       runs service tests,                             │
│       pushes fix commit                               │
│              │                                        │
│              ▼                                        │
│       Push triggers re-scan                           │
│       (Trivy: synchronize event)                      │
│       (SonarCloud: new check_run on re-analysis)      │
│       → closed-loop verification                      │
└───────────────────────────────────────────────────────┘
```

## Scanner Paths

### Path 1: Trivy (Dependency CVEs)

Triggered by `pull_request` events (`opened`, `synchronize`). Trivy scans the
full filesystem for known dependency vulnerabilities (HIGH and CRITICAL
severity). Results are parsed into a structured findings summary and included
in the Devin prompt.

- **Re-scan loop:** Devin's fix push fires a `synchronize` event → Trivy
  re-scans automatically.
- **Escalation:** After `MAX_FIX_ATTEMPTS` (default: 2) fix cycles, a GitHub
  Issue is opened for manual review.

### Path 2: SonarCloud (Code Quality Gate)

Triggered by `check_run` events (`completed`) posted by the **SonarQube Cloud
GitHub app** (`sonarqubecloud`). SonarCloud analyzes the PR (Automatic Analysis
configured via `sonar-project.properties`) and reports the quality gate as a
check run. When the check run's conclusion is `failure`, the workflow:

1. Resolves the associated PR from the check run's head SHA
   (`GET /repos/{repo}/commits/{sha}/pulls`) and validates it is **open** and
   **not authored by `devin-ai-integration[bot]`**.
2. Verifies Devin hasn't already attempted a fix on this PR (comment marker).
3. Calls the **Devin v3 API** with the SonarCloud dashboard link and
   remediation instructions targeting the PR's head branch.
4. Posts a PR comment with the Devin session link.

This path is a **one-time remediation attempt** — if Devin has already posted
a fix comment on the PR, no additional sessions are created.

- **Re-scan loop:** Devin's fix push triggers SonarCloud re-analysis → a new
  `check_run` fires → if the quality gate still fails, the comment-marker check
  prevents a second session (one-time).
- **Dashboard link:** The Devin prompt includes the SonarCloud dashboard URL
  for the specific PR.

## Bot-Loop Prevention

Both paths check the PR author against `devin-ai-integration[bot]`
(the Trivy path via `github.event.pull_request.user.login`, the SonarCloud
path via the PR resolved from the check run's head SHA). PRs opened
by Devin are never remediated by this workflow. For Devin's *commits* on
human-authored PRs, the re-scan events still fire but the author check
passes (it is the human's PR), so the re-scan runs — which is the desired
closed-loop behavior.

**Trivy path:** A secondary guard counts Devin's commits on the PR. If that
count reaches `MAX_FIX_ATTEMPTS` (default: 2), the pipeline stops triggering
Devin and escalates instead.

**SonarCloud path:** A comment-based check (the "Devin SAST Auto-Fix —
Remediation In Progress (SonarCloud)" marker) enforces one-time remediation
per PR, and a concurrency group keyed on the check run's head SHA prevents
duplicate sessions from concurrent check_run deliveries.

## Escalation Policy

When automated remediation is exhausted (Trivy path only):

1. A GitHub Issue is created with the `security` and `needs-human-review` labels
2. The issue body contains the full findings summary
3. A PR comment notifies the developer that manual review is required

## Devin v3 API

Both paths create Devin sessions via the **Devin v3 organizations API**:

```
POST {DEVIN_API_BASE}/v3/organizations/{DEVIN_ORG_ID}/sessions
Authorization: Bearer $DEVIN_API_KEY
```

Request body:
- `prompt`: Remediation instructions — branch to check out, findings summary
  (Trivy) or SonarCloud dashboard link (SonarCloud), constraints (fix on the
  same branch, no new PR, don't suppress findings), and test expectations.
- `title`: Human-readable session title including the scanner and PR number.
- `tags`: `["sast-auto-remediate", "trivy"|"sonarcloud"]` for filtering.

The response's `session_id`/`url` is surfaced in the job summary and PR
comment so reviewers can follow the remediation live.

Required GitHub Actions secrets:
- `DEVIN_API_KEY` — Devin v3 API key (service user with session-creation
  permission for the org)

## Scan Configuration

| Setting | Value | Source |
|---------|-------|--------|
| Trivy scanner | Trivy v0.71.0 | `.github/workflows/sast-auto-remediate.yml` |
| Trivy severity filter | CRITICAL, HIGH | `SEVERITY_THRESHOLD` env var |
| Trivy excluded dirs | `services/report-service` | Legacy Java 8 service (separate upgrade track) |
| Trivy suppressions | `.trivyignore` | Acknowledged CVEs with documented justification |
| SonarCloud project key | `Cognition-Partner-Workshops_otterworks` | `sonar-project.properties` |
| SonarCloud org | `cognition-partner-workshops` | `sonar-project.properties` |

## Services Covered

| Service | Language | Manifest | Scan Target |
|---------|----------|----------|-------------|
| api-gateway | Go 1.22 | `go.mod` | Go modules |
| auth-service | Java 17 | `build.gradle` | Gradle dependencies |
| file-service | Rust | `Cargo.toml` | Cargo crates |
| document-service | Python 3.12 | `pyproject.toml` | Poetry packages |
| collab-service | Node.js 20 | `package.json` | npm packages |
| notification-service | Kotlin | `build.gradle.kts` | Gradle dependencies |
| search-service | Python 3 | `requirements.txt` | pip packages |
| analytics-service | Scala 3.4 | `build.sbt` | sbt dependencies |
| admin-service | Ruby 3.3 | `Gemfile` | Bundler gems |
| audit-service | C# 12 | `AuditService.csproj` | NuGet packages |
| report-service | Java 8 | `pom.xml` | **Excluded** (legacy upgrade track) |

## Extending to Snyk

The pipeline is scanner-agnostic. To add Snyk as a third scanner path:

Replace the Trivy step with `snyk/actions/node@master` (or the appropriate
ecosystem action). Parse `snyk test --json` output for `severity` fields. The
Devin prompt structure, bot-loop prevention, and escalation logic remain
unchanged.
