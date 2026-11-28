# pyright: strict
"""What taking a person off the team moves, worked out before it happens, kept when it happens, and put back on Restore.

`preview` is the one read behind Settings > People > Remove, `tico human leaving` and
`GET /api/v2/humans/{pid}/leaving`. `access.leave` applies what it lists and stores `record` on a `person.left`
event; `restore` reads that event back: the reports move back to the person (all but those moved by hand since), and
it names the plans that went, which only the person can reconnect."""

import json
from typing import Any, cast

from . import hubdb as H_
from . import people as P_

Row = dict[str, Any]
# Untyped helpers, as Any so this strict module checks its own code (as backend/flow_ping.py does).
H: Any = H_
P: Any = P_

LEFT = "person.left"


def plans(c: Any, owner: str) -> list[Row]:
    """The model plans `owner` (an actor) connected that leaving removes, each with the bots that lose their model:
    the plans they own, then their pooled seat. One read for the preview and for the leave itself."""
    if not H._has_table(c, "model_subscriptions"):
        return []
    from .model_subscriptions import KINDS

    out: list[Row] = []
    for s in c.execute(
        "SELECT id,name,kind FROM model_subscriptions WHERE owner_id=? ORDER BY created_at,id", (owner,)
    ).fetchall():
        bots: list[str] = [
            r[0]
            for r in c.execute("SELECT bot FROM agent_subscriptions WHERE subscription_id=? ORDER BY bot", (s["id"],))
        ]
        kind = (KINDS.get(s["kind"]) or {}).get("name") or s["kind"]
        out.append({"id": s["id"], "name": s["name"] or kind, "kind": kind, "bots": bots})
    pooled: list[str] = [
        r[0]
        for r in c.execute(
            "SELECT DISTINCT bot FROM agent_subscriptions WHERE kind='pool' AND person=? ORDER BY bot", (owner,)
        )
    ]
    if pooled:
        out.append({"id": "", "name": "Pooled plans", "kind": "pool", "bots": pooled})
    return out


def reports(people: list[Row], pid: str) -> list[str]:
    """The people who report to `pid` and move up when they leave."""
    return [p["id"] for p in people if p.get("reports_to") == pid and p["id"] != pid]


def record(c: Any, people: list[Row], row: Row) -> Row:
    """What `access.leave` stores on the `person.left` event, read before it changes anything."""
    owner = H.human_actor(row["id"])
    found = plans(c, owner)
    return {
        "reports": reports(people, row["id"]),
        "to": row.get("reports_to") or "",
        "plans": [{"name": p["name"], "kind": p["kind"], "bots": list(p["bots"])} for p in found],
        "bots": sorted({b for p in found for b in p["bots"]}),
    }


def _bot(c: Any, bot: str) -> Row:
    row: Row = H.bot(c, bot) or {}
    return {"id": bot, "slug": row.get("slug") or bot, "name": row.get("display_name") or row.get("slug") or bot}


def _name(roster: Row, pid: str) -> Row:
    person: Row = P.person(pid, roster) or {}
    return {"id": pid, "name": person.get("name") or person.get("email") or pid}


def preview(c: Any, roster: Row, pid: str) -> Row:
    """What removing `pid` would do, changing nothing. Every list is empty when it has nothing to move."""
    person: Row | None = P.person(pid, roster)
    owner = H.human_actor(pid)
    people: list[Row] = roster.get("people") or []
    found = plans(c, owner)
    to: str = (person.get("reports_to") or "") if person else ""
    who = (owner, pid)
    tasks: int = c.execute(
        "SELECT count(*) FROM tasks WHERE owner_id IN (?,?) AND status='open'",
        who,
    ).fetchone()[0]
    asks: int = (
        c.execute(
            "SELECT count(*) FROM question_marks WHERE cleared_at IS NULL AND person_id IN (?,?)",
            who,
        ).fetchone()[0]
        if H._has_table(c, "question_marks")
        else 0
    )
    mailbot: Row | None = None
    if person and person.get("inbox_bot"):
        from . import mailbox_access as mailbox_access_

        mailbox: Any = mailbox_access_

        mailbot = {
            **_bot(c, person["inbox_bot"]),
            "address": mailbox.message_bot_mailbox(c, person, person["inbox_bot"]),
        }
    org: dict[str, Row] = roster.get("org_groups") or {}
    groups = [
        {"id": gid, "name": g.get("name") or gid} for gid, g in sorted(org.items()) if g.get("lead") == "person:" + pid
    ]
    return {
        "person": _name(roster, pid),
        "plans": [{**p, "bots": [_bot(c, b) for b in p["bots"]]} for p in found],
        "bots": [_bot(c, b) for b in sorted({b for p in found for b in p["bots"]})],
        "reports": [_name(roster, r) for r in reports(people, pid)],
        "to": _name(roster, to) if to else None,
        "tasks": tasks,
        "asks": asks,
        "message_bot": mailbot,
        "groups": groups,
    }


def last_record(c: Any, pid: str) -> tuple[Row, int] | None:
    """The newest `person.left` record for `pid`, with its event's rowid (what came after it is newer)."""
    row = c.execute(
        "SELECT detail,rowid FROM events WHERE action=? AND target=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (LEFT, pid),
    ).fetchone()
    if not row:
        return None
    try:
        found: Any = json.loads(row[0] or "{}")
    except ValueError:
        return None
    return (cast(Row, found), int(row[1])) if isinstance(found, dict) else None


def moved_by_hand(c: Any, rid: str, after: int) -> bool:
    """Whether someone set `rid`'s manager since the event at rowid `after`: in Settings (or the API), or through a
    directory sync. A move back to the same manager still counts: that person chose where they report."""
    for action, detail in c.execute(
        "SELECT action,detail FROM events WHERE target=? AND rowid>? AND action IN"
        " ('person.updated','directory.person_updated')",
        (rid, after),
    ):
        try:
            seen: Any = json.loads(detail or "{}")
        except ValueError:
            continue
        if not isinstance(seen, dict):
            continue
        said = cast(Row, seen)
        if action == "person.updated" and said.get("reports_to") is not None:
            return True
        if action == "directory.person_updated" and "manager" in (said.get("fields") or []):
            return True
    return False


def where_left(c: Any, rid: str, to: str, after: int) -> list[str]:
    """The managers removals since the event at rowid `after` may have left `rid` with, who that removal moved to `to`:
    `to`, then, if `to` has left since and their removal moved `rid` on, that manager, and so on up the chain. (If `to`
    is back since, their restore moved `rid` back to them.)"""
    chain: list[str] = []
    while to not in chain:
        chain.append(to)
        found = last_record(c, to) if to else None
        if not found or found[1] <= after or rid not in (found[0].get("reports") or []):
            break
        to, after = found[0].get("to") or "", found[1]
    return chain


def restore(c: Any, people: list[Row], pid: str) -> Row:
    """Move `pid`'s reports back to them, in `people` (the caller's list, mutated), and say what could not come back.
    A report someone gave a manager by hand since the removal stays where they put them, as does one whose manager is
    no longer the one Remove (or a later removal of that manager) moved them to. A report who has left since moves back
    too, so restoring two people in either order, after removing them in either order, rebuilds the chart between them."""
    found = last_record(c, pid)
    kept: Row = found[0] if found else {}
    after = found[1] if found else 0
    index = {p["id"]: i for i, p in enumerate(people)}
    moved: list[Row] = []
    ids: list[str] = kept.get("reports") or []
    for rid in ids:
        i = index.get(rid)
        # Still where Remove put them, or where a later removal of that manager moved them on (Cara leaves, then
        # Ben: Dee went to Ben, then on to Ana, and restoring Cara first brings Dee back from Ana).
        put = where_left(c, rid, kept.get("to") or "", after)
        if i is None or (people[i].get("reports_to") or "") not in put or moved_by_hand(c, rid, after):
            continue
        people[i] = {**people[i], "reports_to": pid}
        if not people[i].get("hidden"):
            moved.append({"id": rid, "name": people[i].get("name") or people[i].get("email") or rid})
    # Each plan with the bots that ran on it. A record from before plans named their bots lists them once, beside.
    plans: list[Row] = [
        {
            "name": p.get("name") or "",
            "kind": p.get("kind") or "",
            **({"bots": [_bot(c, b) for b in p["bots"]]} if "bots" in p else {}),
        }
        for p in cast(list[Row], kept.get("plans") or [])
    ]
    lost: list[str] = kept.get("bots") or []
    me: Row = people[index[pid]] if pid in index else {}
    name = me.get("name") or me.get("email") or pid
    return {"name": name, "reports": moved, "plans": plans, "bots": [_bot(c, b) for b in lost]}
