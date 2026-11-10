"""The routes that work a task (docs/dev/pipelines-v3-cutover.md, "HTTP"): done, move, owner, snooze, unsnooze, try
again, asks and their answers, OKs bound to what a person was shown (task_asks), and taking an answer back.

Each route calls one task call (`H.task_done`, `H.task_move`, ...) inside the store's write and answers the task as
JSON. None of them writes a task's status, stage or owner: the engine does. A refusal is the engine's
(`task_not_met`, `task_changed`, ...), with what is missing, the commands allowed next and the version.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import Request

from . import models as M
from . import ask_ingest, org_rule_check, reach, strategy_merge, task_asks, task_caps
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
        raise Problem("date", "until is an ISO-8601 date or date-time", 422)
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
    rule_update_check,
    rule_update_text,
    store,
    task_view,
):
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

    # ------------------------------------------------------------------ where it is
    @app.post("/api/v2/tasks/{tid}/done")
    def task_done(request: Request, tid: str, body: M.TaskDone):
        """The work of this stage is finished. It never pushes past anything: while something else is missing it is
        refused with what is missing and the commands allowed next. In the last stage the task is Done."""

        def call(c, who, task_id):
            H.task_done(c, who.actor, task_id, body.note or "", body.version, mover=mover(c, who) or None)

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/move")
    def task_move(request: Request, tid: str, body: M.TaskMove):
        """Forward (`next`; with a why, past what is not met), back to an earlier stage, `done`, `closed` or `reopen`."""

        def call(c, who, task_id):
            H.task_move(c, who.actor, task_id, body.to, body.why, body.version, mover=mover(c, who) or None)

        return work(request, tid, body, call)

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

    @app.post("/api/v2/tasks/{tid}/owner")
    def task_owner(request: Request, tid: str, body: M.TaskOwner):
        """Give the task to another owner for this stage."""
        caller = request.state.identity
        rc = rule_update_check(caller, tid, body)
        ingest = None if org_rule_check.holds(rc) else owner_ingest(caller, tid, body)

        def call(c, who, task_id):
            replay = {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)}
            gate = org_rule_check.apply(c, rc, rule_update_text(body, H.task(c, task_id)), replay) if rc else None
            if gate and gate.get("held"):
                return org_rule_check.answer(gate)
            return {**(reassign(c, who, task_id, body, ingest) or {}), **org_rule_check.answer(gate)}

        return ask_ingest.judged(store, [ingest] if ingest else [], lambda: work(request, tid, body, call))

    @org_rule_check.replayer("task_owner")
    def rule_replay_owner(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskOwner.model_validate(replay["body"])
        task_id = auth.resolve_task(c, bot, replay["task"])
        row = H.task(c, task_id)
        if row:
            # `send` refused it if the task changed since the hold (its version is the check's object).
            body = body.model_copy(update={"version": row["version"]})
        return answer(c, bot, task_id, **(reassign(c, bot, task_id, body) or {}), **org_rule_check.answer(gate))

    # ------------------------------------------------------------------ set aside, hand back
    @app.post("/api/v2/tasks/{tid}/snooze")
    def task_snooze(request: Request, tid: str, body: M.TaskSnooze):
        """Set a task aside in its stage until a date, or with none (On ice)."""
        until = parse_until(body.until)

        def call(c, who, task_id):
            done = H.task_snooze(c, who.actor, task_id, body.why, until, body.version, mover=mover(c, who) or None)
            return {"note": done["note"]} if done.get("note") else None

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/unsnooze")
    def task_unsnooze(request: Request, tid: str, body: M.TaskWhy):
        """Bring a snoozed task back now."""

        def call(c, who, task_id):
            H.task_unsnooze(c, who.actor, task_id, body.why)

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/try-again")
    def task_try_again(request: Request, tid: str, body: M.TaskWhy):
        """A person hands a task that reached them back to its owner, with the count at zero."""
        who = request.state.identity
        if who.role == "bot" or who.task_actor:
            raise Problem("forbidden", "A person hands a task back to its owner", 403)

        def call(c, who, task_id):
            H.task_try_again(c, who.actor, task_id, body.why, mover=mover(c, who) or None)

        return work(request, tid, body, call)

    # ------------------------------------------------------------------ asks
    @app.post("/api/v2/tasks/{tid}/asks")
    def task_ask(request: Request, tid: str, body: M.TaskAsk):
        """Ask a person on the task, in one sentence: a question (with choices, or a typed answer) or an OK (C19)."""

        def call(c, who, task_id):
            from . import lifecycle

            # An onboarding bot puts nothing to anyone but its manager and the people above it.
            lifecycle.refuse_send(c, who, H.resolve_actor(c, body.to) or body.to, what="a question")
            made = H.task_ask(
                c,
                who.actor,
                task_id,
                body.to,
                body.line,
                kind=body.kind,
                choices=body.choices,
                shows=body.show or None,
                seen_version=body.version,
                mover=mover(c, who) or None,
            )
            return {"requirement_id": made.get("requirement_id")}

        return work(request, tid, body, call)

    @app.delete("/api/v2/tasks/{tid}/asks/{requirement_id}")
    def task_withdraw_ask(request: Request, tid: str, requirement_id: str, body: M.TaskWhy | None = None):
        """Take back an ask that was added to the task."""
        body = body or M.TaskWhy()

        def call(c, who, task_id):
            requirement = requirement_on(c, task_id, requirement_id)
            H.task_withdraw_ask(c, who.actor, requirement, body.why, mover=mover(c, who) or None)

        return work(request, tid, body, call)

    @app.post("/api/v2/tasks/{tid}/asks/{requirement_id}/answer")
    def task_answer(request: Request, tid: str, requirement_id: str, body: M.TaskAskAnswer):
        """OK, send back (`no` with a note), look again (`again`), or the answer to a question. A requirement of the
        stage is answered here too."""

        def call(c, who, task_id):
            asked = requirement_on(c, task_id, requirement_id)
            refused = task_asks.refuses_ok_by(c, who, asked, body.value)
            if refused:
                raise Problem("ok_not_yours", refused, 403)
            done = H.task_answer(c, who.actor, task_id, asked, body.value, body.note, body.version)
            # An answer on the Strategy Bot's merged item goes to each original it stands for.
            strategy_merge.on_answer(c, who.actor, task_id, body.value)
            return {"answer_id": done.get("answer_id")}

        return work(request, tid, body, call)

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

    @app.post("/api/v2/tasks/{tid}/oks")
    def task_ask_ok(request: Request, tid: str, body: M.TaskOkAsk):
        """A bot asks a person to OK exactly this content before it does `subject`. Subjects whose own module asks
        (an email, a letter, an edition...) are asked there, not here."""
        if not task_asks.generic(body.subject):
            raise Problem("validation", "This kind of OK is asked where the work is, not here", 422)

        def call(c, who, task_id):
            from . import lifecycle, task_engine

            lifecycle.refuse_send(c, who, H.resolve_actor(c, body.to) or body.to, what="an ask for an OK")
            task_engine.check_version(c, who.actor, H.task(c, task_id), body.version)
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

        return work(request, tid, body, call)

    @app.post("/api/v2/oks/{requirement_id}/use")
    def ok_use(request: Request, requirement_id: str, body: M.OkUse):
        """Whoever asked uses the OK, once, for the same subject and exactly the same content, just before the act."""
        if not task_asks.generic(body.subject):
            raise Problem("validation", "This kind of OK is used where the work is, not here", 422)

        def write(c):
            who = request.state.identity
            task_id = ok_task(c, who, requirement_id)
            task_asks.use(c, requirement_id, body.subject, body.content, who.actor)
            task_asks.settle(c, requirement_id, body.note or "Done")
            return answer(c, who, task_id, requirement_id=requirement_id)

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
            H.task_answer_undo(c, who.actor, answer_id)
            return answer(c, who, found[0])

        return mutate(request, body, write)
