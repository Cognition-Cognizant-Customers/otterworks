# cron-search — Atlas Search replaces the weekly reindex

Unit `cron-search`. Disposition **delete**: the legacy weekly job
`etl/scripts/search_reindex_weekly.py` (319 LOC, crontab `0 4 * * 0`) deleted and
recreated two search indexes every Sunday. Atlas Search maintains its indexes
continuously from the collection change stream, so the capability the job existed
to provide is native to the target platform and the job has no replacement
schedule — there is none in this unit, and none is needed.

## Target objects

| Object | Owner |
| --- | --- |
| `ow_tp_demo.documents`, `ow_tp_demo.files` | parent (already bootstrapped) |
| Atlas Search index `default` on `ow_tp_demo.documents` | this unit's definition, parent-applied |
| Atlas Search index `default` on `ow_tp_demo.files` | this unit's definition, parent-applied |

Two indexes on the free-tier M0, which allows three. No cluster, no additional
index, no hourly-cost resource is created by this unit.

Definitions as code:

- `search-indexes/documents.default.json`
- `search-indexes/files.default.json`

## Attribute-role mapping

The legacy job patched three MeiliSearch settings per index. Every role is
carried over; the Atlas type is what makes the role work, so the mapping is
type-by-type rather than a claim of equivalence.

- **searchable** → an analyzed `string` mapping (`lucene.standard`), which is
  what the `$search` `text` operator queries.
- **filterable** → an exact mapping: `token` with `normalizer: "none"` for
  strings (MeiliSearch filter equality is exact and case-sensitive, and the
  `$search` `equals` operator requires `token`), `date` for timestamps,
  `number` for numerics.
- **sortable** → the same exact mapping; `date`/`number` are sortable as
  indexed, and `$sort` after `$search` orders on the stored value.

A field in two roles gets two mappings (a `string` for search plus a `token` for
filtering) — `tags` on both collections and `mime_type` on `files`.

### `ow_tp_demo.documents`

| MeiliSearch role | Attribute | Atlas Search mapping |
| --- | --- | --- |
| searchable | `title` | `string` (lucene.standard) |
| searchable | `content` | `string` (lucene.standard) |
| searchable, filterable | `tags` | `string` + `token` |
| filterable | `type` | `token` |
| filterable | `owner_id` | `token` |
| filterable, sortable | `created_at` | `date` |
| filterable, sortable | `updated_at` | `date` |
| — (identity) | `id` | `token` |

### `ow_tp_demo.files`

| MeiliSearch role | Attribute | Atlas Search mapping |
| --- | --- | --- |
| searchable | `name` | `string` (lucene.standard) |
| searchable, filterable | `tags` | `string` + `token` |
| searchable, filterable | `mime_type` | `string` + `token` |
| filterable | `type` | `token` |
| filterable | `owner_id` | `token` |
| filterable | `folder_id` | `token` |
| sortable | `size` | `number` |
| filterable, sortable | `created_at` | `date` |
| filterable, sortable | `updated_at` | `date` |
| — (identity) | `id` | `token` |

`dynamic: false` on both indexes: the mapped fields are exactly the roles the
contract fixes, so an extra field arriving on a source record is stored and
attributed but never silently changes the query surface.

MeiliSearch **ranking rules** have no field-level equivalent and are deliberately
not mapped; relevance ordering and scores are a declared coverage gap
(`meili_ranking_rules_not_portable`), and every golden query is compared as a
document-id **set**. MeiliSearch typo tolerance and Atlas `fuzzy` are also not
equivalent (`typo_tolerance_semantics`); the golden query set does not exercise
typo behavior.

## Ingest: source of truth in, no rebuild ever

`scripts/tp_atlas/cronbox_search_ingest.py` transforms source records and
upserts them per record (`ReplaceOne(..., upsert=True)` keyed on the source id),
so the collections track the source of truth continuously at per-record
granularity — the contract's `trigger_granularity`. There is no delete-index,
no recreate, no bulk swap, and no schedule. Consequences of that shape:

- **Empty input is empty input, not a rebuild trigger.** With no records the
  ingest performs no writes and a populated collection and its index are left
  untouched (`empty_input_semantics`).
- **Reruns are identical.** The upsert is keyed on the id, so re-ingesting the
  same corpus converges to the same document set.
- **Encoding is preserved byte-for-byte.** UTF-8 text is stored as-is
  (`Δocument ☕` and `Fichier Δ ☕` stay searchable); a value that is not valid
  UTF-8 is stored as BSON binary under `<field>__binary` and left out of the
  analyzed path, because an analyzer cannot index bytes that are not text.
- **Malformed records are attributed, never indexed blind.** A record with a
  missing or empty id is excluded and reported with its source position; nothing
  is ever written under a blank id.

## Commands

```bash
# offline: validate the definitions preserve every attribute role
python3 scripts/tp_atlas/cronbox_search_indexes.py --dry-run

# offline: transform the fixture corpus and print the attribution report
python3 scripts/tp_atlas/cronbox_search_ingest.py --collection documents \
  --source-url http://localhost:8088 --dry-run

# child self-check: golden query set evaluated over the fixture corpus
make tp-search-recon-fixture

# parent, live validation window: read-only recon against deployed Atlas
make tp-search-recon
```

`--apply` on the index script and the live recon are **parent-owned**; this unit
performs no Atlas write.
