# BRD → OtterWorks: trust-score based auto-decline rules

A worked example of taking an external Business Requirement Document
("Credit Score Based Auto-Decline Rules — Agency Portal to Guidewire PolicyCenter")
and landing it in OtterWorks as working code plus the test coverage that proves
each requirement. The point is the *shape* of the workflow — BRD table → rule
table → table-driven tests → traceability — not the insurance domain.

## Domain mapping

| BRD (insurance) | OtterWorks | Where |
| --- | --- | --- |
| Quote | External share request for a document | `POST /api/v1/share-requests` |
| Source of Quote — Agency Portal | `CLIENT_PORTAL` | `RequestSource` |
| Source of Quote — Guidewire PolicyCenter | `ADMIN_CONSOLE` | `RequestSource` |
| State (MA) | Tenant data-residency region (`MA`) | `ShareRequest.region` |
| Line of Business (Home) | Workspace type (`HOME_DRIVE`) | `WorkspaceType` |
| Policy Type HO6 | Share type `PUBLIC_LINK` | `ShareType` |
| Policy Type HO4 | Share type `EXTERNAL_EMAIL` | `ShareType` |
| Transaction (New Business) | `NEW_SHARE` | `TransactionType` |
| Applicant credit score | Requester trust score (300–850) | `app/services/trust_score.py` |
| Declination Notice sent to GWPC | `share.declination_notice` SNS event consumed by the audit service | `app/api/share_requests.py` |

Code lives in `services/document-service/app/services/share_decline_rules.py`
(pure decision logic, no I/O), `app/services/trust_score.py` (score lookup) and
`app/api/share_requests.py` (transport + notice emission). The endpoint is
owner-only: it authenticates the caller like the other document routes and 404s
or 403s before any rule is evaluated, so notices cannot be forged for someone
else's document.

## Rule table (BRD section 3)

| Rule | Source | Region | Workspace | Share type | Transaction | Condition |
| --- | --- | --- | --- | --- | --- | --- |
| RULE-1 | Client portal | MA | HOME_DRIVE | PUBLIC_LINK | NEW_SHARE | trust score < 590 |
| RULE-2 | Client portal | MA | HOME_DRIVE | EXTERNAL_EMAIL | NEW_SHARE | trust score < 580 |

The threshold is exclusive: 589 declines, 590 does not. All other dimensions
must match exactly — a `TEAM_DRIVE`, a `RENEWAL`, or a non-MA region is out of
scope no matter how low the score.

## Behaviour matrix (BRD section 5)

| BRD | Source | Criteria met | Outcome | Declination notice |
| --- | --- | --- | --- | --- |
| 5.1 | Client portal | yes | `DECLINED` | generated |
| 5.2 | Client portal | no | `ALLOWED` | none |
| 5.3 | Admin console | yes | `BLOCKED` | none |
| 5.4 | Admin console | no | `ALLOWED` | none |

## Reproducible test data (BRD section 4)

The BRD requires testers to reproduce a given score band from fixed applicant
details (name, DOB, address). `TEST_PROFILE_SCORES` in
`app/services/trust_score.py` pins one profile per band — including the 589/590
pair that straddles the RULE-1 boundary — and any other profile hashes to a
stable score in range, so a run is never flaky. Callers may also pass
`trust_score` directly, which wins over the profile lookup.

## Traceability

| BRD section | Implementation | Test |
| --- | --- | --- |
| 3 — rule table | `RULES` | `test_rule_table_matches_brd`, `test_threshold_boundaries` |
| 2 — scope | `DeclineRule.matches_dimensions` | `test_out_of_scope_dimension_is_not_declined` |
| 4 — test data | `resolve_trust_score` | `test_designated_test_profiles_pin_their_band` |
| 5.1–5.4 | `evaluate` | `test_behaviour_quadrants`, `tests/test_share_requests_api.py` |

## Running it

```bash
cd services/document-service
poetry install
poetry run pytest tests/test_share_decline_rules.py tests/test_share_requests_api.py -v
poetry run pytest --cov=app --cov-report=term-missing
```

The new modules are at 100% statement coverage; document-service overall moves
from 81% to 85%.

Coverage was also under-reporting before this change: SQLAlchemy's async bridge
switches greenlets, so every line after the first awaited query went unrecorded
until `concurrency = ["thread", "greenlet"]` was set in `pyproject.toml`. On
`main` that understates the service by three points (78% reported vs 81%
actual) and hides most of `app/api/documents.py`.

```bash
curl -sX POST localhost:8083/api/v1/share-requests \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $JWT" \
  -d '{"document_id":"11111111-1111-1111-1111-111111111111","source":"CLIENT_PORTAL",
       "region":"MA","workspace_type":"HOME_DRIVE","share_type":"PUBLIC_LINK",
       "transaction":"NEW_SHARE","requester":{"first_name":"Olive","last_name":"Otter",
       "date_of_birth":"1985-03-14","address":"12 Harbor St, Boston, MA"}}'
# {"outcome":"DECLINED","rule_id":"RULE-1","trust_score":545,"declination_notice_sent":true,...}
```

## Extending this pattern

Each new rule is one row appended to `RULES` plus one row in the boundary
parametrisation — the behaviour matrix and dimension tests come along for free.
That is the leverage: a BRD table maps 1:1 onto a data-driven rule table, and
coverage of the requirement is a parametrised list rather than a hand-written
test per case.
