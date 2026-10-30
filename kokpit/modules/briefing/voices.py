"""Speech for the two-voice podcast: raw PCM in, PCM out, one wav file at the end.

Two providers:
- ``gemini_multi``: one Gemini ``generateContent`` request per chunk of lines, every line its own
  part with ``speechMetadata.speaker`` and a ``multiSpeakerVoiceConfig``. The answer is base64
  PCM, 24 kHz, signed 16 bit little endian, mono.
- ``openai_lines``: the existing ``[briefing.tts]`` client, one request per line, the voice of
  each line taken from ``[briefing.podcast] speakers``. Slower, but needs no Gemini key.

Keys come from the environment only and are scrubbed out of every error message.
"""
from __future__ import annotations

import array
import base64
import io
import json
import math
import sys
import urllib.error
import urllib.request
import wave

from ...config import Config
from . import tts

RATE = 24000          # Hz, Gemini TTS output
WIDTH = 2             # bytes per sample (s16le)
GAP = 0.35            # seconds of silence between chunks and lines
# trim_tail thresholds: fixed on purpose, covered by tests
TAIL_LOOK = 1.5       # seconds at the end that are inspected
FRAME = 0.02          # seconds per analysis frame
QUIET = 600           # frame peak below this counts as silence
LOUD = 29000          # frame peak at or above this counts as a full-scale burst
MIN_SILENCE = 0.2     # seconds of silence needed before the burst
MAX_BURST = 0.6       # a longer loud stretch is speech, not a glitch


class VoiceError(RuntimeError):
    """A speech request failed. The message never carries a key."""


def silence(seconds: float) -> bytes:
    return b"\x00" * (int(round(RATE * seconds)) * WIDTH)


def _samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % WIDTH])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def trim_tail(pcm: bytes) -> bytes:
    """Cuts a short full-scale burst that follows silence in the last 1.5 s; clean audio is returned unchanged."""
    s = _samples(pcm)
    step = max(1, int(RATE * FRAME))
    start = max(0, len(s) - int(RATE * TAIL_LOOK))
    frames = []  # (offset, peak)
    for off in range(start, len(s), step):
        chunk = s[off: off + step]
        frames.append((off, max((abs(v) for v in chunk), default=0)))
    loud = [i for i, (_, p) in enumerate(frames) if p >= LOUD]
    if not loud:
        return pcm
    first = loud[0]
    # the burst is the non-silent stretch around the first loud frame
    b0 = first
    while b0 > 0 and frames[b0 - 1][1] >= QUIET:
        b0 -= 1
    b1 = first
    while b1 + 1 < len(frames) and frames[b1 + 1][1] >= QUIET:
        b1 += 1
    if any(i > b1 for i in loud):  # more than one burst: leave it alone
        return pcm
    if (b1 - b0 + 1) * FRAME > MAX_BURST + 1e-9:
        return pcm
    quiet = 0
    i = b0 - 1
    while i >= 0 and frames[i][1] < QUIET:
        quiet += 1
        i -= 1
    if quiet * FRAME < MIN_SILENCE - 1e-9:
        return pcm
    # nothing but silence may follow the burst
    if any(p >= QUIET for _, p in frames[b1 + 1:]):
        return pcm
    return pcm[: frames[b0][0] * WIDTH]


def split_turns(lines: list, n: int) -> list[list]:
    """Splits into near-equal chunks of at most ``n`` lines: 13 lines with n=12 gives 7 + 6."""
    if len(lines) <= n:
        return [list(lines)] if lines else []
    k = math.ceil(len(lines) / n)
    size = math.ceil(len(lines) / k)
    return [list(lines[i: i + size]) for i in range(0, len(lines), size)]


def join(chunks: list[bytes], gap: float = GAP) -> bytes:
    out = bytearray()
    for i, c in enumerate(chunks):
        if i:
            out += silence(gap)
        out += c
    return bytes(out)


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[key]") if secret else text


def gemini_multi(lines: list[dict], voices: dict, key: str, model: str, base_url: str,
                 timeout: int = 400, opener=None) -> bytes:
    """One request for ``lines`` ([{"who": "A"|"B", "text": str}]); returns PCM 24 kHz s16le mono."""
    if not key:
        raise VoiceError("no Gemini key in the environment")
    speakers = ["A", "B"]  # both always declared: a chunk with one speaker is still a two-speaker request
    body = {"contents": [{"parts": [{"text": ln["text"], "speechMetadata": {"speaker": ln["who"]}} for ln in lines]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [{"speaker": s, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voices[s]}}}
                                        for s in speakers]}}}}
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _scrub(exc.read().decode("utf-8", "replace")[:200], key)
        if exc.code == 429:
            raise VoiceError(f"Gemini quota reached (HTTP 429): {detail}") from None
        raise VoiceError(f"Gemini HTTP {exc.code}: {detail}") from None
    except Exception as exc:
        raise VoiceError(_scrub(f"Gemini {type(exc).__name__}: {str(exc)[:200]}", key)) from None
    try:
        parts = data["candidates"][0]["content"]["parts"]
        pcm = base64.b64decode(next(p["inlineData"]["data"] for p in parts if "inlineData" in p))
    except Exception:
        raise VoiceError("Gemini answer has no audio") from None
    if not pcm:
        raise VoiceError("Gemini answer has no audio")
    return pcm


def _wav_to_pcm(raw: bytes) -> bytes:
    with wave.open(io.BytesIO(raw), "rb") as w:
        if w.getsampwidth() != WIDTH or w.getnchannels() != 1:
            raise VoiceError("wav from the TTS server is not 16 bit mono")
        if w.getframerate() != RATE:
            raise VoiceError(f"wav from the TTS server is {w.getframerate()} Hz, expected {RATE}")
        return w.readframes(w.getnframes())


def openai_lines(cfg: Config, lines: list[dict], voices: dict, fmt: str = "pcm") -> bytes:
    """One ``/audio/speech`` request per line through ``tts.request``; ``pcm`` is used raw, a ``wav`` header is dropped."""
    secret = tts.key(cfg)
    out = []
    for ln in lines:
        try:
            raw = tts.request(cfg, ln["text"], voice=voices[ln["who"]], fmt=fmt)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise VoiceError(f"TTS quota reached (HTTP 429): {_scrub(str(exc)[:200], secret)}") from None
            raise VoiceError(_scrub(f"TTS HTTP {exc.code}: {str(exc)[:200]}", secret)) from None
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(_scrub(f"TTS {type(exc).__name__}: {str(exc)[:200]}", secret)) from None
        out.append(_wav_to_pcm(raw) if fmt == "wav" or raw[:4] == b"RIFF" else raw)
    return join(out)


def write_wav(path, pcm: bytes) -> float:
    """Writes 24 kHz s16le mono; returns the length in seconds read back from the header."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(WIDTH)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return seconds(path)


def seconds(path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 2)
