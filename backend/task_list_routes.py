"""Task list routes: task filters, notes, labels, stuck tasks, task flow."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from fastapi.responses import Response
from . import delivery
from . import lifecycle
from . import org_rule_check
from . import task_privacy as privacy
import re
from . import reach
from . import views


def register(app, auth, mutate, rule_check, rule_target, store, task_filters, task_idents, visible_tasks):
    @app.get("/api/v2/tasks")
    def tasks(
        request: Request,
        response: Response,
        owner: str | None = None,
        requester: str | None = None,
        status: str | None = None,
        lane: str | None = None,
        label: str | None = None,
        limit: int = 500,
        offset: int = 0,
        sort: str = "queue",
        type: str | None = None,
        step: str | None = None,
        number: int | None = None,
        updated_since: str | None = None,
        brief: bool = False,
        member: str | None = None,
        role: str | None = None,
        changed_after: str | None = None,
        ids: str | None = None,
        snoozed: str | None = None,
        q: str = "",
    ):
        from . import task_reads as reads

        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            if limit < 1 or limit > 500:
                raise Problem("limit", "limit is between 1 and 500", 422)
            if offset < 0:
                raise Problem("offset", "offset is zero or greater", 422)
            if sort not in ("queue", "finished", "step"):
                raise Problem("sort", "sort is queue, finished or step", 422)
            owner, requester, filters = task_filters(
                c,
                owner=owner,
                requester=requester,
                status=status,
                lane=lane,
                label=label,
                type=type,
                step=step,
                number=number,
                updated_since=updated_since,
                member=member,
                role=role,
                snoozed=snoozed,
                # A snoozed task is off every list until its date unless asked for by id, number or filter.
                by_id=ids is not None or number is not None or bool(re.fullmatch(r"#\d+", q.strip())),
            )
            # One read: the visibility, the rows, the cursor and the ETag agree (backend/task_reads.py).
            visible_sql = auth.task_sql(c, who)
            # Several tasks by id in one read (an agent's "show these"), instead of one GET each.
            typed = None
            if ids is not None:
                typed, found = task_idents(c, who, ids, visible_sql)
                if changed_after is not None:
                    raise Problem("ids", "ids and changed_after are separate reads", 422)
            filters.update(order=sort, visible_sql=visible_sql, q=q)
            if typed is not None:
                filters["ids"] = [tid for tid in found.values() if tid]

            def brief_rows(rows):
                # A board or a bot's task list polling many tasks needs neither their text nor their criteria.
                if brief:
                    for row in rows:
                        for field in (
                            "body",
                            "acceptance_criteria",
                            "acceptance",
                            "public_title",
                            "public_summary",
                            "check_back_note",
                            "check_back",
                            "requester_count",
                            "external_requesters",
                            "internal_requesters",
                            "important_requesters",
                            "organization",
                            "size",
                        ):
                            row.pop(field, None)
                        for field in (
                            "verdict",
                            "verdict_head",
                            "check_back_at",
                            "done_at",
                            "closed_at",
                            "closed_by_id",
                        ):
                            if row.get(field) is None:
                                row.pop(field, None)
                        if isinstance(row.get("step"), dict):
                            row["step"].pop("public_name", None)
                            # A stage's Warn after is the stage's, sent once with the pipeline's steps (task types), not on
                            # each of a board's cards (pipeline-rail.ts pipelineStepLimit).
                            row["step"].pop("limit_minutes", None)
                        # A snooze's note (up to 500 characters) is text too; the task's own read keeps it whole.
                        for hold in row.get("holds") or ():
                            if hold["kind"] == "snoozed":
                                hold.pop("reason", None)

            if changed_after is not None:
                found = reads.changed(c, changed_after, visible_sql)
                ids = reads.around(c, found[0]) if found else []
                if found is None or len(ids) > reads.CAP:
                    return {
                        "tasks": [],
                        "gone": [],
                        "reset": True,
                        "cursor": reads.cursor(c, visible_sql),
                        "next_offset": None,
                    }
                was, cursor = found
                rows = visible_tasks(c, who, owner, requester, limit=len(ids), ids=ids, **filters)[0] if ids else []
                gone = reads.gone(c, visible_sql, was, {row["id"] for row in rows})
                result = {"tasks": rows, "gone": gone, "cursor": cursor, "next_offset": None}
            else:
                # Unchanged anywhere: a 304 without reading a row. Changed elsewhere but not in these rows: a 304
                # after reading them, so an idle list on a busy team still sends no body.
                reader = (
                    reads.access(visible_sql),
                    who.actor,
                    who.task_actor,
                    sorted(request.query_params.multi_items()),
                )
                whole, held = reads.digest("tasks", reads.version(c), reads.names(c), *reader), reads.held(request)
                for was_whole, was_rows in held:
                    if was_whole == whole:
                        return reads.not_modified(reads.list_tag(whole, was_rows))
                rows, next_offset = visible_tasks(c, who, owner, requester, limit=limit, offset=offset, **filters)
                brief_rows(rows)
                part = reads.digest(rows, next_offset, *reader)
                tag = reads.list_tag(whole, part)
                if any(was_rows == part for _, was_rows in held):
                    return reads.not_modified(tag)
                response.headers["ETag"] = tag
                result = {"tasks": rows, "next_offset": next_offset, "cursor": reads.cursor(c, visible_sql)}
                if typed is not None:
                    # What was asked for and is not here: unknown, ambiguous, outside the filters or not readable.
                    returned = {row["id"] for row in rows}
                    result["missing"] = [ident for ident in typed if found[ident] not in returned]
            brief_rows(rows)
            return result

    # ------------------------------------------------------------------ quiet notes
    def note_view(row, c):
        waiting = H.note_waiting(c, row)
        return {
            "id": row["id"],
            "from": row["from_actor"],
            "to": row["to_actor"],
            "text": row["body"],
            "created_at": row["created_at"],
            "waiting": waiting,
            "carried": bool(row.get("carried_by")) and not waiting and not row.get("canceled_at"),
            "carried_by": row.get("carried_by"),
            "canceled_at": row.get("canceled_at"),
        }

    def note_visible(c, who, row):
        # A note copied from a message is read only while its recipient still reads that message (the same check a
        # run's claim makes, threads.revalidate_notes), whoever asks, and only by who may read that message themselves.
        from . import threads

        if not threads.note_readable(c, row, row["to_actor"]):
            return False
        # A bot sees the notes it left and the ones left for it; a person, the ones they left and
        # every note between bots whose activity they may read.
        if who.role == "bot":
            allowed = who.actor in (row["from_actor"], row["to_actor"])
        elif who.actor == row["from_actor"]:
            allowed = True
        else:
            bots = [H.actor_id(a) for a in (row["from_actor"], row["to_actor"]) if str(a).startswith("bot:")]
            allowed = all(level["read"] for level in auth.bot_accesses(c, who, bots).values())
        return allowed and threads.note_source_shown(c, auth, who, row)

    def note_write(c, who, body, rc=None):
        to = auth.target(c, who, body.to, need="write")
        auth.require_bot_contact(c, who, to, kind="note")
        reach.check(c, who, to, "chat")
        lifecycle.refuse_send(c, who, to, what="a note")
        rule = org_rule_check.apply(c, rc, body.text, {"body": body.model_dump(mode="json", by_alias=True)})
        if rule and rule.get("held"):
            return org_rule_check.answer(rule)
        # A note is a deferred message to the bot's next run of any kind; `defer_for` wakes it by then.
        made = H.note_create(c, who.actor, to, org_rule_check.warned_text(rule, body.text))
        due = delivery.defer(c, made["to_actor"], "note", made["id"], defer_for=body.defer_for)
        return {
            "note": {**note_view(made, c), "sent_as": delivery.sent_as("defer", due)},
            **org_rule_check.answer(rule),
        }

    @app.post("/api/v2/notes")
    def create_note(request: Request, body: M.NoteCreate):
        who = request.state.identity
        reach.precheck(store, who, lambda c: reach.check(c, who, auth.target(c, who, body.to, need="write"), "chat"))
        rc = rule_check(who, "note", body.text, lambda c: [rule_target(c, who, body.to)])
        return mutate(request, body, lambda c: note_write(c, who, body, rc))

    @org_rule_check.replayer("note")
    def rule_replay_note(c, bot, hold, replay, ingest, gate=None):
        body = M.NoteCreate.model_validate(replay["body"])
        # The note's reader must read what the run it came from carries, as for any write to them.
        privacy.require_destination(c, bot, auth.target(c, bot, body.to, need="write"), None, {})
        return note_write(c, bot, body, org_rule_check.decided(gate))

    @app.get("/api/v2/notes")
    def notes(
        request: Request,
        to: str | None = None,
        sender: str | None = None,
        since: str | None = None,
        waiting: bool = False,
        limit: int = 200,
    ):
        who = request.state.identity
        auth.domain(who)
        if limit < 1 or limit > 500:
            raise Problem("limit", "limit is between 1 and 500", 422)
        with store.read() as c:
            where, args = [], []
            if to:
                where.append("n.to_actor=?")
                args.append(H.resolve_actor(c, to) or to)
            if sender:
                where.append("n.from_actor=?")
                args.append(H.resolve_actor(c, sender) or sender)
            if since:
                where.append("n.created_at>=?")
                args.append(views.since_time(since))
            if waiting:
                where.append(H.NOTE_WAITING_SQL)
            if who.role == "bot":
                where.append("(n.from_actor=? OR n.to_actor=?)")
                args += [who.actor, who.actor]
            elif who.role == "human":
                hidden = ["bot:" + slug for slug in sorted(auth.unreadable_bots(c, who))]
                if hidden:
                    marks = "(" + ",".join("?" * len(hidden)) + ")"
                    where.append(f"(n.from_actor=? OR NOT (n.from_actor IN {marks} OR n.to_actor IN {marks}))")
                    args += [who.actor, *hidden, *hidden]
            sql = (
                "SELECT n.* FROM notes n"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY n.created_at DESC, n.id DESC LIMIT ?"
            )
            rows = [dict(r) for r in c.execute(sql, (*args, limit))]
            return {"notes": [note_view(r, c) for r in rows if note_visible(c, who, r)]}

    @app.post("/api/v2/notes/{nid}/cancel")
    def cancel_note(request: Request, nid: str, body: M.Empty):
        def work(c):
            who = request.state.identity
            row = H.note(c, nid)
            if not row or not note_visible(c, who, row):
                raise Problem("not_found", "Note not found", 404)
            return {"note": note_view(H.note_cancel(c, who.actor, nid, human=who.role in ("human", "owner")), c)}

        return mutate(request, body, work)

    @app.get("/api/v2/tasks/labels")
    def task_labels(request: Request, response: Response):
        from . import task_reads as reads

        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            visible_sql = auth.task_sql(c, who)
            tag = reads.etag("labels", reads.version(c), reads.access(visible_sql), reads.names(c), who.actor)
            if reads.fresh(request, tag):
                return reads.not_modified(tag)
            keys = H.labels_in_use(c, visible_sql)
            response.headers["ETag"] = tag
            return {"labels": keys, "tags": [H.tag_by_key(c, key) for key in keys]}

    @app.get("/api/v2/tasks/stuck")
    def tasks_stuck(request: Request, hours: int = H.STUCK_HOURS):
        """Every bot's stuck work, for the fleet maintainer's sweep and for people."""
        who = request.state.identity
        if who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER):
            raise Problem("forbidden", "The stuck-task sweep is BotOps's and people's", 403)
        with store.read() as c:
            return {
                "tasks": [
                    t
                    for t in H.stuck_tasks(c, hours=max(1, hours), hidden=auth.unreadable_bots(c, who))
                    if privacy.task_readable(c, who, H.task(c, t["id"]))
                ]
            }

    @app.get("/api/v2/tasks/flow")
    def tasks_flow(
        request: Request,
        days: int = 30,
        tz: int = 0,
        owner: str | None = None,
        requester: str | None = None,
        owner_kind: str | None = None,
        label: str | None = None,
        recurring: bool | None = None,
        type: str | None = None,
    ):
        """Status changes over time for the Tasks page's stats: counts by day, time in each stage."""
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "Task stats are for humans", 403)
        if days < 1 or days > 365:
            raise Problem("days", "days is between 1 and 365", 422)
        if owner_kind not in (None, "", "bot", "human"):
            raise Problem("owner_kind", "owner_kind is bot or human", 422)
        with store.read() as c:
            owner = H.resolve_actor(c, owner) if owner else None
            requester = H.resolve_actor(c, requester) if requester else None
            typ = H.type_get(c, type) if type else None
            if type and not typ:
                raise Problem("type", "No task type " + type, 422)
            tz = max(-840, min(840, tz))  # minutes east of UTC; days are the viewer's days
            today = H.shift(H.now(), minutes=tz)[:10] + "T00:00:00.000000Z"
            since = H.shift(today, days=1 - days, minutes=-tz)
            return {
                "since": since,
                **H.task_flow(
                    c,
                    since,
                    tz_minutes=tz,
                    visible=auth.task_sql(c, who),
                    owner=owner,
                    requester=requester,
                    owner_kind=owner_kind or None,
                    label=label,
                    recurring=recurring,
                    type_id=typ["id"] if typ else None,
                ),
            }
