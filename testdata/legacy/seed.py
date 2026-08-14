# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg2-binary", "boto3"]
# ///
"""
Deterministic legacy seed-data generator for partner migration demos.

Seeds three core data stores with realistic volume, plants exactly-enumerable
data-quality anomalies, and writes/merges a manifest per the contract in
docs/tech-partnerships/README.md:

  - postgres:  documents + document_versions + document_snapshots in
               schema otterworks_<ns> (document-service shapes)
  - dynamodb:  file-metadata items in otterworks-file-metadata (LocalStack),
               namespaced by an `ns` attribute (ids stay plain UUIDs so the
               file-service can parse every row in the shared table)
  - s3:        hourly gzip JSON event objects under
               s3://otterworks-data-lake/events/<ns>/

Usage:
    uv run testdata/legacy/seed.py --ns <ns> [--scale demo|full]
        [--targets postgres,dynamodb,s3]

Reruns are idempotent: each target's namespace slice is wiped and reseeded, so
counts and checksums are byte-identical for a given (ns, scale).
"""

import argparse
import gzip
import io
import json
import sys
import time

import psycopg2

from legacy_common import (
    ANCHOR,
    DATA_LAKE_BUCKET,
    DYNAMO_TABLE,
    EVENT_TYPES,
    MIME_TYPES,
    SCALES,
    Checksum,
    anchor_minus,
    aws_client,
    aws_resource,
    det_uuid,
    iso,
    merge_manifest,
    ns_seed,
    pg_config,
    power_law_index,
    rng_for,
    schema_name,
    valid_ns,
)

from datetime import timedelta

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.documents (
    id UUID PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    content_type VARCHAR(50) NOT NULL DEFAULT 'text/markdown',
    owner_id UUID NOT NULL,
    folder_id UUID,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    is_template BOOLEAN NOT NULL DEFAULT FALSE,
    word_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{ns}_documents_owner ON {schema}.documents (owner_id);

CREATE TABLE IF NOT EXISTS {schema}.document_versions (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES {schema}.documents (id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{ns}_document_versions_doc
    ON {schema}.document_versions (document_id);

-- Legacy snapshot table: intentionally no FK, mirroring the collab-service
-- Redis snapshot shape persisted to Postgres by the old archiver job.
CREATE TABLE IF NOT EXISTS {schema}.document_snapshots (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    state_b64 TEXT NOT NULL,
    label VARCHAR(100),
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
"""


def log(msg: str) -> None:
    print(f"[seed-legacy] {msg}", flush=True)


COPY_FLUSH_DOCS = 20_000  # flush COPY buffers every N documents to bound memory


# ── Postgres ──────────────────────────────────────────────────────────────────


def seed_postgres(ns: str, cfg: dict) -> tuple[dict, list[dict]]:
    schema = schema_name(ns)
    rng = rng_for(ns, "postgres")
    users = [det_uuid(rng) for _ in range(cfg["users"])]
    folders = [det_uuid(rng) for _ in range(max(10, cfg["users"] // 5))]

    n_docs = cfg["documents"]
    gap_count = max(1, n_docs // 200)         # docs with a missing middle version
    orphan_snap_count = max(1, n_docs // 333)  # snapshots pointing at missing docs
    gap_docs = set(rng.sample(range(n_docs), gap_count))

    doc_ck, ver_ck, snap_ck = Checksum(), Checksum(), Checksum()
    docs_buf, vers_buf, snaps_buf = io.StringIO(), io.StringIO(), io.StringIO()
    total_versions = 0
    total_snapshots = 0

    conn = psycopg2.connect(**pg_config())
    conn.autocommit = False
    cur = conn.cursor()

    copy_specs = (
        (docs_buf, "documents",
         "(id,title,content,content_type,owner_id,folder_id,is_deleted,"
         "is_template,word_count,version,created_at,updated_at)"),
        (vers_buf, "document_versions",
         "(id,document_id,version_number,title,content,created_by,created_at)"),
        (snaps_buf, "document_snapshots",
         "(id,document_id,state_b64,label,created_by,created_at)"),
    )

    def flush_buffers() -> None:
        for buf, table, cols in copy_specs:
            if buf.tell():
                buf.seek(0)
                cur.copy_expert(f"COPY {schema}.{table} {cols} FROM STDIN", buf)
                buf.seek(0)
                buf.truncate(0)

    cur.execute(DDL.format(schema=schema, ns=ns))
    cur.execute(f"TRUNCATE {schema}.document_versions, {schema}.document_snapshots")
    cur.execute(f"TRUNCATE {schema}.documents CASCADE")

    for i in range(n_docs):
        doc_id = det_uuid(rng)
        owner = users[power_law_index(rng, len(users))]
        folder = folders[rng.randrange(len(folders))] if rng.random() < 0.8 else r"\N"
        n_versions = rng.randint(cfg["versions_min"], cfg["versions_max"])
        created = anchor_minus(rng, 720)
        title = f"Legacy document {ns}-{i:06d}"
        content = f"Body of {title}, revision {n_versions}. " * rng.randint(1, 4)
        word_count = len(content.split())
        updated = min(created + timedelta(hours=rng.randint(1, 24 * 30)), ANCHOR)
        is_deleted = "t" if rng.random() < 0.03 else "f"

        docs_buf.write(
            f"{doc_id}\t{title}\t{content}\ttext/markdown\t{owner}\t{folder}\t"
            f"{is_deleted}\tf\t{word_count}\t{n_versions}\t{iso(created)}\t{iso(updated)}\n"
        )
        doc_ck.add(f"{doc_id}|{n_versions}|{word_count}")

        skip_version = rng.randint(2, n_versions) if (i in gap_docs and n_versions >= 2) else 0
        for v in range(1, n_versions + 1):
            ver_id = det_uuid(rng)
            v_created = min(created + timedelta(hours=v * rng.randint(1, 48)), updated)
            if v == skip_version:
                continue  # planted version gap
            vers_buf.write(
                f"{ver_id}\t{doc_id}\t{v}\t{title}\trev {v} of {title}\t{owner}\t{iso(v_created)}\n"
            )
            ver_ck.add(f"{doc_id}|{v}")
            total_versions += 1

        if rng.random() < 0.2:
            snap_id = det_uuid(rng)
            snaps_buf.write(
                f"{snap_id}\t{doc_id}\tc3RhdGU=\tautosave\t{owner}\t{iso(updated)}\n"
            )
            snap_ck.add(f"{snap_id}|{doc_id}")
            total_snapshots += 1

        if (i + 1) % COPY_FLUSH_DOCS == 0:
            flush_buffers()

    for _ in range(orphan_snap_count):  # planted orphaned snapshots
        snap_id = det_uuid(rng)
        missing_doc = det_uuid(rng)
        snaps_buf.write(
            f"{snap_id}\t{missing_doc}\tb3JwaGFu\torphan\t{users[0]}\t{iso(ANCHOR)}\n"
        )
        snap_ck.add(f"{snap_id}|{missing_doc}")
        total_snapshots += 1

    try:
        flush_buffers()
        conn.commit()
    finally:
        cur.close()
        conn.close()

    targets = {
        f"postgres.{schema}.documents": {
            "rows": n_docs, "checksum": doc_ck.hexdigest()},
        f"postgres.{schema}.document_versions": {
            "rows": total_versions, "checksum": ver_ck.hexdigest()},
        f"postgres.{schema}.document_snapshots": {
            "rows": total_snapshots, "checksum": snap_ck.hexdigest()},
    }
    anomalies = [
        {"kind": "version_gaps",
         "target": f"postgres.{schema}.document_versions", "count": gap_count},
        {"kind": "orphaned_snapshots",
         "target": f"postgres.{schema}.document_snapshots", "count": orphan_snap_count},
    ]
    log(f"postgres: {n_docs} documents, {total_versions} versions, "
        f"{total_snapshots} snapshots ({gap_count} version gaps, "
        f"{orphan_snap_count} orphaned snapshots)")
    return targets, anomalies


# ── DynamoDB ──────────────────────────────────────────────────────────────────


def clear_dynamo_namespace(table, ns: str) -> int:
    deleted = 0
    scan_kwargs = {
        "ProjectionExpression": "id",
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": ns},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={"id": item["id"]})
            deleted += len(items)
        if "LastEvaluatedKey" not in resp:
            return deleted
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def seed_dynamodb(ns: str, cfg: dict) -> tuple[dict, list[dict]]:
    rng = rng_for(ns, "dynamodb")
    users = [det_uuid(rng) for _ in range(cfg["users"])]
    folders = [det_uuid(rng) for _ in range(max(10, cfg["users"] // 5))]

    n_items = cfg["dynamo_items"]
    orphan_count = max(1, n_items // 250)  # items whose s3_key points nowhere
    orphan_idx = set(rng.sample(range(n_items), orphan_count))

    table = aws_resource("dynamodb").Table(DYNAMO_TABLE)
    cleared = clear_dynamo_namespace(table, ns)
    if cleared:
        log(f"dynamodb: cleared {cleared} previous items for ns '{ns}'")

    ck = Checksum()
    with table.batch_writer() as batch:
        for i in range(n_items):
            # id stays a plain UUID so the file-service's Uuid parsing of the
            # shared table never breaks; the `ns` attribute carries the
            # namespace for slice clears and validation.
            item_id = det_uuid(rng)
            file_uuid = item_id
            owner = users[power_law_index(rng, len(users))]
            mime = MIME_TYPES[rng.randrange(len(MIME_TYPES))]
            size = rng.randint(128, 250_000_000)
            created = anchor_minus(rng, 720)
            prefix = "missing" if i in orphan_idx else "files"
            s3_key = f"{ns}/{prefix}/{owner}/{file_uuid}"
            batch.put_item(Item={
                "id": item_id,
                "ns": ns,
                "name": f"file-{ns}-{i:07d}.{mime.split('/')[-1][:4]}",
                "mime_type": mime,
                "size_bytes": size,
                "s3_key": s3_key,
                "folder_id": folders[rng.randrange(len(folders))],
                "owner_id": owner,
                "version": rng.randint(1, 9),
                "is_trashed": rng.random() < 0.05,
                "created_at": iso(created),
                "updated_at": iso(min(created + timedelta(hours=rng.randint(0, 720)), ANCHOR)),
            })
            ck.add(f"{item_id}|{size}|{s3_key}")

    targets = {
        "dynamodb.file-metadata": {
            "items": n_items, "checksum": ck.hexdigest()},
    }
    anomalies = [
        {"kind": "orphaned_metadata", "target": "dynamodb.file-metadata",
         "count": orphan_count},
    ]
    log(f"dynamodb: {n_items} items ({orphan_count} orphaned s3_key items)")
    return targets, anomalies


# ── S3 event history ──────────────────────────────────────────────────────────


def seed_s3(ns: str, cfg: dict) -> tuple[dict, list[dict]]:
    rng = rng_for(ns, "s3")
    users = [det_uuid(rng) for _ in range(cfg["users"])]
    s3 = aws_client("s3")
    prefix = f"events/{ns}/"

    # wipe previous namespace slice
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=DATA_LAKE_BUCKET, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=DATA_LAKE_BUCKET, Delete={"Objects": keys})

    days = cfg["event_days"]
    total_hours = days * 24
    missing_count = max(1, days // 30)  # planted missing hourly objects
    missing_hours = set(rng.sample(range(1, total_hours - 1), missing_count))

    ck = Checksum()
    objects, total_bytes = 0, 0
    for h in range(total_hours):
        hour_start = ANCHOR - timedelta(hours=total_hours - h)
        n_events = rng.randint(20, 120)
        events = []
        for e in range(n_events):
            events.append({
                "event_id": det_uuid(rng),
                "event_type": EVENT_TYPES[rng.randrange(len(EVENT_TYPES))],
                "user_id": users[power_law_index(rng, len(users))],
                "resource_id": det_uuid(rng),
                "occurred_at": iso(hour_start + timedelta(seconds=rng.randint(0, 3599))),
            })
        if h in missing_hours:
            continue  # planted gap in the hourly series
        body = gzip.compress(
            ("\n".join(json.dumps(ev, sort_keys=True) for ev in events) + "\n").encode(),
            mtime=0,
        )
        key = f"{prefix}{hour_start.strftime('%Y/%m/%d/%H')}.json.gz"
        s3.put_object(Bucket=DATA_LAKE_BUCKET, Key=key, Body=body)
        ck.add(f"{key}|{n_events}|{len(body)}")
        objects += 1
        total_bytes += len(body)

    targets = {
        f"s3.data-lake/{prefix}": {
            "objects": objects, "bytes": total_bytes,
            "checksum": ck.hexdigest()},
    }
    anomalies = [
        {"kind": "missing_hours", "target": f"s3.data-lake/{prefix}",
         "count": missing_count},
    ]
    log(f"s3: {objects} hourly objects, {total_bytes} bytes "
        f"({missing_count} missing hours planted)")
    return targets, anomalies


# ── Main ──────────────────────────────────────────────────────────────────────

SEEDERS = {"postgres": seed_postgres, "dynamodb": seed_dynamodb, "s3": seed_s3}
OWNED_PREFIX = {
    "postgres": lambda ns: f"postgres.{schema_name(ns)}.",
    "dynamodb": lambda ns: "dynamodb.file-metadata",
    "s3": lambda ns: f"s3.data-lake/events/{ns}/",
}
PARAM_KEYS = {
    "postgres": ("documents", "versions_min", "versions_max", "users"),
    "dynamodb": ("dynamo_items", "users"),
    "s3": ("event_days", "users"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--scale", choices=sorted(SCALES), default="demo")
    parser.add_argument("--targets", default="postgres,dynamodb,s3")
    args = parser.parse_args()

    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    requested = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in requested if t not in SEEDERS]
    if unknown:
        print(f"Unknown targets: {unknown} (valid: {sorted(SEEDERS)})", file=sys.stderr)
        return 2

    cfg = SCALES[args.scale]
    log(f"ns={args.ns} scale={args.scale} seed={ns_seed(args.ns)} targets={requested}")

    all_targets: dict = {}
    all_anomalies: list = []
    owned: list[str] = []
    started = time.monotonic()
    for name in requested:
        t0 = time.monotonic()
        targets, anomalies = SEEDERS[name](args.ns, cfg)
        all_targets.update(targets)
        all_anomalies.extend(anomalies)
        owned.append(OWNED_PREFIX[name](args.ns))
        log(f"{name}: done in {time.monotonic() - t0:.1f}s")

    params = {
        name: {"scale": args.scale, **{k: cfg[k] for k in PARAM_KEYS[name]}}
        for name in requested
    }
    manifest = merge_manifest(
        args.ns,
        all_targets,
        all_anomalies,
        tuple(owned),
        params=params,
    )
    log(f"manifest written: testdata/legacy/manifests/{args.ns}.json "
        f"({len(manifest['targets'])} targets, "
        f"{len(manifest['planted_anomalies'])} anomalies)")
    log(f"total: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
