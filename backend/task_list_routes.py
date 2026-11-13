"""Task list routes: task filters, notes, labels."""

import base64
import json
import re
from typing import Any

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from fastapi.responses import Response
from . import delivery
from . import lifecycle
from . import org_rule_check
from . import task_privacy as privacy
from . import reach
from . import views


# The query parameters GET /api/v2/tasks takes: its own filters (the handler's keywords below, kept equal by
# test_task_reads) and `on_behalf_of`, which the BotOps middleware reads on every v2 route. Any other name is refused:
# FastAPI drops unknown query parameters, so a misspelled or unsupported filter (`assignee=`) used to answer the
# whole list instead of the filtered one.
LIST_FILTERS = frozenset(
    {
        "owner",
        "requester",
        "status",
        "lane",
        "label",
        "limit",
        "offset",
        "sort",
        "number",
        "updated_since",
        "brief",
        "member",
        "role",
        "changed_after",
        "ids",
        "snoozed",
        "q",
        "pipeline",
        "stage",
        "waiting_on",
        "pushed_past",
        "search",
        "owners",
        "stages",
        "requesters",
        "labels",
        "turn",
        "due",
        "late",
        "after",
    }
)
# Passed through and ignored. `type`: the Recorder's task picker sends `type=<task type>` from 0.5.181 (Nov 5, 2026)
# until the build that stopped (recorder/src/task_links.rs `search_tasks`); installed copies keep sending it, so it is
# accepted rather than refused. The list has no type filter, so it never narrowed the answer.
_LIST_PASSED = frozenset({"on_behalf_of", "type"})
_FILTER_HINTS = {"assignee": "use owner= (who has it now)"}


def unknown_filters(names):
    """Refuse a list read naming a filter it does not take (422 `filter`), with the filters it does take."""
    unknown = sorted(set(names) - LIST_FILTERS - _LIST_PASSED)
    if unknown:
        hints = "".join(f" For {n}, {_FILTER_HINTS[n]}." for n in unknown if n in _FILTER_HINTS)
        raise Problem(
            "filter",
            f"Unknown filter: {', '.join(unknown)}.{hints} Filters: {', '.join(sorted(LIST_FILTERS))}.",
            422,
        )


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
        number: int | None = None,
        updated_since: str | None = None,
        brief: bool = False,
        member: str | None = None,
        role: str | None = None,
        changed_after: str | None = None,
        ids: str | None = None,
        snoozed: str | None = None,
        q: str = "",
        pipeline: str | None = None,
        stage: str | None = None,
        waiting_on: str | None = None,
        pushed_past: bool | None = None,
        search: str = "",
        owners: str | None = None,
        stages: str | None = None,
        requesters: str | None = None,
        labels: str | None = None,
        turn: str | None = None,
        due: str | None = None,
        late: bool | None = None,
        after: str | None = None,
    ):
        """Tasks this reader may read. `pipeline` and `stage` (ids) keep a pipeline's or a stage's tasks;
        `waiting_on` (a person or a bot) keeps the open tasks with a wait on them (waiting_on.py: a person's or a
        bot's turn, or a bot that can't run); `pushed_past=true` keeps the tasks that entered their stage pushed past
        a requirement (false, the rest). `search`, `owners`, `stages`, `requesters`, `labels`, `turn`, `due` and
        `late` are the Tasks page's search and filters, with its meaning (backend/task_match.py). Every page answers
        `next_after` when there is more: pass it back as `after` for the next page (a keyset page, which a task added
        or removed meanwhile does not shift); `next_offset` stays for offset paging."""
        from . import task_match
        from . import task_reads as reads
        from . import teammate_access

        who = request.state.identity
        auth.domain(who)
        unknown_filters(request.query_params.keys())
        with store.read_transaction() as c:
            if limit < 1 or limit > 500:
                raise Problem("limit", "limit is between 1 and 500", 422)
            if offset < 0:
                raise Problem("offset", "offset is zero or greater", 422)
            if sort not in ("queue", "finished"):
                raise Problem("sort", "sort is queue or finished", 422)
            owner, requester, filters = task_filters(
                c,
                owner=owner,
                requester=requester,
                status=status,
                lane=lane,
                label=label,
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
            narrow = narrowing(c, pipeline, stage, pushed_past)
            if narrow:
                filters["narrow"] = narrow
            if waiting_on is not None:
                waits_on = waited_on(c, waiting_on, visible_sql, narrow)
                filters["ids"] = [t for t in filters["ids"] if t in waits_on] if "ids" in filters else sorted(waits_on)
            hidden = frozenset(teammate_access.Known(c, who, auth).people if (owners or requesters or turn) else ())
            match = task_match.parse(
                H.now(), search, owners, stages, requesters, labels, turn, due, late, hidden=hidden
            )
            if match.sql:
                filters["match"] = match.sql
            if after is not None:
                if changed_after is not None or offset:
                    raise Problem("after", "after is a page's start: not with changed_after or offset", 422)
                filters["after"] = keyset_start(after, sort)

            def matched(restrict=None):
                """The search's and Waiting on's ids among the rows the other filters leave (all of the view's, or
                `restrict`'s); None when neither is set."""
                if not (match.words or match.turn is not None):
                    return None
                kw = {k: v for k, v in filters.items() if k not in ("visible_sql", "narrow", "order", "after")}
                if restrict is not None:
                    chosen = set(restrict)
                    kw["ids"] = [t for t in kw["ids"] if t in chosen] if "ids" in kw else list(restrict)
                candidates: Any = H.tasks(
                    c,
                    owner=owner,
                    requester=requester,
                    limit=None,
                    order=sort,
                    visible=f"({visible_sql}) AND ({narrow})" if narrow else visible_sql,
                    keys_only=True,
                    **kw,
                )
                return task_match.kept(c, match, candidates, visible_sql)

            def brief_rows(rows):
                # A board or a bot's task list polling many tasks needs neither their text nor their criteria.
                if brief:
                    for row in rows:
                        for field in (
                            "body",
                            "acceptance_criteria",
                            "public_title",
                            "public_summary",
                            "requester_count",
                            "external_requesters",
                            "internal_requesters",
                            "important_requesters",
                            "organization",
                            "size",
                        ):
                            row.pop(field, None)
                        for field in ("done_at", "closed_at", "closed_by_id", "assignee_id", "human_id"):
                            if row.get(field) is None:
                                row.pop(field, None)

            if changed_after is not None:
                found = reads.changed(c, changed_after, visible_sql)
                changed_ids = reads.around(c, found[0]) if found else []
                if found is None or len(changed_ids) > reads.CAP:
                    return {
                        "tasks": [],
                        "gone": [],
                        "reset": True,
                        "cursor": reads.cursor(c, visible_sql),
                        "next_offset": None,
                    }
                was, cursor = found
                # A waiting-on (or ids) selection already in the filters keeps only the changed tasks it holds.
                chosen = set(filters.pop("ids")) if "ids" in filters else None
                changed_ids = [tid for tid in changed_ids if chosen is None or tid in chosen]
                kept = matched(changed_ids) if changed_ids else None
                if kept is not None:
                    changed_ids = [tid for tid in changed_ids if tid in set(kept)]
                rows = (
                    visible_tasks(c, who, owner, requester, limit=len(changed_ids), ids=changed_ids, **filters)[0]
                    if changed_ids
                    else []
                )
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
                kept = matched()
                if kept is not None:
                    filters["ids"] = [t for t in filters["ids"] if t in set(kept)] if "ids" in filters else kept
                rows, last = visible_tasks(c, who, owner, requester, limit=limit, offset=offset, keyset=True, **filters)
                next_offset = offset + limit if last is not None else None
                next_after = keyset_token(last) if last is not None else None
                brief_rows(rows)
                # The validator is the answer itself and the reader's access. A change this reader cannot read (a
                # hidden ticket, another team's) leaves it as it was, so it sends no new ETag for identical data.
                tag = reads.etag("tasks", rows, next_offset, next_after, *reader)
                if reads.fresh(request, tag):
                    return reads.not_modified(tag)
                response.headers["ETag"] = tag
                result = {
                    "tasks": rows,
                    "next_offset": next_offset,
                    "next_after": next_after,
                    "cursor": reads.cursor(c, visible_sql),
                }
                if typed is not None:
                    # What was asked for and is not here: unknown, ambiguous, outside the filters or not readable.
                    returned = {row["id"] for row in rows}
                    result["missing"] = [ident for ident in typed if found[ident] not in returned]
            brief_rows(rows)
            return result

    def keyset_token(key):
        return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")

    def keyset_start(token, sort):
        """The sort key a page's `next_after` carries, checked: four parts in queue order, two in finished order."""
        try:
            key = (
                json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode())
                if len(token) < 2000
                else None
            )
        except (ValueError, UnicodeDecodeError):
            key = None
        shapes = {"queue": (int, (int, float), str, str), "finished": (str, str)}[sort]
        if (
            not isinstance(key, list)
            or len(key) != len(shapes)
            or any(isinstance(v, bool) or not isinstance(v, kind) for v, kind in zip(key, shapes, strict=True))
        ):
            raise Problem("after", "after is a page's next_after, in the same sort", 422)
        return key

    def narrowing(c, pipeline, stage, pushed_past):
        """The list's pipeline, stage and pushed-past filters as one WHERE fragment over the task's own columns, or
        None. A pipeline or stage is matched by its rowid, so nothing a caller typed is ever part of the SQL."""
        parts = []
        if pipeline is not None:
            found = c.execute("SELECT rowid FROM pipelines WHERE id=?", (pipeline,)).fetchone()
            if not found:
                raise Problem("pipeline", "No such pipeline", 422)
            parts.append(f"pipeline_id=(SELECT id FROM pipelines WHERE rowid={int(found[0])})")
        if stage is not None:
            found = c.execute("SELECT rowid FROM stages WHERE id=?", (stage,)).fetchone()
            if not found and pipeline is not None:
                # A stage by its name within the pipeline, as a move takes it (`tico task move <id> Slipping`): a bot
                # that lists "Watching" by name is not refused (Initech, Nov 11).
                found = c.execute(
                    "SELECT rowid FROM stages WHERE pipeline_id=? AND lower(name)=lower(?) AND retired_at IS NULL",
                    (pipeline, str(stage).strip()),
                ).fetchone()
            if not found:
                raise Problem(
                    "stage",
                    "No such stage in this pipeline"
                    if pipeline is not None
                    else "No such stage. Give its id, or its name with the pipeline",
                    422,
                )
            parts.append(f"stage_id=(SELECT id FROM stages WHERE rowid={int(found[0])})")
        if pushed_past is not None:
            parts.append(
                ("" if pushed_past else "NOT ")
                + "EXISTS (SELECT 1 FROM visits v WHERE v.task_id=tasks.id AND v.left_at IS NULL "
                "AND v.entered_how='pushed_past')"
            )
        return " AND ".join(parts) or None

    WAITING_ON_CAP = 500

    def waited_on(c, actor, visible_sql, narrow):
        """The ids of the open tasks this reader may read that wait on `actor`: a person's or a bot's turn, or a bot
        that can't run. Each task's waits are its own read (waiting_on.py), so at most WAITING_ON_CAP open tasks are
        looked at: a wider ask is refused, to be narrowed by pipeline or stage first."""
        from . import waiting_on as W

        found = H.resolve_actor(c, actor)
        if not found:
            raise Problem("waiting_on", "waiting_on is a person or a bot on the roster", 422)
        where = f"status='open' AND pipeline_id IS NOT NULL AND ({visible_sql})" + (
            f" AND ({narrow})" if narrow else ""
        )
        ids = [r[0] for r in c.execute(f"SELECT id FROM tasks WHERE {where} LIMIT ?", (WAITING_ON_CAP + 1,))]
        if len(ids) > WAITING_ON_CAP:
            raise Problem(
                "waiting_on",
                f"waiting_on looks at {WAITING_ON_CAP} open tasks at most: add a pipeline or a stage",
                422,
            )
        return {
            tid
            for tid, waits in W.waiting_on_many(c, ids).items()
            if any(w.who == found and w.state in (W.PERSON, W.PAUSED, W.BOT) for w in waits)
        }

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
            # The tag is of the labels this reader sees, not of the newest task change: an edit to a task it cannot
            # read leaves the tag, and the answer, as they were.
            keys = H.labels_in_use(c, visible_sql)
            tags = [H.tag_by_key(c, key) for key in keys]
            tag = reads.etag("labels", reads.access(visible_sql), reads.names(c), who.actor, keys, tags)
            if reads.fresh(request, tag):
                return reads.not_modified(tag)
            response.headers["ETag"] = tag
            return {"labels": keys, "tags": tags}
