"""What the Strategy Bot does alone at level 3, and what it never does (docs/goals-and-kpis.md, "Your Strategy Bot";
spec: Head of Product, Strategy Bot, tests 9, 12 and 15).

At level 3 ("Acts on its own") the Strategy Bot makes a decision alone: one reason and a few changes to bot tasks, made
together under one action id (`decide`). The changes it may make are `create` (a new task for a bot), `reorder` (a bot's
task to the top of its queue), `reassign` (a bot's task to another bot) and `close` (a bot's task). Every change is
written with the action id on its task events, and the decision is a row in `strategy_actions`, the Strategy Bot log on
its page, newest first. Below level 3 it decides nothing alone: a recovery is a proposal (backend/strategy_suggest.py).

The owner undoes a decision (`undo`): each task it changed goes back to what it was, and the bot that owns it is told.
Undo puts back only what the decision changed, and only on a task nothing happened to since: a task someone changed,
asked or answered on, snoozed or filed a task under after the decision is left as it is, and the result says so in plain
words. A task the decision made is closed on the same terms. What one task can't take back (a refusal) is said too, and
the rest of the decision is still undone.

"Always the person's" (`PERSON_ONLY`) is checked on the server at every level (`gate`): anything that raises cost (wake
schedules, routines, models), publishing, sending outside the company, adding or removing bots, changing a goal or KPI
target, changing the vision, changing a bot's instructions, and any task for a person. The Strategy Bot never does
these; each try becomes a proposal in the owner's Needs you that says what it wanted and why it can't do it itself
(`Proposed`). A change in a decision that can't be made now (a person's task, a bot that is offline, a refusal) is never
dropped either: it becomes a proposal the same way, and the log keeps it."""

import contextvars

from . import strategy_bot
from . import hubdb as H
from . import task_followers as TF
from . import task_trigger_rules as triggers
from .migrations import renames

STRATEGY_BOT = "bot:" + strategy_bot.SLUG
ACTIONS = ("create", "reorder", "reassign", "close")
MAX_CHANGES = 10
LOG_PAGE = 50
# What Undo says it put back or kept, in the owner's words.
PLAIN = {"owner_id": "its owner", "status": "its status", "rank": "its place in the queue", "note": "its note"}
# Task history Tico writes on its own (backend/never_stuck.py): upkeep, not someone acting on the task.
UPKEEP = ("woken_again", "late", "ladder")

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
        super().__init__(
            "proposed",
            f"{PERSON_ONLY[kind][0].upper()}{PERSON_ONLY[kind][1:]} stays the owner's at every "
            f"level: the Strategy Bot does not do it. It is now a proposal in the owner's Needs you: "
            f"{said or what}",
        )


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
        raise Proposed(
            kind, what, " ".join(str(why or "").split())[:1000], task_id, _say(c, what, task_id, _company(c), PRIVATE)
        )


def settle(c, exc):
    """After a `Proposed` write is rolled back (backend/store.py `refused`): the proposal in the owner's Needs you and
    the line in the Strategy Bot log. Returns the action id."""
    change = {"do": exc.kind, **({"task_id": exc.task_id} if exc.task_id else {})}
    return _log(
        c,
        kind=exc.kind,
        reason=exc.why,
        state="proposed",
        left=[{"change": change, "text": exc.what, "why": PERSON_ONLY[exc.kind] + " stays the owner's"}],
    )


# ----------------------------------------------------------------------------- deciding alone
def _name(c, actor):
    if H.is_bot(actor):
        found = H.bot(c, H.actor_id(actor)) or {}
        return found.get("display_name") or found.get("slug") or H.actor_id(actor)
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

    def would_read(self, owner, requester, private=0, readers=None):
        return bool(
            self.c.execute(
                f"SELECT 1 FROM (SELECT NULL AS id, ? AS owner_id, ? AS requester_id, ? AS private, ? AS readers) tasks "
                f"WHERE ({self.visible})",
                (owner, requester, private, readers),
            ).fetchone()
        )

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
    as the owner, could not write it. One it can't read never gets here (`Caller.task`)."""
    row = H.task(c, str(task_id or ""))
    if not row or row["status"] not in H.ACTIVE_STATUSES or not H.is_bot(row["owner_id"]):
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
        return f"Move {TASK} to the top of {caller.name((row or {}).get('owner_id', ''))}'s queue"
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
    if row and H.is_human(row["owner_id"]):
        return f"{TASK} is a person's task, so it stays theirs", "person_task"
    if not _bot_task(c, change.get("task_id")):
        return "name a live bot task (a finished or private one can't be changed)", None
    if do == "reassign" and change["owner"] == row["owner_id"]:
        return f"{TASK} is already {caller.name(row['owner_id'])}'s", None
    if do == "reassign" and not caller.would_read(
        change["owner"], row["requester_id"], row.get("private") or 0, row.get("readers")
    ):
        return f"giving {TASK} to {_name(c, change['owner'])} would put it out of the Strategy Bot's sight", None
    if do == "reassign" and (private := _keeps_private(c, change["owner"], TASK)):
        return private, None
    if do == "close":
        change["note"] = " ".join(str(change.get("note") or "").split())[:1000]
    return _offline(c, row["owner_id"]) or (_offline(c, change["owner"]) if do == "reassign" else None), None


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
    for (changes_json,) in c.execute(
        "SELECT changes FROM strategy_actions WHERE kind='decision' AND state='undone' ORDER BY undone_at DESC LIMIT ?",
        (UNDONE_SCAN,),
    ):
        if any(_same(x.get("change") or {}) == key for x in (H._json(changes_json, None) or {}).get("done") or []):
            return "the owner undid this same change in an earlier decision, so making it again stays theirs"
    return None


FIELDS = (
    "status",
    "owner_id",
    "rank",
    "note",
    "closed_at",
    "closed_by_id",
    "version",
    "private",
    "readers",
)


def _snapshot(row):
    return {k: row.get(k) for k in FIELDS}


def _mark(c, task_id):
    """The task's newest history row: anything written after it happened after the decision."""
    return c.execute(
        "SELECT max(rowid) FROM history_events WHERE subject_type='task' AND subject_id=?", (task_id,)
    ).fetchone()[0]


def _followers(c, row):
    """Who follows the task's thread and how: {actor: [level, reason]}, or None for a task with no thread of its own.
    Giving a task to a bot changes who follows it (task_followers.owner_moved), and giving it back starts the old
    owner at `live`."""
    tid = TF.thread_id(c, row)
    if not tid:
        return None
    return {
        actor: [notify, reason]
        for actor, notify, reason in c.execute(
            "SELECT actor, notify, reason FROM thread_followers WHERE thread_id=? AND following=1", (tid,)
        )
    }


def _refollow(c, task, item, left):
    """Each follower the decision changed and nobody changed since (`left`: as Undo found them) back as it was."""
    was, then = (item.get("follows") or {}).get("before"), (item.get("follows") or {}).get("after")
    tid = TF.thread_id(c, task)
    if was is None or then is None or left is None or not tid:
        return
    for actor in sorted({*was, *then, *(_followers(c, task) or {})}):
        if left.get(actor) != then.get(actor):
            continue
        if actor in was:
            level, reason = was[actor]
            TF._upsert(c, tid, actor, reason, level)
            c.execute(
                "UPDATE thread_followers SET notify=?, reason=? WHERE thread_id=? AND actor=?",
                (level, reason, tid, actor),
            )
        else:
            c.execute(
                "UPDATE thread_followers SET following=0, reason='read', updated_at=? WHERE thread_id=? AND actor=? "
                "AND following=1",
                (H.now(), tid, actor),
            )


def _changed(c, task_id):
    """What Undo needs of a task the decision changed: its fields after, and where its history stood."""
    return {"after": _snapshot(H.task(c, task_id)), "mark": _mark(c, task_id)}


def _was_private(c, item, task):
    """Whether the task was private before the decision (its snapshot); a row logged before that was kept reads the
    task as it is now."""
    before = item.get("before") or {}
    return bool(before["private"]) if "private" in before else H.task_private(c, task)


def _open_under(c, task_id, seen=None):
    """Every open task a close of this one closes with it (task_engine._close_under: the tasks it filed, however deep,
    through ended ones in between)."""
    seen = seen if seen is not None else set()
    out = []
    for child, status in c.execute(
        "SELECT t.id, t.status FROM links l JOIN tasks t ON t.id=l.to_task_id WHERE l.from_task_id=? "
        "ORDER BY l.created_at, l.id",
        (task_id,),
    ).fetchall():
        if child in seen:
            continue
        seen.add(child)
        if status == "open":
            out.append(child)
        out.extend(_open_under(c, child, seen))
    return out


def _apply(c, change, goal_id, reason):
    """Make one change as the Strategy Bot; returns what Undo needs: the task, and its fields before and after."""
    do = change["do"]
    if do == "create":
        made = H.task_create(
            c,
            STRATEGY_BOT,
            change["title"],
            (change["body"] + "\n\n" if change["body"] else "")
            + f"Made by the {strategy_bot.name(c)} on its own: {reason}",
            change["owner"],
            goal_id=goal_id or None,
            lane="company",
        )
        return {"task_id": made["id"], "made": True, "before": None, **_changed(c, made["id"])}
    row = H.task(c, change["task_id"])
    if not row:
        H.refuse(c, STRATEGY_BOT, "not-found", "Task not found")
    before, follows = _snapshot(row), _followers(c, row)
    # Each write is the Strategy Bot's own, at the version just read, and raises the task's version.
    if do == "reorder":
        rank = H._queue_end(c, row["owner_id"], top=True)
        H.task_update(c, STRATEGY_BOT, row["id"], rank=rank, mover=True, quiet=True, seen_version=row["version"])
    elif do == "reassign":
        H.task_reassign(c, STRATEGY_BOT, row["id"], change["owner"], reason, row["version"], mover=True)
    else:
        # A bot does not close what a person asked for: refused, the close becomes the owner's proposal.
        note = change["note"] or f"Closed by the {strategy_bot.name(c)}: {reason}"
        open_under = {t: _snapshot(H.task(c, t)) for t in _open_under(c, row["id"])}
        H.task_close(c, STRATEGY_BOT, row["id"], note, row["version"], mover=True)
        # The close ended the open tasks under it too: each is kept (no words), so Undo reopens them all.
        under = [
            {"task_id": t, "before": was, **_changed(c, t)}
            for t, was in open_under.items()
            if (H.task(c, t) or {}).get("status") == "closed"
        ]
        return {"task_id": row["id"], "made": False, "before": before, **_changed(c, row["id"]), "under": under}
    after = {"before": follows, "after": _followers(c, H.task(c, row["id"]))} if follows is not None else None
    return {"task_id": row["id"], "made": False, "before": before, **_changed(c, row["id"]), "follows": after}


def decide(c, actor, reason, changes, goal_id=None, *, visible, give, sees):
    """A decision the Strategy Bot makes alone, at level 3: each change made on its own under one action id, the rest (a
    person's task, a bot offline, a refusal) one proposal in the owner's Needs you with the reason. `visible` is the
    Strategy Bot's auth.task_sql, `give` its check on handing a bot work and `sees` whether it may see a bot (`Caller`).
    Returns the action id; the reader reads it with `entry`."""
    H._writer(c, actor)
    if not is_strategy_bot(actor) or H.VIA.get():
        H.refuse(c, actor, "identity", f"Only the {strategy_bot.name(c)} decides alone, and only for itself")
    if not acts(c):
        H.refuse(
            c,
            actor,
            "state",
            f"The {strategy_bot.name(c)} acts on its own only at level 3, which the owner chooses "
            "in Settings; propose a recovery instead (POST /api/v2/goal-proposals)",
        )
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
            try:
                with triggers.savepoint(c):
                    out = _apply(c, change, goal_id, reason)
            except H.Refused as exc:
                left.append(
                    {
                        "change": change,
                        "text": describe(c, change, caller),
                        "rule": exc.rule,
                        "why": _refusal(c, H.task(c, change.get("task_id") or ""), exc),
                    }
                )
                continue
            text = describe(c, change, caller)
            if out["made"]:
                # Its title is the Strategy Bot's, but the task is the company's now: named as it is when read.
                text = f"New task for {_name(c, change['owner'])}: {TASK}"
            done.append({"change": change, "text": text, **out})
    finally:
        ACTION.reset(token)
    return _log(
        c,
        kind="decision",
        reason=reason,
        goal_id=goal_id,
        done=done,
        left=left,
        action_id=action_id,
        state="done" if done else "proposed",
    )


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
    said = [
        (_say(c, x["text"], _task_of(x), _company(c), PRIVATE), _say(c, x["why"], _task_of(x), _company(c), PRIVATE))
        for x in left
    ]
    lines = "\n".join(f"- {text}: {why}" for text, why in said)
    # One item per thing proposed: the same thing tried again finds the item already waiting (a duplicate).
    title = (
        f"Decide on the {strategy_bot.name(c)}'s proposal: {said[0][0]}"
        if len(left) == 1
        else f"Decide on {len(left)} changes the {strategy_bot.name(c)} proposed: {reason}"
    )[:200]
    body = (
        f"Please decide whether to make these changes yourself; the {strategy_bot.name(c)} may not make them.\n"
        + (f"Why it wanted them: {reason}\n" if reason else "")
        + lines
        + "\nNothing changed. Make the change yourself if you agree, or close this to leave it."
    )
    try:
        # Private, between the owner and the Strategy Bot that asks: it names company tasks by their titles, which a
        # person who reads the owner's work may not be allowed to open. The Strategy Bot stays its requester, so the 5 a
        # day and a repeated proposal are still counted (strategy_bot.asked_today).
        with triggers.savepoint(c):
            made = H.task_create(c, STRATEGY_BOT, title, body, owner, lint=False, private=True)
    except H.Refused as exc:
        return None, f"Not in Needs you: {exc.detail or exc.rule}; it stays here in the log"
    return made["id"], ""


def _log(c, *, kind, reason, state, done=(), left=(), goal_id=None, action_id=None):
    proposal, note = _propose(c, reason, list(left)) if left else (None, "")
    row = {
        "id": action_id or H.new_id(),
        "created_at": H.now(),
        "actor": STRATEGY_BOT,
        "kind": kind,
        "reason": reason or "",
        "goal_id": goal_id or None,
        "state": state,
        "proposal_task": proposal,
        "note": note,
        "changes": H._dump({"done": [_logged(c, x) for x in done], "left": [_logged(c, x) for x in left]}),
    }
    c.execute(
        "INSERT INTO strategy_actions (id, created_at, actor, kind, reason, goal_id, state, proposal_task, note, "
        "changes) VALUES (:id, :created_at, :actor, :kind, :reason, :goal_id, :state, :proposal_task, :note, "
        ":changes)",
        row,
    )
    H.event(
        c,
        STRATEGY_BOT,
        "strategy_bot." + ("decided" if kind == "decision" else "proposed"),
        row["id"],
        {"kind": kind, "done": len(done), "left": len(left)},
    )
    return row["id"]


def _stored(c, action_id):
    """A log row as kept: its lines with TASK, for Undo. Nobody reads this; they read `entry`."""
    row = H._one(c, "SELECT * FROM strategy_actions WHERE id=?", (action_id,))
    if not row:
        return None
    changes = H._json(row.pop("changes"), None) or {}
    row["done"], row["left"] = changes.get("done") or [], changes.get("left") or []
    for item in row["done"]:
        # A task's fields before and after, as stored: keyed by the tasks columns of the release that made the
        # decision, read under this one's (backend/migrations/renames_data.py), so Undo puts back what was there.
        for kept in [item, *(item.get("under") or [])]:
            for side in ("before", "after"):
                if isinstance(kept.get(side), dict):
                    kept[side] = renames.current_row("tasks", kept[side])
    row["undo"] = H._json(row.pop("undo", None), None) or None
    row["undoable"] = row["state"] == "done"
    return row


def reads(c, visible):
    """Whether a reader may open a task, by the SQL that decides what they read (auth.task_sql): a private task's
    parties and readers, and a company task unless its owner or requester is a bot hidden from them."""
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
        # The tasks a close ended under its task, and who followed it, are Undo's to put back, not a line to read.
        x = {k: v for k, v in x.items() if k not in ("under", "follows")}
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
        restored, kept = (
            [line(x) for x in row["undo"].get("restored") or []],
            [line(x) for x in row["undo"].get("kept") or []],
        )
        row["undo"] = {"restored": restored, "kept": kept, "text": _undo_text(restored, kept)}
    return row


def receipt(row):
    """What the Strategy Bot is told of its own decision: each line as it may read it, without the task fields Undo
    keeps (owner, status, queue place, readers). The log with those is the owner's (GET /api/v2/strategy-bot/log)."""

    def item(x):
        return {k: v for k, v in x.items() if k not in ("before", "after", "mark", "under", "follows")}

    return {**row, "done": [item(x) for x in row["done"]], "left": [item(x) for x in row["left"]]}


def log(c, visible, before=None, limit=LOG_PAGE):
    """The Strategy Bot log, newest first, as a reader sees it (`visible`, their auth.task_sql): {items, next_before}.
    Paged by `created_at`, then id: `next_before` is "<created>|<id>" of the page's last entry, so entries made in the
    same instant are never skipped between pages. A bare time is read as before that time."""
    limit = max(1, min(int(limit or LOG_PAGE), LOG_PAGE))
    at, _, after_id = str(before or "").partition("|")
    rows = c.execute(
        "SELECT id, created_at FROM strategy_actions WHERE (?='' OR created_at<? OR "
        "(?!='' AND created_at=? AND id<?)) "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (at, at, after_id, at, after_id, limit + 1),
    ).fetchall()
    items = [entry(c, r[0], visible) for r in rows[:limit]]
    last = rows[limit - 1] if len(rows) > limit else None
    return {"items": items, "next_before": f"{last[1]}|{last[0]}" if last else None}


# ----------------------------------------------------------------------------- undo
def undo(c, actor, action_id):
    """The owner undoes a decision. Each task it changed goes back to what it was before, unless anything happened to
    it since (`_touched`): then it stays as it is, and the result says so and by whom. A task it made is closed on the
    same terms. A task Tico won't put back (a refusal) is said, and the others still are.
    Each bot whose task changed is told. Done once. Returns the action id; the reader reads it with `entry`."""
    H._writer(c, actor)
    row = _stored(c, action_id)
    if not row:
        H.refuse(c, actor, "not-found", f"no Strategy Bot decision {action_id}")
    if row["state"] != "done":
        H.refuse(
            c,
            actor,
            "duplicate",
            "this decision was "
            + ("undone already" if row["state"] == "undone" else "a proposal; nothing was changed to undo"),
        )
    restored, kept = [], []
    token = ACTION.set(action_id)
    try:
        for item in _per_task(row["done"]):
            task = H.task(c, item["task_id"])
            note = lambda text, task_id=item["task_id"]: kept.append({"task_id": task_id, "text": text})
            if not task:
                note(f"{item['text']}: the task is gone")
                continue
            if not item.get("made") and _was_private(c, item, task):
                # The decision never acts on a private task (_bot_task); one logged as private before it is left
                # alone, so nothing is refused and its readers keep it.
                note(f"{TASK} is private, so Undo leaves it as it is")
                continue
            try:
                with triggers.savepoint(c):
                    did = (_unmake if item.get("made") else _restore)(c, actor, task, item, row["created_at"], note)
            except H.Refused as exc:
                note(f"{TASK} could not be put back: {_refusal(c, task, exc)}")
                continue
            if did:
                restored.append({"task_id": task["id"], "text": did})
                _tell(c, actor, H.task(c, task["id"]), item["after"]["owner_id"])
                # A close that ended the tasks under it: they open again with it, each on the same terms.
                for below in item.get("under") or []:
                    _restore_under(c, actor, below, row["created_at"], restored, kept)
    finally:
        ACTION.reset(token)
    result = {"restored": restored, "kept": kept}
    c.execute(
        "UPDATE strategy_actions SET state='undone', undone_at=?, undone_by=?, undo=? WHERE id=?",
        (H.now(), actor, H._dump(result), action_id),
    )
    H.event(c, actor, "strategy_bot.undone", action_id, {"restored": len(restored), "kept": len(kept)})
    return action_id


def _restore_under(c, actor, item, since, restored, kept):
    task = H.task(c, item["task_id"])
    if not task:
        return
    note = lambda text: kept.append({"task_id": task["id"], "text": text})
    try:
        with triggers.savepoint(c):
            did = _restore(c, actor, task, item, since, note)
    except H.Refused as exc:
        note(f"{TASK} could not be put back: {_refusal(c, task, exc)}")
        return
    if did:
        restored.append({"task_id": task["id"], "text": did})
        _tell(c, actor, H.task(c, task["id"]), item["after"]["owner_id"])


def _per_task(done):
    """One item per task, in the order the decision first changed it: its fields before the decision's first change
    to it and after its last, so two changes to one task are undone together."""
    tasks = {}
    for item in done:
        if item["task_id"] in tasks:
            first = tasks[item["task_id"]]
            first.update(after=item["after"], mark=item.get("mark"))
            if first.get("follows") and item.get("follows"):
                first["follows"] = {"before": first["follows"]["before"], "after": item["follows"]["after"]}
            elif item.get("follows"):
                first["follows"] = item["follows"]
            first["under"] = [*(first.get("under") or []), *(item.get("under") or [])]
        else:
            tasks[item["task_id"]] = dict(item)
    return list(tasks.values())


def _touched(c, task, item, since):
    """Who did something to the task after the decision, or None: anyone's change, ask, answer, snooze, comment or
    sub-task on it (its history after the decision's, Tico's own upkeep aside), or a newer version than the decision
    left. A log row from before the history mark was kept is read from the decision's time, and by its fields."""
    mark, after = item.get("mark"), item["after"]
    where, at = ("rowid>?", mark) if mark is not None else ("created_at>?", since)
    found = c.execute(
        f"SELECT actor_id FROM history_events WHERE subject_type='task' AND subject_id=? AND {where} "
        f"AND coalesce(action_id,'')!=? AND kind NOT IN ({','.join('?' * len(UPKEEP))}) ORDER BY rowid DESC LIMIT 1",
        (task["id"], at, ACTION.get() or "", *UPKEEP),
    ).fetchone()
    if found:
        return found[0]
    if after.get("version") is not None and task.get("version") != after["version"]:
        return "someone"
    return (
        "someone"
        if any(task.get(k) != after[k] for k in ("owner_id", "status", "rank", "note") if k in after)
        else None
    )


def _unmake(c, actor, task, item, since, note):
    """Close a task the decision made, unless anything happened to it since: then it stays as they left it."""
    if task["status"] == "closed":
        note(f"{TASK}, which it made, was closed since")
        return None
    who = _touched(c, task, item, since)
    if who:
        note(f"{TASK}, which it made, was changed since by {_name(c, who)}, so it stays open")
        return None
    H.task_close(
        c, H.KEEPER, task["id"], f"Undone by {_name(c, actor)}: the {strategy_bot.name(c)} made this task on its own"
    )
    return f"Closed {TASK}, which it made"


def _restore(c, actor, task, item, since, note):
    """Put back what the decision changed on a task nothing happened to since; returns what was put back. A task
    someone touched since is theirs now: Undo leaves all of it as it is."""
    before, after = item["before"], item["after"]
    back = [f for f in ("owner_id", "status", "rank", "note") if before.get(f) != after.get(f)]
    if not back:
        return None
    who = _touched(c, task, item, since)
    if who:
        note(f"{TASK} was changed since by {_name(c, who)}, so Undo leaves it as it is")
        return None
    if task["owner_id"] != before["owner_id"] and H.private_tasks_default(c, before["owner_id"]):
        if not H.task_private(c, task):
            # Given back, it would turn private (task_effects._follow_owner): more than the decision changed.
            note(
                f"{TASK} would turn private with {_name(c, before['owner_id'])}, which keeps its tasks private now, "
                f"so it stays with {_name(c, task['owner_id'])}"
            )
            return None
    left = _followers(c, task)
    # A task private since the decision (moved under a private task) is not the owner's to write: Tico puts it back.
    writer = H.KEEPER if H.task_private(c, task) else actor
    why = f"Undone by {_name(c, actor)}: the {strategy_bot.name(c)} did this on its own"
    if "status" in back:
        # A decision only closes a task, so its status goes back by reopening it. Reopened, it goes to its stage's
        # owner, which may not be the owner it had.
        H.task_move(c, writer, task["id"], H.TASK_REOPEN, why, mover=True)
    if H.task(c, task["id"])["owner_id"] != before["owner_id"]:
        H.task_reassign(c, writer, task["id"], before["owner_id"], why, mover=True)
    fields = {}
    # A new owner (or a reopen) puts it at the end of their queue: the place it had goes back too.
    if H.task(c, task["id"])["rank"] != before["rank"]:
        fields["rank"] = before["rank"]
    if "note" in back:
        fields["note"] = before.get("note") or ""
    if fields:
        H.task_update(c, writer, task["id"], mover=True, **fields)
    _refollow(c, task, item, left)
    return f"{TASK}: {', '.join(PLAIN[f] for f in back)} put back"


def _tell(c, actor, task, held_by):
    """Tell each bot the Undo changed things for: the task's owner now, and the bot the decision gave it to. News, not
    work: a reopen or a reassign wakes whoever has the turn itself."""
    said = (
        f"{_name(c, actor)} undid a decision the {strategy_bot.name(c)} made on its own; "
        "this task is back as it was before it"
    )
    for bot in dict.fromkeys((task["owner_id"], held_by)):
        if bot and H.is_bot(bot):
            H._wake(
                c,
                task,
                bot,
                said
                + ("." if bot == task["owner_id"] else f", with {_name(c, task['owner_id'])}. It is no longer yours."),
                {"quiet": True},
            )


def _undo_text(restored, kept):
    text = ("Undone: " + "; ".join(restored) + ".") if restored else "Nothing needed restoring."
    if kept:
        text += " Not restored: " + "; ".join(kept) + "."
    return text
