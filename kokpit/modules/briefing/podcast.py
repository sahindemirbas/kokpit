"""Two-voice podcast of the day's digest: the model writes it segment by segment, a TTS provider reads it.

Off by default (``[briefing.podcast] enabled``). The text needs ``[model.uses] podcast = true`` and goes
through the shared model connection (``modules.model.client``); this module has no model client of its own.

Speaker A asks and keeps the practical angle, speaker B explains and weighs pros and cons. Neither has a
name or introduces itself; the listener is addressed as a single "you". The listener's first name, when
set, is used only in the opening greeting and the closing goodbye.

Files, all under the data folder next to the database:
- ``podcast/<day>/script.json``: the text and its fingerprint; same fingerprint, no new model call.
- ``audio/podcast-<day>.wav`` (and ``.mp3`` when ffmpeg is on the PATH): the episode.
A failed run leaves no audio file and no table row behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ...config import Config
from ...db import connect
from ..health import store as health_store
from ..model import client
from . import collect, tts, voices

DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
WORDS_PER_MINUTE = 130
MIN_SEGMENT_WORDS = 250
MAX_LINE_SENTENCES = 4
KEEP_PREVIOUS = 3  # last lines of the previous segment handed to the next prompt

# Phrases a two-host show for one listener must not use. Checked case-insensitively on every line.
FORBID = (
    r"\bhello,? everyone\b", r"\bhi,? everyone\b", r"\bhey,? everyone\b", r"\bwelcome,? everyone\b",
    r"\bdear listeners?\b", r"\bladies and gentlemen\b", r"\byou guys\b", r"\bfolks\b",
    r"\bmy name is\b", r"\bI(?:'m| am) (?:host |speaker )?[AB]\b", r"\b(?:host|speaker) [AB]\b",
)
_FORBID = [re.compile(p, re.I) for p in FORBID]

SYSTEM = """You write one segment of a two-host audio podcast for a single listener. Rules:
- Use only the facts given in the input. Do not invent numbers, dates, names, versions or benchmark results; when something is not given, say it is not stated.
- Write for the ear: no lists, no brackets, no links, no markdown, no emoji. Write numbers as spoken words where it helps, for example "209 million", "30 percent".
- Two speakers, labelled A and B. A asks questions and keeps asking what it means in practice for the listener. B explains and weighs pros and cons.
- One line has at most four sentences. Alternate speakers naturally.
- The hosts have no names and never introduce themselves. Never say "I am A" or "my name is".
- Talk to one listener as "you". No crowd greetings such as "hello everyone" or "dear listeners".
- Answer with JSON only: [{"lines": [{"who": "A", "text": "..."}, {"who": "B", "text": "..."}]}]"""


class PodcastError(RuntimeError):
    """The episode could not be made; nothing was written."""


class PodcastDisabled(PodcastError):
    """The podcast is off or its speech provider is not configured. No network call was made."""


# ---------- paths and settings ----------

def settings(cfg: Config) -> dict:
    return cfg.briefing["podcast"]


def data_dir(cfg: Config) -> Path:
    return cfg.db.parent


def script_path(cfg: Config, day: str) -> Path:
    return data_dir(cfg) / "podcast" / day / "script.json"


def audio_path(cfg: Config, day: str, ext: str = "wav") -> Path:
    return data_dir(cfg) / "audio" / f"podcast-{day}.{ext}"


def gemini_key(cfg: Config) -> str:
    return os.environ.get(settings(cfg)["gemini_key_env"], "").strip()


def status(cfg: Config) -> tuple[bool, str]:
    """(on, reason). Off when the switch is off, the Briefing module is off, or speech has no key."""
    p = settings(cfg)
    if not p["enabled"]:
        return False, "disabled"
    if not cfg.modules.get("briefing", False):
        return False, "briefing module is off"
    if p["tts_provider"] == "gemini":
        if not gemini_key(cfg):
            return False, f"no key in the environment variable {p['gemini_key_env']}"
        return True, ""
    try:
        tts.check(cfg)
    except RuntimeError as exc:
        return False, f"[briefing.tts] {exc}"
    return True, ""


def enabled(cfg: Config) -> bool:
    return status(cfg)[0]


# ---------- text ----------

_URL = re.compile(r"https?://\S+|www\.\S+")
_MD = re.compile(r"[*_`#>\[\]{}()|~]+")
_SCALE = re.compile(r"(\d+(?:[.,]\d+)?)\s?([KkMB]|bn)\b")
_PCT = re.compile(r"%\s?(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s?%")
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]")
_SCALE_WORD = {"K": "thousand", "k": "thousand", "M": "million", "B": "billion", "bn": "billion"}


def clean_text(text: str) -> str:
    """Makes a line speakable: no links, markdown or emoji; "209M" -> "209 million", "%30" -> "30 percent"."""
    t = _URL.sub("", str(text))
    t = _EMOJI.sub("", t)
    t = _PCT.sub(lambda m: f"{m.group(1) or m.group(2)} percent", t)
    t = _SCALE.sub(lambda m: f"{m.group(1)} {_SCALE_WORD[m.group(2)]}", t)
    t = _MD.sub(" ", t)
    t = t.replace("\u2014", ", ").replace("\u2013", " to ")
    return re.sub(r"\s+", " ", t).strip()


def forbidden(text: str) -> list[str]:
    return [m.group(0) for rx in _FORBID if (m := rx.search(text))]


def _drop_forbidden(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if not forbidden(s)).strip()


def _limit_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:MAX_LINE_SENTENCES]).strip()


def parse_lines(raw: str) -> list[dict]:
    """Model answer -> [{"who", "text"}]. Accepts the list form, one object, or a bare lines list."""
    raw = (raw or "").strip()
    s, e = raw.find("["), raw.rfind("]")
    o, oe = raw.find("{"), raw.rfind("}")
    data = None
    for a, b in ((s, e), (o, oe)):
        if a != -1 and b > a:
            try:
                data = json.loads(raw[a: b + 1])
                break
            except ValueError:
                continue
    if isinstance(data, dict):
        data = [data]
    out: list[dict] = []
    for seg in data if isinstance(data, list) else []:
        rows = seg.get("lines", []) if isinstance(seg, dict) and "lines" in seg else [seg]
        for ln in rows if isinstance(rows, list) else []:
            if isinstance(ln, dict) and ln.get("who") in ("A", "B") and isinstance(ln.get("text"), str):
                out.append({"who": ln["who"], "text": ln["text"]})
    return out


def segments(items: list[dict]) -> list[dict]:
    """One segment per Briefing topic, in digest order."""
    out: dict[str, dict] = {}
    for it in items:
        out.setdefault(it["topic"], {"topic": it["topic"], "items": []})["items"].append(it)
    return list(out.values())


def word_targets(cfg: Config, segs: list[dict]) -> list[int]:
    total = settings(cfg)["minutes"] * WORDS_PER_MINUTE
    n = sum(len(s["items"]) for s in segs) or 1
    return [max(MIN_SEGMENT_WORDS, round(total * len(s["items"]) / n)) for s in segs]


def _item_text(it: dict) -> str:
    extra = it.get("extra") or {}
    if isinstance(extra, str):
        extra = json.loads(extra or "{}")
    tags = it.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags or "[]")
    facts = {k: v for k, v in extra.items() if k != "personal_context" and isinstance(v, (int, float, str))}
    parts = [f"Title: {it['title']}"]
    if tags:
        parts.append("Tags: " + ", ".join(map(str, tags[:8])))
    if it.get("summary"):
        parts.append(f"Summary: {it['summary']}")
    if it.get("why"):
        parts.append(f"Why it may matter: {it['why']}")
    if it.get("project"):
        parts.append(f"Matches the listener's project: {it['project']}")
    if facts:
        parts.append("Measured facts: " + ", ".join(f"{k} {v}" for k, v in facts.items()))
    return "\n".join(parts)


def prompt(cfg: Config, seg: dict, index: int, count: int, words: int, previous: list[dict], context: str = "") -> str:
    p = settings(cfg)
    name = p["listener_name"]
    out = [f"Language of the text: {p['language']}.",
           f"Segment {index + 1} of {count}. Topic: {seg['topic']}. Aim for about {words} words."]
    if index == 0:
        out.append("Open the episode with one short, warm greeting to the listener"
                   + (f", using the first name {name} once." if name else ", without any name."))
    else:
        out.append("Do not greet again; continue the conversation with a natural transition.")
    if index == count - 1:
        out.append("Close the episode with a short goodbye to the listener"
                   + (f", using the first name {name} once." if name else ", without any name."))
    else:
        out.append("Do not say goodbye in this segment.")
    if previous:
        out.append("The previous segment ended with these lines, continue from there:\n"
                   + "\n".join(f"{ln['who']}: {ln['text']}" for ln in previous))
    if context:
        out.append("Background about the listener, use only where it truly fits:\n" + context)
    out.append("Items:\n\n" + "\n\n".join(_item_text(it) for it in seg["items"]))
    return "\n\n".join(out)


def _tidy(lines: list[dict]) -> list[dict]:
    out = []
    for ln in lines:
        t = _limit_sentences(clean_text(ln["text"]))
        if t:
            out.append({"who": ln["who"], "text": t})
    return out


def write_segment(cfg: Config, seg: dict, index: int, count: int, words: int, previous: list[dict],
                  context: str, complete) -> list[dict]:
    user = prompt(cfg, seg, index, count, words, previous, context)
    max_tokens = max(1024, words * 3)
    lines = _tidy(parse_lines(complete(SYSTEM, user, max_tokens)))
    bad = sorted({f for ln in lines for f in forbidden(ln["text"])})
    if bad:
        retry = user + "\n\nYour previous answer used phrases that are not allowed: " + "; ".join(bad) + ". Write it again without them."
        lines = _tidy(parse_lines(complete(SYSTEM, retry, max_tokens)))
        lines = [{"who": ln["who"], "text": _drop_forbidden(ln["text"])} if forbidden(ln["text"]) else ln for ln in lines]
        lines = [ln for ln in lines if ln["text"]]
    if not lines:
        raise PodcastError(f"segment '{seg['topic']}' came back empty")
    return lines


def fingerprint(cfg: Config, items: list[dict], context_fp: str = "") -> str:
    p = settings(cfg)
    payload = {"items": [[it.get("id"), it.get("url"), it.get("title")] for it in items], "context": context_fp,
               "language": p["language"], "minutes": p["minutes"], "listener": p["listener_name"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context_for(cfg: Config) -> tuple[str, str]:
    """(prompt text, fingerprint) of the personal context, or ("", "") when it is not allowed or empty."""
    if not (settings(cfg)["brain_context"] and cfg.modules.get("brain", False)):
        return "", ""
    from ..brain.context import personal_context
    try:
        pc = personal_context(cfg.vault)
    except Exception:
        return "", ""
    return (pc.prompt, pc.fingerprint) if pc.prompt else ("", "")


def _model(cfg: Config):
    def complete(system: str, user: str, max_tokens: int) -> str:
        return client.complete(cfg, "podcast", system, user, max_tokens=max_tokens)
    return complete


def script(cfg: Config, day: str | None, items: list[dict], context: tuple[str, str] = ("", ""), complete=None) -> dict:
    """Returns {"day", "fingerprint", "segments": [{"topic", "lines"}]}; cached in podcast/<day>/script.json."""
    if not items:
        raise PodcastError("no digest yet: run `kokpit run-job briefing`" if not day else f"no digest items for {day}")
    if not DAY.fullmatch(day or ""):
        raise PodcastError("day must be YYYY-MM-DD")
    text, ctx_fp = context
    fp = fingerprint(cfg, items, ctx_fp)
    path = script_path(cfg, day)
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp and cached.get("segments"):
                return cached
        except ValueError:
            pass
    if complete is None:
        client.check(cfg, "podcast")  # raises ModelDisabled before any call
        complete = _model(cfg)
    segs = segments(items)
    targets = word_targets(cfg, segs)
    out, previous = [], []
    for i, seg in enumerate(segs):
        lines = write_segment(cfg, seg, i, len(segs), targets[i], previous, text, complete)
        out.append({"topic": seg["topic"], "lines": lines})
        previous = lines[-KEEP_PREVIOUS:]
    result = {"day": day, "fingerprint": fp, "segments": out, "created": health_store.now_iso()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return result


def as_text(sc: dict) -> str:
    out = []
    for seg in sc["segments"]:
        out.append(f"## {seg['topic']}")
        out += [f"{ln['who']}: {ln['text']}" for ln in seg["lines"]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------- items and intro ----------

def load_items(cfg: Config, day: str | None = None) -> tuple[str | None, list[dict]]:
    """Digest rows of ``day`` (default: the newest day) as dicts, in digest order."""
    if not cfg.db.exists():
        return None, []
    conn = connect(cfg.db, readonly=True)
    try:
        try:
            t = collect.table_name(conn)
            day = day or conn.execute(f"SELECT MAX(day) FROM {t}").fetchone()[0]
            rows = conn.execute(f"SELECT * FROM {t} WHERE day=? ORDER BY project IS NULL, score DESC, title", (day,)).fetchall() if day else []
        except Exception:
            return day, []
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["extra"] = json.loads(d.get("extra") or "{}")
        out.append(d)
    return day, out


def intro_text(cfg: Config, now: datetime | None = None) -> tuple[str, str]:
    """(text, note). A short spoken intro from Today: date, weather, today's calendar. Template sentences, no model.

    Only English templates ship; for another language the intro is skipped and the note says why.
    """
    p = settings(cfg)
    if not p["intro"]:
        return "", ""
    if p["language"].split("-")[0] != "en":
        return "", f"intro skipped: no template for language {p['language']}"
    from ..today import api as today_api
    now = now or datetime.now()
    try:
        s = today_api.view(cfg, now).get("sections", {})
    except Exception:
        return "", "intro skipped: Today data could not be read"
    said = []
    w = s.get("weather") or {}
    wd = next((d for d in w.get("days", []) if d.get("date") == now.date().isoformat()), None)
    if w.get("status") in ("ok", "stale") and wd:
        t = f"The weather today: {wd.get('text') or 'no description'}"
        if wd.get("min") is not None and wd.get("max") is not None:
            t += f", between {round(wd['min'])} and {round(wd['max'])} degrees"
        if wd.get("rain_pct"):
            t += f", with a {wd['rain_pct']} percent chance of rain"
        said.append(t + ".")
    c = s.get("calendar") or {}
    if c.get("status") == "connected":
        evs = c.get("today", [])
        if not evs:
            said.append("Your calendar is clear today.")
        else:
            timed = sorted((m.group(0), e) for e in evs if not e.get("all_day")
                           and (m := re.search(r"\d{2}:\d{2}", str(e.get("start") or ""))))
            t = f"You have {len(evs)} {'event' if len(evs) == 1 else 'events'} today"
            if timed:
                first = clean_text(timed[0][1].get("title") or "")
                t += f"; the first is at {timed[0][0]}" + (f": {first}" if first else "")
            said.append(t + ".")
    if not said:
        return "", "intro skipped: no weather or calendar data"
    return " ".join([f"Good morning. Today is {now.strftime('%A, %d %B')}."] + said[:3]), ""


# ---------- speech, files and records ----------

def _speak(cfg: Config, lines: list[dict], opener=None) -> bytes:
    """Line by line through the ``[briefing.tts]`` server (openai_compatible); no request limit applies."""
    p = settings(cfg)
    return voices.openai_lines(cfg, lines, p["speakers"], p["openai_format"])


def _speak_gemini(cfg: Config, segments: list[list[dict]], opener=None) -> tuple[bytes, int]:
    """The whole episode in at most ``max_requests`` Gemini requests: (pcm, lines trimmed)."""
    p = settings(cfg)
    key = gemini_key(cfg)
    parts, dropped = voices.plan_requests(segments, p["max_requests"], p["max_turns_per_request"],
                                          p["max_turns_stretch"])
    chunks = [voices.trim_tail(voices.gemini_multi(part, p["speakers"], key, p["gemini_model"], p["gemini_base_url"],
                                                   p["timeout"], opener))
              for part in parts]
    return voices.join(chunks), dropped


def engine(cfg: Config) -> str:
    p = settings(cfg)
    if p["tts_provider"] == "gemini":
        return f"gemini:{p['gemini_model']}"
    return f"openai_compatible:{cfg.briefing['tts']['model']}"


def _mp3(wav: Path) -> Path | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    out = wav.with_suffix(".mp3")
    tmp = out.with_name(out.stem + ".part.mp3")
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "4", str(tmp)],
                       check=True, capture_output=True, timeout=600)
        tmp.replace(out)
        return out
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def ensure_schema(conn) -> None:
    collect.ensure_schema(conn)


def make(cfg: Config, day: str | None = None, complete=None, opener=None, now: datetime | None = None) -> dict:
    """Script (cached), speech, files and the table row. Raises PodcastError; leaves nothing half written."""
    on, why = status(cfg)
    if not on:
        raise PodcastDisabled(why)
    day, items = load_items(cfg, day)
    if not day or not items:
        raise PodcastError("no digest yet: run `kokpit run-job briefing`")
    sc = script(cfg, day, items, context_for(cfg), complete)
    note = []
    intro, intro_note = intro_text(cfg, now)
    if intro_note:
        note.append(intro_note)
    wav = audio_path(cfg, day, "wav")
    mp3 = audio_path(cfg, day, "mp3")
    tmp = wav.with_name(wav.stem + ".part.wav")
    wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        intro_line = [{"who": "A", "text": intro}] if intro else []
        if settings(cfg)["tts_provider"] == "gemini":
            segs = [list(seg["lines"]) for seg in sc["segments"]]
            if segs:
                segs[0] = intro_line + segs[0]
            else:
                segs = [intro_line]
            pcm, dropped = _speak_gemini(cfg, segs, opener)
            if dropped:
                note.append(f"trimmed {dropped} lines to fit {settings(cfg)['max_requests']} requests")
        else:
            pieces = []
            if intro_line:
                pieces.append(_speak(cfg, intro_line, opener))
            for seg in sc["segments"]:
                pieces.append(_speak(cfg, seg["lines"], opener))
            pcm = voices.join(pieces)
        secs = voices.write_wav(tmp, pcm)
        tmp.replace(wav)
    except voices.VoiceError as exc:
        tmp.unlink(missing_ok=True)
        raise PodcastError(str(exc)) from None
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    mp3.unlink(missing_ok=True)
    served = _mp3(wav) or wav
    row = {"day": day, "engine": engine(cfg), "file": served.name, "seconds": secs, "note": "; ".join(note),
           "fingerprint": sc["fingerprint"], "created": health_store.now_iso()}
    conn = connect(cfg.db)
    try:
        ensure_schema(conn)
        conn.execute("INSERT OR REPLACE INTO briefing_podcast(day, engine, file, seconds, note, fingerprint, created) "
                     "VALUES (:day,:engine,:file,:seconds,:note,:fingerprint,:created)", row)
        conn.commit()
    finally:
        conn.close()
    return row


def minutes_of(seconds: float) -> int:
    return max(1, round(float(seconds) / 60))


def link(cfg: Config, row: dict) -> str:
    ext = row["file"].rsplit(".", 1)[-1]
    return f"{settings(cfg)['link_base']}/briefing/podcast/{row['day']}.{ext}"


def notify(cfg: Config, text: str, opener=None) -> bool:
    """One bot line when the bot is on and configured; never raises."""
    if not cfg.modules.get("bot", False):
        return False
    from ..bot.runner import _client
    from ..bot.token import chat_id, missing
    if missing(cfg):
        return False
    try:
        _client(cfg, opener).send_message(chat_id(cfg), text)
        return True
    except Exception:
        return False


def job(cfg: Config, day: str | None = None, complete=None, opener=None, bot_opener=None, now=None):
    """Scheduled job. Off: a skipped run. Failure: one bot line, then the error goes to the run record."""
    on, why = status(cfg)
    if not on:
        return 0, {"status": f"skipped ({why})"}, ([] if why == "disabled" else [f"podcast: {why}"])
    try:
        row = make(cfg, day, complete, opener, now)
    except Exception as exc:
        notify(cfg, f"Podcast failed: {str(exc)[:160]}", bot_opener)
        raise
    notify(cfg, f"Podcast ready ({minutes_of(row['seconds'])} min): {link(cfg, row)}", bot_opener)
    return 1, {"day": row["day"], "file": row["file"], "seconds": row["seconds"], "engine": row["engine"],
               **({"note": row["note"]} if row["note"] else {})}, []


def info(cfg: Config, day: str | None = None) -> dict | None:
    """What the Briefing tab shows: {url, seconds, engine, created, minutes} or None."""
    if not cfg.db.exists():
        return None
    conn = connect(cfg.db, readonly=True)
    try:
        try:
            q = "SELECT * FROM briefing_podcast " + ("WHERE day=? " if day else "") + "ORDER BY day DESC LIMIT 1"
            r = conn.execute(q, (day,) if day else ()).fetchone()
        except Exception:
            return None
    finally:
        conn.close()
    if not r or not (audio_path(cfg, r["day"], "x").parent / r["file"]).is_file():
        return None
    ext = r["file"].rsplit(".", 1)[-1]
    return {"url": f"/briefing/podcast/{r['day']}.{ext}", "day": r["day"], "seconds": r["seconds"],
            "minutes": minutes_of(r["seconds"]), "engine": r["engine"], "created": r["created"]}


def file_for(cfg: Config, day: str, ext: str) -> Path | None:
    """The episode file for a validated day and extension, or None. No path joining with user input."""
    if not DAY.fullmatch(day or "") or ext not in ("mp3", "wav"):
        return None
    p = audio_path(cfg, day, ext)
    return p if p.is_file() else None
