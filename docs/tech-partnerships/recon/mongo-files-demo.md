# Recon — `mongo-files` (ns `demo`)

Source of truth: `testdata/legacy/manifests/demo.json` (seed `714559852`), the
before-state manifest written by `make seed-legacy` and independently
verified by `make seed-legacy-validate NS=demo` (15/15).
Every Atlas number below is recomputed from `ow_tp_demo.files` by
`migrations/mongodb/files/recon.py` — never from the DynamoDB source.

Generated: 2026-08-15T22:56:42Z

## Counts and checksum

| metric            | manifest (source)                | atlas (recomputed)               |
|-------------------|----------------------------------|----------------------------------|
| documents         | 10000                            | 10000                            |
| checksum          | db614663b2c6d41141cae82261b416d5 | db614663b2c6d41141cae82261b416d5 |
| orphaned metadata | 40                               | 40                               |

Checksum definition: order-independent sum of per-line md5 digests mod
2^128, one line per document as `<_id>|<sizeBytes>|<storage.s3Key>` with
`sizeBytes` read back as a BSON int64 and rendered as a plain integer.

## Checks

| check                                                | status   | detail                                                                           |
|------------------------------------------------------|----------|----------------------------------------------------------------------------------|
| dynamodb.file-metadata → files documents             | PASS     | atlas=10000 manifest=10000                                                       |
| files holds only tenant 'demo'                       | PASS     | collection=10000 tenant=10000                                                    |
| dynamodb.file-metadata → files checksum              | PASS     | atlas=db614663b2c6d41141cae82261b416d5 manifest=db614663b2c6d41141cae82261b416d5 |
| sizeBytes is BSON int64 in every document            | PASS     | non-int64=0 []                                                                   |
| createdAt/updatedAt are BSON dates                   | PASS     | unparsed=0 []                                                                    |
| _migration provenance on every document              | PASS     | missing=0                                                                        |
| anomaly orphaned_metadata count                      | PASS     | atlas=40 manifest=40                                                             |
| storage.present:false == '<ns>/missing/…' key marker | PASS     | flagged=40 marker_keys=40 symmetric_difference=0                                 |
| orphans carry orphanReason                           | PASS     | reasons=['missing_object_marker']                                                |
| workload indexes present                             | PASS     | indexes=_id_, folder, storage_s3key_unique, tenant_owner, trashed                |

**10/10 checks passed.**

## Anomaly ledger — `orphaned_metadata` (40 of 40 expected)

Flag-in-place, not quarantine: each item below is migrated as a normal
document carrying `storage.present: false` and
`storage.orphanReason: "missing_object_marker"`. The signal is the
`demo/missing/…` key marker only — no S3 objects exist for any
seeded item, so object existence is never consulted.

| _id                                  | storage.s3Key                                                                          |
|--------------------------------------|----------------------------------------------------------------------------------------|
| 09469cfc-94b5-40b7-95cd-39d4f2d610f1 | demo/missing/3fd5b9e3-543a-48dd-bcc7-26864c2d9b2b/09469cfc-94b5-40b7-95cd-39d4f2d610f1 |
| 0bea2d95-5c0b-4905-a77f-cfe8fa8ef266 | demo/missing/feb95b65-da14-4628-a3d4-c8dd68315412/0bea2d95-5c0b-4905-a77f-cfe8fa8ef266 |
| 1537f253-8392-4835-960b-7745159186b9 | demo/missing/2de578f0-49ae-43eb-b377-098f25649f83/1537f253-8392-4835-960b-7745159186b9 |
| 193c5636-a5d3-4b27-80f3-9ad9cc0db8ad | demo/missing/c1b05ebe-4c6f-447a-ad9d-d72b250cb39c/193c5636-a5d3-4b27-80f3-9ad9cc0db8ad |
| 1e94ec4c-daa3-4312-b9b0-a25a2e2dc5a4 | demo/missing/50d8c109-c483-47d4-8f13-c79233570636/1e94ec4c-daa3-4312-b9b0-a25a2e2dc5a4 |
| 22bbaccf-7d13-43b5-85b6-e39c24ba3010 | demo/missing/1beb069a-3a21-448e-b264-3f027c926187/22bbaccf-7d13-43b5-85b6-e39c24ba3010 |
| 2728be0a-4ea7-4193-9ea2-bf01730362e2 | demo/missing/c628d09c-5076-4d31-9038-64af52748ad6/2728be0a-4ea7-4193-9ea2-bf01730362e2 |
| 4fad10a0-6596-4702-82cf-0a3c61adbc88 | demo/missing/1beb069a-3a21-448e-b264-3f027c926187/4fad10a0-6596-4702-82cf-0a3c61adbc88 |
| 5978a85e-d875-4c5e-8330-bd84d7185772 | demo/missing/11257f93-a389-4064-a3e6-dff58046ff47/5978a85e-d875-4c5e-8330-bd84d7185772 |
| 5eb7c396-1a06-4de5-b3b6-e40612983e07 | demo/missing/7c79a0c3-89c6-4ab7-adb1-a8442bbb2d7c/5eb7c396-1a06-4de5-b3b6-e40612983e07 |
| 5ee20541-ad1b-4719-b12f-2d1741cb7b50 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/5ee20541-ad1b-4719-b12f-2d1741cb7b50 |
| 638a536f-f10c-445b-9fad-dc2c46fe27cd | demo/missing/cc6ddb3d-e7b2-4eca-900a-8231c41441e9/638a536f-f10c-445b-9fad-dc2c46fe27cd |
| 68ae546a-4ba4-4d74-aa97-8ffd318a7d39 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/68ae546a-4ba4-4d74-aa97-8ffd318a7d39 |
| 69ebada0-9b3a-47f3-861f-8a9b004bdd01 | demo/missing/0bfccc9c-ac37-4c25-903f-c18b196dab0b/69ebada0-9b3a-47f3-861f-8a9b004bdd01 |
| 75a55eff-9502-4d6b-8cb1-f08adc2f8757 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/75a55eff-9502-4d6b-8cb1-f08adc2f8757 |
| 7729c46f-ade2-4e77-9189-4f0d3e214951 | demo/missing/2f35134b-3099-49fe-b8e0-68b53d79cbb5/7729c46f-ade2-4e77-9189-4f0d3e214951 |
| 7926f16a-a543-4281-ac1f-6c7a17c6b26e | demo/missing/f0343cc0-7304-4938-8c37-8f054fa6e3b9/7926f16a-a543-4281-ac1f-6c7a17c6b26e |
| 7b95f7a6-7d63-4f5c-9ac4-f92e970fded4 | demo/missing/1beb069a-3a21-448e-b264-3f027c926187/7b95f7a6-7d63-4f5c-9ac4-f92e970fded4 |
| 7c6096ae-6dea-4ff9-af1d-c032b828f925 | demo/missing/4815e977-4a11-4bc8-bff2-7a353d7f3890/7c6096ae-6dea-4ff9-af1d-c032b828f925 |
| 7c98ea5a-9b9b-4b3d-a1ad-4cabc54203e0 | demo/missing/19e22931-543b-4b42-9392-415be38cd9ef/7c98ea5a-9b9b-4b3d-a1ad-4cabc54203e0 |
| 855830df-b727-4a3d-b3b4-5a28257bca15 | demo/missing/2fd168e5-48e6-4fbf-9c9a-e300e90d2e8c/855830df-b727-4a3d-b3b4-5a28257bca15 |
| 87f85b2d-c10f-4684-b23e-2dbd47a1f403 | demo/missing/4815e977-4a11-4bc8-bff2-7a353d7f3890/87f85b2d-c10f-4684-b23e-2dbd47a1f403 |
| 94d2bd79-a773-4c49-9158-6a2da6d0dbcb | demo/missing/c32547dd-1869-473e-ac70-b67a4d19b943/94d2bd79-a773-4c49-9158-6a2da6d0dbcb |
| 9becd831-0b0d-43c6-a037-8fac0dd9ff54 | demo/missing/9419bd2f-5514-4c3a-b74f-9585c3ca2817/9becd831-0b0d-43c6-a037-8fac0dd9ff54 |
| 9d84308b-9d65-4d22-af39-17850fbd6b30 | demo/missing/0bfccc9c-ac37-4c25-903f-c18b196dab0b/9d84308b-9d65-4d22-af39-17850fbd6b30 |
| ab7c7723-1407-4509-a735-1e93430456a2 | demo/missing/d4c0d34e-8a1d-488d-97e0-0828b60ca38a/ab7c7723-1407-4509-a735-1e93430456a2 |
| ae07a85c-019d-4a6f-bc8c-5ce34b8095fa | demo/missing/bf5822df-4d04-4648-9991-d20361c00c3c/ae07a85c-019d-4a6f-bc8c-5ce34b8095fa |
| afef0026-1a90-4133-8d3a-307c9fbbaa8a | demo/missing/50d8c109-c483-47d4-8f13-c79233570636/afef0026-1a90-4133-8d3a-307c9fbbaa8a |
| b461b24a-eb45-47a6-a71d-036a8deb3edc | demo/missing/5e7eb38d-aad7-43c1-87cb-4541deda8bba/b461b24a-eb45-47a6-a71d-036a8deb3edc |
| b90c47cc-29b5-4dc2-b705-bb4db20a9ac5 | demo/missing/a24ca2bf-675c-404c-9398-324c170f1bf9/b90c47cc-29b5-4dc2-b705-bb4db20a9ac5 |
| caf96d17-eb4b-4ea9-b622-29940983926a | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/caf96d17-eb4b-4ea9-b622-29940983926a |
| cbe7fb30-3b41-4d38-9cd4-ce5065306e04 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/cbe7fb30-3b41-4d38-9cd4-ce5065306e04 |
| daee66fc-51fb-432c-b522-8c858801b673 | demo/missing/a096dc53-72e2-4d14-a1b5-6fed0d5929b9/daee66fc-51fb-432c-b522-8c858801b673 |
| e1747410-571d-48b2-8243-34fece905486 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/e1747410-571d-48b2-8243-34fece905486 |
| e2cc82b9-f2b0-401d-8766-5869bf36d1d3 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/e2cc82b9-f2b0-401d-8766-5869bf36d1d3 |
| e5194447-f0a5-4bc3-9f57-d8bb5916cce6 | demo/missing/1beb069a-3a21-448e-b264-3f027c926187/e5194447-f0a5-4bc3-9f57-d8bb5916cce6 |
| fd0c5f0b-a57e-470e-afe4-ef1b461b859e | demo/missing/dfa34769-eee9-4bce-9a29-95f8d3320a2e/fd0c5f0b-a57e-470e-afe4-ef1b461b859e |
| fdbe4ce4-e958-47e7-87cc-f9c88dbb9f96 | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/fdbe4ce4-e958-47e7-87cc-f9c88dbb9f96 |
| fed95d77-150e-4587-a75b-2f2b23bb0e1c | demo/missing/818d442c-d231-48a9-b1ed-65a14fb0b0f9/fed95d77-150e-4587-a75b-2f2b23bb0e1c |
| ff00ab82-f830-4c99-97bf-710c23d16fe6 | demo/missing/0da3ce17-b89b-4adc-a63e-b0485d627cd8/ff00ab82-f830-4c99-97bf-710c23d16fe6 |

## Indexes

`_id_`, `folder`, `storage_s3key_unique`, `tenant_owner`, `trashed`
