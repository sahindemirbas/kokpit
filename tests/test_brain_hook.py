"""`kokpit brain hook` on a synthetic vault: envelopes, event policy, silent failure, light imports."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kokpit.modules.brain import context, hook  # noqa: E402
from test_context import make_vault  # noqa: E402


def run_hook(args, stdin="", env_extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KOKPIT_")}
    env["PYTHONPATH"] = str(ROOT)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "kokpit", *args], input=stdin.encode("utf-8"),
                          capture_output=True, env=env, cwd=cwd or tempfile.gettempdir(), timeout=30)


class HookCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "vault").mkdir()
        self.vault = make_vault(self.tmp.name + "/vault")
        self.env = {"KOKPIT_VAULT": str(self.vault)}

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, harness, payload, *extra, env=None):
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return run_hook(["brain", "hook", "--harness", harness, *extra], stdin, env or self.env)

    def test_claude_session_start_envelope(self):
        r = self.hook("claude", {"hook_event_name": "SessionStart", "session_id": "x"})
        self.assertEqual(r.returncode, 0)
        out = r.stdout.decode("utf-8")
        self.assertEqual(out.count("\n"), 1)
        data = json.loads(out)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(data["hookSpecificOutput"]["additionalContext"], context.build_context(self.vault, ""))

    def test_codex_prompt_and_empty_prompt(self):
        r = self.hook("codex", {"hook_event_name": "UserPromptSubmit", "prompt": "garden timer"})
        data = json.loads(r.stdout)
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(data["hookSpecificOutput"]["additionalContext"],
                         context.build_context(self.vault, "garden timer"))
        self.assertIn("Garden timer", data["hookSpecificOutput"]["additionalContext"])
        r = self.hook("codex", {"hook_event_name": "UserPromptSubmit", "prompt": "   "})
        self.assertEqual((r.returncode, r.stdout), (0, b""))

    def test_antigravity_and_generic(self):
        r = self.hook("antigravity", {"invocationNum": 0}, "--event", "SessionStart")
        data = json.loads(r.stdout)
        self.assertTrue(data["injectSteps"][0]["ephemeralMessage"].startswith("[Kokpit brain"))
        r = self.hook("generic", {"hook_event_name": "SessionStart"})
        self.assertTrue(r.stdout.decode("utf-8").startswith("[Kokpit brain"))

    def test_other_events_silent(self):
        for ev in ("PostToolUse", "Stop", "SessionEnd", "PreCompact", "Whatever"):
            r = self.hook("claude", {"hook_event_name": ev, "prompt": "garden timer"})
            self.assertEqual((r.returncode, r.stdout), (0, b""), ev)
        r = self.hook("claude", {}, "--event", "PostToolUse")
        self.assertEqual((r.returncode, r.stdout), (0, b""))

    def test_session_mode(self):
        r = self.hook("claude", {"hook_event_name": "UserPromptSubmit", "prompt": "garden timer"}, "--mode", "session")
        self.assertEqual(r.stdout, b"")
        r = self.hook("claude", {"hook_event_name": "SessionStart"}, "--mode", "session")
        self.assertTrue(r.stdout)

    def test_failures_are_silent(self):
        cases = [
            ("not json", self.env),
            ("", self.env),  # empty payload: no event name
            ("[1, 2]", self.env),
            (json.dumps({"hook_event_name": "SessionStart"}), {"KOKPIT_VAULT": self.tmp.name + "/missing"}),
            (json.dumps({"hook_event_name": "SessionStart"}), {"KOKPIT_CONFIG": self.tmp.name + "/none.toml"}),
        ]
        for stdin, env in cases:
            r = run_hook(["brain", "hook", "--harness", "claude"], stdin, env, cwd=self.tmp.name)
            self.assertEqual((r.returncode, r.stdout), (0, b""), stdin)
            err = r.stderr.decode("utf-8").strip()
            self.assertTrue(err.startswith("kokpit hook:"), err)
            self.assertEqual(len(err.splitlines()), 1)

    def test_empty_stdin_with_event_flag(self):
        r = self.hook("generic", "", "--event", "SessionStart")
        self.assertTrue(r.stdout.decode("utf-8").startswith("[Kokpit brain"))

    def test_max_chars_flag_and_env(self):
        big = "\n".join(f"### Thread {i}\nWhere we left off: step {i} of a long garden timer plan." for i in range(60))
        (self.vault / "Threads.md").write_text("# Threads\n## Active Threads\n\n" + big + "\n", encoding="utf-8")
        r = self.hook("generic", {"hook_event_name": "SessionStart"}, "--max-chars", "400")
        self.assertTrue(r.stdout)
        self.assertLessEqual(len(r.stdout.decode("utf-8").rstrip("\n")), 400)
        r = self.hook("generic", {"hook_event_name": "SessionStart"},
                      env={**self.env, "KOKPIT_CONTEXT_MAX_CHARS": "400"})
        self.assertLessEqual(len(r.stdout.decode("utf-8").rstrip("\n")), 400)

    def test_config_flag(self):
        cfg = Path(self.tmp.name) / "kokpit.toml"
        cfg.write_text(f'[brain]\nvault_path = "{self.vault.as_posix()}"\n', encoding="utf-8")
        r = run_hook(["--config", str(cfg), "brain", "hook", "--harness", "claude"],
                     json.dumps({"hook_event_name": "SessionStart"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hookSpecificOutput", json.loads(r.stdout))

    def test_light_imports_and_fast(self):
        code = ("import io, sys, json; sys.argv=['kokpit','brain','hook','--harness','claude'];"
                "sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps({'hook_event_name':'SessionStart'}).encode()));"
                "from kokpit.__main__ import main; main();"
                "heavy=[m for m in sys.modules if m.startswith(('kokpit.web','kokpit.jobs','kokpit.modules.events',"
                "'kokpit.modules.career','kokpit.modules.instagram','kokpit.modules.bot'))];"
                "print('HEAVY', heavy, file=sys.stderr)")
        env = {k: v for k, v in os.environ.items() if not k.startswith("KOKPIT_")}
        env.update(self.env, PYTHONPATH=str(ROOT))
        start = time.monotonic()
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=30)
        elapsed = time.monotonic() - start
        self.assertIn("HEAVY []", r.stderr.decode("utf-8"))
        self.assertIn(b"hookSpecificOutput", r.stdout)
        self.assertLess(elapsed, 5.0)  # an agent's hook timeout; typically ~0.1 s


class HookUnitTests(unittest.TestCase):
    def test_read_payload(self):
        self.assertEqual(hook.read_payload(b""), {})
        self.assertEqual(hook.read_payload("\ufeff{\"a\": 1}".encode("utf-8")), {"a": 1})
        with self.assertRaises(hook.HookError):
            hook.read_payload(b"{")
        with self.assertRaises(hook.HookError):
            hook.read_payload(b"x" * (hook.MAX_STDIN + 10))

    def test_policy(self):
        self.assertTrue(hook.wants_context("SessionStart", ""))
        self.assertTrue(hook.wants_context("UserPromptSubmit", "hi"))
        self.assertFalse(hook.wants_context("UserPromptSubmit", ""))
        self.assertFalse(hook.wants_context("UserPromptSubmit", "hi", "session"))
        self.assertFalse(hook.wants_context("Stop", "hi"))

    def test_unknown_harness_and_mode(self):
        for kw in ({"harness": "other"}, {"harness": "claude", "mode": "never"}):
            with self.assertRaises(hook.HookError):
                hook.run(b'{"hook_event_name":"SessionStart"}', **kw)

    def test_envelope_is_ascii_json(self):
        out = hook.envelope("claude", "SessionStart", "a · b")
        self.assertTrue(out.isascii())
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], "a · b")
        self.assertEqual(hook.envelope("generic", "SessionStart", "x"), "x")


if __name__ == "__main__":
    unittest.main()
