"""The Tico manual: this release's own docs/*.md, searchable from inside every install.

A read-only collection that is kept apart from the team's docs on purpose: it lives in memory, built from the
files the server image ships, and is never written to the database, so it cannot be edited, synced, exported or
backed up as team content, and a team doc can never be mistaken for it. It is versioned with the release:
the index is rebuilt when the running version (or a file) changes, with no network. Results carry
`collection: "manual"` and the label "Tico manual", the file (`docs/<name>.md`) and a link to that page.
"""

import functools
import re
import sqlite3
import threading
from pathlib import Path

from . import releases

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
LABEL = "Tico manual"
GITHUB = "https://github.com/ticoteam/tico/blob/"
STOP = frozenset(
    "a an and are as at be but by do does for from how i in is it my of on or our the to us was we what "
    "when where which who why with you your can could should would please tell me about into new say says next long its want like help know".split()
)
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_lock = threading.Lock()
_cache = {"key": None, "pages": {}, "index": None}
SECTION_BITS = 20  # a section's full-text rowid: its page's (or doc's) number shifted left, plus its position
# Title, path, heading and body: a heading that has the words marks the section that answers them best.
RANK = "bm25({table}, 3.0, 2.0, 6.0, 1.0)"


def manual_url(version, name, anchor=""):
    """The public page for a manual file: the GitHub file at the release's tag (the docs site's own address pattern is
    not in this repository), or `main` on a build with no release version."""
    ref = "v" + version if re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", str(version or "")) else "main"
    return f"{GITHUB}{ref}/docs/{name}.md" + ("#" + anchor if anchor else "")


def slug(heading):
    """The anchor GitHub gives a heading."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", heading).strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def _words(text):
    return re.findall(r"\w+", str(text).casefold())


def page_text(name, text, title=None):
    """A page's title and sections, each its heading, anchor and span of `text` (`section_text`); at least one, so a
    page with no text is still found by its title."""
    provided_title = title
    title, sections, current, in_code, at = (
        title or name.replace("-", " ").title(),
        [],
        {"heading": "", "anchor": "", "start": 0, "filled": False},
        False,
        0,
    )
    for raw in text.splitlines(keepends=True):
        line, at = raw.splitlines()[0], at + len(raw)
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            if m.group(1) == "#" and not sections and not current["filled"]:
                title, current["heading"] = provided_title or m.group(2), m.group(2)
                current.update(anchor=slug(m.group(2)), start=at)  # only empty lines came before
                continue
            sections.append({**current, "end": at - len(raw)})
            current = {"heading": m.group(2), "anchor": slug(m.group(2)), "start": at, "filled": False}
        else:
            current["filled"] = current["filled"] or bool(line)
    sections.append({**current, "end": at})
    out = [
        {k: s[k] for k in ("heading", "anchor", "start", "end")}
        for s in sections
        if s["heading"] or text[s["start"] : s["end"]].strip()
    ] or [{"heading": "", "anchor": "", "start": 0, "end": len(text)}]
    return {"name": name, "title": title, "body": text, "sections": out}


def section_text(page, section):
    """A section's text from its page's body, each run of whitespace one space."""
    return " ".join(page["body"][section["start"] : section["end"]].split())


def pages():
    """{name: page} for the running release; rebuilt when the version or a file changes."""
    version = releases.version()
    files = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    with _lock:
        # Coarse or preserved timestamps can hide edits; compare the same text snapshots we index.
        sources = tuple((f.stem, f.read_text(encoding="utf-8", errors="replace")) for f in files)
        key = (version, str(DOCS_DIR), sources)
        if _cache["key"] != key:
            _cache.update(key=key, pages={name: page_text(name, text) for name, text in sources}, index=None)
        return _cache["pages"]


def section_rows(number, title, path, page):
    """The full-text rows of one page or doc (`page_text`), one per section."""
    return [
        ((number << SECTION_BITS) + n, title, path, s["heading"], page["body"][s["start"] : s["end"]])
        for n, s in enumerate(page["sections"][: 1 << SECTION_BITS])
    ]


def best_sections(c, table, tokens):
    """[(page or doc number, section rowid, bm25)]: the best-ranked section of each that has any of the words (each
    a prefix, so a word being typed matches); bm25 is SQLite's, lower is better."""
    expression = " OR ".join('"' + _stem(t) + '"*' for t in tokens)
    return c.execute(
        f"WITH hits AS MATERIALIZED (SELECT rowid AS section, {RANK.format(table=table)} AS rank FROM {table} "
        f"WHERE {table} MATCH ?) SELECT section >> {SECTION_BITS}, section, MIN(rank) FROM hits "
        f"GROUP BY section >> {SECTION_BITS}",
        (expression,),
    ).fetchall()


def _index(found):
    """The manual's sections in an in-memory full-text table, built once per `pages()` snapshot."""
    if _cache["index"] is None or _cache["index"][0] is not found:
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.execute(
            "CREATE VIRTUAL TABLE temp.sections USING fts5(title, path, heading, body, tokenize='porter unicode61')"
        )
        for number, page in enumerate(found.values()):
            db.executemany(
                "INSERT INTO sections(rowid,title,path,heading,body) VALUES(?,?,?,?,?)",
                section_rows(number, page["title"], page["name"], page),
            )
        _cache["index"] = (found, db)
    return _cache["index"][1]


def _stem(token):
    return token[:-1] if len(token) > 4 and token.endswith("s") else token


def _hits(words, token):
    stem = _stem(token)
    return token in words or any(w.startswith(stem) for w in words)


@functools.lru_cache(maxsize=8)
def _glossary_rules(glossary):
    """The glossary's old-word -> new-word rules, compiled, longest first."""
    mappings = re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", glossary, re.M)
    return tuple(
        (re.compile(r"\b" + re.escape(old.casefold()) + r"\b"), new.casefold())
        for old, new in sorted(mappings, key=lambda pair: -len(pair[0]))
        if old.casefold() not in ("instead of", "---")
    )


@functools.lru_cache(maxsize=2048)
def _query_words(q, glossary):
    text = q.casefold()
    for pattern, new in _glossary_rules(glossary):
        text = pattern.sub(new, text)
    tokens = _words(text)
    return tuple(dict.fromkeys(t for t in tokens if t not in STOP)) or tuple(tokens)


def query_words(q, glossary=None):
    if glossary is None:
        glossary = pages().get("glossary", {}).get("body", "")
    return list(_query_words(str(q), glossary))


def _excerpt(body, tokens):
    low = body.casefold()
    at = min((i for i in (low.find(_stem(t)) for t in tokens) if i >= 0), default=0)
    if len(body) <= 1200:
        return body.strip()
    begin = max(0, at - 160)
    if begin:
        begin = body.find(" ", begin) + 1
    end = body.rfind(" ", begin, begin + 1200)
    return body[begin : end if end > begin else begin + 1200].strip()


def result(page, section, version, score):
    heading = section["heading"] if section["heading"] and section["heading"] != page["title"] else ""
    return {
        "type": "manual",
        "collection": "manual",
        "label": LABEL,
        "id": "manual:" + page["name"],
        "path": f"docs/{page['name']}.md",
        "title": page["title"] + (" > " + heading if heading else ""),
        "excerpt": "",
        "url": manual_url(version, page["name"], section["anchor"] if heading else ""),
        "version": version,
        "score": round(score, 3),
    }


def search(q, limit=20):
    """The best section of each page that answers the words, best first (a question in a sentence works: small words
    are ignored, and sections are ranked by their matching topic words)."""
    wanted = query_words(q)
    if not wanted:
        return []
    version, found = releases.version(), pages()
    listed = list(found.values())
    with _lock:
        best = best_sections(_index(found), "sections", wanted)
    out = []
    for number, section, rank in sorted(best, key=lambda hit: (hit[2], listed[hit[0]]["name"]))[:limit]:
        page = listed[number]
        chosen = page["sections"][section & ((1 << SECTION_BITS) - 1)]
        row = result(page, chosen, version, -rank)
        row.update(section=chosen["heading"], excerpt=_excerpt(section_text(page, chosen), wanted))
        out.append(row)
    return out


def _name(ref):
    ref = str(ref or "").strip()
    if ref.lower().startswith("manual:"):
        ref = ref[7:]
    ref = ref.removeprefix("docs/").strip("/")
    return re.sub(r"\.md$", "", ref, flags=re.I)


def listing():
    version = releases.version()
    return [
        {
            "id": "manual:" + p["name"],
            "path": f"docs/{p['name']}.md",
            "title": p["title"],
            "collection": "manual",
            "label": LABEL,
            "url": manual_url(version, p["name"]),
            "version": version,
        }
        for p in pages().values()
    ]


def read(ref):
    """One manual page in full, or None."""
    page = pages().get(_name(ref))
    if not page:
        return None
    version = releases.version()
    return {
        "id": "manual:" + page["name"],
        "path": f"docs/{page['name']}.md",
        "title": page["title"],
        "body": page["body"],
        "collection": "manual",
        "label": LABEL,
        "read_only": True,
        "version": version,
        "url": manual_url(version, page["name"]),
    }
