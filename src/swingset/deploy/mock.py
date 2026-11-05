"""An owned HTTP source for the server's real fetch-gate rehearsal."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    events: list[dict[str, object]] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            status, body, headers = 200, b"owned rehearsal evidence\n", {}
            if path == "/events":
                with lock:
                    body = json.dumps(events).encode()
            elif path == "/robots.txt":
                body = b"User-agent: *\nAllow: /\nCrawl-delay: 2\n"
            elif path == "/conditional":
                headers = {"ETag": '"fixture-v1"'}
                if self.headers.get("If-None-Match") == '"fixture-v1"':
                    status, body = 304, b""
            elif path == "/redirect":
                status, body, headers = 302, b"", {"Location": "/ok"}
            elif path == "/redirect-denied":
                status, body, headers = (
                    302,
                    b"",
                    {"Location": "http://forbidden.swingset.invalid:8080/ok"},
                )
            elif path == "/retry-after":
                status, headers = 429, {"Retry-After": "2"}
            elif path == "/timeout":
                time.sleep(10)
            if path != "/events":
                with lock:
                    events.append(
                        {"utc": time.time(), "method": "GET", "path": path, "status": status}
                    )
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass  # Paths may carry secrets; only the explicit bounded event schema is retained.

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    arguments = parser.parse_args()
    serve(arguments.host, arguments.port)
