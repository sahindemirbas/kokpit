"""Local CV text extraction and a small keyword proposal. The CV text never leaves the machine.

``extract_text`` uses ``pdftotext`` when it is on PATH (subprocess without a shell, with a timeout);
.txt/.md files are read directly. Everything that comes out is passed through ``scrub`` first, which
removes obvious e-mail addresses, phone numbers and URLs. Only the proposal (skills, languages) is
meant to be kept, and only after the user confirms it in the profile form.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

MAX_CHARS = 200_000
TIMEOUT_S = 30

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+", re.I)
PHONE = re.compile(r"(?<![\w.])(?:\+|00)?\d[\d ()./-]{7,}\d(?![\w])")

SKILL_TERMS = [
    "python", "sql", "r", "java", "javascript", "typescript", "c++", "c#", "go", "scala", "bash", "excel", "power bi", "tableau",
    "looker", "dbt", "airflow", "spark", "hadoop", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "machine learning",
    "deep learning", "nlp", "statistics", "forecasting", "time series", "a/b testing", "data visualization", "dashboards", "etl",
    "data modeling", "data warehouse", "postgresql", "mysql", "sqlite", "mongodb", "bigquery", "snowflake", "aws", "azure", "gcp",
    "docker", "kubernetes", "git", "linux", "rest api", "react", "django", "flask", "fastapi", "project management", "agile",
    "scrum", "crm", "sap", "reporting", "data analysis", "data engineering", "mlops", "llm",
]
LANGUAGE_NAMES = ["English", "French", "German", "Spanish", "Italian", "Portuguese", "Dutch", "Turkish", "Arabic", "Russian",
                  "Chinese", "Japanese", "Polish", "Romanian", "Swedish"]
LEVEL_WORDS = {"native": "native", "mother tongue": "native", "mothertongue": "native", "bilingual": "C2", "proficient": "C1",
               "fluent": "C1", "advanced": "C1", "upper intermediate": "B2", "intermediate": "B1", "conversational": "B1",
               "basic": "A2", "beginner": "A1", "elementary": "A2"}
CEFR = re.compile(r"\b([ABC][12])\b")


class CVError(RuntimeError):
    """A short reason why text could not be read from the file."""


def scrub(text: str) -> str:
    """Remove obvious e-mail addresses, phone numbers and links."""
    text = EMAIL.sub("", text)
    text = URL.sub("", text)
    text = PHONE.sub(lambda m: "" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0), text)
    return text


def extract_text(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise CVError(f"file not found: {p.name}")
    suffix = p.suffix.lower()
    if suffix in (".txt", ".md", ".text"):
        try:
            return scrub(p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS])
        except OSError as exc:
            raise CVError(f"cannot read {p.name}: {exc.__class__.__name__}") from None
    if suffix == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise CVError("pdftotext is not installed: paste the CV text or pass a .txt/.md file instead")
        try:
            res = subprocess.run([exe, "-layout", str(p), "-"], capture_output=True, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise CVError(f"pdftotext timed out after {TIMEOUT_S} s") from None
        if res.returncode != 0:
            raise CVError("pdftotext could not read the file")
        return scrub(res.stdout.decode("utf-8", errors="replace")[:MAX_CHARS])
    raise CVError("unsupported file type: use .pdf (needs pdftotext), .txt or .md")


_SEP = re.compile(r"[,;.\n|/]")


def language_window(text: str, m: re.Match) -> str:
    """Text next to a language name that can hold its level, cut at the nearest separators."""
    before = text[max(0, m.start() - 25): m.start()]
    after = text[m.end(): m.end() + 30]
    before = _SEP.split(before)[-1]
    after = _SEP.split(after)[0]
    return before + " " + m.group(0) + " " + after


def _has(term: str, low: str) -> bool:
    return re.search(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", low) is not None


def propose(text: str) -> dict:
    """Suggest skills and languages. A proposal only: the user confirms or edits it."""
    text = scrub(text or "")
    low = text.lower()
    skills = [t for t in SKILL_TERMS if _has(t, low)]
    langs = []
    for name in LANGUAGE_NAMES:
        for m in re.finditer(r"\b" + name + r"\b", text, re.I):
            window = language_window(text, m)
            level = ""
            cm = CEFR.search(window)
            if cm:
                level = cm.group(1)
            else:
                wl = window.lower()
                for word, lv in LEVEL_WORDS.items():
                    if word in wl:
                        level = lv
                        break
            langs.append({"language": name, "level": level})
            break
    return {"skills": skills, "languages": langs, "chars": len(text)}
