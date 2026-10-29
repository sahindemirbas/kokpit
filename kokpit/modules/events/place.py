"""`kokpit place "<city>"`: pick a place, see which event sources fit, confirm slugs.

Candidates come from the Open-Meteo Geocoding API (no key, free). Nothing here
checks a slug over the network: the slug is derived from the city name and the
user confirms it with ``--confirm-slug``. ``--write`` edits only the touched keys
of an existing kokpit.toml, keeps every comment and other setting, and leaves a
``.bak`` copy next to it.
"""
from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from urllib.parse import urlencode

from ... import fetch
from .geocode import propose_slugs

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search?"

# What Kokpit knows about each generic source per country. "yes": Kokpit has the
# URL pattern for that country (AllEvents city pages, Eventbrite country domain).
# Anything not listed is "unverified": the source may work, nobody checked it.
# Kokpit has only been run against Italian cities, so most "yes" rows are
# based on the sites' public URL scheme, not on a test run.
COVERAGE = {
    "allevents": {"IT": "yes"},
    "eventbrite": {cc: "yes" for cc in ("IT", "DE", "FR", "ES", "GB", "NL", "PT", "AT", "CH", "BE", "IE", "CA",
                                        "AU", "US")},
}


class PlaceError(Exception):
    """Network failure or no usable answer; the message is meant for the user."""


class UnsafeEdit(Exception):
    """The config could not be edited without touching other settings."""


def search(name: str, count: int = 5) -> list[dict]:
    q = urlencode({"name": name, "count": count, "language": "en", "format": "json"})
    try:
        body = fetch.get(GEOCODER + q, timeout=20)
    except Exception as exc:  # URLError, timeout, HTTP error: all mean "could not ask"
        raise PlaceError(f"could not reach the geocoder ({exc.__class__.__name__}: {exc}); check the connection") from exc
    return parse(body)


def parse(body: str) -> list[dict]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PlaceError("the geocoder sent an answer that is not JSON") from exc
    out = []
    for h in (data.get("results") or []) if isinstance(data, dict) else []:
        try:
            out.append({
                "city": h["name"],
                "region": ", ".join(x for x in (h.get("admin1"), h.get("admin2")) if x),
                "country": str(h.get("country_code", "")).upper(),
                "country_name": h.get("country") or "",
                "lat": round(float(h["latitude"]), 4),
                "lon": round(float(h["longitude"]), 4),
                "population": h.get("population"),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def format_candidates(cands: list[dict]) -> str:
    rows = []
    for i, c in enumerate(cands, 1):
        pop = f"{c['population']:,}" if isinstance(c.get("population"), int) else "?"
        rows.append(f"{i}. {c['city']} | {c['region'] or '-'} | {c['country'] or '?'} | "
                    f"{c['lat']}, {c['lon']} | pop {pop}")
    return "\n".join(rows)


def sources_for(country: str | None) -> dict[str, str]:
    cc = (country or "").upper()
    return {src: table.get(cc, "unverified") for src, table in COVERAGE.items()}


def _lit(v) -> str:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else repr(v)


def plan(cand: dict) -> dict:
    """Keys to write, per section, plus the proposed (unconfirmed) slugs."""
    return {
        "place": {"city": cand["city"], "country": cand["country"], "lat": cand["lat"], "lon": cand["lon"]},
        "slugs": propose_slugs({"city": cand["city"], "country": cand["country"], "country_name": cand["country_name"]}),
        "sources": sources_for(cand["country"]),
    }


def _unconfirmed_line(key: str, value: str) -> str:
    return f"# {key} = {_lit(value)}  # proposed; check it on the site, then rerun with --confirm-slug"


def block(cand: dict, confirm_slug: bool = False) -> str:
    p = plan(cand)
    lines = ["[place]"] + [f"{k} = {_lit(v)}" for k, v in p["place"].items()]
    shown = ", ".join(f"{s} ({st})" for s, st in p["sources"].items())
    suggested = [s for s, st in p["sources"].items() if st == "yes"]
    lines += ["", "[events]", f"# sources for {cand['country'] or '?'}: {shown}",
              f"# suggested: {', '.join(suggested) or 'none confirmed for this country; try both'}"]
    for k, v in p["slugs"].items():
        lines.append(f"{k} = {_lit(v)}" if confirm_slug else _unconfirmed_line(k, v))
    return "\n".join(lines)


# --- in-place TOML edit -------------------------------------------------------

def _header(line: str) -> str | None:
    m = re.match(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$", line)
    return m.group(1) if m else None


def _bounds(lines: list[str], section: str) -> tuple[int | None, int]:
    start = None
    for i, line in enumerate(lines):
        name = _header(line)
        if name == section:
            start = i
        elif name is not None and start is not None:
            return start, i
    return start, len(lines)


def _split_comment(rest: str) -> tuple[str, str]:
    """Split ``value  # comment`` without breaking a quoted string that contains '#'."""
    quote, escaped = None, False
    for i, ch in enumerate(rest):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            j = i
            while j > 0 and rest[j - 1] in " \t":
                j -= 1
            return rest[:j], rest[j:]
    return rest.rstrip(), ""


def _insert_at(lines: list[str], start: int, end: int) -> int:
    """Index after the last non-blank line of the section."""
    j = end
    while j > start + 1 and not lines[j - 1].strip():
        j -= 1
    return j


def edit(text: str, place: dict, slugs: dict, confirm_slug: bool) -> str:
    lines = text.splitlines()
    want: dict[str, dict] = {"place": dict(place)}
    if confirm_slug:
        want["events"] = dict(slugs)
    for section, kv in want.items():
        start, end = _bounds(lines, section)
        if start is None:
            lines += ["", f"[{section}]"] + [f"{k} = {_lit(v)}" for k, v in kv.items()]
            continue
        for k, v in kv.items():
            rx = re.compile(rf"^(\s*{re.escape(k)}\s*=\s*)(.*)$")
            idx = next((i for i in range(start + 1, end) if rx.match(lines[i])), None)
            if idx is None:
                at = _insert_at(lines, start, end)
                lines.insert(at, f"{k} = {_lit(v)}")
                end += 1
            else:
                m = rx.match(lines[idx])
                assert m is not None
                _, comment = _split_comment(m.group(2))
                lines[idx] = f"{m.group(1)}{_lit(v)}{comment}"
    # Proposed slugs: replace any earlier proposal comment, never the active value.
    start, end = _bounds(lines, "events")
    if start is None and not confirm_slug:
        lines += ["", "[events]"]
        start, end = _bounds(lines, "events")
    assert start is not None
    keep = [ln for ln in lines[start + 1:end]
            if not any(re.match(rf"^#\s*{k}\s*=.*# proposed", ln) for k in slugs)]
    lines[start + 1:end] = keep
    end = start + 1 + len(keep)
    if not confirm_slug:
        at = _insert_at(lines, start, end)
        lines[at:at] = [_unconfirmed_line(k, v) for k, v in slugs.items()]
    return "\n".join(lines) + "\n"


def _check(old: dict, new: dict, place: dict, slugs: dict, confirm_slug: bool) -> None:
    changed = {("place", k) for k in place} | ({("events", k) for k in slugs} if confirm_slug else set())
    for sec in set(old) | set(new):
        a, b = old.get(sec, {}), new.get(sec, {})
        if not isinstance(a, dict) or not isinstance(b, dict):
            if a != b:
                raise UnsafeEdit(f"top-level value {sec} changed")
            continue
        for k in set(a) | set(b):
            if (sec, k) not in changed and a.get(k) != b.get(k):
                raise UnsafeEdit(f"[{sec}] {k} would change")
    for k, v in place.items():
        if new["place"].get(k) != v:
            raise UnsafeEdit(f"[place] {k} did not take the new value")
    for k, v in slugs.items():
        expect = v if confirm_slug else old.get("events", {}).get(k)
        if new.get("events", {}).get(k) != expect:
            raise UnsafeEdit(f"[events] {k} is not what was asked")


def write(path: Path, cand: dict, confirm_slug: bool = False) -> Path:
    """Edit ``path`` in place; returns the backup path. Raises UnsafeEdit and writes nothing on doubt."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        old = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UnsafeEdit(f"{path} is not valid TOML ({exc})") from exc
    p = plan(cand)
    new_text = edit(text, p["place"], p["slugs"], confirm_slug)
    try:
        new = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise UnsafeEdit(f"the edited file would not parse ({exc})") from exc
    _check(old, new, p["place"], p["slugs"], confirm_slug)
    bak = path.with_name(path.name + ".bak")
    shutil.copy2(path, bak)
    path.write_text(new_text, encoding="utf-8")
    return bak
