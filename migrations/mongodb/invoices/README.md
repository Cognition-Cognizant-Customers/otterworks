# `mongo-invoices` — Oracle `INVOICE_HEADER` + `INVOICE_LINE` → Atlas `invoices`

Migrates the denormalized mainframe-conversion invoice feed out of Oracle
`OW_BILLING` into MongoDB Atlas, with lines **embedded** in their header
(bounded fan-out, ~8 lines per invoice) and header-less lines quarantined
instead of dropped.

Scope of this workload — nothing else in the Atlas project is touched:

| Object | Purpose |
|---|---|
| `ow_tp_<ns>.invoices` | one document per `INVOICE_HEADER` row, lines embedded |
| `ow_tp_<ns>.invoice_lines_orphaned` | `INVOICE_LINE` rows whose `INVOICE_ID` has no header |

## Prerequisites

- Legacy before-state up and seeded for the namespace (`make oracle-billing-up`,
  `make oracle-billing-seed NS=<ns> SCALE=demo`) — see
  `docs/tech-partnerships/runbook-mongodb.md`.
- `MONGODB_ATLAS_URI` in the environment (never committed, never printed) and
  the calling host's IP on the Atlas project access list.

## Collection / index setup

```bash
MONGODB_ATLAS_URI=... uv run --no-project --with pymongo==4.10.1 \
    migrations/mongodb/invoices/setup_collections.py --ns demo
```

Indexes created on `invoices`:

| Index | Read pattern |
|---|---|
| `{ customerId: 1, invoiceDate: -1 }` | a customer's invoices, newest first |
| `{ invoiceNo: 1 }` (unique) | lookup by human-facing invoice number |
| `{ tenantId: 1, status: 1 }` | per-tenant status dashboards / dunning sweeps |
| `{ "lines.lineId": 1 }` | trace a legacy `LINE_ID` back to its invoice |

`invoice_lines_orphaned` is indexed on `{ "raw.INVOICE_ID": 1 }` (find the
dangling pointer) and `{ quarantine_reason: 1 }` (sweep a quarantine class).

The Atlas project, cluster and database user are owned by the shared
`infrastructure/terraform-atlas/` stack and are **not** managed from here.
