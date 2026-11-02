"""What the Strategy Bot does alone at level 3, and what it never does (docs/goals-and-kpis.md, "Your Strategy Bot";
spec: Head of Product, Strategy Bot, tests 9, 12 and 15).

At level 3 ("Acts on its own") the Strategy Bot makes a decision alone: one reason and a few changes to bot tasks, made
together under one action id (`decide`). The changes it may make are `create` (a new task for a bot), `reorder` (a bot's
task to the top of its queue), `reassign` (a bot's task to another bot) and `close` (a bot's task). Every change is
written with the action id on its task events, and the decision is a row in `strategy_actions`, the Strategy Bot log on
its page, newest first. Below level 3 it decides nothing alone: a recovery is a proposal (backend/strategy_suggest.py).

The owner undoes a decision (`undo`): each task it changed goes back to what it was, field by field, and the bot that
owns it is told. A field someone changed since is kept as they left it and the result says so in plain words: Undo never
overwrites a later edit silently. A task the decision made is closed, unless someone has changed it since. What one task
can't take back (a refusal) is said too, and the rest of the decision is still undone.

"Always the person's" (`PERSON_ONLY`) is checked on the server at every level (`gate`): anything that raises cost (wake
schedules, routines, models), publishing, sending outside the company, adding or removing bots, changing a goal or KPI
target, changing the vision, changing a bot's instructions, and any task for a person. The Strategy Bot never does
these; each try becomes a proposal in the owner's Needs you that says what it wanted and why it can't do it itself
(`Proposed`). A change in a decision that can't be made now (a person's task, a bot that is offline, a refusal) is never
dropped either: it becomes a proposal the same way, and the log keeps it."""

import contextvars

from . import strategy_bot
from . import hubdb as H

STRATEGY_BOT = "bot:" + strategy_bot.SLUG
ACTIONS = ("create", "reorder", "reassign", "close")
MAX_CHANGES = 10
LOG_PAGE = 50
# What Undo says it put back or kept, in the owner's words.
PLAIN = {"owner": "its owner", "status": "its status", "rank": "its place in the queue", "note": "its note"}
# The fields Undo puts back, with the task events that mean someone changed one since: a step move is a status change.
EVENTS = {"owner": ("owner",), "status": ("status", "step", "type"), "rank": ("rank",), "note": ("note",),
          # A task the decision made is someone else's work once they reword it, so Undo leaves it open.
          "words": ("title", "body")}

# What stays the person's at every level: the kind, and the words the owner reads in Needs you.
PERSON_ONLY = {
    "cost": "anything that raises cost (wake schedules, routines, models)",
    "publish": "publishing",
    "send": "sending anything outside the company",
    "bots": "adding or removing bots",
    "target": "changing a goal or KPI target",
    "vision": "changing the company's vision or description",
    "instructions": "changing a bot's instructions",
    "person_task": "a task for a person",
}

# The action a write belongs to while a decision or its Undo is being made: hubdb._task_event stamps it on every task
# event, so the log can show (and Undo can find) every change of one decision.
ACTION = contextvars.ContextVar("strategy_action", default=None)


class Proposed(H.Refused):
    """The Strategy Bot tried something that stays the person's. The write is undone like any refusal, but it is not one
    of the bot's mistakes (it does not count toward quarantine): `settle` files the proposal after the rollback."""

    def __init__(self, kind, what, why="", task_id=None, said=None):
        self.kind, self.what, self.why, self.task_id = kind, what, why, task_id
        super().__init__("proposed",
                         f"{PERSON_ONLY[kind][0].upper()}{PERSON_ONLY[kind][1:]} stays the owner's at every "
                         f"level: the Strategy Bot does not do it. It is now a proposal in the owner's Needs you: "
                         f"{said or what}")


def acts(c):
    """Whether this install's Strategy Bot acts on its own: level 3."""
    return strategy_bot.level(c) >= 3


def is_strategy_bot(actor):
    """Whether this write is the Strategy Bot's, itself or acting for the owner."""
    return actor == STRATEGY_BOT or H.VIA.get() == STRATEGY_BOT


def gate(c, actor, kind, what, why="", task_id=None):
    """Refuse the Strategy Bot something on the "always the person's" list (`PERSON_ONLY`), at every level. Anyone else
    passes. The caller says what was tried, in plain words (`what`); `settle` makes it a proposal. A try on an existing
    task names it `TASK` in `what` and passes `task_id`: the log names it only to a reader who can open it."""
    if kind not in PERSON_ONLY:
        raise ValueError(kind)
    if is_strategy_bot(actor):
        what = " ".join(str(what or "").split())[:500]
        raise Proposed(kind, what, " ".join(str(why or "").split())[:1000], task_id,
                       _say(c, what, task_id, _company(c), PRIVATE))


def settle(c, exc):
    """After a `Proposed` write is rolled back (backend/store.py `refused`): the proposal in the owner's Needs you and
    the line in the Strategy Bot log. Returns the action id."""
    change = {"do": exc.kind, **({"task_id": exc.task_id} if exc.task_id else {})}
    return _log(c, kind=exc.kind, reason=exc.why, state="proposed",
                left=[{"change": change, "text": exc.what, "why": PERSON_ONLY[exc.kind] + " stays the owner's"}])


# ----------------------------------------------------------------------------- deciding alone
def _name(c, actor):
    if H.is_bot(actor):
        return (H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor)
    return (H.human(c, H.actor_id(actor)) or {}).get("name") or H.actor_id(actor or "")


# A line in the log is kept with TASK where a task's title goes, and named when it is read (`_say`): the title as the
# task is now, and only to a reader who can open it. A title kept from when the task was public never reaches a reader
# after it turned private, or a reader who can't read its owner bot.
TASK = "{task}"
HIDDEN = "a task you can't open"
PRIVATE = "a private task"


def _company(c):
    """Who reads the owner's proposal in Needs you, and the Strategy Bot's own replies: a company task, by its title."""
    return lambda row: not H.task_private(c, row)


def _name_task(c, task_id, can_read, hidden=HIDDEN):
    row = H.task(c, str(task_id)) if task_id else None
    if not row:
        return "a task"
    return f'"{row["title"]}"' if can_read(row) else hidden


def _say(c, line, task_id, can_read, hidden=HIDDEN):
    line = str(line or "")
    return line.replace(TASK, _name_task(c, task_id, can_read, hidden)) if TASK in line else line


# What a change carries in the task's own words: kept out of the log for a private task.
WORDS = ("title", "body", "note")


def _logged(c, item):
    """One done or left item as the log keeps it: a private task's own words are left out."""
    task_id = item.get("task_id") or (item.get("change") or {}).get("task_id")
    row = H.task(c, task_id) if task_id else None
    if not row or not H.task_private(c, row):
        return item
    return {**item, "change": {k: v for k, v in (item.get("change") or {}).items() if k not in WORDS}}


def _refusal(c, row, exc):
    """Why Tico refused a change, for the log: a refusal on a private task may quote it, so only its rule is kept. A
    reader who can't open the task reads only the rule too (`_as_read_by`)."""
    if row and H.task_private(c, row):
        return _refused(exc.rule)
    return exc.detail or str(exc.rule)


def _refused(rule):
    return f"Tico refused it ({rule})"


class Caller:
    """What the Strategy Bot may touch in a decision: no more than its own API calls may. A decision is made on the
    server with Tico's own writes, which check nothing about the caller, so every task it names and every bot it gives a
    task to is checked here first, by the rules a task read and a task write are held to:

    - `reads(row)`: auth.task_sql (`visible`), the SQL behind every task the caller reads. A company task is hidden when
      its owner or requester is a bot the caller may not read, unless the caller is one of them.
    - `would_read(...)`: the same SQL on a task as a change would leave it, so a change never moves a task out of the
      Strategy Bot's sight either.
    - `give(owner, task_id)`: why the Strategy Bot may not hand this bot work (auth.target `write` and bot_contact), or
      None; the route supplies it from the caller's identity.
    - `sees(owner)`: whether the Strategy Bot may see this bot (auth `see`). One it can't see does not exist for it
      (docs/permissions.md): it is answered as a bot that isn't there, and never named (`name`)."""

    def __init__(self, c, visible, give, sees):
        self.c, self.visible, self._give, self._sees = c, visible, give, sees

    def reads(self, row):
        return bool(row) and reads(self.c, self.visible)(row)

    def would_read(self, owner, requester, private=0, guest=None):
        return bool(self.c.execute(f"SELECT 1 FROM (SELECT ? AS owner, ? AS requester, ? AS private, ? AS guest) "
                                   f"WHERE ({self.visible})", (owner, requester, private, guest)).fetchone())

    def give(self, owner, task_id=None):
        return self._give(owner, task_id)

    def sees(self, actor):
        return not H.is_bot(actor) or bool(self._sees(actor))

    def name(self, actor):
        """An actor as the Strategy Bot may name it: a bot it can't see by no more than the words it gave, as for a bot
        that isn't there (`_name`)."""
        return _name(self.c, actor) if self.sees(actor) else H.actor_id(actor)

    def task(self, actor, task_id):
        """The task a change names, refused as a task that isn't there when the Strategy Bot can't read it: the log, the
        proposal and their events then say nothing about it, as for any task write it can't read."""
        row = H.task(self.c, str(task_id)) if task_id else None
        if row and not self.reads(row):
            H.refuse(self.c, actor, "not-found", "Task not found")
        return row


def _bot_task(c, task_id):
    """A task the Strategy Bot may change alone: live, owned by a bot, and company-visible. None otherwise. It never
    acts alone on a private task, even one it may read: the log would carry the task's words (its note), and Undo, made
    as the owner, could neither write it nor give back its guest. One it can't read never gets here (`Caller.task`)."""
    row = H.task(c, str(task_id or ""))
    if not row or row["status"] not in H.ACTIVE_STATUSES or not H.is_bot(row["owner"]):
        return None
    return None if H.task_private(c, row) else row


def _offline(c, actor):
    state = (H.bot(c, H.actor_id(actor)) or {}).get("state")
    return None if state == "active" else f"{_name(c, actor)} is {state or 'gone'} right now"


def describe(c, change, caller):
    """One change in plain words, with TASK for the task it changes. A bot is named only as the Strategy Bot sees it
    (`Caller.name`)."""
    do = change.get("do")
    row = H.task(c, change.get("task_id") or "") if change.get("task_id") else None
    if do == "create":
        return f'New task for {caller.name(change.get("owner", ""))}: "{change.get("title", "")}"'
    if do == "reorder":
        return f"Move {TASK} to the top of {caller.name((row or {}).get('owner', ''))}'s queue"
    if do == "reassign":
        return f"Give {TASK} to {caller.name(change.get('owner', ''))}"
    if do == "close":
        return f"Close {TASK}"
    return PERSON_ONLY.get(do, str(do))


def _keeps_private(c, owner, what):
    """Why a task can't go to a bot that keeps its tasks private, or None. Giving it one makes the task private
    (task_create, task_update): the owner could then neither read it nor put it back or close it, so the Strategy Bot
    does not do it alone, for a new task or a reassigned one."""
    if H.private_tasks_default(c, owner):
        return f"{_name(c, owner)} keeps its tasks private, so giving it {what} stays the owner's"
    return None


def _check(c, change, caller):
    """Why this change can't be made alone, in plain words, with whether it is a person's (`PERSON_ONLY`) — or None.
    Cleans `change` in place: owners become actors and text is trimmed. A task it names that the Strategy Bot can't read
    is refused outright (`Caller.task`); a bot it may not give work to, or a task the change would hide from it, is
    the owner's proposal, said before anything about that bot (its state, its privacy) is."""
    do = change.get("do")
    row = caller.task(STRATEGY_BOT, change.get("task_id"))
    if do not in ACTIONS:
        return f"a change is {'|'.join(ACTIONS)}", None
    if do in ("create", "reassign"):
        owner = H.resolve_actor(c, str(change.get("owner") or "").strip()) or str(change.get("owner") or "")
        if H.is_human(owner):
            change["owner"] = owner
            return "a task for a person stays the person's to give", "person_task"
        if not H.is_bot(owner) or not H.bot(c, H.actor_id(owner)) or not caller.sees(owner):
            # A bot the Strategy Bot can't see is answered as one that isn't there, in its own words, and `change` keeps
            # them: nothing it writes back (the receipt, the proposal) tells the two apart.
            return f"no bot {change.get('owner') or '(none named)'} to give the task to", None
        change["owner"] = owner
        what = "a new task" if do == "create" else TASK
        if why := caller.give(owner, change.get("task_id") if do == "reassign" else None):
            return f"giving {what} to {caller.name(owner)} stays the owner's: {why}", None
    if do == "create":
        change["title"] = " ".join(str(change.get("title") or "").split())[:300]
        change["body"] = str(change.get("body") or "").strip()[:8000]
        if not change["title"]:
            return "a new task needs a title", None
        if not caller.would_read(change["owner"], STRATEGY_BOT):
            return f"a new task for {_name(c, change['owner'])} would be out of the Strategy Bot's sight", None
        return _keeps_private(c, change["owner"], "a new task") or _offline(c, change["owner"]), None
    if row and H.is_human(row["owner"]):
        return f"{TASK} is a person's task, so it stays theirs", "person_task"
    if not _bot_task(c, change.get("task_id")):
        return "name a live bot task (a finished or private one can't be changed)", None
    if do == "reassign" and change["owner"] == row["owner"]:
        return f"{TASK} is already {caller.name(row['owner'])}'s", None
    if do == "reassign" and not caller.would_read(change["owner"], row["requester"], row.get("private") or 0):
        # A new owner drops the guest (task_update), so the task is read as it would be then.
        return f"giving {TASK} to {_name(c, change['owner'])} would put it out of the Strategy Bot's sight", None
    if do == "reassign" and (private := _keeps_private(c, change["owner"], TASK)):
        return private, None
    if do == "close":
        change["note"] = " ".join(str(change.get("note") or "").split())[:1000]
    return _offline(c, row["owner"]) or (_offline(c, change["owner"]) if do == "reassign" else None), None


# How many undone decisions a new one is checked against: Undo is rare, so this reaches back months.
UNDONE_SCAN = 200


def _same(change):
    """What makes two changes the same, once `_check` has cleaned them: a new task by its owner and title, any other
    by its task (and, for a reassign, the bot it goes to)."""
    do = change.get("do")
    if do == "create":
        return do, change.get("owner"), " ".join(str(change.get("title") or "").lower().split())
    return do, change.get("task_id"), change.get("owner") if do == "reassign" else None


def _undone(c, change):
    """Why a change the owner already undid can't be made again alone, or None. Undo of a reorder, reassign or close
    leaves no mark on the task the Strategy Bot reads, so the server keeps the owner's Undo standing: the same change
    again is their proposal, not the bot's decision."""
    key = _same(change)
    for (changes_json,) in c.execute("SELECT changes FROM strategy_actions WHERE "
                                     "kind='decision' AND state='undone' "
                                     "ORDER BY undone_at DESC LIMIT ?", (UNDONE_SCAN,)):
        if any(_same(x.get("change") or {}) == key for x in (H._json(changes_json, None) or {}).get("done") or []):
            return "the owner undid this same change in an earlier decision, so making it again stays theirs"
    return None


FIELDS = ("status", "step_id", "step_rank", "owner", "rank", "note", "closed_at", "closed_by", "version", "private",
          "guest")


def _snapshot(row):
    return {k: row.get(k) for k in FIELDS}


def _was_private(c, item, task):
    """Whether the task was private before the decision (its snapshot); a row logged before that was kept reads the
    task as it is now."""
    before = item.get("before") or {}
    return bool(before["private"]) if "private" in before else H.task_private(c, task)


def _apply(c, change, goal_id, reason):
    """Make one change as the Strategy Bot; returns what Undo needs: the task, and its fields before and after."""
    do = change["do"]
    if do == "create":
        made = H.task_create(c, STRATEGY_BOT, change["title"],
                             (change["body"] + "\n\n" if change["body"] else "") +
                             f"Made by the {strategy_bot.name(c)} on its own: {reason}",
                             change["owner"], goal_id=goal_id or None, lane="company")
        return {"task_id": made["id"], "made": True, "before": None, "after": _snapshot(H.task(c, made["id"]))}
    row = H.task(c, change["task_id"])
    before = _snapshot(row)
    if do == "reorder":
        H.task_update(c, STRATEGY_BOT, row["id"], rank=H._queue_end(c, row["owner"], top=True), mover=True, quiet=True)
    elif do == "reassign":
        H.task_update(c, STRATEGY_BOT, row["id"], owner=change["owner"], mover=True)
    else:
        H.task_close(c, H.KEEPER, row["id"], change["note"] or f"Closed by the {strategy_bot.name(c)}: {reason}")
        c.execute("UPDATE tasks SET closed_by=? WHERE id=?", (STRATEGY_BOT, row["id"]))
    _bump(c, row["id"])
    return {"task_id": row["id"], "made": False, "before": before, "after": _snapshot(H.task(c, row["id"]))}


def _bump(c, task_id):
    """A form or bot holding the task from before this write gets a version conflict, as after any task update."""
    c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))


def decide(c, actor, reason, changes, goal_id=None, *, visible, give, sees):
    """A decision the Strategy Bot makes alone, at level 3: each change made on its own under one action id, the rest (a
    person's task, a bot offline, a refusal) one proposal in the owner's Needs you with the reason. `visible` is the
    Strategy Bot's auth.task_sql, `give` its check on handing a bot work and `sees` whether it may see a bot (`Caller`).
    Returns the action id; the reader reads it with `entry`."""
    H._writer(c, actor)
    if not is_strategy_bot(actor) or H.VIA.get():
        H.refuse(c, actor, "identity", f"Only the {strategy_bot.name(c)} decides alone, and only for itself")
    if not acts(c):
        H.refuse(c, actor, "state",
                 f"The {strategy_bot.name(c)} acts on its own only at level 3, which the owner chooses "
                 "in Settings; propose a recovery instead (POST /api/v2/goal-proposals)")
    reason = " ".join(str(reason or "").split())[:1000]
    if not reason:
        H.refuse(c, actor, "lint", "say why, in a sentence the owner reads in the log")
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        H.refuse(c, actor, "lint", f"a decision makes 1 to {MAX_CHANGES} changes")
    if goal_id and not c.execute("SELECT 1 FROM goals WHERE id=?", (goal_id,)).fetchone():
        H.refuse(c, actor, "not-found", f"no goal {goal_id}")
    caller = Caller(c, visible, give, sees)
    # Checked before anything is written: the log, the proposal and their events must say nothing about a task the
    # Strategy Bot can't read, so such a decision is refused as a task that isn't there, like any task write it can't
    # read. `_check` asks again before each change, by the same rule.
    for change in changes:
        caller.task(actor, change.get("task_id") if isinstance(change, dict) else None)
    action_id = H.new_id()
    done, left = [], []
    token = ACTION.set(action_id)
    try:
        for change in changes:
            change = dict(change) if isinstance(change, dict) else {}
            why, kind = _check(c, change, caller)
            why = why or _undone(c, change)
            if why:
                left.append({"change": change, "text": describe(c, change, caller), "why": why, "kind": kind})
                continue
            c.execute("SAVEPOINT strategy_change")
            try:
                out = _apply(c, change, goal_id, reason)
            except H.Refused as exc:
                c.execute("ROLLBACK TO strategy_change")
                c.execute("RELEASE strategy_change")
                left.append({"change": change, "text": describe(c, change, caller), "rule": exc.rule,
                             "why": _refusal(c, H.task(c, change.get("task_id") or ""), exc)})
                continue
            c.execute("RELEASE strategy_change")
            text = describe(c, change, caller)
            if out["made"]:
                # Its title is the Strategy Bot's, but the task is the company's now: named as it is when read.
                text = f"New task for {_name(c, change['owner'])}: {TASK}"
            done.append({"change": change, "text": text, **out})
    finally:
        ACTION.reset(token)
    return _log(c, kind="decision", reason=reason, goal_id=goal_id, done=done, left=left, action_id=action_id,
                state="done" if done else "proposed")


# ----------------------------------------------------------------------------- the log and its proposals
def _owner(c):
    from . import people as P, views
    roster = views.roster(c) or {}
    person = P.person(roster.get("default_user"), roster) or {}
    return H.human_actor(person["id"]) if person.get("id") else None


def _task_of(item):
    return item.get("task_id") or (item.get("change") or {}).get("task_id")


def _propose(c, reason, left):
    """What the Strategy Bot couldn't do alone, as one item in the owner's Needs you, or why it isn't there (no owner
    yet). Returns (task id or None, note)."""
    owner = _owner(c)
    if not owner:
        return None, "No owner to propose it to yet"
    # The item is a company task: a private task is named there by no title.
    said = [(_say(c, x["text"], _task_of(x), _company(c), PRIVATE), _say(c, x["why"], _task_of(x), _company(c),
                                                                         PRIVATE)) for x in left]
    lines = "\n".join(f"- {text}: {why}" for text, why in said)
    # One item per thing proposed: the same thing tried again finds the item already waiting (a duplicate).
    title = (f"Decide on the {strategy_bot.name(c)}'s proposal: {said[0][0]}" if len(left) == 1 else
             f"Decide on {len(left)} changes the {strategy_bot.name(c)} proposed: {reason}")[:200]
    body = (f"Please decide whether to make these changes yourself; the {strategy_bot.name(c)} may not make them.\n"
            + (f"Why it wanted them: {reason}\n" if reason else "") + lines +
            "\nNothing changed. Make the change yourself if you agree, or close this to leave it.")
    c.execute("SAVEPOINT strategy_propose")
    try:
        # Private, between the owner and the Strategy Bot that asks: it names company tasks by their titles, which a
        # person who reads the owner's work may not be allowed to open. The Strategy Bot stays its requester, so the 5 a
        # day and a repeated proposal are still counted (strategy_bot.asked_today).
        made = H.task_create(c, STRATEGY_BOT, title, body, owner, lint=False, private=True)
    except H.Refused as exc:
        c.execute("ROLLBACK TO strategy_propose")
        c.execute("RELEASE strategy_propose")
        return None, f"Not in Needs you: {exc.detail or exc.rule}; it stays here in the log"
    c.execute("RELEASE strategy_propose")
    return made["id"], ""


def _log(c, *, kind, reason, state, done=(), left=(), goal_id=None, action_id=None):
    proposal, note = _propose(c, reason, list(left)) if left else (None, "")
    row = {"id": action_id or H.new_id(), "created_at": H.now(), "actor": STRATEGY_BOT, "kind": kind,
           "reason": reason or "", "goal_id": goal_id or None, "state": state, "proposal_task": proposal,
           "note": note,
           "changes": H._dump({"done": [_logged(c, x) for x in done], "left": [_logged(c, x) for x in left]})}
    c.execute("INSERT INTO strategy_actions (id, created_at, actor, kind, reason, goal_id, state, proposal_task, note, "
              "changes) VALUES (:id, :created_at, :actor, :kind, :reason, :goal_id, :state, :proposal_task, :note, "
              ":changes)", row)
    H.event(c, STRATEGY_BOT, "strategy_bot." + ("decided" if kind == "decision" else "proposed"), row["id"],
            {"kind": kind, "done": len(done), "left": len(left)})
    return row["id"]


def _stored(c, action_id):
    """A log row as kept: its lines with TASK, for Undo. Nobody reads this; they read `entry`."""
    row = H._one(c, "SELECT * FROM strategy_actions WHERE id=?", (action_id,))
    if not row:
        return None
    changes = H._json(row.pop("changes"), None) or {}
    row["done"], row["left"] = changes.get("done") or [], changes.get("left") or []
    row["undo"] = H._json(row.pop("undo", None), None) or None
    row["undoable"] = row["state"] == "done"
    return row


def reads(c, visible):
    """Whether a reader may open a task, by the SQL that decides what they read (auth.task_sql): a private task's
    parties and guest, and a company task unless its owner or requester is a bot hidden from them."""
    return lambda row: bool(c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({visible})", (row["id"],)).fetchone())


def entry(c, action_id, visible):
    """One log row as a reader reads it; `visible` is their auth.task_sql."""
    row = _stored(c, action_id)
    return _as_read_by(c, reads(c, visible), row) if row else None


def _as_read_by(c, can_read, row):
    """An entry as a reader sees it. Each line names its task as it is now, by its title only when they may open it
    (`_say`); a task they can't open loses its id and its words (title, body, note) everywhere in the line, and a
    refusal on it says only its rule. One task they can't open does not hide the whole log."""
    def readable(task_id):
        found = H.task(c, str(task_id)) if task_id else None
        return bool(found) and can_read(found)

    def item(x):
        task_id = _task_of(x)
        if task_id is None:
            return x
        out = {**x, "text": _say(c, x.get("text"), task_id, can_read)}
        if "why" in x:
            out["why"] = _say(c, x["why"], task_id, can_read)
        if readable(task_id):
            return out
        if x.get("rule"):
            out["why"] = _refused(x["rule"])
        out.pop("task_id", None)
        out["change"] = {k: v for k, v in (x.get("change") or {}).items() if k != "task_id" and k not in WORDS}
        for side in ("before", "after"):
            if isinstance(x.get(side), dict):
                out[side] = {k: v for k, v in x[side].items() if k not in WORDS}
        return out

    def line(x):
        return _say(c, x["text"], x.get("task_id"), can_read) if isinstance(x, dict) else str(x)

    row["done"], row["left"] = [item(x) for x in row["done"]], [item(x) for x in row["left"]]
    if row.get("proposal_task") and not readable(row["proposal_task"]):
        row["proposal_task"] = None
    if row.get("undo"):
        restored, kept = [line(x) for x in row["undo"].get("restored") or []], \
                         [line(x) for x in row["undo"].get("kept") or []]
        row["undo"] = {"restored": restored, "kept": kept, "text": _undo_text(restored, kept)}
    return row


def receipt(row):
    """What the Strategy Bot is told of its own decision: each line as it may read it, without the task fields Undo
    keeps (owner, status, queue place, guest). The log with those is the owner's (GET /api/v2/strategy-bot/log)."""
    def item(x):
        return {k: v for k, v in x.items() if k not in ("before", "after")}
    return {**row, "done": [item(x) for x in row["done"]], "left": [item(x) for x in row["left"]]}


def log(c, visible, before=None, limit=LOG_PAGE):
    """The Strategy Bot log, newest first, as a reader sees it (`visible`, their auth.task_sql): {items, next_before}.
    Paged by `created_at`, then id: `next_before` is "<created>|<id>" of the page's last entry, so entries made in the
    same instant are never skipped between pages. A bare time is read as before that time."""
    limit = max(1, min(int(limit or LOG_PAGE), LOG_PAGE))
    at, _, after_id = str(before or "").partition("|")
    rows = c.execute("SELECT id, created_at FROM strategy_actions WHERE (?='' OR created_at<? OR "
                     "(?!='' AND created_at=? AND id<?)) "
                     "ORDER BY created_at DESC, id DESC LIMIT ?",
                     (at, at, after_id, at, after_id, limit + 1)).fetchall()
    items = [entry(c, r[0], visible) for r in rows[:limit]]
    last = rows[limit - 1] if len(rows) > limit else None
    return {"items": items, "next_before": f"{last[1]}|{last[0]}" if last else None}


# ----------------------------------------------------------------------------- undo
def undo(c, actor, action_id):
    """The owner undoes a decision. Each task it changed goes back field by field where nobody changed the field
    since; a field changed since keeps its later value, and the result says which and by whom. A task it made is
    closed unless someone changed it since. A task Tico won't put back (a refusal) is said, and the others still are.
    Each bot whose task changed is told. Done once. Returns the action id; the reader reads it with `entry`."""
    H._writer(c, actor)
    row = _stored(c, action_id)
    if not row:
        H.refuse(c, actor, "not-found", f"no Strategy Bot decision {action_id}")
    if row["state"] != "done":
        H.refuse(c, actor, "duplicate", "this decision was " + ("undone already" if row["state"] == "undone" else
                                                                "a proposal; nothing was changed to undo"))
    restored, kept = [], []
    token = ACTION.set(action_id)
    try:
        for item in _per_task(row["done"]):
            task = H.task(c, item["task_id"])
            note = lambda text, task_id=item["task_id"]: kept.append({"task_id": task_id, "text": text})  # noqa: E731
            if not task:
                note(f"{item['text']}: the task is gone")
                continue
            if not item.get("made") and _was_private(c, item, task):
                # The decision never acts on a private task (_bot_task); one logged as private before it is left
                # alone, so nothing is refused and its guest keeps it.
                note(f"{TASK} is private, so Undo leaves it as it is")
                continue
            c.execute("SAVEPOINT strategy_undo")
            try:
                did = (_unmake if item.get("made") else _restore)(c, actor, task, item, row["created_at"], note)
            except H.Refused as exc:
                c.execute("ROLLBACK TO strategy_undo")
                c.execute("RELEASE strategy_undo")
                note(f"{TASK} could not be put back: {_refusal(c, task, exc)}")
                continue
            c.execute("RELEASE strategy_undo")
            if did:
                restored.append({"task_id": task["id"], "text": did})
                _bump(c, task["id"])
                _tell(c, actor, H.task(c, task["id"]), item["after"]["owner"])
    finally:
        ACTION.reset(token)
    result = {"restored": restored, "kept": kept}
    c.execute("UPDATE strategy_actions SET state='undone', undone_at=?, undone_by=?, undo=? WHERE id=?",
              (H.now(), actor, H._dump(result), action_id))
    H.event(c, actor, "strategy_bot.undone", action_id, {"restored": len(restored), "kept": len(kept)})
    return action_id


def _per_task(done):
    """One item per task, in the order the decision first changed it: its fields before the decision's first change
    to it and after its last, so two changes to one task are undone together."""
    tasks = {}
    for item in done:
        if item["task_id"] in tasks:
            tasks[item["task_id"]]["after"] = item["after"]
        else:
            tasks[item["task_id"]] = dict(item)
    return list(tasks.values())


def _later(c, task_id, fields, since, action_id):
    """Who last changed any of these task event fields after the decision, or None."""
    marks = ",".join("?" * len(fields))
    found = c.execute(f"SELECT actor FROM task_events WHERE task_id=? AND field IN ({marks}) AND ts>? "
                      "AND coalesce(action_id,'')!=? ORDER BY ts DESC, rowid DESC LIMIT 1",
                      (task_id, *fields, since, action_id)).fetchone()
    return found[0] if found else None


def _value(task, field):
    return (task.get("status"), task.get("step_id")) if field == "status" else task.get(field)


def _changed_since(c, task, after, field, since):
    """Who changed this field after the decision (or "someone", when its value moved with no event), or None."""
    action_id = ACTION.get()
    who = _later(c, task["id"], EVENTS[field], since, action_id)
    if who:
        return who
    if field == "words":
        # The log keeps no copy of a task's title or body, so a change with no event shows only as a newer version
        # than the decision left; a log row from before versions were kept has none to compare.
        return "someone" if after.get("version") is not None and task.get("version") != after["version"] else None
    return "someone" if _value(task, field) != _value(after, field) else None


def _unmake(c, actor, task, item, since, note):
    """Close a task the decision made, unless someone changed it since: then it stays as they left it."""
    if task["status"] == "closed":
        note(f"{TASK}, which it made, was closed since")
        return None
    for field in ("owner", "status", "note", "words"):
        who = _changed_since(c, task, item["after"], field, since)
        if who:
            note(f"{TASK}, which it made, was changed since by {_name(c, who)}, so it stays open")
            return None
    H.task_close(c, H.KEEPER, task["id"],
                 f"Undone by {_name(c, actor)}: the {strategy_bot.name(c)} made this task on its "
                 "own")
    return f"Closed {TASK}, which it made"


def _restore(c, actor, task, item, since, note):
    """Put back each field the decision changed that nobody changed since, in one write; returns what was put back."""
    before, after = item["before"], item["after"]
    back = []
    for field in ("owner", "status", "rank", "note"):
        if _value(before, field) == _value(after, field):
            continue
        who = _changed_since(c, task, after, field, since)
        if who:
            note(f"{TASK}: {PLAIN[field]} was changed since by {_name(c, who)}, so their change stays")
        else:
            back.append(field)
    if "owner" in back and task.get("guest") and task["guest"] != before.get("guest"):
        # A new owner drops the guest (task_update): a guest given the task since keeps it, so its owner stays, with
        # its place in that owner's queue.
        back = [f for f in back if f not in ("owner", "rank")]
        note(f"{TASK} has a guest now, so its owner stays")
    if not back:
        return None
    put = {}
    if "owner" in back:
        put["owner"] = before["owner"]
        # A new owner otherwise puts it at the bottom of their queue: the place goes back too, or stays as it is now.
        put["rank"] = before["rank"] if "rank" in back else task["rank"]
    elif "rank" in back:
        put["rank"] = before["rank"]
    if "status" in back:
        put["status"] = before["status"]
        if before.get("step_id"):
            put["step"], put["step_rank"] = before["step_id"], before.get("step_rank")
    if "note" in back:
        put["note"] = before.get("note") or ""
    # A task private since the decision (moved under a private task) is not the owner's to write: Tico puts it back.
    H.task_update(c, H.KEEPER if H.task_private(c, task) else actor, task["id"], mover=True, **put)
    if "status" in back:
        c.execute("UPDATE tasks SET closed_at=?, closed_by=? WHERE id=?",
                  (before.get("closed_at"), before.get("closed_by"), task["id"]))
    return f"{TASK}: {', '.join(PLAIN[f] for f in back)} put back"


def _tell(c, actor, task, held_by):
    """Tell each bot the Undo changed things for: the task's owner now, and the bot the decision gave it to."""
    said = (f"{_name(c, actor)} undid a decision the {strategy_bot.name(c)} made on its own; "
            "this task is back as it was before it")
    for bot in dict.fromkeys((task["owner"], held_by)):
        if bot and H.is_bot(bot):
            H._wake(c, task, bot, said + ("." if bot == task["owner"] else
                                          f", with {_name(c, task['owner'])}. It is no longer yours."),
                    {"wake": "assignment"} if bot == task["owner"] else None)


def _undo_text(restored, kept):
    text = ("Undone: " + "; ".join(restored) + ".") if restored else "Nothing needed restoring."
    if kept:
        text += " Not restored: " + "; ".join(kept) + "."
    return text
