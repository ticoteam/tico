"""Product updates: the ones the owner posts and the packaged announcements, with each person's read state."""

import hashlib
import json
from functools import lru_cache

import yaml

from .store import H, encode

READ_KEY = "changelog.read"


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
        value = {"title": str(row["title"]).strip(), "bullets": bullets, "shipped_at": str(row.get("shipped_at") or "")}
        # Reordering the packaged announcements must not change which ones a person has read.
        value["id"] = "packaged-" + hashlib.sha256(encode(value).encode()).hexdigest()[:20]
        out.append(value)
    return tuple(out)


def products(c, settings):
    extra = stored_products(c)
    titles = {row.get("title") for row in extra}
    rows = extra + [row for row in packaged_products(settings) if row["title"] not in titles]
    rows.sort(key=lambda row: row.get("shipped_at") or "", reverse=True)
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
    c.execute(
        "INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (actor, READ_KEY, encode(sorted(read)), H.now()),
    )
    return summary(c, settings, actor)
