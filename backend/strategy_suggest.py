"""What the Strategy Bot suggests at level 2 (docs/goals-and-kpis.md, "Your Strategy Bot"; spec: Head of Product,
Strategy Bot).

At level 2 ("Suggests actions") a goal that goes red or stalls gets a recovery proposal instead of only a question: a
`goal_proposals` row of kind `recovery` with the evidence, up to four options and the Strategy Bot's pick. Each option
is a short list of bot-task changes:

- `create`: a new task for a bot, on the goal;
- `reorder`: a bot's task to the top of its queue;
- `settle`: a clash between two bots' tasks: the first goes to the top of its queue, ahead of the other, and both owners
  hear the call once, on their task.

Nothing changes until the person who decides the goal approves one option on Goals > Needs you. Approving applies
exactly that option's changes, as the Strategy Bot, and never more than the person approving may do themselves: a
reorder of someone else's queue needs a person who may move any task. A change that can't be made (a bot paused, a task
finished since, a refusal) is never dropped: it comes back as a new proposal that says why in plain words, for the
person to approve again or reject.

The Strategy Bot only proposes here: a task for a person, spending, publishing and any change to a goal stay the
person's at every level, and a recovery names bot tasks only. Only the Strategy Bot proposes a recovery, only at level 2
or 3 with the switch on; lowering the level to 1 withdraws the ones still waiting.

`streak` counts the owner's recovery approvals in a row that kept the Strategy Bot's pick unchanged. After 10, the
Monday note may offer level 3 once (`offer_due`, `offer_made`), never more often than every 30 days; the Strategy Bot
never raises its own level (backend/goal_routes.py refuses it)."""

from datetime import timedelta

from . import strategy_bot
from . import hubdb as H

KIND = "recovery"
ACTIONS = ("create", "reorder", "settle")
MAX_OPTIONS = 4
MAX_ACTIONS = 6
STREAK_FOR_OFFER = 10
OFFER_EVERY_DAYS = 30
WITHDRAWN = "Withdrawn: the Strategy Bot was set to Passive, where it suggests no task changes"
STRATEGY_BOT = "bot:" + strategy_bot.SLUG


def suggests(c):
    """Whether this install's Strategy Bot suggests actions: level 2 or 3."""
    return strategy_bot.level(c) >= 2


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _bot(c, value):
    """`value` as a bot actor, or None when it is not a bot on this install."""
    actor = H.resolve_actor(c, str(value or "").strip())
    return actor if actor and H.is_bot(actor) else None


def _live_bot_task(c, task_id):
    """A task a recovery may move: live, owned by a bot, and one the Strategy Bot may read."""
    row = H.task(c, str(task_id or ""))
    if not row or row["status"] not in H.ACTIVE_STATUSES or not H.is_bot(row["owner_id"]):
        return None
    if not H.task_private_readable(c, STRATEGY_BOT, row):
        return None
    return row


def payload_error(c, goal_id, payload):
    """What is wrong with a recovery's payload, in a sentence, or None. Cleans `payload` in place: owners become
    actors, text is trimmed, and `pick` defaults to the first option."""
    options = payload.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        return f"a recovery has 1 to {MAX_OPTIONS} options, each a list of bot-task changes"
    payload["evidence"] = _text(payload.get("evidence"), 2000)
    if not payload["evidence"]:
        return "say what shows the goal is behind (evidence): the KPI readings, reports or Health it is based on"
    pick = payload.get("pick", 0)
    if not isinstance(pick, int) or isinstance(pick, bool) or not 0 <= pick < len(options):
        return "pick is the number of the option you recommend, counting from 0"
    payload["pick"] = pick
    for n, option in enumerate(options):
        if not isinstance(option, dict):
            return f"option {n} is an object with a label and actions"
        option["label"] = _text(option.get("label"), 200)
        actions = option.get("actions")
        if not option["label"]:
            return f"option {n} needs a label: what it does, in a few words"
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            return f"option {n} has 1 to {MAX_ACTIONS} actions"
        for m, action in enumerate(actions):
            error = _action_error(c, action if isinstance(action, dict) else {})
            if error:
                return f"option {n}, action {m}: {error}"
    return None


def _action_error(c, action):
    do = action.get("do")
    if do not in ACTIONS:
        return f"an action is {'|'.join(ACTIONS)}"
    if do == "create":
        owner = str(action.get("owner") or "").strip()
        if H.is_human(owner) or (H.resolve_actor(c, owner) or "").startswith("human:"):
            return "a task for a person stays the person's to make; the Strategy Bot proposes tasks for bots only"
        actor = _bot(c, owner)
        if not actor:
            return f"no bot {owner or '(none named)'} to give the task to"
        action["owner"] = actor
        action["title"] = _text(action.get("title"), 300)
        action["body"] = str(action.get("body") or "").strip()[:8000]
        if not action["title"]:
            return "a new task needs a title"
        return None
    if do == "reorder":
        if not _live_bot_task(c, action.get("task_id")):
            return "name a live bot task to move up (a task for a person, a finished one or a private one can't be)"
        return None
    first, then = _live_bot_task(c, action.get("first")), _live_bot_task(c, action.get("then"))
    if not first or not then or first["id"] == then["id"]:
        return "a clash names two different live bot tasks: the one that goes first and the one that waits"
    action["why"] = _text(action.get("why"), 1000)
    if not action["why"]:
        return "say why the first one goes first"
    return None


# ----------------------------------------------------------------------------- what the person reads
def _name(c, actor):
    found = H.bot(c, H.actor_id(actor)) if H.is_bot(actor) else None
    return (found or {}).get("display_name") or (found or {}).get("slug") or H.actor_id(actor or "")


def _title(c, task_id):
    row = H.task(c, task_id) if task_id else None
    return f'"{row["title"]}"' if row else "a task"


def describe(c, action):
    """One action in plain words."""
    if action["do"] == "create":
        return f'New task for {_name(c, action["owner"])}: "{action["title"]}"'
    if action["do"] == "reorder":
        row = H.task(c, action["task_id"]) or {}
        return f'Move {_title(c, action["task_id"])} to the top of {_name(c, row.get("owner_id", ""))}\'s queue'
    return f'Settle a clash: {_title(c, action["first"])} goes before {_title(c, action["then"])} ({action["why"]})'


def offline(c, action):
    """Why the bot this action reaches can't take it now, in plain words, or None."""
    owners = [action["owner"]] if action["do"] == "create" else \
        [(H.task(c, action.get(k)) or {}).get("owner_id") for k in ("task_id", "first", "then") if action.get(k)]
    for owner in owners:
        state = (H.bot(c, H.actor_id(owner or "")) or {}).get("state") if owner else None
        if owner and state != "active":
            return f"{_name(c, owner)} is {state or 'gone'} right now"
    return None


def options_view(c, proposal):
    """[{label, pick, actions: [{text, note}]}]: each option in plain words, `note` saying why a part can't happen now."""
    payload = proposal["payload"]
    return [{"label": option["label"], "pick": n == payload["pick"],
             "actions": [{"text": describe(c, action), "note": offline(c, action) or ""} for action in option["actions"]]}
            for n, option in enumerate(payload["options"])]


def ask_text(c, proposal, goal_title):
    """The Needs you task's title and body for a recovery: the evidence, each option, the pick, and anything that
    can't happen right now, in plain words."""
    payload = proposal["payload"]
    title = f"Approve a recovery for {goal_title}" if goal_title else "Approve a recovery"
    lines = []
    for n, option in enumerate(options_view(c, proposal)):
        mark = f" (the {strategy_bot.name(c)}'s pick)" if option["pick"] else ""
        lines.append(f"Option {n + 1}: {option['label']}{mark}")
        for action in option["actions"]:
            note = action["note"]
            lines.append(f"  - {action['text']}" + (f". Note: {note}; this part would come back to you." if note else ""))
    said = f"\nWhy now: {proposal['reason']}" if proposal.get("reason") else ""
    body = (f"{title}. The {strategy_bot.name(c)} suggests how the bots could get this goal back on track.\n"
            f"Evidence: {payload['evidence']}{said}\n" + "\n".join(lines) +
            "\nApprove one option or reject them all under Needs you on the Goals page. Approving applies exactly that "
            f"option, in the {strategy_bot.name(c)}'s name; nothing changes until you do. This closes by "
            "itself once it is decided.")
    return title, body


# ----------------------------------------------------------------------------- applying an approval
def _may_move(c, approver):
    """Whether the person approving may reorder any bot's queue themselves: the environment owner or a mover."""
    from . import people as P, views
    roster = views.roster(c) or {}
    owner = P.person(roster.get("default_user"), roster) or {}
    return bool(owner.get("id")) and approver == H.human_actor(owner["id"]) or H.can_move(c, approver)


def _blocked(c, action, approver):
    """Why this action can't be made now, in plain words, or None. Checked before trying, so a predictable refusal
    never counts against the Strategy Bot."""
    note = offline(c, action)
    if note:
        return note
    if action["do"] == "create":
        return None
    ids = [action["task_id"]] if action["do"] == "reorder" else [action["first"], action["then"]]
    for task_id in ids:
        row = H.task(c, task_id)
        if not row or row["status"] not in H.ACTIVE_STATUSES:
            return f"{_title(c, task_id)} is finished or gone since this was proposed"
    if not _may_move(c, approver):
        return "only the owner or someone who may move any task can reorder another bot's queue"
    return None


def _apply(c, action, goal_id, approver, proposal_id):
    who = _name(c, approver) if H.is_bot(approver) else ((H.human(c, H.actor_id(approver)) or {}).get("name")
                                                          or H.actor_id(approver))
    if action["do"] == "create":
        made = H.task_create(c, STRATEGY_BOT, action["title"],
                             (action["body"] + "\n\n" if action["body"] else "") +
                             f"Suggested by the {strategy_bot.name(c)} to recover a goal; approved by {who}.",
                             action["owner"], goal_id=goal_id or None, lane="company")
        return {"task_id": made["id"]}
    if action["do"] == "reorder":
        row = H.task(c, action["task_id"])
        H.task_update(c, STRATEGY_BOT, row["id"], rank=H._queue_end(c, row["owner_id"], top=True), mover=True, quiet=True)
        return {"task_id": row["id"]}
    first, then = H.task(c, action["first"]), H.task(c, action["then"])
    H.task_update(c, STRATEGY_BOT, first["id"], rank=H._queue_end(c, first["owner_id"], top=True), mover=True, quiet=True)
    said = (f"The {strategy_bot.name(c)} settled a clash, approved by {who}: \"{first['title']}\" goes before "
            f"\"{then['title']}\". {action['why']}")
    for row in (first, then):
        # The order is the approved change; a paused owner's full held queue withholds only its note.
        H.unless_queue_full(c, lambda: H.task_comment(c, STRATEGY_BOT, row["id"], said))
    return {"task_id": first["id"], "then": then["id"]}


def apply(c, approver, proposal, option):
    """Make the chosen option's changes as the Strategy Bot. Each change on its own: one that can't be made is skipped
    with its reason, the rest go ahead. Returns {done: [...], left: [{action, why}]}."""
    done, left = [], []
    for action in proposal["payload"]["options"][option]["actions"]:
        why = _blocked(c, action, approver)
        if why:
            left.append({"action": action, "why": why})
            continue
        c.execute("SAVEPOINT recovery_action")
        try:
            out = _apply(c, action, proposal["goal_id"], approver, proposal["id"])
        except H.Refused as exc:
            c.execute("ROLLBACK TO recovery_action")
            c.execute("RELEASE recovery_action")
            left.append({"action": action, "why": exc.detail or str(exc.rule)})
            continue
        c.execute("RELEASE recovery_action")
        done.append({"action": action, **out})
    return {"done": done, "left": left}


def follow_up(c, proposal, left):
    """What couldn't be made comes back as a new recovery with one option, saying why, so nothing is dropped."""
    from . import goals as G
    # Only what could still be made later comes back: a task finished since has nothing left to move, and says so in
    # the closed ask (`result_text`).
    again = [x for x in left if not _action_error(c, dict(x["action"]))]
    if not again:
        return None
    reasons = "; ".join(sorted({x["why"] for x in again}))
    payload = {"evidence": proposal["payload"]["evidence"], "pick": 0,
               "options": [{"label": "Try again: the part that couldn't be done", "actions": [x["action"] for x in again]}]}
    return G.propose(c, STRATEGY_BOT, KIND, proposal["goal_id"], None, payload,
                     f"Not done when you approved it: {reasons}. Approve to try again, or reject to leave it.")


def result_text(c, proposal):
    """What approving a recovery did, in plain words, for the ask it closes."""
    result = proposal.get("result") or {}
    if proposal["status"] != "confirmed" or "done" not in result:
        return None
    done = [describe(c, x["action"]) for x in result["done"]]
    left = [f"{describe(c, x['action'])}: {x['why']}" for x in result["left"]]
    text = f"Approved by {H.actor_id(proposal['decided_by'] or '')}; option {result['option'] + 1} applied by the " \
           f"{strategy_bot.name(c)}."
    if done:
        text += " Done: " + "; ".join(done) + "."
    if left:
        text += " Not done: " + "; ".join(left) + "."
        text += " What can still be done is back in your Needs you to approve again." if result.get("follow_up") else ""
    return text


def withdraw(c):
    """Lowering the level to Passive withdraws every recovery still waiting: Passive suggests no task changes. Tico
    decides it, not a person, so no streak counts it as a rejection: neither `streak` nor an older release's, which
    knows only the note it wrote (strategy_bot.OLD_WITHDRAWN). Who lowered the level is in its event."""
    c.execute("UPDATE goal_proposals SET status='rejected', decided_by=?, decided_at=?, decision_note=? "
              "WHERE status='pending' AND kind=?", (H.KEEPER, H.now(), WITHDRAWN, KIND))


# ----------------------------------------------------------------------------- the level 3 offer
def streak(c):
    """How many of its recovery suggestions in a row the owner approved with the Strategy Bot's pick unchanged, newest
    first. A rejection or another option ends it; a withdrawn one is no decision and is passed over."""
    n = 0
    for row in H._rows(c.execute("SELECT status, decided_by, decision_note, result FROM goal_proposals "
                                 "WHERE kind=? AND status!='pending' ORDER BY decided_at DESC", (KIND,))):
        if row["decision_note"] == WITHDRAWN or not H.is_human(row["decided_by"] or ""):
            continue
        result = H._json(row["result"], None) or {}
        if row["status"] != "confirmed" or not result.get("unchanged"):
            break
        n += 1
    return n


def _offered(c):
    return [x for x in strategy_bot._record(c).get("level3_offers") or [] if isinstance(x, str)]


def offer_view(c):
    """{streak, needed, due, last_offered_at}: `due` when the Monday note may offer level 3 now."""
    last = (_offered(c) or [""])[-1]
    stamp = H.parse_ts(last) if last else None
    recent = bool(stamp) and stamp > H.parse_ts(H.now()) - timedelta(days=OFFER_EVERY_DAYS)
    n = streak(c) if strategy_bot.level(c) == 2 else 0
    return {"streak": n, "needed": STREAK_FOR_OFFER, "due": n >= STREAK_FOR_OFFER and not recent,
            "last_offered_at": last or ""}


def offer_made(c, actor):
    """The Strategy Bot records that its Monday note offered level 3. Refused unless the offer is due, so it is made
    once and never more often than every 30 days."""
    if not offer_view(c)["due"]:
        raise H.Refused("state", f"Level 3 can be offered after {STREAK_FOR_OFFER} approvals in a row at level 2, "
                                 f"at most once every {OFFER_EVERY_DAYS} days; it is not due now")
    record = strategy_bot._record(c)
    record["level3_offers"] = (_offered(c) + [H.now()])[-12:]
    strategy_bot._save(c, record)
    H.event(c, actor, "strategy_bot.level3_offered", strategy_bot.SLUG, {})
    return offer_view(c)
