# Single Pane of Glass — multi-system aggregation demo

An agentic workflow that pulls from **three heterogeneous systems**, massages and
reconciles the data, and renders **one consolidated dashboard** — the "single
pane of glass". Built to demonstrate, on the OtterWorks golden app, the pattern
of *content retrieval from many systems → transformation → unified view*, run on
a schedule.

It exercises the Devin capabilities that pattern needs:

| Leg | System | What it stands for | Mechanism |
|-----|--------|--------------------|-----------|
| 1 | OtterWorks enterprise drive (API gateway) | a structured / "mainframe / DB2" system with an API | HTTP + JWT (`connectors/structured_api.py`) |
| 2 | OtterWorks web portal (`:3000`) | a **UI-only web app with no API** | logs in **through the browser** with vaulted creds, reads content off the screen (`connectors/web_portal.mjs`, Playwright over CDP) |
| 3 | World Bank Open Data | the **public web / internet** | HTTP crawl (`connectors/external_web.py`) |

The **transform** step (`aggregate.py`) rolls up per-department metrics, folds in
external market context, and **reconciles** the files a human sees in the web UI
against the structured system of record. The **render** step (`render.py`) emits
a self-contained `dashboard.html` (inline CSS + charts, no external CDNs) plus the
browser screenshot captured during leg 2.

## Run it

Prereqs: the OtterWorks stack is up (`make infra-up && make up`) and the drive
account credentials are available in the environment (from the Devin vault):

```bash
export DRIVE_EMAIL=...        # provided by the secrets vault
export DRIVE_PASSWORD=...     # provided by the secrets vault

# from the repo root
make demo-single-pane
# or directly:
python3 demo/single-pane-of-glass/run.py
```

Skip the browser leg (no CDP browser needed) with:

```bash
make demo-single-pane ARGS="--skip-portal"
```

Outputs land in `demo/single-pane-of-glass/output/` (git-ignored):

- `dashboard.html` — the single pane of glass (open in a browser)
- `portal.png` — the web portal screenshot captured via the browser
- `data.json` — the aggregated, reconciled dataset

## Configuration

Everything is overridable via environment variables (see `config.py`) so the same
workflow runs unchanged locally, in CI, and in a scheduled Devin session:

`OTTER_GATEWAY_URL`, `OTTER_WEB_URL`, `OTTER_CDP_URL`, `DRIVE_EMAIL`,
`DRIVE_PASSWORD`, `WORLDBANK_COUNTRY`, `SPOG_OUTPUT_DIR`.

The browser leg reuses the Playwright install already present in
`frontend/client-app/node_modules` — no extra dependencies.

## As a scheduled session

The intended production shape is a scheduled Devin Automation that runs this
workflow on a cadence (e.g. weekly), then delivers `dashboard.html` /
`dashboard_full.png` to Slack. Because every source, credential, and output path
is environment-driven, the schedule only needs the stack reachable and the
`DRIVE_*` secrets in the vault.
