"""Kokpit's on-disk contract, read without importing Kokpit.

Mirrors (keep in step; tests in the Kokpit repo guard the drift):
  kokpit/config.py                find_config, load, Config.resolve (paths relative to the config file)
  kokpit/modules/brain/reader.py  Threads.md, To-Do.md, Last-Session.md parsing
  kokpit/modules/brain/capture.py note and task capture rules
  kokpit/modules/brain/receipt.py receipt file format
  kokpit/modules/*/schema.sql     table and column names
The database is always opened read-only. The only writes are capture, the
session card and the receipt, all inside the vault (or the demo copy).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO_SRC = HERE / "demo"
DEFAULT_LOCATIONS = (Path("kokpit.toml"), Path.home() / ".config" / "kokpit" / "kokpit.toml")
MODULE_DEFAULTS = {"today": True, "brain": True, "health": True, "events": False, "jobs": False, "ai_usage": False}
CARD_MAX = 800
FIX_CONFIG = ("set plugins.entries.kokpit.settings.kokpit_config to your kokpit.toml "
              "(hermes config set plugins.entries.kokpit.settings.kokpit_config /path/kokpit.toml), "
              "or run `python -m kokpit hermes setup`, or set demo: on")


class NotReady(Exception):
    def __init__(self, error: str, fix: str):
        super().__init__(error)
        self.error, self.fix = error, fix


def disabled(module: str) -> NotReady:
    return NotReady("module disabled", f"module disabled, enable with [modules] {module} = true in kokpit.toml")


# ---------------------------------------------------------------- config

def find_config(explicit: str = "") -> Path | None:
    if explicit:
        p = Path(os.path.expanduser(explicit))
        return p if p.is_file() else None
    env = os.environ.get("KOKPIT_CONFIG", "").strip()  # documented by Kokpit itself
    if env and Path(os.path.expanduser(env)).is_file():
        return Path(os.path.expanduser(env))
    for p in DEFAULT_LOCATIONS:
        if p.is_file():
            return p
    return None


def load_config(path: Path) -> dict:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    base = Path(path).resolve().parent

    def resolve(p) -> Path:
        q = Path(os.path.expanduser(str(p)))
        return q if q.is_absolute() else base / q

    mods = dict(MODULE_DEFAULTS)
    for k, v in (raw.get("modules") or {}).items():
        mods[k] = bool(v)
    brain, health, ai = raw.get("brain") or {}, raw.get("health") or {}, raw.get("ai_usage") or {}
    return {"path": str(path), "modules": mods,
            "vault": resolve(brain.get("vault_path", "data/vault")),
            "quick_capture": bool(brain.get("quick_capture", True)),
            "db": resolve(health.get("db_path", "data/kokpit.db")),
            "expected": [str(x) for x in health.get("expected", []) if str(x).strip()],
            "prices": resolve(ai["prices_path"]) if ai.get("prices_path") else None}


class Env:
    """Resolves settings into a mode (live or demo) plus vault and database paths."""

    def __init__(self, get_config, data_dir: Path | None = None):
        self._get = get_config
        self._data_dir = data_dir

    def setting(self, key, default):
        try:
            v = self._get(key, default)
        except Exception:
            return default
        return default if v is None else v

    def data_dir(self) -> Path:
        if self._data_dir is None:
            try:
                from plugins.plugin_storage import plugin_data_dir
                self._data_dir = Path(plugin_data_dir("kokpit"))
            except Exception:
                self._data_dir = Path(tempfile.gettempdir()) / "kokpit-hermes-plugin"
        return self._data_dir

    def layout_settings(self) -> dict:
        return {k: str(self.setting(k, "") or "") for k in LAYOUT_SETTINGS}

    def state(self) -> dict:
        """-> {mode, vault, db, cfg, layout}. Raises NotReady with a fix when nothing usable is configured."""
        st = self._state()
        v = st["vault"]
        st["layout"] = resolve_layout(v, {} if st["mode"] == "demo" else self.layout_settings()) \
            if v is not None and Path(v).is_dir() else None
        return st

    def _state(self) -> dict:
        demo = str(self.setting("demo", "auto")).strip().lower() or "auto"
        if demo == "on":
            return self.demo()
        vault_setting = str(self.setting("vault_path", "") or "").strip()
        explicit = str(self.setting("kokpit_config", "") or "").strip()
        cfg_path = find_config(explicit)
        if explicit and cfg_path is None:
            raise NotReady("kokpit.toml not found", f"{explicit} does not exist; " + FIX_CONFIG)
        if cfg_path is not None:
            try:
                cfg = load_config(cfg_path)
            except (OSError, tomllib.TOMLDecodeError, ValueError, TypeError) as e:
                raise NotReady(f"kokpit.toml could not be read: {type(e).__name__}",
                               f"fix the TOML syntax in {cfg_path}")
            if vault_setting:
                cfg["vault"] = Path(os.path.expanduser(vault_setting))
            return {"mode": "live", "vault": cfg["vault"], "db": cfg["db"], "cfg": cfg}
        if vault_setting:
            cfg = {"path": "", "modules": dict(MODULE_DEFAULTS), "vault": Path(os.path.expanduser(vault_setting)),
                   "quick_capture": True, "db": None, "expected": [], "prices": None}
            return {"mode": "live", "vault": cfg["vault"], "db": None, "cfg": cfg}
        if demo == "auto":
            return self.demo()
        raise NotReady("kokpit not configured", FIX_CONFIG)

    def demo(self) -> dict:
        root = self.data_dir() / "demo"
        vault, db = root / "vault", root / "demo.db"
        if not vault.is_dir():
            root.mkdir(parents=True, exist_ok=True)
            copy_demo_vault(vault)
        if not db.is_file():
            build_demo_db(db)
        cfg = {"path": "", "modules": {k: True for k in MODULE_DEFAULTS}, "vault": vault, "quick_capture": True,
               "db": db, "expected": ["weather", "calendar", "events-collect", "career", "ai_usage", "backup"],
               "prices": DEMO_SRC / "prices.json"}
        return {"mode": "demo", "vault": vault, "db": db, "cfg": cfg}


DEMO_DATE = re.compile(r"\{\{([+-]\d+)\}\}")


def copy_demo_vault(dest: Path, today: date | None = None) -> None:
    """Copy demo/vault, turning {{+3}} into the date three days from today so the demo never looks stale."""
    today = today or date.today()
    src = DEMO_SRC / "vault"
    for p in sorted(src.rglob("*")):
        out = Path(dest) / p.relative_to(src)
        if p.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        text = DEMO_DATE.sub(lambda m: date.fromordinal(today.toordinal() + int(m.group(1))).isoformat(),
                             p.read_text(encoding="utf-8"))
        out.write_text(text, encoding="utf-8")


def build_demo_db(path: Path) -> None:
    """Build the demo database from demo/seed.json. "@+2" means today plus 2 days ("@-1T09:00" adds a time)."""
    seed = json.loads((DEMO_SRC / "seed.json").read_text(encoding="utf-8"))
    today = date.today()

    def rel(v):
        if isinstance(v, dict):
            return {k: rel(x) for k, x in v.items()}
        if isinstance(v, list):
            return [rel(x) for x in v]
        if isinstance(v, str) and v.startswith("@"):
            m = re.fullmatch(r"@([+-]\d+)(T.*)?", v)
            if m:
                return date.fromordinal(today.toordinal() + int(m.group(1))).isoformat() + (m.group(2) or "")
        return v

    def fill(v):
        v = rel(v)
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(seed["schema"])
        for table, items in seed["rows"].items():
            for r in items:
                cols = list(r)
                conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                             [fill(r[c]) for c in cols])
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)


# ---------------------------------------------------------------- database (read-only)

def open_db(path: Path | None) -> sqlite3.Connection | None:
    if path is None or not Path(path).is_file():
        return None
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name: str) -> bool:
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def rows(conn, sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------- vault (mirrors brain/reader.py)

WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
ISO_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
DUE = re.compile(r"due:\s*(20\d\d-\d\d-\d\d)", re.I)
TASK = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")
OWNER = re.compile(r"^\s*(?:on\s+(you|me|[\w .'-]+?)|(Sende|Bende|Başkası))\b[^:]*:\s*", re.I)
WAIT_WORDS = re.compile(r"\b(approve|approval|decide|decision|waiting on you|onay|karar)\b", re.I)
ACTIVE_HEADINGS = ("## Active Threads", "## Active threads", "## Aktif")
CLOSED_HEADINGS = ("## Closed Threads", "## Closed threads", "## Kapanan")
OPEN_MARKERS = ("Open:", "Açık:")
STATUS_MARKERS = ("Where we left off", "Nerede kaldık")
SOURCE_MARKERS = ("Source:", "Kaynak:")
EARLIER_HEADINGS = ("## Earlier sessions", "## Önceki oturumlar")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, UnicodeDecodeError):
        return ""


def clean(s: str, n: int | None = None) -> str:
    s = WIKILINK.sub(lambda m: m.group(2) or m.group(1).split("/")[-1], s)
    s = re.sub(r"\s+", " ", s).strip()
    if n and len(s) > n:
        s = s[: n - 3].rstrip() + "..."
    return s


def _cut_at(text: str, markers) -> tuple[str, str]:
    best = None
    for m in markers:
        i = text.find(m)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, m)
    if best is None:
        return text, ""
    return text[:best[0]], text[best[0] + len(best[1]):]


def parse_threads(text: str) -> list[dict]:
    for m in ACTIVE_HEADINGS:
        if m in text:
            text = text.split(m, 1)[1]
            break
    for m in CLOSED_HEADINGS:
        text = text.split(m, 1)[0]
    out = []
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        body = body.strip()
        before_open, open_part = _cut_at(body, OPEN_MARKERS)
        open_part, _ = _cut_at(open_part, SOURCE_MARKERS)
        status, _ = _cut_at(before_open, SOURCE_MARKERS)
        for m in STATUS_MARKERS:
            status = status.replace(m, "", 1)
        if status.lstrip().startswith("("):
            status = re.sub(r"^\s*\(?([^):]*)\)?:\s*", "", status.strip(), count=1)
        status = status.strip().lstrip(":").strip()
        d = ISO_DATE.search(body)
        opens = []
        for item in re.split(r";|\n", open_part):
            item = item.strip().rstrip(".")
            if len(item) < 3:
                continue
            who = OWNER.match(item)
            txt = item[who.end():] if who else item
            owner = ""
            if who:
                g = (who.group(1) or who.group(2) or "").strip().lower()
                owner = {"sende": "you", "bende": "me", "başkası": "someone"}.get(g, g)
            due = ISO_DATE.search(txt)
            opens.append({"text": clean(txt, 260), "owner": owner,
                          "on_you": owner == "you" or (not owner and bool(WAIT_WORDS.search(item))),
                          "due": due.group(1) if due else ""})
        out.append({"title": clean(title), "date": d.group(1) if d else "", "status": clean(status, 520), "open": opens})
    return out


TODO_OPEN = ("open", "açık", "acik", "to do", "todo", "aperti", "da fare")
TODO_DONE = ("done", "bitti", "tamamlanan", "tamamlandı", "completed", "fatto", "fatti")
_H2 = re.compile(r"^## +(.+?)\s*$", re.M)


def _heading_kind(title: str) -> str:
    t = re.sub(r"[^\w ]", "", title.casefold(), flags=re.U).strip()
    if any(t == w or t.startswith(w + " ") for w in TODO_OPEN):
        return "open"
    if any(t == w or t.startswith(w + " ") for w in TODO_DONE):
        return "done"
    return ""


def todo_open_heading(text: str):
    """The first '## Open' style heading (Open, Açık, To do, Aperti ...) or None."""
    for m in _H2.finditer(text):
        if _heading_kind(m.group(1)) == "open":
            return m
    return None


def parse_todo(text: str, today: date | None = None) -> list[dict]:
    """Unchecked tasks. With an Open-style heading only that section counts; without one every
    section counts except Done-style ones (Done, Bitti, Fatto ...)."""
    today = today or date.today()
    m = todo_open_heading(text)
    if m:
        section = re.split(r"^## ", text[m.end():], maxsplit=1, flags=re.M)[0]
    else:
        keep, on = [], True
        for line in text.splitlines():
            h = _H2.match(line)
            if h:
                on = _heading_kind(h.group(1)) != "done"
            elif on:
                keep.append(line)
        section = "\n".join(keep)
    out = []
    for line in section.splitlines():
        m = TASK.match(line)
        if not m or m.group(1).lower() == "x":
            continue
        due = DUE.search(m.group(2))
        due_s = due.group(1) if due else ""
        days = None
        if due_s:
            try:
                days = (date.fromisoformat(due_s) - today).days
            except ValueError:
                due_s = ""
        out.append({"text": clean(DUE.sub("", m.group(2)).replace("()", "").strip(" -")), "due": due_s,
                    "days_left": days, "overdue": days is not None and days < 0})
    out.sort(key=lambda t: (t["due"] == "", t["due"]))
    return out


def parse_sessions(text: str, limit: int = 8) -> list[dict]:
    for h in EARLIER_HEADINGS:
        text = text.split(h, 1)[0]
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        parts = [p.strip() for p in head.split("·")]
        if len(parts) < 2:
            continue
        out.append({"at": parts[0], "title": clean(parts[1]), "session": parts[2] if len(parts) > 2 else "",
                    "text": clean(body, 300)})
    return out[:limit]


def upcoming(threads, todo, today: date | None = None, horizon: int = 120) -> list[dict]:
    today = today or date.today()
    hits = [{"date": t["due"], "text": t["text"], "from": "To-Do"} for t in todo if t["due"]]
    hits += [{"date": o["due"], "text": o["text"], "from": th["title"]} for th in threads for o in th["open"] if o["due"]]
    out = []
    for h in hits:
        try:
            days = (date.fromisoformat(h["date"]) - today).days
        except ValueError:
            continue
        if -1 <= days <= horizon:
            out.append({**h, "days_left": days})
    return sorted(out, key=lambda h: h["date"])


_CACHE: dict = {}


def _file(layout: dict | None, vault: Path, key: str) -> Path | None:
    """Path of a layout file (None when ambiguous); the root default when no layout was resolved."""
    if layout is None:
        return Path(vault) / FILES[key][1]
    return layout[key]["path"]


def read_vault(vault: Path, today: date | None = None, layout: dict | None = None) -> dict:
    """Parsed Threads, To-Do and Last-Session, cached by file modification times and sizes."""
    vault = Path(vault)
    paths = [_file(layout, vault, k) for k in ("threads", "todo", "last_session")]

    def stamp(p: Path | None):
        try:
            st = p.stat() if p is not None else None
            return (str(p), st.st_mtime_ns, st.st_size) if st else (None, 0, 0)
        except OSError:
            return (str(p), 0, 0)

    key = (str(vault), str(today or date.today()), tuple(stamp(p) for p in paths))
    if _CACHE.get("key") == key:
        return _CACHE["value"]
    text = [read(p) if p is not None else "" for p in paths]
    threads = parse_threads(text[0])
    todo = parse_todo(text[1], today)
    value = {"exists": vault.is_dir(), "threads": threads, "todo": todo,
             "sessions": parse_sessions(text[2]), "upcoming": upcoming(threads, todo, today)}
    _CACHE.update(key=key, value=value)
    return value


# ---------------------------------------------------------------- vault layout (settings, then root, then discovery)

SKIP_DIRS = {"receipts", "archive", ".obsidian", ".git", ".trash"}
FILES = {"threads": ("threads_file", "Threads.md"), "todo": ("todo_file", "To-Do.md"),
         "last_session": ("last_session_file", "Last-Session.md")}
DIRS = {"receipts": ("receipts_dir", "receipts"), "inbox": ("inbox_dir", "inbox")}
LAYOUT_SETTINGS = tuple(s for s, _ in (*FILES.values(), *DIRS.values()))
DISCOVER_DEPTH = 4          # folders below the vault root
DISCOVER_MAX_ENTRIES = 20000
DISCOVER_TTL = 120          # seconds a discovery result is reused
_FOUND: dict = {}


def in_vault(vault: Path, value: str) -> Path:
    """A layout setting: absolute (or ~) as given, otherwise relative to the vault."""
    p = Path(os.path.expanduser(str(value).strip()))
    return p if p.is_absolute() else Path(vault) / p


def discover(vault: Path, name: str, depth: int = DISCOVER_DEPTH, max_entries: int = DISCOVER_MAX_ENTRIES) -> list[Path]:
    """Files called `name` (case-insensitive) under the vault, breadth first. Never follows symbolic
    links, skips hidden folders and archive/receipts, stops after depth folders, max_entries entries
    or the third match (two are enough to call it ambiguous)."""
    want, hits, seen = name.casefold(), [], 0
    level = [Path(vault)]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                seen += 1
                if seen > max_entries:
                    return hits
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith(".") and e.name.lower() not in SKIP_DIRS:
                            nxt.append(Path(e.path))
                    elif e.name.casefold() == want and e.is_file(follow_symlinks=False):
                        hits.append(Path(e.path))
                        if len(hits) >= 3:
                            return hits
                except OSError:
                    continue
        level = nxt
        if not level:
            break
    return hits


def _discover_cached(vault: Path, name: str) -> list[Path]:
    key = (str(vault), name)
    hit = _FOUND.get(key)
    now = datetime.now().timestamp()
    if hit and now - hit[0] < DISCOVER_TTL and all(p.is_file() for p in hit[1]):
        return hit[1]
    found = discover(vault, name)
    _FOUND[key] = (now, found)
    return found


def resolve_layout(vault: Path, settings: dict | None = None) -> dict:
    """Where Threads, To-Do, Last-Session, receipts/ and inbox/ live in this vault.

    Order: the plugin setting (vault-relative or absolute), the vault root, then a single match
    found by discover(). Several matches -> how="ambiguous", path None: reads see nothing and
    writes refuse until the setting is filled in. No match -> the root path (created on write)."""
    vault, settings = Path(vault), settings or {}
    out: dict = {}
    for key, (setting, name) in FILES.items():
        val = str(settings.get(setting) or "").strip()
        item = {"name": name, "setting": setting, "candidates": []}
        if val:
            item.update(path=in_vault(vault, val), how="setting")
        elif (vault / name).is_file():
            item.update(path=vault / name, how="root")
        else:
            hits = _discover_cached(vault, name) if vault.is_dir() else []
            if len(hits) == 1:
                item.update(path=hits[0], how="discovered")
            elif hits:
                item.update(path=None, how="ambiguous", candidates=hits)
            else:
                item.update(path=vault / name, how="default")
        item["found"] = item["path"] is not None and item["path"].is_file()
        out[key] = item
    for key, (setting, name) in DIRS.items():
        val = str(settings.get(setting) or "").strip()
        p = in_vault(vault, val) if val else vault / name
        out[key] = {"name": name + "/", "setting": setting, "candidates": [], "path": p,
                    "how": "setting" if val else "default", "found": p.is_dir()}
    return out


def rel(vault: Path, p: Path | None) -> str:
    """Vault-relative posix path when inside the vault, otherwise the path as given."""
    if p is None:
        return ""
    try:
        return Path(p).relative_to(Path(vault)).as_posix()
    except ValueError:
        return str(p)


def layout_report(vault: Path, layout: dict) -> dict:
    """JSON-friendly view: which vault files were found, where, and how."""
    out = {}
    for key, item in layout.items():
        r = {"file": rel(vault, item["path"]) if item["path"] is not None else "",
             "found": item["found"], "how": item["how"]}
        if item["how"] == "ambiguous":
            r["candidates"] = [rel(vault, p) for p in item["candidates"]]
            r["fix"] = f"several {item['name']} files; set plugins.entries.kokpit.settings.{item['setting']}"
        out[key] = r
    return out


def ambiguous(layout: dict | None, key: str) -> NotReady | None:
    if layout is None or layout[key]["how"] != "ambiguous":
        return None
    item = layout[key]
    return NotReady(f"{item['name']} is ambiguous ({len(item['candidates'])} matches in the vault)",
                    f"set plugins.entries.kokpit.settings.{item['setting']} to the right file, vault-relative or absolute")


def note_files(vault: Path, extra_dirs=()) -> list[Path]:
    vault = Path(vault)
    out = sorted(vault.glob("*.md"))
    subs = [vault / "notes", vault / "inbox"] + [Path(d) for d in extra_dirs if d]
    seen = set()
    for sub in subs:
        try:
            key = sub.resolve()
            sub.relative_to(vault)
        except (OSError, ValueError):
            continue  # outside the vault: never searched
        if key in seen or not sub.is_dir():
            continue
        seen.add(key)
        out += [p for p in sorted(sub.rglob("*.md")) if not (set(p.relative_to(vault).parts) & SKIP_DIRS)]
    return out


def note_meta(p: Path, text: str) -> tuple[str, list[str]]:
    title, aliases = p.stem, []
    fm = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if fm:
        m = re.search(r"^aliases:\s*\[(.*?)\]\s*$", fm.group(1), re.M)
        if m:
            aliases = [a.strip().strip("'\"") for a in m.group(1).split(",") if a.strip()]
        else:
            block = re.search(r"^aliases:\s*\n((?:\s*-\s*.+\n?)+)", fm.group(1), re.M)
            if block:
                aliases = [a.strip().lstrip("-").strip().strip("'\"") for a in block.group(1).splitlines() if a.strip()]
    h = re.search(r"^# (.+)$", text, re.M)
    if h:
        title = h.group(1).strip()
    return title, aliases


def search(vault: Path, query: str, limit: int = 5, extra_dirs=()) -> list[dict]:
    words = [w for w in re.findall(r"\w+", str(query).lower(), re.U) if len(w) > 1]
    if not words or not Path(vault).is_dir():
        return []
    out = []
    for p in note_files(vault, extra_dirs):
        text = read(p)
        title, aliases = note_meta(p, text)
        hay = (title + " " + " ".join(aliases) + " " + p.stem + " " + text).lower()
        if not all(w in hay for w in words):
            continue
        line_no, snippet = 0, ""
        for i, line in enumerate(text.splitlines(), 1):
            if any(w in line.lower() for w in words) and line.strip() != "---":
                line_no, snippet = i, clean(line, 200)
                break
        score = sum(hay.count(w) for w in words) + 5 * sum(w in title.lower() for w in words)
        out.append({"path": p.relative_to(vault).as_posix(), "title": title, "line": line_no, "snippet": snippet,
                    "_score": score})
    out.sort(key=lambda r: (-r["_score"], r["path"]))
    for r in out:
        r.pop("_score")
    return out[:limit]


# ---------------------------------------------------------------- writes (mirror brain/capture.py, brain/receipt.py)

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".kokpit-tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:48] or "note"


def capture_note(vault: Path, text: str, now: datetime | None = None, inbox: Path | None = None) -> Path:
    text = CONTROL.sub("", text).strip()
    if not text or len(text) > 2000:
        raise ValueError("note must be 1-2000 characters")
    now = now or datetime.now()
    inbox = Path(inbox) if inbox is not None else Path(vault) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    base = f"{now:%Y-%m-%d-%H%M}-{_slug(text.splitlines()[0])}"
    p, n = inbox / f"{base}.md", 1
    while p.exists():
        n += 1
        p = inbox / f"{base}-{n}.md"
    atomic_write(p, f"---\ncaptured: {now.isoformat(timespec='seconds')}\nsource: kokpit quick capture\n---\n{text}\n")
    return p


def capture_task(vault: Path, text: str, due: str = "", path: Path | None = None) -> str:
    text = " ".join(CONTROL.sub("", text).split())
    if not text or len(text) > 300:
        raise ValueError("task must be 1-300 characters")
    if due and not re.fullmatch(r"20\d\d-\d\d-\d\d", due):
        raise ValueError("due must be YYYY-MM-DD")
    line = f"- [ ] {text}" + (f" (due: {due})" if due else "")
    p = Path(path) if path is not None else Path(vault) / "To-Do.md"
    body = read(p) if p.exists() else "# To-Do\n\n## Open\n"
    h = todo_open_heading(body)  # the vault's own heading (## Open, ## Açık, ## To do ...)
    if not h:
        body = body.rstrip("\n") + "\n\n## Open\n"
        h = todo_open_heading(body)
    head, tail = body[: h.end()], body[h.end():]
    m = re.search(r"^## ", tail, re.M)
    if m:
        tail = tail[: m.start()].rstrip("\n") + "\n" + line + "\n\n" + tail[m.start():]
    else:
        tail = tail.rstrip("\n") + "\n" + line + "\n"
    atomic_write(p, head + tail)
    return line


def safe_session(s) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(s or ""))[:64]


def write_card(vault: Path, session: str, title: str, lines: list[str], now: datetime | None = None,
               path: Path | None = None) -> str:
    """Insert or update this session's card at the top of Last-Session.md (newest first, one card per session id)."""
    now = now or datetime.now()
    card = (f"## {now:%Y-%m-%d %H:%M} · {title} · {session}\n" + "\n".join(lines))[:CARD_MAX].rstrip() + "\n"
    p = Path(path) if path is not None else Path(vault) / "Last-Session.md"
    blocks = re.split(r"(?=^## )", read(p), flags=re.M)
    pre, kept = blocks[0], []
    for b in blocks[1:]:
        parts = [x.strip() for x in b.split("\n", 1)[0][3:].split("·")]
        if len(parts) >= 3 and parts[2] == session:
            continue
        kept.append(b if b.endswith("\n") else b + "\n")
    if pre.strip() and not pre.endswith("\n\n"):
        pre = pre.rstrip("\n") + "\n\n"
    atomic_write(p, pre + card + ("\n" if kept else "") + "".join(kept))
    return card


def write_receipt(vault: Path, event_id: str, summary: str, refs: list[str], session: str,
                  folder: Path | None = None) -> dict:
    """Same file format as kokpit/modules/brain/receipt.py: receipts/<sha256(event id)>.md with a JSON block."""
    name = hashlib.sha256(event_id.encode("utf-8")).hexdigest() + ".md"
    folder = Path(folder) if folder is not None else Path(vault) / "receipts"
    path = folder / name
    rel_ = rel(vault, path)
    if path.exists():
        return {"status": "unchanged", "path": rel_}
    meta = {"kind": "receipt", "event_id": event_id, "harness": "hermes", "refs": refs,
            "created_at": datetime.now(timezone.utc).isoformat()}
    if session:
        meta["session"] = session
    atomic_write(path, "---\n" + json.dumps(meta, indent=2, ensure_ascii=False) + "\n---\n" + summary.strip()[:4000] + "\n")
    return {"status": "succeeded", "path": rel_}


# ---------------------------------------------------------------- context block (mirrors brain/context.py priorities)

def context_block(vault: Path, message, max_chars: int, today: date | None = None, full: bool = True,
                  layout: dict | None = None) -> str:
    """Threads, last session, upcoming dates and related notes, cut to max_chars.
    full=False keeps only the notes related to this message (used on later turns)."""
    if max_chars <= 0 or not Path(vault).is_dir():
        return ""
    v = read_vault(vault, today, layout) if full else {"threads": [], "sessions": [], "upcoming": []}
    parts = []
    if v["threads"]:
        lines = ["## Active threads"]
        for t in v["threads"][:6]:
            lines.append(f"- {t['title']}: {clean(t['status'], 160)}")
            for o in t["open"][:3]:
                who = f"on {o['owner']}: " if o["owner"] else ""
                lines.append(f"  - {who}{clean(o['text'], 120)}")
        parts.append("\n".join(lines))
    if v["sessions"]:
        s = v["sessions"][0]
        parts.append(f"## Last session\n- {s['at']} {s['title']}: {clean(s['text'], 240)}")
    if v["upcoming"]:
        parts.append("## Upcoming\n" + "\n".join(f"- {u['date']}: {clean(u['text'], 100)} ({u['from']})"
                                                 for u in v["upcoming"][:6]))
    if isinstance(message, str) and message.strip():
        seen: dict = {}
        for w in sorted(set(re.findall(r"\w{4,}", message.lower())), key=len, reverse=True)[:6]:
            inbox = rel(vault, layout["inbox"]["path"]).rstrip("/") + "/" if layout else "inbox/"
            for h in search(vault, w, 3, [layout["inbox"]["path"]] if layout else ()):
                if h["path"].startswith(("notes/", "inbox/", inbox)):
                    seen.setdefault(h["path"], h)
        if seen:
            parts.append("## Related notes\n" + "\n".join(f"- {h['path']}: {h['snippet']}" for h in list(seen.values())[:3]))
    if not parts:
        return ""
    block = "Kokpit brain context (read-only):\n\n" + "\n\n".join(parts)
    if len(block) > max_chars:
        block = block[: max(0, max_chars - 4)].rstrip() + "\n..."
    return block
