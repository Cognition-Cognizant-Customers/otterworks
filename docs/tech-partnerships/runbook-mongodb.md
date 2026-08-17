# Demo Runbook — MongoDB: Oracle/DynamoDB → Atlas on AWS

**Duration:** ~30 minutes standalone.
**Story:** OtterWorks' customer/billing data is trapped in a 155-column Oracle
table plus an EAV dumping ground; document content lives in Postgres; file
metadata lives in DynamoDB. Three stores, three shapes, one domain — a natural
document model. We migrate to MongoDB Atlas on AWS with Devin fanning out one
child session per workload, and prove the move with seed-manifest
reconciliation.

All numbers below are for namespace `demo` at `SCALE=demo` — they are
deterministic (seeded RNG derived from the namespace), so what you see on
screen will match this runbook byte-for-byte if you use `NS=demo`.

## Pre-demo setup (do before the audience arrives)

```bash
make infra-up                     # Postgres + LocalStack (DynamoDB, S3)
make oracle-billing-up            # Oracle Free on localhost:52521 (first boot: 10–20 min)
make seed-legacy NS=demo          # Postgres/DynamoDB/S3 estates (~30 s)
make oracle-billing-seed NS=demo  # Oracle estate (~2–4 min)
make seed-legacy-validate NS=demo # sanity: 15/15 checks PASS
```

Expected seed output (deterministic for `NS=demo`):

| Store | Object | Count |
|---|---|---|
| Oracle | `OW_BILLING.CUSTOMER_MASTER` | 25,000 rows |
| Oracle | `OW_BILLING.INVOICE_HEADER` / `INVOICE_LINE` | 18,750 / 150,000 rows |
| Oracle | `OW_BILLING.ENTITY_ATTR_VALUE` | 8,333 rows |
| Postgres | `otterworks_demo.documents` / `document_versions` / `document_snapshots` | 2,000 / 13,876 / 390 rows |
| DynamoDB | `otterworks-file-metadata` (ns=demo) | 10,000 items |

Manifest: `testdata/legacy/manifests/demo.json` (seed `714559852`).

### Local MongoDB fixture

Migration development uses the local MongoDB fixture only; it never connects
to MongoDB Atlas. Set `MONGO_URI` for migration commands, or use the local
default:

```bash
export MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
make tp-mongo-fixture-up
# stop the shared fixture when the demo is complete:
# make tp-mongo-fixture-down
```

## Beat 1 — Before-state tour (0:00–0:10)

### 1a. The Oracle horror (5 min)

```bash
sqlplus ow_billing/ow_billing@localhost:52521/FREEPDB1
# no host sqlplus? use the client inside the fixture container:
# docker exec -it otterworks-oracle-billing-oracle-billing-1 \
#   sqlplus ow_billing/ow_billing@localhost:1521/FREEPDB1
```

Show the 155-column table and read a few names out loud:

```sql
SELECT COUNT(*) FROM user_tab_columns WHERE table_name = 'CUSTOMER_MASTER';
-- 155
SELECT column_name FROM user_tab_columns
 WHERE table_name = 'CUSTOMER_MASTER'
   AND (column_name LIKE 'FLAG%' OR column_name LIKE 'UDF%'
        OR column_name LIKE 'ADDR%' OR column_name LIKE 'PHONE%')
 ORDER BY column_id;
-- ADDR_LINE_1..6, PHONE1..4, FLAG_01..20, UDF_01..40
```

Talking points while it scrolls: comma-separated ID lists in `VARCHAR2`
(`RELATED_ACCT_IDS`, `PROMO_CODES_CSV`), dates stored as `VARCHAR2(9)
'DD-MON-YY'` strings, magic-number `*_CD` statuses resolved through a generic
`CODES` table, full-row-copy `_HIST` triggers.

Then the EAV table — "the schema gave up":

```sql
SELECT attr_name, attr_value FROM entity_attr_value
 WHERE ROWNUM <= 10;
-- PORTAL_THEME=blue, Y2K_VERIFIED=TRUE, COLLECTIONS_NOTE=see ticket 48213 ...
SELECT COUNT(*) FROM entity_attr_value;   -- 8333
```

Optional garnish: open
`services/legacy-billing/db/oracle/ops/deploy_prod_FINAL_v2.sh.txt` and
`services/legacy-billing/db/oracle/ops/OPERATIONS_HANDBOOK.doc.txt` — this is
how the estate is actually operated today.

### 1b. Postgres documents + DynamoDB metadata (3 min)

```bash
docker exec -it otterworks-postgres psql -U otterworks -d otterworks \
  -c "SELECT COUNT(*) FROM otterworks_demo.documents;"          # 2000
docker exec -it otterworks-postgres psql -U otterworks -d otterworks \
  -c "SELECT COUNT(*) FROM otterworks_demo.document_versions;"  # 13876

# LocalStack accepts any credentials; the scan paginates, so sum the pages
AWS_DEFAULT_REGION=us-east-1 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
aws --endpoint-url http://localhost:4566 dynamodb scan \
  --table-name otterworks-file-metadata \
  --filter-expression "ns = :n" \
  --expression-attribute-values '{":n":{"S":"demo"}}' \
  --select COUNT --query Count --output text | paste -sd+ | bc
# 10000
```

Punchline: a *document* domain spread across a relational horror table, an
EAV escape hatch, a versioned Postgres store, and a key-value metadata table —
this is the textbook "should have been a document database" estate.

### 1c. The contract (2 min)

Open `testdata/legacy/manifests/demo.json`: row counts, order-independent
md5 checksums, and **exactly enumerated planted anomalies** — 37 orphaned
`INVOICE_LINE` rows, 50 dirty `SIGNUP_DT` strings (`31-FEB-24`, `N/A`, …),
31 malformed CSV lists, 10 document version gaps, 6 orphaned snapshots,
40 orphaned DynamoDB metadata items. This manifest is the before-contract
every migration report must reconcile against.

## Beat 2 — Target model + migration plan (0:10–0:15)

Sketch the Atlas target (Atlas on AWS, same region as the app's EKS/S3
estate):

| Legacy store | Atlas collection | Modeling move |
|---|---|---|
| `CUSTOMER_MASTER` (155 cols) + `ENTITY_ATTR_VALUE` | `customers` | Sparse columns → optional fields; EAV rows fold into an `attributes` subdocument; CSV lists → real arrays; `DD-MON-YY` strings → BSON dates (dirty ones quarantined) |
| `INVOICE_HEADER` + `INVOICE_LINE` | `invoices` | Lines embedded in their header (bounded ~25 lines/invoice); orphaned lines quarantined |
| Postgres `documents` + `document_versions` + `document_snapshots` | `documents` | Versions as a bounded subarray or bucketed collection; snapshots referenced |
| DynamoDB `otterworks-file-metadata` | `files` | Item-per-document 1:1; `ns` attribute → tenant field |

## Beat 3 — Parallel child-session fan-out (0:15–0:22)

Show the Devin fan-out plan — one child session per workload, all running
concurrently because namespaces and manifest targets are independent:

| Child session | Scope | Input contract | Done when |
|---|---|---|---|
| `mongo-customers` | Oracle `CUSTOMER_MASTER` + EAV → `customers` | manifest `oracle.OW_BILLING.CUSTOMER_MASTER` (25,000 rows, checksum) | 25,000 docs; 50 dirty dates + 31 bad CSVs quarantined and enumerated |
| `mongo-invoices` | Oracle invoices → `invoices` | `INVOICE_HEADER` 18,750 / `INVOICE_LINE` 150,000 + checksum | 18,750 docs, 149,963 embedded lines, 37 orphans quarantined |
| `mongo-documents` | Postgres → `documents` | 2,000 / 13,876 / 390 + checksums | 2,000 docs; 10 version gaps + 6 orphaned snapshots reported |
| `mongo-files` | DynamoDB → `files` | 10,000 items + checksum | 10,000 docs; 40 orphaned-metadata markers reported |

For multi-tenant scale-out, the same fan-out works **per tenant**: each seeded
namespace (`demo`, `t01`, `t02`, …) is an independent slice with its own
manifest, so you can demo "one child per tenant" instead of (or on top of)
"one child per workload". Seed extra tenants live if asked:
`make seed-legacy NS=t01` (~30 s each, deterministic).

Talking points: each child gets the manifest as its acceptance contract; the
parent session only reviews parity reports; retries are safe because seeds
are deterministic and reruns are idempotent.

## Beat 4 — Reconciliation evidence (0:22–0:28)

What "done" looks like — show, don't tell:

1. **The manifest** (`testdata/legacy/manifests/demo.json`) — the immutable
   before-contract: counts, checksums, and the exact anomaly enumeration.
2. **The validator** — `make seed-legacy-validate NS=demo` re-derives counts
   and checksums **from the live stores** and prints a PASS/FAIL table
   (15/15 checks at demo scale). The same pattern extends to the Mongo side:
   a parity report that recomputes the identical line-format checksums from
   Atlas collections and diffs them against the manifest.
3. **Anomaly ledger** — the migration must *find* every planted defect:
   37 + 50 + 31 Oracle anomalies, 10 + 6 Postgres anomalies, 40 DynamoDB
   anomalies. A recon report that surfaces exactly those counts (no more, no
   fewer) is the proof the pipeline reads every row.

## Beat 5 — Wrap (0:28–0:30)

- Three stores → one document database; the app's read patterns collapse to
  single-document reads.
- Deterministic seeds + manifest contracts = re-runnable, auditable demos.
- Segue to the combined demo: `runbook-modernize-otterworks.md`.

## Cleanup

```bash
make oracle-billing-down        # drops all Oracle data
make testdata-clean NS=demo     # drops otterworks_demo Postgres schema
```

DynamoDB/S3 slices are wiped automatically on the next reseed of the
namespace.
