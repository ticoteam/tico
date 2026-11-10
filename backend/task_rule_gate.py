"""The org rule check (backend/org_rule_check.py) on the v3 task routes (backend/task_moves_routes.py): the one place a
task write that says something goes through. A done note, a move's or a hand-off's why, an ask's line and choices, an
answer, a snooze's why: each is checked like any other write of its author, outside the store's write, and its verdict
applied inside it.

- Pass: the write goes as it was sent.
- Warn: the write goes, with the warning line on what the recipient reads (`warn_on`), and the answer carries it.
- Hold: nothing is written but the hold; the answer names the rule. Send anyway is the hold's own route
  (`org_rule_check.send`): only whoever may send a hold today, and it replays the same write (`TaskRules.replay`).
- An ask a rule covers (docs/pipelines-v3.md, "How a rule stops the next one"): a bot's ask is refused with the rule's
  words, under `COVERS`, an engine-style refusal that never counts toward quarantine. With the judge out the ask goes;
  a cause here (`LOCAL`: every call slot busy, the day's calls spent, too long to check) refuses it to try again.
- The check cannot run (an error here, not the judge being out): nothing is written (fail closed).

The hold's text is what the write says. A hand-off also says the task to its new owner, so the judge reads the task's
title and body too; the hold keeps them only when the task is not private, so a hold never shows a private task's text
to a reviewer who may not read it.
"""

import logging

from . import org_rule_check
from .store import H, Problem

LOG = logging.getLogger("tico.task_rule_gate")

# An ask a rule covers is refused under this rule. Starting with `task_`, it is a 409 that says what to do instead and
# never counts toward quarantine (hubdb._escalate).
COVERS = "task_rule"
FAILED = "The rule check could not run, so nothing was written. Try again."
# Answers that are buttons, not words: nothing to check.
BUTTONS = {"ok", "no", "again", "done"}

# What each write is, in the words the judge reads and the bot is told ("Your a task marked done was held...").
org_rule_check.OPERATIONS.setdefault("task_done", "a task marked done")
org_rule_check.OPERATIONS.setdefault("task_move", "a task move")
org_rule_check.OPERATIONS.setdefault("task_snooze", "a task set aside")
org_rule_check.OPERATIONS.setdefault("task_unsnooze", "a task brought back")
org_rule_check.OPERATIONS.setdefault("task_try_again", "a task handed back")
org_rule_check.OPERATIONS.setdefault("task_withdraw_ask", "an ask taken back")
org_rule_check.OPERATIONS.setdefault("task_ask_answer", "an answer on a task")
org_rule_check.OPERATIONS.setdefault("task_ok_ask", "an ask for an OK")
org_rule_check.OPERATIONS.setdefault("task_ok_use", "an OK used")


def said_text(row, said, hands_over=False, hide_task=False):
    """What a write says, as the check reads it: each (label, text) it carries, and the task itself when it hands the
    task to a new owner. `hide_task`: the task's own words are left out (a private task's, on its hold)."""
    parts = [f"{label}: {value}" for label, value in said if value]
    if hands_over and row:
        if hide_task:
            parts.append("Assigned: a private task")
        else:
            parts.append(f"Assigned: {row['title']}\n\n{row.get('body') or ''}".rstrip())
    return "\n\n".join(parts)


def covering(verdict):
    """The rules a verdict found broken, strictest first: what covers an ask. Nothing for no verdict, a pass, a check
    that could not run (the ask goes: better one extra ask than a lost one) or one already decided."""
    if not verdict or "refused" in verdict or "applied" in verdict or verdict.get("draft"):
        return []
    if verdict.get("check") == "structured":
        rules = list(verdict.get("held") or []) if verdict.get("outcome") == "held" and not verdict.get("error") else []
        judged = verdict.get("judged")
        return rules + (covering(judged) if judged else [])
    if verdict.get("outcome") != "broken":
        return []
    broken = [r for r in verdict.get("rules") or [] if r.get("id") in (verdict.get("broken") or [])]
    return sorted(broken, key=lambda r: -org_rule_check.STRICT.get(r.get("if_broken") or "block", 2))


# A check that could not run for a cause here, not the judge being out: nothing about the judge's health, so it never
# fails open (docs/pipelines-v3.md's "if it can't be reached, the ask goes" is the judge being out).
# - `busy`: every call slot is in use. Nothing is written; the writer tries again (503, retryable).
# - `day_cap`: the day's rule-check calls are spent; `too_large`: the text is past what one call can read. These take
#   the hold path a too-long output already takes: held when a Block or Escalate rule matches, delivered with a line
#   saying it could not be checked when every rule is Warn.
# A bot's ask (or OK ask) with any of them is refused, nothing written, never counted toward quarantine: an ask is
# never let through unchecked by a cap the bot itself can spend.
LOCAL = ("busy", "day_cap", "too_large")
NOT_ASKED_LOCAL = {
    "busy": "Not asked yet: the rule check is busy. Try again in a minute.",
    "day_cap": "Not asked: the rule check has no calls left today, so this ask cannot be checked. Do the work without "
    "asking, or ask tomorrow.",
    "too_large": "Not asked: this ask is too long to check against the company's rules. Shorten it and ask again.",
}
BUSY = "The rule check is busy, so nothing was written. Try again in a minute."


def local_cause(verdict):
    """Why a verdict could not be judged for a cause here (`LOCAL`), or None. A structured verdict reads its judged
    part."""
    if not verdict or "refused" in verdict or "applied" in verdict:
        return None
    if verdict.get("check") == "structured":
        return local_cause(verdict.get("judged"))
    if verdict.get("local"):
        return verdict["local"]
    if verdict.get("outcome") == "too_large":
        return "too_large"
    # Unchecked whole, or in part (some of its calls answered, `unchecked_rules` the rest).
    if (verdict.get("outcome") == "unchecked" or verdict.get("unchecked_rules")) and verdict.get("error") in LOCAL:
        return verdict["error"]
    return None


def held_locally(verdict):
    """A verdict whose day's calls were spent, on the hold path: every rule it meets broken as `too_large` is, the
    cause kept (`local`), so `org_rule_check.apply` holds it or warns that it went unchecked."""
    if not verdict or "refused" in verdict or "applied" in verdict:
        return verdict
    if verdict.get("check") == "structured":
        judged = verdict.get("judged")
        return {**verdict, "judged": held_locally(judged)} if judged else verdict
    if verdict.get("outcome") == "unchecked" and verdict.get("error") == "day_cap":
        return {
            **verdict,
            "outcome": "too_large",
            "local": "day_cap",
            "broken": [r["id"] for r in verdict.get("rules") or []],
        }
    return verdict


def not_asked(rule):
    return f'Not asked. A rule covers this: "{rule.get("text") or "a rule"}" Act on the rule instead of asking.'


def note_first(c, who, task_id, gate, version=None, mover=None):
    """A warned write with no words of its own the recipient reads (a hand-off alone, an ask's line): the warning
    line goes before the task's note, which stays whole. Returns the version to send the write itself with."""
    if not (gate and gate.get("warn")):
        return version
    row = H.task(c, task_id)
    H.task_update(
        c,
        who.actor,
        task_id,
        note=org_rule_check.warned_note(gate, row.get("note") or ""),
        mover=mover,
        seen_version=version,
    )
    return H.task(c, task_id)["version"] if version is not None else None


def held(gate):
    return bool(gate and gate.get("held"))


def warn_on(gate, text):
    """A field the recipient reads, with the warning line first when the write was warned."""
    return org_rule_check.warned_text(gate, text) if gate and gate.get("warn") else text


class TaskRules:
    """The check of a task write, built once per app (backend/app.py) from its rule check and audience."""

    def __init__(self, auth, rule_check, rule_task_audience, rule_target, store):
        self.auth, self.rule_check, self.store = auth, rule_check, store
        self.rule_task_audience, self.rule_target = rule_task_audience, rule_target

    def audience(self, c, who, row, to=None):
        """A task write's audience: the task's owner and requester, and the person it hands the task or the ask to."""
        return self.rule_task_audience(c, row, who.actor, "", *([self.rule_target(c, who, to)] if to else []))

    def check(self, who, tid, operation, said, *, to=None, hands_over=False):
        """The verdict for one task write by `who`, outside the write, or None (nothing to check). `said` is the
        write's (label, text) pairs; `to` who it reaches besides the task's people; `hands_over` that it gives the
        task to `to`. A task the caller may not read is `org_rule_check.refused`. Any other error is a refusal too:
        the write never goes unchecked because this failed."""
        try:
            if not org_rule_check.author_of(who, self.auth.settings.assistant_bot):
                return None
            if not any(value for _, value in said) and not hands_over:
                return None
            with self.store.read() as c:
                if not org_rule_check.in_force(c):
                    return None  # no rule in force: nothing read, nothing asked
                try:
                    row = self.auth.task(c, who, self.auth.resolve_task(c, who, tid))
                except Problem as exc:
                    return org_rule_check.refused(exc)
            text = said_text(row, said, hands_over)
            if not text:
                return None
            return held_locally(
                self.rule_check(
                    who,
                    operation,
                    text,
                    lambda c: self.audience(c, who, row, to),
                    obj=f"{row['id']}@{row['version']}",
                    context={"task": row["title"]},
                )
            )
        except Exception:  # fail closed: the write is refused, never sent unchecked
            LOG.exception("Task rule check failed")
            return org_rule_check.refused(Problem("rule_check", FAILED, 503, retryable=True))

    def apply(self, c, who, task_id, verdict, said, replay, *, hands_over=False, ask=False):
        """The verdict inside the write: the gate (`org_rule_check.apply`), or a refusal. An ask (`ask`) a rule covers
        is refused back to the bot with the rule's words, and nothing is held. A person's ask is held or warned like
        their other writes."""
        if not verdict:
            return None
        cause = local_cause(verdict)
        bot_ask = ask and H.is_bot(str(verdict.get("author") or ""))
        if bot_ask and cause:
            raise Problem("task_rule_unchecked", NOT_ASKED_LOCAL[cause], 503, retryable=True)
        if cause == "busy":
            raise Problem("rule_check_busy", BUSY, 503, retryable=True)
        if bot_ask:
            rules = covering(verdict)
            if rules:
                H.refuse(c, who.actor, COVERS, not_asked(rules[0]))
        row = H.task(c, task_id)
        payload = said_text(row, said, hands_over, hide_task=bool(row and H.task_private(c, row)))
        return org_rule_check.apply(c, verdict, payload, replay)
