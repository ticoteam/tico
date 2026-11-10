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
  words, under `COVERS`, an engine-style refusal that never counts toward quarantine. With the check down the ask goes.
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

    def audience(self, c: Any, who: Any, row: Row, to: str | None = None) -> Any:
        """A task write's audience: the task's owner and requester, and the person it hands the task or the ask to."""
        return self.rule_task_audience(c, row, who.actor, "", *([self.rule_target(c, who, to)] if to else []))

    def check(
        self, who: Any, tid: str, operation: str, said: Said, *, to: str | None = None, hands_over: bool = False
    ) -> Any:
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
                    row: Row = self.auth.task(c, who, self.auth.resolve_task(c, who, tid))
                except Problem as exc:
                    return org_rule_check.refused(exc)
            text = said_text(row, said, hands_over)
            if not text:
                return None

            def reached(c: Any) -> Any:
                return self.audience(c, who, row, to)

            return self.rule_check(
                who,
                operation,
                text,
                reached,
                obj=f"{row['id']}@{row['version']}",
                context={"task": row["title"]},
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
        hands_over: bool = False,
        ask: bool = False,
    ) -> Any:
        """The verdict inside the write: the gate (`org_rule_check.apply`), or a refusal. An ask (`ask`) a rule covers
        is refused back to the bot with the rule's words, and nothing is held. A person's ask is held or warned like
        their other writes."""
        if not verdict:
            return None
        if ask and H.is_bot(str(verdict.get("author") or "")):
            rules = covering(verdict)
            if rules:
                H.refuse(c, who.actor, COVERS, not_asked(rules[0]))
        row: Row | None = H.task(c, task_id)
        payload = said_text(row, said, hands_over, hide_task=bool(row and H.task_private(c, row)))
        return org_rule_check.apply(c, verdict, payload, replay)
