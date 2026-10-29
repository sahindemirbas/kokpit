"""The web app: one static page plus a small JSON API. Standard library only.

Reads: everything. Writes: quick capture into the vault, nothing else.
Binds to localhost unless the config says otherwise.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .modules.brain import api as brain_api
from .modules.events import api as events_api
from .modules.connections import api as connections_api
from .modules.health import api as health_api
from .modules.today import api as today_api

STATIC = Path(__file__).parent / "static"


def make_handler(cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Kokpit/{__version__}"

        def log_message(self, format, *args):  # quieter than the default: only errors
            if args and str(args[1:2] and args[1]).startswith(("4", "5")):
                super().log_message(format, *args)

        def _json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, name: str, ctype: str):
            p = STATIC / name
            if not p.is_file():
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            body = p.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ics(self, raw_id: str):
            try:
                ev = events_api.one(cfg, int(raw_id))
            except ValueError:
                ev = None
            if ev is None:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            body = events_api.store.ics(ev).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="kokpit-event-{ev["id"]}.ics"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._file("index.html", "text/html; charset=utf-8")
                if path == "/api/today":
                    return self._json(today_api.view(cfg))
                if path == "/api/brain":
                    return self._json(brain_api.view(cfg))
                if path == "/api/health":
                    return self._json(health_api.view(cfg))
                if path == "/api/events":
                    q = parse_qs(urlparse(self.path).query)
                    days = max(1, min(3650, int(q.get("days", ["14"])[0])))
                    return self._json(events_api.view(cfg, days=days))
                if path.startswith("/api/events/") and path.endswith(".ics"):
                    return self._ics(path.split("/")[3].removesuffix(".ics"))
                if path == "/api/connections":
                    return self._json(connections_api.view(cfg))
                if path == "/api/meta":
                    return self._json({"version": __version__, "modules": cfg.modules, "config": str(cfg.path)})
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:  # show the error on the page instead of a blank card
                return self._json({"error": f"{exc.__class__.__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/brain/capture", "/api/brain/done"):
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/brain/done":
                    return self._json(brain_api.complete_task(cfg, data.get("text", "")))
                res = brain_api.quick_capture(cfg, data.get("kind", ""), data.get("text", ""), data.get("due", ""))
                return self._json(res)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return Handler


def serve(cfg: Config) -> None:
    srv = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg))
    print(f"Kokpit {__version__}  http://{cfg.host}:{cfg.port}/  (config: {cfg.path})")
    print(f"vault: {cfg.vault}   db: {cfg.db}   Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
