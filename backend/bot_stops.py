"""The ways Tico stops a bot that is failing or running away, or one of its queued runs, and what every page says.

- `run_backoff`: a bot whose runs keep failing within a minute waits between runs, then is held after five in a row;
  Tico tries one run of a held bot on its own at growing intervals and releases it when one completes.
- `turn_budgets`: a bot that starts more runs in an hour or a day than its budget is held.
- `claim_holds`: one queued run whose claim raised while preparing it is held by itself; the bot's other runs go on.

A hold keeps the bot's queued work and starts none of it until a person releases the bot (one action, all three
holds) or, for a failed-runs hold, until Tico's own try completes. A claim hold keeps only its run. What every page
says about a hold (the status row, a sender's warning, the "Saved - waiting" label, a task's Paused cause) is read in
one place, backend/holds.py. This module files and closes the Needs-you item each hold raises for the bot's operator
(`raise_item`, `close_item`), and releases the bot (`release`).
"""

import logging

from . import run_backoff, turn_budgets
from .store import H

log = logging.getLogger(__name__)


def name_of(c, bot):
    return (H.bot(c, bot) or {}).get("display_name") or bot


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
    if not row or row["status"] != "open":
        return
    if H.attempt(c, H.task_close, H.KEEPER, task_id, note) is None:
        log.warning("Could not close the Needs-you task %s", task_id)


def release(c, actor, bot):
    """Release the bot from every stop it has: the failed-runs hold and its back-off, the budget hold, and the jobs its
    claim could not start (backend/claim_holds.py). Queued work starts again. Answers what was cleared:
    {"run_hold": bool, "budget": bool, "claim": the number of jobs released}."""
    from . import claim_holds

    return {
        "run_hold": run_backoff.release(c, actor, bot),
        "budget": turn_budgets.release(c, actor, bot),
        "claim": claim_holds.release(c, actor, bot),
    }
