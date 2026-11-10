"""Night pass: rule-based proposals over the vault (and Kokpit's database when there is one).

It never edits, moves, renames or deletes a note. Proposals are stored outside the notes, in the
Hermes plugin data dir (one JSON file per vault) or, without one, in <vault>/.kokpit/night.json.
The vault is written only when the user accepts a proposal, and then only through the append-only
capture writers (a task line in To-Do.md, or a new file in ideas/).

Kinds: todo, improve, link, idea, news. Every proposal quotes its sources word for word, and a
proposal whose quote cannot be found in its source file is dropped before it is stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import contract as c

KINDS = ("todo", "improve", "link", "idea", "news")
MAX_PER_KIND = {"todo": 12, "improve": 3, "link": 5, "idea": 5, "news": 10}
LINK_MIN_SHARED = 3          # strong words two notes must share before a link is proposed
MAX_NOTE_BYTES = 64 * 1024   # only the head of a huge note is read
MAX_NOTES = 5000
MAX_KEPT = 1000              # done/dismissed proposals remembered so they never reopen
AUTO_HOURS = 20
QUOTE_MAX = 200
TEXT_MAX = 220

WORD = re.compile(r"[a-zà-öø-ÿğışç]{4,}", re.I)
STOP = set("""
about above after again against also always among another anything around back because been before being below
between both cannot could didn does doing done down during each else even every from further have having here
hers herself himself into itself just know last less like made make many maybe more most much must myself need
needs never next none nothing often once only other ours ourselves over own same should show since some still
such than that their theirs them themselves then there these they this those though through today tomorrow
under until upon very want week weeks well were what when where whether which while whom whose will with within
without would year years yesterday your yours yourself yourselves open item items note notes todo task tasks
thread threads source sources where left demo check keep decide decision days time minutes hour hours thing
things part parts using used use good better best first second new old line lines file files page pages
kokpit vault inbox journal daily news idea ideas project projects
""".split())
# Turkish and Italian function words, pronouns, conjunctions and common verb forms. Without them a shared
# "için" or "della" looks like a common topic. WORD only matches 4+ letters, so shorter words are left out.
STOP |= set("""
acaba ait ama ancak arada arasında artık aslında ayrıca aynı bana bazı bazen belki bende beni benim beri
bile birçok biri birkaç birlikte biraz bizde bize bizi bizim böyle böylece bugün buna bunda bundan bunlar
bunları bunu bunun burada buradan çoğu çünkü daha dahil defa değil değildir diğer diye dolayı dün eğer
elbette etmek etti eder ediyor edildi edilir fakat falan gerek gerekli gibi göre güzel hala hâlâ halde hangi
hatta hemen hepsi herkes herşey hiçbir için içinde ilgili ilk işte kadar kendi kendini kendisi kere kez
lazım nasıl neden nedenle nerede niye olabilir olacak olan olarak oldu olduğu olduğunu olmak olmadı olmaz
olsa olsun olur onda ondan onlar onları onun orada oysa öyle önce önemli sadece sana sende seni senin sonra
sonunda şekilde şimdi şöyle şunu şunun şuan şey şeyi şeyler tamam tekrar tüm yani yapılacak yapıldı yapılır
yapmak yaptı yarın yeni yine yoksa zaten adet büyük küçük eski kötü
abbiamo adesso agli alla alle allo allora altra altre altri altro anche ancora anni anno avere avete aveva
avevo bambini bene cioè come cosa cose così dagli dalla dalle dallo degli della delle dello dentro dopo dove
ecco erano essere fanno fare faccio fatto fino giorni giorno ieri domani invece loro lungo meno mentre miei
molta molte molti molto nella nelle nello negli niente nostra nostro nulla ogni oggi oltre perché però
piuttosto più poco poi prima proprio quale quali quando quanto quasi quella quelle quelli quello questa
queste questi questo quindi sarà sarebbe sempre senza siamo siete sono sopra sotto stata state stati stato
stesso sugli sulla sulle sullo suoi tanto tutta tutte tutti tutto verso viene voglio volta vuole
""".split())
LINK_MAX_DF = 0.30  # a word in more than this share of the notes is too generic to suggest a link

# Month names (Turkish, Italian, English), keyed without diacritics: "29 Ekim", "31 Eki", "5 Şubat 2026".
_MONTHS = {}
for _n, _names in enumerate((
        "ocak oca gennaio gen january jan", "subat sub febbraio feb february",
        "mart mar marzo march", "nisan nis aprile apr april", "mayis may maggio mag",
        "haziran haz giugno giu june jun", "temmuz tem luglio lug july jul",
        "agustos agu agosto ago august aug", "eylul eyl settembre set september sept sep",
        "ekim eki ottobre ott october oct", "kasim kas novembre nov november",
        "aralik ara dicembre dic december dec"), start=1):
    for _w in _names.split():
        _MONTHS[_w] = _n
_ASCII = str.maketrans("çğıöşüâîûàèéìòù", "cgiosuaiuaeeiou")
_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b"
                   r"|\b(\d{1,2})\.?\s+([A-Za-zÇĞİÖŞÜçğıöşüâîûàèéìòù]{3,9})\.?(?:\s+(20\d\d)\b)?")


# ---------------------------------------------------------------- small helpers

def words(text: str) -> set[str]:
    return {w.casefold() for w in WORD.findall(text or "") if w.casefold() not in STOP}


def find_dates(text: str, today: date) -> list[date]:
    """Every date in text, in order: ISO (2026-10-29) or day + month name with an optional year
    ("29 Ekim", "(31 Eki)", "29 Ekim 2026", "5 marzo", "3 May"). Without a year the current year is used,
    or the previous one when that would put the date in the future."""
    out = []
    for m in _DATE.finditer(text or ""):
        try:
            if m.group(1):
                out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                continue
            month = _MONTHS.get(m.group(5).replace("İ", "i").casefold().translate(_ASCII))
            if month is None:
                continue
            day = int(m.group(4))
            if m.group(6):
                out.append(date(int(m.group(6)), month, day))
                continue
            d = date(today.year, month, day)
            out.append(d if d <= today else date(today.year - 1, month, day))
        except ValueError:  # 31 Şubat, 2026-13-01
            continue
    return out


def parse_date(text: str, today: date) -> date | None:
    """The first date in text (see find_dates), or None."""
    found = find_dates(text, today)
    return found[0] if found else None


def _id(kind: str, key: str, sources: list[dict]) -> str:
    key = json.dumps([kind, key, sorted((s.get("path") or s.get("item") or "", s["quote"]) for s in sources)],
                     ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _clip(s: str, n: int = QUOTE_MAX) -> str:
    """A verbatim prefix: still a substring of the source, so it can be checked word for word."""
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip()


def _read(p: Path) -> str:
    try:
        with open(p, "rb") as f:
            return f.read(MAX_NOTE_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def _prop(kind: str, text: str, reason: str, sources: list[dict], key: str = "", **extra) -> dict:
    """key: what makes this proposal itself (defaults to the reason); never a value that changes daily."""
    sources = [{**s, "quote": _clip(s["quote"])} for s in sources]
    return {"id": _id(kind, key or reason, sources), "kind": kind, "text": c.clean(text, TEXT_MAX), "reason": reason,
            "sources": sources, **{k: v for k, v in extra.items() if v not in (None, "")}}


# ---------------------------------------------------------------- reading the vault

def raw_threads(text: str, today: date | None = None) -> list[dict]:
    """Active threads with verbatim pieces: the heading, the open items as written, a date and the source link.

    Splits blocks the same way as contract.parse_threads (its markers, _cut_at, OWNER and EMPTY_VALUES), so an
    inline "Nerede kaldık: ... Açık: Sende: ...; Bende (30 Ekim): ... Kaynak: [[...]]" line is read like the
    one-line-per-part form. Items keep their raw text so the quote can be found in Threads.md word for word.
    The date is the latest date in the block that is not in the future (ISO or "29 Ekim"), as an ISO string."""
    today = today or date.today()
    body = text
    for m in c.ACTIVE_HEADINGS:
        if m in body:
            body = body.split(m, 1)[1]
            break
    for m in c.CLOSED_HEADINGS:
        body = body.split(m, 1)[0]
    out = []
    for block in re.split(r"^### ", body, flags=re.M)[1:]:
        title, _, rest = block.partition("\n")
        title = title.strip()
        _before, open_part = c._cut_at(rest, c.OPEN_MARKERS)
        open_part, _ = c._cut_at(open_part, c.SOURCE_MARKERS)
        _, src_part = c._cut_at(rest, c.SOURCE_MARKERS)
        link = c.WIKILINK.search(src_part)
        source = link.group(1).strip() if link else ""
        items = []
        for seg in re.split(r";|\n", open_part):
            seg = seg.strip().rstrip(".").strip()
            if len(seg) < 3:
                continue
            who = c.OWNER.match(seg)
            txt = seg[who.end():] if who else seg
            if txt.strip().rstrip(".").strip().casefold() in c.EMPTY_VALUES:
                continue
            owner = ""
            if who:
                g = (who.group(1) or who.group(2) or "").strip().lower()
                owner = {"sende": "you", "bende": "me", "başkası": "someone"}.get(g, g)
            items.append({"quote": seg, "text": c.clean(txt, 200), "owner": owner})
        past = [d for d in find_dates(rest, today) if d <= today]
        out.append({"title": title, "heading": "### " + title, "items": items, "source": source,
                    "date": max(past).isoformat() if past else ""})
    return out


def _todo_lines(text: str, today: date) -> dict:
    """Checkbox lines of To-Do.md: every task text (open or done) and the open ones with their raw line."""
    open_texts = {t["text"] for t in c.parse_todo(text, today)}
    every, opened = [], []
    for line in text.splitlines():
        m = c.TASK.match(line)
        if not m:
            continue
        clean = c.clean(c.DUE.sub("", m.group(2)).replace("()", "").strip(" -"))
        every.append(clean)
        if m.group(1) == " " and clean in open_texts:
            due = c.DUE.search(m.group(2))
            opened.append({"raw": line.strip(), "text": clean, "due": due.group(1) if due else ""})
    return {"all": every, "open": opened}


def _in_todo(text: str, todo_all: list[str]) -> bool:
    k = words(text)
    if not k:
        return True  # nothing to compare: do not propose
    need = max(1, math.ceil(0.6 * len(k)))
    return any(len(k & words(t)) >= need for t in todo_all)


def _norm_task(s: str) -> str:
    s = c.DUE.sub("", s).replace("()", "")
    return " ".join(re.sub(r"[^\w\s]", " ", s.casefold()).split())


def _date_of(p: Path, text: str, today: date | None = None) -> date | None:
    """The date of a news or inbox item: front matter date, then a date in the file name, then the file time.
    Dates go through parse_date, so "2026-10-29", "29 Ekim 2026" and "29 Ekim" all work."""
    today = today or date.today()
    fm = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if fm:
        m = re.search(r"^(?:date|published|captured):\s*['\"]?([^'\"\n]+)", fm.group(1), re.M)
        d = parse_date(m.group(1), today) if m else None
        if d is not None:
            return d
    d = parse_date(p.name, today)
    if d is not None:
        return d
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).date()
    except OSError:
        return None


def _title_of(p: Path, text: str) -> tuple[str, str]:
    """(title, verbatim quote) from the first '# ' heading, else the first non-empty body line, else the file name."""
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip(), s
        if s and not s.startswith("#"):
            return c.clean(s, 120), s
    return p.stem, ""


class Vault:
    """One read of everything the night pass needs."""

    def __init__(self, state: dict, today: date):
        self.root = Path(state["vault"])
        self.today = today
        self.cfg = state["cfg"].get("night") or {}
        lay = state.get("layout") or c.resolve_layout(self.root)
        self.paths = {k: lay[k]["path"] for k in ("threads", "todo", "last_session")}
        self.todo_path = lay["todo"]["path"]
        self.inbox = lay["inbox"]["path"]
        self.journal_dir = self.root / self.cfg.get("journal_dir", "daily")
        self.news_dir = self.root / self.cfg.get("news_dir", "news")
        self.threads_text = _read(self.paths["threads"]) if self.paths["threads"] else ""
        self.todo_text = _read(self.todo_path) if self.todo_path else ""
        self.threads = raw_threads(self.threads_text, today)
        self.todo = _todo_lines(self.todo_text, today)
        self._cache: dict[Path, str] = {}
        if self.paths["threads"] is not None:
            self._cache[self.paths["threads"]] = self.threads_text
        if self.todo_path is not None:
            self._cache[self.todo_path] = self.todo_text
        self.projects = self._projects()

    def text(self, p: Path) -> str:
        if p not in self._cache:
            self._cache[p] = _read(p)
        return self._cache[p]

    def rel(self, p: Path | None) -> str:
        return c.rel(self.root, p)

    def resolve_link(self, link: str) -> Path | None:
        if not link:
            return None
        cand = self.root / (link if link.endswith(".md") else link + ".md")
        try:
            cand.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        return cand if cand.is_file() else None

    def _projects(self) -> list[dict]:
        out = []
        for t in self.threads:
            kw = words(t["title"])
            note = self.resolve_link(t["source"])
            if note is not None:
                _title, aliases = c.note_meta(note, self.text(note))
                for a in aliases:
                    kw |= words(a)
            if kw:
                out.append({**t, "keywords": kw, "note": note})
        return out

    def notes(self) -> list[Path]:
        extra = [self.inbox, self.journal_dir, self.news_dir]
        files = c.note_files(self.root, extra_dirs=extra)
        return files[:MAX_NOTES]

    def match_projects(self, text: str) -> list[tuple[dict, list[str]]]:
        w = words(text)
        hits = []
        for p in self.projects:
            shared = sorted(p["keywords"] & w)
            if shared:
                hits.append((p, shared))
        return hits


# ---------------------------------------------------------------- rule passes

def pass_todo(v: Vault) -> list[dict]:
    out = []
    threads_rel = v.rel(v.paths["threads"])
    todo_rel = v.rel(v.todo_path)
    for t in v.threads:
        for it in t["items"]:
            if it["owner"] not in ("", "you", "me") or _in_todo(it["text"], v.todo["all"]):  # others' items stay theirs
                continue
            out.append(_prop("todo", f"Add to To-Do: {it['text']} ({t['title']})", "thread_item_not_in_todo",
                             [{"path": threads_rel, "quote": it["quote"]}], task=it["text"]))
    for o in v.todo["open"]:
        if o["due"] and o["due"] < v.today.isoformat():
            out.append(_prop("todo", f"Overdue since {o['due']}: {o['text']}", "overdue",
                             [{"path": todo_rel, "quote": o["raw"]}]))
    places = defaultdict(list)
    for o in v.todo["open"]:
        places[_norm_task(o["text"])].append({"path": todo_rel, "quote": o["raw"]})
    skip = {v.paths["threads"], v.paths["last_session"], v.todo_path}
    for p in v.notes():
        if p in skip:
            continue
        for line in v.text(p).splitlines():
            m = c.TASK.match(line)
            if m and m.group(1) == " ":
                places[_norm_task(m.group(2))].append({"path": v.rel(p), "quote": line.strip()})
    for key, srcs in places.items():
        if key and len({s["path"] + s["quote"] for s in srcs}) >= 2:
            m0 = c.TASK.match(srcs[0]["quote"])
            label = c.clean(c.DUE.sub("", m0.group(2) if m0 else srcs[0]["quote"]).replace("()", ""), 120)
            out.append(_prop("todo", f"Same open task in {len(srcs)} places: {label}", "duplicate", srcs[:4]))
    return out


def _touched(v: Vault, t: dict) -> date | None:
    if t["date"]:
        d = parse_date(t["date"], v.today)
        if d is not None:
            return d
    note = v.resolve_link(t["source"])
    if note is not None:
        try:
            return datetime.fromtimestamp(note.stat().st_mtime).date()
        except OSError:
            return None
    return None


def pass_improve(v: Vault) -> list[dict]:
    stale_days = int(v.cfg.get("stale_days", 7))
    rows = []
    for t in v.threads:
        d = _touched(v, t)
        if d is None or not t["items"] or (v.today - d).days < stale_days:
            continue
        rows.append(((v.today - d).days, t))
    rows.sort(key=lambda r: -r[0])
    out = []
    for days, t in rows[: MAX_PER_KIND["improve"]]:
        it = t["items"][0]
        out.append(_prop("improve", f"{t['title']} has not moved for {days} days. Next step: {it['text']}",
                         "stale_thread", [{"path": v.rel(v.paths["threads"]), "quote": it["quote"]}],
                         task=f"{t['title']}: {it['text']}"))
    return out


def _linked(a_text: str, b: Path, b_title: str, root: Path) -> bool:
    targets = {b.stem.casefold(), b_title.casefold(), c.rel(root, b).casefold(), c.rel(root, b)[:-3].casefold()}
    for m in c.WIKILINK.finditer(a_text):
        if m.group(1).strip().casefold().split("#")[0] in targets:
            return True
    return f"]({c.rel(root, b)}" in a_text or f"]({b.name}" in a_text


def pass_link(v: Vault) -> list[dict]:
    skip = {v.paths["threads"], v.paths["last_session"], v.todo_path}
    skip_dirs = {v.journal_dir.resolve(), v.news_dir.resolve()}
    docs = []
    for p in v.notes():
        if p in skip or any(d in p.resolve().parents for d in skip_dirs):
            continue
        txt = v.text(p)
        docs.append((p, txt, words(txt)))
    if len(docs) < 2:
        return []
    df = defaultdict(int)
    for _p, _t, w in docs:
        for x in w:
            df[x] += 1
    # Rare words carry the topic: at most 2% of the notes (2 minimum, so a pair can always share a word), and
    # never more than LINK_MAX_DF of a larger vault, so a word every other note uses cannot make a link.
    limit = max(2, min(int(0.02 * len(docs)), int(LINK_MAX_DF * len(docs))))
    index = defaultdict(list)
    strong = []
    for i, (_p, _t, w) in enumerate(docs):
        s = {x for x in w if df[x] <= limit}
        strong.append(s)
        for x in s:
            index[x].append(i)
    pairs = defaultdict(set)
    for x, ids in index.items():
        if len(ids) < 2:
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pairs[(ids[a], ids[b])].add(x)
    out = []
    for (a, b), shared in sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(shared) < LINK_MIN_SHARED:
            continue
        (pa, ta, _), (pb, tb, _) = docs[a], docs[b]
        title_a, _ = c.note_meta(pa, ta)
        title_b, _ = c.note_meta(pb, tb)
        if _linked(ta, pb, title_b, v.root) or _linked(tb, pa, title_a, v.root):
            continue
        shown = sorted(shared)[:5]
        srcs = []
        for p, t in ((pa, ta), (pb, tb)):
            line = next((ln for ln in t.splitlines() if words(ln) & shared and ln.strip()), "")
            srcs.append({"path": v.rel(p), "quote": line.strip()})
        out.append(_prop("link", f"Link {v.rel(pa)} and {v.rel(pb)}: both mention {', '.join(shown)}",
                         "shared_keywords", srcs, key=v.rel(pa) + "|" + v.rel(pb), words=shown))
        if len(out) >= MAX_PER_KIND["link"]:
            break
    return out


def vault_news(v: Vault) -> list[dict]:
    if not v.news_dir.is_dir():
        return []
    since = v.today - timedelta(days=int(v.cfg.get("news_days", 2)))
    items = []
    for p in sorted(v.news_dir.rglob("*.md"))[:500]:
        txt = v.text(p)
        d = _date_of(p, txt, v.today)
        if d is None or d < since:
            continue
        title, quote = _title_of(p, txt)
        if quote:
            items.append({"path": v.rel(p), "title": title, "quote": quote, "text": txt, "date": d.isoformat()})
    return items


def db_news(conn, v: Vault, label: str = "briefing_items") -> list[dict] | None:
    """Briefing items already matched to a project (by Kokpit, or by the plugin's own collector);
    None when the table is not there. label names the source in each proposal's item reference."""
    if conn is None or not c.has_table(conn, "briefing_items"):
        return None
    if not all(c.has_column(conn, "briefing_items", col) for col in ("project", "title", "day")):
        return None
    since = (v.today - timedelta(days=int(v.cfg.get("news_days", 2)))).isoformat()
    reason = "reason" if c.has_column(conn, "briefing_items", "reason") else "'' AS reason"
    rows = c.rows(conn, f"SELECT id, day, title, project, {reason} FROM briefing_items "
                        "WHERE project IS NOT NULL AND project != '' AND day >= ? ORDER BY day DESC, id DESC LIMIT 50",
                  (since,))
    return [{"item": f"{label}#{r['id']}", "title": c.clean(r["title"], 160), "quote": r["title"],
             "project": c.clean(r["project"], 80), "why": c.clean(r.get("reason") or "", 160), "date": r["day"]}
            for r in rows]


def pass_news(v: Vault, conn, store_conn=None) -> tuple[list[dict], list[dict], dict]:
    """(news proposals, matching todo proposals, info about the source).
    Order: the Kokpit app database, then the plugin's own collector store, then the vault news folder."""
    news, todos = [], []
    try:
        items = db_news(conn, v)
    except sqlite3.Error:
        items = None
    source = "kokpit database (briefing_items)"
    if not items and store_conn is not None:
        try:
            items = db_news(store_conn, v, label="plugin_briefing")
        except sqlite3.Error:
            items = None
        source = "plugin store (briefing_items)"
    if items:
        info = {"source": source, "items": len(items), "matched": len(items)}
        for it in items:
            src = [{"item": it["item"], "quote": it["quote"]}]
            reason = it["why"] or f"tagged {it['project']} by " + ("Kokpit" if source.startswith("kokpit") else "the plugin")
            news.append(_prop("news", f"News for {it['project']}: {it['title']}", reason, src,
                              key=it["project"], project=it["project"]))
            task = f"Check {it['title']} for {it['project']}"
            if not _in_todo(task, v.todo["all"]):
                todos.append(_prop("todo", task, "news_match", src, key="news_match:" + task, task=task))
        return news, todos, info
    items = vault_news(v)
    if not items:
        what = "no news source" if not v.news_dir.is_dir() else "no recent news items"
        return [], [], {"source": what, "items": 0, "matched": 0}
    matched = 0
    for it in items:
        hits = v.match_projects(it["title"] + "\n" + it["text"][:1500])
        if not hits:
            continue
        matched += 1
        for proj, shared in hits[:2]:
            src = [{"path": it["path"], "quote": it["quote"]}]
            reason = f"matches {proj['title']}: {', '.join(shared[:3])}"
            news.append(_prop("news", f"News for {proj['title']}: {it['title']}", reason, src,
                              key=proj["title"], project=proj["title"]))
            task = f"Check {it['title']} for {proj['title']}"
            if not _in_todo(task, v.todo["all"]):
                todos.append(_prop("todo", task, "news_match", src, key="news_match:" + task, task=task))
    return news, todos, {"source": f"vault {v.rel(v.news_dir)}/", "items": len(items), "matched": matched}


def recent_items(v: Vault, news: list[dict]) -> list[dict]:
    """Inbox notes, journal lines and news items from the last news_days days."""
    since = v.today - timedelta(days=int(v.cfg.get("news_days", 2)))
    out = []
    if v.inbox and Path(v.inbox).is_dir():
        for p in sorted(Path(v.inbox).rglob("*.md"))[-200:]:
            txt = v.text(p)
            d = _date_of(p, txt, v.today)
            if d is not None and d >= since:
                title, quote = _title_of(p, txt)
                if quote:
                    out.append({"path": v.rel(p), "title": title, "quote": quote, "text": txt})
    if v.journal_dir.is_dir():
        for p in sorted(v.journal_dir.glob("20??-??-??*.md"))[-int(v.cfg.get("news_days", 2)) - 1:]:
            d = _date_of(p, "", v.today)
            if d is None or d < since:
                continue
            for line in v.text(p).splitlines():
                s = line.strip()
                if s.startswith("- ") and len(s) > 8:
                    body = re.sub(r"^- (\d\d:\d\d )?", "", s)
                    out.append({"path": v.rel(p), "title": c.clean(body, 90), "quote": s, "text": body})
    for it in news:
        if "path" in it:
            out.append(it)
    return out


def pass_idea(v: Vault, news_items: list[dict]) -> list[dict]:
    out = []
    for it in recent_items(v, news_items):
        hits = v.match_projects(it["title"] + "\n" + it["text"][:1500])
        if len(hits) < 2:
            continue
        (a, wa), (b, wb) = hits[0], hits[1]
        srcs = [{"path": it["path"], "quote": it["quote"]},
                {"path": v.rel(v.paths["threads"]), "quote": a["heading"]},
                {"path": v.rel(v.paths["threads"]), "quote": b["heading"]}]
        out.append(_prop("idea", f"\"{c.clean(it['title'], 90)}\" connects {a['title']} and {b['title']}",
                         f"shared words: {', '.join((wa + wb)[:4])}", srcs, key="connects",
                         idea_title=f"{a['title']} meets {b['title']}"))
        if len(out) >= MAX_PER_KIND["idea"]:
            break
    return out


# ---------------------------------------------------------------- optional model pass

MODEL_KINDS = ("todo", "improve", "idea")
MODEL_SCHEMA = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "maxItems": 6, "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": list(MODEL_KINDS)},
                       "text": {"type": "string"}, "path": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["kind", "text", "path", "quote"]}}},
    "required": ["proposals"],
}


def check_model(v: Vault, raw, allowed_paths: set[str]) -> tuple[list[dict], int]:
    """Keep a model proposal only when its kind is allowed, its text is short, its path is a real vault
    file that was offered, and its quote is found word for word in that file. Others are only counted."""
    kept, rejected = [], 0
    items = raw.get("proposals") if isinstance(raw, dict) else None
    for x in items if isinstance(items, list) else []:
        try:
            kind, text = str(x["kind"]), " ".join(str(x["text"]).split())
            path, quote = str(x["path"]).strip(), str(x["quote"]).strip()
        except (KeyError, TypeError):
            rejected += 1
            continue
        p = v.root / path
        ok = (kind in MODEL_KINDS and 3 <= len(text) <= 200 and path in allowed_paths and 8 <= len(quote) <= QUOTE_MAX
              and p.is_file())
        if ok:
            try:
                p.resolve().relative_to(v.root.resolve())
            except (OSError, ValueError):
                ok = False
        if not ok or quote not in v.text(p):
            rejected += 1
            continue
        kept.append(_prop(kind, text, "model", [{"path": path, "quote": quote}],
                          task=text if kind in ("todo", "improve") else None,
                          idea_title=text[:80] if kind == "idea" else None))
    return kept, rejected


def pass_model(v: Vault, llm) -> tuple[list[dict], dict]:
    files = [v.paths["threads"]] + [p["note"] for p in v.projects if p["note"] is not None]
    files += sorted((p for p in v.notes() if p not in files), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
    files = [p for p in files if p is not None][:10]
    allowed = {v.rel(p) for p in files}
    blob = "\n\n".join(f"=== {v.rel(p)}\n{v.text(p)[:1500]}" for p in files)
    try:
        res = llm.complete_structured(
            instructions=("Propose at most 6 short next steps (todo), stalled-project nudges (improve) or ideas "
                          "that connect two projects (idea). Each must cite one file path from the list and a "
                          "quote copied exactly from that file. No invented facts."),
            input=[{"type": "text", "text": blob}], json_schema=MODEL_SCHEMA, purpose="kokpit.night",
            temperature=0.0, max_tokens=900)
        parsed = getattr(res, "parsed", None)
    except Exception as e:  # the rule path already ran; a model failure only shows up in the counts
        return [], {"model": f"failed ({type(e).__name__})", "model_kept": 0, "model_rejected": 0}
    kept, rejected = check_model(v, parsed, allowed)
    return kept, {"model": "on", "model_kept": len(kept), "model_rejected": rejected}


# ---------------------------------------------------------------- storage

def load(path: Path | None) -> dict:
    empty = {"version": 1, "last_run": "", "last_counts": {}, "proposals": []}
    if path is None or not Path(path).is_file():
        return empty
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        return empty
    return {**empty, **data}


def save(path: Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c.atomic_write(Path(path), json.dumps(data, ensure_ascii=False, indent=1))


def merge(store: dict, fresh: list[dict], now: str) -> int:
    """Add new proposals as open, refresh open ones, drop open ones whose cause is gone.
    Done and dismissed proposals stay as they are and never reopen. Returns the number of new ones."""
    old = {p["id"]: p for p in store["proposals"]}
    seen, out, new = set(), [], 0
    for p in fresh:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        prev = old.get(p["id"])
        if prev is None:
            out.append({**p, "created": now, "status": "open"})
            new += 1
        elif prev.get("status") == "open":
            out.append({**p, "created": prev.get("created", now), "status": "open"})
        else:
            out.append(prev)
    closed = [p for p in store["proposals"] if p["id"] not in seen and p.get("status") in ("done", "dismissed")]
    store["proposals"] = out + closed[-MAX_KEPT:]
    return new


def _verified(v: Vault, p: dict) -> bool:
    for s in p["sources"]:
        if "path" in s:
            f = v.root / s["path"]
            if not s["quote"] or not f.is_file() or s["quote"] not in v.text(f):
                return False
    return True


# ---------------------------------------------------------------- run

def run(state: dict, today: date | None = None, now: datetime | None = None) -> dict:
    """One night pass. Writes only the proposal store; returns counts for the reply."""
    if state.get("vault") is None or not Path(state["vault"]).is_dir():
        raise c.NotReady("vault not found", "set plugins.entries.kokpit.settings.vault_path or kokpit_config")
    store_path = state.get("night_store")
    if store_path is None:
        raise c.NotReady("no place to store night proposals", "check the plugin data dir and vault permissions")
    t0 = time.perf_counter()
    now = now or datetime.now()
    today = today or now.date()
    v = Vault(state, today)
    conn = own = None
    try:
        conn = c.open_db(state.get("db"))
    except sqlite3.Error:
        conn = None
    try:  # the plugin's own collector store, read-only here; never in demo mode
        own_path = (state.get("store") or {}).get("path")
        own = c.open_db(own_path) if own_path is not None and state.get("mode") != "demo" else None
    except sqlite3.Error:
        own = None
    try:
        todos = pass_todo(v)
        news, news_todos, news_info = pass_news(v, conn, own)
    finally:
        for x in (conn, own):
            if x is not None:
                x.close()
    news_items = vault_news(v)
    fresh = (todos + news_todos)[: MAX_PER_KIND["todo"]] + pass_improve(v) + pass_link(v) \
        + pass_idea(v, news_items) + news[: MAX_PER_KIND["news"]]
    info = {"model": "off"}
    if v.cfg.get("night_model"):
        llm = state.get("llm")
        try:
            llm = llm() if callable(llm) else llm
        except Exception:
            llm = None
        if llm is None or not hasattr(llm, "complete_structured"):
            info = {"model": "unavailable (this Hermes has no plugin LLM access)"}
        else:
            kept, info = pass_model(v, llm)
            fresh += kept
    dropped = len(fresh)
    fresh = [p for p in fresh if _verified(v, p)]
    dropped -= len(fresh)
    uniq: dict[str, dict] = {}
    for p in fresh:
        uniq.setdefault(p["id"], p)
    fresh = list(uniq.values())
    store = load(store_path)
    stamp = now.isoformat(timespec="seconds")
    new = merge(store, fresh, stamp)
    generated = {k: sum(1 for p in fresh if p["kind"] == k) for k in KINDS}
    store["last_run"] = stamp
    store["last_counts"] = {"generated": generated, "new": new, "news": news_info, **info,
                            "unverified_dropped": dropped,
                            "seconds": round(time.perf_counter() - t0, 3)}
    save(store_path, store)
    return {"last_run": stamp, **store["last_counts"], "open": open_counts(store)}


def open_counts(store: dict) -> dict:
    out = {k: 0 for k in KINDS}
    for p in store["proposals"]:
        if p.get("status") == "open" and p.get("kind") in out:
            out[p["kind"]] += 1
    return out


def due_for_auto(store_path: Path | None, now: datetime | None = None) -> bool:
    store = load(store_path)
    if not store["last_run"]:
        return True
    try:
        last = datetime.fromisoformat(store["last_run"])
    except ValueError:
        return True
    return (now or datetime.now()) - last >= timedelta(hours=AUTO_HOURS)


ORDER = ("todo", "idea", "news", "improve", "link")


def ordered(proposals: list[dict]) -> list[dict]:
    """Open proposals, one of each kind first (so a short list shows the range), then the rest."""
    by = defaultdict(list)
    for p in proposals:
        if p.get("status") == "open":
            by[p["kind"]].append(p)
    out, rnd = [], 0
    while any(by.values()):
        for k in ORDER + tuple(x for x in by if x not in ORDER):
            if len(by[k]) > rnd:
                out.append(by[k][rnd])
        rnd += 1
        if rnd > 1000:
            break
    seen, uniq = set(), []
    for p in out:
        if p["id"] not in seen:
            seen.add(p["id"])
            uniq.append(p)
    return uniq


def find(store: dict, pid: str) -> tuple[dict | None, str]:
    pid = (pid or "").strip().lower()
    if len(pid) < 4 or not re.fullmatch(r"[0-9a-f]+", pid):
        return None, "give the proposal id (at least 4 characters), as shown by /kokpit night"
    hits = [p for p in store["proposals"] if p["id"].startswith(pid)]
    if not hits:
        return None, f"no proposal {pid}; run /kokpit night to list them"
    if len(hits) > 1:
        return None, f"{pid} matches {len(hits)} proposals; give more characters"
    return hits[0], ""


def decide(state: dict, pid: str, action: str, now: datetime | None = None) -> dict:
    """accept or dismiss one open proposal. Accept may append a task to To-Do.md or create a new idea file."""
    store_path = state.get("night_store")
    if store_path is None:
        raise c.NotReady("no night proposals yet", "run /kokpit night run")
    store = load(store_path)
    p, err = find(store, pid)
    if p is None:
        raise c.NotReady(err, "run /kokpit night run, then /kokpit night")
    if p.get("status") != "open":
        raise c.NotReady(f"proposal {p['id'][:6]} is already {p.get('status')}", "nothing to do")
    now = now or datetime.now()
    out = {"id": p["id"], "kind": p["kind"], "text": p["text"]}
    if action == "dismiss":
        p["status"], p["decided"] = "dismissed", now.isoformat(timespec="seconds")
        save(store_path, store)
        return {**out, "status": "dismissed", "written": ""}
    if action != "accept":
        raise c.NotReady("action must be accept or dismiss", "use /kokpit add <id> or /kokpit skip <id>")
    vault = Path(state["vault"]) if state.get("vault") else None
    if vault is None or not vault.is_dir():
        raise c.NotReady("vault not found", "set the vault in the plugin settings")
    v = Vault(state, now.date())
    if not _verified(v, p):
        raise c.NotReady("the source changed since the night pass", "run /kokpit night run again")
    written, line = "", ""
    writes = p["kind"] in ("todo", "improve") and p.get("task") or p["kind"] == "idea"
    if writes:
        if not state["cfg"]["modules"].get("brain", True) or not state["cfg"].get("quick_capture", True):
            raise c.NotReady("quick capture is off", "enable with [brain] quick_capture = true in kokpit.toml")
    if p["kind"] in ("todo", "improve") and p.get("task"):
        err = c.ambiguous(state.get("layout"), "todo")
        if err:
            raise err
        if _norm_task(p["task"]) in {_norm_task(t) for t in v.todo["all"]}:
            line = "already in To-Do.md"
        else:
            body, due = c.split_due(p["task"])
            line = c.capture_task(vault, body[:300], due, path=v.todo_path)
            written = v.rel(v.todo_path)
    elif p["kind"] == "idea":
        lines = [p["text"], ""] + [f"- Source: {s.get('path') or s.get('item')}: \"{s['quote']}\"" for s in p["sources"]]
        f = c.capture_idea(vault, p.get("idea_title") or p["text"][:80], lines, now)
        written = v.rel(f)
    p["status"], p["decided"] = "done", now.isoformat(timespec="seconds")
    save(store_path, store)
    return {**out, "status": "done", "written": written, **({"line": line} if line else {})}


def app_proposals(db) -> list[dict]:
    """Open proposals from the Kokpit app's own organiser, read-only; empty on any other schema."""
    try:
        conn = c.open_db(db)
    except sqlite3.Error:
        return []
    if conn is None:
        return []
    try:
        if not c.has_table(conn, "organiser_proposals"):
            return []
        rows = c.rows(conn, "SELECT id, kind, path, text FROM organiser_proposals WHERE status = 'open' "
                            "ORDER BY last_seen DESC, id DESC LIMIT 5")
        return [{"id": r["id"], "kind": c.clean(r["kind"], 20), "path": c.clean(r["path"], 120),
                 "text": c.clean(r["text"], 200), "from": "Kokpit app"} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def listing(state: dict, kind: str = "", limit: int = 5) -> dict:
    store = load(state.get("night_store"))
    items = ordered(store["proposals"])
    if kind:
        items = [p for p in items if p["kind"] == kind]
    return {"last_run": store["last_run"] or "never", "open": open_counts(store),
            "last_counts": store.get("last_counts") or {},
            "proposals": [{**p, "short": p["id"][:6]} for p in items[:limit]],
            "kokpit_app": app_proposals(state.get("db"))}


def brain_line(store_path: Path | None) -> str:
    """'Night: 2 todos, 1 idea, 1 news match. /kokpit night' or '' when nothing is open."""
    counts = open_counts(load(store_path))
    names = {"todo": ("todo", "todos"), "improve": ("next step", "next steps"), "link": ("link", "links"),
             "idea": ("idea", "ideas"), "news": ("news match", "news matches")}
    parts = [f"{n} {names[k][0] if n == 1 else names[k][1]}" for k in KINDS if (n := counts[k])]
    return f"Night: {', '.join(parts)}. /kokpit night" if parts else ""
