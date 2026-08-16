#!/usr/bin/env python3
"""Deterministic planted-anomaly CUSTBILL file for the AWS serverless track.

Writes CUSTBILL_<NS>_ANOM.dat into the legacy SFTP drop directory. The file
carries exactly four planted anomalies the converted pipeline must surface:

  A-invalid-date     record 1 has BILL-DATE 20241385 (no such day)
  A-nonutf8-byte     record 2 has byte 0xA3 in CUST-NAME (not valid UTF-8)
  A-short-record     record 3 is truncated to 40 bytes (copybook needs 65)
  A-trailer-mismatch TRL claims 5 records; the body holds 3

The legacy chain tolerates all four silently; the byte content is fixed so
golden baselines and recon runs are reproducible.

Usage: gen_anomaly_file.py [NS]   (NS defaults to "demo")
"""
import os
import sys


def record(cust: bytes, name: bytes, date: bytes, amt: bytes, ccy: bytes, rt: bytes) -> bytes:
    assert len(cust) == 10 and len(name) == 30 and len(date) == 8
    assert len(amt) == 12 and len(ccy) == 3 and len(rt) == 2
    return cust + name + date + amt + ccy + rt + b"\n"


def main() -> None:
    ns = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NS", "demo")).upper()
    root = os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/otterworks-legacy")
    drop = os.path.join(root, "sftp-drop", "upload")
    os.makedirs(drop, exist_ok=True)
    path = os.path.join(drop, f"CUSTBILL_{ns}_ANOM.dat")

    hdr = ("HDR CUSTBILL EXTRACT NS=%-10s FILE=ANOM" % ns).encode("ascii")
    hdr = hdr + b" " * (65 - len(hdr)) + b"\n"
    r1 = record(b"C000000901", b"%-30s" % b"BADDATE CORP", b"20241385",
                b"000000010000", b"USD", b"01")
    r2 = record(b"C000000902", b"STERLING \xa3 LTD".ljust(30), b"20240601",
                b"000000020000", b"GBP", b"01")
    r3 = record(b"C000000903", b"%-30s" % b"SHORTY GMBH", b"20240715",
                b"000000030000", b"EUR", b"02")[:40] + b"\n"
    trl = b"TRL0000000005" + b" " * 52 + b"\n"

    with open(path, "wb") as out:
        out.write(hdr + r1 + r2 + r3 + trl)
    print(f"wrote {path} (3 records, 4 planted anomalies)")


if __name__ == "__main__":
    main()
