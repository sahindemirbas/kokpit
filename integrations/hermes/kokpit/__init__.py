"""Kokpit plugin for Hermes Agent.

Self-contained and stdlib-only. It talks to Kokpit through Kokpit's files
(kokpit.toml, the SQLite database opened read-only, the Markdown vault), never
through Python imports, so it works when Hermes installs only this folder.
See docs/hermes-plugin.md in the Kokpit repository.
"""
from __future__ import annotations

from . import commands, hooks, schemas, tools
from .contract import Env

PLUGIN_NAME = "kokpit"
TOOLSET = "kokpit"


def _llm(ctx):
    """The host LLM facade (ctx.llm) when this Hermes has one; only used when night_model is on."""
    def get():
        try:
            return ctx.llm
        except Exception:
            return None
    return get


def _cli_setup(parser):
    parser.add_argument("action", nargs="?", default="run", choices=("run", "list"),
                        help="run: make a night pass (default); list: show open proposals")


def _cli_handler(env: Env):
    def handle(args):
        raw = "night run" if getattr(args, "action", "run") == "run" else "night"
        print(commands.run(env, raw))
        return 0
    return handle


def register(ctx):
    env = Env(lambda key, default=None: ctx.get_config(key, default=default), llm=_llm(ctx))
    for name, schema in schemas.ALL.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema,
                          handler=tools.handler(env, name), description=schema["description"])
    h = hooks.Hooks(env)
    ctx.register_hook("pre_llm_call", h.pre_llm_call)
    ctx.register_hook("post_tool_call", h.post_tool_call)
    ctx.register_hook("on_session_start", h.on_session_start)
    ctx.register_hook("on_session_finalize", h.on_session_finalize)
    ctx.register_hook("post_compaction", h.post_compaction)
    ctx.register_command("kokpit", commands.handler(env), description="Kokpit: today, brain, capture, night",
                         args_hint="today|brain|note|todo|j|night|add|skip|health|help")
    if hasattr(ctx, "register_cli_command"):  # `hermes kokpit-night` for cron or systemd; older Hermes: skipped
        try:
            ctx.register_cli_command("kokpit-night", "Kokpit night pass over the vault (rules only unless night_model is on)",
                                     _cli_setup, _cli_handler(env))
        except Exception:
            pass
