"""Which Slack channels bots may read and post in: a list kept in the database and managed in the app.

Settings > Tools > Slack (an owner or an admin), `tico slack channel`, the MCP tools and BotOps all change this one
list. An entry names the channel (its name, its id, or both), whether bots may post there (on by default, like every
internal channel) and a note. Its readers are the bots that follow the channel's linked thread at `digest`: adding a
reader links a channel that has no link yet to a read-only thread (`slack_threads.add_readers`), and the hourly bot
digest wakes each reader with what the channel said. The bots' Slack routes (backend/slack_bots.py) read the list;
nobody edits a file. A channel shared outside the workspace is refused by the gateway whatever this list says: that
cannot be overridden here.
"""

import re

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H
from .store import Problem

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
        raise Problem(
            "slack_channel", "A channel name is lowercase letters, digits, dashes, dots and underscores.", 422
        )
    return cid, label


def entry_key(cid, name):
    return cid or "#" + name


def _entry(row, source):
    return {
        "key": row["key"],
        "id": row["id"],
        "name": row["name"],
        "channel": row["name"] and "#" + row["name"] or row["id"],
        "readers": list(row["readers"]),
        "link": row["link"],
        "post": bool(row["post"]),
        "note": row["note"],
        "source": source,
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by", ""),
    }


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


# ----------------------------------------------------------------------------- the stored list
# Each listed channel's link: by the reference it was asked with, and by the channel id once the gateway found it. A
# refused read-only link is still the channel's (adding a reader asks it again), so its state shows. CROSS JOIN keeps
# the list outer, so each entry looks its links up by index and the links table is never scanned.
LINKS_SQL = (
    "SELECT s.key, l.id, l.thread_id, l.state, l.reason, l.read_only, l.created_at FROM slack_channels s "
    "CROSS JOIN slack_thread_links l ON l.channel_ref=s.key "
    "AND l.state IN ('requested','active','suspended','refused') "
    "UNION ALL "
    "SELECT s.key, l.id, l.thread_id, l.state, l.reason, l.read_only, l.created_at FROM slack_channels s "
    "CROSS JOIN slack_thread_links l ON l.channel_id=s.channel_id AND l.state IN ('active','suspended') "
    "WHERE s.channel_id<>''"
)
# A channel's readers: the bots that follow its linked thread at digest.
READERS_SQL = (
    "SELECT thread_id, actor FROM thread_followers WHERE thread_id IN ({}) AND following=1 AND notify='digest' "
    "AND actor LIKE 'bot:%' ORDER BY created_at, rowid"
)


def _links(c):
    """{channel key: its link as the list shows it}: an open link before a refused one, the newest first."""
    best = {}
    for r in c.execute(LINKS_SQL):
        rank = (r["state"] != "refused", r["created_at"])
        if r["key"] not in best or rank > best[r["key"]][0]:
            best[r["key"]] = (
                rank,
                {
                    "id": r["id"],
                    "thread_id": r["thread_id"],
                    "state": r["state"],
                    "reason": r["reason"],
                    "read_only": bool(r["read_only"]),
                },
            )
    return {key: link for key, (_rank, link) in best.items()}


def _readers_of(c, thread_ids):
    out = {}
    for part in H.chunks(sorted(set(thread_ids))):
        for r in c.execute(READERS_SQL.format(",".join("?" * len(part))), tuple(part)):
            out.setdefault(r["thread_id"], []).append(r["actor"][4:])
    return out


def _stored(c):
    links = _links(c)
    readers = _readers_of(c, [link["thread_id"] for link in links.values()])
    out = []
    for r in c.execute("SELECT * FROM slack_channels ORDER BY name, channel_id"):
        link = links.get(r["key"])
        out.append(
            {
                "key": r["key"],
                "id": r["channel_id"],
                "name": r["name"],
                "readers": readers.get(link["thread_id"], []) if link else [],
                "link": link,
                "post": bool(r["post"]),
                "note": r["note"],
                "updated_at": r["updated_at"],
                "updated_by": r["updated_by"],
            }
        )
    return out


def _same(a, b):
    return bool((a["id"] and a["id"] == b["id"]) or (a["name"] and a["name"] == b["name"]))


def listing(c, settings):
    """Every channel on the list."""
    return [_entry(r, "app") for r in _stored(c)]


def channel_map(c, settings):
    """{channel id: {name, purpose, post, readers}} for the channels that have an id: what the gateway reads."""
    return {
        r["id"]: {
            "name": r["name"],
            "purpose": r["note"],
            "post": r["post"],
            "readers": r["readers"],
        }
        for r in listing(c, settings)
        if r["id"]
    }


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
    return [dict(r) for r in c.execute("SELECT key, name FROM slack_channels WHERE channel_id='' AND name<>''")]


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
        c.execute(
            "INSERT INTO slack_channels(key,channel_id,name,post,note,created_at,updated_at,updated_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (entry["key"], entry["id"], entry["name"], int(entry["post"]), entry["note"], now, now, who.actor),
        )
    else:
        c.execute(
            "UPDATE slack_channels SET channel_id=?,name=?,post=?,note=?,updated_at=?,updated_by=? WHERE key=?",
            (entry["id"], entry["name"], int(entry["post"]), entry["note"], now, who.actor, entry["key"]),
        )


def _check_readers(c, readers):
    """The bot ids for `readers` (each a bot's slug or id); refuses any that is no bot."""
    ids = [(H.resolve_bot(c, slug) or {}).get("id") for slug in readers]
    unknown = [slug for slug, bot_id in zip(readers, ids, strict=True) if not bot_id]
    if unknown:
        raise Problem(
            "slack_reader", "No bot named " + ", ".join(unknown) + ". Readers are bots' slugs (Settings > Bots).", 422
        )
    return ids


def add(c, settings, who, ref, readers=None, post=None, note=None, name=""):
    """Add a channel, or change the one already listed: readers are added to the ones it has (they follow its thread,
    see `slack_threads.add_readers`), `post` and `note` replace theirs when given."""
    from . import slack_threads as ST

    cid, label = parse_ref(ref, name)
    readers = _check_readers(c, _readers(readers))
    rows = listing(c, settings)
    have = _find(rows, cid, label)
    if have is None:
        entry = {
            "key": entry_key(cid, label),
            "id": cid,
            "name": label,
            "post": True if post is None else bool(post),
            "note": (note or "").strip(),
        }
        if any(r["key"] == entry["key"] for r in _stored(c)):  # the same channel under its other spelling
            raise Problem("slack_channel", "That channel is already listed.", 409)
        _save(c, who, entry, True)
        created = True
    else:
        entry = {
            "key": have["key"],
            "id": have["id"] or cid,
            "name": have["name"] or label,
            "post": have["post"] if post is None else bool(post),
            "note": have["note"] if note is None else note.strip(),
        }
        _save(c, who, entry, False)
        created = False
    if readers:
        ST.add_readers(c, who, entry["key"], entry["name"], entry["id"], readers)
    shown = next(r for r in listing(c, settings) if _same(entry, r))
    H.event(
        c,
        who.actor,
        "slack.channel_added" if created else "slack.channel_changed",
        entry["key"],
        {"readers": shown["readers"], "post": entry["post"]},
    )
    return {**shown, "created": created}


def remove(c, settings, who, ref, reader=""):
    """Take a channel off the list (its read-only link ends with it), or only one reader off it."""
    from . import slack_threads as ST

    cid, label = parse_ref(ref)
    rows = listing(c, settings)
    have = _find(rows, cid, label)
    if have is None:
        raise Problem("not_found", "That channel is not on the list.", 404)
    link = ST.channel_link(c, have["key"], have["id"])
    if reader:
        slug = _readers([reader])[0]
        bot_id = (H.resolve_bot(c, slug) or {}).get("id", slug)
        if bot_id not in have["readers"] or link is None:
            return {**have, "removed": False}
        ST.remove_reader(c, who, link, bot_id)
        H.event(c, who.actor, "slack.channel_reader_removed", have["key"], {"reader": bot_id})
        return {**next(r for r in listing(c, settings) if _same(have, r)), "removed": True}
    ST.drop_channel(c, who, link)
    c.execute("DELETE FROM slack_channels WHERE key=?", (have["key"],))
    H.event(c, who.actor, "slack.channel_removed", have["key"], {})
    return {**have, "removed": True, "deleted": True}


# ----------------------------------------------------------------------------- the API
class ChannelAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=100)
    readers: list[str] = Field(default_factory=list, max_length=200)
    post: bool | None = None
    note: str | None = Field(default=None, max_length=500)
    operation_id: str | None = None


class ChannelRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1, max_length=200)
    reader: str = Field(default="", max_length=100)
    operation_id: str | None = None


def install(app, settings, store, auth):
    def person(request, manage=False):
        who = request.state.identity
        auth.domain(who)
        if manage and not auth.bot_admin(who):
            raise Problem(
                "forbidden", "Only an owner or an admin changes which Slack channels bots may read and post in", 403
            )
        return who

    @app.get("/api/v2/slack/channels")
    def channels(request: Request):
        """The list bots' Slack reads and posts follow, and whether the gateway they go through is connected. Anyone
        signed in may read it (a bot's `connectors/slack.py channels` and `doctor`)."""
        from .slack_app import status

        who = person(request)
        with store.read() as c:
            return {
                "channels": listing(c, settings),
                "gateway": status(c)["state"],
                "can_manage": auth.bot_admin(who),
                "bots": [
                    {"id": b["id"], "name": b.get("name") or b["slug"]}
                    for b in H.bots(c)
                    if b.get("state") != "archived"
                ]
                if auth.bot_admin(who)
                else [],
            }

    @app.post("/api/v2/slack/channels")
    def channel_add(request: Request, body: ChannelAdd):
        who = person(request, manage=True)
        with store.transaction() as c:
            return add(c, settings, who, body.channel, body.readers, body.post, body.note, body.name)

    @app.post("/api/v2/slack/channels/remove")
    def channel_remove(request: Request, body: ChannelRemove):
        who = person(request, manage=True)
        with store.transaction() as c:
            return remove(c, settings, who, body.channel, body.reader)
