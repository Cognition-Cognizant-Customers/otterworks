# Recon — `mongo-invoices` (NS=`demo`)

**Verdict: PASS** — every number below is recomputed from Atlas (`ow_tp_demo`) and compared against the seed manifest `testdata/legacy/manifests/demo.json` (SCALE=`demo`, generated `2026-08-01T00:00:00Z`, runtime state, never committed).

Reconciled at `2026-08-15T23:53:15Z`.

## Counts

| Metric | From Atlas | Expected | |
|---|---|---|---|
| `invoices` documents | 18,750 | 18,750 | ok |
| Embedded lines across all invoices | 149,963 | 149,963 | ok |
| `invoice_lines_orphaned` documents | 37 | 37 | ok |
| Embedded + orphaned lines | 150,000 | 150,000 | ok |
| Source-parity checksum | `88a66751f0b08b476b492105a2efc537` | `88a66751f0b08b476b492105a2efc537` | ok |
| Invoices with zero lines | 5 | 5 | ok |
| Invoices with fewer than 3 lines | 268 | 268 | ok |

Line fan-out per invoice runs 0–23 (lines are assigned to a uniformly random header in the source estate, so the distribution is Poisson-like, not the 3–25 the contract text assumed). All 5 line-less headers are migrated with `lines: []`, `lineCount: 0` and `lineTotal: NumberDecimal("0.00")` — an empty header is not an anomaly and is never quarantined.

Checksum definition: md5 over `"{lineId}:{amount:.2f}\n"` for all 150,000 lines (embedded **and** orphaned) in ascending `lineId` order. Summed line amount: `1855870025.91`.

## Checks

| Check | Actual | Expected | Result |
|---|---|---|---|
| invoices documents == manifest INVOICE_HEADER rows | `18750` | `18750` | PASS |
| invoices lineCount field sums to the embedded lines | `149963` | `149963` | PASS |
| embedded + orphaned lines == manifest INVOICE_LINE rows | `150000` | `150000` | PASS |
| embedded lines == manifest INVOICE_LINE rows minus the planted orphans | `149963` | `149963` | PASS |
| checksum stream covers every line | `150000` | `150000` | PASS |
| zero-line invoices carry lines: [] and lineTotal 0.00 | `5` | `5` | PASS |
| orphan documents == planted orphaned_rows anomaly | `37` | `37` | PASS |
| every quarantined row is a planted orphan (ids re-derived, not counted) | `0` | `0` | PASS |
| every quarantined row carries quarantine_reason missing_header | `0` | `0` | PASS |
| no dangling INVOICE_ID resolves to a header | `0` | `0` | PASS |
| no orphan line is also embedded in an invoice | `0` | `0` | PASS |
| every line carries an amount (the checksum covers all of them) | `0` | `0` | PASS |
| source-parity checksum == manifest checksum | `88a66751f0b08b476b492105a2efc537` | `88a66751f0b08b476b492105a2efc537` | PASS |
| invoices with zero lines (migrated, not quarantined) | `5` | `5` | PASS |
| invoices with fewer than 3 lines | `268` | `268` | PASS |

## Anomaly ledger — `orphaned_rows` on `oracle.OW_BILLING.INVOICE_LINE`

Manifest plants **37**; Atlas holds **37** in `invoice_lines_orphaned` with quarantine reason(s) ['missing_header'], 0 of them failing the planted `<NS>-GHOST-<i>` id recipe, 0 of them also embedded in an invoice, 0 carrying no `INVOICE_ID` at all, and 0 of the 37 distinct `INVOICE_ID`s they point at resolving to a header document.

| # | `LINE_ID` | dangling `INVOICE_ID` | `INVOICE_NO` | amount |
|---|---|---|---|---|
| 1 | `0e7a3dc2-d003-464a-9c2b-14125c79cbe9` | `b73e6da9-a875-066f-bc70-ce7b67a78f78` | `DEMO-GHOST-000096153` | 1339.44 |
| 2 | `0fbd756a-1d5f-bdfb-4cb9-81d2cbb4fee4` | `e56a27b8-9d69-797f-3900-036730ccdfe9` | `DEMO-GHOST-000114112` | 5158.36 |
| 3 | `0fe3f0d5-2e0d-87dc-0933-f84887549f80` | `a087ab39-88c0-5e0d-19e3-ee9f9515ae1d` | `DEMO-GHOST-000135842` | 2434.04 |
| 4 | `1c435cb0-171d-69c3-f600-a5f4bc7e90fe` | `c88972ef-3117-3cfc-39a0-9b14135636d2` | `DEMO-GHOST-000030943` | 20943.53 |
| 5 | `21367220-1d41-e396-0f3b-dbf2ae5d91e9` | `dca8aea8-95d4-e773-9016-c8a86c7381aa` | `DEMO-GHOST-000130011` | 24990.59 |
| 6 | `4b34c0f1-8476-f678-add0-b3f921a7cd5f` | `1fa280ee-1f5a-afee-1a6b-50f70c3fdfec` | `DEMO-GHOST-000102611` | 4094.63 |
| 7 | `4c826f11-c733-eac0-9318-29ae919beec4` | `72707e74-5fc9-67ff-b100-713f577fe063` | `DEMO-GHOST-000131314` | 23131.02 |
| 8 | `50526783-22bf-ae7c-f6ba-4f8aa111b611` | `a961af58-f536-e8f6-ece5-73498cecf4f5` | `DEMO-GHOST-000070864` | 969.96 |
| 9 | `57f7b9b0-9e76-4439-48b9-3b71025322a4` | `15f97fdf-21a5-21ce-057d-e7fe9c0d329a` | `DEMO-GHOST-000010946` | 269.34 |
| 10 | `670cd75f-cf78-0b95-5426-a95484ef2a0e` | `6f4cf14a-243c-b3bc-c58e-c7eb78c2c123` | `DEMO-GHOST-000132355` | 4327.76 |
| 11 | `7242b80f-45f6-4bbf-3153-bb2ce0c6441e` | `86bf978d-9d57-91a7-b877-c30b84f0f800` | `DEMO-GHOST-000142406` | 14661.25 |
| 12 | `78572c4c-b635-7daa-183e-5490237ab052` | `0b4bcca1-e49e-d12c-94c0-9156218a6a5f` | `DEMO-GHOST-000092003` | 1580.78 |
| 13 | `78f6a5bb-18f3-bf6e-2aa3-92f241f6c5b1` | `bab6f802-7fdb-72a3-8215-f43dc900c98a` | `DEMO-GHOST-000112719` | 129.85 |
| 14 | `8c12956a-e778-88e6-67d5-ad44d3b540c3` | `c596beeb-fcba-2e4b-e2d2-6ce00627a132` | `DEMO-GHOST-000074944` | 10563.50 |
| 15 | `8c95dd3d-97ed-420d-1965-cf320c34fb84` | `3917dde8-e0e3-5022-5143-2907c389c943` | `DEMO-GHOST-000004022` | 7135.97 |
| 16 | `8e85a5db-53c9-a05b-3049-752bb6632003` | `f66d92af-39ca-05f3-f75b-25ec557c5fbc` | `DEMO-GHOST-000117620` | 1997.11 |
| 17 | `90ddc15d-a71e-ea73-9392-338da7255605` | `9b94b0ab-8455-3453-a3e3-629f00a97040` | `DEMO-GHOST-000005672` | 6769.85 |
| 18 | `95d5043f-e481-1de8-cd92-bd76bc09dc19` | `d3fceb54-e51d-d48a-2c81-ecfa847b077e` | `DEMO-GHOST-000139723` | 2.32 |
| 19 | `97f42ec8-39e7-19f2-9a76-84414644efc3` | `7b2f44c8-8256-53e1-37e5-8ddbdf6d4705` | `DEMO-GHOST-000089086` | 1164.50 |
| 20 | `982209a3-c282-e02f-83ec-ee9d0b474d7f` | `de1c1fce-9389-e3a5-f84b-34fad5ddace0` | `DEMO-GHOST-000144001` | 21866.52 |
| 21 | `9dd7ea2b-091d-c45f-abd6-2a2fb2e43891` | `a9b1f407-bc28-6ef2-7b39-4048f77cf01d` | `DEMO-GHOST-000072668` | 40423.94 |
| 22 | `9f1600ed-b570-0274-d42e-c1dab91038c4` | `c58a51b7-5ed0-c49f-2c16-cabe502c70f2` | `DEMO-GHOST-000031293` | 15663.26 |
| 23 | `a53ea126-8621-3e3c-a8be-88435d7ca266` | `eb6ed01e-3939-0456-7d5a-93fa3bb73a39` | `DEMO-GHOST-000010247` | 4404.82 |
| 24 | `aef7cf16-4fdc-e2bc-7de5-ddbc1782c43a` | `4693ffca-770f-93b1-fd15-53dea8f96514` | `DEMO-GHOST-000011765` | 584.17 |
| 25 | `b0479162-4897-2f54-82d5-c08044b3a1a7` | `3102c632-a2a7-009e-acfd-d45cd3057b26` | `DEMO-GHOST-000013973` | 10640.30 |
| 26 | `b259311b-5098-d0f9-a658-585d6eff8f81` | `a87d09e3-8772-9071-ac83-ed73c79ac45b` | `DEMO-GHOST-000094614` | 9739.31 |
| 27 | `bddd1f4a-cad4-50cb-a066-9ee465981778` | `66e49dad-f229-f865-602e-f4a5119211fb` | `DEMO-GHOST-000142402` | 29498.18 |
| 28 | `c08e04e0-0a1c-27fc-12a4-802a2421036c` | `64d5ab87-522f-24e4-10ab-6aca81a4f768` | `DEMO-GHOST-000099517` | 17849.25 |
| 29 | `c1008e17-8f78-a845-2ba2-bccd154a0b9c` | `227479c5-9ecc-7f3c-c794-54cf33844f95` | `DEMO-GHOST-000012515` | 9875.95 |
| 30 | `c4a55285-efd8-3c27-d21e-ab29cdf11eae` | `2b5a56b7-07ef-6320-65bb-b6432ace3fcf` | `DEMO-GHOST-000039076` | 12430.94 |
| 31 | `c8ee4242-005d-bf23-d5a0-d8c906ef6f3f` | `50242b74-cc87-5433-b344-3ab031368e28` | `DEMO-GHOST-000050082` | 3559.33 |
| 32 | `cfb214f7-cf89-751a-ed4c-8ef1bfa2665d` | `3e7a1f88-97f8-6cd4-8bea-b3aa1e9c14d6` | `DEMO-GHOST-000047775` | 4221.54 |
| 33 | `d032629f-368d-fc21-534c-13866e7dd45a` | `fe77a64b-7e71-0211-85d6-e539cd5d5f60` | `DEMO-GHOST-000064425` | 329.62 |
| 34 | `da96e215-3481-a34b-2d84-c4a8f67ee7e3` | `34ae08ff-c0ff-399b-5f2e-53826a387b73` | `DEMO-GHOST-000089499` | 4842.99 |
| 35 | `e77fefbf-059b-62cb-f889-1c7296f933b5` | `3be8d384-f25a-6bf1-a0aa-b049cb718a94` | `DEMO-GHOST-000084889` | 7368.46 |
| 36 | `f032b198-0c91-8ccd-a23d-3804994faacd` | `6c8daa53-f025-cf26-d0b8-54cf07327d64` | `DEMO-GHOST-000123485` | 36764.48 |
| 37 | `f2053077-7f8a-ea53-b9ed-7c5e2b9fce88` | `a75ef128-b6fc-e80a-30eb-92538f670fb3` | `DEMO-GHOST-000079271` | 28392.20 |
