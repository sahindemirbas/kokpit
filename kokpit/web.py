"""The web app: one static page plus a small JSON API. Standard library only.

Reads: everything. Writes: quick capture and the marked meeting-notes section into the vault, the local career profile file and the Instagram content calendar (publishing only
through the Approve action of one slot), nothing else.
Binds to localhost unless the config says otherwise.
"""
from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .modules.briefing import api as briefing_api, podcast as briefing_podcast
from .modules.ai_usage import api as ai_usage_api
from .modules.organiser import api as organiser_api
from .modules.instagram import api as instagram_api, planapi as instagram_plan
from .modules.brain import api as brain_api
from .modules.brain import meetings
from .modules.career import api as career_api
from .modules.events import api as events_api
from .modules.connections import api as connections_api
from .modules.health import api as health_api
from .modules.today import api as today_api
from .modules.today import summary as today_summary

STATIC = Path(__file__).parent / "static"
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_PODCAST = re.compile(r"/briefing/podcast/(\d{4}-\d{2}-\d{2})\.(mp3|wav)")


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

        def _ranged(self, p: Path, ctype: str):
            """Serves a file with HTTP Range support (one range): 200 full, 206 partial, 416 outside the file."""
            size = p.stat().st_size
            start, end, status = 0, size - 1, HTTPStatus.OK
            m = _RANGE.fullmatch((self.headers.get("Range") or "").strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:  # suffix: the last N bytes
                    start = max(0, size - int(m.group(2)))
                if start >= size or start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1 if size else 0
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            with p.open("rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

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
                if path == "/api/summary":
                    return self._json(today_summary.view(cfg))
                if path == "/api/brain":
                    return self._json(brain_api.view(cfg))
                if path == "/api/brain/meetings":
                    mid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    if not mid:
                        return self._json({"meetings": meetings.list_meetings(cfg), "write": cfg.meetings_write})
                    try:
                        return self._json(meetings.load(cfg, mid))
                    except meetings.UnknownMeeting:
                        return self._json({"error": "meeting not found"}, HTTPStatus.NOT_FOUND)
                    except meetings.SourceError:
                        return self._json({"error": "meeting source could not be read"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                if path in ("/api/briefing", "/api/agenda"):  # /api/agenda: old name, kept as an alias
                    return self._json(briefing_api.view(cfg))
                if path == "/api/organiser":
                    show = parse_qs(urlparse(self.path).query).get("dismissed", [""])[0] == "1"
                    return self._json(organiser_api.view(cfg, show_dismissed=show))
                if path in ("/api/briefing/audio", "/api/agenda/audio"):
                    f = briefing_api.audio_file(cfg) if cfg.modules.get("briefing", False) else None
                    if f is None:
                        return self._json({"error": "no audio"}, HTTPStatus.NOT_FOUND)
                    return self._ranged(f, "audio/mpeg")
                if path.startswith("/briefing/podcast/"):
                    m = _PODCAST.fullmatch(path)
                    f = briefing_podcast.file_for(cfg, m.group(1), m.group(2)) if m and cfg.modules.get("briefing", False) else None
                    if m is None or f is None:
                        return self._json({"error": "no podcast"}, HTTPStatus.NOT_FOUND)
                    return self._ranged(f, "audio/mpeg" if m.group(2) == "mp3" else "audio/wav")
                if path == "/api/health":
                    return self._json(health_api.view(cfg))
                if path == "/api/events":
                    q = parse_qs(urlparse(self.path).query)
                    days = max(1, min(3650, int(q.get("days", ["14"])[0])))
                    return self._json(events_api.view(cfg, days=days))
                if path.startswith("/api/events/") and path.endswith(".ics"):
                    return self._ics(path.split("/")[3].removesuffix(".ics"))
                if path == "/api/career/export.csv":
                    q = parse_qs(urlparse(self.path).query).get("status", [""])[0]
                    body = career_api.export_csv(cfg, q if q in ("open", "closed") else None).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="kokpit-career.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/api/ai_usage"):
                    q = parse_qs(urlparse(self.path).query)
                    num = lambda k, d, lo, hi: max(lo, min(hi, int(q.get(k, [str(d)])[0])))
                    sub = path[len("/api/ai_usage"):].strip("/")
                    if sub == "":
                        return self._json(ai_usage_api.view(cfg))
                    if sub == "sessions":
                        return self._json(ai_usage_api.sessions_view(cfg, num("days", 30, 1, 3650), num("limit", 10, 1, 200)))
                    views = {"advice": ai_usage_api.advice_view, "limits": ai_usage_api.limits_view, "prices": ai_usage_api.prices_view}
                    if sub in views:
                        return self._json(views[sub](cfg))
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                if path == "/api/instagram/plan":
                    return self._json(instagram_plan.view(cfg))
                if path == "/api/instagram":
                    return self._json(instagram_api.view(cfg))
                if path == "/api/career":
                    return self._json(career_api.view(cfg))
                if path == "/api/career/skills":
                    return self._json(career_api.skills_view(cfg))
                if path == "/api/connections":
                    return self._json(connections_api.view(cfg))
                if path == "/api/meta":
                    return self._json({"version": __version__, "modules": cfg.modules, "config": str(cfg.path)})
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:  # show the error on the page instead of a blank card
                return self._json({"error": f"{exc.__class__.__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _career_post(self, path: str):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 400_000:
                    return self._json({"error": "request too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("JSON object expected")
                if path == "/api/career/profile":
                    return self._json(career_api.save_profile(cfg, data))
                if path == "/api/career/skills":
                    return self._json(career_api.save_skill(cfg, data))
                if path == "/api/career/cv":
                    return self._json(career_api.cv_proposal(cfg, str(data.get("text", ""))))
                return self._json(career_api.score_preview(cfg, data))
            except PermissionError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _plan_post(self, path: str):
            """Content calendar writes. JSON content type is required, so a page on another site cannot trigger them."""
            try:
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    return self._json({"error": "Content-Type must be application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                n = int(self.headers.get("Content-Length") or 0)
                if n > 100_000:
                    return self._json({"error": "request too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("JSON object expected")
                parts = path.split("/")[4:]  # /api/instagram/plan[/<id>[/<action>]]
                if not parts:
                    return self._json(instagram_plan.create(cfg, data))
                slot_id, action = int(parts[0]), (parts[1] if len(parts) > 1 else "update")
                if action == "update":
                    return self._json(instagram_plan.update(cfg, slot_id, data))
                if action == "delete":
                    return self._json(instagram_plan.delete(cfg, slot_id))
                if action == "approve":
                    return self._json(instagram_plan.approve(cfg, slot_id))
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
            except LookupError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _meetings_post(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > meetings.MAX_BODY:
                    return self._json({"ok": False, "code": "too_large", "error": "request too large"},
                                      HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                data = json.loads(self.rfile.read(n) or b"{}")
                return self._json(meetings.save(cfg, data))
            except meetings.UnknownMeeting:
                return self._json({"ok": False, "code": "unknown", "error": "meeting not found"}, HTTPStatus.NOT_FOUND)
            except meetings.Conflict as exc:
                return self._json({"ok": False, "code": "conflict", "current": exc.current}, HTTPStatus.CONFLICT)
            except meetings.TooLarge as exc:
                return self._json({"ok": False, "code": exc.code, "error": "text too long"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            except meetings.Invalid as exc:
                return self._json({"ok": False, "code": exc.code, "error": "invalid request"}, HTTPStatus.BAD_REQUEST)
            except meetings.SourceError as exc:
                return self._json({"ok": False, "code": exc.code, "error": "meeting source could not be read"},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
            except PermissionError as exc:
                return self._json({"ok": False, "code": "read_only", "error": str(exc)}, HTTPStatus.FORBIDDEN)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"ok": False, "code": "invalid", "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _organiser_post(self):
            """A decision on one proposal. JSON content type and the X-Kokpit header are required, so a page on
            another site cannot trigger it (a cross-site form can set neither). Writes the database, never a note."""
            try:
                if not (self.headers.get("Content-Type") or "").startswith("application/json") or not self.headers.get("X-Kokpit"):
                    return self._json({"error": "JSON content type and X-Kokpit header required"}, HTTPStatus.FORBIDDEN)
                n = int(self.headers.get("Content-Length") or 0)
                if n > 10_000:
                    return self._json({"error": "request too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("JSON object expected")
                return self._json(organiser_api.decide(cfg, data))
            except LookupError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/organiser/decide":
                return self._organiser_post()
            if path == "/api/instagram/plan" or path.startswith("/api/instagram/plan/"):
                return self._plan_post(path)
            if path in ("/api/career/profile", "/api/career/cv", "/api/career/score", "/api/career/skills"):
                return self._career_post(path)
            if path == "/api/brain/meetings/save":
                return self._meetings_post()
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
