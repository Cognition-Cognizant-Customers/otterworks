# Recon — `mongo-customers` (NS=demo)

- Target: `ow_tp_demo.customers` / `ow_tp_demo.customers_quarantine`
- Source of truth: `testdata/legacy/manifests/demo.json` (generated 2026-08-01T00:00:00Z)
- Recomputed from Atlas at 2026-08-15T23:17:14Z
- Verdict: **PASS** (11/11 checks)

## Counts and checksum

| Check | Expected | From Atlas | Result | How |
|---|---|---|---|---|
| customers documents | `25000` | `25000` | PASS | manifest rows vs. count_documents in Atlas |
| source-parity checksum | `4f92feef2ad58dbab30e289957931928` | `4f92feef2ad58dbab30e289957931928` | PASS | ordered md5 over 25000 documents sorted by _id |
| EAV source rows | `8333` | `8333` | PASS | recomputed from attributes + legacy.attributeConflicts |
| EAV folded keys | `8141` | `8141` | PASS | recomputed from attributes + legacy.attributeConflicts |
| EAV conflicts | `192` | `192` | PASS | recomputed from attributes + legacy.attributeConflicts |
| EAV customers with attributes | `7075` | `7075` | PASS | recomputed from attributes + legacy.attributeConflicts |
| quarantine ledger documents | `81` | `81` | PASS | no stale or duplicated ledger entries after reruns |
| anomaly dirty_dates (SIGNUP_DT) | `50` | `50` | PASS | quarantine ledger, fields=['SIGNUP_DT'] |
| anomaly dirty_dates: raw value preserved on the customer | `50` | `50` | PASS | customer document still present with _quarantine.<field> |
| anomaly malformed_csv_lists (RELATED_ACCT_IDS) | `31` | `31` | PASS | quarantine ledger, fields=['RELATED_ACCT_IDS'] |
| anomaly malformed_csv_lists: raw value preserved on the customer | `31` | `31` | PASS | customer document still present with _quarantine.<field> |

## Anomaly ledger

Quarantined *fields*: the customer document is still migrated and counted, with the offending value preserved raw under `_quarantine.<COLUMN>` and the parsed field omitted.

### `dirty_dates` — 50 (fields: SIGNUP_DT)

First 10 affected `CUST_ID`s:
- `0931e05f-7a53-91fb-39f3-f10317fbedba`
- `0b72b826-7a70-89ad-8956-4d1d1287ce03`
- `0cd52db0-39a5-f76a-9624-10618b8e5ee3`
- `0ceaf652-e792-4f5d-02c4-1a5650f94bc5`
- `0e20f5f2-bb03-f430-92ea-8819b8370c76`
- `15d8d526-f39f-66d5-60fa-8e2c66aca1d8`
- `200c44cb-eb01-dd1a-1aa5-8436fc358370`
- `236ba540-6dbd-01f7-f5d6-e440ca6d55af`
- `35399046-a141-1f6a-1028-746cfcd6a165`
- `38a1a82d-9b1e-dc6f-a1b4-30c0c51006dc`

<details><summary>all 50 affected `CUST_ID`s</summary>

```
0931e05f-7a53-91fb-39f3-f10317fbedba
0b72b826-7a70-89ad-8956-4d1d1287ce03
0cd52db0-39a5-f76a-9624-10618b8e5ee3
0ceaf652-e792-4f5d-02c4-1a5650f94bc5
0e20f5f2-bb03-f430-92ea-8819b8370c76
15d8d526-f39f-66d5-60fa-8e2c66aca1d8
200c44cb-eb01-dd1a-1aa5-8436fc358370
236ba540-6dbd-01f7-f5d6-e440ca6d55af
35399046-a141-1f6a-1028-746cfcd6a165
38a1a82d-9b1e-dc6f-a1b4-30c0c51006dc
461df492-edf6-f1bd-a4fc-ab55b615ac1a
52f7c36b-4e0a-b6ee-63ca-c8d9dc570349
54208580-584d-7abc-0f31-5362160c3a06
58be9596-13c2-fda7-c5c9-709916eb828c
590510fc-0773-299a-2979-b7ae3a5d314a
5c9ebd25-bdde-0d89-7591-2cb0e9f6e8fe
6d6cb3e3-8e62-a268-4d75-1fae996b14f1
6e19e052-a225-b5f6-cdb4-633760d51009
834eee22-fbc2-b907-16e3-570115cc9688
84204704-821c-b4a4-ce1d-4c476292ffbf
85812c6d-f229-8790-fe4d-134e5a522e89
865d5742-e7be-162e-4b0c-253ddd333e39
881276a6-e295-efb6-8396-5d898a7bd035
92e0161e-a326-6dee-c053-3ce963076ba3
98c0171f-b4f8-7213-adb2-071313df499f
99f947dc-028d-4a1b-7dae-70909c02c1d5
9c0f3544-9f4e-ca2f-c2da-db1eb70b902d
9c1e0305-08e5-1a0f-2d5c-866e6b81b30b
a0fcdd65-47a4-9802-83c1-fadcd3116e47
a15517bb-4d6c-9c1d-c983-5a65855181e1
a2f182a2-7909-1db9-8665-55643905569d
a468f4dd-b451-e118-ae9e-ec8568f18ef0
a46aa7a9-0d47-826f-23ff-9ad206a0cf41
abc6f7e6-d7dc-82f3-242c-edf12d0082b7
abde3178-831b-83f6-cf0d-4144f3c4fdb3
ac1994eb-9ceb-6c7b-53c8-5d2abe52897a
b4e457a6-72af-71e9-832a-8752868fbc52
b599f339-d672-2512-cfa8-2b07469e1c5f
c5634db2-dd3b-7cfe-e1b5-d9718a002c8c
cb07750d-441c-1dc8-61cf-8dd5ef99ca4e
ccc1495b-3727-2502-5c20-b58130f62efb
dc769d6b-48cc-0274-78db-c23ebab51411
e81efdcf-4c77-7509-95b5-4ff0132d4451
ea7ee282-ce1f-14d8-94d2-83a7c40144d1
eda0cdff-79f3-188e-9658-248ea1740e04
f014d019-bee4-b087-60a6-42f277518aa3
f0a488e0-7635-572e-c8cd-fe5d7192b74b
fa52f588-cd41-8c0d-6c97-6f72dd440ff8
fc090edd-ac26-063c-4bcd-3c23276d937a
fd6c7a7b-9b6b-63a5-f92e-c90346616364
```

</details>

### `malformed_csv_lists` — 31 (fields: RELATED_ACCT_IDS)

First 10 affected `CUST_ID`s:
- `117cdf47-d1e2-7c0a-d0be-5c1a9355d07a`
- `2a5ce91c-e8dd-c7ec-1318-bb44f598f5a7`
- `316d759b-4523-13bc-37c3-5033233e888b`
- `3a455e63-97ee-2c4d-44c5-4dcdbda1fb3e`
- `3f023357-c47c-6606-7dbc-b7ac4cb1d50d`
- `3ff02cc2-0541-5937-b49c-d79aa940820d`
- `5be573f9-e260-a179-d1d5-edbe2812c15f`
- `5d606a03-220d-0256-bd1f-0cfb813b7f9d`
- `5eb5b92c-b586-025c-44fe-ce836c0310f6`
- `6417fd5e-55c7-7eec-0310-9617884062dc`

<details><summary>all 31 affected `CUST_ID`s</summary>

```
117cdf47-d1e2-7c0a-d0be-5c1a9355d07a
2a5ce91c-e8dd-c7ec-1318-bb44f598f5a7
316d759b-4523-13bc-37c3-5033233e888b
3a455e63-97ee-2c4d-44c5-4dcdbda1fb3e
3f023357-c47c-6606-7dbc-b7ac4cb1d50d
3ff02cc2-0541-5937-b49c-d79aa940820d
5be573f9-e260-a179-d1d5-edbe2812c15f
5d606a03-220d-0256-bd1f-0cfb813b7f9d
5eb5b92c-b586-025c-44fe-ce836c0310f6
6417fd5e-55c7-7eec-0310-9617884062dc
69cb7a0a-230d-6198-2b2b-d14c3a4178fe
6cf74c05-d22d-0a80-404c-c9ebcc0a2b52
732a88ad-1f1d-6994-2a64-fe88854b6411
77701b4a-4962-e19d-90f4-34bc8e2f4731
7c77eb7c-a707-fd40-9104-cfd1e487e54f
7e82b995-d89d-5d6a-a182-aeab38db5ccc
8aaf7462-938a-9cd9-44b2-4244bf861e29
8d7e8797-3f19-0cbf-45b4-ed19a23e36f1
9bb7fdb8-4af5-fde1-5eaa-6deceef48075
9c0bdd70-a8ef-5a59-bcf6-0789bad445bd
9c62762b-d911-5bfb-c785-382c315962e9
a25e7aef-ba8b-cd79-75b9-58998da62a54
adcc7ab6-8134-270f-c8e7-960152887068
c548da20-236d-aaff-7c45-b111b4debbf8
c7d394fb-3e0f-0ca6-035f-e46197f34d53
cab9fcd5-b4e4-9582-5edd-2206f8d326b1
dd325ff7-7afe-1ad5-d781-7a5fc9e3156a
e392a00e-b663-ab29-8ddd-ec093c91b54f
e3c490e3-b883-6419-67d0-ecc9ee270185
e6ccd836-9fe6-b04b-acd6-6162bbd7a7ca
f48ed8a5-4961-b4dc-9164-dc3729fdcef3
```

</details>

