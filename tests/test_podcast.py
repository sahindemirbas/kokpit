"""Briefing podcast: fake local servers stand in for the model, Gemini TTS and an OpenAI-compatible TTS.

No real network call and no real key: every base URL points at 127.0.0.1.
"""
from __future__ import annotations

import array
import base64
import contextlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kokpit import __main__ as cli, config as cfgmod, jobs, scheduler
from kokpit.db import connect
from kokpit.modules.briefing import api, collect, podcast, voices
from kokpit.modules.model.client import ModelDisabled
from kokpit.web import make_handler

GEMINI_KEY = "AIza" + "fake" + "-" + "z" * 8
MODEL_KEY = "sk-" + "model" + "-" + "q" * 6
TTS_KEY = "sk-" + "tts" + "-" + "w" * 6
DAY = "2026-10-29"
PCM_PER_LINE = 480  # bytes of fake audio per line (10 ms)


def tone(n_bytes: int, amp: int = 3000) -> bytes:
    a = array.array("h", [amp if (i // 12) % 2 else -amp for i in range(n_bytes // 2)])
    return a.tobytes()


class Fake:
    model_answers: list = []
    model_prompts: list = []
    gemini: list = []
    speech: list = []
    gemini_status = 200
    lock = threading.Lock()

    @classmethod
    def reset(cls):
        cls.model_answers, cls.model_prompts, cls.gemini, cls.speech, cls.gemini_status = [], [], [], [], 200


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/chat/completions":
            with Fake.lock:
                Fake.model_prompts.append(body["messages"][1]["content"])
                ans = Fake.model_answers.pop(0) if Fake.model_answers else "[]"
            return self._send(200, json.dumps({"choices": [{"message": {"content": ans}}]}).encode())
        if self.path.startswith("/gem/models/") and self.path.endswith(":generateContent"):
            Fake.gemini.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
            if Fake.gemini_status != 200:
                return self._send(Fake.gemini_status, b'{"error": {"message": "Resource has been exhausted"}}')
            n = len(body["contents"][0]["parts"])
            data = base64.b64encode(tone(PCM_PER_LINE * n)).decode()
            return self._send(200, json.dumps({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000", "data": data}}]}}]}).encode())
        if self.path == "/v1/audio/speech":
            Fake.speech.append({"voice": body.get("voice"), "format": body.get("response_format"), "input": body["input"],
                                "auth": self.headers.get("Authorization")})
            return self._send(200, tone(PCM_PER_LINE), "audio/pcm")
        self._send(404, b"{}")

    def log_message(self, *a):
        pass


def lines_json(*pairs) -> str:
    return json.dumps([{"lines": [{"who": w, "text": t} for w, t in pairs]}])


def make_cfg(tmp: Path, base: str, provider="gemini", name="", model=True, enabled=True, bot=False, intro=False) -> cfgmod.Config:
    p = tmp / "kokpit.toml"
    p.write_text(f'''[modules]
briefing = true
bot = {str(bot).lower()}
[health]
db_path = "t.db"
[model]
enabled = {str(model).lower()}
base_url = "{base}/v1"
model = "fake-1"
[model.uses]
podcast = true
[briefing]
[[briefing.topics]]
name = "releases"
feeds = ["{base}/rss"]
keywords = ["release"]
[briefing.tts]
provider = "openai_compatible"
base_url = "{base}/v1"
model = "tts-fake"
[briefing.podcast]
enabled = {str(enabled).lower()}
minutes = 5
listener_name = "{name}"
intro = {str(intro).lower()}
tts_provider = "{provider}"
gemini_base_url = "{base}/gem"
gemini_model = "tts-model-x"
speakers = {{ A = "VoiceOne", B = "VoiceTwo" }}
max_turns_per_request = 12
[bot]
chat_id = "42"
''')
    return cfgmod.load(p)


def item(topic, n, title=None):
    return {"topic": topic, "url": f"http://x/{topic}/{n}", "title": title or f"{topic} item {n}", "tags": ["t"],
            "published": None, "score": 1.0 - n / 10, "headline": n == 0, "summary": f"summary {n}", "why": "",
            "by_model": False, "project": None, "reason": None, "extra": {"stars": 10 + n}}


def seed(cfg, items=None, day=DAY):
    conn = connect(cfg.db)
    try:
        collect.store(conn, day, items or [item("releases", 0), item("releases", 1), item("security", 0)])
    finally:
        conn.close()


TWO = [lines_json(("A", "Good morning. What came out today?"), ("B", "A new release with 209M downloads, up %30.")),
       lines_json(("A", "And security?"), ("B", "One fix landed."), ("A", "Thanks for listening, goodbye."))]


class Base(unittest.TestCase):
    def setUp(self):
        Fake.reset()
        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)
        self.env = mock.patch.dict(os.environ, {"KOKPIT_GEMINI_API_KEY": GEMINI_KEY, "KOKPIT_MODEL_API_KEY": MODEL_KEY,
                                                "KOKPIT_TTS_API_KEY": TTS_KEY})
        self.env.start()
        self.nompeg = mock.patch("kokpit.modules.briefing.podcast.shutil.which", return_value=None)
        self.nompeg.start()

    def tearDown(self):
        self.nompeg.stop()
        self.env.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.td.cleanup()

    def cfg(self, **kw):
        return make_cfg(self.tmp, self.base, **kw)


class ScriptTests(Base):
    def test_two_segments_and_previous_lines_in_second_prompt(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        day, items = podcast.load_items(cfg)
        sc = podcast.script(cfg, day, items)
        self.assertEqual([s["topic"] for s in sc["segments"]], ["releases", "security"])
        self.assertEqual(len(Fake.model_prompts), 2)
        self.assertIn("previous segment ended", Fake.model_prompts[1])
        self.assertIn("A new release with 209 million downloads, up 30 percent.", Fake.model_prompts[1])
        self.assertIn("209 million", sc["segments"][0]["lines"][1]["text"])
        self.assertTrue(podcast.script_path(cfg, DAY).is_file())

    def test_word_target_follows_items_with_floor(self):
        cfg = self.cfg()
        segs = podcast.segments([item("a", 0), item("a", 1), item("a", 2), item("b", 0)])
        self.assertEqual(podcast.word_targets(cfg, segs), [488, 250])  # 5 min * 130 = 650 words

    def test_forbidden_phrase_retried_then_sentence_dropped(self):
        cfg = self.cfg()
        seed(cfg, [item("releases", 0)])
        bad = lines_json(("A", "Hello everyone, welcome. What is new?"), ("B", "My name is B. A release shipped."))
        still = lines_json(("A", "Hello everyone. What is new?"), ("B", "A release shipped. Dear listeners, enjoy."))
        Fake.model_answers = [bad, still]
        day, items = podcast.load_items(cfg)
        sc = podcast.script(cfg, day, items)
        self.assertEqual(len(Fake.model_prompts), 2)
        self.assertIn("not allowed", Fake.model_prompts[1])
        text = podcast.as_text(sc)
        self.assertNotRegex(text.lower(), r"hello everyone|dear listeners|my name is")
        self.assertIn("A: What is new?", text)
        self.assertIn("B: A release shipped.", text)

    def test_retry_that_comes_back_clean_is_used(self):
        cfg = self.cfg()
        seed(cfg, [item("releases", 0)])
        Fake.model_answers = [lines_json(("A", "I am A. Hi.")), lines_json(("A", "What shipped?"), ("B", "A release."))]
        day, items = podcast.load_items(cfg)
        sc = podcast.script(cfg, day, items)
        self.assertEqual(sc["segments"][0]["lines"][0]["text"], "What shipped?")

    def test_no_listener_name_means_no_name_anywhere(self):
        cfg = self.cfg(name="")
        seed(cfg)
        Fake.model_answers = list(TWO)
        day, items = podcast.load_items(cfg)
        podcast.script(cfg, day, items)
        self.assertIn("without any name", Fake.model_prompts[0])
        self.assertNotIn("first name", " ".join(Fake.model_prompts))

    def test_listener_name_only_in_opening_and_closing(self):
        cfg = self.cfg(name="Robin")
        seed(cfg, [item("a", 0), item("b", 0), item("c", 0)])
        Fake.model_answers = [lines_json(("A", "Hi.")), lines_json(("A", "Next.")), lines_json(("A", "Bye."))]
        day, items = podcast.load_items(cfg)
        podcast.script(cfg, day, items)
        self.assertIn("Robin", Fake.model_prompts[0])
        self.assertNotIn("Robin", Fake.model_prompts[1])
        self.assertIn("Robin", Fake.model_prompts[2])

    def test_empty_segment_is_an_error_and_writes_nothing(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = [TWO[0], "no json here"]
        day, items = podcast.load_items(cfg)
        with self.assertRaises(podcast.PodcastError):
            podcast.script(cfg, day, items)
        self.assertFalse(podcast.script_path(cfg, DAY).exists())
        with self.assertRaises(podcast.PodcastError):
            podcast.make(cfg)
        self.assertFalse(podcast.audio_path(cfg, DAY).exists())
        self.assertEqual(list((self.tmp / "audio").glob("*")) if (self.tmp / "audio").exists() else [], [])

    def test_same_fingerprint_does_not_call_the_model_again(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        day, items = podcast.load_items(cfg)
        a = podcast.script(cfg, day, items)
        b = podcast.script(cfg, day, items)
        self.assertEqual(len(Fake.model_prompts), 2)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        changed = make_cfg(self.tmp, self.base)
        changed.briefing["podcast"]["minutes"] = 30
        self.assertNotEqual(podcast.fingerprint(changed, items), a["fingerprint"])

    def test_model_off_raises_model_disabled(self):
        cfg = self.cfg(model=False)
        seed(cfg)
        day, items = podcast.load_items(cfg)
        with self.assertRaises(ModelDisabled):
            podcast.script(cfg, day, items)
        self.assertEqual(Fake.model_prompts, [])

    def test_script_only_cli_makes_no_speech_request(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--config", str(cfg.path), "briefing", "podcast", "--day", DAY, "--script-only"])
        self.assertEqual(rc, 0)
        self.assertIn("## releases", out.getvalue())
        self.assertIn("B: One fix landed.", out.getvalue())
        self.assertEqual(Fake.gemini, [])
        self.assertEqual(Fake.speech, [])

    def test_clean_text(self):
        self.assertEqual(podcast.clean_text("**Big** news: 7B model, 1.5K stars, 45% faster (see https://x.y/z) \U0001F680"),
                         "Big news: 7 billion model, 1.5 thousand stars, 45 percent faster see")


class VoiceTests(Base):
    def test_gemini_body_parts_speakers_and_voices(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        row = podcast.make(cfg)
        self.assertEqual(len(Fake.gemini), 1)  # 2 + 3 lines are planned as one request
        first = Fake.gemini[0]
        self.assertTrue(first["path"].endswith("/models/tts-model-x:generateContent"))
        self.assertEqual(first["key"], GEMINI_KEY)
        parts = first["body"]["contents"][0]["parts"]
        self.assertEqual(len(parts), 5)
        self.assertEqual([p["speechMetadata"]["speaker"] for p in parts], ["A", "B", "A", "B", "A"])
        gc = first["body"]["generationConfig"]
        self.assertEqual(gc["responseModalities"], ["AUDIO"])
        cfgs = gc["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
        self.assertEqual({c["speaker"]: c["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] for c in cfgs},
                         {"A": "VoiceOne", "B": "VoiceTwo"})
        wav = podcast.audio_path(cfg, DAY)
        self.assertTrue(wav.is_file())
        self.assertEqual(row["file"], wav.name)
        with wave.open(str(wav)) as w:
            self.assertEqual((w.getframerate(), w.getsampwidth(), w.getnchannels()), (24000, 2, 1))
            frames = w.getnframes()
        gap = int(24000 * voices.GAP)
        self.assertEqual(frames, (PCM_PER_LINE * 5) // 2)  # 2 + 3 lines in one request, no gap
        self.assertGreater(gap, 0)
        conn = connect(cfg.db)
        r = conn.execute("SELECT * FROM briefing_podcast WHERE day=?", (DAY,)).fetchone()
        conn.close()
        self.assertEqual(r["engine"], "gemini:tts-model-x")
        self.assertAlmostEqual(r["seconds"], round(frames / 24000, 2))

    def test_thirteen_lines_two_requests_seven_and_six(self):
        lines = [{"who": "AB"[i % 2], "text": f"line {i}"} for i in range(13)]
        self.assertEqual([len(c) for c in voices.split_turns(lines, 12)], [7, 6])
        self.assertEqual([len(c) for c in voices.split_turns(lines[:12], 12)], [12])
        cfg = self.cfg()
        seed(cfg, [item("releases", 0)])
        Fake.model_answers = [json.dumps([{"lines": lines}])]
        podcast.make(cfg)
        self.assertEqual([len(g["body"]["contents"][0]["parts"]) for g in Fake.gemini], [7, 6])
        with wave.open(str(podcast.audio_path(cfg, DAY))) as w:
            self.assertEqual(w.getnframes() * 2, PCM_PER_LINE * 13 + len(voices.silence(voices.GAP)))

    def test_whole_episode_fits_max_requests_with_intro_first(self):
        cfg = self.cfg(intro=True)
        cfg.briefing["podcast"]["max_requests"] = 3
        seed(cfg)
        big = [{"who": "AB"[i % 2], "text": f"first {i}"} for i in range(30)]
        small = [{"who": "AB"[i % 2], "text": f"second {i}"} for i in range(25)]
        Fake.model_answers = [json.dumps([{"lines": big}]), json.dumps([{"lines": small}])]
        today = {"sections": {"weather": {"status": "ok", "days": [{"date": "2026-10-30", "text": "sunny", "min": 8.2, "max": 17.6}]}}}
        with mock.patch("kokpit.modules.today.api.view", return_value=today):
            row = podcast.make(cfg, now=datetime(2026, 10, 30, 7, 0))
        self.assertEqual(len(Fake.gemini), 3)
        sizes = [len(g["body"]["contents"][0]["parts"]) for g in Fake.gemini]
        self.assertEqual(sizes, [16, 16, 16])  # 1 intro + 55 lines trimmed to 3 * 16
        first = Fake.gemini[0]["body"]["contents"][0]["parts"][0]
        self.assertEqual(first["speechMetadata"]["speaker"], "A")
        self.assertIn("Friday, 30 October", first["text"])
        texts = [p["text"] for g in Fake.gemini for p in g["body"]["contents"][0]["parts"]]
        for kept in ("first 0", "first 29", "second 0", "second 24"):
            self.assertTrue(any(t.endswith(kept) for t in texts), kept)
        self.assertIn("trimmed 8 lines to fit 3 requests", row["note"])
        with wave.open(str(podcast.audio_path(cfg, DAY))) as w:
            self.assertEqual(w.getnframes() * 2, PCM_PER_LINE * 48 + 2 * len(voices.silence(voices.GAP)))

    def test_silence_byte_count(self):
        self.assertEqual(len(voices.silence(0.35)), 8400 * 2)
        self.assertEqual(len(voices.join([b"\x01\x00" * 10, b"\x01\x00" * 10], 0.35)), 40 + 16800)

    def test_trim_tail_cuts_burst_and_keeps_clean_audio(self):
        speech = tone(24000 * 2 * 2)  # 2 s of quiet-ish speech
        quiet = voices.silence(0.4)
        burst = array.array("h", [32767, -32768] * 2400).tobytes()  # 0.2 s at full scale
        dirty = speech + quiet + burst + voices.silence(0.1)
        cut = voices.trim_tail(dirty)
        self.assertLess(len(cut), len(speech) + len(quiet) + 1)
        self.assertGreaterEqual(len(cut), len(speech))
        self.assertEqual(voices.trim_tail(speech + quiet), speech + quiet)
        loud_speech = tone(24000 * 2, amp=31000)  # 1 s loud, no silence before it: not a glitch
        self.assertEqual(voices.trim_tail(speech + loud_speech), speech + loud_speech)

    def test_429_is_a_quota_error_and_the_run_fails(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        Fake.gemini_status = 429
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = jobs.run(cfg, "podcast")
        self.assertFalse(ok)
        conn = connect(cfg.db)
        r = conn.execute("SELECT ok, info FROM runs WHERE job='podcast' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(r["ok"], 0)
        self.assertIn("quota", r["info"])
        self.assertNotIn(GEMINI_KEY, r["info"])
        self.assertFalse(podcast.audio_path(cfg, DAY).exists())
        self.assertEqual(list(podcast.audio_path(cfg, DAY).parent.glob("*")), [])

    def test_no_gemini_key_means_off_and_no_call(self):
        cfg = self.cfg()
        seed(cfg)
        with mock.patch.dict(os.environ, {"KOKPIT_GEMINI_API_KEY": ""}):
            on, why = podcast.status(cfg)
            self.assertFalse(on)
            self.assertIn("KOKPIT_GEMINI_API_KEY", why)
            n, info, warnings = podcast.job(cfg)
        self.assertEqual(n, 0)
        self.assertIn("skipped", info["status"])
        self.assertTrue(any("KOKPIT_GEMINI_API_KEY" in w for w in warnings))
        self.assertEqual((Fake.gemini, Fake.model_prompts), ([], []))

    def test_openai_path_one_request_per_line_with_voice(self):
        cfg = self.cfg(provider="openai_compatible")
        seed(cfg)
        Fake.model_answers = list(TWO)
        row = podcast.make(cfg)
        self.assertEqual(len(Fake.speech), 5)
        self.assertEqual([s["voice"] for s in Fake.speech], ["VoiceOne", "VoiceTwo", "VoiceOne", "VoiceTwo", "VoiceOne"])
        self.assertEqual({s["format"] for s in Fake.speech}, {"pcm"})
        self.assertEqual(Fake.speech[0]["auth"], "Bearer " + TTS_KEY)
        self.assertEqual(Fake.gemini, [])
        self.assertEqual(row["engine"], "openai_compatible:tts-fake")

    def test_intro_from_today_is_spoken_first_with_voice_a(self):
        cfg = self.cfg(intro=True)
        seed(cfg)
        Fake.model_answers = list(TWO)
        today = {"sections": {"weather": {"status": "ok", "days": [{"date": "2026-10-30", "text": "sunny", "min": 8.2, "max": 17.6}]},
                              "calendar": {"status": "connected", "today": [{"start": "2026-10-30T09:30", "all_day": False, "title": "Team call"}]}}}
        with mock.patch("kokpit.modules.today.api.view", return_value=today):
            row = podcast.make(cfg, now=datetime(2026, 10, 30, 7, 0))
        parts = Fake.gemini[0]["body"]["contents"][0]["parts"]
        self.assertEqual(len(Fake.gemini), 1)
        self.assertEqual(len(parts), 6)  # intro + 2 + 3 lines in the one planned request
        intro = parts[:1]
        self.assertEqual(intro[0]["speechMetadata"]["speaker"], "A")
        self.assertIn("Friday, 30 October", intro[0]["text"])
        self.assertIn("between 8 and 18 degrees", intro[0]["text"])
        self.assertIn("first is at 09:30: Team call", intro[0]["text"])
        self.assertEqual(row["note"], "")

    def test_intro_without_data_is_skipped_with_a_note(self):
        cfg = self.cfg(intro=True)
        seed(cfg)
        Fake.model_answers = list(TWO)
        with mock.patch("kokpit.modules.today.api.view", return_value={"sections": {}}):
            row = podcast.make(cfg)
        self.assertEqual(len(Fake.gemini), 1)
        self.assertEqual(len(Fake.gemini[0]["body"]["contents"][0]["parts"]), 5)
        self.assertIn("intro skipped", row["note"])


class PlanTests(unittest.TestCase):
    @staticmethod
    def segs(sizes):
        return [[f"{i}.{j}" for j in range(n)] for i, n in enumerate(sizes)]

    def check(self, parts, max_requests, limit):
        self.assertLessEqual(len(parts), max_requests)
        self.assertTrue(all(0 < len(p) <= limit for p in parts))
        self.assertLessEqual(max(map(len, parts)) - min(map(len, parts)), 1)

    def test_real_episode_with_intro_is_exactly_ten_requests(self):
        segs = self.segs([18, 30, 32, 6, 15, 12])
        segs[0] = ["intro"] + segs[0]
        parts, dropped = voices.plan_requests(segs, 10, 12, 16)
        self.assertEqual((len(parts), dropped), (10, 0))
        self.check(parts, 10, 12)
        self.assertEqual(parts[0][0], "intro")
        self.assertEqual([x for p in parts for x in p], [x for s in segs for x in s])

    def test_sixty_lines_need_five_requests(self):
        segs = self.segs([20, 25, 15])
        parts, dropped = voices.plan_requests(segs, 10, 12, 16)
        self.assertEqual(([len(p) for p in parts], dropped), ([12] * 5, 0))
        self.assertEqual([x for p in parts for x in p], [x for s in segs for x in s])

    def test_one_hundred_fifty_lines_stretch_to_sixteen(self):
        parts, dropped = voices.plan_requests(self.segs([50, 50, 50]), 10, 12, 16)
        self.assertEqual((len(parts), dropped), (10, 0))
        self.check(parts, 10, 16)
        self.assertEqual(sum(map(len, parts)), 150)

    def test_two_hundred_lines_are_trimmed_to_one_hundred_sixty(self):
        segs = self.segs([80, 50, 40, 30])
        parts, dropped = voices.plan_requests(segs, 10, 12, 16)
        self.assertEqual((len(parts), dropped), (10, 40))
        self.assertEqual([len(p) for p in parts], [16] * 10)
        flat = [x for p in parts for x in p]
        for s in segs:
            self.assertIn(s[0], flat)
            self.assertIn(s[-1], flat)
        kept = set(flat)
        self.assertEqual(flat, [x for s in segs for x in s if x in kept])
        self.assertEqual(len(kept), 160)

    def test_trim_takes_from_the_longest_segment_first(self):
        segs = self.segs([30, 5])
        parts, dropped = voices.plan_requests(segs, 2, 12, 16)
        self.assertEqual(dropped, 3)
        flat = [x for p in parts for x in p]
        self.assertEqual([x for x in flat if x.startswith("1.")], segs[1])
        self.assertNotIn("0.28", flat)
        self.assertIn("0.29", flat)

    def test_edges_never_break_the_limits(self):
        self.assertEqual(voices.plan_requests([], 10, 12, 16), ([], 0))
        self.assertEqual(voices.plan_requests([[], []], 10, 12, 16), ([], 0))
        self.assertEqual(voices.plan_requests([["a"]], 10, 12, 16), ([["a"]], 0))
        for sizes in ([1] * 30, [2] * 20, [1, 1, 1], [100], [3, 1, 7, 1, 2]):
            for m in (1, 2, 3, 10):
                parts, dropped = voices.plan_requests(self.segs(sizes), m, 2, 2)
                self.assertEqual(sum(map(len, parts)) + dropped, sum(sizes))
                self.check(parts, m, 2)

    def test_config_defaults_and_bounds(self):
        d = cfgmod._podcast({})
        self.assertEqual((d["max_requests"], d["max_turns_per_request"], d["max_turns_stretch"]), (10, 12, 16))
        self.assertEqual(cfgmod._podcast({"max_requests": 0})["max_requests"], 1)
        self.assertEqual(cfgmod._podcast({"max_requests": 500})["max_requests"], 100)
        self.assertEqual(cfgmod._podcast({"max_requests": "x"})["max_requests"], 10)
        self.assertEqual(cfgmod._podcast({"max_turns_stretch": 5})["max_turns_stretch"], 12)
        self.assertEqual(cfgmod._podcast({"max_turns_stretch": 99})["max_turns_stretch"], 40)
        self.assertEqual(cfgmod._podcast({"max_turns_per_request": 20})["max_turns_stretch"], 20)


class ServeTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg_ = self.cfg()
        seed(self.cfg_)
        Fake.model_answers = list(TWO)
        podcast.make(self.cfg_)
        self.body = podcast.audio_path(self.cfg_, DAY).read_bytes()
        self.web = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg_))
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.web.server_port}"

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        super().tearDown()

    def get(self, path, rng=None):
        req = urllib.request.Request(self.url + path, headers={"Range": rng} if rng else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_api_has_podcast_field(self):
        v = api.view(self.cfg_)
        self.assertEqual(v["podcast"]["url"], f"/briefing/podcast/{DAY}.wav")
        self.assertEqual(v["podcast"]["engine"], "gemini:tts-model-x")
        self.assertIn("created", v["podcast"])

    def test_no_range_is_200_full(self):
        code, h, body = self.get(f"/briefing/podcast/{DAY}.wav")
        self.assertEqual(code, 200)
        self.assertEqual(body, self.body)
        self.assertEqual(h["Accept-Ranges"], "bytes")
        self.assertEqual(h["Content-Type"], "audio/wav")

    def test_first_hundred_bytes(self):
        code, h, body = self.get(f"/briefing/podcast/{DAY}.wav", "bytes=0-99")
        self.assertEqual(code, 206)
        self.assertEqual(body, self.body[:100])
        self.assertEqual(h["Content-Range"], f"bytes 0-99/{len(self.body)}")

    def test_open_ended_range(self):
        code, h, body = self.get(f"/briefing/podcast/{DAY}.wav", "bytes=100-")
        self.assertEqual(code, 206)
        self.assertEqual(body, self.body[100:])

    def test_range_outside_is_416(self):
        code, h, _ = self.get(f"/briefing/podcast/{DAY}.wav", "bytes=999999-")
        self.assertEqual(code, 416)
        self.assertEqual(h["Content-Range"], f"bytes */{len(self.body)}")

    def test_bad_day_or_extension_is_404(self):
        for p in ("/briefing/podcast/2026-1-29.wav", "/briefing/podcast/..%2Ft.db.wav", f"/briefing/podcast/{DAY}.ogg",
                  "/briefing/podcast/2026-10-28.wav"):
            self.assertEqual(self.get(p)[0], 404, p)


class JobTests(Base):
    def test_disabled_is_a_skipped_run(self):
        cfg = self.cfg(enabled=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(jobs.run(cfg, "podcast"))
        conn = connect(cfg.db)
        r = conn.execute("SELECT ok, info FROM runs WHERE job='podcast'").fetchone()
        conn.close()
        self.assertEqual(json.loads(r["info"])["status"], "skipped (disabled)")
        self.assertEqual((Fake.gemini, Fake.model_prompts), ([], []))

    def test_success_records_run_and_row(self):
        cfg = self.cfg()
        seed(cfg)
        Fake.model_answers = list(TWO)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(jobs.run(cfg, "podcast"))
        conn = connect(cfg.db)
        r = conn.execute("SELECT ok, rows, info FROM runs WHERE job='podcast'").fetchone()
        row = conn.execute("SELECT * FROM briefing_podcast").fetchone()
        conn.close()
        self.assertEqual((r["ok"], r["rows"]), (1, 1))
        self.assertEqual(row["day"], DAY)

    def test_scheduled_after_briefing(self):
        cfg = self.cfg()
        e = {x.job: x for x in scheduler.entries_for(cfg)}
        self.assertEqual(e["podcast"].at, ("07:00",))
        self.assertGreater(e["podcast"].at[0], e["briefing"].at[0])

    def test_bot_gets_one_line(self):
        cfg = self.cfg(bot=True)
        seed(cfg)
        Fake.model_answers = list(TWO)
        sent = []
        with mock.patch("kokpit.modules.briefing.podcast.notify", side_effect=lambda c, t, o=None: sent.append(t)):
            podcast.job(cfg)
        self.assertEqual(len(sent), 1)
        self.assertRegex(sent[0], rf"^Podcast ready \(\d+ min\): /briefing/podcast/{DAY}\.wav$")
        Fake.gemini_status = 429
        podcast.script_path(cfg, DAY).unlink()
        Fake.model_answers = list(TWO)
        with mock.patch("kokpit.modules.briefing.podcast.notify", side_effect=lambda c, t, o=None: sent.append(t)):
            with self.assertRaises(podcast.PodcastError):
                podcast.job(cfg)
        self.assertTrue(sent[1].startswith("Podcast failed: Gemini quota"))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_off_and_bounded(self):
        d = cfgmod._podcast({})
        self.assertFalse(d["enabled"])
        self.assertEqual((d["minutes"], d["language"], d["tts_provider"]), (20, "en", "gemini"))
        self.assertEqual(d["speakers"], {"A": "Kore", "B": "Puck"})
        self.assertEqual(d["listener_name"], "")
        self.assertEqual(cfgmod._podcast({"minutes": 99})["minutes"], 40)
        self.assertEqual(cfgmod._podcast({"minutes": 1})["minutes"], 5)
        self.assertEqual(cfgmod._podcast({"speakers": {"A": "bad voice!"}})["speakers"]["A"], "Kore")


class PrivacyScanTests(unittest.TestCase):
    def test_added_files_carry_no_personal_values(self):
        root = Path(__file__).resolve().parent.parent
        rx = re.compile(r"sahin|cremona|beynim|/home/|tailscale|notebooklm|aistudio|100\.", re.I)
        for rel in ("kokpit/modules/briefing/podcast.py", "kokpit/modules/briefing/voices.py"):
            self.assertIsNone(rx.search((root / rel).read_text(encoding="utf-8")), rel)


if __name__ == "__main__":
    unittest.main()
