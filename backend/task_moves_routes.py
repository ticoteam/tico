# pyright: strict
"""The routes that work a task (docs/dev/pipelines-v3-cutover.md, "HTTP"): done, move, owner, snooze, unsnooze, try
again, asks and their answers, OKs bound to what a person was shown (task_asks), and taking an answer back.

Each route calls one task call (`H.task_done`, `H.task_move`, ...) inside the store's write and answers the task as
JSON. None of them writes a task's status, stage or owner: the engine does. A refusal is the engine's
(`task_not_met`, `task_changed`, ...), with what is missing, the commands allowed next and the version.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request

from . import ask_ingest as _ask_ingest
from . import models as M
from . import org_rule_check as _org_rule_check
from . import reach as _reach
from . import setup_live as _setup_live
from . import strategy_merge as _strategy_merge
from . import task_asks as _task_asks
from . import task_caps as _task_caps
from . import task_rule_gate as _task_rule_gate
from . import stage_rules as _stage_rules
from . import task_engine as _task_engine
from .store import H as _H
from .store import Problem

# Untyped modules, reached through Any in this strict module.
H: Any = _H
ask_ingest: Any = _ask_ingest
org_rule_check: Any = _org_rule_check
reach: Any = _reach
setup_live: Any = _setup_live
strategy_merge: Any = _strategy_merge
task_asks: Any = _task_asks
task_caps: Any = _task_caps
task_rule_gate: Any = _task_rule_gate
stage_rules: Any = _stage_rules
task_engine: Any = _task_engine

# A snooze's bare date is the start of that day here, as the bot tools say ("the start of that day, Pacific").
ZONE = ZoneInfo("America/Los_Angeles")


def parse_until(value: object) -> str | None:
    """`value` (an ISO-8601 date or date-time) as a stored UTC timestamp. A bare date, or a time with no offset, reads
    in the team's zone. None for none; Problem for one that is not a date."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            at = datetime.combine(datetime.fromisoformat(text).date(), time(0), ZONE)
        else:
            at = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=ZONE)
    except ValueError:
        raise Problem("date", "until is an ISO-8601 date or date-time", 422) from None
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    if stamp <= H.now():
        raise Problem("date", "until is a date to come", 422)
    return stamp


def register(
    app: FastAPI,
    auth: Any,
    delegated_identity: Callable[..., Any],
    handoff_preflight: Callable[..., Any],
    mover: Callable[..., Any],
    mutate: Callable[..., Any],
    task_rules: Any,
    store: Any,
    task_view: Callable[..., Any],
) -> None:
    """`task_rules` is the org rule check of a task write (backend/task_rule_gate.py): every route here that carries
    words goes through it (`checked`), and Send anyway replays the same write (`replays`)."""

    def acting(c: Any, caller: Any, body: Any) -> Any:
        """Who the change is made as: the caller, or the person whose request BotOps carries (`on_behalf_of`)."""
        ref = getattr(body, "on_behalf_of", None)
        return delegated_identity(c, caller, ref) if ref else caller

    def answer(c: Any, who: Any, task_id: str, **more: Any) -> dict[str, Any]:
        return {"task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who), **more}

    def work(request: Request, tid: str, body: Any, call: Callable[[Any, Any, str], Any]) -> Any:
        """One task call on the task `tid` names, as the caller (or the person they act for), in the store's write.
        `call(c, who, task_id)` makes it and returns what the answer carries besides the task (or None); the answer is
        the task as it is after."""
        caller: Any = request.state.identity

        def write(c: Any) -> dict[str, Any]:
            who = acting(c, caller, body)
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            more: Any = call(c, who, task_id) or {}
            return answer(c, who, task_id, **more)

        return mutate(request, body, write)

    def requirement_on(c: Any, task_id: str, requirement_id: str) -> str:
        """A requirement of this task's current visit or of its stage: what an ask route names. 404 otherwise."""
        found = c.execute(
            "SELECT 1 FROM requirements r JOIN visits v ON v.task_id=? AND v.left_at IS NULL "
            "WHERE r.id=? AND (r.visit_id=v.id OR r.stage_id=v.stage_id)",
            (task_id, requirement_id),
        ).fetchone()
        if not found:
            raise Problem("not_found", "No such ask on this task", 404)
        return requirement_id

    # ------------------------------------------------------------------ the rule check of what a write says
    # Each write that carries words, by its rule check operation: its body's model, what it says ((label, text)
    # pairs), who it reaches besides the task's people, whether it hands the task over or asks, and the write itself:
    # `call(c, who, task_id, body, gate, params, ingest)`, where `gate` is the verdict applied (a warning line goes
    # on what the recipient reads: `carry`).
    writes: dict[str, dict[str, Any]] = {}

    def says(
        operation: str,
        model: Any,
        said: Callable[[Any], Any],
        call: Callable[..., Any],
        *,
        to: Callable[[Any], Any] = lambda body: None,
        also: Callable[[Any, Any], Any] | None = None,
        hands_over: bool = False,
        ask: bool = False,
        late: bool = False,
    ) -> None:
        """`also(c, params)`: actors the write reaches besides the task's people and `to` (an answer's asker). `late`:
        under rules that only warn, the write goes first and its check right after; its call says where its words went
        (org_rule_check `late_on`)."""
        writes[operation] = {
            "model": model,
            "said": said,
            "call": call,
            "to": to,
            "also": also,
            "hands_over": hands_over,
            "ask": ask,
            "late": late,
        }

    def carry(c: Any, who: Any, task_id: str, gate: Any, text: Any, version: Any) -> tuple[Any, Any]:
        """A warned write's words, the line first: on its own text when it has some, else before the task's note.
        Returns (text, version to write with)."""
        if text:
            return task_rule_gate.warn_on(gate, text), version
        return text, task_rule_gate.note_first(c, who, task_id, gate, version, mover(c, who) or None)

    def gated(
        c: Any,
        who: Any,
        task_id: str,
        body: Any,
        verdict: Any,
        operation: str,
        params: Any = None,
        ingest: Any = None,
    ) -> dict[str, Any]:
        """The write inside the store's write, through its verdict: held, nothing is written but the hold."""
        spec = writes[operation]
        replay: dict[str, Any] = {
            "task": task_id,
            "body": body.model_dump(mode="json", by_alias=True),
            "params": params or {},
        }
        gate = task_rules.apply(
            c,
            who,
            task_id,
            verdict,
            spec["said"](body),
            replay,
            to=spec["to"](body),
            also=reached(spec, params),
            hands_over=spec["hands_over"],
            ask=spec["ask"],
        )
        if task_rule_gate.held(gate):
            return org_rule_check.answer(gate)
        out: Any = spec["call"](c, who, task_id, body, gate, params or {}, ingest) or {}
        return {**out, **org_rule_check.answer(gate)}

    def reached(spec: dict[str, Any], params: Any) -> Callable[[Any], Any] | None:
        """The write's `also`, bound to its params: a function of the connection, or None."""
        also = spec["also"]
        return (lambda c: also(c, params or {})) if also else None

    def check(caller: Any, tid: str, body: Any, operation: str, params: Any = None) -> Any:
        """The verdict for this write, outside the store's write."""
        spec = writes[operation]
        return task_rules.check(
            caller,
            tid,
            operation,
            spec["said"](body),
            to=spec["to"](body),
            also=reached(spec, params),
            hands_over=spec["hands_over"],
            late=spec["late"],
            ask=spec["ask"],
        )

    def checked(request: Request, tid: str, body: Any, operation: str, params: Any = None) -> Any:
        """A route's write: checked outside the store's write, then made through its verdict inside it."""
        rc = check(request.state.identity, tid, body, operation, params)
        return work(request, tid, body, lambda c, who, task_id: gated(c, who, task_id, body, rc, operation, params))

    def replays() -> None:
        """Send anyway on a held task write: the same write once, as its author, at the version it was checked at
        (`org_rule_check.send` refuses once the task changed), with a newer rule's warning line if any (`gate`)."""

        def replay_of(operation: str) -> tuple[Callable[..., Any], Callable[..., Any]]:
            spec = writes[operation]

            def replay(c: Any, bot: Any, hold: Any, data: Any, ingest: Any, gate: Any = None) -> dict[str, Any]:
                body = spec["model"].model_validate(data["body"])
                who = acting(c, bot, body)
                task_id = auth.resolve_task(c, who, data["task"])
                auth.task(c, who, task_id)
                row = H.task(c, task_id)
                if row and getattr(body, "version", None) is not None:
                    body = body.model_copy(update={"version": row["version"]})
                decided = org_rule_check.decided(gate)
                return answer(c, who, task_id, **gated(c, who, task_id, body, decided, operation, data.get("params")))

            def audience(c: Any, bot: Any, data: Any) -> Any:
                body = spec["model"].model_validate(data["body"])
                row = auth.task(c, bot, auth.resolve_task(c, bot, data["task"]))
                also = reached(spec, data.get("params"))
                return task_rules.audience(c, bot, row, spec["to"](body), also)

            return replay, audience

        for operation in writes:
            replay, audience = replay_of(operation)
            org_rule_check.replayer(operation, audience=audience)(replay)

    # ------------------------------------------------------------------ where it is
    def setup_finished(c: Any, who: Any, task_id: str) -> None:
        """A seat bot's "Set up" task is done once the bot is live, and its template's routines are on by then: go-live
        turned them on, and one made active another way (its Settings switch) gets them here, once."""
        bot = setup_live.require_live(c, task_id)
        onboarding = getattr(app.state, "onboarding", None)
        if bot and onboarding:
            onboarding.arm_routines(c, who, bot)

    def done_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        setup_finished(c, who, task_id)
        note, version = carry(c, who, task_id, gate, body.note or "", body.version)
        done: dict[str, Any] = H.task_done(c, who.actor, task_id, note, version, mover=mover(c, who) or None) or {}
        # A done note is kept on the answer it gave (answers.answer names it): a late check's warning line goes there.
        answered: str | None = done.get("answer_id")
        if answered:
            org_rule_check.late_on(c, gate, "answer", answer=answered)

    says("task_done", M.TaskDone, lambda body: [("Done", body.note)], done_call, late=True)

    @app.post("/api/v2/tasks/{tid}/done")
    def task_done(request: Request, tid: str, body: M.TaskDone):
        """The work of this stage is finished. It never pushes past anything: while something else is missing it is
        refused with what is missing and the commands allowed next. In the last stage the task is Done."""
        return checked(request, tid, body, "task_done")

    def move_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        if str(body.to).strip().lower() == "done":
            setup_finished(c, who, task_id)
        why, version = carry(c, who, task_id, gate, body.why, body.version)
        H.task_move(c, who.actor, task_id, body.to, why, version, mover=mover(c, who) or None)

    says("task_move", M.TaskMove, lambda body: [("Why", body.why)], move_call)

    @app.post("/api/v2/tasks/{tid}/move")
    def task_move(request: Request, tid: str, body: M.TaskMove):
        """Forward (`next`; with a why, past what is not met), back to an earlier stage, `done`, `closed` or `reopen`."""
        return checked(request, tid, body, "task_move")

    def reassign(c: Any, who: Any, task_id: str, body: Any, ingest: Any = None) -> Any:
        """H.task_reassign, with the checks a hand-over makes before it: the new owner is someone the caller may give
        work to and reach, within the caller's task cap for them, and ask ingest's verdict on a bot handing its own
        task to a person."""
        row = H.task(c, task_id)
        if not row:
            raise Problem("not_found", "No such task", 404)
        target = auth.target(c, who, body.owner, need="write")
        if target == row["owner_id"]:
            return None
        auth.require_bot_contact(c, who, target, task_id=task_id, kind="task")
        reach.check(c, who, target, "task", task_id=task_id)
        task_caps.check(c, who.actor, target)
        gate = None
        if ingest and ingest["person"] == target and row["owner_id"] == who.actor:
            gate = ask_ingest.apply(c, ingest)
            ask_ingest.record(c, ingest, gate, task_id)
            if gate["acts"]:
                return ask_ingest.reply(gate)  # the bot keeps the task: its ask joins one the person has
        elif ingest:
            ask_ingest.skipped(c, ingest, "not_applied")
        H.task_reassign(
            c,
            who.actor,
            task_id,
            target,
            body.why,
            body.version,
            mover=mover(c, who) or None,
            set_anyway=bool(getattr(body, "set_anyway", False)),
        )
        task_caps.record(c, task_id, who.actor, target)
        return ask_ingest.reply(gate) if gate else None

    def owner_ingest(caller: Any, tid: str, body: Any) -> Any:
        """Ask ingest for a bot handing its own task to a person, judged outside the write like a task filed for them."""
        if not ask_ingest.gated(caller, auth.settings.assistant_bot):
            return None
        with store.read() as c:
            try:
                row = auth.task(c, caller, auth.resolve_task(c, caller, tid))
            except Problem:
                return None
        if row["owner_id"] != caller.actor:
            return None
        return handoff_preflight(caller, row, body.owner, body.why)

    def owner_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        # A warned hand-off's line goes on its why, or with none before the task's note (kept whole).
        why, version = carry(c, who, task_id, gate, body.why, body.version)
        return reassign(c, who, task_id, body.model_copy(update={"why": why, "version": version}), ingest)

    says(
        "task_owner",
        M.TaskOwner,
        lambda body: [("Why", body.why)],
        owner_call,
        to=lambda body: body.owner,
        hands_over=True,
    )

    @app.post("/api/v2/tasks/{tid}/owner")
    def task_owner(request: Request, tid: str, body: M.TaskOwner):
        """Give the task to another owner for this stage."""
        caller = request.state.identity
        rc = check(caller, tid, body, "task_owner")
        ingest = None if org_rule_check.holds(rc) else owner_ingest(caller, tid, body)

        def call(c: Any, who: Any, task_id: str) -> Any:
            return gated(c, who, task_id, body, rc, "task_owner", ingest=ingest)

        return ask_ingest.judged(store, [ingest] if ingest else [], lambda: work(request, tid, body, call))

    # ------------------------------------------------------------------ set aside, hand back
    def snooze_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        setup_live.refuse_snooze_once_live(c, task_id)  # a seat's setup task waits for its go-live, not after it
        why, version = carry(c, who, task_id, gate, body.why, body.version)
        until = parse_until(body.until)
        done = H.task_snooze(c, who.actor, task_id, why, until, version, mover=mover(c, who) or None)
        return {"note": done["note"]} if done.get("note") else None

    says("task_snooze", M.TaskSnooze, lambda body: [("Why", body.why)], snooze_call)

    @app.post("/api/v2/tasks/{tid}/snooze")
    def task_snooze(request: Request, tid: str, body: M.TaskSnooze):
        """Set a task aside in its stage until a date, or with none."""
        until = parse_until(body.until)  # a bad date is refused before anything is checked
        snooze_rules(request, tid, body, until)
        return checked(request, tid, body, "task_snooze")

    def snooze_rules(request: Request, tid: str, body: Any, until: Any) -> None:
        """The stage rules on a bot's snooze with a Jev question (Snooze only for a real wait), asked once for all of
        them, outside the store's write (stage_rules.judge_snooze). A Block is written on the task's history, naming
        the rule, and the snooze is refused with its reason. Jev off or failing lets the snooze go."""
        caller = request.state.identity
        if not H.is_bot(caller.actor) or getattr(body, "on_behalf_of", None):
            return  # a person's snooze, or one made for a person, is not judged
        with store.read() as c:
            try:
                task_id = auth.resolve_task(c, caller, tid)
                row = auth.task(c, caller, task_id)
            except Problem:
                return  # the write itself says what is wrong
            row = H.task(c, task_id) or row
            if row.get("status") != "open":
                return
            found, asked = stage_rules.snooze_rules(c, row, caller.actor, body.why or "", until)
        found = found or stage_rules.ask_snooze(row, body.why or "", until, asked, store=store)
        if not found:
            return
        rule, line = found
        with store.transaction() as c:
            stage_rules.record(c, task_id, rule, line, caller.actor, data={"why": body.why or "", "until": until or ""})
        # As the in-write refusal does: the bot is told what to do instead (an ask, or a sub-task), at this version.
        extra = {"next": task_engine._then(row, "ask", "file"), "version": row.get("version")}
        raise Problem("task_stage_rule", task_engine.refusal_line(rule, line), 409, extra=extra)

    def unsnooze_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        H.task_unsnooze(c, who.actor, task_id, task_rule_gate.warn_on(gate, body.why))

    says("task_unsnooze", M.TaskWhy, lambda body: [("Why", body.why)], unsnooze_call)

    @app.post("/api/v2/tasks/{tid}/unsnooze")
    def task_unsnooze(request: Request, tid: str, body: M.TaskWhy):
        """Bring a snoozed task back now."""
        return checked(request, tid, body, "task_unsnooze")

    def try_again_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        H.task_try_again(c, who.actor, task_id, task_rule_gate.warn_on(gate, body.why), mover=mover(c, who) or None)

    says("task_try_again", M.TaskWhy, lambda body: [("Why", body.why)], try_again_call)

    @app.post("/api/v2/tasks/{tid}/try-again")
    def task_try_again(request: Request, tid: str, body: M.TaskWhy):
        """A person hands a task that reached them back to its owner, with the count at zero."""
        who = request.state.identity
        if who.role == "bot" or who.task_actor:
            raise Problem("forbidden", "A person hands a task back to its owner", 403)
        return checked(request, tid, body, "task_try_again")

    # ------------------------------------------------------------------ asks
    def ask_said(body: Any) -> list[tuple[str, Any]]:
        return [
            ("Ask", body.line),
            ("Choices", " / ".join(body.choices or [])),
            ("Shows", "\n".join(body.show or [])),
        ]

    def ask_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        from . import lifecycle as _lifecycle

        lifecycle: Any = _lifecycle
        # An onboarding bot puts nothing to anyone but its manager and the people above it.
        lifecycle.refuse_send(c, who, H.resolve_actor(c, body.to) or body.to, what="a question")
        # An ask's line is one plain sentence: a warning line goes before the task's note instead.
        version = task_rule_gate.note_first(c, who, task_id, gate, body.version, mover(c, who) or None)
        made = H.task_ask(
            c,
            who.actor,
            task_id,
            body.to,
            body.line,
            kind=body.kind,
            choices=body.choices,
            shows=body.show or None,
            seen_version=version,
            mover=mover(c, who) or None,
        )
        if gate and gate.get("late"):
            saved: dict[str, Any] = H.task(c, task_id) or {}
            org_rule_check.late_on(c, gate, "task_note", task=task_id, note=saved.get("note") or "")
        return {"requirement_id": made.get("requirement_id")}

    says("task_ask", M.TaskAsk, ask_said, ask_call, to=lambda body: body.to, ask=True, late=True)

    @app.post("/api/v2/tasks/{tid}/asks")
    def task_ask(request: Request, tid: str, body: M.TaskAsk):
        """Ask a person on the task, in one sentence: a question (with choices, or a typed answer) or an OK (C19). A
        bot's ask a company rule covers is refused with the rule's words (docs/pipelines-v3.md, "How a rule stops the
        next one")."""
        return checked(request, tid, body, "task_ask")

    def withdraw_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        requirement = requirement_on(c, task_id, params["requirement_id"])
        H.task_withdraw_ask(
            c, who.actor, requirement, task_rule_gate.warn_on(gate, body.why), mover=mover(c, who) or None
        )

    says("task_withdraw_ask", M.TaskWhy, lambda body: [("Why", body.why)], withdraw_call)

    @app.delete("/api/v2/tasks/{tid}/asks/{requirement_id}")
    def task_withdraw_ask(request: Request, tid: str, requirement_id: str, body: M.TaskWhy | None = None):
        """Take back an ask that was added to the task."""
        body = body or M.TaskWhy()
        return checked(request, tid, body, "task_withdraw_ask", {"requirement_id": requirement_id})

    def answer_said(body: Any) -> list[tuple[str, Any]]:
        typed = body.value if body.value.strip().lower() not in task_rule_gate.BUTTONS else None
        return [("Answer", typed), ("Note", body.note)]

    def answer_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        asked = requirement_on(c, task_id, params["requirement_id"])
        refused = task_asks.refuses_ok_by(c, who, asked, body.value)
        if refused:
            raise Problem("ok_not_yours", refused, 403)
        # A warned answer carries the line in its own notes, with or without a note of its own: answering never needs
        # the right to edit the task.
        note = task_rule_gate.warn_on(gate, body.note) if body.note else org_rule_check.warn_line(gate) or body.note
        done = H.task_answer(
            c,
            who.actor,
            task_id,
            asked,
            body.value,
            note,
            body.version,
            own_click=task_asks.own_click(who),
            page_version=body.page_version,
        )
        # An answer on the Strategy Owner's merged item goes to each original it stands for.
        strategy_merge.on_answer(c, who.actor, task_id, body.value)
        return {"answer_id": done.get("answer_id")}

    def asker(c: Any, params: dict[str, Any]) -> list[str]:
        """Whoever asked what an answer answers: the answer reaches them too."""
        found: Any = c.execute(
            "SELECT added_by_id FROM requirements WHERE id=?", (params.get("requirement_id"),)
        ).fetchone()
        return [found[0]] if found and found[0] else []

    says("task_ask_answer", M.TaskAskAnswer, answer_said, answer_call, also=asker)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/answer")
    def task_answer(request: Request, tid: str, requirement_id: str, body: M.TaskAskAnswer):
        """OK, send back (`no` with a note), look again (`again`), or the answer to a question. A requirement of the
        stage is answered here too."""
        return checked(request, tid, body, "task_ask_answer", {"requirement_id": requirement_id})

    @app.post("/api/v2/tasks/{tid}/pages")
    def task_page_attach(request: Request, tid: str, body: M.TaskPageAttach):
        """Attach a page to a page requirement of the task's stage: Jev checks it, then a person OKs it if one is
        named. Attaching again replaces the page."""
        from . import files as F
        from . import task_pages

        def call(c: Any, who: Any, task_id: str) -> Any:
            ref = body.page.strip()
            try:
                file_id, _ = task_pages.parse_ref(ref)
            except ValueError as exc:
                raise Problem("validation", str(exc), 422) from None
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (file_id,)).fetchone()
            if not row or not F.file_visible(c, auth, who, row, {}):  # pyright: ignore[reportUnknownMemberType]
                raise Problem("not_found", "That page is not one you can read", 404)
            made = H.task_page_attach(c, who.actor, task_id, body.requirement, ref, body.version)
            return {"page": {"file_id": made["file_id"], "version": made["version"]}}

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/give")
    def task_give(request: Request, tid: str, requirement_id: str, body: M.TaskAskGive):
        """Give an ask to another person: it leaves your list and goes to theirs."""

        def call(c: Any, who: Any, task_id: str) -> Any:
            H.task_give(c, who.actor, task_id, requirement_on(c, task_id, requirement_id), body.to, body.version)

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/thumbs")
    def task_thumbs(request: Request, tid: str, requirement_id: str, body: M.TaskAskThumbs):
        """Was this worth asking. A thumbs down says what was wrong, and sends an open ask back."""

        def call(c: Any, who: Any, task_id: str) -> Any:
            done = H.task_thumbs(c, who.actor, task_id, requirement_on(c, task_id, requirement_id), body.up, body.note)
            return {"feedback_id": done.get("feedback_id")} if done.get("feedback_id") else None

        return work(request, tid, body, call)

    # ------------------------------------------------------------------ OKs bound to what was shown (task_asks)
    def ok_task(c: Any, who: Any, requirement_id: str) -> str:
        """The task an OK is on, when the caller may read it. 404 otherwise, alike for one that is not there."""
        task_id = task_asks.task_of(c, requirement_id) if task_asks.grant(c, requirement_id) else None
        if not task_id:
            raise Problem("not_found", "No such OK", 404)
        try:
            auth.task(c, who, task_id)
        except Problem:
            raise Problem("not_found", "No such OK", 404) from None
        return task_id

    @app.get("/api/v2/oks/{requirement_id}")
    def ok_preview(request: Request, requirement_id: str):
        """What an OK is for, exactly as it was asked, with the `shown_hash` to send back with the OK. Opened by a
        person in their own session, it records that they were shown it."""
        who = request.state.identity
        with store.transaction() as c:
            task_id = ok_task(c, who, requirement_id)
            if not task_asks.may_see(c, auth, who, requirement_id):
                # The subject's own reader check: reading the task an OK is on never widens who sees what it is for.
                raise Problem("not_found", "No such OK", 404)
            shown = task_asks.preview(c, who.actor, requirement_id, own_click=task_asks.own_click(who))
            return {**shown, "task_id": task_id}

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/ok")
    def task_give_ok(request: Request, tid: str, requirement_id: str, body: M.TaskOkGive):
        """The person it is asked of OKs exactly what they were shown, by their own click. Never a bot, a token, the
        Assistant or anyone acting for them; never content that changed since they opened it."""
        who = request.state.identity
        ctx = task_asks.Context(auth, auth.settings, who, app)

        def write(c: Any) -> dict[str, Any]:
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            if task_asks.task_of(c, requirement_id) != task_id or not task_asks.grant(c, requirement_id):
                raise Problem("not_found", "No such OK on this task", 404)
            task_asks.give_ok(
                c, who.actor, requirement_id, body.shown_hash, own_click=task_asks.own_click(who), ctx=ctx
            )
            return answer(c, who, task_id, requirement_id=requirement_id)

        out = mutate(request, body, write, durable=True)
        for after in ctx.after:
            after()
        return out

    def ok_ask_said(body: Any) -> list[tuple[str, Any]]:
        content = json.dumps(body.content, sort_keys=True, default=str) if body.content is not None else ""
        return [("Ask", body.line), ("Shows", "\n".join(body.show or [])), ("Content", content)]

    def ok_ask_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        from . import lifecycle as _lifecycle
        from . import task_engine

        lifecycle: Any = _lifecycle
        lifecycle.refuse_send(c, who, H.resolve_actor(c, body.to) or body.to, what="an ask for an OK")
        row = H.task(c, task_id)
        if not row:
            raise Problem("not_found", "No such task", 404)
        task_engine.check_version(c, who.actor, row, body.version)
        # The OK's line and content are exactly what is OK'd: a warning line goes before the task's note instead.
        task_rule_gate.note_first(c, who, task_id, gate, body.version, mover(c, who) or None)
        asked = task_asks.ask_ok(
            c,
            who.actor,
            body.to,
            body.line,
            shows=body.show or None,
            subject=body.subject,
            content=body.content,
            task_id=task_id,
        )
        return {"requirement_id": asked["requirement_id"]}

    says("task_ok_ask", M.TaskOkAsk, ok_ask_said, ok_ask_call, to=lambda body: body.to, ask=True)

    @app.post("/api/v2/tasks/{tid}/oks")
    def task_ask_ok(request: Request, tid: str, body: M.TaskOkAsk):
        """A bot asks a person to OK exactly this content before it does `subject`. Subjects whose own module asks
        (an email, a letter, an edition...) are asked there, not here. One a company rule covers is refused like any
        ask."""
        if not task_asks.generic(body.subject):
            raise Problem("validation", "This kind of OK is asked where the work is, not here", 422)
        return checked(request, tid, body, "task_ok_ask")

    def ok_use_call(c: Any, who: Any, task_id: str, body: Any, gate: Any, params: Any, ingest: Any) -> Any:
        requirement_id = params["requirement_id"]
        task_asks.use(c, requirement_id, body.subject, body.content, who.actor)
        task_asks.settle(c, requirement_id, task_rule_gate.warn_on(gate, body.note) or "Done")
        return {"requirement_id": requirement_id}

    says("task_ok_use", M.OkUse, lambda body: [("Done", body.note)], ok_use_call)

    @app.post("/api/v2/oks/{requirement_id}/use")
    def ok_use(request: Request, requirement_id: str, body: M.OkUse):
        """Whoever asked uses the OK, once, for the same subject and exactly the same content, just before the act."""
        if not task_asks.generic(body.subject):
            raise Problem("validation", "This kind of OK is used where the work is, not here", 422)
        who = request.state.identity
        rc = None
        if body.note:
            with store.read() as c:
                tid = task_asks.task_of(c, requirement_id) if task_asks.grant(c, requirement_id) else None
            rc = check(who, tid, body, "task_ok_use") if tid else None
        params = {"requirement_id": requirement_id}

        def write(c: Any) -> dict[str, Any]:
            task_id = ok_task(c, who, requirement_id)
            return answer(c, who, task_id, **gated(c, who, task_id, body, rc, "task_ok_use", params))

        return mutate(request, body, write, durable=True)

    @app.post("/api/v2/answers/{answer_id}/undo")
    def answer_undo(request: Request, answer_id: str, body: M.Empty):
        """Take an answer back: whoever gave it, or any person."""

        def write(c: Any) -> dict[str, Any]:
            who = request.state.identity
            found = c.execute(
                "SELECT v.task_id FROM answers a JOIN visits v ON v.id=a.visit_id WHERE a.id=?", (answer_id,)
            ).fetchone()
            if not found:
                raise Problem("not_found", "No such answer", 404)
            auth.task(c, who, found[0])
            # An undo that touches a bound OK (the OK or its send back) is the person's own click: never the
            # Assistant's, BotOps' or a token's.
            bound = c.execute(
                "SELECT 1 FROM answers a JOIN ok_grants g ON g.requirement_id=a.requirement_id WHERE a.id=?",
                (answer_id,),
            ).fetchone()
            if bound and not task_asks.own_click(who):
                raise Problem("ok_not_yours", task_asks.NOT_YOURS, 403)
            H.task_answer_undo(c, who.actor, answer_id)
            return answer(c, who, found[0])

        return mutate(request, body, write)

    # ------------------------------------------------------------------ the merge queue
    @app.post("/api/v2/tasks/{tid}/queue")
    def task_queue(request: Request, tid: str, body: M.TaskQueue):
        """Queue again: after GitHub took a PR out of the merge queue (or refused it), put it back at its newest
        commit (backend/item_sources/github.py `requeue`). The stage owner, the pipeline's manager or any person."""

        def call(c: Any, who: Any, task_id: str) -> Any:
            from .item_sources import github as GS

            owed = H.task_requeue(c, who.actor, task_id, body.version)
            prs = [(GS.split_key(e["item_key"]), e["pinned"]) for e in owed]
            return {"queued": [{"repo": repo, "number": number, "head": head} for (repo, number), head in prs]}

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/release")
    def task_release(request: Request, tid: str, body: M.TaskRelease):
        """Cut the release the task waits for in its Release stage, the way In a release names (`release_with`): Tico
        runs the release workflow or creates the tag through its GitHub App (backend/item_sources/github_release.py).
        The stage owner, the pipeline's manager or any person; a bot only once a person signed off before release."""

        def call(c: Any, who: Any, task_id: str) -> Any:
            owed = H.task_release(c, who.actor, task_id, body.tag, body.version)
            return {"asked": [{"repo": e["item_key"], "how": e["pinned"].rpartition("#")[0]} for e in owed]}

        read_releases_first(request, tid)
        return work(request, tid, body, call)

    def read_releases_first(request: Request, tid: str) -> None:
        """Read the releases the task waits on from GitHub before the write, with no transaction held: the repo may
        have published one with the merge since Tico last read them, and then the answer names it instead of asking
        for another (item_sources/github.py `read_releases_now`). Anything that fails here leaves the call as it was."""
        service = getattr(request.app.state, "github_app", None)
        try:
            if service is None or service.row() is None:
                return
            with store.read() as c:
                task_id = auth.resolve_task(c, request.state.identity, tid)
            from .github import AppGitHub
            from .item_sources import github as GS

            GS.read_releases_now(store, AppGitHub(service), task_id)
        except Exception:  # noqa: BLE001 - the write below answers as it would have
            logging.getLogger("tico.github").info("No releases read before the release of %s", tid, exc_info=True)

    replays()
