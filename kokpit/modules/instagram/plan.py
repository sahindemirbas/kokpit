"""Content calendar: planned slots in the ig_plan table (create, edit, delete, list, guard).

Statuses: planned, ready (you say it can go out), approved (publishing in progress, set only by the Approve action),
published, failed (the platform's message is kept on the slot). Times are UTC. Nothing here talks to the network.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

KINDS = ("story", "post", "reel", "carousel")
STATUSES = ("planned", "ready", "approved", "published", "failed")
USER_STATUSES = ("planned", "ready")
MAX_CAPTION = 2200
MAX_COLLABS = 3
VIDEO_EXT = (".mp4", ".mov", ".m4v")
USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
COLS = ("id", "due_at", "kind", "owner", "caption", "media_urls", "collabs", "status", "error", "media_id", "permalink",
        "created_at", "updated_at", "approved_at", "published_at")


def is_video(url: str) -> bool:
    return url.split("?")[0].split("#")[0].lower().endswith(VIDEO_EXT)


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _due(v) -> str:
    s = str(v or "").strip().replace(" ", "T").rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    raise ValueError("due must look like 2026-03-10 18:30 (UTC)")


def _urls(v) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[\s,]+", v) if x]
    if not isinstance(v, list):
        raise ValueError("media must be a list of public URLs")
    out = []
    for u in v:
        u = str(u).strip()
        if not re.match(r"^https?://[^\s]+$", u) or len(u) > 2000:
            raise ValueError("every media URL must start with http:// or https:// and contain no spaces")
        out.append(u)
    return out


def _collabs(v) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[\s,]+", v) if x]
    if not isinstance(v, list):
        raise ValueError("collaborators must be a list of usernames")
    out = []
    for c in v:
        c = str(c).strip().lstrip("@")
        if not USERNAME.match(c):
            raise ValueError("a collaborator username may contain letters, digits, dot and underscore only")
        if c not in out:
            out.append(c)
    if len(out) > MAX_COLLABS:
        raise ValueError(f"at most {MAX_COLLABS} collaborators")
    return out


def validate(d: dict) -> dict:
    """Checks a full slot dict (already merged with defaults) and returns the cleaned fields."""
    kind = str(d.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind must be one of: " + ", ".join(KINDS))
    caption = str(d.get("caption") or "").strip()
    if len(caption) > MAX_CAPTION:
        raise ValueError(f"caption is longer than {MAX_CAPTION} characters")
    urls, collabs = _urls(d.get("media_urls") or []), _collabs(d.get("collabs") or [])
    if kind == "carousel" and not 2 <= len(urls) <= 10:
        raise ValueError("a carousel needs 2 to 10 media URLs")
    if kind != "carousel" and len(urls) > 1:
        raise ValueError(f"a {kind} takes one media URL")
    if kind == "post" and urls and is_video(urls[0]):
        raise ValueError("a post is a single image; publish video as a reel")
    if kind == "story" and collabs:
        raise ValueError("collaborators are not supported for stories")
    owner = str(d.get("owner") or "").strip()
    if len(owner) > 60:
        raise ValueError("owner is longer than 60 characters")
    status = str(d.get("status") or "planned").strip().lower()
    if status not in USER_STATUSES:
        raise ValueError("status can be set to planned or ready; approved, published and failed are set by the app")
    return {"due_at": _due(d.get("due")) if "due" in d else _due(d.get("due_at")), "kind": kind, "owner": owner, "caption": caption,
            "media_urls": urls, "collabs": collabs, "status": status}


def row(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in COLS}
    d["media_urls"] = json.loads(d["media_urls"] or "[]")
    d["collabs"] = json.loads(d["collabs"] or "[]")
    return d


def get(conn: sqlite3.Connection, slot_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM ig_plan WHERE id=?", (slot_id,)).fetchone()
    return row(r) if r else None


def create(conn: sqlite3.Connection, data: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    c = validate(data)
    with conn:
        cur = conn.execute("INSERT INTO ig_plan(due_at, kind, owner, caption, media_urls, collabs, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (c["due_at"], c["kind"], c["owner"], c["caption"], json.dumps(c["media_urls"]), json.dumps(c["collabs"]), c["status"],
                            _stamp(now), _stamp(now)))
    return get(conn, cur.lastrowid)


def update(conn: sqlite3.Connection, slot_id: int, data: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cur = get(conn, slot_id)
    if cur is None:
        raise LookupError("no such slot")
    if cur["status"] in ("published", "approved"):
        raise ValueError(f"a {cur['status']} slot cannot be edited")
    merged = {**cur, **{k: v for k, v in data.items() if k in ("due", "due_at", "kind", "owner", "caption", "media_urls", "collabs", "status")}}
    if "due" not in data:
        merged["due"] = merged["due_at"]
    if "status" not in data:
        merged["status"] = "planned" if cur["status"] == "failed" else cur["status"]
    c = validate(merged)
    with conn:
        conn.execute("UPDATE ig_plan SET due_at=?, kind=?, owner=?, caption=?, media_urls=?, collabs=?, status=?, error=CASE WHEN ?='failed' THEN error ELSE NULL END, updated_at=? WHERE id=?",
                     (c["due_at"], c["kind"], c["owner"], c["caption"], json.dumps(c["media_urls"]), json.dumps(c["collabs"]), c["status"], c["status"],
                      _stamp(now), slot_id))
    return get(conn, slot_id)


def delete(conn: sqlite3.Connection, slot_id: int) -> None:
    cur = get(conn, slot_id)
    if cur is None:
        raise LookupError("no such slot")
    if cur["status"] == "approved":
        raise ValueError("a slot that is being published cannot be deleted")
    with conn:
        conn.execute("DELETE FROM ig_plan WHERE id=?", (slot_id,))


def claim(conn: sqlite3.Connection, slot_id: int, now: datetime) -> bool:
    """Atomic ready/failed -> approved. False when another request got there first or the status does not allow it."""
    with conn:
        n = conn.execute("UPDATE ig_plan SET status='approved', error=NULL, approved_at=?, updated_at=? WHERE id=? AND status IN ('ready','failed')",
                         (_stamp(now), _stamp(now), slot_id)).rowcount
    return n == 1


def mark_published(conn, slot_id: int, media_id: str, permalink: str | None, now: datetime) -> None:
    with conn:
        conn.execute("UPDATE ig_plan SET status='published', error=NULL, media_id=?, permalink=?, published_at=?, updated_at=? WHERE id=?",
                     (media_id, permalink, _stamp(now), _stamp(now), slot_id))


def mark_failed(conn, slot_id: int, message: str, now: datetime) -> None:
    with conn:
        conn.execute("UPDATE ig_plan SET status='failed', error=?, updated_at=? WHERE id=?", (message[:500], _stamp(now), slot_id))


def listing(conn: sqlite3.Connection, recent_published: int = 10) -> list[dict]:
    openr = [row(r) for r in conn.execute("SELECT * FROM ig_plan WHERE status!='published' ORDER BY due_at, id")]
    done = [row(r) for r in conn.execute("SELECT * FROM ig_plan WHERE status='published' ORDER BY due_at DESC, id DESC LIMIT ?", (recent_published,))]
    return openr + done


def upcoming(conn: sqlite3.Connection, now: datetime, days: int) -> int:
    """Slots still to go out (planned, ready, approved) due between now and now + days."""
    a, b = now.astimezone(timezone.utc), now.astimezone(timezone.utc) + timedelta(days=days)
    f = "%Y-%m-%dT%H:%M:%S"
    return conn.execute("SELECT COUNT(*) FROM ig_plan WHERE status IN ('planned','ready','approved') AND due_at>=? AND due_at<=?",
                        (a.strftime(f), b.strftime(f))).fetchone()[0]
