"""Shipped product history, available from the installed source without Git or a network call."""
import hashlib
import json
import re
from functools import lru_cache

import yaml

from . import releases
from .store import H, encode

READ_KEY = "changelog.read"


@lru_cache(maxsize=4)
def parse_notes(text):
    rows, row, bullet = [], None, None
    for line in text.splitlines():
        if line.startswith("## "):
            match = re.fullmatch(r"## \[?([^\] ]+)\]?\s+-\s+(\d{4}-\d{2}-\d{2})\s*", line)
            row = None
            bullet = None
            if match and releases.parse(match[1]):
                version = match[1].lstrip("v")
                row = {"id": "release-" + version, "version": version, "title": "Tico v" + version,
                       "shipped_at": match[2] + "T00:00:00Z", "bullets": [], "source": "release",
                       "url": "https://github.com/ticoteam/tico/releases/tag/v" + version}
                rows.append(row)
            continue
        if row is None:
            continue
        match = re.match(r"^[-*]\s+(.+)", line)
        if match:
            row["bullets"].append(match[1].strip())
            bullet = len(row["bullets"]) - 1
        elif line[:1].isspace() and line.strip() and bullet is not None:
            row["bullets"][bullet] += " " + line.strip()
        else:
            bullet = None
    return [row for row in rows if row["bullets"]]


def release_entries():
    path = releases.ROOT / "CHANGELOG.md"
    try:
        stat = path.stat()
        rows = _notes(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        rows = []
    current = releases.parse(releases.version())
    # A rollback or pinned installation never announces features from a later release.
    return [dict(row) for row in rows if current is None or releases.parse(row["version"]) <= current]


@lru_cache(maxsize=2)
def _notes(path, mtime_ns, size):
    with open(path) as handle:
        return parse_notes(handle.read())


def stored_products(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key='product_updates'").fetchone()
    return json.loads(row[0]) if row else []


def packaged_products(settings):
    # Parsed once per version of the file: /api/v2/config reads it for every tab and computer.
    path = settings.registry_dir / "product-updates.yaml"
    try:
        stat = path.stat()
    except OSError:
        return []
    return [dict(row) for row in _packaged(str(path), stat.st_mtime_ns, stat.st_size)]


@lru_cache(maxsize=4)
def _packaged(path, mtime_ns, size):
    try:
        with open(path) as handle:
            rows = yaml.safe_load(handle.read()) or []
    except (OSError, yaml.YAMLError):
        return ()
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not str(row.get("title") or "").strip():
            continue
        bullets = [str(item).strip() for item in (row.get("bullets") or []) if str(item).strip()]
        if not bullets:
            continue
        value = {"title": str(row["title"]).strip(), "bullets": bullets,
                 "shipped_at": str(row.get("shipped_at") or "")}
        # Reordering the packaged announcements must not change which ones a person has read.
        value["id"] = "packaged-" + hashlib.sha256(encode(value).encode()).hexdigest()[:20]
        out.append(value)
    return tuple(out)


def products(c, settings):
    extra = stored_products(c)
    titles = {row.get("title") for row in extra}
    rows = release_entries() + extra + [row for row in packaged_products(settings) if row["title"] not in titles]
    rows.sort(key=lambda row: (row.get("shipped_at") or "",
                              releases.parse(row.get("version")) or (-1, -1, -1, (0,))), reverse=True)
    return [{**row, "area": "Product", "kind": "product"} for row in rows]


def read_ids(c, actor):
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (actor, READ_KEY)).fetchone()
    value = json.loads(row[0]) if row else None
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def summary(c, settings, actor):
    read = read_ids(c, actor)
    return {"unread_count": sum(row["id"] not in read for row in products(c, settings))}


def mark_read(c, settings, actor, ids):
    known = {row["id"] for row in products(c, settings)}
    # Merge acknowledgements from concurrent tabs; a later announcement is never acknowledged implicitly.
    read = read_ids(c, actor) | (set(ids) & known)
    c.execute("INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
              "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (actor, READ_KEY, encode(sorted(read)), H.now()))
    return summary(c, settings, actor)
