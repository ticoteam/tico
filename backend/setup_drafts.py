"""The tasks made from the drafts an agent wrote in setup (backend/company_import.py `import_drafts`), held by the server.

A draft is the agent's own words from before the company had an account, so the person decides, not the model:

- `waiting`: the agent asked the person Send, Edit or Later (its ❓ on the task). No run of the agent starts on the task,
  a person's own message included (execution.job_refusal, `refusal`), until the person who was asked says Send in its
  chat: their reply starting with "Send" (the card's button, or typed with more words, which ride along) makes it
  `approved` and wakes the agent once (`answered`). "Later" leaves it held and out of Needs you; "Edit" and anything
  else they write leave it held and ask again (Edit asks what to change). Nobody else's message decides it.
- `approved`: the agent carries it out once it can run, and only on a connected plan: a turn that would run on Gateway
  credit is refused at the claim (`refusal`), and the claim gives it no Gateway fallback (`no_gateway`).
"""

import re

from .store import H

WAITING, APPROVED = "waiting", "approved"
CHOICES = ("Send", "Edit", "Later")  # a waiting draft's ❓: the setup page's Flow buttons
WORD = re.compile(r"[a-z]+")
ASK_WHY = "I drafted it in setup. Send, edit, or leave it for later."
EDIT_LINE = "What should change? Write it here, then choose Send."
EDIT_WHY = "Nothing goes out until you choose Send."
AGAIN_LINE = "Send this {kind} now?"
SENT = (
    "The person chose Send on your draft from setup: carry it out now, as drafted and within the company's rules. "
    "Anything they wrote with it, or since, is in this task's chat."
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


def ask(c, bot, task_id, person, line, why, body="", choices=CHOICES):
    """The agent's ❓ to the person on the task, in Tico's words for it: its message (whose @words reach nobody) and
    the mark with the choices. The agent's status is the caller's to count again."""
    from . import person_asks, question_marks

    line, why = person_asks.shaped(line, why)
    text = "\n\n".join(part for part in (line, why, body) if part)
    said = H.task_comment(c, bot, task_id, text, wake=False, checked=False, extra_refs={"no_mentions": True})
    return question_marks.mark(c, bot, said["id"], person, checked=False, choices=choices, recount=False)


def answered(c, task_id, actor, message):
    """A person wrote in a task's chat (hubdb._write_message): when the task is a setup draft waiting on them, their
    words decide it (see the module's docstring)."""
    found = get(c, task_id)
    if not found or found["status"] != WAITING or actor != found["person_id"]:
        return
    first = WORD.match(str(message.get("body") or "").strip().lower())
    said = first.group(0) if first else ""
    bot = found["bot_id"]
    if said == "send":
        c.execute(
            "UPDATE setup_drafts SET status=?, decided_at=?, decided_by_id=? WHERE task_id=?",
            (APPROVED, H.now(), actor, task_id),
        )
        _wake(c, task_id, bot)
    elif said != "later":
        open_mark = c.execute(
            "SELECT 1 FROM question_marks WHERE task_id=? AND bot_id=? AND cleared_at IS NULL", (task_id, bot)
        ).fetchone()
        if not open_mark:
            if said == "edit":
                ask(c, bot, task_id, actor, EDIT_LINE, EDIT_WHY, choices=("Send", "Later"))
            else:
                ask(c, bot, task_id, actor, AGAIN_LINE.format(kind=found["kind"]), ASK_WHY)
    H._recount(c, bot)


def _wake(c, task_id, bot):
    from . import task_engine, task_wakes

    task = task_engine.task(c, task_id)
    if not task or task.get("status") != "open" or not task.get("visit"):
        return
    task_wakes.add(c, task, bot, task["visit"], SENT, wake_key="%s:setup-send" % task_id)


def status(c, task_id, loads=None):
    """A task's setup draft status (WAITING or APPROVED), or None for a task not made from one; through the claim's reads
    (claim_loads.Loads.setup_draft) when it has them, so Health asks once for every queued job."""
    if not task_id:
        return None
    if loads is not None:
        return loads.setup_draft(c, str(task_id))
    row = c.execute("SELECT status FROM setup_drafts WHERE task_id=?", (str(task_id),)).fetchone()
    return row[0] if row else None


def refusal(found, gateway):
    """Why the claim passes over a run on a task whose setup draft status is `found`, or None: a setup draft still
    waiting on its Send, or one that would run on Gateway credit (`gateway`: a callable saying whether this turn would)."""
    if not found:
        return None
    if found == WAITING:
        return DRAFT_WAIT
    return DRAFT_NEEDS_PLAN if gateway() else None


def no_gateway(config):
    """A claimed turn's config with no Gateway fallback: a setup draft's work runs on a connected plan or not at all."""
    from .gateway_connection import is_tico

    out = {key: value for key, value in config.items() if key != "gateway_fallback"}
    fallback = out.get("fallback")
    if isinstance(fallback, dict) and is_tico(fallback.get("model")):
        out.pop("fallback")
    return out
