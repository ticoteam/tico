"""The Tico manual: this release's own docs/*.md, searchable from inside every install.

A read-only collection that is kept apart from the team's docs on purpose: it lives in memory, built from the
files the server image ships, and is never written to the database, so it cannot be edited, synced, exported or
backed up as team content, and a team doc can never be mistaken for it. It is versioned with the release:
the index is rebuilt when the running version (or a file) changes, with no network. Results carry
`collection: "manual"` and the label "Tico manual", the file (`docs/<name>.md`) and a link to that page.
"""
import re
import math
import threading
from pathlib import Path

from . import releases

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
LABEL = "Tico manual"
GITHUB = "https://github.com/ticoteam/tico/blob/"
STOP = frozenset("a an and are as at be but by do does for from how i in is it my of on or our the to us was we what "
                 "when where which who why with you your can could should would please tell me about into new say says next long its want like help know".split())
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_lock = threading.Lock()
_cache = {"key": None, "pages": {}}


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


def _page(path):
    return page_text(path.stem, path.read_text(encoding="utf-8", errors="replace"))


def page_text(name, text, title=None):
    provided_title = title
    title, sections, current, in_code = title or name.replace("-", " ").title(), [], {"heading": "", "anchor": "", "lines": []}, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            if m.group(1) == "#" and not sections and not any(current["lines"]):
                title, current["heading"] = provided_title or m.group(2), m.group(2)
                current["anchor"] = slug(m.group(2))
                continue
            sections.append(current)
            current = {"heading": m.group(2), "anchor": slug(m.group(2)), "lines": []}
        else:
            current["lines"].append(line)
    sections.append(current)
    out = []
    for s in sections:
        body = " ".join(" ".join(s["lines"]).split())
        if s["heading"] or body:
            out.append({"heading": s["heading"], "anchor": s["anchor"], "body": body,
                        "words": set(_words(s["heading"] + " " + body)), "head_words": set(_words(s["heading"]))})
    return {"name": name, "title": title, "body": text, "sections": out, "title_words": set(_words(title + " " + name))}


def pages():
    """{name: page} for the running release; rebuilt when the version or a file changes."""
    version = releases.version()
    files = sorted(DOCS_DIR.glob("*.md")) if DOCS_DIR.is_dir() else []
    key = (version, str(DOCS_DIR), tuple((f.name, f.stat().st_mtime_ns) for f in files))
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, pages={f.stem: _page(f) for f in files})
        return _cache["pages"]


def _stem(token):
    return token[:-1] if len(token) > 4 and token.endswith("s") else token


def _hits(words, token):
    stem = _stem(token)
    return token in words or any(w.startswith(stem) for w in words)


def query_words(q, glossary=None):
    text = str(q).casefold()
    if glossary is None:
        glossary = pages().get("glossary", {}).get("body", "")
    mappings = re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", glossary, re.M)
    for old, new in sorted(mappings, key=lambda pair: -len(pair[0])):
        if old.casefold() in ("instead of", "---"):
            continue
        text = re.sub(r"\b" + re.escape(old.casefold()) + r"\b", new.casefold(), text)
    tokens = _words(text)
    return list(dict.fromkeys(t for t in tokens if t not in STOP)) or tokens


def query_weights(wanted):
    corpus = [page["title_words"] | set().union(*(section["words"] for section in page["sections"]))
              for page in pages().values()]
    return {term: 1 + math.log((len(corpus) + 1) / (1 + sum(_hits(words, term) for words in corpus)))
            for term in wanted}


def best_section(page, wanted, weights=None):
    weights = weights or query_weights(wanted)
    need = 1
    best = None
    for section in page["sections"]:
        words = section["words"] | page["title_words"]
        have = [t for t in wanted if _hits(words, t)]
        if len(have) < need:
            continue
        body = section["body"].casefold()
        score = sum(weights[t] * (10 + 5 * _hits(page["title_words"], t)
                                  + 15 * _hits(section["head_words"], t)
                                  + min(body.count(_stem(t)), 5)) for t in have)
        if best is None or score > best[0]:
            best = (score, section)
    return best


def _excerpt(body, tokens):
    low = body.casefold()
    at = min((i for i in (low.find(_stem(t)) for t in tokens) if i >= 0), default=0)
    if len(body) <= 1200:
        return body.strip()
    begin = max(0, at - 160)
    if begin:
        begin = body.find(" ", begin) + 1
    end = body.rfind(" ", begin, begin + 1200)
    return body[begin:end if end > begin else begin + 1200].strip()


def result(page, section, version, score):
    heading = section["heading"] if section["heading"] and section["heading"] != page["title"] else ""
    return {"type": "manual", "collection": "manual", "label": LABEL, "id": "manual:" + page["name"],
            "path": f"docs/{page['name']}.md", "title": page["title"] + (" > " + heading if heading else ""),
            "excerpt": "",
            "url": manual_url(version, page["name"], section["anchor"] if heading else ""), "version": version,
            "score": round(score, 3)}


def search(q, limit=20):
    """The best section of each page that answers the words, best first (a question in a sentence works: small words
    are ignored, and sections are ranked by their matching topic words)."""
    wanted = query_words(q)
    if not wanted:
        return []
    version, found = releases.version(), []
    weights = query_weights(wanted)
    for page in pages().values():
        best = best_section(page, wanted, weights)
        if best:
            row = result(page, best[1], version, best[0])
            row["section"] = best[1]["heading"]
            row["excerpt"] = _excerpt(best[1]["body"], wanted)
            found.append(row)
    found.sort(key=lambda r: (-r["score"], r["path"]))
    return found[:limit]


def _name(ref):
    ref = str(ref or "").strip()
    if ref.lower().startswith("manual:"):
        ref = ref[7:]
    ref = ref.removeprefix("docs/").strip("/")
    return re.sub(r"\.md$", "", ref, flags=re.I)


def listing():
    version = releases.version()
    return [{"id": "manual:" + p["name"], "path": f"docs/{p['name']}.md", "title": p["title"], "collection": "manual",
             "label": LABEL, "url": manual_url(version, p["name"]), "version": version} for p in pages().values()]


def read(ref):
    """One manual page in full, or None."""
    page = pages().get(_name(ref))
    if not page:
        return None
    version = releases.version()
    return {"id": "manual:" + page["name"], "path": f"docs/{page['name']}.md", "title": page["title"], "body": page["body"],
            "collection": "manual", "label": LABEL, "read_only": True, "version": version,
            "url": manual_url(version, page["name"])}
