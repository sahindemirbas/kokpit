"""Command line: kokpit init | run | run-job <name> | schedule | context | brain receipt|receipts|compact | place <city> | jobs | adapters | career | ai-usage summary | instagram collect|summary|refresh-token | bot poll [--once] | bot send-test"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config as cfgmod
from .modules.brain import api as brain_api

# `jobs` imports every module, so it is loaded on first use: `kokpit brain hook`
# runs on every agent message under a short timeout and must stay light.


def cmd_init(args) -> int:
    path = Path(args.config or "kokpit.toml")
    fresh = not path.exists() or args.force
    if not fresh:
        print(f"{path} exists; use --force to overwrite the config (the vault is never overwritten)")
        cfg = cfgmod.load(path)
    else:
        place = slugs = None
        if args.city:
            from . import wizard
            try:
                found = wizard.find_place(args.city)
            except Exception as exc:  # offline or Nominatim down: config still gets written
                found = None
                print(f"could not geocode '{args.city}' ({exc.__class__.__name__}); set [place] by hand in {path} or on the setup screen")
            if found and found["place"]:
                place, slugs, counts = found["place"], found["slugs"], found["counts"]
                print(f"place: {place.get('display')} ({place['lat']}, {place['lon']})")
                shown = ", ".join(f"{s} '{slugs[s + '_slug']}' ({'unreachable' if n < 0 else f'{n} events'})" for s, n in counts.items())
                print(f"event sources: {shown} (change them on the Connections tab; a wrong slug shows as 0 records)")
            elif found is not None:
                print(f"no match for '{args.city}'; events stay off until [place] is filled in")
        cfgmod.write_default(path, vault=args.vault, db=args.db, place=place, slugs=slugs,
                             name=args.name or "", language=args.language or "en")
        print(f"wrote {path}")
        cfg = cfgmod.load(path)
    created = brain_api.init_vault(cfg.vault)
    if created:
        print(f"starter vault in {cfg.vault}: " + ", ".join(created))
    else:
        print(f"vault {cfg.vault} already has its files; nothing copied")
    if fresh and not args.yes and sys.stdin.isatty() and sys.stdout.isatty():
        from . import wizard
        print("A few questions; press Enter to skip any of them. The setup screen in the browser asks the same.")
        try:
            wizard.ask_cli(path)
        except (EOFError, KeyboardInterrupt):
            print("\nstopped; the setup screen opens on the first `kokpit run`")
    elif fresh:
        print("answer the setup questions in the browser: run `kokpit run` (or ./run)")
    return 0


def _load(args) -> cfgmod.Config:
    path = cfgmod.find_config(args.config)
    if path is None or not path.exists():
        print("no kokpit.toml found. Run `kokpit init` (or ./run, which does it for you).")
        sys.exit(2)
    return cfgmod.load(path)


def cmd_run(args) -> int:
    from . import jobs
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
    from . import jobs
    cfg = _load(args)
    if getattr(args, "dry_run", False):
        name = jobs.ALIASES.get(args.name, args.name)
        if name != "organiser":
            raise SystemExit("--dry-run is supported for the organiser job only")
        from .modules.organiser import collect as organiser
        print(organiser.dry_run(cfg))  # writes nothing: no plan file, no database, no report
        return 0
    return 0 if jobs.run(cfg, args.name) else 1


def cmd_schedule(args) -> int:
    from . import scheduler
    return scheduler.main(_load(args))


def cmd_context(args) -> int:
    """Print the read-only brain context block (what an agent hook injects)."""
    from .modules.brain.context import build_context
    cfg = _load(args)
    message = " ".join(args.message) if args.message else ""
    text = build_context(cfg.vault, message, max_chars=args.max_chars)
    if text:
        print(text)
    return 0


def cmd_brain(args) -> int:
    """brain receipt | receipts | compact [--dry-run]. Receipts and compact write only inside the vault."""
    import json
    if args.brain_cmd == "hook":  # no _load: the hook finds the vault itself and never fails loudly
        from .modules.brain import hook
        return hook.main(sys.stdin, sys.stdout, sys.stderr, args.harness, args.event, args.mode,
                         args.max_chars, args.config)
    if args.brain_cmd == "agents":
        return cmd_brain_agents(args)
    from .modules.brain import compact, receipt
    cfg = _load(args)
    if args.brain_cmd == "context":
        return cmd_context(args)
    if args.brain_cmd == "compact":
        print(json.dumps(compact.compact(cfg.vault, dry_run=args.dry_run), indent=2, ensure_ascii=False))
        return 0
    if args.brain_cmd == "receipts":
        res = receipt.scan_receipts(cfg.vault, limit=args.limit)
        if not res["receipts"]:
            print("no receipts yet")
        for r in res["receipts"]:
            first = r["refs"][0] if r["refs"] else "-"
            more = f" (+{len(r['refs']) - 1})" if len(r["refs"]) > 1 else ""
            print(f"{r['created_at'][:16].replace('T', ' ')}  {r['harness']:12s} {r['event_id']:40s} {first}{more}")
        if res["skipped"]:
            print(f"{res['skipped']} unreadable receipt file(s) skipped (left in place)")
        return 0
    try:
        if args.summary_file:
            summary = Path(args.summary_file).read_text(encoding="utf-8")
        else:
            summary = args.summary
        res = receipt.write_receipt(cfg.vault, args.event_id, summary, args.ref, harness=args.harness,
                                    session=args.session)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 3 if res["status"] == "conflict" else 0


def cmd_brain_agents(args) -> int:
    """brain agents init: copy AGENTS.md, CLAUDE.md and hook settings; existing files are never overwritten."""
    import json
    from .modules.brain import agent_setup
    path = cfgmod.find_config(args.config)
    if path is None or not path.exists():
        print("no kokpit.toml found. Run `kokpit init` first; the hook commands need its path.", file=sys.stderr)
        return 2
    try:
        harnesses = agent_setup.parse_harnesses(args.harness)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target = Path(args.target) if args.target else cfgmod.load(path).vault
    report = agent_setup.init_agents(target, path, harnesses, dry_run=args.dry_run)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    for rel, text in report["snippets"].items():
        print(f"\n{rel} already exists; merge this snippet by hand:\n{text}", file=sys.stderr)
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
    from . import jobs
    for name, fn in sorted(jobs.REGISTRY.items()):
        print(f"{name:14s} {(fn.__doc__ or '').strip().splitlines()[0]}")
    return 0


def cmd_adapters(args) -> int:
    """List local event and job adapters (adapters/events, adapters/jobs and the adapter_dirs), with on/off state."""
    from .modules.career import adapters as job_adapters
    from .modules.events import adapters
    cfg = _load(args)
    found = adapters.discover(cfg)
    if not found:
        print("no event adapters found")
    for a in found:
        where = "user" if a["user"] else "repo"
        city = f"{a['city']}, {a['country']}" if a["city"] and a["country"] else (a["city"] or "-")
        print(f"{'on ' if a['enabled'] else 'off'}  {a['id']:24s} source={a['name']:24s} {city:20s} {where}: {a['path']}")
    for name in adapters.missing(cfg):
        print(f"missing  {name}: enabled in [events] adapters but no such directory")
    print("\nenable with [events] adapters = [\"<id>\"] in kokpit.toml, then `kokpit run-job events-collect`")
    jfound = job_adapters.discover(cfg)
    print("\njob adapters:" if jfound else "\nno job adapters found")
    for a in jfound:
        print(f"{'on ' if a['enabled'] else 'off'}  {a['id']:24s} source={a['name']:24s} {a['company'] or '-':20s} {'user' if a['user'] else 'repo'}: {a['path']}")
    for name in job_adapters.missing(cfg):
        print(f"missing  {name}: enabled in [career] adapters but no such directory")
    if jfound:
        print("enable with [career] adapters = [\"<id>\"] in kokpit.toml (and jobs = true under [modules]), then `kokpit run-job career`")
    return 0


def _career_market(cfg) -> int:
    from .db import connect
    from .modules.career import market
    try:
        conn = connect(cfg.db, readonly=True)
    except FileNotFoundError:
        print("no database yet: run `kokpit run-job career`")
        return 0
    try:
        m = market.view(conn)
    finally:
        conn.close()
    print(f"{m['open']} open of {m['total']} listings, window {m['window_days']} day(s)")
    print("\nper company:")
    for c in m["companies"]:
        print(f"  {c['open']:4d}  {c['company']}")
    print("\nweekly (week starting Monday):")
    for w in m["weekly"]:
        print(f"  {w['week']}  {w['listings']}")
    print("\ntop skills in open listings:")
    for k in m["skills"]:
        print(f"  {k['open']:4d}  {k['skill']}")
    for n in m["notes"]:
        print(f"note: {n}")
    return 0


def _career_sources(cfg, check: bool) -> int:
    """Enabled career sources and the browser setup. Never touches the network."""
    from .modules.career import adapters as job_adapters, browser, profile
    names = job_adapters.enabled_names(cfg)
    print(f"jobs module:      {'on' if cfg.modules.get('jobs', False) else 'off'}")
    print(f"job adapters:     {', '.join(sorted(names)) or 'none'}")
    print(f"linkedin guest:   {'on' if cfg.career.get('linkedin_guest') else 'off'}")
    enabled = cfg.career.get("browser_sources") or []
    print(f"browser sources:  {', '.join(enabled) or 'none (off)'}")
    for n in cfg.career.get("browser_ignored", []):
        print(f"  ignored unknown source {n!r} (allowed: {', '.join(browser.SOURCES)})")
    exe = browser.executable(cfg)
    want = cfg.career.get("browser_executable") or ""
    print(f"browser path:     {exe or 'not found'}" + (f" (configured: {want})" if want else f" (searched: {', '.join(browser.BROWSERS)})"))
    print(f"playwright:       {'installed' if browser.playwright_installed() else 'missing (pip install playwright)'}")
    site, why = browser.site_for(cfg)
    print(f"sites:            {('indeed ' + site['indeed'] + ', jooble ' + site['jooble']) if site else why}")
    if not check:
        return 0
    problems = []
    if enabled:
        if not browser.playwright_installed():
            problems.append("playwright is not installed: pip install playwright")
        if not exe:
            problems.append("no Chromium found: install one or set [career] browser_executable")
        if not site:
            problems.append(why)
        if not profile.load(cfg).get("target_roles"):
            problems.append("profile has no target roles")
    for p in problems:
        print(f"problem: {p}")
    print(f"check: {'ok' if not problems else 'not ready'} ({browser.status(cfg)}; no network used)")
    return 0 if not problems else 1


def cmd_career(args) -> int:
    """career profile show | import-cv PATH [--apply] | set FIELD VALUE; career score --title ..."""
    import json
    from .modules.career import api as career_api, cv, profile, scoring
    cfg = _load(args)
    if args.career_cmd == "export":
        text = career_api.export_csv(cfg, args.status)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {args.out} ({max(0, text.count(chr(10)) - 1)} listings)")
        else:
            sys.stdout.write(text)
        return 0
    if args.career_cmd == "market":
        return _career_market(cfg)
    if args.career_cmd == "sources":
        return _career_sources(cfg, args.check)
    if args.career_cmd == "score":
        text = Path(args.file).read_text(encoding="utf-8") if args.file else ""
        remote = {"yes": True, "no": False}.get(args.remote)
        res = scoring.score(profile.load(cfg), {"title": args.title, "description": text, "location": args.location, "remote": remote})
        print("score: " + ("unknown" if res["score"] is None else f"{res['score']}/100"))
        for r in res["reasons"]:
            print(f"  [{r['effect']:7s}] {r['kind']}: {r['text']}")
        return 0
    if args.profile_cmd == "show":
        print(json.dumps(profile.load(cfg), indent=2, ensure_ascii=False))
        print(f"\nfile: {profile.path(cfg)}" + ("" if profile.exists(cfg) else " (not saved yet)"))
        return 0
    if args.profile_cmd == "import-cv":
        try:
            text = cv.extract_text(args.path)
        except cv.CVError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        prop = cv.propose(text)
        print(f"read {prop['chars']} characters locally (e-mail, phone and links removed); nothing was stored or sent")
        print("proposed skills: " + (", ".join(prop["skills"]) or "none"))
        print("proposed languages: " + (", ".join(f"{x['language']} {x['level'] or '?'}" for x in prop["languages"]) or "none"))
        if not args.apply:
            print("\n(nothing saved; add --apply to merge these into your profile, then edit with `career profile set`)")
            return 0
        profile.save(cfg, profile.merge_proposal(profile.load(cfg), prop))
        print(f"\nmerged into {profile.path(cfg)}")
        return 0
    prof = profile.load(cfg)
    if args.field not in profile.empty():
        print(f"error: unknown field '{args.field}'. Fields: {', '.join(profile.empty())}", file=sys.stderr)
        return 2
    prof[args.field] = args.value
    try:
        saved = profile.save(cfg, prof)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{args.field} = {json.dumps(saved[args.field], ensure_ascii=False)}")
    return 0


def cmd_ai_usage(args) -> int:
    from .modules.ai_usage import api as ai_usage_api
    print(ai_usage_api.summary_text(_load(args)))
    return 0


def cmd_instagram(args) -> int:
    """instagram collect | summary | refresh-token. None of them prints the token or media details."""
    from . import jobs
    from .modules.instagram import api as ig_api, token as ig_token
    from .modules.instagram.client import ApiError
    cfg = _load(args)
    if args.ig_cmd == "summary":
        print(ig_api.summary_text(cfg))
        return 0
    if args.ig_cmd == "collect":
        return 0 if jobs.run(cfg, "instagram") else 1
    try:
        res = ig_token.refresh(cfg)
    except ApiError as exc:
        print(f"error: token refresh failed ({exc.kind}): {exc}", file=sys.stderr)
        return 3
    print(f"token refreshed, stored in {res['stored']}; valid until {res['expires_at']} ({res['expires_in_days']} days). The token is not printed.")
    return 0


def cmd_briefing(args) -> int:
    """briefing podcast [--day D] [--script-only]. The script-only form makes no speech request."""
    from .modules.briefing import podcast
    from .modules.health import store
    from .modules.model.client import ModelDisabled, ModelError
    cfg = _load(args)
    if args.day and not podcast.DAY.fullmatch(args.day):
        print("error: --day must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.script_only:
        day, items = podcast.load_items(cfg, args.day)
        try:
            sc = podcast.script(cfg, day or "", items, podcast.context_for(cfg))
        except (ModelDisabled, ModelError, podcast.PodcastError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(podcast.as_text(sc), end="")
        return 0
    started = store.now_iso()
    try:
        rows, info, warnings = podcast.job(cfg, args.day)
        ok = True
    except Exception as exc:  # the failure itself is the record
        rows, info, warnings, ok = None, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, [], False
    from .db import connect
    conn = connect(cfg.db)
    try:
        store.record(conn, "podcast", started, ok, rows=rows, info=info, warnings=warnings)
    finally:
        conn.close()
    if not ok:
        print(f"podcast: error: {info['error']}", file=sys.stderr)
        return 1
    print("podcast: " + (f"{info['file']}, {info['seconds']} s" if rows else info.get("status", "ok"))
          + (f"; warnings: {'; '.join(warnings)}" if warnings else ""))
    return 0


def cmd_bot(args) -> int:
    """bot poll [--once] | send-test. Neither prints the token, the chat id or message text."""
    from . import jobs
    from .modules.bot import runner
    from .modules.bot.client import BotApiError
    cfg = _load(args)
    if args.bot_cmd == "poll" and args.once:
        return 0 if jobs.run(cfg, "bot-poll") else 1
    try:
        if args.bot_cmd == "send-test":
            runner.send_test(cfg)
            print("test message sent")
            return 0
        print("polling; press Ctrl-C to stop")
        runner.loop(cfg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BotApiError as exc:
        print(f"error: Telegram call failed ({exc.kind}): {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("stopped")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kokpit", description=f"Kokpit {__version__}")
    ap.add_argument("--config", help="path to kokpit.toml (default: ./kokpit.toml, then ~/.config/kokpit/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write kokpit.toml and the starter vault")
    p.add_argument("--vault", default="data/vault", help="Markdown folder (default data/vault)")
    p.add_argument("--db", default="data/kokpit.db")
    p.add_argument("--city", help='your city, e.g. "Example City" or "Example City, Country"; geocoded once and written to [place]')
    p.add_argument("--name", help="your name, used in the greeting")
    p.add_argument("--language", help="screen language code, e.g. en or tr")
    p.add_argument("--force", action="store_true")
    p.add_argument("--yes", action="store_true", help="ask nothing; the setup screen asks in the browser")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("run", help="start the dashboard")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("run-job", help="run one scheduled job now")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true", help="organiser only: detect and print, write nothing")
    p.set_defaults(fn=cmd_run_job)
    p = sub.add_parser("schedule", help="run the jobs of the enabled modules in a foreground loop (Docker scheduler)")
    p.set_defaults(fn=cmd_schedule)
    p = sub.add_parser("context", help="print the read-only brain context an agent hook would inject")
    p.add_argument("message", nargs="*", help="the user's message, used only to pick related notes")
    p.add_argument("--max-chars", type=int, default=3500)
    p.set_defaults(fn=cmd_context)
    p = sub.add_parser("brain", help="session receipts and companion compact (writes only inside the vault)")
    bsub = p.add_subparsers(dest="brain_cmd", required=True)
    q = bsub.add_parser("receipt", help="record which files a session changed (idempotent per event id)")
    q.add_argument("--event-id", required=True, help="stable id for this piece of work, e.g. 2026-01-05-garden-timer")
    q.add_argument("--harness", default="manual", help="which tool wrote it: hermes, claude, codex, antigravity, manual")
    q.add_argument("--session", help="optional session id")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--summary", help="short summary (max 4000 characters)")
    g.add_argument("--summary-file", help="file with the summary")
    q.add_argument("--ref", action="append", required=True, help="vault-relative file the session touched (repeat)")
    q = bsub.add_parser("receipts", help="list the newest receipts")
    q.add_argument("--limit", type=int, default=20)
    q = bsub.add_parser("compact", help="move old cards of Last-Session.md / Threads.md to the archive when over the limit")
    q.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    q = bsub.add_parser("context", help="same as `kokpit context`")
    q.add_argument("message", nargs="*")
    q.add_argument("--max-chars", type=int, default=3500)
    q = bsub.add_parser("hook", help="agent hook: read the hook JSON on stdin, print the context in the agent's envelope (read-only, always exit 0)")
    q.add_argument("--harness", default="generic", help="claude, codex, antigravity or generic (plain text)")
    q.add_argument("--event", help="event name when the payload has no hook_event_name (SessionStart, UserPromptSubmit)")
    q.add_argument("--mode", default="turn", help="turn: every prompt (default); session: only at SessionStart")
    q.add_argument("--max-chars", type=int, default=None, help="cap the block (default KOKPIT_CONTEXT_MAX_CHARS or 3500)")
    q = bsub.add_parser("agents", help="set up agent files (AGENTS.md, CLAUDE.md, hook settings)")
    asub = q.add_subparsers(dest="agents_cmd", required=True)
    r = asub.add_parser("init", help="copy the agent templates; existing files are never overwritten")
    r.add_argument("--target", help="folder to write into (default: the vault)")
    r.add_argument("--harness", help="comma list of claude,codex,antigravity (default: all)")
    r.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    p.set_defaults(fn=cmd_brain)
    p = sub.add_parser("place", help="find your city (Open-Meteo geocoder), show event sources, set [place]")
    p.add_argument("query", help='city name, e.g. "Bergamo"')
    p.add_argument("--pick", type=int, help="number of the candidate to use (from the list)")
    p.add_argument("--write", action="store_true", help="update kokpit.toml (keeps a .bak copy)")
    p.add_argument("--confirm-slug", action="store_true", help="make the proposed event slugs active")
    p.add_argument("--count", type=int, default=5, help="how many candidates to list (default 5)")
    p.set_defaults(fn=cmd_place)
    p = sub.add_parser("adapters", help="list local event adapters and whether they are enabled")
    p.set_defaults(fn=cmd_adapters)
    p = sub.add_parser("career", help="career profile and scoring")
    csub = p.add_subparsers(dest="career_cmd", required=True)
    pp = csub.add_parser("profile", help="show, import a CV, or set a profile field")
    psub = pp.add_subparsers(dest="profile_cmd", required=True)
    psub.add_parser("show")
    q = psub.add_parser("import-cv", help="read a .pdf (pdftotext), .txt or .md locally and propose skills and languages")
    q.add_argument("path")
    q.add_argument("--apply", action="store_true", help="merge the proposal into the saved profile")
    q = psub.add_parser("set", help="set one field; lists are comma separated, languages as English:C1,Italian:A2")
    q.add_argument("field")
    q.add_argument("value")
    pp.set_defaults(fn=cmd_career)
    q = csub.add_parser("score", help="score a pasted listing against the profile, with reasons")
    q.add_argument("--title", required=True)
    q.add_argument("--file", help="text file with the description")
    q.add_argument("--location", default="")
    q.add_argument("--remote", choices=["yes", "no", "unknown"], default="unknown")
    q.set_defaults(fn=cmd_career)
    q = csub.add_parser("export", help="CSV of listings with score and reasons")
    q.add_argument("--status", choices=["open", "closed"])
    q.add_argument("--out", help="file to write (default: stdout)")
    q.set_defaults(fn=cmd_career)
    q = csub.add_parser("market", help="per-company counts, weekly trend and top skills")
    q.set_defaults(fn=cmd_career)
    q = csub.add_parser("sources", help="enabled listing sources, browser path and Playwright state")
    q.add_argument("--check", action="store_true", help="installation check only (no network); exit 1 when an enabled browser source is not ready")
    q.set_defaults(fn=cmd_career)
    p = sub.add_parser("ai-usage", help="AI usage totals from the local usage table")
    asub = p.add_subparsers(dest="ai_cmd", required=True)
    q = asub.add_parser("summary", help="totals for today, 7 and 30 days, per tool, readers, limits and advice ids")
    q.set_defaults(fn=cmd_ai_usage)
    p = sub.add_parser("instagram", help="Instagram analysis (read-only API)")
    isub = p.add_subparsers(dest="ig_cmd", required=True)
    isub.add_parser("collect", help="read account, media and story insights now (run twice a day)")
    isub.add_parser("summary", help="aggregates from the local tables: followers, 30-day totals, errors")
    isub.add_parser("refresh-token", help="exchange the long-lived token for a fresh one (explicit only); prints the expiry date, never the token")
    p.set_defaults(fn=cmd_instagram)
    p = sub.add_parser("briefing", help="Briefing extras")
    brsub = p.add_subparsers(dest="briefing_cmd", required=True)
    q = brsub.add_parser("podcast", help="make the two-voice podcast of a digest day ([briefing.podcast])")
    q.add_argument("--day", help="digest day YYYY-MM-DD (default: the newest)")
    q.add_argument("--script-only", action="store_true", help="print the text only; no speech request")
    p.set_defaults(fn=cmd_briefing)
    p = sub.add_parser("bot", help="Telegram bot (one allowed chat)")
    bsub = p.add_subparsers(dest="bot_cmd", required=True)
    q = bsub.add_parser("poll", help="answer commands: long poll loop, or one pass with --once (cron friendly)")
    q.add_argument("--once", action="store_true", help="one getUpdates pass, recorded in Health, then exit")
    bsub.add_parser("send-test", help="send one test message to the configured chat")
    p.set_defaults(fn=cmd_bot)
    p = sub.add_parser("jobs", help="list jobs")
    p.set_defaults(fn=cmd_jobs)
    args = ap.parse_args(argv)
    _load_env(args)
    return args.fn(args)


def _load_env(args) -> None:
    """Keys from the .env file next to kokpit.toml, before any config is read. The environment always wins."""
    from . import secrets
    path = Path(args.config) if args.config else cfgmod.find_config(None)
    if path is None and getattr(args, "cmd", "") == "init":
        path = Path("kokpit.toml")
    if path is not None:
        secrets.load_dotenv(path.resolve().parent / ".env")


if __name__ == "__main__":
    sys.exit(main())
