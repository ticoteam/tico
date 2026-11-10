"""The routes that work a task (docs/dev/pipelines-v3-cutover.md, "HTTP"): done, move, owner, snooze, unsnooze, try
again, asks and their answers, OKs bound to what a person was shown (task_asks), and taking an answer back.

Each route calls one task call (`H.task_done`, `H.task_move`, ...) inside the store's write and answers the task as
JSON. None of them writes a task's status, stage or owner: the engine does. A refusal is the engine's
(`task_not_met`, `task_changed`, ...), with what is missing, the commands allowed next and the version.
"""

import json
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import Request

from . import models as M
from . import ask_ingest, org_rule_check, reach, strategy_merge, task_asks, task_caps, task_rule_gate
from .store import H, Problem

# A snooze's bare date is the start of that day here, as the bot tools say ("the start of that day, Pacific").
ZONE = ZoneInfo("America/Los_Angeles")


def parse_until(value):
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
    app,
    auth,
    delegated_identity,
    handoff_preflight,
    mover,
    mutate,
    task_rules,
    store,
    task_view,
):
    """`task_rules` is the org rule check of a task write (backend/task_rule_gate.py): every route here that carries
    words goes through it (`checked`), and Send anyway replays the same write (`replays`)."""

    def acting(c, caller, body):
        """Who the change is made as: the caller, or the person whose request BotOps carries (`on_behalf_of`)."""
        ref = getattr(body, "on_behalf_of", None)
        return delegated_identity(c, caller, ref) if ref else caller

    def answer(c, who, task_id, **more):
        return {"task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who), **more}

    def work(request, tid, body, call):
        """One task call on the task `tid` names, as the caller (or the person they act for), in the store's write.
        `call(c, who, task_id)` makes it and returns what the answer carries besides the task (or None); the answer is
        the task as it is after."""
        caller = request.state.identity

        def write(c):
            who = acting(c, caller, body)
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            more = call(c, who, task_id) or {}
            return answer(c, who, task_id, **more)

        return mutate(request, body, write)

    def requirement_on(c, task_id, requirement_id):
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
    writes = {}

    def says(operation, model, said, call, *, to=None, hands_over=False, ask=False):
        writes[operation] = {
            "model": model,
            "said": said,
            "call": call,
            "to": to or (lambda body: None),
            "hands_over": hands_over,
            "ask": ask,
        }

    def carry(c, who, task_id, gate, text, version):
        """A warned write's words, the line first: on its own text when it has some, else before the task's note.
        Returns (text, version to write with)."""
        if text:
            return task_rule_gate.warn_on(gate, text), version
        return text, task_rule_gate.note_first(c, who, task_id, gate, version, mover(c, who) or None)

    def gated(c, who, task_id, body, verdict, operation, params=None, ingest=None):
        """The write inside the store's write, through its verdict: held, nothing is written but the hold."""
        spec = writes[operation]
        replay = {"task": task_id, "body": body.model_dump(mode="json", by_alias=True), "params": params or {}}
        gate = task_rules.apply(
            c, who, task_id, verdict, spec["said"](body), replay, hands_over=spec["hands_over"], ask=spec["ask"]
        )
        if task_rule_gate.held(gate):
            return org_rule_check.answer(gate)
        out = spec["call"](c, who, task_id, body, gate, params or {}, ingest) or {}
        return {**out, **org_rule_check.answer(gate)}

    def check(caller, tid, body, operation):
        """The verdict for this write, outside the store's write."""
        spec = writes[operation]
        return task_rules.check(
            caller, tid, operation, spec["said"](body), to=spec["to"](body), hands_over=spec["hands_over"]
        )

    def checked(request, tid, body, operation, params=None):
        """A route's write: checked outside the store's write, then made through its verdict inside it."""
        rc = check(request.state.identity, tid, body, operation)
        return work(request, tid, body, lambda c, who, task_id: gated(c, who, task_id, body, rc, operation, params))

    def replays():
        """Send anyway on a held task write: the same write once, as its author, at the version it was checked at
        (`org_rule_check.send` refuses once the task changed), with a newer rule's warning line if any (`gate`)."""

        def replay_of(operation):
            spec = writes[operation]

            def replay(c, bot, hold, data, ingest, gate=None):
                body = spec["model"].model_validate(data["body"])
                who = acting(c, bot, body)
                task_id = auth.resolve_task(c, who, data["task"])
                auth.task(c, who, task_id)
                row = H.task(c, task_id)
                if row and getattr(body, "version", None) is not None:
                    body = body.model_copy(update={"version": row["version"]})
                decided = org_rule_check.decided(gate)
                return answer(c, who, task_id, **gated(c, who, task_id, body, decided, operation, data.get("params")))

            def audience(c, bot, data):
                body = spec["model"].model_validate(data["body"])
                row = auth.task(c, bot, auth.resolve_task(c, bot, data["task"]))
                return task_rules.audience(c, bot, row, spec["to"](body))

            return replay, audience

        for operation in writes:
            replay, audience = replay_of(operation)
            org_rule_check.replayer(operation, audience=audience)(replay)

    # ------------------------------------------------------------------ where it is
    def done_call(c, who, task_id, body, gate, params, ingest):
        note, version = carry(c, who, task_id, gate, body.note or "", body.version)
        H.task_done(c, who.actor, task_id, note, version, mover=mover(c, who) or None)

    says("task_done", M.TaskDone, lambda body: [("Done", body.note)], done_call)

    @app.post("/api/v2/tasks/{tid}/done")
    def task_done(request: Request, tid: str, body: M.TaskDone):
        """The work of this stage is finished. It never pushes past anything: while something else is missing it is
        refused with what is missing and the commands allowed next. In the last stage the task is Done."""
        return checked(request, tid, body, "task_done")

    def move_call(c, who, task_id, body, gate, params, ingest):
        why, version = carry(c, who, task_id, gate, body.why, body.version)
        H.task_move(c, who.actor, task_id, body.to, why, version, mover=mover(c, who) or None)

    says("task_move", M.TaskMove, lambda body: [("Why", body.why)], move_call)

    @app.post("/api/v2/tasks/{tid}/move")
    def task_move(request: Request, tid: str, body: M.TaskMove):
        """Forward (`next`; with a why, past what is not met), back to an earlier stage, `done`, `closed` or `reopen`."""
        return checked(request, tid, body, "task_move")

    def reassign(c, who, task_id, body, ingest=None):
        """H.task_reassign, with the checks a hand-over makes before it: the new owner is someone the caller may give
        work to and reach, within the caller's task cap for them, and ask ingest's verdict on a bot handing its own
        task to a person."""
        row = H.task(c, task_id)
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
        H.task_reassign(c, who.actor, task_id, target, body.why, body.version, mover=mover(c, who) or None)
        task_caps.record(c, task_id, who.actor, target)
        return ask_ingest.reply(gate) if gate else None

    def owner_ingest(caller, tid, body):
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

    def owner_call(c, who, task_id, body, gate, params, ingest):
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

        def call(c, who, task_id):
            return gated(c, who, task_id, body, rc, "task_owner", ingest=ingest)

        return ask_ingest.judged(store, [ingest] if ingest else [], lambda: work(request, tid, body, call))

    # ------------------------------------------------------------------ set aside, hand back
    def snooze_call(c, who, task_id, body, gate, params, ingest):
        why, version = carry(c, who, task_id, gate, body.why, body.version)
        until = parse_until(body.until)
        done = H.task_snooze(c, who.actor, task_id, why, until, version, mover=mover(c, who) or None)
        return {"note": done["note"]} if done.get("note") else None

    says("task_snooze", M.TaskSnooze, lambda body: [("Why", body.why)], snooze_call)

    @app.post("/api/v2/tasks/{tid}/snooze")
    def task_snooze(request: Request, tid: str, body: M.TaskSnooze):
        """Set a task aside in its stage until a date, or with none (On ice)."""
        parse_until(body.until)  # a bad date is refused before anything is checked
        return checked(request, tid, body, "task_snooze")

    def unsnooze_call(c, who, task_id, body, gate, params, ingest):
        H.task_unsnooze(c, who.actor, task_id, task_rule_gate.warn_on(gate, body.why))

    says("task_unsnooze", M.TaskWhy, lambda body: [("Why", body.why)], unsnooze_call)

    @app.post("/api/v2/tasks/{tid}/unsnooze")
    def task_unsnooze(request: Request, tid: str, body: M.TaskWhy):
        """Bring a snoozed task back now."""
        return checked(request, tid, body, "task_unsnooze")

    def try_again_call(c, who, task_id, body, gate, params, ingest):
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
    def ask_said(body):
        return [
            ("Ask", body.line),
            ("Choices", " / ".join(body.choices or [])),
            ("Shows", "\n".join(body.show or [])),
        ]

    def ask_call(c, who, task_id, body, gate, params, ingest):
        from . import lifecycle

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
        return {"requirement_id": made.get("requirement_id")}

    says("task_ask", M.TaskAsk, ask_said, ask_call, to=lambda body: body.to, ask=True)

    @app.post("/api/v2/tasks/{tid}/asks")
    def task_ask(request: Request, tid: str, body: M.TaskAsk):
        """Ask a person on the task, in one sentence: a question (with choices, or a typed answer) or an OK (C19). A
        bot's ask a company rule covers is refused with the rule's words (docs/pipelines-v3.md, "How a rule stops the
        next one")."""
        return checked(request, tid, body, "task_ask")

    def withdraw_call(c, who, task_id, body, gate, params, ingest):
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

    def answer_said(body):
        typed = body.value if body.value.strip().lower() not in task_rule_gate.BUTTONS else None
        return [("Answer", typed), ("Note", body.note)]

    def answer_call(c, who, task_id, body, gate, params, ingest):
        asked = requirement_on(c, task_id, params["requirement_id"])
        refused = task_asks.refuses_ok_by(c, who, asked, body.value)
        if refused:
            raise Problem("ok_not_yours", refused, 403)
        note, version = carry(c, who, task_id, gate, body.note, body.version)
        done = H.task_answer(c, who.actor, task_id, asked, body.value, note, version)
        # An answer on the Strategy Bot's merged item goes to each original it stands for.
        strategy_merge.on_answer(c, who.actor, task_id, body.value)
        return {"answer_id": done.get("answer_id")}

    says("task_ask_answer", M.TaskAskAnswer, answer_said, answer_call)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/answer")
    def task_answer(request: Request, tid: str, requirement_id: str, body: M.TaskAskAnswer):
        """OK, send back (`no` with a note), look again (`again`), or the answer to a question. A requirement of the
        stage is answered here too."""
        return checked(request, tid, body, "task_ask_answer", {"requirement_id": requirement_id})

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/give")
    def task_give(request: Request, tid: str, requirement_id: str, body: M.TaskAskGive):
        """Give an ask to another person: it leaves your list and goes to theirs."""

        def call(c, who, task_id):
            H.task_give(c, who.actor, task_id, requirement_on(c, task_id, requirement_id), body.to, body.version)

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/thumbs")
    def task_thumbs(request: Request, tid: str, requirement_id: str, body: M.TaskAskThumbs):
        """Was this worth asking. A thumbs down says what was wrong, and sends an open ask back."""

        def call(c, who, task_id):
            done = H.task_thumbs(c, who.actor, task_id, requirement_on(c, task_id, requirement_id), body.up, body.note)
            return {"feedback_id": done.get("feedback_id")} if done.get("feedback_id") else None

        return work(request, tid, body, call)

    # ------------------------------------------------------------------ OKs bound to what was shown (task_asks)
    def ok_task(c, who, requirement_id):
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

        def write(c):
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

    def ok_ask_said(body):
        content = json.dumps(body.content, sort_keys=True, default=str) if body.content is not None else ""
        return [("Ask", body.line), ("Shows", "\n".join(body.show or [])), ("Content", content)]

    def ok_ask_call(c, who, task_id, body, gate, params, ingest):
        from . import lifecycle, task_engine

        lifecycle.refuse_send(c, who, H.resolve_actor(c, body.to) or body.to, what="an ask for an OK")
        task_engine.check_version(c, who.actor, H.task(c, task_id), body.version)
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

    def ok_use_call(c, who, task_id, body, gate, params, ingest):
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

        def write(c):
            task_id = ok_task(c, who, requirement_id)
            return answer(c, who, task_id, **gated(c, who, task_id, body, rc, "task_ok_use", params))

        return mutate(request, body, write, durable=True)

    @app.post("/api/v2/answers/{answer_id}/undo")
    def answer_undo(request: Request, answer_id: str, body: M.Empty):
        """Take an answer back: whoever gave it, or any person."""

        def write(c):
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

    replays()
