#!/usr/bin/env python3
"""Extract the search corpus from document-service and file-service into the landing volume.

This is the durable replacement for the extract half of
etl/scripts/search_reindex_weekly.py. Differences that matter:

* nothing is deleted anywhere -- the extract only ever writes new landing files,
  so a failure here cannot leave the serving index empty;
* every paginated call retries with exponential backoff on transient failures
  (connection errors, 429, 5xx) instead of dying on page 1;
* service URLs and the optional bearer token come from the environment, never
  from a checked-in config file with credentials in it;
* progress and failures are emitted as structured JSON log lines, not print();
* a manifest records the per-entity counts the downstream ingest validates
  against, so a truncated extract is detected rather than silently published.

Usage:
    extract_search_sources.py --ns demo [--out DIR] [--landing-prefix search_reindex] [--upload]

Environment:
    OW_DOCUMENT_SERVICE_URL   e.g. http://localhost:8083   (required)
    OW_FILE_SERVICE_URL       e.g. http://localhost:8082   (required)
    OW_SEARCH_API_TOKEN       optional bearer token for both services
    OW_SEARCH_PAGE_SIZE       page size, default 100 (same as the legacy cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402

NS_PATTERN = re.compile(r"^[a-z0-9_]+$")
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 1.0
TIMEOUT_S = 30
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_PAGE_SIZE = 100

log = logging.getLogger("search_reindex.extract")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def _log(level: int, event: str, **fields) -> None:
    log.log(level, event, extra={"fields": fields})


def get_json(url: str, params: dict[str, object], token: str | None) -> dict:
    """GET a JSON page, retrying transient failures with exponential backoff."""
    target = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(target, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                _log(logging.ERROR, "http_error", url=target, status=exc.code)
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        sleep_s = BACKOFF_BASE_S * (2 ** (attempt - 1))
        _log(
            logging.WARNING,
            "retrying_page",
            url=target,
            attempt=attempt,
            max_attempts=MAX_ATTEMPTS,
            sleep_s=sleep_s,
            error=str(last_error),
        )
        if attempt == MAX_ATTEMPTS:
            break
        time.sleep(sleep_s)
    raise RuntimeError(f"GET {target} failed after {MAX_ATTEMPTS} attempts: {last_error}")


def paginate(url: str, page_param: str, size_param: str, page_size: int, keys: tuple[str, ...], token: str | None):
    """Yield source records page by page, mirroring the legacy pagination contract."""
    page = 1
    while True:
        body = get_json(url, {page_param: page, size_param: page_size}, token)
        records = next((body[key] for key in keys if isinstance(body.get(key), list)), [])
        if not records:
            return
        _log(logging.INFO, "page_extracted", url=url, page=page, records=len(records))
        yield from records
        page += 1


def parse_page_size(raw: str) -> int:
    try:
        page_size = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"OW_SEARCH_PAGE_SIZE must be an integer between 1 and {MAX_PAGE_SIZE}, got {raw!r}"
        ) from exc
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(
            f"OW_SEARCH_PAGE_SIZE must be between 1 and {MAX_PAGE_SIZE}, got {page_size}"
        )
    return page_size


def entity_id_of(record: dict, id_keys: tuple[str, ...], entity_type: str = "unknown") -> str:
    for key in id_keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    raise RuntimeError(f"source record of type {entity_type!r} has no id in keys {id_keys!r}")


def write_ndjson(path: Path, ns: str, entity_type: str, records, id_keys: tuple[str, ...], extracted_at: str) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            envelope = {
                "ns": ns,
                "entity_type": entity_type,
                "entity_id": entity_id_of(record, id_keys, entity_type),
                "extracted_at": extracted_at,
                "payload": json.dumps(record, sort_keys=True),
            }
            handle.write(json.dumps(envelope) + "\n")
            count += 1
    _log(logging.INFO, "entity_extracted", entity_type=entity_type, records=count, path=str(path))
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--out", default="/tmp/ow_tp_search_reindex")
    parser.add_argument("--landing-prefix", default="search_reindex")
    parser.add_argument("--upload", action="store_true", help="upload the extract to the landing volume")
    args = parser.parse_args(argv)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    if not NS_PATTERN.match(args.ns):
        raise SystemExit(f"--ns must match {NS_PATTERN.pattern}, got {args.ns!r}")

    document_service = os.environ.get("OW_DOCUMENT_SERVICE_URL")
    file_service = os.environ.get("OW_FILE_SERVICE_URL")
    missing = [name for name, value in
               (("OW_DOCUMENT_SERVICE_URL", document_service), ("OW_FILE_SERVICE_URL", file_service)) if not value]
    if missing:
        raise SystemExit(f"missing required environment variables: {', '.join(missing)}")
    token = os.environ.get("OW_SEARCH_API_TOKEN")
    try:
        page_size = parse_page_size(os.environ.get("OW_SEARCH_PAGE_SIZE", "100"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    extracted_at = datetime.now(timezone.utc).isoformat()
    _log(logging.INFO, "extract_started", ns=args.ns, page_size=page_size, extracted_at=extracted_at)

    counts = {
        "document": write_ndjson(
            out_dir / "documents.ndjson", args.ns, "document",
            paginate(f"{document_service.rstrip('/')}/api/v1/documents", "page", "size", page_size,
                     ("documents", "items"), token),
            ("document_id", "id"), extracted_at,
        ),
        "file": write_ndjson(
            out_dir / "files.ndjson", args.ns, "file",
            paginate(f"{file_service.rstrip('/')}/api/v1/files", "page", "page_size", page_size,
                     ("files", "items"), token),
            ("file_id", "id"), extracted_at,
        ),
    }

    manifest = {
        "ns": args.ns,
        "extracted_at": extracted_at,
        "page_size": page_size,
        "counts": counts,
        "sources": {"document": document_service, "file": file_service},
    }
    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    if args.upload:
        for name in ("documents.ndjson", "files.ndjson", "_manifest.json"):
            target = dbx.upload(str(out_dir / name), f"{args.ns}/{args.landing_prefix}/{name}")
            _log(logging.INFO, "uploaded", file=name, target=target)

    _log(logging.INFO, "extract_completed", ns=args.ns, counts=counts)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
