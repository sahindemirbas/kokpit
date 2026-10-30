"""``kokpit schedule``: a foreground loop that runs the jobs of the enabled modules.

Stdlib only. Jobs run one after another (SQLite writers must not overlap, see
docs/architecture.md). A failing job is recorded in Health by ``jobs.run`` and the loop goes on.
Times are local time; set the ``TZ`` environment variable to change it.

Table (``SCHEDULE``): ``every`` jobs run on a fixed grid counted from midnight (30 minutes means
:00 and :30), ``daily`` jobs run at fixed times. ``bot-poll`` is not here: the ``bot`` service
(``kokpit bot poll``) runs it. A job that has never run is run once at start, so a fresh
install is not empty until the first slot.
"""
from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs
from .config import Config
from .db import connect
from .modules.health import store

HEARTBEAT_NAME = ".scheduler-heartbeat"
LOCK_NAME = ".scheduler.lock"
TICK_SECONDS = 15
HEARTBEAT_MAX_AGE = 90  # seconds; used by the compose healthcheck


@dataclass(frozen=True)
class Entry:
    job: str
    module: str  # module switch in [modules] that enables the job
    every: int | None = None  # minutes
    at: tuple[str, ...] = ()  # "HH:MM" local times


SCHEDULE: tuple[Entry, ...] = (
    Entry("weather", "today", every=30),
    Entry("calendar", "today", every=15),
    Entry("brain-check", "brain", every=60),
    Entry("events-collect", "events", at=("06:30",)),
    Entry("career", "jobs", at=("06:30",)),
    Entry("organiser", "organiser", at=("03:30",)),
    Entry("agenda", "agenda", at=("06:45",)),
    Entry("ai_usage", "ai_usage", every=60),
    Entry("instagram", "instagram", at=("07:00", "19:00")),
    Entry("instagram-plan", "instagram", at=("07:00",)),
    Entry("bot-morning", "bot", at=("07:30",)),  # replaced by [bot] morning_time, see entries_for
    Entry("bot-instagram-remind", "bot", every=15),
)


def entries_for(cfg: Config) -> list[Entry]:
    """The entries whose module is switched on. ``bot-poll`` never appears."""
    out = []
    for e in SCHEDULE:
        if e.job == "bot-poll" or not cfg.modules.get(e.module, False):
            continue
        if e.job == "bot-morning":
            hhmm = str((cfg.bot or {}).get("morning_time") or e.at[0])
            e = Entry(e.job, e.module, at=(hhmm,))
        if e.job == "bot-instagram-remind" and not cfg.modules.get("instagram", False):
            continue
        out.append(e)
    return out


def next_run(entry: Entry, after: datetime) -> datetime:
    """First run time strictly after ``after`` (naive local time). Pure."""
    after = after.replace(microsecond=0)
    if entry.every:
        midnight = after.replace(hour=0, minute=0, second=0)
        step = entry.every * 60
        elapsed = int((after - midnight).total_seconds())
        return midnight + timedelta(seconds=(elapsed // step + 1) * step)
    best = None
    for hhmm in entry.at:
        hh, _, mm = hhmm.partition(":")
        for add in (0, 1):
            t = (after + timedelta(days=add)).replace(hour=int(hh), minute=int(mm), second=0)
            if t > after and (best is None or t < best):
                best = t
    if best is None:
        raise ValueError(f"entry {entry.job} has no schedule")
    return best


def acquire_lock(path: Path):
    """Exclusive lock so two schedulers never run on one data folder. Returns the open file or None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def heartbeat_path(cfg: Config) -> Path:
    return cfg.db.parent / HEARTBEAT_NAME


def beat(cfg: Config) -> None:
    p = heartbeat_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()


def never_run(cfg: Config, names: list[str]) -> list[str]:
    if not cfg.db.exists():
        return list(names)
    conn = connect(cfg.db)
    try:
        seen = {r["job"] for r in store.latest_runs(conn)}
    finally:
        conn.close()
    return [n for n in names if n not in seen]


def run_loop(cfg: Config, stop: threading.Event, now=datetime.now, runner=None, tick: float = TICK_SECONDS) -> None:
    runner = runner or jobs.run
    entries = entries_for(cfg)
    due: dict[str, datetime] = {}
    t0 = now()
    first = set(never_run(cfg, [e.job for e in entries]))
    for e in entries:
        due[e.job] = t0 if e.job in first else next_run(e, t0)
    print("scheduler: " + (", ".join(e.job for e in entries) or "no jobs (no module is on)"), flush=True)
    while not stop.is_set():
        beat(cfg)
        t = now()
        for e in entries:
            if stop.is_set():
                break
            if due[e.job] <= t:
                try:
                    runner(cfg, e.job)
                except BaseException as exc:  # the loop must not die; jobs.run already records job errors
                    print(f"scheduler: {e.job} crashed: {exc!r}", file=sys.stderr, flush=True)
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        stop.set()
                due[e.job] = next_run(e, max(now(), due[e.job]))
        stop.wait(tick)
    print("scheduler: stopped", flush=True)


def main(cfg: Config) -> int:
    lock = acquire_lock(cfg.db.parent / LOCK_NAME)
    if lock is None:
        print("another scheduler already holds the lock; exiting", file=sys.stderr)
        return 1
    if os.environ.get("TZ") and hasattr(time, "tzset"):
        time.tzset()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_loop(cfg, stop)
    lock.close()
    return 0
