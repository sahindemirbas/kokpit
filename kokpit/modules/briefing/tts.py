"""Optional text to speech for the digest: any OpenAI-compatible ``POST <base_url>/audio/speech``.

Off by default. The key comes from the environment variable named by ``[briefing.tts] key_env``;
it is never written to the toml, the database or the screen. The only thing sent is the summary text.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from ...config import Config
from ...db import connect
from ..health import store

MAX_CHARS = 4000
FIELDS = "the digest summary text (titles and one-sentence summaries)"


def key(cfg: Config) -> str:
    return os.environ.get(cfg.briefing["tts"]["key_env"], "").strip()


def enabled(cfg: Config) -> bool:
    t = cfg.briefing["tts"]
    return t["provider"] == "openai_compatible" and bool(t["base_url"] and t["model"] and key(cfg))


def audio_path(cfg: Config, day: str) -> Path:
    return cfg.db.parent / "briefing" / f"digest-{day}.mp3"


def _record(cfg: Config, started: str, ok: bool, info: dict, warnings: list[str]) -> None:
    conn = connect(cfg.db)
    try:
        store.record(conn, "tts:briefing", started, ok, rows=1 if ok else 0, info=info, warnings=warnings)
    finally:
        conn.close()


def check(cfg: Config) -> str:
    """Returns the key, or raises RuntimeError saying what is missing. No network call."""
    t = cfg.briefing["tts"]
    if t["provider"] != "openai_compatible":
        raise RuntimeError("provider is none")
    if not (t["base_url"] and t["model"]):
        raise RuntimeError("[briefing.tts] base_url and model are not set")
    secret = key(cfg)
    if not secret:
        raise RuntimeError(f"no key in the environment variable {t['key_env']}")
    return secret


def request(cfg: Config, text: str, voice: str = "", fmt: str = "mp3") -> bytes:
    """One ``/audio/speech`` call; returns the raw audio. Errors keep the key out (callers scrub with ``key``)."""
    t = cfg.briefing["tts"]
    secret = check(cfg)
    body = {"model": t["model"], "input": text[:MAX_CHARS], "response_format": fmt}
    if voice or t["voice"]:
        body["voice"] = voice or t["voice"]
    req = urllib.request.Request(t["base_url"].rstrip("/") + "/audio/speech", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret})
    with urllib.request.urlopen(req, timeout=t["timeout"]) as r:
        audio = r.read()
    if not audio:
        raise ValueError("empty audio")
    return audio


def speak(cfg: Config, day: str, text: str) -> str:
    """Writes the audio file under the data folder and returns its file name. Raises RuntimeError with a key-free message."""
    t = cfg.briefing["tts"]
    secret = check(cfg)
    text = text[:MAX_CHARS]
    started = store.now_iso()
    info = {"declared_fields": FIELDS, "chars_sent": len(text), "model": t["model"]}
    try:
        audio = request(cfg, text)
        p = audio_path(cfg, day)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(audio)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {str(exc)[:120]}".replace(secret, "[key]")
        _record(cfg, started, False, {**info, "error": msg}, [f"tts: {msg}"])
        raise RuntimeError(msg) from None
    _record(cfg, started, True, {**info, "bytes": len(audio)}, [])
    return p.name
