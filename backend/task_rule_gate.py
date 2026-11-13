# pyright: strict
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
from collections.abc import Callable, Sequence
from typing import Any

from . import org_rule_check as org_rule_check_module
from .store import H as hubdb, Problem

H: Any = hubdb
org_rule_check: Any = org_rule_check_module
Row = dict[str, Any]
Said = Sequence[tuple[str, Any]]

LOG = logging.getLogger("tico.task_rule_gate")

# An ask a rule covers is refused under this rule. Starting with `task_`, it is a 409 that says what to do instead and
# never counts toward quarantine (hubdb._escalate).
COVERS = "task_rule"
# The write a `kind:ask` rule meets, when a bot makes it to a person: a question on a task.
ASKS = "task_ask"
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
org_rule_check.OPERATIONS.setdefault("strategy_merge", "an ask merged for a person")
org_rule_check.OPERATIONS.setdefault("strategy_decision", "a decision made alone")
org_rule_check.OPERATIONS.setdefault("batch_note", "a note to a bot on its item")
org_rule_check.OPERATIONS.setdefault("task_bulk", "a change to many tasks")


def said_text(row: Row | None, said: Said, hands_over: bool = False, hide_task: bool = False) -> str:
    """What a write says, as the check reads it: each (label, text) it carries, and the task itself when it hands the
    task to a new owner. `hide_task`: the task's own words are left out (a private task's, on its hold)."""
    parts = [f"{label}: {value}" for label, value in said if value]
    if hands_over and row:
        if hide_task:
            parts.append("Assigned: a private task")
        else:
            parts.append(f"Assigned: {row['title']}\n\n{row.get('body') or ''}".rstrip())
    return "\n\n".join(parts)


def covering(verdict: Row | None) -> list[Row]:
    """The rules a verdict found broken, strictest first: what covers an ask. Nothing for no verdict, a pass, a check
    that could not run (the ask goes: better one extra ask than a lost one) or one already decided."""
    if not verdict or "refused" in verdict or "applied" in verdict or verdict.get("draft"):
        return []
    if verdict.get("check") == "structured":
        rules: list[Row] = (
            list(verdict.get("held") or []) if verdict.get("outcome") == "held" and not verdict.get("error") else []
        )
        judged: Row | None = verdict.get("judged")
        return rules + (covering(judged) if judged else [])
    if verdict.get("outcome") != "broken":
        return []
    found: list[Row] = verdict.get("rules") or []
    broken = [r for r in found if r.get("id") in (verdict.get("broken") or [])]
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


def local_cause(verdict: Row | None) -> str | None:
    """Why a verdict could not be judged for a cause here (`LOCAL`), or None. A structured verdict reads its judged
    part."""
    if not verdict or "refused" in verdict or "applied" in verdict:
        return None
    if verdict.get("check") == "structured":
        return local_cause(verdict.get("judged"))
    if verdict.get("local"):
        return str(verdict["local"])
    if verdict.get("outcome") == "too_large":
        return "too_large"
    # Unchecked whole, or in part (some of its calls answered, `unchecked_rules` the rest).
    if (verdict.get("outcome") == "unchecked" or verdict.get("unchecked_rules")) and verdict.get("error") in LOCAL:
        return str(verdict["error"])
    return None


def held_locally(verdict: Row | None) -> Row | None:
    """A verdict whose day's calls were spent, on the hold path: every rule it meets broken as `too_large` is, the
    cause kept (`local`), so `org_rule_check.apply` holds it or warns that it went unchecked."""
    if not verdict or "refused" in verdict or "applied" in verdict:
        return verdict
    if verdict.get("check") == "structured":
        judged: Row | None = verdict.get("judged")
        return {**verdict, "judged": held_locally(judged)} if judged else verdict
    if verdict.get("outcome") == "unchecked" and verdict.get("error") == "day_cap":
        rules: list[Row] = verdict.get("rules") or []
        return {
            **verdict,
            "outcome": "too_large",
            "local": "day_cap",
            "broken": [r["id"] for r in rules],
        }
    return verdict


CHANGED = "The task changed while this was checked, so nothing was written. Send it again."


def stale(verdict: Row, obj_now: Any, audience_now: Any) -> bool:
    """Whether the task or the audience moved since `verdict` was checked."""
    if verdict.get("obj") and obj_now != verdict["obj"]:
        return True
    author = verdict.get("author")
    now: list[str] = list(audience_now or ())
    return sorted({r for r in now if r and r != author}) != list(verdict.get("recipients") or [])


def not_asked(rule: Row) -> str:
    return f'Not asked. A rule covers this: "{rule.get("text") or "a rule"}" Act on the rule instead of asking.'


def note_first(
    c: Any, who: Any, task_id: str, gate: Row | None, version: int | str | None = None, mover: bool | None = None
) -> Any:
    """A warned write with no words of its own the recipient reads (a hand-off alone, an ask's line): the warning
    line goes before the task's note, which stays whole. Returns the version to send the write itself with."""
    if not (gate and gate.get("warn")):
        return version
    row: Row = H.task(c, task_id) or {}
    H.task_update(
        c,
        who.actor,
        task_id,
        note=org_rule_check.warned_note(gate, row.get("note") or ""),
        mover=mover,
        seen_version=version,
    )
    after: Row = H.task(c, task_id) or {}
    return after.get("version") if version is not None else None


def held(gate: Row | None) -> bool:
    return bool(gate and gate.get("held"))


def warn_on(gate: Row | None, text: str) -> str:
    """A field the recipient reads, with the warning line first when the write was warned."""
    return org_rule_check.warned_text(gate, text) if gate and gate.get("warn") else text


class Place:
    """A task write's place, read inside the check (`place_for`). `asks_person` says, before it is read, that the write
    is a bot's question to a person (org_rule_check.asking): no other write pays for a `kind:ask` rule."""

    def __init__(self, row: Row | None, ask: bool) -> None:
        self.row, self.asks_person = row, ask

    def __call__(self, c: Any, author: Any, recipients: Any) -> Any:
        found: Row = org_rule_check.place_for(c, author, recipients, (self.row or {}).get("conversation_id") or "")
        return {**found, "ask": True} if self.asks_person else found


class TaskRules:
    """The check of a task write, built once per app (backend/app.py) from its rule check and audience."""

    def __init__(
        self,
        auth: Any,
        rule_check: Callable[..., Any],
        rule_task_audience: Callable[..., Any],
        rule_target: Callable[..., Any],
        store: Any,
    ) -> None:
        self.auth, self.rule_check, self.store = auth, rule_check, store
        self.rule_task_audience, self.rule_target = rule_task_audience, rule_target

    def audience(
        self, c: Any, who: Any, row: Row | None, to: str | None = None, also: Callable[[Any], Any] | None = None
    ) -> Any:
        """A task write's audience: the task's owner and requester, the person it hands the task or the ask to (`to`,
        as the caller names them), and anyone else it reaches (`also(c)`: actors, such as whoever asked what an
        answer answers)."""
        extra = [self.rule_target(c, who, to)] if to else []
        return self.rule_task_audience(c, row, who.actor, "", *extra, *(also(c) if also else ()))

    @staticmethod
    def place(row: Row | None, ask: bool = False) -> "Place":
        """A task write's place, as `where:` reads it: its task's conversation, the thread its comments are in. `ask`:
        it is a bot's question to a person, which a `kind:ask` rule meets (the built-in "Asks to a person")."""
        return Place(row, ask)

    def check(
        self,
        who: Any,
        tid: str,
        operation: str,
        said: Said,
        *,
        to: str | None = None,
        also: Callable[[Any], Any] | None = None,
        hands_over: bool = False,
    ) -> Any:
        """The verdict for one task write by `who`, outside the write, or None (nothing to check). `said` is the
        write's (label, text) pairs; `to` who it reaches besides the task's people, `also(c)` anyone else; `hands_over`
        that it gives the task to `to`. The write's place is its task's thread (`place`). A task the caller may not
        read is `org_rule_check.refused`. Any other error is a refusal too: the write never goes unchecked because
        this failed."""
        try:
            if not org_rule_check.author_of(who, self.auth.settings.assistant_bot):
                return None
            if not any(value for _, value in said) and not hands_over:
                return None
            with self.store.read() as c:
                ask = (
                    operation == ASKS
                    and H.is_bot(str(who.actor))
                    and bool(to)
                    and org_rule_check.in_force(c, True)
                    and H.is_human(str(self.rule_target(c, who, to) or ""))
                )
                if not org_rule_check.in_force(c, ask):
                    return None  # no rule in force: nothing read, nothing asked
                try:
                    row: Row = self.auth.task(c, who, self.auth.resolve_task(c, who, tid))
                except Problem as exc:
                    return org_rule_check.refused(exc)
            text = said_text(row, said, hands_over)
            if not text:
                return None

            def reached(c: Any) -> Any:
                return self.audience(c, who, row, to, also)

            return held_locally(
                self.rule_check(
                    who,
                    operation,
                    text,
                    reached,
                    obj=f"{row['id']}@{row['version']}",
                    context={"task": row["title"]},
                    place=self.place(row, ask),
                    track_route=True,  # the audience is kept even when no rule matches: apply compares it
                )
            )
        except Exception:  # fail closed: the write is refused, never sent unchecked
            LOG.exception("Task rule check failed")
            return org_rule_check.refused(Problem("rule_check", FAILED, 503, retryable=True))

    def apply(
        self,
        c: Any,
        who: Any,
        task_id: str,
        verdict: Row | None,
        said: Said,
        replay: Any,
        *,
        to: str | None = None,
        also: Callable[[Any], Any] | None = None,
        hands_over: bool = False,
        ask: bool = False,
    ) -> Any:
        """The verdict inside the write: the gate (`org_rule_check.apply`), or a refusal. The task and the audience
        are read again here and must be the ones checked (`obj_now`, `audience_now`): a task edited, handed over or
        given new people since its check is a 409 (`task_changed`, nothing written; sent again, it is checked again),
        `version` sent or not. A verdict that holds still holds. An ask (`ask`) a rule covers is refused back to the
        bot with the rule's words, and nothing is held. A person's ask is held or warned like their other writes."""
        if not verdict:
            return None
        if "refused" in verdict:
            raise verdict["refused"]
        row: Row | None = H.task(c, task_id)
        if "applied" not in verdict:
            obj_now = org_rule_check.current_obj(c, verdict.get("obj")) if verdict.get("obj") else None
            audience_now = self.audience(c, who, row, to, also)
            if not org_rule_check.holds(verdict) and stale(verdict, obj_now, audience_now):
                raise Problem("task_changed", CHANGED, 409, retryable=True)
        else:
            obj_now = audience_now = None
        self._refused(c, who, verdict, ask)
        # The reviewer reads the write without a private task's words; the hold keeps what was judged for Send
        # anyway's recheck alone (`judged`).
        # Nor its people: everyone the write reaches is reached through the private task.
        private = bool(row and H.task_private(c, row))
        payload = said_text(row, said, hands_over, hide_task=private)
        judged = said_text(row, said, hands_over)
        private_to = {row["id"]: list(verdict.get("recipients") or [])} if private and row else None
        gate = org_rule_check.apply(
            c,
            verdict,
            payload,
            replay,
            obj_now=obj_now,
            audience_now=audience_now,
            judged=judged,
            private_to=private_to,
        )
        if gate and gate.get("moved"):
            raise Problem("task_changed", CHANGED, 409, retryable=True)
        return gate

    @staticmethod
    def _refused(c: Any, who: Any, verdict: Row, ask: bool) -> None:
        """What stops a write before the gate: a cause here (`LOCAL`) refuses a bot's ask and makes any write wait out
        a busy check; a bot's ask a rule covers is refused with the rule's words."""
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

    # A task write that is not one route's move on one task (the Strategy Owner's merged ask and its decisions,
    # backend/goal_routes.py): the same contract, checked outside the write and applied inside it, with the audience
    # the caller names (`reach(c)`, actors) read again inside. Its hold has no Send anyway replayer: the writer edits
    # and sends again.
    def check_words(
        self, who: Any, operation: str, said: Said, reach: Callable[[Any], Any], context: Row | None = None
    ) -> Any:
        """The verdict for a write of `said` ((label, text) pairs) reaching `reach(c)`, outside the write, or None.
        Fails closed like `check`."""
        try:
            if not org_rule_check.author_of(who, self.auth.settings.assistant_bot):
                return None
            text = said_text(None, said)
            if not text:
                return None
            with self.store.read() as c:
                if not org_rule_check.in_force(c):
                    return None
            return held_locally(self.rule_check(who, operation, text, reach, context=context, track_route=True))
        except Exception:
            LOG.exception("Task rule check failed")
            return org_rule_check.refused(Problem("rule_check", FAILED, 503, retryable=True))

    def apply_words(
        self,
        c: Any,
        who: Any,
        verdict: Row | None,
        said: Said,
        reach: Callable[[Any], Any],
        ask: bool = False,
        private_to: Callable[[Any], Any] | None = None,
    ) -> Any:
        """`check_words`'s verdict inside the write: a refusal, a 409 when the audience moved since the check, or the
        gate (`org_rule_check.apply`). `private_to(c)`: {private task id: the people the write reaches only through
        it}, whom a hold lists only to a reader of that task."""
        if not verdict:
            return None
        if "refused" in verdict:
            raise verdict["refused"]
        audience_now = reach(c) if "applied" not in verdict else None
        if audience_now is not None and not org_rule_check.holds(verdict) and stale(verdict, None, audience_now):
            raise Problem("task_changed", CHANGED, 409, retryable=True)
        self._refused(c, who, verdict, ask)
        gate = org_rule_check.apply(
            c,
            verdict,
            said_text(None, said),
            None,
            audience_now=audience_now,
            private_to=private_to(c) if private_to else None,
        )
        if gate and gate.get("moved"):
            raise Problem("task_changed", CHANGED, 409, retryable=True)
        return gate
