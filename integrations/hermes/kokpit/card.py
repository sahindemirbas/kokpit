"""Opt-in model session card (card_model): decisions and next steps quoted from the user's own messages.

The rule card stays the default. With card_model on and a host ctx.llm, the model sees at most
SEND_MAX characters of this session's recent user messages and must quote each line's source.
A line is kept only when its quote (QUOTE_MIN to QUOTE_MAX characters) appears word for word in
the text that was sent; every other line is rejected and counted. Any failure falls back to the
rule card and reports only the exception type.
"""
from __future__ import annotations

from . import contract as c

SEND_MAX = 6000
QUOTE_MIN, QUOTE_MAX = 8, 200
MAX_EACH = 3
MSG_KEEP = 24        # user messages kept per session, in memory only
MSG_MAX = 2000       # characters kept per message

SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {"type": "array", "maxItems": MAX_EACH, "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["text", "quote"]}},
        "next": {"type": "array", "maxItems": MAX_EACH, "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["text", "quote"]}},
    },
    "required": ["decisions", "next"],
}

INSTRUCTIONS = ("From these user messages of one work session, list at most 3 decisions the user made and at "
                "most 3 next steps. Each item needs a short text and a quote copied exactly, word for word, "
                "from the messages (8 to 200 characters). If there is nothing clear, return empty lists. "
                "No invented facts.")


def collect(messages) -> str:
    """The most recent messages, newest kept first when cutting, joined in order, at most SEND_MAX chars."""
    out, size = [], 0
    for m in reversed(list(messages)):
        m = m.strip()
        if not m:
            continue
        if size + len(m) + 2 > SEND_MAX:
            room = SEND_MAX - size - 2
            if room >= QUOTE_MIN and not out:
                out.append(m[-room:])
            break
        out.append(m)
        size += len(m) + 2
    return "\n\n".join(reversed(out))[:SEND_MAX]


def check(parsed, blob: str) -> tuple[dict, int]:
    """-> ({decisions: [...], next: [...]}, rejected count). Quotes must be verbatim substrings of blob."""
    kept, rejected = {"decisions": [], "next": []}, 0
    if not isinstance(parsed, dict):
        return kept, 1
    for key in kept:
        items = parsed.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if len(kept[key]) >= MAX_EACH:
                rejected += 1
                continue
            text = it.get("text") if isinstance(it, dict) else None
            quote = it.get("quote") if isinstance(it, dict) else None
            if (not isinstance(text, str) or not isinstance(quote, str) or not text.strip()
                    or not QUOTE_MIN <= len(quote.strip()) <= QUOTE_MAX or quote.strip() not in blob):
                rejected += 1
                continue
            kept[key].append({"text": c.clean(text, 140), "quote": c.clean(quote.strip(), 90)})
    return kept, rejected


def model_lines(llm, messages) -> tuple[list[str], dict]:
    """-> (card lines, info). Empty lines with info["model"] = "failed (...)" or "unavailable" means: use rules."""
    if llm is None or not hasattr(llm, "complete_structured"):
        return [], {"model": "unavailable"}
    blob = collect(messages)
    if not blob:
        return [], {"model": "on", "kept": 0, "rejected": 0, "sent": 0}
    try:
        res = llm.complete_structured(instructions=INSTRUCTIONS, input=[{"type": "text", "text": blob}],
                                      json_schema=SCHEMA, purpose="kokpit.session_card", temperature=0.0,
                                      max_tokens=600)
        parsed = getattr(res, "parsed", None)
    except Exception as e:
        return [], {"model": f"failed ({type(e).__name__})"}
    kept, rejected = check(parsed, blob)
    lines = [f"- Decision: {d['text']} (\"{d['quote']}\")" for d in kept["decisions"]]
    lines += [f"- Next: {n['text']} (\"{n['quote']}\")" for n in kept["next"]]
    return lines, {"model": "on", "kept": len(kept["decisions"]) + len(kept["next"]), "rejected": rejected,
                   "sent": len(blob)}
