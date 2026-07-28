# Shared market-series & product-catalog seed data (OTD-15 contract)

Committed reference dataset for the margins analytics feature. **Owned by
OTD-15**; consumed by the analytics-service startup seeder and by OTD-14 (the
drive-spreadsheet seed) so both surfaces show the same numbers.

The analytics-service Docker build context is `services/analytics-service`
only, so these files are duplicated at
`services/analytics-service/src/main/resources/seed/market-series/`.
`MarketSeedResourcesSpec` asserts the two copies are checksum-identical —
if you change one, change both.

## Files & schemas

### `series.csv` — registry of commodity/freight/FX series
| Column | Type | Notes |
|---|---|---|
| `series_code` | string, PK | e.g. `SALMON_NOK_KG`, `DREWRY_WCI_USD_FEU`, `USD_NOK` |
| `name` | string | display name |
| `unit` | string | e.g. `NOK/kg`, `USD/FEU`, `NOK per USD` |
| `currency` | string | native quote currency (`USD` or `NOK`) |
| `category` | enum | `commodity` \| `freight` \| `fx` |

### `baseline_prices.csv` — daily historical observations per series
| Column | Type | Notes |
|---|---|---|
| `series_code` | string, FK → series.csv | |
| `price_date` | ISO date | daily, 2024-08-01 … 2026-06-30 |
| `value` | decimal | in the series' native unit |

### `products.csv` — ~40-SKU otter-retail catalog
| Column | Type | Notes |
|---|---|---|
| `sku` | string, PK | e.g. `SLM-001` |
| `name` | string | |
| `category` | string | `Seafood` \| `Pantry` \| `Apparel` |
| `commodity_series_code` | FK → series.csv | driving commodity |
| `content_kg` | decimal(10,4) | commodity content per unit |
| `freight_kg` | decimal(10,4) | chargeable shipping weight per unit |
| `overhead_pct` | decimal(5,2) | overhead applied on top of commodity+freight |
| `list_price_usd` | decimal(10,2) | |
| `supplier` | string | synthetic supplier name |

## Deterministic extension rule (implemented by `MarketSeeder`)

Baseline history ends at the last `price_date` in `baseline_prices.csv`. From
the next day up to "today" the service extends each series with a **seeded
random walk** so every environment generates identical values:

- RNG: `java.util.Random(seed)` per step, where
  `seed = series_code.hashCode ^ epochDay` (epochDay of the date generated).
- Step: `value(d) = max(value(d-1) × (1 + σ × z), 0.01)` with `z` the first
  Gaussian from the step RNG and a fixed per-series daily σ:
  `SALMON_NOK_KG 0.012, SHRIMP_USD_KG 0.008, SOYBEAN_OIL_USD_KG 0.010,
  SUGAR_USD_KG 0.011, COTTON_USD_KG 0.009, DREWRY_WCI_USD_FEU 0.015,
  USD_NOK 0.004`.
- Rows are inserted with `source='synthetic'` and never overwrite existing
  rows (`ON CONFLICT DO NOTHING`), so manually pulled values win.

## Margin model (locked by OTD-15)

```
commodity_cost_usd = commodity_price(native) × fx_to_usd × content_kg   (NOK series: ÷ USD_NOK, most recent FX ≤ date)
freight_cost_usd   = (DREWRY_WCI_USD_FEU / 25000 kg-per-FEU) × freight_kg
cogs_usd           = (commodity_cost_usd + freight_cost_usd) × (1 + overhead_pct/100)
margin_pct         = (list_price_usd − cogs_usd) / list_price_usd × 100
```
