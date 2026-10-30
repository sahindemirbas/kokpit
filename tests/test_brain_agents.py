"""`kokpit brain agents init`: templates, placeholders, never overwrite, dry run, no personal values."""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kokpit import __main__ as cli  # noqa: E402
from kokpit.modules.brain import agent_setup  # noqa: E402

EXPECTED = ["AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".codex/hooks.json",
            ".codex/config.toml", ".agents/hooks.json"]
FORBIDDEN = re.compile(r"sahin|cremona|beynim|/home/|cattolica|tailscale|850|beyin", re.I)


def files(target: Path) -> dict:
    return {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}


class AgentsInitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.vault = base / "vault"
        self.vault.mkdir()
        self.cfg = base / "kokpit.toml"
        self.cfg.write_text(f'[brain]\nvault_path = "{self.vault.as_posix()}"\n', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["--config", str(self.cfg), "brain", "agents", "init", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_fresh_init(self):
        rc, out, _ = self.cli()
        self.assertEqual(rc, 0)
        report = json.loads(out)
        self.assertEqual(report["written"], EXPECTED)
        self.assertEqual(report["skipped"], [])
        for rel in EXPECTED:
            text = (self.vault / rel).read_text(encoding="utf-8")
            self.assertNotIn("{{", text, rel)
        self.assertTrue((self.vault / "CLAUDE.md").read_text(encoding="utf-8").startswith("@AGENTS.md\n"))
        self.assertTrue(tomllib.loads((self.vault / ".codex/config.toml").read_text())["features"]["hooks"])
        for rel, harness in ((".claude/settings.json", "claude"), (".codex/hooks.json", "codex")):
            data = json.loads((self.vault / rel).read_text(encoding="utf-8"))
            self.assertEqual(sorted(data["hooks"]), ["SessionStart", "UserPromptSubmit"])
            for ev in data["hooks"].values():
                h = ev[0]["hooks"][0]
                self.assertEqual(h["timeout"], 5)
                self.assertIn(f"brain hook --harness {harness}", h["command"])
                self.assertIn(str(self.cfg.resolve()), h["command"])
        ag = json.loads((self.vault / ".agents/hooks.json").read_text(encoding="utf-8"))
        h = ag["kokpit-brain"]["PreInvocation"][0]
        self.assertEqual(h["timeout"], 5)
        self.assertIn("brain hook --harness antigravity", h["command"])

    def test_second_run_skips_everything(self):
        self.cli()
        before = files(self.vault)
        rc, out, _ = self.cli()
        report = json.loads(out)
        self.assertEqual((rc, report["written"], report["skipped"]), (0, [], EXPECTED))
        self.assertEqual(files(self.vault), before)

    def test_existing_settings_not_overwritten(self):
        own = self.vault / ".claude" / "settings.json"
        own.parent.mkdir()
        own.write_text('{"permissions": {}}\n', encoding="utf-8")
        rc, out, err = self.cli("--harness", "claude")
        report = json.loads(out)
        self.assertEqual(own.read_text(encoding="utf-8"), '{"permissions": {}}\n')
        self.assertIn(".claude/settings.json", report["skipped"])
        self.assertIn("brain hook --harness claude", report["snippets"][".claude/settings.json"])
        self.assertIn("merge this snippet by hand", err)
        self.assertEqual(report["written"], ["AGENTS.md", "CLAUDE.md"])

    def test_dry_run_writes_nothing(self):
        rc, out, _ = self.cli("--dry-run")
        report = json.loads(out)
        self.assertEqual(report["written"], EXPECTED)
        self.assertTrue(report["dry_run"])
        self.assertEqual(files(self.vault), {})

    def test_target_and_harness_filter(self):
        other = Path(self.tmp.name) / "project"
        rc, out, _ = self.cli("--target", str(other), "--harness", "codex")
        self.assertEqual(json.loads(out)["written"], ["AGENTS.md", ".codex/hooks.json", ".codex/config.toml"])
        self.assertTrue((other / ".codex/hooks.json").exists())
        rc, _, err = self.cli("--harness", "kiro")
        self.assertEqual(rc, 2)

    def test_quoting_paths_with_spaces(self):
        report = agent_setup.init_agents(Path(self.tmp.name) / "t", Path(self.tmp.name) / "my dir" / "kokpit.toml",
                                         ("claude",), kokpit="'/opt/k p/kokpit'")
        self.assertIn(".claude/settings.json", report["written"])
        cmd = json.loads((Path(self.tmp.name) / "t/.claude/settings.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("'/opt/k p/kokpit'", cmd)
        self.assertIn("my dir/kokpit.toml'", cmd)

    def test_templates_are_generic(self):
        for p in agent_setup.TEMPLATES.iterdir():
            text = p.read_text(encoding="utf-8")
            self.assertIsNone(FORBIDDEN.search(text), p.name)
            self.assertNotIn("\u2014", text, p.name)
            if p.suffix == ".json":
                json.loads(text)  # placeholders sit inside strings, so the raw template is valid JSON
        agents_md = (agent_setup.TEMPLATES / "AGENTS.md.tmpl").read_text(encoding="utf-8")
        self.assertLessEqual(len(agents_md), 4000)
        self.assertIn("kokpit brain receipt", agents_md)
        self.assertIn("kokpit brain compact", agents_md)

    def test_kokpit_command_absolute(self):
        cmd = agent_setup.kokpit_command()
        self.assertTrue("/" in cmd or "\\" in cmd)


if __name__ == "__main__":
    unittest.main()
