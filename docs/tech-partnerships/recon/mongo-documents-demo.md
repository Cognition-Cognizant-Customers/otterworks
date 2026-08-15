# Recon — `mongo-documents` (ns=`demo`)

Verdict: **PASS** (13/13 checks passed)

- Target: Atlas `ow_tp_demo` (`documents`, `document_snapshots`, `document_snapshots_orphaned`)
- Baseline: `testdata/legacy/manifests/demo.json`, written by `make seed-legacy` from the legacy Postgres estate and independently re-derived from the source by `make seed-legacy-validate NS=demo` (15/15).
- Every count and checksum in this report is recomputed **from Atlas** by streaming the migrated collections; nothing is read back from Postgres.
- Checksums use the manifest's order-independent md5-sum (`testdata/legacy/legacy_common.Checksum`) over the contract lines `{_id}|{declaredVersion}|{wordCount}`, `{_id}|{versionNumber}` and `{snapshotId}|{documentId}`.

## Counts and checksums

| check                                                       | status   | detail                                                                                                  |
|-------------------------------------------------------------|----------|---------------------------------------------------------------------------------------------------------|
| postgres.otterworks_demo.documents rows                     | PASS     | atlas=2000 manifest=2000                                                                                |
| postgres.otterworks_demo.documents checksum                 | PASS     | atlas=e70001cf6110014dab6e1d80adb40285 manifest=e70001cf6110014dab6e1d80adb40285                        |
| postgres.otterworks_demo.document_versions rows             | PASS     | atlas=13876 manifest=13876                                                                              |
| postgres.otterworks_demo.document_versions checksum         | PASS     | atlas=13bc033b2780a0569d7f2217e85d7303 manifest=13bc033b2780a0569d7f2217e85d7303                        |
| postgres.otterworks_demo.document_snapshots rows            | PASS     | atlas=390 manifest=390                                                                                  |
| postgres.otterworks_demo.document_snapshots checksum        | PASS     | atlas=abe69084205723d6ad79e825b8c752dd manifest=abe69084205723d6ad79e825b8c752dd                        |
| anomaly version_gaps                                        | PASS     | atlas=10 manifest=10                                                                                    |
| version_gaps are real inconsistencies                       | PASS     | 10/10 gaps carry missing version numbers                                                                |
| anomaly orphaned_snapshots                                  | PASS     | atlas=6 manifest=6                                                                                      |
| orphaned_snapshots quarantined, not dropped                 | PASS     | ow_tp_demo.document_snapshots_orphaned: 6 with quarantine_reason='missing_document'                     |
| every record carries valid _migration                       | PASS     | 0 documents with wrong sourceTable/migratedAt, 0 records in the owned collections with no _migration.ns |
| every migrated snapshot is referenced by its document       | PASS     | unreferenced=0                                                                                          |
| idempotent rerun: Atlas state identical to the previous run | PASS     | every count, checksum and anomaly id matches the earlier recon                                          |

## Atlas totals

| collection                             |   documents |
|----------------------------------------|-------------|
| ow_tp_demo.documents                   |        2000 |
| embedded versions (all documents)      |       13876 |
| ow_tp_demo.document_snapshots          |         384 |
| ow_tp_demo.document_snapshots_orphaned |           6 |
| snapshots total (source set)           |         390 |

## Anomaly ledger

### version_gaps — 10

Documents whose `declaredVersion` (copied verbatim from the source `documents.version`) exceeds the versions actually present. Preserved, not repaired.

| document _id                         |   declaredVersion |   versionCount |   missing |
|--------------------------------------|-------------------|----------------|-----------|
| 4e73d26c-1b17-4c29-a972-723f6a81d5a6 |                12 |             11 |         3 |
| 54f581c5-c84d-4e03-9d49-8a53205d5468 |                 6 |              5 |         6 |
| 55651142-8ab8-4fc3-93f7-2f53b8924e79 |                 3 |              2 |         2 |
| 5908ebb5-dbb7-45f5-b6c7-0e35421f3512 |                 4 |              3 |         2 |
| 83683854-d16e-42ff-a6cd-dba0778c8ebc |                 6 |              5 |         5 |
| 91e0ef71-06e8-4176-8634-b3d37c66435b |                 5 |              4 |         3 |
| 9e712908-6721-4e4a-837d-a235146eb26b |                 9 |              8 |         4 |
| cf385bde-c00f-43f4-ba79-5e734af2a082 |                 3 |              2 |         3 |
| d03d71b1-a7e5-494a-bb12-c1e79866b084 |                10 |              9 |         9 |
| fb541d99-8a4a-4911-a0e3-934d4bbf312d |                 6 |              5 |         2 |

### orphaned_snapshots — 6

Snapshots whose `document_id` has no document. All landed in `ow_tp_demo.document_snapshots_orphaned` with `quarantine_reason: "missing_document"`.

| snapshot _id                         | dangling documentId                  | quarantine_reason   |
|--------------------------------------|--------------------------------------|---------------------|
| 60bfc8b6-ef1b-4ecf-bf98-49267e67054d | c708f278-f52a-4cdc-af0f-580df58b1c2e | missing_document    |
| 9e6c9aa9-2522-4008-b8de-d94a2964917c | e5c8ea48-bab7-4613-95be-1f728308e340 | missing_document    |
| ae8c479d-8de3-46f8-bfc8-a942df697f2f | b74dd4d1-26ab-4214-8324-2d395106fed1 | missing_document    |
| cce98042-94a1-4e9a-98b9-4b1069a76fa1 | 7fdd092a-f910-409b-81d4-fda8d1abf9e2 | missing_document    |
| e0de8004-a5ec-472f-b008-150408e83560 | bd856a34-9754-40b6-8523-02b7a4aa3ea6 | missing_document    |
| e61553f8-e4ea-4caf-b7d7-863fa9547358 | 7d0902fa-48db-4295-b42d-d3ee00dce90d | missing_document    |
