"""Example job adapter: a template, no network, synthetic listings from a fictional company.

Copy this folder to adapters/jobs/<your-source>/ (or to a folder listed in
[career] adapter_dirs), rename NAME, and replace the body of collect() with code
that reads one public careers page. Enable it with [career] adapters = ["<folder>"].
"""
from __future__ import annotations

NAME = "example-careers"       # becomes the `source` of every record; unique slug
COMPANY = "Example Corp"
ENABLED_BY_DEFAULT = False     # informational: only [career] adapters in kokpit.toml enables an adapter


def collect(settings: dict) -> list[dict]:
    """Return job records (adapters/README.md). Raise on a broken page; return [] when it is genuinely empty.

    ``settings`` carries the profile targets: target_roles, locations, remote_pref, languages.
    """
    return [
        {
            "external_id": "ex-001",
            "title": "Data Analyst",
            "company": COMPANY,
            "location": "Example City",
            "remote": "hybrid",
            "url": "https://example.org/careers/ex-001",
            "posted_at": None,                     # unknown stays null, never invented
            "description": "Synthetic listing. SQL and Python for reporting. English required.",
            "language": "en",
        },
        {
            "external_id": "ex-002",
            "title": "Senior Data Engineer",
            "company": COMPANY,
            "location": "Example City",
            "remote": "remote",
            "url": "https://example.org/careers/ex-002",
            "posted_at": "2026-01-15",
            "description": "Synthetic listing. Airflow, dbt and a warehouse. 5+ years of experience.",
            "language": "en",
        },
    ]
