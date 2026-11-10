"""Kokpit plugin for Hermes Agent.

Self-contained and stdlib-only. It talks to Kokpit through Kokpit's files
(kokpit.toml, the SQLite database opened read-only, the Markdown vault), never
through Python imports, so it works when Hermes installs only this folder.
The only network access is the opt-in news briefing (collector.py): off by
default, and then only to the feed URLs the user configured.
See docs/hermes-plugin.md in the Kokpit repository.
"""
from __future__ import annotations

from . import commands, hooks, schemas, tools
from .contract import Env

PLUGIN_NAME = "kokpit"
TOOLSET = "kokpit"


def _llm(ctx):
    """The host LLM facade (ctx.llm) when this Hermes has one; only used when night_model or briefing_model is on."""
    def get():
        try:
            return ctx.llm
        except Exception:
            return None
    return get


def _setter(ctx):
    """ctx.set_config (plugins.entries.kokpit.settings.<key>, atomic, locked) when this Hermes has it."""
    fn = getattr(ctx, "set_config", None)
    return fn if callable(fn) else None


def _night_setup(parser):
    parser.add_argument("action", nargs="?", default="run", choices=("run", "list"),
                        help="run: make a night pass (default); list: show open proposals")


def _setup_setup(parser):
    parser.add_argument("path", nargs="?", default="", help="vault folder, default ~/Kokpit")


def _no_args(parser):
    return None


def _cli(env: Env, raw):
    def handle(args):
        print(commands.run(env, raw(args)))
        return 0
    return handle


CLI = (("kokpit-night", "Kokpit night pass over the vault (rules only unless night_model is on)", _night_setup,
        lambda a: "night run" if getattr(a, "action", "run") == "run" else "night"),
       ("kokpit-setup", "Create or adopt your Kokpit vault and save it as the plugin's vault_path", _setup_setup,
        lambda a: ("setup " + (getattr(a, "path", "") or "")).strip()),
       ("kokpit-brief", "Print the Kokpit morning brief (plain text, for hermes cron --no-agent)", _no_args,
        lambda a: "brief"),
       ("kokpit-collect", "Collect the Kokpit news briefing now from your configured feeds (needs briefing on)",
        _no_args, lambda a: "news run"))


def register(ctx):
    env = Env(lambda key, default=None: ctx.get_config(key, default=default), llm=_llm(ctx), set_config=_setter(ctx))
    for name, schema in schemas.ALL.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema,
                          handler=tools.handler(env, name), description=schema["description"])
    h = hooks.Hooks(env)
    env.recorder = h  # /kokpit done and kokpit_capture kind receipt write this session's card through the hooks
    ctx.register_hook("pre_llm_call", h.pre_llm_call)
    ctx.register_hook("post_tool_call", h.post_tool_call)
    ctx.register_hook("on_session_start", h.on_session_start)
    ctx.register_hook("on_session_finalize", h.on_session_finalize)
    ctx.register_hook("post_compaction", h.post_compaction)
    ctx.register_command("kokpit", commands.handler(env), description="Kokpit: setup, brief, today, brain, capture, done, night, news",
                         args_hint="setup|brief|today|brain|note|todo|j|done|night|add|skip|health|news|help")
    if hasattr(ctx, "register_cli_command"):  # `hermes kokpit-night|kokpit-setup|kokpit-brief|kokpit-collect`; older Hermes: skipped
        for name, help_, setup, raw in CLI:
            try:
                ctx.register_cli_command(name, help_, setup, _cli(env, raw))
            except Exception:
                pass
