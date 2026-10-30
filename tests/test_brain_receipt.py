"""Receipts: idempotent session records. Synthetic vaults only."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from kokpit.modules.brain import receipt

ROOT = Path(__file__).resolve().parents[1]


def vault_with_note(tmp) -> Path:
    v = Path(tmp) / "vault"
    (v / "notes").mkdir(parents=True)
    (v / "notes" / "garden.md").write_text("# Garden timer\n", encoding="utf-8")
    (v / "Last-Session.md").write_text("# Last session\n", encoding="utf-8")
    return v


class Receipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.v = vault_with_note(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **kw):
        args: dict = dict(event_id="ev-1", summary="Added the garden timer note.", refs=["notes/garden.md"],
                    harness="codex", session="s1")
        args.update(kw)
        return receipt.write_receipt(self.v, **args)

    def test_write_success(self):
        res = self.write(now=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc))
        name = hashlib.sha256(b"ev-1").hexdigest() + ".md"
        self.assertEqual(res["status"], "succeeded")
        self.assertEqual(res["path"], f"receipts/{name}")
        self.assertEqual(res["missing_refs"], [])
        text = (self.v / "receipts" / name).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head, body = text[4:].split("\n---\n", 1)
        meta = json.loads(head)
        self.assertEqual(meta["kind"], "receipt")
        self.assertEqual(meta["event_id"], "ev-1")
        self.assertEqual(meta["harness"], "codex")
        self.assertEqual(meta["session"], "s1")
        self.assertEqual(meta["refs"], ["notes/garden.md"])
        self.assertEqual(meta["created_at"], "2026-01-05T09:00:00+00:00")
        self.assertEqual(body, "Added the garden timer note.\n")

    def test_same_call_twice_is_unchanged(self):
        self.write()
        p = self.v / receipt.DIR / receipt.receipt_name("ev-1")
        before, mtime = p.read_bytes(), p.stat().st_mtime_ns
        time.sleep(0.01)
        res = self.write(summary="  Added the garden timer note.\n")
        self.assertEqual(res["status"], "unchanged")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(p.stat().st_mtime_ns, mtime)

    def test_conflict_leaves_file(self):
        self.write()
        p = self.v / receipt.DIR / receipt.receipt_name("ev-1")
        before = p.read_bytes()
        res = self.write(summary="Something else entirely.")
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(res["reason"], "event id already used with different content")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(self.write(refs=["notes/garden.md", "Last-Session.md"])["status"], "conflict")

    def test_validation_errors(self):
        bad = [dict(event_id=""), dict(event_id="-x"), dict(event_id="a b"), dict(event_id="x" * 101),
               dict(summary="   "), dict(summary="x" * 4001), dict(refs=[]), dict(refs=["../x"]),
               dict(refs=["notes/../../x"]), dict(refs=["/etc/passwd"]), dict(harness="Bad Harness"),
               dict(session="has space")]
        for kw in bad:
            with self.subTest(kw=list(kw)), self.assertRaises(ValueError):
                self.write(**kw)
        self.assertFalse((self.v / receipt.DIR).exists())

    def test_summary_at_limit_is_accepted(self):
        self.assertEqual(self.write(summary="x" * 4000)["status"], "succeeded")

    def test_missing_refs_reported(self):
        res = self.write(refs=["notes/garden.md", "notes/not-yet.md"])
        self.assertEqual(res["status"], "succeeded")
        self.assertEqual(res["missing_refs"], ["notes/not-yet.md"])

    def test_secret_pattern_rejected(self):
        for s in ("set api_key=abc123 in the env", "the key is sk-abcdefgh12345", "-----BEGIN KEY-----"):
            with self.subTest(s=s[:10]), self.assertRaises(ValueError) as cm:
                self.write(event_id="ev-secret", summary=s)
            self.assertIn("secret", str(cm.exception))
        self.assertFalse((self.v / receipt.DIR).exists())

    def test_read_receipts_sorted_and_skips_broken(self):
        self.write(event_id="old", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.write(event_id="new", now=datetime(2026, 1, 3, tzinfo=timezone.utc))
        self.write(event_id="mid", now=datetime(2026, 1, 2, tzinfo=timezone.utc))
        (self.v / receipt.DIR / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
        (self.v / receipt.DIR / "badjson.md").write_text("---\n{not json\n---\nx\n", encoding="utf-8")
        res = receipt.scan_receipts(self.v)
        self.assertEqual([r["event_id"] for r in res["receipts"]], ["new", "mid", "old"])
        self.assertEqual(res["skipped"], 2)
        self.assertTrue((self.v / receipt.DIR / "broken.md").exists())
        self.assertEqual([r["event_id"] for r in receipt.read_receipts(self.v, limit=1)], ["new"])
        self.assertEqual(receipt.read_receipts(self.v)[0]["refs"], ["notes/garden.md"])

    def test_receipts_never_enter_context(self):
        from kokpit.modules.brain import context
        self.write(summary="Unique receipt marker text.")
        self.assertEqual(context.safe_read(self.v, self.v / receipt.DIR / receipt.receipt_name("ev-1")), "")
        self.assertNotIn("Unique receipt marker", context.build_context(self.v, "receipt marker"))


class ReceiptCli(unittest.TestCase):
    def run_cli(self, cfg, *args):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        return subprocess.run([sys.executable, "-m", "kokpit", "--config", str(cfg), "brain", *args],
                              cwd=ROOT, env=env, capture_output=True, text=True)

    def test_cli_receipt_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = vault_with_note(tmp)
            cfg = Path(tmp) / "kokpit.toml"
            cfg.write_text(f'[brain]\nvault_path = "{v}"\n', encoding="utf-8")
            args = ["receipt", "--event-id", "cli-1", "--harness", "manual", "--summary", "Wrote the note.",
                    "--ref", "notes/garden.md"]
            out = self.run_cli(cfg, *args)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["status"], "succeeded")
            out = self.run_cli(cfg, *args)
            self.assertEqual((out.returncode, json.loads(out.stdout)["status"]), (0, "unchanged"))
            out = self.run_cli(cfg, *args[:-4], "--summary", "Different.", "--ref", "notes/garden.md")
            self.assertEqual(out.returncode, 3)
            self.assertEqual(json.loads(out.stdout)["status"], "conflict")
            out = self.run_cli(cfg, *args[:-1], "../outside.md")
            self.assertEqual(out.returncode, 2)
            self.assertIn("error:", out.stderr)
            sf = Path(tmp) / "summary.txt"
            sf.write_text("From a file.\n", encoding="utf-8")
            out = self.run_cli(cfg, "receipt", "--event-id", "cli-2", "--summary-file", str(sf), "--ref", "notes/garden.md")
            self.assertEqual(out.returncode, 0, out.stderr)
            out = self.run_cli(cfg, "receipts")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("cli-1", out.stdout)
            self.assertIn("cli-2", out.stdout)
            self.assertIn("notes/garden.md", out.stdout)


if __name__ == "__main__":
    unittest.main()
