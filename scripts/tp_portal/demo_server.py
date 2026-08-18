#!/usr/bin/env python3
"""Local static server + reverse proxy for the Otter Portal demo page.

Serves services/portal-serverless/demo-ui/ and proxies /api/* and /health to the
legacy monolith, so the before-state act needs no CORS changes to legacy code:
the page and the API share one origin.

Usage:
  demo_server.py [--port 8000] [--target http://localhost:8095]
"""
from __future__ import annotations

import argparse
import http.server
import os
import urllib.error
import urllib.request

UI_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "services", "portal-serverless", "demo-ui"))

PROXY_PREFIXES = ("/api/", "/health")
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}


def make_handler(target: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=UI_DIR, **kwargs)

        def _proxied(self) -> bool:
            return any(self.path == p.rstrip("/") or self.path.startswith(p)
                       for p in PROXY_PREFIXES)

        def _proxy(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            url = target.rstrip("/") + self.path
            req = urllib.request.Request(url, data=body, method=self.command)
            for k, v in self.headers.items():
                if k.lower() not in HOP_BY_HOP:
                    req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    payload = resp.read()
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in HOP_BY_HOP:
                            self.send_header(k, v)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
            except urllib.error.HTTPError as e:
                payload = e.read()
                self.send_response(e.code)
                for k, v in e.headers.items():
                    if k.lower() not in HOP_BY_HOP:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (urllib.error.URLError, OSError):
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Bad Gateway","message":"monolith unreachable"}')

        def do_GET(self):
            self._proxy() if self._proxied() else super().do_GET()

        def do_POST(self):
            if self._proxied():
                self._proxy()
            else:
                self.send_error(404)

        def do_PUT(self):
            if self._proxied():
                self._proxy()
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):  # quieter demo output
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--target", default="http://localhost:8095",
                        help="Monolith base URL to proxy /api/* and /health to")
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.target))
    print(f"Otter Portal demo page: http://localhost:{args.port} (proxying API to {args.target})")
    server.serve_forever()


if __name__ == "__main__":
    main()
