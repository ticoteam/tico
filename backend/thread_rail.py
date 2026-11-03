"""Pins, the thread rail, Browse and Catch up (docs/dev/threads-contract.md §10.1).

A reader pins a root for themselves (`thread_pins`, pinned 1) and unpins it for themselves over a manager's pin for
everyone (`conversations.pinned_for_everyone_at`, pinned 0). Their own row wins; without one the pin for everyone counts.

The rail is the threads a reader is in, by title: company, team and members roots and group DMs (two or more others),
never a 1:1 DM, a Home, a task's thread or a meeting. `pinned` holds the ones pinned for them, `threads` the rest, paged.
Browse lists the company and team roots a reader may read and is not in yet; following one (PUT .../follow) puts it in
the rail. Catch up lists each root with something new for the reader, and each open Needs attention item, with the new
messages in tree order.

Every row passes the same read check as the sidebar (`threads.readable_sql`, then `thread_readable`), so a thread hidden
from the reader never shows, nor does its existence. Each list is paged and byte-capped.
"""
import base64
import json

from . import threads as T
from .store import H, Problem

RAIL_LIMIT = 100
PINNED_MAX = 200
BROWSE_LIMIT = 50
CATCH_UP_LIMIT = 20
CATCH_UP_MAX_LIMIT = 50
CATCH_UP_MESSAGES = 20          # an item shows this many new messages; `more` counts the rest
CATCH_UP_READ = 400             # new messages read for one item, before the per-message check
CATCH_UP_SCAN = 1000            # roots one Catch up looks at
CATCH_UP_SHOWN = 99             # `count` stops here (the sidebar badge)
ATTENTION_KINDS = ("thread_mention", "thread_ask", "thread_dm", "thread_held")
MENTION_KINDS = ("thread_mention", "thread_ask")
EVERYONE_REFUSED = ("home", "task", "meeting")


# ----------------------------------------------------------------------------- shared SQL
def _audience():
    return f"COALESCE(r.audience,{T._derived_audience('r')})"


def _is_dm():
    return f"{_audience()} IN ('personal','participants')"


def _group_dm():
    """A DM root that may hold two or more others: Python confirms each (`_others`)."""
    return (f"({_is_dm()} AND ((SELECT COUNT(*) FROM thread_members gm WHERE gm.thread_id=r.id)>=3 OR "
            "(CASE WHEN json_valid(r.participants) THEN json_array_length(r.participants) ELSE 0 END)>=3))")


def _in(names_sql):
    """The reader is in root `r`: its starter, a member (or in a member group), following it or any thread under it,
    or one of its participants. `:me` is the reader."""
    return (f"(r.owner_id=:me OR EXISTS (SELECT 1 FROM thread_members im WHERE im.thread_id=r.id AND im.actor IN "
            f"({names_sql})) OR r.id IN (SELECT COALESCE(s.root_id,s.id) FROM thread_followers f JOIN conversations s ON "
            f"s.id=f.thread_id WHERE f.actor=:me AND f.following=1 AND {T._SUBTREE_OPEN}) OR EXISTS (SELECT 1 FROM "
            f"{T._PARTICIPANTS.format(r='r')} WHERE value=:me))")


PINNED_SQL = ("(EXISTS (SELECT 1 FROM thread_pins p WHERE p.thread_id=r.id AND p.actor=:me AND p.pinned=1) OR "
              "(r.pinned_for_everyone_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM thread_pins p WHERE p.thread_id=r.id "
              "AND p.actor=:me AND p.pinned=0)))")
ROOT_OPEN = "r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL"
ACTIVITY = (f"COALESCE((SELECT MAX(s.last_seq) FROM conversations s WHERE s.root_id=r.id AND {T._SUBTREE_OPEN}),"
            "r.last_seq,0)")


def _names(v, c):
    names = v.member_names(c)
    return {f"name{i}": name for i, name in enumerate(names)}, ",".join(f":name{i}" for i in range(len(names)))


def _encode(position):
    return base64.urlsafe_b64encode(json.dumps(position, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(cursor, size):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4))
        position = json.loads(raw)
    except (ValueError, TypeError):
        raise Problem("cursor", "Unknown cursor", 422) from None
    if not isinstance(position, list) or len(position) != size:
        raise Problem("cursor", "Unknown cursor", 422)
    return position


def _limit(limit, default, most):
    try:
        return max(1, min(int(limit or default), most))
    except (TypeError, ValueError):
        raise Problem("limit", "limit is a number", 422) from None


def _viewer(v):
    return T.viewer(None, v) if not isinstance(v, T.Viewer) else v


def _others(c, v, root, members):
    return [a for a in T._dm_members(c, root, rows=members) if a != v.actor]


def _fits(rows, budget):
    """The rows that fit `budget` bytes, in order, and whether any were left out. The first row always goes, so a page
    over the budget still moves its cursor on."""
    out = []
    for row in rows:
        budget -= len(json.dumps(row, default=str))
        if budget < 0 and out:
            return out, True
        out.append(row)
    return out, False


def _views(c, v, rows):
    """Each root row as a list shows it: its whole subtree's unread and activity, its DM title, the reader's pin."""
    ids = [r["id"] for r in rows]
    if v._members is None:
        v._members = {}
    v._members.update(T._prefetch_members(c, v, ids))
    summaries = T.subtree_summaries(c, v, ids)
    followers = T._followers_of(c, v, ids)
    dms = T.dm_with(c, v, rows)
    pins = T.pin_rows(c, v.actor, ids)
    out = {}
    for r in rows:
        summary = summaries.get(r["id"]) or {"unread": 0, "activity": False}
        item = T.view(c, v, r, summary=summary, dm=dms.get(r["id"]), follower=followers.get(r["id"], {}), pins=pins)
        if summary.get("last_message_at"):
            item["last_message_at"] = summary["last_message_at"]
        item["activity_seq"] = int(summary.get("activity_seq") or r.get("last_seq") or 0)
        out[r["id"]] = item
    return out


# ----------------------------------------------------------------------------- pins
def _pin_root(c, principal, thread_id):
    actor, v, row = T._require_thread(c, principal, thread_id)
    if row.get("parent_id"):
        raise Problem("pin_root", "Only a root thread is pinned: pin the thread it is under", 422)
    return actor, v, row


def _pin_audit(c, actor, root_id, before, after, action):
    T._event(c, actor, action, root_id, {"pinned": after})
    T._audit(c, actor, "thread:" + root_id, {"pinned": before}, {"pinned": after})


def pin(c, principal, thread_id):
    """Pin a root thread for the caller. Reading it is enough; a thread they may not read is a 404."""
    actor, v, row = _pin_root(c, principal, thread_id)
    own = T.pin_rows(c, actor, [row["id"]]).get(row["id"])
    before = T.pinned(row.get("pinned_for_everyone_at"), own)
    now = T._now()
    c.execute("INSERT INTO thread_pins(id,thread_id,actor,pinned,created_at,updated_at) VALUES(?,?,?,1,?,?) "
              "ON CONFLICT(thread_id,actor) DO UPDATE SET pinned=1, updated_at=excluded.updated_at",
              (T._new_id(), row["id"], actor, now, now))
    _pin_audit(c, actor, row["id"], before, True, "thread.pinned")
    return T.view(c, principal, T.thread_row(c, row["id"]))


def unpin(c, principal, thread_id):
    """Unpin a root thread for the caller: over a pin for everyone, a row that unpins it for them alone."""
    actor, v, row = _pin_root(c, principal, thread_id)
    own = T.pin_rows(c, actor, [row["id"]]).get(row["id"])
    before = T.pinned(row.get("pinned_for_everyone_at"), own)
    now = T._now()
    if row.get("pinned_for_everyone_at"):
        c.execute("INSERT INTO thread_pins(id,thread_id,actor,pinned,created_at,updated_at) VALUES(?,?,?,0,?,?) "
                  "ON CONFLICT(thread_id,actor) DO UPDATE SET pinned=0, updated_at=excluded.updated_at",
                  (T._new_id(), row["id"], actor, now, now))
    else:
        c.execute("DELETE FROM thread_pins WHERE thread_id=? AND actor=?", (row["id"], actor))
    _pin_audit(c, actor, row["id"], before, False, "thread.unpinned")
    return T.view(c, principal, T.thread_row(c, row["id"]))


def set_pinned_for_everyone(c, principal, thread_id, value):
    """Pin a root for everyone who reads it, or take that pin off: a manager's call (as a rename is). Never on a 1:1 DM,
    a Home, a task's thread or a meeting. Taking it off drops the rows that unpinned it for one reader."""
    actor, v, row = _pin_root(c, principal, thread_id)
    if v is not None and not T.can_manage(c, v, row):
        raise Problem("forbidden", "Only the thread's owner or an admin pins it for everyone", 403)
    audience = T.audience_of(row)
    if audience in EVERYONE_REFUSED or (audience in ("personal", "participants")
                                        and len([a for a in T._dm_members(c, row) if a != actor]) < 2):
        raise Problem("pin_everyone", "Only a thread or a group DM is pinned for everyone", 422)
    before = bool(row.get("pinned_for_everyone_at"))
    value = bool(value)
    if value != before:
        now = T._now()
        c.execute("UPDATE conversations SET pinned_for_everyone_at=?, updated_at=? WHERE id=?",
                  (now if value else None, now, row["id"]))
        if not value:
            c.execute("DELETE FROM thread_pins WHERE thread_id=? AND pinned=0", (row["id"],))
    T._event(c, actor, "thread.pinned_for_everyone", row["id"], {"pinned_for_everyone": value})
    T._audit(c, actor, "thread:" + row["id"], {"pinned_for_everyone": before}, {"pinned_for_everyone": value})
    return T.view(c, principal, T.thread_row(c, row["id"]))


# ----------------------------------------------------------------------------- the rail
_TITLE_ORDER = ("CASE WHEN TRIM(COALESCE(r.subject,''))='' THEN 1 ELSE 0 END", "lower(COALESCE(r.subject,''))")


def _title_page(c, v, where, args, after, limit):
    """Up to `limit` readable roots in title order (unnamed last) after `after`, and whether more follow. Rows the read
    check refuses are skipped, so a batch is read again past them."""
    rows, position = [], after
    fetch = limit + 20
    while True:
        cond, at = "", {}
        if position:
            cond = (" WHERE t_blank>:at_blank OR (t_blank=:at_blank AND (t_key>:at_key OR (t_key=:at_key AND "
                    "id>:at_id)))")
            at = {"at_blank": int(position[0]), "at_key": str(position[1]), "at_id": str(position[2])}
        sql = (f"SELECT * FROM (SELECT r.*, {_TITLE_ORDER[0]} AS t_blank, {_TITLE_ORDER[1]} AS t_key FROM conversations r "
               f"WHERE {where}){cond} ORDER BY t_blank, t_key, id LIMIT :fetch")
        found = [T.thread_row(c, dict(r)) for r in c.execute(sql, {**args, **at, "fetch": fetch})]
        T.prime_members(c, v, [r["id"] for r in found])
        dms, members = [r["id"] for r in found if T.audience_of(r) in ("personal", "participants")], {}
        if dms:
            members = T._member_rows(c, v, dms)
        for r in found:
            position = [r["t_blank"], r["t_key"], r["id"]]
            if not T.thread_readable(c, v, r):
                continue
            if r["id"] in members and len(_others(c, v, r, members[r["id"]])) < 2:
                continue
            if len(rows) >= limit:
                return rows, True
            rows.append(r)
        if len(found) < fetch:
            return rows, False


def rail(c, v, *, cursor=None, limit=RAIL_LIMIT):
    """The reader's threads by title: {"pinned", "threads", "catch_up", "dms", "next_cursor"}. `pinned` (the first page
    only, at most PINNED_MAX) holds the ones pinned for them, among them threads pinned for everyone they read but never
    joined; `threads` the others they are in, paged. `catch_up` is Catch up's count and `dms` the unread in each
    one-teammate DM (bots and people), on the first page only (later pages: null and {})."""
    v = _viewer(v)
    limit = _limit(limit, RAIL_LIMIT, T.MAX_PAGE)
    after = _decode(cursor, 3)
    extra, marks = _names(v, c)
    args = {"me": v.actor, **extra}
    kind = f"({_audience()} IN ('company','team','members') OR {_group_dm()})"
    base = [ROOT_OPEN, kind, T.readable_sql(c, v, "r")]
    v._members = {}
    try:
        threads_rows, more = _title_page(c, v, " AND ".join([*base, _in(marks), f"NOT {PINNED_SQL}"]), args, after,
                                         limit)
        pinned_rows = [] if cursor else _title_page(c, v, " AND ".join([*base, PINNED_SQL]), args, None, PINNED_MAX)[0]
        shown = _views(c, v, pinned_rows + threads_rows)
    finally:
        v._members = None
    pinned_items, _ = _fits([shown[r["id"]] for r in pinned_rows], T.MAX_PAGE_BYTES // 4)
    thread_items, cut = _fits([shown[r["id"]] for r in threads_rows], T.MAX_PAGE_BYTES // 2)
    more = more or cut
    last = threads_rows[len(thread_items) - 1] if thread_items else None
    return {"pinned": pinned_items, "threads": thread_items,
            "catch_up": None if cursor else catch_up_count(c, v),
            "dms": {} if cursor else T.one_dm_unread(c, v),
            "next_cursor": _encode([last["t_blank"], last["t_key"], last["id"]]) if more and last else None}


# ----------------------------------------------------------------------------- browse
def browse(c, v, *, q=None, cursor=None, limit=BROWSE_LIMIT):
    """Company and team roots the reader may read and is not in, newest activity first; `q` matches the title. Each row
    is the thread view and its `member_count` (its members and followers). Following one puts it in the rail."""
    v = _viewer(v)
    limit = _limit(limit, BROWSE_LIMIT, T.MAX_PAGE)
    after = _decode(cursor, 2)
    extra, marks = _names(v, c)
    args = {"me": v.actor, **extra}
    where = [ROOT_OPEN, f"{_audience()} IN ('company','team')", T.readable_sql(c, v, "r"), f"NOT {_in(marks)}"]
    text = str(q or "").strip()
    if text:
        args["q"] = "%" + text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("lower(COALESCE(r.subject,'')) LIKE :q ESCAPE '\\'")
    rows, position, fetch = [], after, limit + 20
    more = False
    while True:
        cond, at = "", {}
        if position:
            cond = " WHERE activity<:at_seq OR (activity=:at_seq AND id<:at_id)"
            at = {"at_seq": int(position[0]), "at_id": str(position[1])}
        found = [T.thread_row(c, dict(r)) for r in c.execute(
            f"SELECT * FROM (SELECT r.*, {ACTIVITY} AS activity FROM conversations r WHERE {' AND '.join(where)})"
            f"{cond} ORDER BY activity DESC, id DESC LIMIT :fetch", {**args, **at, "fetch": fetch})]
        for r in found:
            position = [r["activity"], r["id"]]
            if not T.thread_readable(c, v, r):
                continue
            if len(rows) >= limit:
                more = True
                break
            rows.append(r)
        if more or len(found) < fetch:
            break
    counts = _member_counts(c, [r["id"] for r in rows])
    v._members = {}
    try:
        shown = _views(c, v, rows)
    finally:
        v._members = None
    items, cut = _fits([{**shown[r["id"]], "member_count": counts.get(r["id"], 0)} for r in rows],
                       T.MAX_PAGE_BYTES // 2)
    last = rows[len(items) - 1] if items else None
    return {"threads": items,
            "next_cursor": _encode([last["activity"], last["id"]]) if (more or cut) and last else None}


def _member_counts(c, ids):
    """{root id: how many teammates are in it}: its members and its followers, each once."""
    out = {}
    for part in H.chunks(list(ids)):
        marks = ",".join("?" * len(part))
        for r in c.execute(f"SELECT thread_id, COUNT(DISTINCT actor) FROM (SELECT thread_id, actor FROM thread_members "
                           f"WHERE thread_id IN ({marks}) UNION SELECT thread_id, actor FROM thread_followers WHERE "
                           f"following=1 AND thread_id IN ({marks})) GROUP BY thread_id", (*part, *part)):
            out[r[0]] = int(r[1])
    return out


# ----------------------------------------------------------------------------- catch up
def _attention(c, v):
    """{root id: {"mention": bool, "messages": [message ids]}} for the reader's open Needs attention items: mentions,
    asks, unread DMs with another person, and held posts (thread_attention)."""
    from . import thread_attention
    items = [it for it in thread_attention.items(c, None, v, with_capped=False) if it["kind"] in ATTENTION_KINDS]
    places = [it.get("conversation_id") or it.get("thread_id") for it in items]
    found, _ = T.read_threads(c, v, places)
    out = {}
    for item, place in zip(items, places, strict=True):
        thread = found.get(place) if place else None
        if thread is None:
            continue
        entry = out.setdefault(thread.get("root_id") or thread["id"], {"mention": False, "messages": []})
        entry["mention"] = entry["mention"] or item["kind"] in MENTION_KINDS
        if item["kind"] in MENTION_KINDS and item.get("message_id"):
            entry["messages"].append(item["message_id"])
    return out


def _mentioned(c, v):
    """The roots with a new message past the reader's mark that names them (a mention, an ask or an assignment)."""
    mark = T._subtree_mark(c, v)
    return {r[0] for r in c.execute(
        "SELECT DISTINCT COALESCE(s.root_id,s.id) FROM message_deliveries d CROSS JOIN messages m ON m.id=d.message_id "
        "CROSS JOIN conversations s ON s.id=m.conversation_id LEFT JOIN thread_followers f ON f.thread_id=s.id AND "
        "f.actor=d.recipient WHERE d.recipient=? AND d.reason IN ('mention', 'ask', 'assign', 'addressed') "
        "AND d.state IN ('pending', 'delivered', 'read') AND d.reason<>'addressed' "
        f"AND m.seq>{mark} AND m.sender_id<>? AND m.deleted_at IS NULL AND {T._SUBTREE_OPEN}",
        (v.actor, v.actor))}


def _catch_up_items(c, v):
    """Every Catch up item for the reader, in order: mentions and asks first, then newest activity. Each is
    {"row", "summary", "mention", "new_count", "activity", "attention"}."""
    attention = _attention(c, v)
    history = T._history_only_roots(c, v)
    extra, marks = _names(v, c)
    args = {"me": v.actor, "attention": json.dumps(sorted(attention)), "history": json.dumps(history),
            "scan": CATCH_UP_SCAN, **extra}
    by_person, to_viewer = T.unread_terms(c, v)
    is_attention = "r.id IN (SELECT value FROM json_each(:attention))"
    audience = _audience()
    where = [ROOT_OPEN, T.readable_sql(c, v, "r"),
             f"(({audience} IN ('company','team','members','personal','participants','task') AND "
             f"({_in(marks)} OR {PINNED_SQL})) OR ({audience}='home' AND r.owner_id=:me) OR {is_attention})",
             f"({is_attention} OR (r.id NOT IN (SELECT value FROM json_each(:history)) AND ({by_person} OR {to_viewer})))"]
    found = [T.thread_row(c, dict(r)) for r in c.execute(
        f"SELECT r.*, {ACTIVITY} AS activity FROM conversations r WHERE {' AND '.join(where)} "
        "ORDER BY activity DESC, r.id DESC LIMIT :scan", args)]
    rows = [r for r in found if T.thread_readable(c, v, r)]
    summaries = T.subtree_summaries(c, v, [r["id"] for r in rows])
    followers = T._followers_of(c, v, [r["id"] for r in rows])
    mentioned = _mentioned(c, v)
    out = []
    for r in rows:
        summary = summaries.get(r["id"]) or {"unread": 0, "activity": False}
        flagged = attention.get(r["id"])
        mention = bool(flagged and flagged["mention"]) or r["id"] in mentioned
        unread = 0 if r["id"] in history else int(summary.get("unread") or 0)
        if not unread and not flagged:
            continue
        if (followers.get(r["id"]) or {}).get("notify") == "muted" and not mention:
            continue
        out.append({"row": r, "summary": summary, "mention": mention, "new_count": unread or 1,
                    "activity": int(summary.get("activity_seq") or r.get("activity") or 0),
                    "attention": (flagged or {}).get("messages") or []})
    out.sort(key=_order)
    return out


def _order(item):
    return (0 if item["mention"] else 1, -item["activity"], item["row"]["id"])


def catch_up_count(c, v):
    """How many Catch up items the reader has, at most CATCH_UP_SHOWN: the sidebar's badge."""
    return min(len(_catch_up_items(c, _viewer(v))), CATCH_UP_SHOWN)


def catch_up(c, v, *, cursor=None, limit=CATCH_UP_LIMIT):
    """{"items", "count", "next_cursor"}: each root with something new for the reader, and each open Needs attention
    item, mentions and asks first, then newest activity. An item is its thread view, `new_count` (its subtree's unread,
    or 1 for an attention item alone), `mention`, and its new messages in tree order, at most CATCH_UP_MESSAGES, each
    with its `depth` and through the per-message check; `more` counts the rest. `count` is every item, at most
    CATCH_UP_SHOWN. Reading one is POST .../read-all; muting one is following it with notify `muted`."""
    v = _viewer(v)
    limit = _limit(limit, CATCH_UP_LIMIT, CATCH_UP_MAX_LIMIT)
    after = _decode(cursor, 3)
    every = _catch_up_items(c, v)
    if after:
        at = (int(after[0]), -int(after[1]), str(after[2]))
        every_after = [it for it in every if _order(it) > at]
    else:
        every_after = every
    page, more = every_after[:limit], len(every_after) > limit
    v._members = {}
    try:
        shown = _views(c, v, [it["row"] for it in page])
    finally:
        v._members = None
    items, budget, tail = [], T.MAX_PAGE_BYTES, None
    for it in page:
        messages, rest = _new_messages(c, v, it["row"], it["attention"])
        entry = {"thread": shown[it["row"]["id"]], "new_count": it["new_count"], "mention": it["mention"],
                 "messages": messages, "more": rest}
        budget -= len(json.dumps(entry, default=str))
        if items and budget < 0:
            more = True
            break
        items.append(entry)
        tail = it
    return {"items": items, "count": min(len(every), CATCH_UP_SHOWN),
            "next_cursor": _encode([0 if tail["mention"] else 1, tail["activity"], tail["row"]["id"]])
            if more and tail else None}


def _new_messages(c, v, root, attention_ids):
    """The new messages of a root's open subtree for the reader, in tree order (a message, then the sub-thread under
    it), the first CATCH_UP_MESSAGES of them, and how many more. An attention item's own message is among them even
    when it was read."""
    mark = T._subtree_mark(c, v)
    rows = [dict(r) for r in c.execute(
        f"SELECT m.* FROM {T._SUBTREE.format(me='?')}JOIN messages m ON m.conversation_id=s.id AND m.seq>{mark} "
        f"WHERE s.root_id=? AND {T._SUBTREE_OPEN} AND s.last_unread_seq>{mark} AND {T.unread_message_sql()} "
        "ORDER BY m.seq LIMIT ?", (v.actor, root["id"], v.actor, v.actor, CATCH_UP_READ))]
    have = {m["id"] for m in rows}
    for mid in attention_ids:
        if mid not in have:
            found = H.message(c, mid)
            if found and found.get("seq") is not None:
                rows.append(dict(found))
                have.add(mid)
    # Every thread of the subtree with the seq of the message it branched from: a message's place in tree order is the
    # seqs of the messages its thread hangs from, then its own.
    threads_of = {r["id"]: dict(r) for r in c.execute(
        "SELECT s.id, s.path, s.parent_message_id, (SELECT seq FROM messages WHERE id=s.parent_message_id) AS anchor "
        "FROM conversations s WHERE s.root_id=? OR s.id=?", (root["id"], root["id"]))}
    shown = []
    for m in rows:
        thread = threads_of.get(m["conversation_id"])
        if thread is None or (thread["id"] != root["id"] and not T.thread_readable(c, v, thread["id"])):
            continue
        if not T.message_readable(c, v, m, thread_checked=True):
            continue
        path = [p for p in str(thread.get("path") or f"/{thread['id']}/").split("/") if p]
        key = [int((threads_of.get(p) or {}).get("anchor") or 0) for p in path[1:]] + [int(m["seq"] or 0)]
        shown.append((key, len(path) - 1, m))
    shown.sort(key=lambda entry: entry[0])
    first = shown[:CATCH_UP_MESSAGES]
    known = v.known(c)
    marks = T.reactions(c, [m["id"] for _, _, m in first])
    out = []
    for _, depth, m in first:
        item = {**known.message(T._message_out(m)), "depth": depth}
        if m["id"] in marks:
            item["reactions"] = known.reactions(marks[m["id"]], m["conversation_id"])
        out.append(item)
    return out, len(shown) - len(first)
