# pyright: strict
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

import sqlite3
from typing import Any

from . import hubdb as _hubdb
from .store import Problem

H: Any = _hubdb  # untyped module, through Any as in question_marks.py
Row = dict[str, Any]

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


def hold(c: sqlite3.Connection, task_id: str, bot: str, person: str, kind: str, status: str) -> None:
    """Record a task made from a setup draft, waiting on `person`'s Send or approved already."""
    c.execute(
        "INSERT INTO setup_drafts (task_id, bot_id, person_id, kind, status, created_at) VALUES (?,?,?,?,?,?)",
        (task_id, bot, person, kind, status, H.now()),
    )


def get(c: sqlite3.Connection, task_id: object) -> Row | None:
    """The setup draft a task was made from, or None."""
    if not task_id:
        return None
    row: Any = c.execute("SELECT * FROM setup_drafts WHERE task_id=?", (str(task_id),)).fetchone()
    return dict(row) if row else None


def status(c: sqlite3.Connection, task_id: object, loads: Any = None) -> str | None:
    """A task's setup draft status (WAITING or APPROVED), or None for a task not made from one; through the claim's reads
    (claim_loads.Loads.setup_draft) when it has them, so Health asks once for every queued job."""
    if not task_id:
        return None
    if loads is not None:
        found: Any = loads.setup_draft(c, str(task_id))
        return str(found) if found else None
    row: Any = c.execute("SELECT status FROM setup_drafts WHERE task_id=?", (str(task_id),)).fetchone()
    return str(row[0]) if row else None


def ask(
    c: sqlite3.Connection,
    bot: str,
    task_id: str,
    person: str,
    line: str,
    why: str,
    body: str = "",
    choices: tuple[str, ...] = CHOICES,
) -> Row:
    """The agent's ❓ to the person on the task, in Tico's words for it: its message (whose @words reach nobody) and
    the mark with the choices. The agent's status is the caller's to count again."""
    from . import person_asks, question_marks

    asks: Any = person_asks
    shaped: Any = asks.shaped(line, why)
    line, why = str(shaped[0]), str(shaped[1])
    text = "\n\n".join(part for part in (line, why, body) if part)
    said: Any = H.task_comment(c, bot, task_id, text, wake=False, checked=False, extra_refs={"no_mentions": True})
    return question_marks.mark(c, bot, str(said["id"]), person, checked=False, choices=choices, recount=False)


def answered(c: sqlite3.Connection, task_id: str, actor: str, message: Row) -> None:
    """A person wrote in a task's chat (hubdb._write_message, after their reply cleared their ❓ there): when the task is
    a setup draft waiting on them and they wrote it in their own hand, Send on its card releases it; anything else keeps
    the card there to tap (see the module's docstring)."""
    found = get(c, task_id)
    if not found or found["status"] != WAITING or actor != found["person_id"] or not H.by_own_hand(actor):
        return
    from . import question_marks

    bot = str(found["bot_id"])
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
            ask(c, bot, task_id, actor, AGAIN_LINE.format(kind=str(found["kind"])), ASK_WHY)
    H._recount(c, bot)


def _asked_here(c: sqlite3.Connection, mark_id: str, task_id: str, bot: str) -> bool:
    """Whether the ❓ the reply answered by its card is this draft's: its agent's, on its task, offering Send."""
    from . import question_marks

    row: Any = c.execute("SELECT task_id, bot_id, choices FROM question_marks WHERE id=?", (mark_id,)).fetchone()
    if not row or row[0] != task_id or row[1] != bot:
        return False
    return SEND in question_marks.choices_of({"choices": row[2]})


def plan_connected(c: sqlite3.Connection) -> bool:
    """Whether the company can run work on its own plan or key: a connected plan or API key (model_subscriptions, one
    not waiting to be reconnected), or a computer signed in to a runtime itself."""
    from . import models_preset as _models_preset
    from . import store as _store

    preset: Any = _models_preset
    store: Any = _store

    if (
        H._has_table(c, "model_subscriptions")
        and c.execute("SELECT 1 FROM model_subscriptions WHERE status<>'needs_reconnect' LIMIT 1").fetchone()
    ):
        return True
    for found in c.execute("SELECT readiness FROM runners_with_state WHERE revoked_at IS NULL"):
        ready: Any = store.readiness_document(found[0])
        runtimes: Any = ready.get("runtimes") or {}
        if any(preset.signed_in(ready, runtime) for runtime in runtimes):
            return True
    return False


def _wake(c: sqlite3.Connection, task_id: str, bot: str) -> None:
    from . import task_engine, task_wakes

    task: Any = task_engine.task(c, task_id)
    if not task or task.get("status") != "open" or not task.get("visit"):
        return
    task_wakes.add(c, task, bot, task["visit"], SENT, wake_key="%s:setup-send" % task_id)


def no_gateway(config: Row) -> Row:
    """A turn's config with no Gateway fallback: a setup draft's work runs on a connected plan or not at all."""
    out: Row = {key: value for key, value in config.items() if key not in ("gateway_fallback", "automatic_fallbacks")}
    fallback: Any = out.get("fallback")
    if isinstance(fallback, dict) and _is_tico(_as_row(fallback).get("model")):
        out["fallback"] = {"off": True}
    return out


def on_gateway(config: Row, check: Row | None = None) -> bool:
    """Whether a turn handed out on `config` (with `check`, its task's check model) would spend Gateway credit."""
    return (
        bool(config.get("waiting_for_sign_in"))
        or _is_tico(config.get("model"))
        or bool(check and _is_tico(check.get("model")))
    )


def _as_row(value: Any) -> Row:
    return value


def _is_tico(model: object) -> bool:
    from . import gateway_connection

    connection: Any = gateway_connection
    return bool(connection.is_tico(model))
