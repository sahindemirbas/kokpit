"""Small, comment-preserving edits of kokpit.toml. Standard library only.

Only what the setup screen needs: set one ``key = value`` in a section, add or
remove ``[[name]]`` blocks. Every result is parsed with ``tomllib`` before it
is accepted; an edit that would break the file raises ValueError and nothing
is written.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

_HEADER = re.compile(r"^\s*(\[\[?)\s*([A-Za-z0-9_.\-]+)\s*(\]\]?)\s*(#.*)?$")
_BARE_KEY = re.compile(r"[A-Za-z0-9_\-]+")


def dumps_value(value) -> str:
    """A TOML literal for str, bool, int, float or a list of those."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("not a finite number")
        return repr(value)
    if isinstance(value, str):
        out = []
        for ch in value:
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\r":
                out.append("\\r")
            elif ord(ch) < 0x20 or ord(ch) == 0x7F:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(dumps_value(v) for v in value) + "]"
    raise ValueError(f"unsupported value type {type(value).__name__}")


def _key(key: str) -> str:
    if not _BARE_KEY.fullmatch(key):
        raise ValueError(f"unsupported key {key!r}")
    return key


def _header(line: str) -> tuple[str, str] | None:
    """('table' | 'array', name) for a header line, else None."""
    m = _HEADER.match(line)
    if not m or (m.group(1) == "[[") != (m.group(3) == "]]"):
        return None
    return ("array" if m.group(1) == "[[" else "table", m.group(2))


def _value_end(s: str) -> int:
    """Index just after a single-line TOML value at the start of ``s``; -1 if it does not end on this line."""
    i, depth, n = 0, 0, len(s)
    while i < n:
        ch = s[i]
        if ch == '"' or ch == "'":
            q = ch
            i += 1
            while i < n and s[i] != q:
                i += 2 if (q == '"' and s[i] == "\\") else 1
            if i >= n:
                return -1
            i += 1
            if depth == 0:
                return i
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return i + 1
        elif ch == "#" and depth == 0:
            return i
        i += 1
    return n if depth == 0 else -1


def _comment(line: str) -> str:
    """The trailing comment of a ``key = value`` line, with its leading spaces, or ''."""
    eq = line.find("=")
    if eq < 0:
        return ""
    rest = line[eq + 1:]
    lead = len(rest) - len(rest.lstrip())
    end = _value_end(rest[lead:])
    if end < 0:
        return ""
    tail = rest[lead + end:]
    stripped = tail.lstrip()
    return tail.rstrip("\n") if stripped.startswith("#") else ""


def _sections(lines: list[str]) -> list[tuple[int, str, str]]:
    out = []
    for i, line in enumerate(lines):
        h = _header(line)
        if h:
            out.append((i, h[0], h[1]))
    return out


def _check(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"edit would make the config invalid: {exc}") from None


def _lookup(data: dict, section: str):
    cur = data
    for part in section.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _section_end(lines: list[str], start: int) -> int:
    """Index of the line after the last non-blank line of the section starting at ``start``."""
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _header(lines[i]):
            end = i
            break
    # blank lines and comments right above the next header belong to that header
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return end


def set_value(text: str, section: str, key: str, value) -> str:
    """Set ``key = value`` under ``[section]``. Keeps comments and order; returns the new text."""
    _key(key)
    for part in section.split("."):
        _key(part)
    literal = dumps_value(value)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=")
    secs = _sections(lines)
    idx = next((i for i, kind, name in secs if kind == "table" and name == section), None)
    if idx is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines += [f"[{section}]\n", f"{key} = {literal}\n"]
    else:
        nxt = next((i for i, _, _ in secs if i > idx), len(lines))
        hit = next((i for i in range(idx + 1, nxt) if key_re.match(lines[i])), None)
        if hit is not None:
            indent = key_re.match(lines[hit]).group(1)  # type: ignore[union-attr]
            lines[hit] = f"{indent}{key} = {literal}{_comment(lines[hit])}\n"
        else:
            lines.insert(_section_end(lines, idx), f"{key} = {literal}\n")
    out = "".join(lines)
    got = _lookup(_check(out), section)
    if not isinstance(got, dict) or got.get(key) != (list(value) if isinstance(value, tuple) else value):
        raise ValueError(f"could not set {section}.{key} safely")
    return out


def _block(name: str, mapping: dict) -> list[str]:
    for part in name.split("."):
        _key(part)
    return [f"[[{name}]]\n"] + [f"{_key(k)} = {dumps_value(v)}\n" for k, v in mapping.items()]


def append_table(text: str, name: str, mapping: dict) -> str:
    """Add one ``[[name]]`` block: after the last existing one, else at the end of the parent table, else at the end."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    secs = _sections(lines)
    same = [i for i, kind, n in secs if kind == "array" and n == name]
    parent = name.rsplit(".", 1)[0] if "." in name else None
    anchor = same[-1] if same else next((i for i, kind, n in secs if kind == "table" and n == parent), None) if parent else None
    block = _block(name, mapping)
    if anchor is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines += block
    else:
        at = _section_end(lines, anchor)
        tail = ["\n"] if at < len(lines) and lines[at].strip() else []
        lines[at:at] = ["\n"] + block + tail
    out = "".join(lines)
    got = _lookup(_check(out), name)
    if not isinstance(got, list) or not got or got[-1] != {k: list(v) if isinstance(v, tuple) else v for k, v in mapping.items()}:
        raise ValueError(f"could not add a [[{name}]] block safely")
    return out


def remove_tables(text: str, name: str) -> str:
    """Remove every ``[[name]]`` block (header and its key lines). Other sections and comments stay."""
    lines = text.splitlines(keepends=True)
    keep, held, skipping = [], [], False
    for line in lines:
        h = _header(line)
        if h:
            if skipping:  # comments after a removed block belong to what follows
                while held and not held[0].strip():
                    held.pop(0)
                if held and keep and keep[-1].strip():
                    keep.append("\n")
                keep += held
                held = []
            skipping = h == ("array", name)
            if skipping:
                while keep and not keep[-1].strip():
                    keep.pop()
                if keep:
                    keep.append("\n")
                continue
        elif skipping:
            s = line.strip()
            if not s or s.startswith("#"):
                held.append(line)
            else:
                held = []
            continue
        keep.append(line)
    if skipping:
        keep += [x for x in held if x.strip()]
    out = "".join(keep)
    if _lookup(_check(out), name):
        raise ValueError(f"could not remove the [[{name}]] blocks safely")
    return out


def write_atomic(path: str | Path, text: str) -> None:
    """Validate, then write through a temporary file and ``os.replace``."""
    _check(text)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, path)
