# OtterWorks Enterprise Drive — synthetic app-data seed

Populates the **OtterWorks company drive** (an enterprise retailer of products
for otters — SalmonSnax, shrimp treats, fish-oil supplements, kelp snacks,
otter apparel and grooming textiles) into a live OtterWorks deployment so the
web UI shows a deep, realistic, browsable, multimodal drive: ~15 departments as
top-level folders, a nested subfolder tree (3–4 levels), a multi-format corpus
(xlsx/docx/pptx/pdf/csv/txt/md/json/png/jpg/svg + committed mp4 clips), and
rich-text documents.

Unlike the schema-based harness in `testdata/generated/seed` / `.../golden`
(which writes relational rows into an `otterworks_<ns>` **schema**), this seed
creates **real application data through the public API gateway**:

| Resource | Service | Backing store |
|----------|---------|---------------|
| Folders  | file-service `/api/v1/folders` | DynamoDB (folders table) |
| Files    | file-service `/api/v1/files/upload` | DynamoDB (metadata) + S3 (bytes) |
| Documents| document-service `/api/v1/documents` | Postgres `public` |

All resources are owned by one shared account (credentials come from the
`DRIVE_EMAIL` / `DRIVE_PASSWORD` secrets) so the result is a single enterprise
drive. The data is **synthetic** — no real people, customers, or PII.

## Files

- `catalog.py` — the shared ~40-SKU product catalog + market price model.
  Loads the committed OTD-15 contract CSVs (see below) and exposes
  `price_on` / `cogs_usd` / `margin_pct`. **Fails fast** if
  `testdata/market-series/` is missing — figures never fall back to random.
- `taxonomy.py` — the department/subfolder/file-template definition (data-driven;
  templates expand over years, quarters, regions, stores, suppliers, campaigns,
  categories, skus). Suppliers/categories/SKUs come from `catalog.py`. Edit this
  to reshape the drive.
- `filegen.py` — produces real, openable bytes for each file type. Every
  financial figure comes from `catalog.py`. Multimodal builders: matplotlib
  chart PNGs (`kind: chart`), reportlab platypus PDFs with tables + embedded
  images (`kind: contract|spec_sheet|invoice`), image-bearing pptx decks,
  per-SKU product-art PNG/SVG.
- `assets/` — 3 tiny committed MP4 clips (video/mp4, <20 KB each), uploaded
  into the folders listed in `taxonomy.ASSET_PLACEMENTS` (the web app previews
  them with a native `<video>` player).
- `generate_drive.py` — logs in, walks the taxonomy for the requested
  departments, creates folders/files/documents, then uploads the committed
  assets. **Idempotent** (skips folders, files, and documents that already
  exist) and **shardable** by department.
- `tests/` — self-contained pytest suite for the pipeline (no stack needed).

## Shared market-series contract (OTD-15)

All prices/costs/margins derive from `testdata/market-series/`
(`series.csv`, `baseline_prices.csv`, `products.csv`), the committed dataset
**owned by OTD-15** and also consumed by the analytics-service margins
dashboard. `catalog.py` reads the CSVs verbatim and reimplements the
documented deterministic extension: past the last baseline date each series is
extended with a per-day seeded random walk using a bit-exact port of
`java.util.Random.nextGaussian` with `seed = series_code.hashCode ^ epochDay`
and the fixed per-series daily sigmas from the market-series README — so the
drive artifacts and the analytics dashboard produce **identical numbers** for
any date. The margin model is likewise the one locked by OTD-15:

```
commodity_cost_usd = commodity_price(native) × fx_to_usd × content_kg
freight_cost_usd   = (DREWRY_WCI_USD_FEU / 25000 kg-per-FEU) × freight_kg
cogs_usd           = (commodity_cost_usd + freight_cost_usd) × (1 + overhead_pct/100)
margin_pct         = (list_price_usd − cogs_usd) / list_price_usd × 100
```

## Run

```bash
pip install -r requirements.txt

# preview volume (nothing written)
python generate_drive.py --gateway http://<gw> --email x --password x \
    --departments all --scale 1.0 --dry-run

# populate one department (shard) ...
python generate_drive.py --gateway http://<gw-host>:8080 \
    --email "$DRIVE_EMAIL" --password "$DRIVE_PASSWORD" \
    --departments Finance --scale 1.0 --workers 6

# ... or the whole drive
python generate_drive.py --gateway http://<gw-host>:8080 \
    --email "$DRIVE_EMAIL" --password "$DRIVE_PASSWORD" \
    --departments all --scale 1.0
```

`--scale` multiplies per-axis breadth (default `1.0` ≈ 2,500 files across 15
departments; each file <5 MB, total corpus well under a few hundred MB).
Because every department is an independent top-level subtree and the generator
is idempotent, the work fans out safely across many parallel workers/sessions
all writing under the same owner.

### Reseeding after a content change

Idempotency is filename-based, so a renamed taxonomy **adds** files next to
old ones instead of replacing them. To pick up new content start from fresh
volumes (`docker compose ... down -v && make up`) and re-run the generator
with `--register`; the blueprint snapshot bake already starts from `down -v`,
so a rebuild picks the new content up automatically.

## Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
```

## Seed-loader integration

`seed-loader.job.yaml` is a Kubernetes Job that runs this generator against the
in-cluster gateway (`api-gateway.<ns>.svc.cluster.local:8080`) on demand /
after a spin-up, mirroring the golden reference-data loader. It reads the drive
credentials from the `retail-drive-seed` Secret and passes `--register` so it
bootstraps the account on a fresh environment. See the job manifest for details.
