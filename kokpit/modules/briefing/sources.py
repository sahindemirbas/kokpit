"""Optional non-feed sources for the digest: GitHub repository search and Hugging Face models / daily papers.

Each source is off until configured under ``[briefing.github]`` or ``[briefing.huggingface]``.
Only numbers the API returns are kept in ``extra`` (measured facts); nothing is estimated or invented.
A GitHub token is optional and read from the environment variable named in ``token_env``.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from ... import fetch
from . import feeds

GITHUB = "GitHub"
HUGGINGFACE = "Hugging Face"


def _date(s) -> datetime | None:
    return feeds.parse_date(str(s)) if s else None


def _age_days(created: datetime | None, now: datetime) -> int | None:
    return max(0, int((now - created).total_seconds() // 86400)) if created else None


def _num(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def github_items(cfg_gh: dict, now: datetime, get=None) -> tuple[list[dict], list[str]]:
    """Newest repositories per query, sorted by stars. Facts: stars, age_days, lang, forks."""
    get = get or fetch.get
    if not cfg_gh["queries"]:
        return [], []
    since = (now - timedelta(days=cfg_gh["created_days"])).date().isoformat()
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get(cfg_gh["token_env"], "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out, warnings = [], []
    for q in cfg_gh["queries"]:
        full = f"{q} created:>={since}" + (f" language:{cfg_gh['language']}" if cfg_gh["language"] else "")
        url = (f"{cfg_gh['base_url']}/search/repositories?" +
               urllib.parse.urlencode({"q": full, "sort": "stars", "order": "desc", "per_page": cfg_gh["max_items"]}))
        try:
            data = json.loads(get(url, headers=headers))
        except Exception as exc:
            warnings.append(f"github / {q}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        for r in (data.get("items") or [])[: cfg_gh["max_items"]]:
            if not isinstance(r, dict) or not r.get("html_url") or not r.get("full_name"):
                continue
            created = _date(r.get("created_at"))
            extra = {"source_type": "github", "stars": _num(r.get("stargazers_count")),
                     "forks": _num(r.get("forks_count")), "age_days": _age_days(created, now),
                     "lang": str(r.get("language") or "")[:30]}
            out.append({"title": str(r["full_name"])[:200], "url": str(r["html_url"]),
                        "summary": feeds.strip_html(str(r.get("description") or "")),
                        "tags": [str(t)[:40] for t in (r.get("topics") or [])][:8],
                        "published": _date(r.get("pushed_at")) or created,
                        "extra": {k: v for k, v in extra.items() if v not in (None, "")}})
    return out, warnings


def _params(m: dict) -> int | None:
    st = m.get("safetensors")
    return _num(st.get("total")) if isinstance(st, dict) else None


def huggingface_items(cfg_hf: dict, now: datetime, get=None) -> tuple[list[dict], list[str]]:
    """Models by search term (facts: likes, downloads, parameter count when published) and daily papers (upvotes)."""
    get = get or fetch.get
    base, n = cfg_hf["base_url"], cfg_hf["max_items"]
    out, warnings = [], []
    for q in cfg_hf["models"]:
        params = [("search", q), ("sort", "likes"), ("direction", "-1"), ("limit", str(n))]
        params += [("expand[]", f) for f in ("likes", "downloads", "createdAt", "lastModified", "pipeline_tag", "tags", "safetensors")]
        try:
            data = json.loads(get(f"{base}/api/models?" + urllib.parse.urlencode(params)))
        except Exception as exc:
            warnings.append(f"huggingface / {q}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        for m in (data if isinstance(data, list) else [])[:n]:
            mid = str(m.get("id") or m.get("modelId") or "") if isinstance(m, dict) else ""
            if not mid:
                continue
            created = _date(m.get("createdAt"))
            kind = str(m.get("pipeline_tag") or "")[:40]
            p = _params(m)
            extra = {"source_type": "huggingface", "kind": kind, "likes": _num(m.get("likes")),
                     "downloads": _num(m.get("downloads")), "params": p,
                     "params_b": round(p / 1e9, 2) if p else None, "age_days": _age_days(created, now)}
            out.append({"title": mid[:200], "url": f"{base}/{mid}",
                        "summary": f"{mid} is a {kind} model on Hugging Face." if kind else f"{mid} is a model on Hugging Face.",
                        "tags": [str(t)[:40] for t in (m.get("tags") or []) if ":" not in str(t)][:8],
                        "published": _date(m.get("lastModified")) or created,
                        "extra": {k: v for k, v in extra.items() if v not in (None, "")}})
    if cfg_hf["papers"]:
        try:
            data = json.loads(get(f"{base}/api/daily_papers?limit={n}"))
        except Exception as exc:
            warnings.append(f"huggingface / papers: {type(exc).__name__}: {str(exc)[:100]}")
            data = []
        for d in (data if isinstance(data, list) else [])[:n]:
            if not isinstance(d, dict):
                continue
            p = d["paper"] if isinstance(d.get("paper"), dict) else {}
            pid, title = str(p.get("id") or ""), str(p.get("title") or d.get("title") or "").strip()
            if not pid or not title:
                continue
            created = _date(p.get("publishedAt") or d.get("publishedAt"))
            extra = {"source_type": "huggingface", "kind": "paper", "upvotes": _num(p.get("upvotes")),
                     "age_days": _age_days(created, now)}
            out.append({"title": title[:200], "url": f"{base}/papers/{pid}",
                        "summary": feeds.strip_html(str(p.get("summary") or "")), "tags": ["paper"],
                        "published": created, "extra": {k: v for k, v in extra.items() if v not in (None, "")}})
    return out, warnings


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
