"""Tags, metadata and "made from" on files (docs/files.md): three general additions any file may carry.

- Tags: the company's own tags (backend/tags.py) attached to a file through `file_tags`, as tasks carry them. Only a
  tag that already exists and that the writer may read is attached; a file's tags show only to its readers, and only
  the tags each of them may read.
- Metadata: one JSON object of a team's own fields per file, up to META_BYTES, on the file rather than a version, so
  it is kept across versions. Its own table keeps the many `bot_files` reads small.
- Made from: `bot_files.parent_id`, the file this one was made from (`from` on a publish). The writer must read the
  parent, and a chain never loops or runs past PARENT_STEPS.

Whoever may write the file changes all three (page_links.may_write: the file's bot, its managers, the owner, a page's
writers); whoever may read it reads them.
"""

import json
from typing import Any

from fastapi import Request
from pydantic import Field

from . import page_links
from . import task_privacy as privacy
from .api_contract import api_v2
from .models import Contract
from .store import H, Problem

META_BYTES = 16 * 1024
META_KEY_MAX = 200
TAGS_MAX = 50  # tags on one file
PARENT_STEPS = 50


class FileTagChange(Contract):
    add: list[str] = Field(default=[], max_length=TAGS_MAX)
    remove: list[str] = Field(default=[], max_length=TAGS_MAX)


class FileMetaChange(Contract):
    # A key set to null is removed, as `remove` removes it.
    set: dict[str, Any] = Field(default={})
    remove: list[str] = Field(default=[], max_length=500)


FILE_TAGS_SQL = (
    "SELECT ft.file_id AS file_id,tags.* FROM file_tags ft JOIN tags ON tags.id=ft.tag_id "
    "WHERE ft.file_id IN ({marks}) ORDER BY tags.key"
)


def tags_of(c, who, ids):
    """{file id: [{id, key, label}]} for `ids` (files the caller already may read), each tag one `who` may read: one
    query for the files, one readability pass over their distinct tags."""
    found, by_tag = {fid: [] for fid in ids}, {}
    for part in H.chunks(list(found)):
        for row in c.execute(FILE_TAGS_SQL.format(marks=",".join("?" * len(part))), part):
            value = dict(row)
            fid = value.pop("file_id")
            by_tag.setdefault(value["id"], (H._tag_view(value), []))[1].append(fid)
    readable = {row["id"] for row in privacy.tags_readable(c, who, [tag for tag, _ in by_tag.values()])}
    for tag_id, (tag, files) in by_tag.items():
        if tag_id in readable:
            for fid in files:
                found[fid].append({"id": tag["id"], "key": tag["key"], "label": tag["label"]})
    return found


def find_tag(c, key):
    """The tag named `key`: its id, its key, or its key as a label is written (lower case, hyphens)."""
    key = str(key or "").strip()
    normal = H._labels([key])
    return (H.tag(c, key) if key else None) or (H.tag(c, normal[0]) if normal else None)


def tag_id(c, who, key):
    """The id of the tag named `key` when `who` may read it, or None: the Library's `tag=` filter."""
    row = find_tag(c, key)
    return row["id"] if row and privacy.tag_readable(c, who, row) else None


def metadata_of(c, fid):
    row = c.execute("SELECT data FROM file_metadata WHERE file_id=?", (fid,)).fetchone()
    return H._json(row["data"], {}) if row else {}


def parent_of(c, auth, who, row):
    """{id, title} of the file `row` was made from, when `who` may read it; else None, which says nothing of it."""
    from .files import file_visible

    pid = row["parent_id"] if "parent_id" in row.keys() else None
    if not pid:
        return None
    parent = c.execute("SELECT * FROM bot_files WHERE id=?", (pid,)).fetchone()
    if not parent or parent["state"] == "deleted" or not file_visible(c, auth, who, parent, {}):
        return None
    return {"id": parent["id"], "title": parent["title"]}


def details(c, auth, who, row):
    """What a file's details add (Files.info): its tags, metadata, parent and whether the reader may change them."""
    return {
        "tags": tags_of(c, who, [row["id"]])[row["id"]],
        "metadata": metadata_of(c, row["id"]),
        "parent": parent_of(c, auth, who, row),
        "may_edit": who.role in ("owner", "human", "bot") and page_links.may_write(c, auth, who, row),
    }


def editable(c, auth, who, fid):
    """The file `fid` for a change to its tags, metadata or parent: 404 unless `who` reads it, 403 unless they write it."""
    from .files import file_visible

    row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
    if not row or row["state"] == "deleted" or not file_visible(c, auth, who, row, {}):
        raise Problem("not_found", "File not found", 404)
    if who.role not in ("owner", "human", "bot") or not page_links.may_write(c, auth, who, row):
        raise Problem("forbidden", "Only someone who may write this file changes its tags or metadata", 403)
    H._writer(c, who.actor, task_id=row["task_id"])
    return row


def set_parent(c, auth, who, row, pid):
    """Say `row` was made from file `pid` (None clears it). `who` must read the parent; a loop or a chain longer than
    PARENT_STEPS is refused."""
    from .files import file_visible

    if pid:
        parent = c.execute("SELECT * FROM bot_files WHERE id=?", (pid,)).fetchone()
        if not parent or parent["state"] == "deleted" or not file_visible(c, auth, who, parent, {}):
            raise Problem("not_found", "No file " + str(pid) + " you can read to make this from", 404)
        at, steps = parent["id"], 0
        while at:
            if at == row["id"]:
                raise Problem("validation", "A file cannot be made from itself or from a file made from it", 422)
            steps += 1
            if steps > PARENT_STEPS:
                raise Problem("validation", f"Made from goes back at most {PARENT_STEPS} files", 422)
            found = c.execute("SELECT parent_id FROM bot_files WHERE id=?", (at,)).fetchone()
            at = found["parent_id"] if found else None
        pid = parent["id"]
    if (row["parent_id"] if "parent_id" in row.keys() else None) == (pid or None):
        return
    c.execute("UPDATE bot_files SET parent_id=? WHERE id=?", (pid or None, row["id"]))
    # The event names no parent: the company audit trail may be read by people who cannot read the parent.
    H.event(c, who.actor, "file.parent", row["id"], {"set": bool(pid)})


def change_tags(c, auth, files, who, fid, body):
    row = editable(c, auth, who, fid)
    wanted = []
    for key, attach in [(k, True) for k in body.add] + [(k, False) for k in body.remove]:
        tag = find_tag(c, key)
        if not tag or not privacy.tag_readable(c, who, tag):
            raise Problem("not_found", f"No tag {key}; make it with tico tag create first", 404)
        if attach and tag["is_template"]:
            raise Problem("validation", f"{tag['key']} is a template; make an instance to tag a file", 422)
        wanted.append((tag, attach))
    # Activity names tags by id: a file's readers read its activity, and a tag's key is only for those who read the tag.
    added, removed = [], []
    for tag, attach in wanted:
        if attach:
            if c.execute(
                "INSERT OR IGNORE INTO file_tags(file_id,tag_id) VALUES(?,?)", (row["id"], tag["id"])
            ).rowcount:
                added.append(tag["id"])
        elif c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id=?", (row["id"], tag["id"])).rowcount:
            removed.append(tag["id"])
    if c.execute("SELECT count(*) FROM file_tags WHERE file_id=?", (row["id"],)).fetchone()[0] > TAGS_MAX:
        raise Problem("validation", f"A file carries at most {TAGS_MAX} tags", 422)
    if added or removed:
        detail = {"added": added, "removed": removed}
        files.activity(c, row["id"], who.actor, "tagged", detail=detail)
        H.event(c, who.actor, "file.tag", row["id"], detail)
    return {"file": row["id"], "tags": tags_of(c, who, [row["id"]])[row["id"]]}


def change_metadata(c, auth, files, who, fid, body):
    row = editable(c, auth, who, fid)
    data = metadata_of(c, row["id"])
    for key, value in body.set.items():
        if not key.strip() or len(key) > META_KEY_MAX:
            raise Problem("validation", f"A metadata key is 1 to {META_KEY_MAX} characters", 422)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    for key in body.remove:
        data.pop(key, None)
    try:
        packed = pack(data)
    except ValueError:
        raise Problem("validation", "Metadata values are JSON: no NaN or Infinity", 422) from None
    if len(packed.encode()) > META_BYTES:
        raise Problem("validation", f"A file's metadata is at most {META_BYTES // 1024} KB", 422)
    before = metadata_of(c, row["id"])
    if packed == pack(before):
        return {"file": row["id"], "metadata": data}
    now = H.now()
    if data:
        c.execute(
            "INSERT INTO file_metadata(file_id,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(file_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
            (row["id"], packed, now),
        )
    else:
        c.execute("DELETE FROM file_metadata WHERE file_id=?", (row["id"],))
    changed = sorted(
        k for k in set(data) | set(before) if k not in data or k not in before or pack(data[k]) != pack(before[k])
    )
    files.activity(c, row["id"], who.actor, "metadata_changed", detail={"keys": changed}, now=now)
    H.event(c, who.actor, "file.metadata", row["id"], {"keys": changed})
    return {"file": row["id"], "metadata": data}


def pack(value):
    """Canonical JSON: true and 1 differ here, as Python's == does not tell them apart."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def purge(c, fid):
    """A file deleted for good (file_library.purge_rows) keeps no tags, metadata or parent, and its children no link."""
    c.execute("DELETE FROM file_tags WHERE file_id=?", (fid,))
    c.execute("DELETE FROM file_metadata WHERE file_id=?", (fid,))
    c.execute("UPDATE bot_files SET parent_id=NULL WHERE parent_id=? OR id=?", (fid, fid))


def install(app, store, auth, files):
    def read(who, fid):
        from .files import file_visible

        with store.read() as c:
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            if not row or row["state"] == "deleted" or not file_visible(c, auth, who, row, {}):
                raise Problem("not_found", "File not found", 404)
            return {"file": row["id"], "metadata": metadata_of(c, row["id"])}

    @app.post(
        "/api/v2/files/{fid}/tags",
        **api_v2(
            "Files",
            "tagFile",
            "Attach or remove the company's existing tags on a file (anyone who may write the file)",
            "FileTags",
        ),
    )
    def tag(request: Request, fid: str, body: FileTagChange):
        who = request.state.identity
        if not body.add and not body.remove:
            raise Problem("validation", "Say which tags to add or remove", 422)
        return files.write(
            request, who, {"id": fid, **body.model_dump()}, lambda c: change_tags(c, auth, files, who, fid, body)
        )

    @app.get(
        "/api/v2/files/{fid}/metadata",
        **api_v2(
            "Files", "getFileMetadata", "A file's metadata: one JSON object of a team's own fields", "FileMetadata"
        ),
    )
    def metadata(request: Request, fid: str):
        return read(request.state.identity, fid)

    @app.patch(
        "/api/v2/files/{fid}/metadata",
        **api_v2(
            "Files",
            "editFileMetadata",
            "Set or remove keys of a file's metadata (anyone who may write the file); null removes a key; at most 16 KB",
            "FileMetadata",
        ),
    )
    def edit_metadata(request: Request, fid: str, body: FileMetaChange):
        who = request.state.identity
        if not body.set and not body.remove:
            raise Problem("validation", "Say which keys to set or remove", 422)
        return files.write(
            request, who, {"id": fid, **body.model_dump()}, lambda c: change_metadata(c, auth, files, who, fid, body)
        )
