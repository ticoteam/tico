"""Proposals for the redesign's budgets (redesign CONTRACT §5): a bot's change to a task cap or a spend limit, asked as
an OK of the top of the org (backend/task_asks.py, subject `proposal:<id>`, content `{kind, change}`). An org change is
no proposal: the Strategy Owner suggests it in words and makes it when the person asks (the Assistant makes it as them,
backend/assistant.py `admits_on_turn`); the owner and group leads make it themselves on the Org tab.

  KINDS                      task_cap, spend_limit
  check(c, kind, payload)    refuses, when it is asked, a proposal the hub could not apply
  decider(c)                 who decides: the top of the org
  propose(c, who, kind, payload, task_id=None)
                             the bot's ask: {task_id, requirement_id, approval, status}
  ok_given(c, grant, ctx)    the person OK'd it from its preview (task_asks.ON_OK): the hub applies it in the same
                             transaction, as that person, audited with via `approval:<requirement id>`; the OK is used
                             and BotOps gets a notice

Payloads: task_cap `{sender, receiver, per_day}`; spend_limit `{bot}` or `{pool: "company"|"group:<id>"}` with
`per_30m_usd, daily_usd, weekly_usd, monthly_usd`.

The note to BotOps is a hub notice (from the engine, kind `notice`, refs `{approval}`), as every hub notice reaches a
bot: BotOps reads it in its inbox, the owner in BotOps's conversations. Nothing waits in a bot's queue: only work
outside the hub becomes a BotOps task.
"""

from pydantic import ValidationError

from . import hubdb as H
from . import models as M
from . import task_asks
from .store import Problem

KINDS = ("task_cap", "spend_limit")
SUBJECT = "proposal:"


class SpendLimit(M.UsageLimit):
    """A spend limit change: one bot's own, or a pool's (`company` or `group:<id>`)."""

    bot: M.ID | None = None
    pool: str | None = M.Field(default=None, max_length=200)


def _parse(kind, payload):
    if kind not in KINDS:
        raise Problem("validation", f"A proposal is {' or '.join(KINDS)}", 422)
    try:
        if kind == "task_cap":
            from .task_caps import TaskCap

            return TaskCap(**payload)
        found = SpendLimit(**payload)
        if bool(found.bot) == bool(found.pool):
            raise Problem("validation", "A spend_limit names a bot or a pool, not both", 422)
        if found.pool and found.pool != "company" and not found.pool.startswith("group:"):
            raise Problem("validation", "A pool is company or group:<id>", 422)
        return found
    except ValidationError as exc:
        first = exc.errors()[0]
        raise Problem(
            "validation", f"{kind} payload: {'.'.join(map(str, first['loc']))}: {first['msg']}", 422
        ) from None


def check(c, kind, payload):
    _parse(kind, payload)


def decider(c):
    """The person who decides a proposal: the top of the org (the stored owner's person)."""
    from .automations import top_person

    pid = top_person(c)
    return "human:" + pid if pid else None


def may_ok(c, person, grant):
    """Whether `person` may OK a proposal now (task_asks.MAY_OK): only the top of the org, whoever holds the ask."""
    if person != decider(c):
        return "Only the top of the org OKs this change"
    return None


LINES = {
    "task_cap": "Change how many tasks a teammate may send a manager a day?",
    "spend_limit": "Change a spend limit?",
}


def propose(c, who, kind, payload, task_id=None):
    """A bot's change as an OK the top of the org gives from its preview. Returns {task_id, requirement_id, approval,
    status}: `approval` is the requirement id, as a cap PUT has always answered."""
    check(c, kind, payload)
    person = decider(c)
    if not person:
        raise Problem("no_approver", "Nobody can approve this: the company has no owner on the team", 409)
    asked = task_asks.ask_ok(
        c,
        who.actor,
        person,
        LINES[kind],
        shows=[f"proposal:{kind}"],
        subject=SUBJECT + H.new_id(),
        content={"kind": kind, "change": payload},
        task_id=task_id,
        title=LINES[kind].rstrip("?"),
    )
    return {**asked, "approval": asked["requirement_id"], "status": "pending"}


def waiting(c, kind):
    """The proposals of one kind a person has yet to OK, oldest first: (grant, change)."""
    out = []
    for (rid,) in c.execute(
        "SELECT requirement_id FROM ok_grants WHERE subject LIKE ? AND used_at IS NULL ORDER BY created_at",
        (SUBJECT + "%",),
    ).fetchall():
        found = task_asks.grant(c, rid)
        content = (found or {}).get("content") or {}
        if found and content.get("kind") == kind and task_asks.standing(c, rid) == task_asks.WAITING:
            out.append((found, content.get("change") or {}))
    return out


def _name(c, value):
    from .task_caps import _name as name

    return name(c, value)


def _teammate(c, value):
    from .task_caps import _teammate as teammate

    return teammate(c, value)


def _group_name(c, gid):
    from . import groups as G

    return (G._roster(c)["org_groups"].get(gid) or {}).get("name") or gid or "no group"


def _apply_cap(c, auth, settings, who, rid, change):
    from . import task_caps

    task_caps.apply_approved(c, who.actor, rid, change)
    sender, receiver = _teammate(c, change.sender), _teammate(c, change.receiver)
    if change.per_day is None:
        return f"Task cap removed: {_name(c, sender)} to {_name(c, receiver)}."
    return f"Task cap set: {_name(c, sender)} sends {_name(c, receiver)} {change.per_day} a day."


def _apply_spend(c, auth, settings, who, rid, change):
    from . import usage

    # Only the fields the proposal names: one it leaves out keeps its stored value (backend/usage.py `amounts`).
    limits = M.UsageLimit(**change.model_dump(include=set(M.UsageLimit.model_fields), exclude_unset=True))
    if change.bot:
        bot = (H.resolve_bot(c, change.bot) or {}).get("id", change.bot)
        usage.save_limit(c, auth, who, bot, limits, via=f"approval:{rid}")
        return f"Spend limit changed for {_name(c, 'bot:' + bot)}."
    usage.save_pool(c, auth, who, change.pool, limits, via=f"approval:{rid}")
    return "Spend limit changed for " + (
        "the whole team." if change.pool == "company" else _group_name(c, change.pool.split(":", 1)[1]) + "."
    )


APPLY = {"task_cap": _apply_cap, "spend_limit": _apply_spend}


def ok_given(c, grant, ctx):
    """The person OK'd a proposal from its preview (task_asks.ON_OK), in that transaction: the change is applied as
    them (their rights checked as if they made it), the OK is used, and BotOps hears once."""
    who = getattr(ctx, "who", None)
    auth = getattr(ctx, "auth", None)
    if who is None or auth is None or who.actor != grant["ok_by_id"]:
        raise Problem("approval", "A proposal is OK'd from its preview in Tico", 409)
    content = grant["content"] or {}
    kind = content.get("kind")
    if kind not in APPLY:
        raise Problem("approval", "This OK names no change Tico knows", 409)
    change = content.get("change") or {}
    words = APPLY[kind](c, auth, getattr(ctx, "settings", None), who, grant["requirement_id"], _parse(kind, change))
    task_asks.use(c, grant["requirement_id"], grant["subject"], content, grant["asked_by_id"])
    task_asks.settle(c, grant["requirement_id"], words)
    note(c, grant["requirement_id"], f"{words} Approved by {_name(c, who.actor)}.")


def note(c, rid, words):
    """BotOps's notice of a change the hub applied: no task, nothing for it to do unless it sees work outside the hub."""
    if H.bot(c, "botops"):
        H.say(c, H.KEEPER, H.bot_actor("botops"), words, kind="notice", refs={"approval": rid})
