"""Which Slack channels bots may read and post in: a list kept in the database and managed in the app.

Settings > Tools > Slack (an owner or an admin), `hub slack channel`, the MCP tools and BotOps all change this one
list. An entry names the channel (its name, its id, or both), the bots that read it, whether bots may post there
(on by default, like every internal channel) and a note. The Slack gateway's readers' pass and the `connectors/slack.py`
connector read it; nobody edits a file.

An install that still has `registry/slack-channels.yaml` keeps working: its channels are listed too, marked as coming
from the file, until an owner or admin imports them once (`POST /api/v2/slack/channels/import`). After the import the
file is ignored, so removing a channel here removes it for good. A channel shared outside the workspace is refused by
the gateway and the connector whatever this list says: neither can be overridden here.
"""

import json
import re
from pathlib import Path

import yaml
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H
from .store import Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_channels(
 key TEXT PRIMARY KEY, channel_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
 readers TEXT NOT NULL DEFAULT '[]', post INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
 digest_hours REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '');
"""
FILE = "slack-channels.yaml"
IMPORTED = "slack_channels_imported"
ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
HOW = "Give a channel name like #customer_success, or its id like C0123456789."


# ----------------------------------------------------------------------------- pure helpers
def parse_ref(ref, name=""):
    """`#customer_success`, `customer_success`, `C0123456789` or Slack's `<#C0123456789|customer_success>` -> (channel id, name);
    one of them may be empty."""
    text = str(ref or "").strip()
    markup = re.fullmatch(r"<#([A-Z0-9]+)(?:\|([^>]*))?>", text)
    if markup:
        text, name = markup.group(1), name or markup.group(2) or ""
    text = text.lstrip("#")
    label = str(name or "").strip().lstrip("#").lower()
    if ID_RE.match(text):
        cid = text
    elif NAME_RE.match(text.lower()):
        cid, label = "", text.lower()
    else:
        raise Problem("slack_channel", HOW, 422)
    if label and not NAME_RE.match(label):
        raise Problem("slack_channel", "A channel name is lowercase letters, digits, dashes, dots and underscores.", 422)
    return cid, label


def entry_key(cid, name):
    return cid or "#" + name


def _entry(row, source):
    return {"key": row["key"], "id": row["id"], "name": row["name"], "channel": row["name"] and "#" + row["name"] or row["id"],
            "readers": list(row["readers"]), "post": bool(row["post"]), "note": row["note"],
            "digest_hours": row["digest_hours"], "source": source,
            "updated_at": row.get("updated_at"), "updated_by": row.get("updated_by", "")}


def _readers(value):
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    seen = []
    for item in value or []:
        slug = str(item or "").strip().lstrip("#").lower()
        if slug.startswith("bot:"):
            slug = slug[4:]
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def file_rows(registry_dir):
    """The channels in the old `registry/slack-channels.yaml`, normalised; [] when there is none."""
    try:
        data = yaml.safe_load((Path(registry_dir) / FILE).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    rows = data.get("channels") if isinstance(data, dict) else data
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip().lstrip("#").lower()
        if not cid and not name:
            continue
        try:
            hours = max(0.0, float(raw.get("digest_hours") or 0))
        except (TypeError, ValueError):
            hours = 0.0
        out.append({"key": entry_key(cid, name), "id": cid, "name": name, "readers": _readers(raw.get("readers")),
                    "post": raw.get("post") is not False, "note": str(raw.get("purpose") or raw.get("note") or ""),
                    "digest_hours": hours})
    return out


def file_channels(registry_dir):
    """The shape the gateway used to read from the file: {channel id: {name, purpose, post, digest_hours, readers}}."""
    return {row["id"]: {"name": row["name"], "purpose": row["note"], "post": row["post"],
                        "digest_hours": row["digest_hours"], "readers": row["readers"]}
            for row in file_rows(registry_dir) if row["id"]}


# ----------------------------------------------------------------------------- the stored list
def _stored(c):
    out = []
    for r in c.execute("SELECT * FROM slack_channels ORDER BY name, channel_id"):
        out.append({"key": r["key"], "id": r["channel_id"], "name": r["name"], "readers": H._json(r["readers"], []),
                    "post": bool(r["post"]), "note": r["note"], "digest_hours": r["digest_hours"],
                    "updated_at": r["updated_at"], "updated_by": r["updated_by"]})
    return out


def imported(c):
    return bool(c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (IMPORTED,)).fetchone())


def _same(a, b):
    return bool((a["id"] and a["id"] == b["id"]) or (a["name"] and a["name"] == b["name"]))


def listing(c, settings):
    """Every channel that counts, with where it came from: `app` (stored here) or `file` (not imported yet)."""
    rows = [_entry(r, "app") for r in _stored(c)]
    if not imported(c):
        for raw in file_rows(settings.registry_dir):
            if not any(_same(raw, r) for r in rows):
                rows.append(_entry(raw, "file"))
    return rows


def channel_map(c, settings):
    """{channel id: {name, purpose, post, digest_hours, readers}} for the channels that have an id: what the gateway reads."""
    return {r["id"]: {"name": r["name"], "purpose": r["note"], "post": r["post"], "digest_hours": r["digest_hours"],
                      "readers": r["readers"]} for r in listing(c, settings) if r["id"]}


def lookup(c, settings, channel_id, name=""):
    """The entry for a channel seen on Slack, by id and else by name, and whether only its name matched."""
    rows = listing(c, settings)
    for r in rows:
        if r["id"] and r["id"] == channel_id:
            return r, False
    want = str(name or "").lstrip("#").lower()
    for r in rows:
        if want and not r["id"] and r["name"] == want:
            return r, True
    return None, False


def unresolved(c):
    """Stored entries that name a channel but not its id yet (the gateway asks Slack for it)."""
    return [r for r in _stored(c) if not r["id"] and r["name"]]


def fill_ids(c, ids):
    """`ids` = {channel name: id}: complete the stored entries that only had the name."""
    done = 0
    for r in unresolved(c):
        cid = ids.get(r["name"])
        if cid:
            c.execute("UPDATE slack_channels SET channel_id=? WHERE key=?", (cid, r["key"]))
            done += 1
    return done


def _find(rows, cid, name):
    probe = {"id": cid, "name": name}
    return next((r for r in rows if _same(probe, r)), None)


def _save(c, who, entry, created):
    now = H.now()
    if created:
        c.execute("INSERT INTO slack_channels(key,channel_id,name,readers,post,note,digest_hours,created_at,updated_at,updated_by) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (entry["key"], entry["id"], entry["name"], _dump(entry["readers"]),
                                                   int(entry["post"]), entry["note"], entry["digest_hours"], now, now, who.actor))
    else:
        c.execute("UPDATE slack_channels SET channel_id=?,name=?,readers=?,post=?,note=?,digest_hours=?,updated_at=?,updated_by=? WHERE key=?",
                  (entry["id"], entry["name"], _dump(entry["readers"]), int(entry["post"]), entry["note"], entry["digest_hours"],
                   now, who.actor, entry["key"]))


def _dump(value):
    return json.dumps(value, separators=(",", ":"))


def _check_readers(c, readers):
    unknown = [slug for slug in readers if not H.bot(c, slug)]
    if unknown:
        raise Problem("slack_reader", "No bot named " + ", ".join(unknown) + ". Readers are bots' slugs (Settings > Bots).", 422)


def add(c, settings, who, ref, readers=None, post=None, note=None, digest_hours=None, name=""):
    """Add a channel, or change the one already listed: readers are added to the ones it has, `post`, `note` and
    `digest_hours` replace theirs when given. A channel that only the old file lists is stored now."""
    cid, label = parse_ref(ref, name)
    readers = _readers(readers)
    _check_readers(c, readers)
    rows = listing(c, settings)
    have = _find(rows, cid, label)
    if have is None:
        entry = {"key": entry_key(cid, label), "id": cid, "name": label, "readers": readers,
                 "post": True if post is None else bool(post), "note": (note or "").strip(),
                 "digest_hours": max(0.0, float(digest_hours or 0))}
        if any(r["key"] == entry["key"] for r in _stored(c)):       # the same channel under its other spelling
            raise Problem("slack_channel", "That channel is already listed.", 409)
        _save(c, who, entry, True)
        created = True
    else:
        entry = {"key": have["key"], "id": have["id"] or cid, "name": have["name"] or label,
                 "readers": have["readers"] + [r for r in readers if r not in have["readers"]],
                 "post": have["post"] if post is None else bool(post),
                 "note": have["note"] if note is None else note.strip(),
                 "digest_hours": have["digest_hours"] if digest_hours is None else max(0.0, float(digest_hours))}
        stored = any(r["key"] == have["key"] for r in _stored(c))
        if stored:
            _save(c, who, entry, False)
        else:
            entry["key"] = entry_key(entry["id"], entry["name"])
            _save(c, who, entry, True)
        created = False
    H.event(c, who.actor, "slack.channel_added" if created else "slack.channel_changed", entry["key"],
            {"readers": entry["readers"], "post": entry["post"]})
    return {**next(r for r in listing(c, settings) if _same(entry, r)), "created": created}


def remove(c, settings, who, ref, reader=""):
    """Take a channel off the list, or only one reader off it."""
    cid, label = parse_ref(ref)
    rows = listing(c, settings)
    have = _find(rows, cid, label)
    if have is None:
        raise Problem("not_found", "That channel is not on the list.", 404)
    if have["source"] == "file":
        raise Problem("slack_file", "That channel is only in registry/slack-channels.yaml. Import the file first "
                      "(Settings > Tools > Slack, or `hub slack channel import`), then change it here.", 409)
    if reader:
        slug = _readers([reader])[0]
        if slug not in have["readers"]:
            return {**have, "removed": False}
        have["readers"] = [r for r in have["readers"] if r != slug]
        _save(c, who, {**have}, False)
        H.event(c, who.actor, "slack.channel_reader_removed", have["key"], {"reader": slug})
        return {**next(r for r in listing(c, settings) if _same(have, r)), "removed": True}
    c.execute("DELETE FROM slack_channels WHERE key=?", (have["key"],))
    H.event(c, who.actor, "slack.channel_removed", have["key"], {})
    return {**have, "removed": True, "deleted": True}


def import_file(c, settings, who):
    """Store the channels the old file lists (the ones already stored win) and stop reading the file."""
    if imported(c):
        raise Problem("slack_file", "The file was imported already; the list here is the one that counts.", 409)
    rows = file_rows(settings.registry_dir)
    if not rows:
        raise Problem("not_found", "There is no registry/slack-channels.yaml with channels in it.", 404)
    stored = _stored(c)
    added = skipped = 0
    for raw in rows:
        if any(_same(raw, r) for r in stored):
            skipped += 1
            continue
        _save(c, who, raw, True)
        stored.append(raw)
        added += 1
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (IMPORTED, _dump({"at": H.now(), "by": who.actor, "added": added})))
    H.event(c, who.actor, "slack.channels_imported", "", {"added": added, "skipped": skipped})
    return {"imported": added, "skipped": skipped}


# ----------------------------------------------------------------------------- the API
class ChannelAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=100)
    readers: list[str] = Field(default_factory=list, max_length=200)
    post: bool | None = None
    note: str | None = Field(default=None, max_length=500)
    digest_hours: float | None = Field(default=None, ge=0, le=720)
    operation_id: str | None = None


class ChannelRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1, max_length=200)
    reader: str = Field(default="", max_length=100)
    operation_id: str | None = None


class Nothing(BaseModel):
    model_config = ConfigDict(extra="forbid")


def install(app, settings, store, auth):
    def person(request, manage=False):
        who = request.state.identity
        auth.domain(who)
        if manage and not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin changes which Slack channels bots may read and post in", 403)
        return who

    @app.get("/api/v2/slack/channels")
    def channels(request: Request):
        """The list bots' Slack reads and posts follow. Anyone signed in may read it (a bot reads it for its own checks)."""
        who = person(request)
        with store.read() as c:
            present = file_rows(settings.registry_dir)
            done = imported(c)
            return {"channels": listing(c, settings), "can_manage": auth.bot_admin(who),
                    "registry_file": {"present": bool(present), "channels": len(present), "imported": done},
                    "bots": [{"id": b["slug"], "name": b.get("name") or b["slug"]} for b in H.bots(c) if b.get("state") != "archived"]
                    if auth.bot_admin(who) else []}

    @app.post("/api/v2/slack/channels")
    def channel_add(request: Request, body: ChannelAdd):
        who = person(request, manage=True)
        with store.transaction() as c:
            return add(c, settings, who, body.channel, body.readers, body.post, body.note, body.digest_hours, body.name)

    @app.post("/api/v2/slack/channels/remove")
    def channel_remove(request: Request, body: ChannelRemove):
        who = person(request, manage=True)
        with store.transaction() as c:
            return remove(c, settings, who, body.channel, body.reader)

    @app.post("/api/v2/slack/channels/import")
    def channel_import(request: Request, body: Nothing):
        who = person(request, manage=True)
        with store.transaction() as c:
            return import_file(c, settings, who)
