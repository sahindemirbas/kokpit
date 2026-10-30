"""Model id normalisation and session hashing."""
from __future__ import annotations

import hashlib
import re

DATE_SUFFIX = re.compile(r"[-@_](?:20\d{2}-?\d{2}-?\d{2}|\d{8})$")
VARIANTS = ("thinking", "minimal", "low", "medium", "high", "xhigh", "max")


def normalize_model(raw: str | None) -> str:
    """'vendor/Model-4-5-20250101-high' -> 'model-4-5'. Empty input -> 'unknown'."""
    m = (raw or "").strip().lower()
    if not m:
        return "unknown"
    m = m.rsplit("/", 1)[-1]
    for _ in range(4):  # date and variant suffixes can stack in any order
        before = m
        m = DATE_SUFFIX.sub("", m)
        for v in VARIANTS:
            if m.endswith("-" + v):
                m = m[: -len(v) - 1]
        if m == before:
            break
    return m or "unknown"


def session_hash(session_id: str) -> str:
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:12]


def alt_forms(model: str) -> list[str]:
    """Spelling variants used by price lists: 4-5 vs 4.5."""
    dots = re.sub(r"(\d)-(\d)", r"\1.\2", model)
    dashes = re.sub(r"(\d)\.(\d)", r"\1-\2", model)
    return [x for x in dict.fromkeys([model, dots, dashes])]
