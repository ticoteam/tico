"""Task caps (redesign/CONTRACT.md §1): how many tasks a teammate may send another each day.

A cap row `{sender, receiver, per_day}` limits the tasks `sender` creates for `receiver`, or hands to them, since local
midnight in the team's time zone. The hub checks it first on every task create and owner change, before automations,
wakes and the LLM checks: over the cap the write is refused with a 409 whose body is the flat payload CONTRACT §1 names,
and no task row is written. A task the sender closed before the receiver changed it leaves the count. Own work (the
one making the change is the owner) is never capped; no row means no cap; 0 blocks the pair.

Who sets a cap: the owner and the bot administrators (anyone), a group lead (the teammates in the groups it leads), and a
bot for its own sends, which is an approval a person decides (kind `task_cap`, POST /api/v2/approvals/{id}); on approve the
hub applies it itself (CONTRACT §5). The receiver must be a manager above the sender at any level of the org chart. Every
change is one audit row (backend/audit_log.py) with the cap before and after. Sends are recorded, so a cap set later counts
today's tasks.
"""

import json
from datetime import datetime, timezone

from fastapi import Request
from pydantic import Field

from . import audit_log
from . import models as M
from .models import ID, Contract
from .store import H, Problem

MAX = 100_000


class Capped(Problem):
    """The 409 a task over its cap gets: the flat payload CONTRACT §1 names. Nothing is written."""

    def __init__(self, payload):
        super().__init__("task_cap", payload["message"], 409)
        self.payload = payload


class TaskCap(Contract):
    """A cap on the tasks `sender` sends `receiver` a day. null (or left out) removes it; 0 blocks the pair."""
    sender: ID
    receiver: ID
    per_day: int | None = Field(default=None, ge=0, le=MAX)


class TaskCapPair(Contract):
    """The two teammates a cap is between, for DELETE."""
    sender: ID
    receiver: ID


# ---------------------------------------------------------------------------------------------------- refs and actors
def ref(actor):
    """An actor as CONTRACT §1 writes it: `person:<id>` or `bot:<slug>`."""
    kind, _, ident = str(actor or "").partition(":")
    return ("person:" if kind == "human" else "bot:") + ident


def actor(value):
    """A teammate ref (`person:<id>`, `bot:<slug>`, or an actor) as an actor string, or None when it is none of those."""
    text = str(value or "").strip()
    if text.startswith("person:"):
        text = "human:" + text[len("person:"):]
    return text if text.startswith(("bot:", "human:")) else None


def _node(found):
    """An actor as an org-chart node: `b:<slug>` or `p:<id>`."""
    kind, _, ident = found.partition(":")
    return ("p:" if kind == "human" else "b:") + ident


def _person(c, pid):
    from . import groups as G
    from . import people as P
    return P.person(pid, G._roster(c))


def _teammate(c, value):
    """The actor a request names, which must be a bot or a person that exists."""
    found = actor(value) or (H.resolve_actor(c, str(value)) if value else None)
    kind, _, ident = (found or "").partition(":")
    exists = (kind == "bot" and H.bot(c, ident)) or (kind == "human" and _person(c, ident))
    if not exists:
        raise Problem("not_found", f"Unknown teammate: {value}", 404)
    return found


def _name(c, who):
    kind, _, ident = who.partition(":")
    if kind == "bot":
        return (H.bot(c, ident) or {}).get("display_name") or ident
    return (_person(c, ident) or {}).get("name") or ident


# ---------------------------------------------------------------------------------------------------- the org chart
def _group_manager(roster, configs, gid):
    """The node above a group: its lead (while a teammate of it), else its reports_to person, else the group it is
    nested in."""
    from . import groups as G
    group = (roster.get("org_groups") or {}).get(gid) or {}
    lead = G.lead_of(gid, roster, configs) if gid in (roster.get("org_groups") or {}) else ""
    if lead:
        found = actor(lead)
        return _node(found) if found else ""
    if group.get("reports_to"):
        return "p:" + group["reports_to"]
    return "g:" + group["parent"] if group.get("parent") else ""


def above(c, who):
    """The teammates above `who` (an actor) on the org chart, nearest first, as actors. A manager at any level counts."""
    from . import groups as G
    from . import people as P
    roster, configs, archived = G._roster(c), G._configs(c), G._archived(c)
    kind, _, ident = who.partition(":")
    seen = {("b:" if kind == "bot" else "p:") + ident}
    out = []
    nxt = P.org_parent("bot" if kind == "bot" else "person", ident, roster, configs, archived, True)
    while nxt and nxt not in seen:
        seen.add(nxt)
        head, _, rest = nxt.partition(":")
        if head == "g":
            nxt = _group_manager(roster, configs, rest)
            continue
        out.append(("human:" if head == "p" else "bot:") + rest)
        nxt = P.org_parent("person" if head == "p" else "bot", rest, roster, configs, archived, True)
    return out


def _team(c, who, roster, configs):
    from . import people as P
    kind, _, ident = who.partition(":")
    if kind == "bot":
        return P.team_of(ident, configs, roster) or ""
    return (P.person(ident, roster) or {}).get("team") or ""


def led(c, who, teammate):
    """Whether `who` (an actor) leads a group `teammate` is in, or a group nested under one it leads."""
    from . import groups as G
    from . import people as P
    roster, configs = G._roster(c), G._configs(c)
    led_ids = {gid for gid in (roster.get("org_groups") or {}) if actor(G.lead_of(gid, roster, configs)) == who}
    team = _team(c, teammate, roster, configs)
    return bool(led_ids and team) and any(team in P.group_and_below(gid, roster) for gid in led_ids)


# ---------------------------------------------------------------------------------------------------- the count
def _midnight():
    """Local midnight in the team's time zone, in the UTC timestamp format every row uses."""
    from .updates import ZONE
    start = datetime.now(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _acted(c, row, receiver):
    """Whether the receiver changed the task after it arrived and before the sender closed it: an event on the task or a
    message in its conversation from the receiver (a status, a step, a comment, an answer)."""
    window = (row["sent_at"], row["closed_at"])     # both ends inclusive: a comment in the same instant as the send counts
    if c.execute("SELECT 1 FROM events WHERE target=? AND actor=? AND ts>=? AND ts<=? LIMIT 1",
                 (row["task_id"], receiver, *window)).fetchone():
        return True
    return bool(row["conversation_id"]) and c.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created_at>=? AND created_at<=? LIMIT 1",
        (row["conversation_id"], receiver, *window)).fetchone() is not None


def counted(c, sender, receiver):
    """The tasks `sender` sent `receiver` today that still count, oldest first. A task the sender closed before the
    receiver acted on leaves the count."""
    rows = c.execute(
        "SELECT s.task_id, s.sent_at, t.title, t.status, t.closed_at, t.closed_by, t.conversation_id "
        "FROM task_cap_log s LEFT JOIN tasks t ON t.id = s.task_id "
        "WHERE s.sender=? AND s.receiver=? AND s.sent_at>=? ORDER BY s.sent_at",
        (sender, receiver, _midnight())).fetchall()
    return [dict(row) for row in rows
            if not (row["closed_by"] == sender and row["closed_at"] and not _acted(c, row, receiver))]


def record(c, task_id, sender, receiver):
    """A send: `sender` created the task for `receiver`, or handed it to them. Every send is kept: a later hand-over of
    the same task adds a row and never takes an earlier send out of its sender's count."""
    if not str(sender).startswith(("bot:", "human:")) or not receiver or sender == receiver:
        return
    c.execute("INSERT OR IGNORE INTO task_cap_log(task_id, sender, receiver, sent_at) VALUES(?,?,?,?)",
              (task_id, sender, receiver, H.now()))


def _payload(c, sender, receiver, cap, today):
    """CONTRACT §1's 409 body. `via` is the manager to go through, set only for a cap of 0."""
    chain = above(c, sender)
    via = chain[chain.index(receiver) - 1] if receiver in chain and chain.index(receiver) > 0 else None
    if cap == 0:
        message = f"{_name(c, receiver)} takes no tasks from {_name(c, sender)}."
        message += f" Go through {_name(c, via)}." if via else ""
    else:
        open_now = sum(1 for row in today if not row["closed_at"])
        message = f"{_name(c, receiver)} takes {cap} tasks a day from {_name(c, sender)} and {open_now} are open."
    payload = {"error": "task_cap", "cap": cap, "count": len(today), "receiver": ref(receiver),
               "today": [{"id": row["task_id"], "title": row["title"] or "", "status": row["status"] or ""} for row in today],
               "message": message + " Nothing was queued."}
    if cap == 0 and via:
        payload["via"] = ref(via)
    return payload


def check(c, sender, owner):
    """Refuse the send `sender` makes `owner` when that pair's cap is used up: raises Capped (a 409) and writes nothing.
    Own work and a pair with no cap row pass."""
    if not sender or not owner or sender == owner:
        return
    row = c.execute("SELECT per_day FROM task_caps WHERE sender=? AND receiver=?", (sender, owner)).fetchone()
    if not row or row["per_day"] is None:
        return
    today = counted(c, sender, owner)
    if len(today) < row["per_day"]:
        return
    raise Capped(_payload(c, sender, owner, row["per_day"], today))


# ---------------------------------------------------------------------------------------------------- the caps
def current(c, sender, receiver):
    row = c.execute("SELECT per_day FROM task_caps WHERE sender=? AND receiver=?", (sender, receiver)).fetchone()
    return row["per_day"] if row else None


def _cap(sender, receiver, per_day):
    return {"sender": ref(sender), "receiver": ref(receiver), "per_day": per_day}


def apply(c, actor_, sender, receiver, per_day, via=None):
    """Save a cap (or remove it, with None) and audit the change: the whole cap before and after, null where there was none
    or it is removed. `via` names the approval a bot's change came through."""
    before = current(c, sender, receiver)
    if before == per_day:
        return
    if per_day is None:
        c.execute("DELETE FROM task_caps WHERE sender=? AND receiver=?", (sender, receiver))
    else:
        c.execute("INSERT INTO task_caps(sender, receiver, per_day, updated_at, updated_by) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(sender, receiver) DO UPDATE SET per_day=excluded.per_day, updated_at=excluded.updated_at, "
                  "updated_by=excluded.updated_by", (sender, receiver, per_day, H.now(), actor_))
    audit_log.write(c, actor_, f"task_cap:{ref(sender)}>{ref(receiver)}",
                    None if before is None else _cap(sender, receiver, before),
                    None if per_day is None else _cap(sender, receiver, per_day), via)


def propose(c, who, sender, receiver, per_day):
    """A bot's own cap as an approval a person decides (kind task_cap, the payload is the change). Returns its id."""
    from .proposals import decider
    row = H.approval_request(c, who.actor, "task_cap", {"sender": ref(sender), "receiver": ref(receiver),
                                                        "per_day": per_day}, to=decider(c))
    return row["id"]


def apply_approved(c, actor_, aid):
    """A person approved a bot's cap change: the hub applies it, audited with the approval as `via` (backend/proposals.py
    spends the approval and tells BotOps). The receiver must still be a manager above the sender."""
    change = H.approval(c, aid)["payload"]
    sender, receiver = _teammate(c, change["sender"]), _teammate(c, change["receiver"])
    if receiver not in above(c, sender):
        raise Problem("validation", "The receiver is no longer a manager above the sender", 422)
    apply(c, actor_, sender, receiver, change.get("per_day"), via=f"approval:{aid}")


def forget(c, teammate):
    """A teammate moved to another group: the caps that name it go, and so do the cap approvals a person has yet to decide,
    so its new pairs start uncapped. Each removed cap is audited as the hub's own change."""
    if not teammate:
        return
    for row in c.execute("SELECT sender, receiver FROM task_caps WHERE sender=? OR receiver=?",
                         (teammate, teammate)).fetchall():
        apply(c, H.KEEPER, row["sender"], row["receiver"], None, via="group move")
    named = ref(teammate)
    for row in c.execute("SELECT id, payload FROM approvals WHERE kind='task_cap' AND decision IS NULL").fetchall():
        change = json.loads(row["payload"] or "{}")
        if named in (change.get("sender"), change.get("receiver")):
            H.approval_withdraw(c, row["id"], "A teammate in it moved group.")


def may_set(c, auth, who, sender):
    """`direct` for the owner, the bot administrators and a group lead over the sender; `proposal` for the bot itself;
    anything else is refused."""
    if who.role == "owner" or auth.bot_admin(who) or led(c, who.actor, sender):
        return "direct"
    if who.role == "bot" and who.actor == sender:
        return "proposal"
    raise Problem("forbidden", "Only the owner, an admin or the sender's group lead sets a task cap; a bot proposes its own",
                  403)


def _proposals(c):
    """The bots' cap changes a person has yet to decide: undecided approvals of kind task_cap."""
    out = []
    for row in c.execute("SELECT id, requested_by, created_at, payload FROM approvals "
                         "WHERE kind='task_cap' AND decision IS NULL ORDER BY created_at"):
        change = json.loads(row["payload"] or "{}")
        out.append({"id": row["id"], "sender": change.get("sender"), "receiver": change.get("receiver"),
                    "per_day": change.get("per_day"), "status": "pending", "proposed_by": ref(row["requested_by"]),
                    "created_at": row["created_at"]})
    return out


def tally(c, sender, receiver):
    """Today's (count, closed) for a pair: the tasks that still count, and the ones the sender closed before the receiver
    acted on (they leave the count)."""
    sent = c.execute("SELECT COUNT(*) FROM task_cap_log WHERE sender=? AND receiver=? AND sent_at>=?",
                     (sender, receiver, _midnight())).fetchone()[0]
    count = len(counted(c, sender, receiver))
    return count, sent - count


def _listing(c, auth, who):
    caps = []
    for r in c.execute("SELECT * FROM task_caps ORDER BY sender, receiver"):
        count, closed = tally(c, r["sender"], r["receiver"])
        caps.append({"sender": ref(r["sender"]), "receiver": ref(r["receiver"]), "per_day": r["per_day"],
                     "count": count, "closed": closed, "updated_at": r["updated_at"], "updated_by": ref(r["updated_by"])})
    proposals = _proposals(c) if (who.role == "owner" or auth.bot_admin(who)) else []
    return {"caps": caps, "proposals": proposals}


# ---------------------------------------------------------------------------------------------------- the routes
def install(app, store, auth, mutate):
    def set_cap(c, who, sender_ref, receiver_ref, per_day):
        sender, receiver = _teammate(c, sender_ref), _teammate(c, receiver_ref)
        if sender == receiver:
            raise Problem("validation", "A teammate has no cap on its own work", 422)
        if receiver not in above(c, sender):
            raise Problem("validation", f"{receiver_ref} is not a manager above {sender_ref}", 422)
        if may_set(c, auth, who, sender) == "proposal":
            aid = propose(c, who, sender, receiver, per_day)
            return {"approval": aid, "status": "pending",
                    "cap": {"sender": ref(sender), "receiver": ref(receiver), "per_day": per_day}}
        apply(c, who.actor, sender, receiver, per_day)
        return {"cap": {"sender": ref(sender), "receiver": ref(receiver), "per_day": per_day}}

    @app.get("/api/v2/task-caps")
    def caps_list(request: Request):
        """The task caps, and for the owner and admins the proposals a person has yet to decide."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return _listing(c, auth, who)

    @app.put("/api/v2/task-caps")
    def caps_put(request: Request, body: TaskCap):
        """Cap the tasks one teammate sends another a day (0 blocks the pair; null removes the cap). The receiver must be
        a manager above the sender. A bot's own cap comes back as a pending proposal."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            return set_cap(c, who, body.sender, body.receiver, body.per_day)
        return mutate(request, body, work)

    @app.delete("/api/v2/task-caps")
    def caps_delete(request: Request, sender: str, receiver: str):
        """Remove the cap between two teammates (`?sender=&receiver=`). The same rules as setting it: a bot's removal is a
        proposal too."""
        who = request.state.identity
        auth.domain(who)
        body = TaskCapPair(sender=sender, receiver=receiver)

        def work(c):
            return set_cap(c, who, body.sender, body.receiver, None)
        return mutate(request, body, work)

