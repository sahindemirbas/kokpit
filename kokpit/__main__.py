"""Command line: kokpit init | run | run-job <name> | context | place <city> | jobs"""
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


def cmd_context(args) -> int:
    """Print the read-only brain context block (what an agent hook injects)."""
    from .modules.brain.context import build_context
    cfg = _load(args)
    message = " ".join(args.message) if args.message else ""
    text = build_context(cfg.vault, message, max_chars=args.max_chars)
    if text:
        print(text)
    return 0


def cmd_place(args) -> int:
    """Geocode a city, show the event sources for its country, print or write the config block."""
    from .modules.events import place
    try:
        cands = place.search(args.query, count=args.count)
    except place.PlaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if not cands:
        print(f"error: no place found for '{args.query}'; try another spelling or add the country", file=sys.stderr)
        return 4
    if args.pick is None:
        print(place.format_candidates(cands))
        if args.write:
            print("error: --write needs --pick N", file=sys.stderr)
            return 2
        print(f"\nnext: python -m kokpit place \"{args.query}\" --pick N")
        return 0
    if not 1 <= args.pick <= len(cands):
        print(f"error: --pick must be between 1 and {len(cands)}", file=sys.stderr)
        return 2
    cand = cands[args.pick - 1]
    print(f"picked: {cand['city']}, {cand['region'] or '-'}, {cand['country']} ({cand['lat']}, {cand['lon']})\n")
    print(place.block(cand, confirm_slug=args.confirm_slug))
    if not args.confirm_slug:
        print("\nThe slugs are derived from the city name and not checked. Open them on the site; "
              "if they work, rerun with --confirm-slug to make them active.")
    if not args.write:
        print("\n(nothing written; add --write to update kokpit.toml)")
        return 0
    path = cfgmod.find_config(args.config)
    if path is None or not path.exists():
        print("error: no kokpit.toml found; run `kokpit init` first or paste the block by hand", file=sys.stderr)
        return 2
    try:
        bak = place.write(path, cand, confirm_slug=args.confirm_slug)
    except place.UnsafeEdit as exc:
        print(f"error: did not write {path}: {exc}. Paste the block above by hand.", file=sys.stderr)
        return 5
    print(f"\nupdated {path} (backup: {bak}); slugs {'active' if args.confirm_slug else 'left as comments'}")
    return 0


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
    p = sub.add_parser("context", help="print the read-only brain context an agent hook would inject")
    p.add_argument("message", nargs="*", help="the user's message, used only to pick related notes")
    p.add_argument("--max-chars", type=int, default=3500)
    p.set_defaults(fn=cmd_context)
    p = sub.add_parser("place", help="find your city (Open-Meteo geocoder), show event sources, set [place]")
    p.add_argument("query", help='city name, e.g. "Bergamo"')
    p.add_argument("--pick", type=int, help="number of the candidate to use (from the list)")
    p.add_argument("--write", action="store_true", help="update kokpit.toml (keeps a .bak copy)")
    p.add_argument("--confirm-slug", action="store_true", help="make the proposed event slugs active")
    p.add_argument("--count", type=int, default=5, help="how many candidates to list (default 5)")
    p.set_defaults(fn=cmd_place)
    p = sub.add_parser("jobs", help="list jobs")
    p.set_defaults(fn=cmd_jobs)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
