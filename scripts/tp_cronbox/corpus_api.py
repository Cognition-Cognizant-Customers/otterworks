#!/usr/bin/env python3
"""Small local HTTP corpus API matching the legacy reindexer's API shapes."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg2
from common import clients, dynamo_scan_all, pg_kwargs


def corpus():
    conn = psycopg2.connect(**pg_kwargs())
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id,title,content,owner_id,tags,created_at,updated_at FROM cronbox_documents ORDER BY id"
        )
        docs = [
            {
                "id": r[0],
                "title": r[1],
                "content": r[2],
                "owner_id": r[3],
                "tags": r[4],
                "created_at": r[5].isoformat(),
                "updated_at": r[6].isoformat(),
            }
            for r in cur.fetchall()
        ]
    conn.close()
    _, dynamo, _ = clients()
    files = []
    for page in dynamo_scan_all(dynamo.Table("otterworks-file-metadata")):
        if page["id"] == "reverse-orphan":
            continue
        files.append(
            {
                "id": page["id"],
                "name": page.get("file_name", ""),
                "owner_id": page.get("owner_id", ""),
                "mime_type": page.get("mime_type", ""),
                "folder_id": page.get("folder_id", ""),
                "size": int(page.get("size_bytes", 0)),
                "tags": page.get("tags", []),
                "created_at": page.get("created_at"),
                "updated_at": page.get("updated_at"),
            }
        )
    return docs, sorted(files, key=lambda x: x["id"])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        docs, files = corpus()
        query = parse_qs(urlparse(self.path).query)
        page = int(query.get("page", ["1"])[0])
        size = int(query.get("size", query.get("page_size", ["100"]))[0])
        source = docs if path.endswith("/documents") else files
        chunk = source[(page - 1) * size : page * size]
        key = "documents" if source is docs else "files"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({key: chunk}, ensure_ascii=False).encode())

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
