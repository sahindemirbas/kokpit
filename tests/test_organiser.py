"""Night organiser: five proposal kinds, idempotency, no vault edits, one report file. Synthetic notes only."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from kokpit import config as cfgmod, jobs, scheduler
from kokpit.db import connect
from kokpit.modules.bot import commands
from kokpit.modules.organiser import api, collect

TODAY = date(2026, 10, 30)
NOTES = {
    "notes/irrigation.md": "---\ntype: project\ndate: 2026-10-20\nsummary: ESP32 soil sensor with a relay that opens the valve.\n---\n# Irrigation timer\nWaiting for the relay board to arrive.\n",
    "notes/irrigation-parts.md": "---\ntype: note\nsummary: Parts list for the garden timer project.\ndate: 2026-10-25\n---\n# Parts\nThe relay board arrived and works.\n",
    "notes/italian.md": "---\ntype: project\nsummary: Unit 7 done, next unit 8 on the imperfetto.\naliases: [Italian lessons]\n---\n# Italian\nSteady progress.\n",
    "notes/plan.md": "---\ntype: note\nsummary: Weekly plan for the study hours.\n---\n# Plan\nPractice Italian lessons on Monday.\n",
    "inbox/loose.md": "# Loose idea\nA small thing with no frontmatter.\n",
    "inbox/odd.md": "---\ntype: gadget\nsummary: Something with an unusual type.\n---\n# Odd\nText.\n",
    "inbox/short.md": "---\ntype: idea\nsummary: tiny\n---\n# Short note\nText.\n",
    "notes/dup-a.md": "---\ntype: note\nsummary: First copy of the duplicate title.\n---\n# Same title\nA.\n",
    "notes/dup-b.md": "---\ntype: note\nsummary: Second copy of the duplicate title.\n---\n# Same title\nB.\n",
}
THREADS = "## Active Threads\n\n### Old thread\nWhere we left off (2026-08-01): paused.\n\n### Fresh thread\nWhere we left off (2026-10-28): going.\n"


def hashes(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def make(tmp: Path, apply=False, enabled=True) -> cfgmod.Config:
    v = tmp / "vault"
    for rel, text in NOTES.items():
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text(text)
    (v / "Threads.md").write_text(THREADS)
    p = tmp / "kokpit.toml"
    p.write_text(f'''[modules]
organiser = {"true" if enabled else "false"}
[brain]
vault_path = "{v}"
[organiser]
stale_days = 30
apply_report_to_vault = {"true" if apply else "false"}
[organiser.folders]
project = "projects"
[db]
path = "{tmp / "data" / "kokpit.db"}"
''')
    return cfgmod.load(p)


class OrganiserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_job(self, cfg, day=TODAY):
        conn = connect(cfg.db)
        try:
            return collect.collect(cfg, conn, day)
        finally:
            conn.close()

    def rows(self, cfg):
        conn = connect(cfg.db)
        try:
            return collect.open_proposals(conn)
        finally:
            conn.close()

    def kinds(self, cfg, kind):
        return [(r["path"], r["detail"], r["target"]) for r in self.rows(cfg) if r["kind"] == kind]

    def test_all_five_kinds(self):
        cfg = make(self.tmp)
        new, info, warns = self.run_job(cfg)
        self.assertEqual(warns, [])
        self.assertEqual(new, info["open"])
        c = self.kinds(cfg, "categorise")
        self.assertIn(("inbox/loose.md", "no-type", ""), c)
        self.assertIn(("inbox/odd.md", "unknown-type", ""), c)
        self.assertIn(("notes/irrigation.md", "folder", "projects"), c)
        self.assertIn(("notes/plan.md", "mention", "notes/italian.md"), self.kinds(cfg, "link"))
        cond = self.kinds(cfg, "conditions")
        self.assertEqual([x[0] for x in cond], ["notes/irrigation.md"])
        self.assertEqual(cond[0][2], "notes/irrigation-parts.md")
        e = self.kinds(cfg, "enrich")
        self.assertIn(("inbox/short.md", "short-summary", ""), e)
        self.assertIn(("inbox/loose.md", "no-frontmatter", ""), e)
        s = self.kinds(cfg, "sync")
        self.assertIn(("notes/dup-a.md", "duplicate-title", "notes/dup-b.md"), s)
        self.assertIn(("Threads.md", "stale-thread", "Old thread"), s)
        self.assertNotIn(("Threads.md", "stale-thread", "Fresh thread"), s)

    def test_no_link_when_already_linked(self):
        cfg = make(self.tmp)
        v = cfg.vault
        (v / "notes/plan.md").write_text(NOTES["notes/plan.md"] + "See [[italian]].\n")
        self.run_job(cfg)
        self.assertNotIn("notes/plan.md", [x[0] for x in self.kinds(cfg, "link")])

    def test_idempotent(self):
        cfg = make(self.tmp)
        first, _, _ = self.run_job(cfg)
        before = self.rows(cfg)
        second, _, _ = self.run_job(cfg)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual([r["id"] for r in before], [r["id"] for r in self.rows(cfg)])

    def test_gone_when_fixed(self):
        cfg = make(self.tmp)
        self.run_job(cfg)
        (cfg.vault / "inbox/loose.md").unlink()
        self.run_job(cfg, date(2026, 10, 31))
        self.assertFalse([r for r in self.rows(cfg) if r["path"] == "inbox/loose.md"])

    def test_default_mode_never_touches_vault(self):
        cfg = make(self.tmp)
        before = hashes(cfg.vault)
        _, info, _ = self.run_job(cfg)
        self.assertEqual(hashes(cfg.vault), before)
        rp = Path(info["report"])
        self.assertTrue(rp.exists())
        self.assertEqual(rp.parent, cfg.db.parent)
        self.assertNotIn("\u2014", rp.read_text())

    def test_apply_report_writes_one_new_file(self):
        cfg = make(self.tmp, apply=True)
        before = hashes(cfg.vault)
        self.run_job(cfg)
        self.run_job(cfg)  # second run replaces only its own file
        after = hashes(cfg.vault)
        added = set(after) - set(before)
        self.assertEqual(added, {"Organiser/organiser-report.md"})
        for k, h in before.items():
            self.assertEqual(after[k], h)

    def test_apply_never_overwrites_foreign_note(self):
        cfg = make(self.tmp, apply=True)
        foreign = cfg.vault / "Organiser" / "organiser-report.md"
        foreign.parent.mkdir()
        foreign.write_text("# my own note\n")
        _, _, warns = self.run_job(cfg)
        self.assertEqual(foreign.read_text(), "# my own note\n")
        self.assertTrue(warns)

    def test_report_folder_must_stay_inside_vault(self):
        cfg = make(self.tmp, apply=True)
        cfg.organiser["report_folder"] = "../escape"
        _, _, warns = self.run_job(cfg)
        self.assertTrue(warns)
        self.assertFalse((self.tmp / "escape").exists())

    def test_morning_text_deterministic(self):
        cfg = make(self.tmp)
        self.run_job(cfg)
        rows = self.rows(cfg)
        t = collect.morning_text(rows)
        self.assertEqual(t, collect.morning_text(list(rows)))
        lines = t.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith(f"Organiser: {len(rows)} open proposal(s)"))
        self.assertEqual(collect.morning_text([]), "Organiser: no open proposals.")
        self.assertEqual(api.view(cfg)["text"], t)
        self.assertNotIn("\u2014", t)

    def test_bot_morning_line_and_off_state(self):
        cfg = make(self.tmp)
        self.assertEqual(api.morning(cfg), "")  # no run yet
        self.run_job(cfg)
        self.assertIn("Organiser:", commands.morning_line(cfg))
        cfg.modules["organiser"] = False
        self.assertEqual(api.morning(cfg), "")
        self.assertFalse(api.view(cfg)["enabled"])

    def test_scheduler_and_registry(self):
        self.assertFalse(cfgmod.Config(path=self.tmp / "k.toml").modules.get("organiser", False))
        cfg = make(self.tmp)
        ents = [e for e in scheduler.entries_for(cfg) if e.job == "organiser"]
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].at, ("03:30",))
        cfg.modules["organiser"] = False
        self.assertNotIn("organiser", {e.job for e in scheduler.entries_for(cfg)})
        self.assertIn("organiser", jobs.REGISTRY)

    def test_job_records_run(self):
        cfg = make(self.tmp)
        self.assertTrue(jobs.run(cfg, "organiser"))
        self.assertTrue(api.view(cfg)["freshness"])

    def test_model_summary_off_by_default(self):
        cfg = make(self.tmp)
        _, info, warns = self.run_job(cfg)
        self.assertEqual(warns, [])
        self.assertEqual(api.view(cfg)["summary"], "")


if __name__ == "__main__":
    unittest.main()
