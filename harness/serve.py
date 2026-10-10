"""Persistent harness server for integrations (Discord bot, etc.).

Usage:
    harness --serve --port 8080

Holds a Loop per session ID in memory — no process startup per message.
Integrations POST to /turn:

    POST /turn
    {"session": "discord-dm-123", "message": "hello"}

    -> {"response": "...", "session": "discord-dm-123"}

    POST /health -> {"status": "ok"}

Stdlib only (http.server). Single-threaded: one request at a time.
The Discord bot already coalesces per session, so this is fine.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class HarnessServer:
    """Holds Loops per session, serves turns over HTTP."""

    def __init__(self, make_loop, sessions_root: Path):
        # make_loop() -> Loop (fresh, configured). Called once; the same
        # Loop instance is reused across sessions (it's session-agnostic;
        # Session carries the per-conversation state).
        self._make_loop = make_loop
        self._loop = None
        self._sessions_root = Path(sessions_root)
        self._sessions: dict[str, object] = {}  # session_id -> Session

    def _get_loop(self):
        if self._loop is None:
            self._loop = self._make_loop()
        return self._loop

    def _get_session(self, session_id: str):
        # Sanitize: session IDs become directory names.
        safe = "".join(c if (c.isalnum() or c in "-_") else "_"
                       for c in session_id)[:64] or "default"
        if safe not in self._sessions:
            from harness.session import Session, init_layout
            from harness.loop import SYSTEM_PROMPT
            sdir = self._sessions_root / safe
            sdir.mkdir(parents=True, exist_ok=True)
            init_layout(sdir, SYSTEM_PROMPT)
            sess = Session(id=safe, dir=sdir,
                           workdir=str(Path.cwd()))
            self._sessions[safe] = sess
        return self._sessions[safe]

    def turn(self, session_id: str, message: str) -> str:
        loop = self._get_loop()
        sess = self._get_session(session_id)
        return loop.run_turn(sess, message) or ""


def _handler_class(server: HarnessServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # quiet; use --verbose if you need access logs

        def _json(self, obj: dict, status: int = 200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._json({"status": "ok"})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/turn":
                self._json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                data = json.loads(body or b"{}")
            except (ValueError, OSError):
                self._json({"error": "bad JSON"}, 400)
                return
            session_id = str(data.get("session") or "default")
            message = str(data.get("message") or "")
            if not message.strip():
                self._json({"error": "empty message"}, 400)
                return
            try:
                response = server.turn(session_id, message)
            except Exception as e:
                self._json({"error": f"turn failed: {e}"}, 500)
                return
            self._json({"response": response, "session": session_id})

    return Handler


def run_serve(make_loop, sessions_root: Path, port: int):
    server = HarnessServer(make_loop, sessions_root)
    httpd = HTTPServer(("127.0.0.1", port), _handler_class(server))
    print(f"harness serve: listening on 127.0.0.1:{port}", file=sys.stderr)
    print(f"sessions root: {sessions_root}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    print("harness serve: stopped", file=sys.stderr)
