# pyright: strict
"""Why queued work does not start, in plain words, with the one fix a person can tap: the single list Health's stuck
bots, its Turns line and the chat's status rows read (execution.claim_refusal, job_refusal and the holds they ask).

Each code has `words`, a sentence ("{computer}" is the computer asked, "{home}" the one that holds its checkout or
saved session), `turns`, the Turns line's short form after "N waiting for" (start_places.describe), and `fix`, (label,
page, tab). A code missing here is a Tico bug: `say` and `turns` show the code itself and log one error per code
(Sentry reads the log), never a generic fallback. backend/tests/test_wait_reasons.py fails on a refusal code that a
claim can return and this list does not have."""

import logging
from typing import NamedTuple

Fix = tuple[str, str, str]
RUNS: Fix = ("Open Runs", "#/settings", "runs")
COMPUTERS: Fix = ("Open Computers", "#/settings", "devices")
BOT_SETTINGS: Fix = ("Open bot settings", "#/settings", "bots")
BILLING: Fix = ("Open Billing", "#/settings", "billing")
USAGE: Fix = ("Raise limit", "#/usage", "")
# The agent's model and Paid by choice: a plan at its usage limit is fixed by switching model (or by waiting).
SWITCH_MODEL: Fix = ("Switch model", "#/settings", "bots")


class Reason(NamedTuple):
    words: str
    fix: Fix
    turns: str


def _r(words: str, fix: Fix, turns: str) -> Reason:
    return Reason(words, fix, turns)


REASONS: dict[str, Reason] = {
    # A Tico Cloud company's access (backend/cloud_access.py): out of credit, or locked behind Pay to continue.
    "out_of_credit": _r("Out of credit: its Tico model turns start once credit is added", BILLING, "credit"),
    "cloud_locked": _r("Pay to continue: bots run again once the plan is paid", BILLING, "the plan to be paid"),
    "company_limit": _r("Your company is at its limit; Tico is fixing what's stuck.", RUNS, "the company's limit"),
    "task_wait": _r("Its task is snoozed: its work starts when the snooze ends", RUNS, "a snoozed task"),
    # A task made from a draft an agent wrote in setup (backend/setup_drafts.py).
    "draft_wait": _r("Its draft from setup waits for your Send", RUNS, "a setup draft's Send"),
    "draft_needs_plan": _r(
        "Its draft from setup runs on your connected plan, never on Gateway credit", RUNS, "a connected plan"
    ),
    # The task was given to this bot while another bot's turn on it was out (execution.turn_moved_on).
    "task_turn_out": _r(
        "Its task was handed to it mid-turn: it starts when the earlier owner's turn ends",
        RUNS,
        "an earlier owner's turn",
    ),
    "paused": _r("Paused: its owner or Agent Ops must resume it", BOT_SETTINGS, "bot paused"),
    "quarantined": _r(
        "Quarantined after repeated failures: its owner or Agent Ops must clear it", BOT_SETTINGS, "a quarantine"
    ),
    "company_paused": _r(
        "All bots on Tico Team are paused: its work starts when someone resumes", BOT_SETTINGS, "the company to resume"
    ),
    "no_slot": _r("No slot on {computer}: queued behind running turns", RUNS, "a free slot"),
    "busy": _r("Queued behind this bot's running turn", RUNS, "its running turn"),
    "draining": _r(
        "Paused, or held while Tico updates: its work starts when it is resumed", BOT_SETTINGS, "bot paused"
    ),
    "moving": _r(
        "Moving to its own cloud machine: its next turn starts there once its running turns end",
        COMPUTERS,
        "a move to its own cloud machine",
    ),
    "restarting": _r("{computer} is restarting: it starts new work once its running turns end", COMPUTERS, "a restart"),
    "maintaining": _r(
        "{computer} is tidying this bot's task worktrees: its work starts when that finishes",
        COMPUTERS,
        "worktree upkeep",
    ),
    "lane_held": _r(
        "{computer} still runs an earlier turn of this bot whose lease lapsed: the next starts when it ends",
        RUNS,
        "an earlier turn to end",
    ),
    "handed_back": _r(
        "{computer} handed its last try back before starting it: it tries again in a moment", RUNS, "a retry"
    ),
    # Its claim raised while preparing it (bot_stops.hold_job): Health names the error in its own finding.
    "claim_held": _r(
        "Tico couldn't start it: an error happened while preparing it. It tries again by itself",
        RUNS,
        "a retry after an error",
    ),
    "cant_start": _r(
        "Its task could not start after repeated tries: Agent Ops has a task to look at it",
        RUNS,
        "Agent Ops to look",
    ),
    "uncertain": _r("An interrupted run on the same task or chat waits for review in Runs", RUNS, "a run's review"),
    "task_quarantine": _r(
        "Held after repeated refusals; resumes after cooldown or quarantine clear", RUNS, "a task quarantine"
    ),
    # A person limited the bot's claims to one task (execution.scoped_sql): the rest waits for the scope's release.
    "scoped": _r("Limited to one task: its other work starts when the limit is lifted", BOT_SETTINGS, "a scope"),
    "cooling_off": _r(
        "Waiting out its model plan's usage limit, or a Tico AI Gateway outage, before it tries again",
        SWITCH_MODEL,
        "a usage limit",
    ),
    "payer_wait": _r(
        "Its Paid by plan can't take this turn now: switch model, or it starts when the plan frees up",
        SWITCH_MODEL,
        "its Paid by plan",
    ),
    "payer_update": _r("Update {computer} to use its Paid by plan", COMPUTERS, "a runner update"),
    "readiness_unknown": _r("{computer} has not said whether it can run this bot", COMPUTERS, "computer readiness"),
    "not_ready": _r(
        "{computer} can't run it yet: its model, repository or settings there are not ready",
        COMPUTERS,
        "computer readiness",
    ),
    "sign_in_rejected": _r("Its model sign-in was rejected on {computer}", COMPUTERS, "sign-in"),
    # The computer read a plan's sign-in as ready, and no plan signs it in now (execution.signed_in_by_lapsed_plan).
    "sign_in_missing": _r("Its model is not signed in on {computer}", COMPUTERS, "sign-in"),
    "gateway_disconnected": _r(
        "Reconnect the Tico AI Gateway to start relationship turns", COMPUTERS, "the Tico AI Gateway"
    ),
    "model_key_missing": _r(
        "Grant the relationship worker an API key for its model in Credentials", COMPUTERS, "a model API key"
    ),
    "login_upgrade": _r(
        "Update {computer} to use one login per AI tool; its bots wait without changing accounts",
        COMPUTERS,
        "a runner update",
    ),
    # A Tico model runs only where bots run as their own user (execution.claim_refusal): the bot waits, it never fails.
    "not_isolated": _r(
        "{computer} can't run Tico Gateway work (it needs Tico's Docker runner): it waits for another "
        "model or computer",
        BOT_SETTINGS,
        "a computer that runs Tico Gateway work",
    ),
    "isolation_unknown": _r(
        "{computer} hasn't said it can run Tico Gateway work: it waits until its runner is updated",
        COMPUTERS,
        "a runner update",
    ),
    "profile": _r(
        "Its subscription profile is not signed in or not reported on {computer}", COMPUTERS, "a subscription profile"
    ),
    "config": _r("Its settings cannot be read", BOT_SETTINGS, "readable settings"),
    "check_model": _r("Its check model does not run on {computer}, or is on hold", BOT_SETTINGS, "its check model"),
    "unreadable": _r("Its queued message is one it may not read", RUNS, "a message it may read"),
    # A turn that saved its session, or a task whose checkout, is on another computer: it starts only there.
    "continuation_home": _r(
        "Its saved session is on {home}: this turn can only continue there", COMPUTERS, "session saved on {home}"
    ),
    "worktree_home": _r(
        "Its checkout is on {home}: it starts there, not on {computer}", COMPUTERS, "checkout is on {home}"
    ),
    "worktree_conflict": _r(
        "Its checkouts are split across {home}: keep them on one computer", COMPUTERS, "checkouts split across {home}"
    ),
    # Tico stopped the bot (backend/bot_stops.py): a person releases it. A back-off is a short pause after a failed run.
    "run_hold": _r(
        "Tico stopped it after five failed runs in a row and tries one run on its own now and then: check its last runs, or release it now",
        BOT_SETTINGS,
        "stopped runs",
    ),
    "budget": _r(
        "Tico stopped it for too many runs in a short time: check its last runs, then release it or raise its budget",
        BOT_SETTINGS,
        "run budget",
    ),
    "backoff": _r("Pausing after a failed run: it tries again in a moment", RUNS, "a failed run"),
    # The owner's "Turns at once" for the bot is full across every computer: its queued work starts when one ends.
    "bot_limit": _r(
        "At its turn limit: it starts when one of its running turns ends", BOT_SETTINGS, "at its turn limit"
    ),
    "limit": _r("Over its spending limit", USAGE, "spending limit"),
    "on_ice": _r("Its card is On ice: it starts when someone moves it out", RUNS, "a card On ice"),
    "task_next_run": _r(
        "Waiting for its next run or a stored resume condition", RUNS, "the next run or a stored resume condition"
    ),
}
# Starts of `turns` that read as "N waiting: <words>", not "N waiting for <words>" (start_places.describe).
TURNS_COLON = ("checkout is on", "checkouts split", "session saved on", "at its turn limit")

log = logging.getLogger("tico.wait_reasons")
_logged: set[str] = set()


def unmapped(code: str) -> None:
    """A code with no words reached a screen: log it once per process, as an error Sentry reads."""
    if code not in _logged:
        _logged.add(code)
        log.error("unmapped wait reason %s: add its words and fix to backend/wait_reasons.py", code)


class _Blank(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return {"computer": "its computer", "home": "another computer"}.get(key, key)


def say(code: str, computer: str = "", home: str = "") -> tuple[str, Fix]:
    """(words, fix) for `code`; "Waiting (code: <code>)" and an error in the log for a code not in REASONS."""
    reason = REASONS.get(code)
    if reason is None:
        unmapped(code)
        return f"Waiting (code: {code})", RUNS
    return reason.words.format_map(_Blank(_given(computer=computer, home=home))), reason.fix


def turns(code: str, home: str = "") -> str:
    """The Turns line's short form for `code` ("checkout is on Test Mac"); "(code: <code>)" when unmapped."""
    reason = REASONS.get(code)
    if reason is None:
        unmapped(code)
        return f"(code: {code})"
    return reason.turns.format_map(_Blank(_given(home=home)))


def _given(**values: str) -> dict[str, str]:
    return {key: value for key, value in values.items() if value}
