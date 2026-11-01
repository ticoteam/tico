"""The ways Tico stops a bot that is failing or running away, or one of its queued runs, and what every page says.

- `run_backoff`: a bot whose runs keep failing within a minute waits between runs, then is held after five in a row;
  Tico tries one run of a held bot on its own at growing intervals and releases it when one completes.
- `turn_budgets`: a bot that starts more runs in an hour or a day than its budget is held.
- `claim_holds`: one queued run whose claim raised while preparing it is held by itself; the bot's other runs go on.

A hold keeps the bot's queued work and starts none of it until a person releases the bot (one action, all three
holds) or, for a failed-runs hold, until Tico's own try completes. A claim hold keeps only its run. A person's chat
message does not bypass a failed-runs hold (it goes with the next try); it does bypass a budget hold, which only
stops work nobody asked for. This module is the one place the pages read: the status row (`overlay`), the warning
beside a message or a task sent to a held bot
(`warning`), the "Saved - waiting" label (`queued_reason`), Health's reason (`reason`), and the Needs-you item both
holds file for the bot's operator (`raise_item`, `close_item`).
"""

import logging

from . import run_backoff, turn_budgets
from .store import H

log = logging.getLogger(__name__)


def name_of(c, bot):
    return (H.bot(c, bot) or {}).get("display_name") or bot


def hold(c, bot, human=False):
    """The hold that stops the bot as {kind, words}, or None. `human`: the work is a person's message, which a budget
    hold lets through. The failed-runs hold is named first: it is the one that needs a fix before a release."""
    if run_backoff.get(c, bot).get("held_at"):
        return {"kind": "run_hold", "words": run_backoff.words(c, bot)}
    if not human and turn_budgets.get(c, bot).get("held_at"):
        return {"kind": "budget", "words": turn_budgets.words(c, bot)}
    return None


def overlay(c, row):
    """A bot's status row as the pages should read it while a hold stops it: paused, with the words. A running turn
    keeps its row. Only a hold stops it: a short back-off after a failed run keeps the state and focus, and adds its
    wait in words as `backoff` for the pages to show beside them."""
    if not row or row.get("state") in ("running", "quarantined"):
        return row
    found = hold(c, row["bot"])
    if found:
        return {**row, "state": "paused", "focus": found["words"], "stopped": found["kind"]}
    if run_backoff.refusal(c, row["bot"]) == "backoff":
        return {**row, "backoff": run_backoff.words(c, row["bot"])}
    return row


def warning(c, actor):
    """The words a sender gets when its message or task goes to a stopped bot, else None. A note beside the saved
    message, not a refusal: the work stays queued and runs once the bot is released."""
    if not str(actor or "").startswith("bot:"):
        return None
    found = hold(c, actor[4:])
    return f"{found['words']} What you sent is kept and starts when the bot runs again." if found else None


def queued_reason(c, bot, human=False):
    """The words after "Saved - " for work waiting on a stop or a back-off, else None."""
    found = hold(c, bot, human)
    if found:
        return "waiting: " + found["words"]
    if run_backoff.refusal(c, bot) == "backoff":
        return "waiting: " + run_backoff.words(c, bot)
    return None


def raise_item(c, settings, bot, title, body):
    """One Needs-you task for the bot's operator (else the owner), and its id; None when nobody can be asked or the
    task was refused. The hold stands either way, and Health names it."""
    from . import usage_limits
    person = usage_limits.recipients(c, bot, getattr(settings, "owner_email", "") or "")
    if not person:
        return None
    try:
        return H.task_create(c, H.KEEPER, title, body, H.human_actor(person["id"]), lint=False)["id"]
    except H.Refused:
        log.warning("Could not file the Needs-you task for the stopped bot %s", bot)
        return None


def close_item(c, task_id, note):
    """Close the Needs-you task a hold filed, once the bot is released. Quiet when it is already closed."""
    row = H.task(c, task_id) if task_id else None
    if not row or row["status"] not in H.ACTIVE_STATUSES:
        return
    try:
        H.task_close(c, H.KEEPER, task_id, note=note)
    except H.Refused:
        log.warning("Could not close the Needs-you task %s", task_id)


def release(c, actor, bot):
    """Release the bot from every stop it has: the failed-runs hold and its back-off, the budget hold, and the jobs its
    claim could not start (backend/claim_holds.py). Queued work starts again. Answers what was cleared:
    {"run_hold": bool, "budget": bool, "claim": the number of jobs released}."""
    from . import claim_holds
    return {"run_hold": run_backoff.release(c, actor, bot), "budget": turn_budgets.release(c, actor, bot),
            "claim": claim_holds.release(c, actor, bot)}
