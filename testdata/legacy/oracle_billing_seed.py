#!/usr/bin/env python3
"""Deterministic seeder for the Oracle billing estate (OW_BILLING).

Invoked as `make oracle-billing-seed NS=<ns> [SCALE=demo|full]`. Generates a
realistically skewed legacy estate — power-law tenant sizes with whale
accounts — into CUSTOMER_MASTER / INVOICE_HEADER / INVOICE_LINE /
ENTITY_ATTR_VALUE plus a slice of the core billing tables, plants exactly
enumerable data-quality anomalies, and writes the seed manifest to
testdata/legacy/manifests/<ns>.json per docs/tech-partnerships/README.md.

All randomness derives from a seed computed from NS, so a namespace
reproduces identical row counts and checksums across runs. Re-running a
namespace first deletes that namespace's rows (rows are tagged with the
namespace batch number / name prefix), so runs are idempotent and concurrent
namespaces do not collide.
"""

import argparse
import hashlib
import os
import random
import sys

import oracledb

import legacy_common

GENERATOR_VERSION = "1"

SCALES = {
    # SCALE=demo targets < 15 minutes on a laptop.
    "demo": {"customers": 25_000, "invoice_lines": 150_000, "core_tenants": 60},
    "full": {"customers": 250_000, "invoice_lines": 2_000_000, "core_tenants": 400},
}

FIRST = ["Alex", "Sam", "Jordan", "Casey", "Riley", "Morgan", "Quinn", "Avery",
         "Harper", "Rowan", "Dana", "Jesse", "Kai", "Skyler", "Taylor", "Drew"]
LAST = ["Otter", "Rivera", "Chen", "Okafor", "Novak", "Silva", "Haddad",
        "Larsen", "Kowalski", "Tanaka", "Moreau", "Bhatt", "Egede", "Vance"]
STREETS = ["Main St", "Riverbend Ave", "Cedar Ln", "Industrial Pkwy",
           "Harbor Rd", "Willow Ct", "5th Ave", "Depot St"]
CITIES = [("Springfield", "IL", "62701"), ("Riverton", "WY", "82501"),
          ("Fairview", "TN", "37062"), ("Georgetown", "TX", "78626"),
          ("Clinton", "IA", "52732"), ("Salem", "OR", "97301")]
MONS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DIRTY_DATES = ["31-FEB-24", "00-XXX-00", "99-999-99", "1/1/1900", "N/A",
               "29-FEB-23", "  -   -  ", "12-13-201"]
ITEM_DESCS = ["Monthly platform fee", "API overage", "Storage overage",
              "Compute overage", "Support retainer", "Migration credit",
              "Late fee", "Manual adjustment - see notes"]


def md5_uuid(s: str) -> str:
    h = hashlib.md5(s.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def dt_str(rng: random.Random, y0=2009, y1=2025) -> str:
    return f"{rng.randint(1, 28):02d}-{rng.choice(MONS)}-{str(rng.randint(y0, y1))[-2:]}"


def zipf_weights(n: int, alpha: float = 1.3):
    return [1.0 / (i ** alpha) for i in range(1, n + 1)]


class Checksum:
    """md5 of ordered PK+amount columns (rows fed in PK order)."""

    def __init__(self):
        self._h = hashlib.md5()

    def add(self, pk: str, amount) -> None:
        self._h.update(f"{pk}:{amount}\n".encode())

    def hexdigest(self) -> str:
        return self._h.hexdigest()


def cleanup(cur, ns: str, batch_no: int) -> None:
    """Remove any prior seed run for this namespace (idempotent re-runs)."""
    cur.execute("""DELETE FROM entity_attr_value WHERE entity_id IN
                   (SELECT cust_id FROM customer_master WHERE conversion_batch_no = :1)""",
                [batch_no])
    cur.execute("DELETE FROM invoice_line WHERE batch_no = :1", [batch_no])
    cur.execute("DELETE FROM invoice_header WHERE batch_no = :1", [batch_no])
    cur.execute("DELETE FROM customer_master WHERE conversion_batch_no = :1", [batch_no])
    cur.execute("DELETE FROM customer_master_hist WHERE conversion_batch_no = :1", [batch_no])
    pfx = f"{ns}::"  # matched literally via SUBSTR, so _/% in ns are not wildcards
    cur.execute("""DELETE FROM dunning_attempts WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM notifications WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM invoice_lines WHERE invoice_id IN
                   (SELECT i.id FROM invoices i, tenants t
                     WHERE t.id = i.tenant_id AND SUBSTR(t.name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM invoices WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM rating_results WHERE period_id IN
                   (SELECT rp.id FROM rating_periods rp, tenants t
                     WHERE t.id = rp.tenant_id AND SUBSTR(t.name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM rating_periods WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM usage_events WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM credit_notes WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM subscriptions WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("""DELETE FROM subscriptions_hist WHERE tenant_id IN
                   (SELECT id FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2)""", [pfx, pfx])
    cur.execute("DELETE FROM tenants WHERE SUBSTR(name, 1, LENGTH(:1)) = :2", [pfx, pfx])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--scale", choices=sorted(SCALES), default="demo")
    ap.add_argument("--host", default=os.environ.get("DB_HOST", "localhost"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("DB_PORT", "52521")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", "ow_billing"))
    ap.add_argument("--password",
                    default=os.environ.get("DB_PASSWORD", "ow_billing"))
    ap.add_argument("--service", default=os.environ.get("DB_SERVICE", "FREEPDB1"))
    args = ap.parse_args()

    if not legacy_common.valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    if len(args.ns) > 11:
        # CUST_NO is VARCHAR2(20): '<NS>-' + 8 digits must fit.
        print("NS must be at most 11 characters", file=sys.stderr)
        return 2

    ns = args.ns
    cfg = SCALES[args.scale]
    seed = legacy_common.ns_seed(ns)
    rng = random.Random(seed)
    batch_no = seed % 90_000_000 + 1_000_000

    n_cust = cfg["customers"]
    n_lines = cfg["invoice_lines"]
    n_core_tenants = cfg["core_tenants"]
    n_orphans = max(37, n_lines // 10_000)
    n_dirty = max(41, n_cust // 500)
    n_bad_csv = max(23, n_cust // 800)

    conn = oracledb.connect(user=args.user, password=args.password,
                            host=args.host, port=args.port,
                            service_name=args.service)
    cur = conn.cursor()
    print(f"[seed] ns={ns} scale={args.scale} seed={seed} batch={batch_no}")
    cleanup(cur, ns, batch_no)
    conn.commit()

    # --- horror tenants: power-law weights with explicit whales up front ---
    n_htenants = max(20, n_cust // 500)
    htenants = [md5_uuid(f"{ns}:htenant:{i}") for i in range(n_htenants)]
    weights = zipf_weights(n_htenants)

    # --- CUSTOMER_MASTER ---
    dirty_idx = set(rng.sample(range(n_cust), n_dirty))
    badcsv_idx = set(rng.sample(range(n_cust), n_bad_csv))
    cust_ck = Checksum()
    cust_rows = []
    cust_ids = []
    cust_tenants = []
    cust_names = []
    cust_pairs = []  # (cust_id, balance) for ordered checksum
    insert_cust = """
        INSERT INTO customer_master (
            cust_id, tenant_id, cust_no, cust_name, legal_name,
            addr_line_1, addr_line_2, addr_line_3, city, state_cd, zip,
            phone1, phone1_type_cd, phone2, phone2_type_cd, email_1,
            signup_dt, last_activity_dt, status_cd, sub_status_cd,
            cust_type_cd, segment_cd, region_cd, tax_exempt_yn,
            credit_hold_yn, vip_yn, cur_bal_amt, past_due_amt,
            ytd_billed_amt, credit_limit_amt, related_acct_ids,
            promo_codes_csv, legacy_sys_key, mainframe_acct_no,
            conversion_batch_no, created_by, created_dt, updated_by, updated_dt
        ) VALUES (
            :1, :2, :3, :4, :5, :6, :7, :8, :9, :10, :11, :12, :13, :14, :15,
            :16, :17, :18, :19, :20, :21, :22, :23, :24, :25, :26, :27, :28,
            :29, :30, :31, :32, :33, :34, :35, :36,
            DATE '2026-08-01' - :37, :38, DATE '2026-08-01'
        )"""

    for i in range(n_cust):
        cust_id = md5_uuid(f"{ns}:cust:{i}")
        cust_ids.append(cust_id)
        tenant = rng.choices(htenants, weights=weights)[0]
        cust_tenants.append(tenant)
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        cust_names.append(name)
        city, st, zp = rng.choice(CITIES)
        whale = i < n_htenants  # the first few customers are whale accounts
        bal = round(rng.uniform(50_000, 900_000), 2) if whale else \
            round(abs(rng.gauss(400, 700)), 2)
        signup = rng.choice(DIRTY_DATES) if i in dirty_idx else dt_str(rng)
        if i in badcsv_idx:
            related = rng.choice([",,", "12345,,67890,", "A;B;C", " , 99 ,",
                                  "NULL,NONE,", "0000000000000000000000,"])
        else:
            related = ",".join(str(rng.randint(10_000, 99_999))
                               for _ in range(rng.randint(0, 4)))
        cust_rows.append((
            cust_id, tenant, f"{ns.upper()}-{i:08d}", name,
            f"{name} {rng.choice(['LLC', 'Inc', 'Co', 'LP'])}",
            f"{rng.randint(1, 9999)} {rng.choice(STREETS)}",
            rng.choice([None, "Suite " + str(rng.randint(1, 400)), "c/o accounting"]),
            rng.choice([None, None, "ATTN: BILLING"]),
            city, st, zp,
            f"{rng.randint(200, 989)}-555-{rng.randint(0, 9999):04d}", 1,
            rng.choice([None, f"{rng.randint(200, 989)}-555-{rng.randint(0, 9999):04d}"]), 2,
            f"user{i}@{ns}.example.com".lower(),
            signup, dt_str(rng, 2020, 2026),
            rng.choices([1, 2, 3, 99], weights=[85, 8, 5, 2])[0],
            rng.choice([None, 1, 2]),
            rng.choices([1, 2, 3], weights=[60, 35, 5])[0],
            rng.randint(1, 9), rng.randint(1, 12),
            "Y" if rng.random() < 0.04 else "N",
            "Y" if rng.random() < 0.03 else "N",
            "Y" if whale else "N",
            bal, round(bal * rng.uniform(0, 0.4), 2),
            round(bal * rng.uniform(1, 9), 2),
            rng.choice([1000, 5000, 10000, 50000, None]),
            related,
            ",".join(rng.sample(["SPRING24", "LEGACY", "WINBACK", "VIP",
                                 "CONV2011"], rng.randint(0, 2))),
            f"SYS{rng.randint(1, 3)}-{rng.randint(100000, 999999)}",
            f"{rng.randint(0, 999999999):09d}",
            batch_no, "CONVERSION", rng.randint(0, 5000), "BATCH",
        ))
        cust_pairs.append((cust_id, f"{bal:.2f}"))
        if len(cust_rows) >= 5000:
            cur.executemany(insert_cust, cust_rows)
            cust_rows = []
    if cust_rows:
        cur.executemany(insert_cust, cust_rows)
    conn.commit()
    for pk, amt in sorted(cust_pairs):
        cust_ck.add(pk, amt)
    print(f"[seed] customer_master: {n_cust}")

    # --- ENTITY_ATTR_VALUE: attribute sprawl for a subset of customers ---
    n_eav = n_cust // 3
    eav_rows = []
    attrs = ["PORTAL_THEME", "SAP_EXPORT_FLAG", "TAX_REGION_OVERRIDE",
             "LEGACY_TIER", "COLLECTIONS_NOTE", "FAX_OPTOUT", "Y2K_VERIFIED"]
    for i in range(n_eav):
        cid = cust_ids[rng.randrange(n_cust)]
        eav_rows.append((f"{ns}:{i}", "CUSTOMER", cid, rng.choice(attrs),
                         rng.choice(["Y", "N", "1", "0", "TRUE", "blue",
                                     "see ticket 48213", "3.14"]),
                         "STR", dt_str(rng)))
        if len(eav_rows) >= 5000:
            cur.executemany("""INSERT INTO entity_attr_value
                (eav_id, entity_type, entity_id, attr_name, attr_value, attr_type, created_dt)
                VALUES (seq_entity_attr_value.NEXTVAL, :2, :3, :4, :5, :6, :7)""",
                            [r[1:] for r in eav_rows])
            eav_rows = []
    if eav_rows:
        cur.executemany("""INSERT INTO entity_attr_value
            (eav_id, entity_type, entity_id, attr_name, attr_value, attr_type, created_dt)
            VALUES (seq_entity_attr_value.NEXTVAL, :2, :3, :4, :5, :6, :7)""",
                        [r[1:] for r in eav_rows])
    conn.commit()
    print(f"[seed] entity_attr_value: {n_eav}")

    # --- INVOICE_HEADER + INVOICE_LINE (skewed: whales get most lines) ---
    n_inv = max(1000, n_lines // 8)
    line_ck = Checksum()
    hdr_rows, line_rows = [], []
    line_pairs = []  # (line_id, amount) for ordered checksum
    orphan_idx = set(rng.sample(range(n_lines), n_orphans))
    inv_ids = []
    for i in range(n_inv):
        cust_i = int(abs(rng.gauss(0, n_cust / 6))) % n_cust  # skew to whales
        inv_id = md5_uuid(f"{ns}:inv:{i}")
        inv_ids.append((inv_id, f"{ns.upper()}-{i:09d}", cust_i))
        total = 0
        hdr_rows.append((inv_id, f"{ns.upper()}-{i:09d}", cust_ids[cust_i],
                         cust_tenants[cust_i], dt_str(rng),
                         dt_str(rng), rng.choices([20, 30, 40], weights=[30, 55, 15])[0],
                         round(rng.uniform(20, 20_000), 2), batch_no))
        if len(hdr_rows) >= 5000:
            cur.executemany("""INSERT INTO invoice_header VALUES
                              (:1, :2, :3, :4, :5, :6, :7, :8, :9)""", hdr_rows)
            hdr_rows = []
    if hdr_rows:
        cur.executemany("INSERT INTO invoice_header VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9)",
                        hdr_rows)
    conn.commit()
    print(f"[seed] invoice_header: {n_inv}")

    insert_line = """
        INSERT INTO invoice_line (
            line_id, invoice_no, invoice_id, cust_id, cust_no, cust_name,
            tenant_id, line_no, line_type_cd, item_desc, qty, unit_price,
            amount, tax_amt, invoice_dt, service_period, posted_yn,
            gl_acct_csv, batch_no, src_system
        ) VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10, :11, :12, :13,
                  :14, :15, :16, :17, :18, :19, :20)"""
    for i in range(n_lines):
        line_id = md5_uuid(f"{ns}:line:{i}")
        if i in orphan_idx:
            inv_id = md5_uuid(f"{ns}:ghost-invoice:{i}")
            inv_no = f"{ns.upper()}-GHOST-{i:09d}"
            cust_i = rng.randrange(n_cust)
        else:
            inv_id, inv_no, cust_i = inv_ids[rng.randrange(n_inv)]
        qty = rng.randint(1, 500)
        price = round(rng.uniform(0.01, 99.0), 4)
        amount = round(qty * price, 2)
        mm = rng.randint(1, 12)
        line_rows.append((
            line_id, inv_no, inv_id,
            cust_ids[cust_i], f"{ns.upper()}-{cust_i:08d}",
            cust_names[cust_i],
            cust_tenants[cust_i],
            i % 25 + 1, rng.choice([1, 1, 1, 2, 3, 9]),
            rng.choice(ITEM_DESCS), qty, price, amount,
            round(amount * 0.0825, 2), dt_str(rng),
            f"{mm:02d}{rng.randint(2015, 2025)}-{mm:02d}{rng.randint(2015, 2025)}",
            rng.choice(["Y", "Y", "Y", "N", None]),
            ",".join(str(rng.randint(40000, 49999)) for _ in range(rng.randint(1, 3))),
            batch_no, "MAINFRAME"))
        line_pairs.append((line_id, f"{amount:.2f}"))
        if len(line_rows) >= 10_000:
            cur.executemany(insert_line, line_rows)
            conn.commit()
            line_rows = []
    if line_rows:
        cur.executemany(insert_line, line_rows)
    conn.commit()
    for pk, amt in sorted(line_pairs):
        line_ck.add(pk, amt)
    print(f"[seed] invoice_line: {n_lines} (orphans: {n_orphans})")

    # --- core billing tables: namespace-prefixed tenants the packages run on ---
    plan_ids = [f"10000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3)]
    core_tenants = []
    for i in range(n_core_tenants):
        tid = md5_uuid(f"{ns}:tenant:{i}")
        core_tenants.append(tid)
        cur.execute("""INSERT INTO tenants (id, name, tax_exempt_yn, status_cd)
                       VALUES (:1, :2, :3, 10)""",
                    [tid, f"{ns}::tenant-{i:04d}",
                     "Y" if rng.random() < 0.05 else "N"])
        plan = rng.choices(plan_ids, weights=[60, 30, 10])[0]
        cur.execute("""INSERT INTO subscriptions (id, tenant_id, plan_id, starts_on, status_cd)
                       VALUES (:1, :2, :3, DATE '2026-01-01', 10)""",
                    [md5_uuid(f"{ns}:sub:{i}"), tid, plan])
        for j in range(rng.randint(3, 25)):
            cur.execute("""INSERT INTO usage_events (id, tenant_id, occurred_at, units, kind_cd)
                           VALUES (:1, :2, TO_TIMESTAMP(:3, 'YYYY-MM-DD HH24:MI:SS'), :4, :5)""",
                        [md5_uuid(f"{ns}:usage:{i}:{j}"), tid,
                         f"2026-02-{rng.randint(1, 28):02d} 10:00:00",
                         rng.randint(1, 800), rng.choice([1, 1, 1, 2, 3])])
    conn.commit()
    print(f"[seed] core tenants: {n_core_tenants}")

    # --- manifest: merge into the shared per-namespace manifest so the
    # postgres/dynamodb/s3 estates' entries are preserved ---
    targets = {
        "oracle.OW_BILLING.CUSTOMER_MASTER":
            {"rows": n_cust, "checksum": cust_ck.hexdigest()},
        "oracle.OW_BILLING.INVOICE_LINE":
            {"rows": n_lines, "checksum": line_ck.hexdigest()},
        "oracle.OW_BILLING.INVOICE_HEADER": {"rows": n_inv},
        "oracle.OW_BILLING.ENTITY_ATTR_VALUE": {"rows": n_eav},
        "oracle.OW_BILLING.TENANTS": {"rows": n_core_tenants},
    }
    anomalies = [
        {"kind": "orphaned_rows",
         "target": "oracle.OW_BILLING.INVOICE_LINE", "count": n_orphans},
        {"kind": "dirty_dates",
         "target": "oracle.OW_BILLING.CUSTOMER_MASTER.SIGNUP_DT",
         "count": n_dirty},
        {"kind": "malformed_csv_lists",
         "target": "oracle.OW_BILLING.CUSTOMER_MASTER.RELATED_ACCT_IDS",
         "count": n_bad_csv},
    ]
    params = {k: {"scale": args.scale, "batch_no": batch_no} for k in targets}
    legacy_common.merge_manifest(ns, targets, anomalies,
                                 owned_prefixes=("oracle.",), params=params)
    print(f"[seed] manifest written: {legacy_common.manifest_path(ns)}")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
