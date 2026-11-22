"""Task list routes: task filters, notes, labels."""

from . import teammate_access
from . import sources as SRC
from . import task_relations as TR
from . import task_roles as TRo

import base64
import json
import re
from typing import Any, cast

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
from .api_contract import api_v2
from .api_answers.tasks import TaskList


# The query parameters GET /api/v2/tasks takes: its own filters (the handler's keywords below, kept equal by
# test_task_reads) and `on_behalf_of`, which the Agent Ops middleware reads on every v2 route. Any other name is refused:
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
        "counts",
        "exact",
        "link",
        "entity",
    }
)
# `on_behalf_of`, read by the Agent Ops middleware (above).
_LIST_PASSED = frozenset({"on_behalf_of"})
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
    @app.get(
        "/api/v2/tasks",
        **api_v2(
            "Tasks",
            "listTasks",
            "Tasks the caller can see, brief: each row's version, pipeline_id, stage_id, owner, turn (whose turn it is, and why), late and snoozed. q matches title words or #N; status (open, done, closed), number and updated_since filter, member=<actor> keeps the tasks that person or bot is on in some role (role=<name> narrows it to one), brief=true leaves out bodies. Snoozed tasks are left out unless snoozed=only|all, status=all, or asked for by ids or number. Each answer carries a `cursor`; changed_after=<cursor> with the same filters answers only the tasks changed since (`tasks`) and the ids that left (`gone`), or `reset: true` to read in full. A full read has an ETag: If-None-Match answers 304 when nothing changed. A query parameter that is not one of these filters answers 422 `filter` naming it (owner=, not assignee=). counts=stage also answers `counts`: how many tasks the same filters select in each stage, across every page",
            TaskList,
            parameters={
                "counts": "stage: also answer `counts`, the number of tasks these filters select in each stage across "
                "every page (the rows a reader may not read are never counted). Not with changed_after",
                "exact": "true: an owner or requester that names nobody answers 422, never the list without that "
                "filter (a client asking for one person's tasks must not get every task)",
                "link": "<source>:<type>:<key>[@<connection_id>] (jira:issue:PROJ-12@tool_1, github:pr:acme/app#7) or "
                "the item's URL (a Jira link takes its site's connection): only the tasks linking that outside item "
                "through that connection. A Jira item always names its connection",
                "entity": "A Contacts entity (an id, email or domain you can see; a merged one is the one it went "
                "into): only the tasks for it. One you cannot see lists none",
            },
        ),
    )
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
        counts: str | None = None,
        exact: bool = False,
        link: str | None = None,
        entity: str | None = None,
    ):
        """Tasks this reader may read. `pipeline` and `stage` (ids) keep a pipeline's or a stage's tasks;
        `waiting_on` (a person or a bot) keeps the open tasks with a wait on them (waiting_on.py: a person's or a
        bot's turn, or a bot that can't run); `pushed_past=true` keeps the tasks that entered their stage pushed past
        a requirement (false, the rest). `search`, `owners`, `stages`, `requesters`, `labels`, `turn`, `due` and
        `late` are the Tasks page's search and filters, with its meaning (backend/task_match.py). Every page answers
        `next_after` when there is more: pass it back as `after` for the next page (a keyset page, which a task added
        or removed meanwhile does not shift); `next_offset` stays for offset paging. `counts=stage` adds how many tasks
        the filters select in each stage, all pages together, so a board shows a stage's count with one page loaded.
        `link` keeps the tasks that link one outside item (backend/item_sources `parse_ref`), through its connection."""
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
            if counts not in (None, "stage"):
                raise Problem("counts", "counts is stage", 422)
            if counts and changed_after is not None:
                raise Problem("counts", "counts is a full read's: not with changed_after", 422)
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
                exact=exact,
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
            if link is not None:
                filters["match"] = [*filters.get("match", ()), linked_to(c, link)]
            moved = False
            if entity is not None:
                from . import page_links
                from .entities import store as ES

                # One the reader cannot see lists nothing, as one that is not there does.
                reader = page_links.entity_reader(c, auth, who)
                entities = ES.get(c, reader, entity) if reader is not None else []
                if len(entities) > 1:
                    raise Problem("ambiguous", f"More than one entity matches {entity}; name one by id", 409)
                # tasks.entity_id<>'' lets the partial index tasks_entity serve it; nothing seen is a literal false.
                chosen = str(entities[0]["id"]) if entities else ""
                fragment = ("tasks.entity_id=? AND tasks.entity_id<>''", (chosen,)) if chosen else ("0", ())
                filters["match"] = [*filters.get("match", ()), fragment]
                # An entity id merged since now lists its kept entity's tasks: a changes read starts over.
                moved = bool(chosen) and chosen != entity and ES.kept(c, entity) == chosen
            if after is not None:
                if changed_after is not None or offset:
                    raise Problem("after", "after is a page's start: not with changed_after or offset", 422)
                filters["after"] = keyset_start(after, sort)

            def matched(restrict=None):
                """The search's, Late's and Waiting on's ids among the rows the other filters leave (all of the view's, or
                `restrict`'s); None when neither is set."""
                if not (match.words or match.turn is not None or match.late):
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
                        for field in ("done_at", "closed_at", "closed_by_id", "assignee_id", "human_id", "came_from"):
                            if row.get(field) is None:
                                row.pop(field, None)

            if changed_after is not None:
                found = None if moved else reads.changed(c, changed_after, visible_sql)
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
                # The same call as the rows' with the same filters, grouped: the counts and the list cannot select
                # differently. A task in no stage counts in the total only.
                per_stage = visible_tasks(c, who, owner, requester, per_stage=True, **filters) if counts else None
                tallies = (
                    {
                        "total": sum(per_stage.values()),
                        "stage": dict(sorted((stage_id, n) for stage_id, n in per_stage.items() if stage_id)),
                    }
                    if per_stage is not None
                    else None
                )
                # The validator is the answer itself and the reader's access. A change this reader cannot read (a
                # hidden ticket, another team's) leaves it as it was, so it sends no new ETag for identical data.
                tag = reads.etag(
                    "tasks", rows, next_offset, next_after, *reader, *([tallies] if tallies is not None else [])
                )
                if reads.fresh(request, tag):
                    return reads.not_modified(tag)
                response.headers["ETag"] = tag
                result = {
                    "tasks": rows,
                    "next_offset": next_offset,
                    "next_after": next_after,
                    "cursor": reads.cursor(c, visible_sql),
                }
                if tallies is not None:
                    result["counts"] = tallies
                if typed is not None:
                    # What was asked for and is not here: unknown, ambiguous, outside the filters or not readable.
                    returned = {row["id"] for row in rows}
                    result["missing"] = [ident for ident in typed if found[ident] not in returned]
            brief_rows(rows)
            return result

    def linked_to(c, ref):
        """The list's `link` filter: a WHERE fragment, with its arguments, keeping the tasks that link the outside item
        `ref` names. 422 `link` for a reference that names no source's item."""
        from . import item_sources as IS

        try:
            item = IS.parse_ref(c, ref)
        except ValueError as error:
            raise Problem("link", str(error), 422) from None
        # Item first: the item's few links by task_links_item_key, then only their tasks.
        return (
            "tasks.id IN (SELECT task_id FROM task_links WHERE source=? AND item_type=? AND item_key=? "
            "AND connection_id=?)",
            item,
        )

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

    def note_bots(row):
        return [H.actor_id(a) for a in (row["from_actor"], row["to_actor"]) if str(a).startswith("bot:")]

    def note_levels(c, who, rows):
        """The caller's access to every bot `note_visible` asks about for `rows`, in one pass (Auth.bot_accesses decides
        each bot on its own, so it answers a page as it answers one note)."""
        if who.role == "bot":
            return {}
        asked = [bot for row in rows if who.actor != row["from_actor"] for bot in note_bots(row)]
        return auth.bot_accesses(c, who, asked) if asked else {}

    def note_visible(c, who, row, levels=None):
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
            bots = note_bots(row)
            found = levels if levels is not None else auth.bot_accesses(c, who, bots)
            allowed = all(found[bot]["read"] for bot in bots)
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
        # One snapshot: the page's source messages are read together once, for both checks each copied note makes.
        with store.read_transaction() as c:
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
            H.prime_messages(c, [r["message_id"] for r in rows])
            levels = note_levels(c, who, rows)
            return {"notes": [note_view(r, c) for r in rows if note_visible(c, who, r, levels)]}

    @app.post("/api/v2/notes/{nid}/cancel")
    def cancel_note(request: Request, nid: str, body: M.Empty):
        def work(c):
            who = request.state.identity
            row = H.note(c, nid)
            if not row or not note_visible(c, who, row):
                raise Problem("not_found", "Note not found", 404)
            return {"note": note_view(H.note_cancel(c, who.actor, nid, human=who.role in ("human", "owner")), c)}

        return mutate(request, body, work)

    @app.get(
        "/api/v2/tasks/labels",
        **api_v2("Tasks", "listTaskLabels", "Labels in use (ETag: If-None-Match answers 304 when unchanged)"),
    )
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


# A tag on a list row: what a chip shows (its label, metadata) and who owns its area. The task's own read and
# GET /api/v2/tags/{id} carry the rest (its notes, writers, template and dates), the same for every task it is on.
LIST_TAG_FIELDS = ("id", "key", "label", "kind", "owner", "metadata")


def readers(auth):

    # The columns of a task's row that are the server's own, never in a view. `prefix` is read as the task's label
    # (hubdb.ticket_refs); a list of 500 rows carries no copy of it. `body` is the description tasks no longer have:
    # what was asked is the first message on the task's chat.
    SERVER_COLUMNS = ("checkpoint_state", "work_revision", "occurrence_step", "rule_due_id", "prefix", "body")

    def plain_view(row, c=None):
        """A task's plain fields, as every view of it starts: the row, without the server's own columns."""
        value = {
            "id": row["id"],
            "short_id": row["id"][:8],
            **{k: v for k, v in row.items() if k not in SERVER_COLUMNS},
            "acceptance_criteria": json.loads(row.get("acceptance") or "[]"),
            "labels": H.task_labels(row),
            "lane": row.get("lane") or "company",
            "came_from": row.get("came_from"),
            "private": H.task_private(c, row),
            "cover": None,
        }
        value.pop("acceptance", None)
        value["readers"] = H.task_readers(row) if value["private"] else []
        if not value.get("occurrence"):
            value.pop("occurrence", None)  # only a due-date routine's task has one: a board of 500 carries none
        return value

    def task_view(row, c=None, visible_sql="1", who=None):
        """The task as JSON, full (docs/dev/pipelines-v3-cutover.md, "The task as JSON"): its plain fields with the
        engine's (H.task_view: where it is, whose turn it is, what it takes to leave its stage, for this reader) laid
        over them. Without a connection, the plain fields alone."""
        if c is not None:
            H.hydrate_task_tags(c, [row])
        value = plain_view(row, c)
        if c is None:
            return value
        # What it reads (the tasks it filed, its stage inputs) as the request reads (task_privacy.actor: a lease of the
        # shared Assistant by its run's scope); what it may do stays the acting identity's (`who`).
        principals = privacy.actor(who) if who is not None else None
        viewer = principals[-1] if isinstance(principals, tuple) else principals
        # Another task it names is shown under the reader's whole rule, the one a direct read of that task uses.
        rule = auth.task_sql(c, who) if who is not None and visible_sql == "1" else visible_sql
        from . import waiting_on

        turn_for = waiting_on.turn_viewer(who)
        value.update(H.task_view(c, row["id"], viewer, rule, inputs=True, turn_for=turn_for) or {"stage_since": None})
        # The task above it by id only to a reader of that task: anyone else sees "a private task" under relations.
        parent = TR.parent_of(c, row["id"])
        value["parent_id"] = (
            parent
            if parent and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({rule})", (parent,)).fetchone()
            else None
        )
        value["relations"] = TR.grouped(c, [row["id"]], visible_sql)[row["id"]]
        value["roles"] = TRo.roles_of(c, row["id"])
        from .roadmaps import stats, EMPTY_STATS

        value.update({**EMPTY_STATS, **stats(c, [row["id"]]).get(row["id"], {})})
        routine = c.execute("SELECT schedule_id FROM schedule_occurrences WHERE task_id=?", (row["id"],)).fetchone()
        if routine:
            value.update(routine_id=routine["schedule_id"])
        value["origin_actor"] = H.task_origin(c, row)
        value["request"] = H.task_request(c, row["id"])
        if filed := SRC.filers(c, [row["id"]]).get(row["id"]):
            value["filer"] = filed
        from . import email_sending

        if sent := email_sending.for_tasks(c, [row["id"]]).get(row["id"]):
            value["email_send"] = sent
        if who is not None and visible_sql != "1":
            value["from_person"] = views.person_origin(c, row, visible_sql, who.actor)
        from . import task_reads

        value["cover"] = task_reads.covers(c, [row["id"]]).get(row["id"])
        from .blobs import brief

        value["attachments"] = [
            brief(r)
            for r in c.execute(
                "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?", (row["id"],)
            )
        ]
        value["links"] = H.task_links(c, row["id"])
        value["children_summary"] = H.children_summary(c, row["id"], visible_sql)
        value["pr_state"] = H.pr_state(value["links"])
        counts = c.execute(
            "SELECT COUNT(*), SUM(status IN ('done','closed')) FROM tasks WHERE id IN ("
            + TR.CHILD_IDS
            + ") AND ("
            + visible_sql
            + ")",
            (row["id"],),
        ).fetchone()
        value["parts"] = {"total": counts[0] or 0, "done": counts[1] or 0}
        from . import risk

        matter = risk.matter_of(c, who, row["id"])  # its facts as a matter, when it carries a risk's
        if matter:
            value["matter"] = matter
        if who is not None:
            from .files import readable_earlier, readable_page_refs

            # An OK's page from an earlier stage may live on another task: the file's own read check decides it. Pages
            # are read as the request reads (auth.run_viewer: a lease of the shared Assistant as its run's person).
            reader = auth.run_viewer(c, who)
            for entry in value.get("to_leave") or []:
                readable_earlier(c, auth, reader, entry)
            value = readable_page_refs(c, auth, reader, value)
            value = teammate_access.Known(c, who, auth).task(value)  # people the reader may not see
        return value

    def list_tags(c, who, rows):
        """{tag id: what a list row shows of it}: LIST_TAG_FIELDS, or the whole tag's placeholder when a page it names
        is one the reader may not open. Each distinct tag on the page is checked whole, in one pass, before it is cut
        down, so a denied page in its notes still hides it (files.readable_page_refs, as a full read does)."""
        from .files import readable_page_refs

        distinct = {tag["id"]: tag for row in rows for tag in row.get("tags") or ()}
        checked = cast(
            "list[dict[str, Any]]", readable_page_refs(c, auth, auth.run_viewer(c, who), list(distinct.values()))
        )
        return {
            tag_id: shown if shown.get("removed") is True else {k: shown[k] for k in LIST_TAG_FIELDS if k in shown}
            for tag_id, shown in zip(distinct, checked, strict=True)
        }

    def task_views(rows, c, who, visible_sql="1"):
        """One task-list page, brief (docs/dev/pipelines-v3-cutover.md, "The task as JSON"): each row's plain fields
        and the engine's brief ones (turn, late, snoozed), with a fixed number of reads for the whole page. `rows`
        were read on `c` under `visible_sql`, the reader's rule, as every list does."""
        if not rows:
            return []
        H.hydrate_task_tags(c, [row for row in rows if "tags" not in row])  # H.tasks' rows have theirs
        ids = [row["id"] for row in rows]
        from . import task_engine
        from .roadmaps import stats, EMPTY_STATS

        task_engine.prime_places(c, rows)  # each row's stage and pipeline, read together for the waits below

        requester_stats = stats(c, ids)
        for row in rows:
            row.update({**EMPTY_STATS, **requester_stats.get(row["id"], {})})
        marks = ",".join("?" * len(ids))
        from . import task_reads

        covers = task_reads.covers(c, ids)
        # From the page's ids, with visibility checked per child: an `IN (SELECT id FROM tasks WHERE ...)`
        # list made SQLite probe every visible task against the page (1.5 s at 30k tasks).
        parts = {
            row["parent_id"]: {"total": row["total"] or 0, "done": row["done"] or 0}
            for row in c.execute(
                f"SELECT r.to_task parent_id,COUNT(*) total,SUM(t.status IN ('done','closed')) done "
                f"FROM task_relations r INDEXED BY task_relations_to JOIN tasks t ON t.id=r.from_task "
                f"WHERE r.to_task IN ({marks}) AND r.kind='parent' "
                f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.from_task AND ({visible_sql})) "
                f"GROUP BY r.to_task",
                ids,
            )
        }
        # The task above each row, by id only where this reader reads it (the rows' own rule).
        parents = {
            r[0]: r[1]
            for r in c.execute(
                f"SELECT r.from_task, r.to_task FROM task_relations r WHERE r.kind='parent' AND r.from_task IN ({marks}) "
                f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.to_task AND ({visible_sql}))",
                ids,
            )
        }
        relations = TR.grouped(c, ids, visible_sql)
        roles = TRo.grouped(c, ids)
        routines_by_task = {
            row["task_id"]: row["schedule_id"]
            for row in c.execute(
                f"SELECT task_id,schedule_id FROM schedule_occurrences WHERE task_id IN ({marks})", ids
            )
        }

        links = {tid: [] for tid in ids}
        for link in c.execute(
            f"SELECT id,task_id,kind,url,title,state,added_by,created_at,pr_sha,pr_merged_at,repo,number,branch,"
            f"computer_id,path,checks,mergeable,review_state,pending_comments,updated_at "
            f"FROM task_links WHERE task_id IN ({marks}) ORDER BY created_at",
            ids,
        ):
            # Not a link's record, its empty columns or its own write time (a computer's heartbeat rewrites that every
            # minute): the task's read and its /links have them, and every open tab gets each list change.
            links[link["task_id"]].append(
                {k: v for k, v in dict(link).items() if v is not None and k not in ("detail", "pr_sha", "updated_at")}
            )

        attachments = {tid: [] for tid in ids}
        # Each without its URL, /api/v2/files/<id> (the task's own read has it): a task can carry hundreds.
        for asset in c.execute(
            f"SELECT a.task_id,b.id,b.name,b.size,b.content_type FROM task_assets a JOIN blobs b ON b.id=a.blob_id "
            f"WHERE a.task_id IN ({marks})",
            ids,
        ):
            attachments[asset["task_id"]].append({k: asset[k] for k in ("id", "name", "size", "content_type")})

        origins = {}
        for event in c.execute(
            f"SELECT target,detail FROM events WHERE actor=? AND action='task.origin' "
            f"AND target IN ({marks}) ORDER BY target,created_at DESC",
            (H.TICO_ACTOR, *ids),
        ):
            if event["target"] in origins:
                continue
            origin = (H._json(event["detail"], {}) or {}).get("bot")
            origins[event["target"]] = origin if H.is_bot(origin) else None

        filed = SRC.filers(c, ids)
        from . import email_sending

        email_sends = email_sending.for_tasks(c, ids)
        summaries = H.children_summaries(c, ids, visible_sql)
        # The rows each row's from_person reads, read for the whole page first; each row is still decided one at a
        # time by the same code below.
        views.prime_list_origins(c, visible_sql, rows)
        from . import waiting_on

        task_reads.brief(c, rows, visible_sql, waiting_on.turn_viewer(who))
        tags = list_tags(c, who, rows)
        result = []
        for row in rows:
            value = plain_view(row, c)
            value.pop("checkpoint", None)  # up to a page of text per task; the full task view carries it
            value["tags"] = [dict(tags[tag["id"]]) for tag in value["tags"]]
            value.update(
                {
                    "parent_id": parents.get(row["id"]),
                    "relations": relations[row["id"]],
                    "roles": roles[row["id"]],
                    "parts": parts.get(row["id"], {"total": 0, "done": 0}),
                    "links": links[row["id"]],
                    "children_summary": summaries[row["id"]],
                    "pr_state": H.pr_state(links[row["id"]]),
                    "attachments": attachments[row["id"]],
                    "cover": covers.get(row["id"]),
                    "origin_actor": origins.get(row["id"]),
                    "from_person": views.person_origin(c, row, visible_sql, who.actor),
                }
            )
            if row["id"] in routines_by_task:
                value["routine_id"] = routines_by_task[row["id"]]
            if row["id"] in filed:
                value["filer"] = filed[row["id"]]
            if row["id"] in email_sends:
                value["email_send"] = email_sends[row["id"]]
            result.append(value)
        from .files import readable_page_refs

        result = readable_page_refs(c, auth, auth.run_viewer(c, who), result)
        return teammate_access.Known(c, who, auth).tasks(result)  # people the reader may not see

    def mover(c, who):
        """Whether this person may move any task: the owner, or a person on a mover team."""
        return who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)

    def visible_tasks(
        c,
        who,
        owner=None,
        requester=None,
        status=None,
        lane=None,
        label=None,
        limit=500,
        offset=0,
        order="queue",
        visible_sql=None,
        narrow=None,
        keyset=False,
        per_stage=False,
        **more,
    ):
        """A page of the tasks `who` may read. `narrow` is a further WHERE fragment over the task's own columns (a
        list's pipeline, stage or pushed-past filter, task_list_routes.narrowing): it picks the rows only, while the
        rows are still read with the reader's own visibility. With `keyset`, the second value is the last row's sort
        key when there is more (the next page's `after`), not an offset. With `per_stage`, the answer is instead
        {stage_id: count} of every task the same filters select, across all pages, from one grouped read
        (H.tasks per_stage): a list's counts go through here so they never select differently from its rows."""
        auth.domain(who)
        visible_sql = visible_sql or auth.task_sql(c, who)
        selection: dict[str, Any] = dict(
            owner=owner,
            requester=requester,
            status=status,
            lane=lane,
            label=label,
            order=order,
            visible=f"({visible_sql}) AND ({narrow})" if narrow else visible_sql,
            **more,
        )
        if per_stage:
            return H.tasks(c, per_stage=True, **selection)
        rows = H.tasks(c, limit=limit + 1, offset=offset, **selection)
        page, has_more = rows[:limit], len(rows) > limit
        if keyset:
            return task_views(page, c, who, visible_sql), H.task_sort_key(page[-1], order) if has_more else None
        return task_views(page, c, who, visible_sql), offset + limit if has_more else None

    return mover, task_view, task_views, visible_tasks
