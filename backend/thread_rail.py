"""Pins, the thread rail, Browse and Catch up (docs/dev/threads-contract.md §10.1).

A reader pins a root for themselves (`thread_pins`, pinned 1) and unpins it for themselves over a manager's pin for
everyone (`conversations.pinned_for_everyone_at`, pinned 0). Their own row wins; without one the pin for everyone counts.

The rail is the threads a reader is in, by title: company, team and members roots and group DMs (two or more others),
never a 1:1 DM, a Home, a task's thread or a meeting. `pinned` holds the ones pinned for them, `threads` the rest, paged.
Browse lists the company and team roots a reader may read and is not in yet; following one (PUT .../follow) puts it in
the rail. Catch up lists each root with something new for the reader, and each open Needs attention item, with the new
messages in tree order. The rail's first page also lists, as `unread`, each Catch up item the rail does not hold (a 1:1
DM, a Home, a task's thread), so every chat the badge counts has a row.

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
CATCH_UP_MESSAGES = 20  # an item shows this many new messages; `more` counts the rest
CATCH_UP_THREADS = 1000  # threads of one subtree an item reads its new messages from
CATCH_UP_BODY = 4000  # a shown message's text is cut here (`clipped`); the thread has it whole
CATCH_UP_CLIP = 500  # a message too big for what is left of the page goes without refs, its text cut here
CATCH_UP_READS = 5  # batches of candidate messages one item reads whole, at most
CATCH_UP_HIDDEN = 200  # messages that can hide, read whole to count one batch of roots' new messages
CATCH_UP_SCAN = 1000  # roots one Catch up looks at, over all its pages
CATCH_UP_BATCH = 100  # roots read and checked together
CATCH_UP_SHOWN = 99  # `count` stops here (the sidebar badge), and the rail's `unread` with it
ATTENTION_SCAN = 500  # open mentions and asks read for one Catch up
DONE = 0  # a rail cursor's list that has no more rows
EVERYONE_REFUSED = ("home", "task", "meeting")


# ----------------------------------------------------------------------------- shared SQL
def _audience():
    return f"COALESCE(r.audience,{T._derived_audience('r')})"


def _is_dm():
    return f"{_audience()} IN ('personal','participants')"


def _group_dm():
    """A DM root that may hold two or more others: Python confirms each (`_others`)."""
    return (
        f"({_is_dm()} AND ((SELECT COUNT(*) FROM thread_members gm WHERE gm.thread_id=r.id)>=3 OR "
        "(CASE WHEN json_valid(r.participants) THEN json_array_length(r.participants) ELSE 0 END)>=3))"
    )


def _in(names_sql):
    """The reader is in root `r`: its starter, a member (or in a member group), following it or any thread under it,
    or one of its participants. `:me` is the reader."""
    return (
        f"(r.owner_id=:me OR EXISTS (SELECT 1 FROM thread_members im WHERE im.thread_id=r.id AND im.actor IN "
        f"({names_sql})) OR r.id IN (SELECT COALESCE(s.root_id,s.id) FROM thread_followers f JOIN conversations s ON "
        f"s.id=f.thread_id WHERE f.actor=:me AND f.following=1 AND {T._SUBTREE_OPEN}) OR EXISTS (SELECT 1 FROM "
        f"{T._PARTICIPANTS.format(r='r')} WHERE value=:me))"
    )


PINNED_SQL = (
    "(EXISTS (SELECT 1 FROM thread_pins p WHERE p.thread_id=r.id AND p.actor=:me AND p.pinned=1) OR "
    "(r.pinned_for_everyone_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM thread_pins p WHERE p.thread_id=r.id "
    "AND p.actor=:me AND p.pinned=0)))"
)


def root_open(c, v):
    """The roots the rail, Browse and Catch up list for this reader: not archived or closed, and not hidden for a
    finished task (T.task_open_sql, which a post the reader reads brings back)."""
    return (
        "r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL AND "
        f"{T.task_open_sql(c, v, 'r', T.revived_roots(c, v))}"
    )


ACTIVITY = (
    f"COALESCE((SELECT MAX(s.last_seq) FROM conversations s WHERE s.root_id=r.id AND {T._SUBTREE_OPEN}),r.last_seq,0)"
)


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


def _views(c, v, rows, *, summaries=None):
    """Each root row as a list shows it: its whole subtree's unread and activity, its DM title, the reader's pin."""
    ids = [r["id"] for r in rows]
    if v._members is None:
        v._members = {}
    v._members.update(T._prefetch_members(c, v, ids))
    summaries = T.subtree_summaries(c, v, ids) if summaries is None else summaries
    followers = T._followers_of(c, v, ids)
    dms = T.dm_with(c, v, rows)
    pins = T.pin_rows(c, v.actor, ids)
    calls = T.live_calls(c, ids)
    out = {}
    for r in rows:
        summary = summaries.get(r["id"]) or {"unread": 0, "activity": False}
        item = T.view(
            c,
            v,
            r,
            summary=summary,
            dm=dms.get(r["id"]),
            follower=followers.get(r["id"], {}),
            pins=pins,
            calls=calls,
        )
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
    c.execute(
        "INSERT INTO thread_pins(id,thread_id,actor,pinned,created_at,updated_at) VALUES(?,?,?,1,?,?) "
        "ON CONFLICT(thread_id,actor) DO UPDATE SET pinned=1, updated_at=excluded.updated_at",
        (T._new_id(), row["id"], actor, now, now),
    )
    _pin_audit(c, actor, row["id"], before, True, "thread.pinned")
    return T.view(c, principal, T.thread_row(c, row["id"]))


def unpin(c, principal, thread_id):
    """Unpin a root thread for the caller: over a pin for everyone, a row that unpins it for them alone."""
    actor, v, row = _pin_root(c, principal, thread_id)
    own = T.pin_rows(c, actor, [row["id"]]).get(row["id"])
    before = T.pinned(row.get("pinned_for_everyone_at"), own)
    now = T._now()
    if row.get("pinned_for_everyone_at"):
        c.execute(
            "INSERT INTO thread_pins(id,thread_id,actor,pinned,created_at,updated_at) VALUES(?,?,?,0,?,?) "
            "ON CONFLICT(thread_id,actor) DO UPDATE SET pinned=0, updated_at=excluded.updated_at",
            (T._new_id(), row["id"], actor, now, now),
        )
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
    if audience in EVERYONE_REFUSED or (
        audience in ("personal", "participants") and len([a for a in T._dm_members(c, row) if a != actor]) < 2
    ):
        raise Problem("pin_everyone", "Only a thread or a group DM is pinned for everyone", 422)
    before = bool(row.get("pinned_for_everyone_at"))
    value = bool(value)
    if value != before:
        now = T._now()
        c.execute(
            "UPDATE conversations SET pinned_for_everyone_at=?, updated_at=? WHERE id=?",
            (now if value else None, now, row["id"]),
        )
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
            cond = (
                " WHERE t_blank>:at_blank OR (t_blank=:at_blank AND (t_key>:at_key OR (t_key=:at_key AND id>:at_id)))"
            )
            at = {"at_blank": int(position[0]), "at_key": str(position[1]), "at_id": str(position[2])}
        sql = (
            f"SELECT * FROM (SELECT r.*, {_TITLE_ORDER[0]} AS t_blank, {_TITLE_ORDER[1]} AS t_key FROM conversations r "
            f"WHERE {where}){cond} ORDER BY t_blank, t_key, id LIMIT :fetch"
        )
        found = [T.thread_row(c, dict(r)) for r in c.execute(sql, {**args, **at, "fetch": fetch})]
        T.prime_members(c, v, [r["id"] for r in found], roots=[r["id"] for r in found])
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


def _rail_cursor(cursor):
    """A rail cursor: where `pinned` and `threads` each go on, or DONE."""
    position = _decode(cursor, 2)
    for part in position:
        if part != DONE and not (isinstance(part, list) and len(part) == 3):
            raise Problem("cursor", "Unknown cursor", 422)
    return position


def _rail_next(rows, items, more):
    if not more or not items:
        return DONE
    last = rows[len(items) - 1]
    return [last["t_blank"], last["t_key"], last["id"]]


def rail(c, v, *, cursor=None, limit=RAIL_LIMIT):
    """The reader's threads by title: {"pinned", "threads", "catch_up", "catch_up_ids", "dms", "next_cursor"}.
    `pinned` holds the ones pinned for them (threads pinned for everyone they read but never joined among them), at most
    PINNED_MAX a page; `threads` the others they are in. Both go on through `next_cursor` until each is done.
    `catch_up` is Catch up's count, `catch_up_ids` the roots it counts, `unread` the Catch up items the rail does not
    hold (`_unread_items`), and `dms` the unread in each one-teammate DM (bots and people), on the first page only (later
    pages: null, [], [] and {}). Count, ids and `unread` come from one Catch up walk, so they agree."""
    v = _viewer(v)
    limit = _limit(limit, RAIL_LIMIT, T.MAX_PAGE)
    first = not cursor
    pinned_at, threads_at = (None, None) if first else _rail_cursor(cursor)
    extra, marks = _names(v, c)
    args = {"me": v.actor, **extra}
    kind = f"({_audience()} IN ('company','team','members') OR {_group_dm()})"
    base = [root_open(c, v), kind, T.readable_sql(c, v, "r")]
    v._members = {}
    try:
        pinned_rows, pinned_more = (
            ([], False)
            if pinned_at == DONE
            else _title_page(c, v, " AND ".join([*base, PINNED_SQL]), args, pinned_at, PINNED_MAX)
        )
        threads_rows, threads_more = (
            ([], False)
            if threads_at == DONE
            else _title_page(c, v, " AND ".join([*base, _in(marks), f"NOT {PINNED_SQL}"]), args, threads_at, limit)
        )
        rows = pinned_rows + threads_rows
        pairs = T._one_dm_pairs(c, v) if first else {}
        summaries = T.subtree_summaries(c, v, list(dict.fromkeys([r["id"] for r in rows] + list(pairs))))
        shown = _views(c, v, rows, summaries=summaries)
    finally:
        v._members = None
    pinned_items, pinned_cut = _fits([shown[r["id"]] for r in pinned_rows], T.MAX_PAGE_BYTES // 4)
    thread_items, threads_cut = _fits([shown[r["id"]] for r in threads_rows], T.MAX_PAGE_BYTES // 2)
    position = [
        _rail_next(pinned_rows, pinned_items, pinned_more or pinned_cut),
        _rail_next(threads_rows, thread_items, threads_more or threads_cut),
    ]
    found = _first(_walk(c, v, _flags(c, v)), CATCH_UP_SHOWN) if first else []
    return {
        "pinned": pinned_items,
        "threads": thread_items,
        "catch_up": len(found) if first else None,
        "catch_up_ids": [it["row"]["id"] for it in found],
        "unread": _unread_items(c, v, found, kind, args, marks),
        "dms": T.one_dm_unread(c, v, pairs=pairs, summaries=summaries) if first else {},
        "next_cursor": None if position == [DONE, DONE] else _encode(position),
    }


def _unread_items(c, v, found, kind, args, marks):
    """The Catch up items (`_walk`'s, in its order: mentions first, then newest activity) that the rail does not hold, as
    the rail's Unread rows: {id, title, name, audience, dm_with, dm_with_count, home_actor, new_count, mention, task_id,
    last_message_at}. A root is on the rail when it is a thread or group DM (`kind`) the reader is in or has pinned."""
    if not found:
        return []
    rows = {it["row"]["id"]: it["row"] for it in found}
    held = set()
    for part in H.chunks(list(rows)):
        marks_ids = ",".join(f":id{i}" for i in range(len(part)))
        held.update(
            r[0]
            for r in c.execute(
                f"SELECT r.id FROM conversations r WHERE r.id IN ({marks_ids}) AND {kind} "
                f"AND ({_in(marks)} OR {PINNED_SQL})",
                {**args, **{f"id{i}": rid for i, rid in enumerate(part)}},
            )
        )
    dms = [r for rid, r in rows.items() if rid in held and T.audience_of(r) in ("personal", "participants")]
    if dms:
        members = T._member_rows(c, v, [r["id"] for r in dms])
        held -= {r["id"] for r in dms if len(_others(c, v, r, members[r["id"]])) < 2}
    off = [it for it in found if it["row"]["id"] not in held]
    with_dm = T.dm_with(c, v, [it["row"] for it in off])
    out = []
    for it in off:
        r = it["row"]
        title = T._shown_title(c, v, r)
        audience = T.audience_of(r)
        dm = with_dm.get(r["id"]) or {}
        out.append(
            {
                "id": r["id"],
                "title": title,
                "name": T._name(c, v, r, title),
                "audience": audience,
                "dm_with": dm.get("dm_with") or [],
                "dm_with_count": int(dm.get("dm_with_count") or 0),
                "home_actor": T.home_owner(r) if audience == "home" else None,
                "new_count": it["new_count"],
                "mention": it["mention"],
                "task_id": r.get("task_id"),
                "last_message_at": r.get("last_message_at"),
            }
        )
    return out


# ----------------------------------------------------------------------------- browse
def browse(c, v, *, q=None, cursor=None, limit=BROWSE_LIMIT):
    """Company and team roots the reader may read and is not in, newest activity first; `q` matches the title. Each row
    is the thread view and its `member_count` (its members and followers). Following one puts it in the rail."""
    v = _viewer(v)
    limit = _limit(limit, BROWSE_LIMIT, T.MAX_PAGE)
    after = _decode(cursor, 2)
    extra, marks = _names(v, c)
    args = {"me": v.actor, **extra}
    where = [root_open(c, v), f"{_audience()} IN ('company','team')", T.readable_sql(c, v, "r"), f"NOT {_in(marks)}"]
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
        found = [
            T.thread_row(c, dict(r))
            for r in c.execute(
                f"SELECT * FROM (SELECT r.*, {ACTIVITY} AS activity FROM conversations r WHERE {' AND '.join(where)})"
                f"{cond} ORDER BY activity DESC, id DESC LIMIT :fetch",
                {**args, **at, "fetch": fetch},
            )
        ]
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
    items, cut = _fits(
        [{**shown[r["id"]], "member_count": counts.get(r["id"], 0)} for r in rows], T.MAX_PAGE_BYTES // 2
    )
    last = rows[len(items) - 1] if items else None
    return {
        "threads": items,
        "next_cursor": _encode([last["activity"], last["id"]]) if (more or cut) and last else None,
    }


def archived(c, v, *, cursor=None, limit=None):
    """The roots the reader archived and may still manage (only a manager can unarchive), newest activity first: Browse
    threads' Archived view, in pages. A deleted thread is not listed. Each row is the thread view, as a list shows it.
    The page is filled from the rows that pass the read and manage checks, so a reader with many archived threads they
    cannot manage still gets a full page and the cursor reaches the rest."""
    v = _viewer(v)
    limit = _limit(limit, BROWSE_LIMIT, T.MAX_PAGE)
    after = _decode(cursor, 2)
    args = {"me": v.actor, **{f"name{i}": n for i, n in enumerate(v.member_names(c))}}
    where = ["r.parent_id IS NULL", "r.archived_at IS NOT NULL", "r.deleted_at IS NULL", T.readable_sql(c, v, "r")]
    rows, position, fetch = [], after, limit + 20
    more = False
    while True:
        cond, at = "", {}
        if position:
            cond = " WHERE activity<:at_seq OR (activity=:at_seq AND id<:at_id)"
            at = {"at_seq": int(position[0]), "at_id": str(position[1])}
        found = [
            T.thread_row(c, dict(r))
            for r in c.execute(
                f"SELECT * FROM (SELECT r.*, {ACTIVITY} AS activity FROM conversations r WHERE {' AND '.join(where)})"
                f"{cond} ORDER BY activity DESC, id DESC LIMIT :fetch",
                {**args, **at, "fetch": fetch},
            )
        ]
        for r in found:
            if not r:
                continue
            position = [r["activity"], r["id"]]
            if not (T.thread_readable(c, v, r) and T.can_manage(c, v, r)):
                continue
            if len(rows) >= limit:
                more = True
                break
            rows.append(r)
        if more or len(found) < fetch:
            break
    v._members = {}
    try:
        shown = _views(c, v, rows)
    finally:
        v._members = None
    items, cut = _fits([shown[r["id"]] for r in rows], T.MAX_PAGE_BYTES // 2)
    last = rows[len(items) - 1] if items else None
    return {
        "threads": items,
        "next_cursor": _encode([last["activity"], last["id"]]) if (more or cut) and last else None,
    }


def _member_counts(c, ids):
    """{root id: how many teammates are in it}: its members and its followers, each once."""
    out = {}
    for part in H.chunks(list(ids)):
        marks = ",".join("?" * len(part))
        for r in c.execute(
            f"SELECT thread_id, COUNT(DISTINCT actor) FROM (SELECT thread_id, actor FROM thread_members "
            f"WHERE thread_id IN ({marks}) UNION SELECT thread_id, actor FROM thread_followers WHERE "
            f"following=1 AND thread_id IN ({marks})) GROUP BY thread_id",
            (*part, *part),
        ):
            out[r[0]] = int(r[1])
    return out


# ----------------------------------------------------------------------------- catch up
# A message the per-message check passes as it stands, without reading it whole: not restricted, no old Home history.
# Any other, and every message of a migrated Home's thread (`threads._legacy_homes`), is read whole for the check.
PLAIN = "(m.restricted=0 AND m.legacy_audience IS NULL)"


def _readable_ids(c, v, light):
    """The ids of these message rows ({"id", "conversation_id", "plain"}, their threads already checked) the reader may
    read: a plain one passes as it is; any other is read whole for the per-message check."""
    legacy = T._legacy_homes(c)
    out, whole = set(), []
    for m in light:
        (out.add if m["plain"] and m["conversation_id"] not in legacy else whole.append)(m["id"])
    for part in H.chunks(whole):
        for r in c.execute(f"SELECT * FROM messages WHERE id IN ({','.join('?' * len(part))})", part):
            if T.message_readable(c, v, dict(r), thread_checked=True):
                out.add(r["id"])
    return out


def _readable_rows(c, v, rows):
    """These message rows ({"id", "conversation_id", "plain", ...}) whose thread and message the reader may read."""
    found, _ = T.read_threads(c, v, [r["conversation_id"] for r in rows])
    rows = [
        r
        for r in rows
        if found.get(r["conversation_id"]) is not None and T.thread_readable(c, v, found[r["conversation_id"]])
    ]
    ok = _readable_ids(c, v, rows)
    return [r for r in rows if r["id"] in ok]


def _bot_home(cv):
    """SQL: the root of thread `cv` is a bot's Home that is not the reader's (`:me` or the first `?` it is given)."""
    return (
        f"EXISTS (SELECT 1 FROM conversations hr WHERE hr.id=COALESCE({cv}.root_id,{cv}.id) "
        f"AND COALESCE(hr.audience,{T._derived_audience('hr')})='home' AND hr.owner_id LIKE 'bot:%')"
    )


def _flags(c, v):
    """({root id: [message ids]}, {root ids}, {root id: count}): the roots that mention the reader, with their open
    mentions' and asks' messages (thread_attention._addressed's rule, ids only, the newest ATTENTION_SCAN); the roots of
    writes held for their review; and the bots' Homes with a reply or mention for them past their mark (`_home_replies`).
    Every message passes the thread and the per-message check; only one that can hide is read whole. A bot's reply in its
    own Home is not an open item: it counts while unread (`_home_replies`), so reading it clears it."""
    from . import thread_attention

    me = v.actor
    rows = [
        dict(r)
        for r in c.execute(
            f"SELECT m.id, m.conversation_id, {PLAIN} AS plain, COALESCE(cv.root_id, cv.id) AS root "
            "FROM message_deliveries d CROSS JOIN messages m ON m.id=d.message_id "
            "CROSS JOIN conversations cv ON cv.id=m.conversation_id "
            "WHERE d.recipient=? AND d.reason IN ('mention','ask','assign','addressed','reply') "
            "AND d.state IN ('pending','delivered','read') AND m.sender_id<>? "
            f"AND {H.delivery_counts_sql('m', 'd.recipient', 'd.reason')} "
            "AND (m.sender_id LIKE 'human:%' OR m.kind='ask' OR d.reason IN ('mention','reply')) "
            "AND m.deleted_at IS NULL AND m.superseded_at IS NULL AND (m.kind<>'ask' OR m.answered_by_id IS NULL) "
            "AND cv.task_id IS NULL AND cv.closed_at IS NULL AND cv.archived_at IS NULL "
            f"AND NOT (d.reason='reply' AND m.sender_id LIKE 'bot:%' AND {_bot_home('cv')}) "
            "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id AND r.sender_id=? "
            "AND r.created_at>m.created_at AND r.deleted_at IS NULL) ORDER BY m.created_at DESC LIMIT ?",
            (me, me, me, ATTENTION_SCAN),
        )
    ]
    mentions = {}
    for r in _readable_rows(c, v, rows):
        mentions.setdefault(r["root"], []).append(r["id"])
    for root in _mentioned(c, v):
        mentions.setdefault(root, [])
    held = set()
    # Only a held native post or edit has a place in a thread (`_held_thread`), so only those are read: the reader's
    # own candidates, through an index, the oldest ATTENTION_SCAN (org_rule_check.held_for).
    for hold in thread_attention._holds(c, v, ATTENTION_SCAN, ("threads", "threads_edit")):
        place = thread_attention._held_thread(c, v, hold["id"])
        row = T.thread_row(c, place) if place else None
        if row is not None:
            held.add(row.get("root_id") or row["id"])
    return mentions, held, _home_replies(c, v)


def _home_replies(c, v):
    """{root id: how many}: the bots' Homes (not the reader's) holding messages past the reader's mark that reply to them
    (`in_reply_to` one of their messages: delivery reason `reply`) or mention, ask or assign them, each through the thread
    and per-message check. Only these count in a bot's Home: its unprompted posts are Updates. At most ATTENTION_SCAN."""
    mark = T._subtree_mark(c, v)
    rows = [
        dict(r)
        for r in c.execute(
            f"SELECT m.id, m.conversation_id, {PLAIN} AS plain, COALESCE(s.root_id,s.id) AS root FROM message_deliveries d "
            "CROSS JOIN messages m ON m.id=d.message_id "
            "CROSS JOIN conversations s ON s.id=m.conversation_id LEFT JOIN thread_followers f ON f.thread_id=s.id AND "
            "f.actor=d.recipient WHERE d.recipient=? AND d.reason IN ('reply', 'mention', 'ask', 'assign') "
            "AND d.state IN ('pending', 'delivered', 'read') "
            f"AND m.seq>{mark} AND m.sender_id<>? AND m.deleted_at IS NULL AND {T._SUBTREE_OPEN} AND {_bot_home('s')} "
            f"AND {H.delivery_counts_sql('m', 'd.recipient', 'd.reason')} LIMIT ?",
            (v.actor, v.actor, ATTENTION_SCAN),
        )
    ]
    out = {}
    for r in _readable_rows(c, v, rows):
        out[r["root"]] = out.get(r["root"], 0) + 1
    return out


def _mentioned(c, v):
    """The roots with a new message past the reader's mark that names them (a mention, an ask or an assignment) and
    that they may read: the thread and the per-message check, as `_flags`."""
    mark = T._subtree_mark(c, v)
    rows = [
        dict(r)
        for r in c.execute(
            f"SELECT m.id, m.conversation_id, {PLAIN} AS plain, COALESCE(s.root_id,s.id) AS root FROM message_deliveries d "
            "CROSS JOIN messages m ON m.id=d.message_id "
            "CROSS JOIN conversations s ON s.id=m.conversation_id LEFT JOIN thread_followers f ON f.thread_id=s.id AND "
            "f.actor=d.recipient WHERE d.recipient=? AND d.reason IN ('mention', 'ask', 'assign') "
            "AND d.state IN ('pending', 'delivered', 'read') "
            f"AND m.seq>{mark} AND m.sender_id<>? AND m.deleted_at IS NULL AND {T._SUBTREE_OPEN} "
            f"AND {H.delivery_counts_sql('m', 'd.recipient', 'd.reason')} LIMIT ?",
            (v.actor, v.actor, ATTENTION_SCAN),
        )
    ]
    return {r["root"] for r in _readable_rows(c, v, rows)}


def _readable_new(c, v, roots):
    """{root id: how many new messages of its open subtree the reader may read}: subtree_summaries' unread, through the
    thread check for each sub-thread (at most CATCH_UP_THREADS a root) and the per-message check for each message that
    can hide (at most CATCH_UP_HIDDEN read whole a call; past that they are not counted)."""
    mark, subtree, legacy = T._subtree_mark(c, v), T._SUBTREE.format(me="?"), T._legacy_homes(c)
    groups = []
    for part in H.chunks(list(roots)):
        unread_sql, args = T._subtree_unread_sql(c, v, part)
        groups += [
            dict(r)
            for r in c.execute(
                f"WITH unread AS ({unread_sql}) SELECT root, thread, plain, n FROM ("
                "SELECT root, thread, SUM(plain) AS plain, COUNT(*) AS n, "
                "ROW_NUMBER() OVER (PARTITION BY root ORDER BY last_unread_seq DESC) AS k "
                "FROM unread GROUP BY thread) WHERE k<=:cap",
                {**args, "cap": CATCH_UP_THREADS},
            )
        ]
    found, _ = T.read_threads(c, v, [g["thread"] for g in groups if g["thread"] != g["root"]])
    out, hidden = {}, []
    for g in groups:
        if g["thread"] != g["root"] and not (found.get(g["thread"]) and T.thread_readable(c, v, found[g["thread"]])):
            continue
        sure = 0 if g["thread"] in legacy else int(g["plain"] or 0)
        out[g["root"]] = out.get(g["root"], 0) + sure
        if int(g["n"]) > sure:
            hidden.append(g)
    budget = CATCH_UP_HIDDEN
    for g in hidden:
        if budget <= 0:
            break
        maybe = "" if g["thread"] in legacy else f" AND NOT {PLAIN}"
        for r in c.execute(
            f"SELECT m.* FROM {subtree}JOIN messages m ON m.conversation_id=s.id AND m.seq>{mark} "
            f"WHERE s.id=? AND {T.unread_message_sql()}{maybe} ORDER BY m.seq LIMIT ?",
            (v.actor, g["thread"], v.actor, v.actor, budget),
        ).fetchall():
            budget -= 1
            if T.message_readable(c, v, dict(r), thread_checked=True):
                out[g["root"]] += 1
    return out


def _walk(c, v, flags, after=None):
    """Catch up's items in order, read a batch at a time: the roots that mention the reader first, then the rest, each
    newest activity first. At most CATCH_UP_SCAN roots are looked at in all: a page's cursor carries how many came
    before it, so a later page goes on from there and pages, `count` and `catch_up_ids` agree. Each item is {"row",
    "rank", "activity", "mention", "new_count", "unread", "attention", "scanned"}."""
    mentions, held, homes = flags
    history = T._history_only_roots(c, v)
    extra, marks = _names(v, c)
    flagged = sorted(set(mentions) | held | set(homes))
    args = {
        "me": v.actor,
        "flagged": json.dumps(flagged),
        "mentions": json.dumps(sorted(mentions)),
        "history": json.dumps(history),
        **extra,
    }
    by_person, to_viewer = T.unread_terms(c, v)
    is_flagged = "r.id IN (SELECT value FROM json_each(:flagged))"
    is_mention = "r.id IN (SELECT value FROM json_each(:mentions))"
    audience = _audience()
    where = [
        root_open(c, v),
        T.readable_sql(c, v, "r"),
        f"(({audience} IN ('company','team','members','personal','participants','task') AND "
        f"({_in(marks)} OR {PINNED_SQL})) OR ({audience}='home' AND r.owner_id=:me) OR {is_flagged})",
        f"({is_flagged} OR (r.id NOT IN (SELECT value FROM json_each(:history)) AND ({by_person} OR {to_viewer})))",
        # A muted root shows only for a mention.
        f"({is_mention} OR NOT EXISTS (SELECT 1 FROM thread_followers mf WHERE mf.thread_id=r.id "
        "AND mf.actor=:me AND mf.notify='muted'))",
    ]
    scanned = int(after[3]) if after else 0
    for rank, phase in ((0, is_mention), (1, f"NOT {is_mention}")):
        if after and int(after[0]) > rank:
            continue
        position = [int(after[1]), str(after[2])] if after and int(after[0]) == rank else None
        while scanned < CATCH_UP_SCAN:
            batch = min(CATCH_UP_BATCH, CATCH_UP_SCAN - scanned)
            cond, at = "", {}
            if position:
                cond = " WHERE activity<:at_seq OR (activity=:at_seq AND id<:at_id)"
                at = {"at_seq": position[0], "at_id": position[1]}
            found = [
                T.thread_row(c, dict(r))
                for r in c.execute(
                    f"SELECT * FROM (SELECT r.*, {ACTIVITY} AS activity FROM conversations r WHERE "
                    f"{' AND '.join([*where, phase])}){cond} ORDER BY activity DESC, id DESC LIMIT :batch",
                    {**args, **at, "batch": batch},
                )
            ]
            T.prime_members(c, v, [r["id"] for r in found], roots=[r["id"] for r in found])
            readable = {r["id"] for r in found if T.thread_readable(c, v, r)}
            counts = _readable_new(c, v, [i for i in readable if i not in history])
            for r in found:
                scanned += 1
                position = [int(r["activity"] or 0), r["id"]]
                if r["id"] not in readable:
                    continue
                mention = r["id"] in mentions
                # In a bot's Home only its replies and mentions for the reader count (`_home_replies`).
                unread = homes.get(r["id"], 0) if _is_bot_home(r, v) else counts.get(r["id"], 0)
                # An item has a new message the reader may read, or an open attention item they read.
                if not unread and r["id"] not in held and not mention:
                    continue
                yield {
                    "row": r,
                    "rank": rank,
                    "activity": position[0],
                    "mention": mention,
                    "new_count": unread or 1,
                    "unread": unread,
                    "attention": mentions.get(r["id"]) or [],
                    "scanned": scanned,
                }
            if len(found) < batch:
                break


def _is_bot_home(row, v):
    owner = str(row.get("owner_id") or "")
    return T.audience_of(row) == "home" and owner.startswith("bot:") and owner != v.actor


def _first(items, n):
    out = []
    for item in items:
        out.append(item)
        if len(out) >= n:
            break
    return out


def catch_up_count(c, v):
    """(how many Catch up items the reader has, at most CATCH_UP_SHOWN; their root ids): the sidebar's badge, and the
    set a client keeps from the live stream. It stops at CATCH_UP_SHOWN and reads no message text."""
    v = _viewer(v)
    found = _first(_walk(c, v, _flags(c, v)), CATCH_UP_SHOWN)
    return len(found), [it["row"]["id"] for it in found]


def catch_up(c, v, *, cursor=None, limit=CATCH_UP_LIMIT):
    """{"items", "count", "next_cursor"}: each root with something new for the reader, and each open mention, ask or
    held write, the ones that mention them first, then newest activity. An item is its thread view, `new_count` (the
    new messages of its subtree the reader may read, or 1 for an attention item alone), `mention`, and its new messages
    in tree order, at most CATCH_UP_MESSAGES, each with its `depth` and through the per-message check; `more` counts the
    rest the reader may read. Cards and their messages stop at the page's byte budget, and a message's text at
    CATCH_UP_BODY. `count` is every item, at most CATCH_UP_SHOWN. Reading one is POST .../read-all; muting one is
    following it with notify `muted`."""
    v = _viewer(v)
    limit = _limit(limit, CATCH_UP_LIMIT, CATCH_UP_MAX_LIMIT)
    after = _decode(cursor, 4)
    if after:
        if not all(isinstance(after[i], int) and not isinstance(after[i], bool) for i in (0, 1, 3)):
            raise Problem("cursor", "Unknown cursor", 422)
        after[3] = max(0, min(after[3], CATCH_UP_SCAN))
    flags = _flags(c, v)
    if after:
        found = _first(_walk(c, v, flags, after), limit + 1)
        count = len(_first(_walk(c, v, flags), CATCH_UP_SHOWN))
    else:
        found = _first(_walk(c, v, flags), max(limit + 1, CATCH_UP_SHOWN))
        count = min(len(found), CATCH_UP_SHOWN)
    page, more = found[:limit], len(found) > limit
    v._members = {}
    try:
        shown = _views(c, v, [it["row"] for it in page])
    finally:
        v._members = None
    items, budget, tail = [], T.MAX_PAGE_BYTES, None
    for it in page:
        head = {"thread": shown[it["row"]["id"]], "new_count": it["new_count"], "mention": it["mention"]}
        head_size = len(json.dumps(head, default=str)) + 32
        if items and head_size > budget:
            more = True
            break
        messages, rest, used = _new_messages(
            c, v, it["row"], it["attention"], it["unread"], budget - head_size, force=not items
        )
        if items and not messages and rest:
            more = True
            break
        items.append({**head, "messages": messages, "more": rest})
        budget -= head_size + used
        tail = it
    return {
        "items": items,
        "count": count,
        "next_cursor": (
            _encode([tail["rank"], tail["activity"], tail["row"]["id"], tail["scanned"]]) if more and tail else None
        ),
    }


def _clip(item, budget):
    """A message cut down to fit `budget` bytes, marked `clipped`: first without its refs and reactions, then with its
    text cut to CATCH_UP_CLIP too. (size, item)."""
    small = {k: val for k, val in item.items() if k != "reactions"}
    small.update(refs={}, clipped=True)
    size = len(json.dumps(small, default=str)) + 2
    if size > budget and len(str(small.get("body") or "")) > CATCH_UP_CLIP:
        small["body"] = str(small["body"])[:CATCH_UP_CLIP].rstrip() + "…"
        size = len(json.dumps(small, default=str)) + 2
    return size, small


def _new_messages(c, v, root, attention_ids, unread, budget, *, force=False):
    """([message], more, bytes used): the new messages of a root's open subtree for the reader, in tree order (a message,
    then the sub-thread under it), the first CATCH_UP_MESSAGES that pass the per-message check and fit `budget` bytes
    (a message that does not fit goes clipped; with `force`, the first one always goes), and how many more the reader
    may read (`unread`, the walk's count, plus the open mentions and asks already read). An open mention's or ask's own
    message is among them even when it was read. Candidates are taken in tree order from every unread sub-thread (at
    most CATCH_UP_THREADS), a few per thread at a time, and at most CATCH_UP_READS batches are read whole."""
    import heapq

    mark, subtree, unread_sql = T._subtree_mark(c, v), T._SUBTREE.format(me="?"), T.unread_message_sql()
    places = {
        r["id"]: dict(r)
        for r in c.execute(
            f"SELECT s.id, s.path FROM {subtree}WHERE (s.root_id=? OR s.id=?) AND {T._SUBTREE_OPEN} "
            f"AND s.last_unread_seq>{mark} ORDER BY s.last_unread_seq DESC LIMIT ?",
            (v.actor, root["id"], root["id"], CATCH_UP_THREADS),
        )
    }
    extras, total = [], int(unread or 0)
    for mid in attention_ids:
        found = H.message(c, mid)
        place = T.thread_row(c, found["conversation_id"]) if found else None
        if not place or (place.get("root_id") or place["id"]) != root["id"]:
            continue
        new = (
            c.execute(
                f"SELECT 1 FROM {subtree}JOIN messages m ON m.conversation_id=s.id AND m.seq>{mark} "
                f"WHERE s.id=? AND m.id=? AND {unread_sql}",
                (v.actor, place["id"], mid, v.actor, v.actor),
            ).fetchone()
            is not None
        )
        total += not new  # an unread one is in `unread` already
        if new and place["id"] in places:
            continue  # its thread's candidates hold it
        places.setdefault(place["id"], {"id": place["id"], "path": place.get("path")})
        extras.append({"id": mid, "conversation_id": place["id"], "seq": int(found.get("seq") or 0)})
    rows, _ = T.read_threads(c, v, [t for t in places if t != root["id"]])
    places = {t: p for t, p in places.items() if t == root["id"] or (rows.get(t) and T.thread_readable(c, v, rows[t]))}
    # A message's place in tree order: the seqs of the messages its thread hangs from, then its own.
    paths = {tid: [p for p in str(t.get("path") or f"/{tid}/").split("/") if p] for tid, t in places.items()}
    anchors = {}
    for part in H.chunks(sorted({p for path in paths.values() for p in path[1:]})):
        for r in c.execute(
            "SELECT s.id, (SELECT seq FROM messages WHERE id=s.parent_message_id) FROM conversations s "
            f"WHERE s.id IN ({','.join('?' * len(part))})",
            part,
        ):
            anchors[r[0]] = int(r[1] or 0)
    keys = {tid: [anchors.get(p, 0) for p in path[1:]] for tid, path in paths.items()}
    per = CATCH_UP_MESSAGES + 5
    heap, left, last, full, extra_ids = [], {}, {}, set(), {m["id"] for m in extras}

    def take(found):
        for m in found:
            tid = m["conversation_id"]
            last[tid] = max(last.get(tid, 0), m["seq"])
            if m["id"] in extra_ids:
                continue  # it goes once, as an open mention's or ask's message
            heapq.heappush(heap, (keys[tid] + [m["seq"]], m["id"], tid))
            left[tid] = left.get(tid, 0) + 1

    light = f"SELECT m.id, m.conversation_id, m.seq FROM {subtree}JOIN messages m ON m.conversation_id=s.id AND m.seq>{mark} "
    for part in H.chunks(list(places)):
        found = [
            dict(r)
            for r in c.execute(
                f"SELECT id, conversation_id, seq FROM (SELECT m.id, m.conversation_id, m.seq, ROW_NUMBER() OVER "
                f"(PARTITION BY m.conversation_id ORDER BY m.seq) AS n FROM {subtree}JOIN messages m ON "
                f"m.conversation_id=s.id AND m.seq>{mark} WHERE s.id IN ({','.join('?' * len(part))}) AND {unread_sql}) "
                "WHERE n<=?",
                (v.actor, *part, v.actor, v.actor, per),
            )
        ]
        counted = {}
        for m in found:
            counted[m["conversation_id"]] = counted.get(m["conversation_id"], 0) + 1
        full.update(t for t, n in counted.items() if n >= per)
        take(found)
    for m in extras:
        if m["conversation_id"] in places:
            heapq.heappush(heap, (keys[m["conversation_id"]] + [m["seq"]], m["id"], ""))

    def pop():
        _, mid, tid = heapq.heappop(heap)
        if tid:
            left[tid] -= 1
            if not left[tid] and tid in full:
                full.discard(tid)
                more = [
                    dict(r)
                    for r in c.execute(
                        f"{light}WHERE s.id=? AND m.seq>? AND {unread_sql} ORDER BY m.seq LIMIT ?",
                        (v.actor, tid, last[tid], v.actor, v.actor, per),
                    )
                ]
                if len(more) >= per:
                    full.add(tid)
                take(more)
        return mid

    known = v.known(c)
    out, used, cut, reads = [], 0, False, 0
    while heap and not cut and len(out) < CATCH_UP_MESSAGES and reads < CATCH_UP_READS:
        reads += 1
        chunk = []
        while heap and len(chunk) < per:
            chunk.append(pop())
        found = {
            r["id"]: dict(r)
            for r in c.execute(f"SELECT * FROM messages WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        }
        marks = T.reactions(c, chunk)
        for mid in chunk:
            m = found.get(mid)
            if m is None or not T.message_readable(c, v, m, thread_checked=True):
                continue
            item = {**known.message(T._message_out(m)), "depth": len(paths[m["conversation_id"]]) - 1}
            if len(str(item.get("body") or "")) > CATCH_UP_BODY:
                item.update(body=str(item["body"])[:CATCH_UP_BODY].rstrip() + "…", clipped=True)
            if mid in marks:
                item["reactions"] = known.reactions(marks[mid], m["conversation_id"])
            size = len(json.dumps(item, default=str)) + 2
            if size > budget - used:
                size, item = _clip(item, budget - used)
                if size > budget - used and (out or not force):
                    cut = True
                    break
            out.append(item)
            used += size
            if len(out) >= CATCH_UP_MESSAGES:
                break
    return out, max(0, total - len(out)), used
