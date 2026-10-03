"""Local web server: static UI, JSON API and a Server-Sent Events stream."""

import json
import logging
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import App

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
HEARTBEAT = 15
MIN_PUSH_INTERVAL = 0.15


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                self._json(app.snapshot())
            elif path == "/api/events":
                self._events()
            elif path == "/api/stats":
                self._json(app.stats(parse_qs(urlparse(self.path).query)))
            else:
                self._static(path)

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/deck":
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                self._json(app.set_manual_deck(str(body.get("code", ""))))
            except (ValueError, IndexError) as exc:
                self._json({"error": f"invalid deck code: {exc}"}, HTTPStatus.BAD_REQUEST)

        def _json(self, data, status=HTTPStatus.OK):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _static(self, path):
            if path in ("", "/"):
                path = "/index.html"
            target = (WEB_DIR / path.lstrip("/")).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            payload = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        def _events(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.close_connection = True
            sent = -1
            try:
                while True:
                    with app.changed:
                        app.changed.wait_for(lambda: app.version != sent, timeout=HEARTBEAT)
                        version = app.version
                    if version != sent:
                        sent = version
                        self.wfile.write(f"data: {json.dumps(app.snapshot())}\n\n".encode())
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(MIN_PUSH_INTERVAL)  # coalesce bursts of log lines
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return

    return Handler


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
