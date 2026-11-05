"""Optional training module: progress of ML runs from their own log files. Read-only.

Inputs are the files matched by the `training_logs` glob list: JSONL (one object per line) or CSV with
a header row. One file is one run (a `run` field splits a file into several runs). Recognised fields:
  step:  step, global_step, iteration, iter, epoch
  time:  time, timestamp, ts, wall_time, datetime (unix seconds or ISO 8601)
  total: total_steps, max_steps, num_steps
  loss:  loss, train_loss, train/loss
Every other numeric field is reported as a metric. Without a time field, the file modification time
stands in for the last update and no rate is computed. Processes are never touched.
"""
from __future__ import annotations

import csv
import glob
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

STEP_KEYS = ("step", "global_step", "iteration", "iter", "epoch")
TIME_KEYS = ("time", "timestamp", "ts", "wall_time", "datetime")
TOTAL_KEYS = ("total_steps", "max_steps", "num_steps")
LOSS_KEYS = ("loss", "train_loss", "train/loss")
SKIP = set(STEP_KEYS + TIME_KEYS + TOTAL_KEYS + LOSS_KEYS) | {"run", "lr", "learning_rate"}
TAIL_BYTES = 256 * 1024
MAX_FILES = 20
RATE_WINDOW = 50   # rows used for the step rate


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ts(v):
    n = _num(v)
    if n is not None:
        return n / 1000.0 if n > 1e11 else n   # milliseconds
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.astimezone()).timestamp()


def _first(row: dict, keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def files(patterns) -> list[Path]:
    seen, out = set(), []
    for pat in patterns or []:
        for m in sorted(glob.glob(os.path.expanduser(str(pat)), recursive=True)):
            p = Path(m)
            if p.is_file() and not p.is_symlink() and p not in seen and p.suffix.lower() in (".jsonl", ".json", ".csv", ".ndjson"):
                seen.add(p)
                out.append(p)
    return out[:MAX_FILES]


def _tail(p: Path) -> tuple[str, bool]:
    size = p.stat().st_size
    with open(p, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
        data = fh.read().decode("utf-8", errors="replace")
    return data, size > TAIL_BYTES


def read_rows(p: Path) -> list[dict]:
    text, cut = _tail(p)
    if p.suffix.lower() == ".csv":
        lines = text.splitlines()
        if cut:  # keep the real header from the top of the file
            with open(p, encoding="utf-8", errors="replace") as fh:
                header = fh.readline().rstrip("\r\n")
            lines = [header] + lines[1:]
        return [dict(r) for r in csv.DictReader(io.StringIO("\n".join(lines)))]
    rows = []
    for i, line in enumerate(text.splitlines()):
        if cut and i == 0:
            continue  # probably a partial line
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            rows.append(o)
    return rows


def summarize(name: str, rows: list[dict], mtime: float, now: float, stall_minutes: float) -> dict:
    rows = [r for r in rows if _num(_first(r, STEP_KEYS)) is not None]
    if not rows:
        return {"run": name, "status": "unknown", "reason": "no rows with a step field"}
    last = rows[-1]
    step = float(_num(_first(last, STEP_KEYS)) or 0)
    t_last = _ts(_first(last, TIME_KEYS))
    updated = t_last if t_last is not None else mtime
    out = {"run": name, "last_step": step, "loss": _num(_first(last, LOSS_KEYS)),
           "metrics": {k: _num(v) for k, v in last.items() if k not in SKIP and _num(v) is not None},
           "last_update": datetime.fromtimestamp(updated, timezone.utc).astimezone().isoformat(timespec="seconds"),
           "minutes_since_update": round(max(0.0, now - updated) / 60, 1),
           "steps_per_sec": None, "total_steps": None, "eta_minutes": None}
    timed = [(_num(_first(r, STEP_KEYS)), _ts(_first(r, TIME_KEYS))) for r in rows[-RATE_WINDOW:]]
    timed = [(float(s or 0), t) for s, t in timed if t is not None]
    if len(timed) >= 2 and timed[-1][1] > timed[0][1] and timed[-1][0] > timed[0][0]:
        out["steps_per_sec"] = round((timed[-1][0] - timed[0][0]) / (timed[-1][1] - timed[0][1]), 4)
    total = None
    for r in reversed(rows):
        total = _num(_first(r, TOTAL_KEYS))
        if total is not None:
            break
    if total:
        out["total_steps"] = total
        out["progress_pct"] = round(min(100.0, 100.0 * step / total), 1)
        if out["steps_per_sec"]:
            out["eta_minutes"] = round(max(0.0, total - step) / out["steps_per_sec"] / 60, 1)
    done = bool(total) and step >= total
    stalled = not done and out["minutes_since_update"] >= stall_minutes
    out["status"] = "finished" if done else "stalled?" if stalled else "running"
    if stalled:
        out["warning"] = f"no new step for {out['minutes_since_update']:.0f} min (threshold {stall_minutes:g}), may have stopped"
    return out


def status(patterns, stall_minutes: float = 30, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    found = files(patterns)
    runs = []
    for p in found:
        try:
            rows, mtime = read_rows(p), p.stat().st_mtime
        except OSError as e:
            runs.append({"run": p.stem, "status": "unknown", "reason": f"could not read ({type(e).__name__})"})
            continue
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(str(r.get("run") or p.stem), []).append(r)
        for name, rs in (groups or {p.stem: []}).items():
            runs.append({**summarize(name, rs, mtime, now, stall_minutes), "file": p.name})
    return {"runs": runs, "files": len(found), "stall_minutes": stall_minutes,
            "warnings": [f"{r['run']}: {r['warning']}" for r in runs if r.get("warning")]}
