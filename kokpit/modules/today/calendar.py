"""Calendar connection: read-only ICS feeds. Fetched by the ``calendar`` job, read by Today.

An ICS address (for example Google's "secret address in iCal format") is a
secret: anyone holding it can read the calendar. It is therefore never written
to the database, the health log, an error message or an API answer; only
``mask(url)`` (scheme + host) is ever shown.

The parser is deliberately small and stdlib-only:
- line unfolding, text unescaping, quoted parameters;
- VEVENT with SUMMARY, LOCATION, DTSTART, DTEND or DURATION, STATUS:CANCELLED;
- all-day ``DATE`` values, UTC ``Z`` times, ``TZID`` times (zoneinfo), floating times;
- RRULE with FREQ=DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL and BYDAY
  for weekly rules; EXDATE; RECURRENCE-ID overrides.
Any other RRULE part (BYMONTHDAY, BYSETPOS, BYDAY on a monthly rule, ...) is not
expanded: the first occurrence is shown and the event is counted in
``unsupported_rrule`` so the job can warn about it.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ... import fetch

WINDOW_DAYS = 7          # today + the next 6 days
MAX_OCCURRENCES = 40000  # per event, guards against runaway rules (a daily rule from ~1920)
SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched TEXT NOT NULL,
    ok INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
SUPPORTED_RULE = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "WKST"}


class ICSError(ValueError):
    """The body is not a usable iCalendar file."""


def mask(url: str) -> str:
    """What may be shown of an ICS address: scheme and host, never the path or query."""
    try:
        p = urlsplit(url.strip())
        host = p.hostname or ""
    except ValueError:
        return "calendar"
    return f"{p.scheme}://{host}/…" if p.scheme and host else "calendar"


# ---------------------------------------------------------------- parsing

def unfold(text: str) -> list[str]:
    """RFC 5545 3.1: a line starting with a space or tab continues the previous one."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _split(line: str) -> tuple[str, dict, str]:
    """``NAME;P1=a;P2="b:c":value`` -> (NAME, {P1: a, P2: b:c}, value)."""
    q, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            q = not q
        elif ch == ":" and not q:
            cut = i
            break
    if cut < 0:
        raise ICSError("content line without a value")
    head, value = line[:cut], line[cut + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def _unescape(v: str) -> str:
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out).strip()


def _zone(tzid: str | None, problems: set) -> tzinfo | None:
    if not tzid:
        return None
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        problems.add(f"unknown TZID {tzid[:40]}, treated as local time")
        return None


def parse_value(value: str, params: dict, problems: set) -> date | datetime:
    """DATE -> date; DATE-TIME -> aware datetime (Z or TZID) or naive (floating, local)."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and v.isdigit()):
        return datetime.strptime(v[:8], "%Y%m%d").date()
    if v.endswith("Z"):
        return datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
    tz = _zone(params.get("TZID"), problems)
    return dt.replace(tzinfo=tz) if tz else dt


def parse_duration(v: str) -> timedelta:
    """``P1D``, ``PT1H30M``, ``P2W``, ``-PT15M``."""
    v = v.strip().upper()
    sign = -1 if v.startswith("-") else 1
    v = v.lstrip("+-")
    if not v.startswith("P"):
        raise ICSError("bad DURATION")
    num, total, in_time = "", timedelta(), False
    units = {"W": timedelta(weeks=1), "D": timedelta(days=1), "H": timedelta(hours=1),
             "M": timedelta(minutes=1), "S": timedelta(seconds=1)}
    for ch in v[1:]:
        if ch == "T":
            in_time = True
        elif ch.isdigit():
            num += ch
        elif ch in units and num:
            if ch == "M" and not in_time:
                raise ICSError("months are not a fixed DURATION")
            total += int(num) * units[ch]
            num = ""
        else:
            raise ICSError("bad DURATION")
    return sign * total


def parse(text: str) -> tuple[list[dict], set]:
    """Raw VEVENTs (no expansion) and a set of short problem notes. Raises ICSError on a non-ICS body."""
    lines = unfold(text.lstrip("\ufeff"))
    if not lines or lines[0].strip().upper() != "BEGIN:VCALENDAR":
        raise ICSError("not an iCalendar file")
    events, problems, cur, depth = [], set(), None, 0
    for line in lines:
        try:
            name, params, value = _split(line)
        except ICSError:
            problems.add("malformed line skipped")
            continue
        if name == "BEGIN":
            if value.upper() == "VEVENT" and cur is None:
                cur, depth = {"exdates": [], "_bad": False}, 0
            elif cur is not None:
                depth += 1  # VALARM and friends inside an event
            continue
        if name == "END":
            if cur is not None and depth:
                depth -= 1
            elif cur is not None and value.upper() == "VEVENT":
                if cur.get("start") is None or cur["_bad"]:
                    problems.add("event without a usable start skipped")
                else:
                    cur.pop("_bad")
                    events.append(cur)
                cur = None
            continue
        if cur is None or depth:
            continue
        try:
            if name == "SUMMARY":
                cur["title"] = _unescape(value)
            elif name == "LOCATION":
                cur["location"] = _unescape(value)
            elif name == "UID":
                cur["uid"] = value.strip()
            elif name == "STATUS":
                cur["status"] = value.strip().upper()
            elif name == "DTSTART":
                cur["start"] = parse_value(value, params, problems)
            elif name == "DTEND":
                cur["end"] = parse_value(value, params, problems)
            elif name == "DURATION":
                cur["duration"] = parse_duration(value)
            elif name == "RRULE":
                cur["rrule"] = value.strip()
            elif name == "EXDATE":
                cur["exdates"].extend(parse_value(x, params, problems) for x in value.split(",") if x.strip())
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = parse_value(value, params, problems)
        except (ValueError, ICSError):
            if name == "DTSTART":
                cur["_bad"] = True
            else:
                problems.add(f"bad {name} value ignored")
    return events, problems


# ---------------------------------------------------------------- expansion

def _rule(text: str) -> dict:
    out = {}
    for part in text.split(";"):
        k, _, v = part.partition("=")
        if k:
            out[k.strip().upper()] = v.strip().upper()
    return out


def _add_months(d: datetime | date, n: int):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    try:
        return d.replace(year=y, month=m)
    except ValueError:
        return None  # e.g. the 31st in a 30-day month: skipped, as RFC 5545 says


def _key(v, tz: tzinfo | None):
    """Comparable key for EXDATE / RECURRENCE-ID matching."""
    if isinstance(v, datetime):
        return _local(v, tz).replace(second=0, microsecond=0)
    return v


def _local(v: datetime, tz: tzinfo | None) -> datetime:
    """Aware -> naive wall time in ``tz`` (None = this machine's zone). Naive stays as is (floating)."""
    if v.tzinfo is None:
        return v
    return (v.astimezone(tz) if tz else v.astimezone()).replace(tzinfo=None)


def _until(raw: str, start, problems: set):
    try:
        u = parse_value(raw, {}, problems)
    except ValueError:
        return None
    if isinstance(start, datetime) and not isinstance(u, datetime):
        u = datetime.combine(u, time(23, 59, 59), start.tzinfo)
    elif isinstance(start, datetime) and isinstance(u, datetime):
        if start.tzinfo is None and u.tzinfo is not None:
            u = _local(u, None)
        elif start.tzinfo is not None and u.tzinfo is None:
            u = u.replace(tzinfo=start.tzinfo)
    elif not isinstance(start, datetime) and isinstance(u, datetime):
        u = u.date()
    return u


def occurrences(ev: dict, window_end, problems: set) -> tuple[list, bool]:
    """Start values of an event up to ``window_end`` (same type as DTSTART). Second value: rule unsupported."""
    start = ev["start"]
    if not ev.get("rrule"):
        return [start], False
    r = _rule(ev["rrule"])
    freq = r.get("FREQ")
    extra = set(r) - SUPPORTED_RULE
    byday = [x for x in r.get("BYDAY", "").split(",") if x]
    if (freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") or extra
            or (byday and (freq != "WEEKLY" or any(x not in DAYS for x in byday)))):
        return [start], True
    try:
        interval = max(1, int(r.get("INTERVAL", "1")))
        count = int(r["COUNT"]) if "COUNT" in r else None
    except ValueError:
        return [start], True
    until = _until(r["UNTIL"], start, problems) if "UNTIL" in r else None

    def candidates():
        if freq == "WEEKLY" and byday:
            wds = sorted(DAYS[x] for x in byday)
            week0 = start - timedelta(days=start.weekday())
            for w in range(MAX_OCCURRENCES):
                for wd in wds:
                    c = week0 + timedelta(days=w * interval * 7 + wd)
                    if c >= start:
                        yield c
            return
        for k in range(MAX_OCCURRENCES):
            if freq == "DAILY":
                yield start + timedelta(days=k * interval)
            elif freq == "WEEKLY":
                yield start + timedelta(weeks=k * interval)
            elif freq == "MONTHLY":
                c = _add_months(start, k * interval)
                if c is not None:
                    yield c
            else:
                c = _add_months(start, 12 * k * interval)
                if c is not None:
                    yield c

    out, n = [], 0
    for c in candidates():
        if until is not None and c > until:
            break
        if count is not None and n >= count:
            break
        n += 1
        if _cmp_key(c) >= _cmp_key(window_end):
            break
        out.append(c)
    return out, False


def _cmp_key(v):
    """Make dates, naive and aware datetimes comparable (used only against the window end)."""
    if isinstance(v, datetime):
        return _local(v, None) if v.tzinfo else v
    return datetime.combine(v, time())


def expand(events: list[dict], today: date, days: int = WINDOW_DAYS, tz: tzinfo | None = None,
           calendar: int = 1) -> tuple[list[dict], int]:
    """Occurrences that touch ``[today, today + days)``, as local wall-clock rows, sorted.

    Returns ``(rows, unsupported_rrule_count)``. ``tz`` is the display zone (None = this machine).
    """
    win_start = datetime.combine(today, time())
    win_end = win_start + timedelta(days=days)
    problems: set = set()
    overridden: dict[str, set] = {}
    for ev in events:
        if ev.get("recurrence_id") is not None and ev.get("uid"):
            overridden.setdefault(ev["uid"], set()).add(_key(ev["recurrence_id"], tz))
    rows, unsupported = [], 0
    for ev in events:
        if ev.get("status") == "CANCELLED":
            continue
        start = ev["start"]
        all_day = not isinstance(start, datetime)
        end = ev.get("end")
        if end is not None and isinstance(end, datetime) == isinstance(start, datetime):
            if isinstance(start, datetime) and (start.tzinfo is None) != (end.tzinfo is None):
                length = _cmp_key(end) - _cmp_key(start)
            else:
                length = end - start
        elif ev.get("duration") is not None:
            length = ev["duration"]
        else:
            length = timedelta(days=1) if all_day else timedelta()
        length = max(length, timedelta())
        # the rule is expanded a day past the window end; rows are filtered below in the display zone
        slack = win_end + timedelta(days=1)
        starts, bad = occurrences(ev, slack.date() if all_day else slack, problems)
        if bad:
            unsupported += 1
        skip = {_key(x, tz) for x in ev.get("exdates", [])}
        if ev.get("rrule") and ev.get("uid") in overridden:
            skip |= overridden[ev["uid"]]
        for s in starts:
            if _key(s, tz) in skip:
                continue
            if all_day:
                d0, d1 = s, s + max(length, timedelta(days=1))
                d = max(d0, today)
                while d < d1 and d < win_end.date():
                    rows.append({"date": d.isoformat(), "start": None, "end": None, "all_day": True,
                                 "title": ev.get("title") or "(no title)", "location": ev.get("location") or "",
                                 "calendar": calendar})
                    d += timedelta(days=1)
                continue
            ls = _local(s, tz)
            le = _local(s + length, tz) if s.tzinfo else s + length
            ended = le <= win_start if length else ls < win_start
            if ended or ls >= win_end:
                continue
            shown = max(ls, win_start)
            rows.append({"date": shown.date().isoformat(), "start": ls.strftime("%H:%M") if ls >= win_start else "…",
                         "end": le.strftime("%H:%M") if length else None, "all_day": False,
                         "title": ev.get("title") or "(no title)", "location": ev.get("location") or "",
                         "calendar": calendar})
    rows.sort(key=lambda r: (r["date"], not r["all_day"], r["start"] or "", r["title"]))
    return rows, unsupported


# ---------------------------------------------------------------- job + cache

def collect(urls: list[str], today: date | None = None, tz: tzinfo | None = None, get=None) -> dict:
    """Fetch and expand every feed. Per-feed failures become masked error strings, never raise with a URL."""
    today = today or date.today()
    get = get or fetch.get
    rows, errors, warnings, unsupported, ok_feeds = [], [], [], 0, 0
    for i, url in enumerate(urls, 1):
        label = f"calendar {i} ({mask(url)})"
        try:
            body = get(url, timeout=25)
        except Exception as exc:  # the URL must not travel with the exception
            errors.append(f"{label}: {_reason(exc)}")
            continue
        try:
            events, problems = parse(body)
        except ICSError as exc:
            errors.append(f"{label}: {exc}")
            continue
        r, bad = expand(events, today, tz=tz, calendar=i)
        ok_feeds += 1
        rows.extend(r)
        unsupported += bad
        if bad:
            warnings.append(f"{label}: {bad} repeating event(s) with an unsupported RRULE, first occurrence only")
        warnings.extend(f"{label}: {p}" for p in sorted(problems))
    rows.sort(key=lambda r: (r["date"], not r["all_day"], r["start"] or "", r["title"]))
    return {"window_start": today.isoformat(), "days": WINDOW_DAYS, "events": rows, "feeds": len(urls),
            "feeds_ok": ok_feeds, "errors": errors, "warnings": warnings, "unsupported_rrule": unsupported}


def _reason(exc: Exception) -> str:
    """Short failure reason without the URL (HTTP code or exception class)."""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return f"HTTP {code}"
    reason = getattr(exc, "reason", None)
    if reason is not None and not isinstance(reason, str):
        return type(reason).__name__
    return type(exc).__name__


def save(conn: sqlite3.Connection, payload: dict, ok: bool, now: str | None = None) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO calendar(fetched, ok, payload) VALUES (?,?,?)",
                 (now or datetime.now().isoformat(timespec="seconds"), int(bool(ok)), json.dumps(payload, ensure_ascii=False)))
    conn.execute("DELETE FROM calendar WHERE id NOT IN (SELECT id FROM calendar ORDER BY id DESC LIMIT 7)")
    conn.commit()


def latest(conn: sqlite3.Connection, now: datetime | None = None) -> dict | None:
    """Last fetch, plus the last successful one when the last fetch failed completely."""
    now = now or datetime.now()
    try:
        last = conn.execute("SELECT fetched, ok, payload FROM calendar ORDER BY id DESC LIMIT 1").fetchone()
        good = conn.execute("SELECT fetched, ok, payload FROM calendar WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not last:
        return None
    out = {"last_ok": bool(last["ok"]), "errors": json.loads(last["payload"]).get("errors", []), "attempted": last["fetched"]}
    if good:
        p = json.loads(good["payload"])
        age_h = (now - datetime.fromisoformat(good["fetched"])).total_seconds() / 3600
        out.update({"events": p.get("events", []), "fetched": good["fetched"], "age_hours": round(age_h, 1),
                    "stale": age_h > 24, "feeds": p.get("feeds", 0), "feeds_ok": p.get("feeds_ok", 0),
                    "window_start": p.get("window_start"), "days": p.get("days", WINDOW_DAYS),
                    "unsupported_rrule": p.get("unsupported_rrule", 0)})
    return out
