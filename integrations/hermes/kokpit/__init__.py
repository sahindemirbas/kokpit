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


def register(ctx):
    env = Env(lambda key, default=None: ctx.get_config(key, default=default))
    for name, schema in schemas.ALL.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema,
                          handler=tools.handler(env, name), description=schema["description"])
    h = hooks.Hooks(env)
    ctx.register_hook("pre_llm_call", h.pre_llm_call)
    ctx.register_hook("post_tool_call", h.post_tool_call)
    ctx.register_hook("on_session_finalize", h.on_session_finalize)
    ctx.register_hook("post_compaction", h.post_compaction)
    ctx.register_command("kokpit", commands.handler(env), description="Kokpit: today, brain, health",
                         args_hint="today|brain|health|help")
