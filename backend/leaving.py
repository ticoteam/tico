"""What taking a person off the team moves, worked out before it happens, kept when it happens, and put back on Restore.

`preview` is the one read behind Settings > People > Remove, `tico human remove --preview` and
`GET /api/v2/humans/{pid}/leaving`. `access.leave` applies what it lists and stores `record` on a `person.left`
event; `restore` reads that event back: the reports move back to the person (all but those moved by hand since), and
it names the plans that went, which only the person can reconnect."""

from . import hubdb as H
from . import people as P

LEFT = "person.left"


def plans(c, owner):
    """The model plans `owner` (an actor) connected that leaving removes, each with the bots that lose their model:
    the plans they own, then their pooled seat. One read for the preview and for the leave itself."""
    if not H._has_table(c, "model_subscriptions"):
        return []
    from .model_subscriptions import KINDS

    out = []
    for s in c.execute(
        "SELECT id,name,kind FROM model_subscriptions WHERE owner_id=? ORDER BY created_at,id", (owner,)
    ).fetchall():
        bots = [
            r[0]
            for r in c.execute("SELECT bot FROM agent_subscriptions WHERE subscription_id=? ORDER BY bot", (s["id"],))
        ]
        kind = (KINDS.get(s["kind"]) or {}).get("name") or s["kind"]
        out.append({"id": s["id"], "name": s["name"] or kind, "kind": kind, "bots": bots})
    pooled = [
        r[0]
        for r in c.execute(
            "SELECT DISTINCT bot FROM agent_subscriptions WHERE kind='pool' AND person=? ORDER BY bot", (owner,)
        )
    ]
    if pooled:
        out.append({"id": "", "name": "Pooled plans", "kind": "pool", "bots": pooled})
    return out


def reports(people, pid):
    """The people who report to `pid` and move up when they leave."""
    return [p["id"] for p in people if p.get("reports_to") == pid and p["id"] != pid]


def record(c, people, row):
    """What `access.leave` stores on the `person.left` event, read before it changes anything."""
    owner = H.human_actor(row["id"])
    found = plans(c, owner)
    return {
        "reports": reports(people, row["id"]),
        "to": row.get("reports_to") or "",
        "plans": [{"name": p["name"], "kind": p["kind"]} for p in found],
        "bots": sorted({b for p in found for b in p["bots"]}),
    }


def _bot(c, bot):
    row = H.bot(c, bot) or {}
    return {"id": bot, "slug": row.get("slug") or bot, "name": row.get("display_name") or row.get("slug") or bot}


def _name(roster, pid):
    person = P.person(pid, roster) or {}
    return {"id": pid, "name": person.get("name") or person.get("email") or pid}


def preview(c, roster, pid):
    """What removing `pid` would do, changing nothing. Every list is empty when it has nothing to move."""
    person = P.person(pid, roster)
    owner = H.human_actor(pid)
    people = roster.get("people") or []
    found = plans(c, owner)
    to = person.get("reports_to") or "" if person else ""
    who = (owner, pid)
    tasks = c.execute(
        "SELECT count(*) FROM tasks WHERE owner_id IN (?,?) AND status='open'",
        who,
    ).fetchone()[0]
    asks = (
        c.execute(
            "SELECT count(*) FROM question_marks WHERE cleared_at IS NULL AND person_id IN (?,?)",
            who,
        ).fetchone()[0]
        if H._has_table(c, "question_marks")
        else 0
    )
    mailbot = None
    if person and person.get("inbox_bot"):
        from .mailbox_access import message_bot_mailbox

        mailbot = {**_bot(c, person["inbox_bot"]), "address": message_bot_mailbox(c, person, person["inbox_bot"])}
    groups = [
        {"id": gid, "name": g.get("name") or gid}
        for gid, g in sorted((roster.get("org_groups") or {}).items())
        if g.get("lead") == "person:" + pid
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


def last_record(c, pid):
    row = c.execute(
        "SELECT detail FROM events WHERE action=? AND target=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (LEFT, pid),
    ).fetchone()
    if not row:
        return None
    import json

    try:
        return json.loads(row[0] or "{}")
    except ValueError:
        return None


def restore(c, people, pid):
    """Move `pid`'s reports back to them, in `people` (the caller's list, mutated), and say what could not come back.
    A report whose manager is no longer the one they were moved to was moved by hand since: it stays."""
    kept = last_record(c, pid) or {}
    index = {p["id"]: i for i, p in enumerate(people)}
    moved = []
    for rid in kept.get("reports") or []:
        i = index.get(rid)
        if i is None or people[i].get("hidden") or (people[i].get("reports_to") or "") != (kept.get("to") or ""):
            continue
        people[i] = {**people[i], "reports_to": pid}
        moved.append({"id": rid, "name": people[i].get("name") or people[i].get("email") or rid})
    bots = [_bot(c, b) for b in kept.get("bots") or []]
    me = people[index[pid]] if pid in index else {}
    name = me.get("name") or me.get("email") or pid
    return {"name": name, "reports": moved, "plans": list(kept.get("plans") or []), "bots": bots}
