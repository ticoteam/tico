"""Backlog warnings: when work piles up at a step, someone hears of it (a step's flow `backlog`, backend/step_flow.py).

`backlog: {"over": n, "per": "step" | "owner", "notify": [...], "every_hours": h}`:
- `per: step` counts the open tickets at the step; `per: owner` counts each owner's open tickets there, and warns for
  each owner over `over` separately.
- `notify` names who hears it, each one of: `manager` (the bot that manages the type), `owner` (the owner over, with
  `per: owner`), `owner_manager` (one level up from that owner on the org chart), or a named bot or person
  (`bot:<slug>`, `human:<id>`, or a bare bot slug).
- `every_hours` (default EVERY_HOURS): one warning per step (and owner) at most this often while it stays over.

`warnings` finds the steps over their limit and sends nothing. The task roundup lists each warning on the list of every
bot it names, as a line on the oldest ticket waiting there (backend/task_roundup.py), at most once per `every_hours`. Only
bots get the line: a person named here is not told by the roundup. A type's manager on the Manager profile is not listed:
the backlog is on its exceptions list (backend/pipeline_exceptions.py).

Only tickets every member may read are counted: a private ticket, or one owned or asked for by a bot not every member
may read (updates.restricted_bots), never counts toward `over`, never names an owner in the spread and never hosts the
warning. Everyone who reads the room reads the warning, so such a ticket changes nothing a warning says."""
from . import hubdb as H

EVENT = "task.step_backlog"
EVERY_HOURS = 4
PER = ("step", "owner")
ROLES = ("manager", "owner", "owner_manager")
FINISHED = ("done", "closed", "declined")
MAX_OVER = 10_000


def clean(c, actor, raw):
    """A step's `backlog` setting as stored, or refused."""
    if not isinstance(raw, dict) or set(raw) - {"over", "per", "notify", "every_hours"}:
        H.refuse(c, actor, "kind", "A step's backlog takes over, per, notify and every_hours")
    over = raw.get("over")
    if isinstance(over, bool) or not isinstance(over, int) or not 0 <= over <= MAX_OVER:
        H.refuse(c, actor, "kind", f"A step's backlog over is a whole number from 0 to {MAX_OVER}")
    per = raw.get("per", "step")
    if per not in PER:
        H.refuse(c, actor, "kind", "A step's backlog per is step or owner")
    notify = raw.get("notify") or ["manager"]
    if isinstance(notify, str):
        notify = [notify]
    out = []
    for who in notify:
        who = str(who or "").strip()
        if who in ROLES:
            if who != "manager" and per != "owner":
                H.refuse(c, actor, "kind", f"A step's backlog notifies {who} only when it counts per owner")
        else:
            if not who.startswith(("bot:", "human:")):
                who = "bot:" + who
            if who.startswith("bot:"):
                known = H.resolve_bot(c, H.actor_id(who))
                who = "bot:" + known["id"] if known else who
            else:
                known = H.human(c, H.actor_id(who))
            if not known:
                H.refuse(c, actor, "not-found", f"{who} is nobody a step's backlog can notify")
        if who not in out:
            out.append(who)
    every = raw.get("every_hours", EVERY_HOURS)
    if isinstance(every, bool) or not isinstance(every, (int, float)) or not 0 < every <= 24 * 14:
        H.refuse(c, actor, "kind", "A step's backlog every_hours is a number of hours above 0, at most two weeks")
    return {"over": over, "per": per, "notify": out, "every_hours": every}


def _targets(c, rule, row, owner):
    from .lifecycle import _manager_actor
    from .pipeline_exceptions import reads_list
    out = []
    for who in rule.get("notify") or ["manager"]:
        if who == "manager":
            # A manager that works from its exceptions list reads the backlog there, not as a notice.
            # task_types.manager holds the manager's slug (roadmap_flow.LEGACY_MANAGERS); its actor is the bot's id.
            found = H.resolve_bot(c, H.type_manager(c, row)) or {}
            who = H.bot_actor(found["id"]) if found and not reads_list(c, found["id"]) else ""
        elif who == "owner":
            who = owner if per_owner(rule) else ""
        elif who == "owner_manager":
            who = _manager_actor(c, H.actor_id(owner)) if per_owner(rule) and H.is_bot(owner) else ""
        if who and who not in out:
            out.append(who)
    return out


def per_owner(rule):
    return rule.get("per") == "owner"


def warnings(c):
    """The steps over their backlog now: one dict each with step, owner (an actor, or None for the whole step), count, over,
    every_hours, key (the step and the owner), text (the warning in words), first (the oldest ticket waiting there, which
    the line hangs on) and targets (the actors it names: bots and people; the roundup lists bots only)."""
    from . import step_flow, updates
    if not step_flow._ready(c):
        return []
    hidden = {H.bot_actor(slug) for slug in updates.restricted_bots(c)}
    out = []
    for step in H._rows(c.execute("SELECT * FROM task_steps WHERE flow LIKE '%\"backlog\"%'")):
        rule = step_flow.of(step).get("backlog")
        if not rule:
            continue
        rows = H._rows(c.execute(
            f"SELECT * FROM {H.TASKS} tasks WHERE step_id=? AND status NOT IN ({','.join('?' * len(FINISHED))}) "
            "ORDER BY created_at, id", (step["id"], *FINISHED)))
        rows = [row for row in rows if not H.task_private(c, row) and not {row["owner_id"], row["requester_id"]} & hidden]
        groups = {}
        for row in rows:
            groups.setdefault(row["owner_id"] if per_owner(rule) else None, []).append(row)
        for owner, waiting in groups.items():
            if len(waiting) <= rule["over"]:
                continue
            by_owner = {}
            for row in waiting:
                who = H.actor_id(row["owner_id"] or "") or "nobody"
                by_owner[who] = by_owner.get(who, 0) + 1
            spread = ", ".join(f"{who} {n}" for who, n in sorted(by_owner.items(), key=lambda x: -x[1])[:6])
            text = (f"Backlog: {len(waiting)} tickets wait at {step['name']}"
                    + (f" for {H.actor_name(c, owner)}" if owner else "") + f" (over {rule['over']}; {spread}). "
                    "Spread them to other owners, reprioritise or close some, or raise the step's backlog limit.")
            out.append({"step": step["name"], "owner": owner, "count": len(waiting), "over": rule["over"],
                        "every_hours": rule.get("every_hours", EVERY_HOURS), "key": step["id"] + ":" + (owner or ""),
                        "text": text, "first": waiting[0], "targets": _targets(c, rule, waiting[0], owner or "")})
    return out
