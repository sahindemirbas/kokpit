"""The ``.env`` file next to kokpit.toml: where keys live. Standard library only.

Only ``KEY=VALUE`` lines are read (an optional ``export`` prefix and one pair of
surrounding quotes are accepted). Variables already set in the environment win,
so Docker, systemd or the shell can always override the file. Values are never
logged or returned by the web API; only key names are shown.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if " #" in v:  # unquoted value with a trailing comment
        v = v.split(" #", 1)[0].rstrip()
    return v


def parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def read(path: str | Path) -> dict[str, str]:
    p = Path(path)
    try:
        return parse(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def load_dotenv(path: str | Path) -> list[str]:
    """Put the file's variables into ``os.environ`` unless already set. Returns the names that were set."""
    done = []
    for k, v in read(path).items():
        if k not in os.environ:
            os.environ[k] = v
            done.append(k)
    return done


def names(path: str | Path) -> list[str]:
    """Key names defined in the file (with a non-empty value). Never the values."""
    return sorted(k for k, v in read(path).items() if v)


def _quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+=,-]*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_env_value(path: str | Path, key: str, value: str) -> None:
    """Replace or add ``key=value``; an empty value removes the key. File mode 0600, atomic write."""
    if not _NAME.fullmatch(key or ""):
        raise ValueError("invalid variable name")
    value = "" if value is None else str(value)
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("the value must be one line")
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    out, seen = [], False
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) == key and not line.lstrip().startswith("#"):
            if value and not seen:
                out.append(f"{key}={_quote(value)}")
            seen = True
            continue
        out.append(line)
    if value and not seen:
        out.append(f"{key}={_quote(value)}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, ("\n".join(out) + "\n" if out else "").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
