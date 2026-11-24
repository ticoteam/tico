"""The tasks made from the drafts an agent wrote in setup (backend/company_import.py `import_drafts`), held by the server.

A draft is the agent's own words from before the company had an account, so the person decides, not the model:

- `waiting`: the agent asked the person Send, Edit or Later (its ❓ on the task, with those choices). No run of the agent
  starts on the task, a person's own message included (execution.job_refusal: `draft_wait`). One thing releases it:
  the person who was asked taps Send on that card, in their own session (`answered`). That makes it `approved` and
  wakes the agent once. It is refused with "Connect your plan first" while the company has no connected plan or API
  key (`plan_connected`). Words never decide it: a comment, Edit or Later stays context for the agent's turn after
  the Send. Edit asks what to change, and anything else asks again, so the card is there to tap; Later leaves it out
  of Needs you until they write again.
- `approved`: the agent carries it out, only on a connected plan or API key. The handout resolves the turn's model as
  it would hand it out, with no Gateway fallback (`no_gateway`); a turn whose model is still the Gateway is not handed
  out and stays queued (execution.start_claimed: `draft_needs_plan`).
"""

from .store import H, Problem

WAITING, APPROVED = "waiting", "approved"
SEND = "Send"
CHOICES = (SEND, "Edit", "Later")  # a waiting draft's ❓: the setup page's Flow buttons
ASK_WHY = "I drafted it in setup. Send, edit, or leave it for later."
EDIT_LINE = "What should change? Write it here, then choose Send."
EDIT_WHY = "Nothing goes out until you choose Send."
AGAIN_LINE = "Send this {kind} now?"
NO_PLAN = "Connect your plan first"
SENT = (
    "The person chose Send on your draft from setup: carry it out now, as drafted and within the company's rules. "
    "Anything they wrote in this task's chat is context for it."
)
# The claim's reasons (backend/health.py HELD has their words).
DRAFT_WAIT = "draft_wait"
DRAFT_NEEDS_PLAN = "draft_needs_plan"


def hold(c, task_id, bot, person, kind, status):
    """Record a task made from a setup draft, waiting on `person`'s Send or approved already."""
    c.execute(
        "INSERT INTO setup_drafts (task_id, bot_id, person_id, kind, status, created_at) VALUES (?,?,?,?,?,?)",
        (task_id, bot, person, kind, status, H.now()),
    )


def get(c, task_id):
    """The setup draft a task was made from, or None."""
    if not task_id:
        return None
    row = c.execute("SELECT * FROM setup_drafts WHERE task_id=?", (str(task_id),)).fetchone()
    return dict(row) if row else None


def status(c, task_id, loads=None):
    """A task's setup draft status (WAITING or APPROVED), or None for a task not made from one; through the claim's reads
    (claim_loads.Loads.setup_draft) when it has them, so Health asks once for every queued job."""
    if not task_id:
        return None
    if loads is not None:
        return loads.setup_draft(c, str(task_id))
    row = c.execute("SELECT status FROM setup_drafts WHERE task_id=?", (str(task_id),)).fetchone()
    return row[0] if row else None


def ask(c, bot, task_id, person, line, why, body="", choices=CHOICES):
    """The agent's ❓ to the person on the task, in Tico's words for it: its message (whose @words reach nobody) and
    the mark with the choices. The agent's status is the caller's to count again."""
    from . import person_asks, question_marks

    line, why = person_asks.shaped(line, why)
    text = "\n\n".join(part for part in (line, why, body) if part)
    said = H.task_comment(c, bot, task_id, text, wake=False, checked=False, extra_refs={"no_mentions": True})
    return question_marks.mark(c, bot, said["id"], person, checked=False, choices=choices, recount=False)


def answered(c, task_id, actor, message):
    """A person wrote in a task's chat (hubdb._write_message, after their reply cleared their ❓ there): when the task is
    a setup draft waiting on them and they wrote it in their own hand, Send on its card releases it; anything else keeps
    the card there to tap (see the module's docstring)."""
    found = get(c, task_id)
    if not found or found["status"] != WAITING or actor != found["person_id"] or not H.by_own_hand(actor):
        return
    from . import question_marks

    bot = found["bot_id"]
    body = str(message.get("body") or "").strip()
    card = question_marks.answering_now()
    if body == SEND and card and _asked_here(c, card, task_id, bot):
        if not plan_connected(c):
            raise Problem("no_plan", NO_PLAN, 409)  # the whole reply is refused: the card stays to tap once connected
        c.execute(
            "UPDATE setup_drafts SET status=?, decided_at=?, decided_by_id=? WHERE task_id=?",
            (APPROVED, H.now(), actor, task_id),
        )
        _wake(c, task_id, bot)
    elif (
        body.lower() != "later"
        and not c.execute(
            "SELECT 1 FROM question_marks WHERE task_id=? AND bot_id=? AND cleared_at IS NULL", (task_id, bot)
        ).fetchone()
    ):
        if body.lower() == "edit":
            ask(c, bot, task_id, actor, EDIT_LINE, EDIT_WHY, choices=(SEND, "Later"))
        else:
            ask(c, bot, task_id, actor, AGAIN_LINE.format(kind=found["kind"]), ASK_WHY)
    H._recount(c, bot)


def _asked_here(c, mark_id, task_id, bot):
    """Whether the ❓ the reply answered by its card is this draft's: its agent's, on its task, offering Send."""
    from . import question_marks

    row = c.execute("SELECT task_id, bot_id, choices FROM question_marks WHERE id=?", (mark_id,)).fetchone()
    return bool(row) and row[0] == task_id and row[1] == bot and SEND in question_marks.choices_of(dict(row))


def plan_connected(c):
    """Whether the company can run work on its own plan or key: a connected plan or API key (model_subscriptions, one
    not waiting to be reconnected), or a computer signed in to a runtime itself."""
    from . import models_preset
    from .store import readiness_document

    if (
        H._has_table(c, "model_subscriptions")
        and c.execute("SELECT 1 FROM model_subscriptions WHERE status<>'needs_reconnect' LIMIT 1").fetchone()
    ):
        return True
    for (doc,) in c.execute("SELECT readiness FROM runners_with_state WHERE revoked_at IS NULL"):
        ready = readiness_document(doc)
        if any(models_preset.signed_in(ready, runtime) for runtime in ready.get("runtimes") or {}):
            return True
    return False


def _wake(c, task_id, bot):
    from . import task_engine, task_wakes

    task = task_engine.task(c, task_id)
    if not task or task.get("status") != "open" or not task.get("visit"):
        return
    task_wakes.add(c, task, bot, task["visit"], SENT, wake_key="%s:setup-send" % task_id)


def no_gateway(config):
    """A turn's config with no Gateway fallback: a setup draft's work runs on a connected plan or not at all."""
    from .gateway_connection import is_tico

    out = {key: value for key, value in config.items() if key not in ("gateway_fallback", "automatic_fallbacks")}
    fallback = out.get("fallback")
    if isinstance(fallback, dict) and is_tico(fallback.get("model")):
        out["fallback"] = {"off": True}
    return out


def on_gateway(config, check=None):
    """Whether a turn handed out on `config` (with `check`, its task's check model) would spend Gateway credit."""
    from .gateway_connection import is_tico

    return (
        bool(config.get("waiting_for_sign_in"))
        or is_tico(config.get("model"))
        or bool(check and is_tico(check.get("model")))
    )
