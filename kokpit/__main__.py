"""Command line: kokpit init | run | run-job <name> | jobs"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config as cfgmod, jobs
from .modules.brain import api as brain_api


def cmd_init(args) -> int:
    path = Path(args.config or "kokpit.toml")
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite the config (the vault is never overwritten)")
        cfg = cfgmod.load(path)
    else:
        place = slugs = None
        if args.city:
            from .modules.events import geocode
            try:
                place = geocode.geocode(args.city)
            except Exception as exc:  # offline or Nominatim down: config still gets written
                print(f"could not geocode '{args.city}' ({exc.__class__.__name__}); set [place] by hand in {path}")
            if place:
                slugs = geocode.propose_slugs(place)
                print(f"place: {place.get('display')} ({place['lat']}, {place['lon']})")
                slugs, counts = geocode.verify_slugs(place, slugs)
                shown = ", ".join(f"{s} '{slugs[s + '_slug']}' ({'unreachable' if n < 0 else f'{n} events'})" for s, n in counts.items())
                print(f"event sources: {shown} (change them on the Connections tab; a wrong slug shows as 0 records)")
            elif place is None and args.city:
                print(f"no match for '{args.city}'; events stay off until [place] is filled in")
        cfgmod.write_default(path, vault=args.vault, db=args.db, place=place, slugs=slugs)
        print(f"wrote {path}")
        cfg = cfgmod.load(path)
    created = brain_api.init_vault(cfg.vault)
    if created:
        print(f"starter vault in {cfg.vault}: " + ", ".join(created))
    else:
        print(f"vault {cfg.vault} already has its files; nothing copied")
    return 0


def _load(args) -> cfgmod.Config:
    path = cfgmod.find_config(args.config)
    if path is None or not path.exists():
        print("no kokpit.toml found. Run `kokpit init` (or ./run, which does it for you).")
        sys.exit(2)
    return cfgmod.load(path)


def cmd_run(args) -> int:
    cfg = _load(args)
    if not cfg.vault.exists():
        brain_api.init_vault(cfg.vault)
    if not cfg.db.exists():
        jobs.run(cfg, "heartbeat")
        jobs.run(cfg, "brain-check")
    from .web import serve
    serve(cfg)
    return 0


def cmd_run_job(args) -> int:
    cfg = _load(args)
    return 0 if jobs.run(cfg, args.name) else 1


def cmd_jobs(args) -> int:
    for name, fn in sorted(jobs.REGISTRY.items()):
        print(f"{name:14s} {(fn.__doc__ or '').strip().splitlines()[0]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kokpit", description=f"Kokpit {__version__}")
    ap.add_argument("--config", help="path to kokpit.toml (default: ./kokpit.toml, then ~/.config/kokpit/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write kokpit.toml and the starter vault")
    p.add_argument("--vault", default="data/vault", help="Markdown folder (default data/vault)")
    p.add_argument("--db", default="data/kokpit.db")
    p.add_argument("--city", help='your city, e.g. "Lodi" or "Lodi, Italy"; geocoded once and written to [place]')
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("run", help="start the dashboard")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("run-job", help="run one scheduled job now")
    p.add_argument("name")
    p.set_defaults(fn=cmd_run_job)
    p = sub.add_parser("jobs", help="list jobs")
    p.set_defaults(fn=cmd_jobs)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
