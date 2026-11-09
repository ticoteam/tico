"""Bulk task routes: bulk select and change, and task run-now."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from pydantic import ValidationError
from . import ask_ingest
from . import pipeline_rights
import json
from . import org_rule_check
from . import task_privacy as privacy
from .store import refused


def register(
    app,
    auth,
    handoff_preflight,
    mutate,
    review_preflight,
    rule_update_check,
    rule_update_text,
    saved_retry,
    settings,
    store,
    task_filters,
    task_idents,
    task_update_one,
    task_view,
):
    # One bulk change holds the write lock for every task it changes, and answers with a line for each:
    # past this many matching tasks it is refused whole, and the caller narrows the filter.
    BULK_CAP = 200

    def bulk_select(c, who, f):
        """The ids of the tasks `f` (M.TaskFilter) selects that `who` may read, in the list's order. A filter that
        names nothing, or more than BULK_CAP matches, is refused."""
        named = (
            f.owner,
            f.requester,
            f.status not in (None, "", "all") or None,
            f.label,
            f.type,
            f.step,
            f.number,
            f.updated_since,
            f.member,
            f.ids,
        )
        if not any(named):
            raise Problem("filter", "Name at least one filter: a bulk change never applies to every task", 422)
        owner, requester, filters = task_filters(
            c,
            owner=f.owner,
            requester=f.requester,
            status=f.status,
            lane=f.lane,
            label=f.label,
            type=f.type,
            step=f.step,
            number=f.number,
            updated_since=f.updated_since,
            member=f.member,
            role=f.role,
            snoozed=f.snoozed,
            by_id=f.ids is not None or f.number is not None,
            exact=True,
            viewer=who.task_actor or who.actor,
        )
        # The members-only rule too (pipeline_rights.list_sql): a non-member selects none of a hidden pipeline's tickets.
        visible_sql = pipeline_rights.list_sql(c, who.task_actor or who.actor, auth.task_sql(c, who))
        if f.ids is not None:
            _, found = task_idents(c, who, ",".join(f.ids), visible_sql)
            filters["ids"] = [tid for tid in found.values() if tid]
        rows = H.tasks(c, owner=owner, requester=requester, limit=BULK_CAP + 1, visible=visible_sql, **filters)
        if len(rows) > BULK_CAP:
            raise Problem(
                "too_many",
                f"More than {BULK_CAP} tasks match; a bulk change takes at most {BULK_CAP}: narrow the filter",
                422,
                extra={"cap": BULK_CAP},
            )
        return [row["id"] for row in rows]

    def bulk_change(c, row, body):
        """The single update (M.TaskUpdate) `body` (M.TaskBulk) makes to the task `row`, or None when the task already
        is that way."""
        base = {"version": row["version"], **({"note": body.note} if body.note else {})}
        if body.action == "close":
            return None if row["status"] == "closed" else M.TaskUpdate(**base, close=True)
        if body.action == "status":
            return None if row["status"] == body.status else M.TaskUpdate(**base, status=body.status)
        if body.action == "owner":
            return None if H.resolve_actor(c, body.owner) == row["owner_id"] else M.TaskUpdate(**base, owner=body.owner)
        if body.action == "step":
            # By id or name within the task's own type, as a single update reads it.
            steps = (H.type_get(c, row.get("type_id") or H.GENERAL_TYPE) or {}).get("steps") or []
            target = next((s for s in steps if s["id"] == body.step), None) or next(
                (s for s in steps if s["name"] == body.step), None
            )
            return None if target and target["id"] == row.get("step_id") else M.TaskUpdate(**base, step=body.step)
        labels = H.task_labels(row)
        add = [key for key in dict.fromkeys(str(t).strip().lower() for t in body.add) if key not in labels]
        drop = {str(t).strip().lower() for t in body.remove}
        changed = [key for key in labels if key not in drop] + add
        return None if changed == labels else M.TaskUpdate(**base, labels=changed)

    @app.post("/api/v2/task-bulk")
    def task_bulk(request: Request, body: M.TaskBulk):
        """One change to every task a filter selects (the filters of `GET /api/v2/tasks`), each made as its single
        update would be: a task the caller may not change is skipped with the reason, the rest still change. A dry
        run makes every change and rolls all of them back, so its counts are the real ones."""
        caller = request.state.identity
        auth.domain(caller)
        with store.read() as c:
            chosen = bulk_select(c, caller, body.filter)
        # A move into a review step reads each task's pull requests first, outside the write lock (review_preflight).
        snapshots, unchecked, ingests = {}, {}, {}
        # The org rule check, each task's change on its own (a few calls at a time): a held task is skipped, the rest
        # still change.
        rule_checks = {}
        if (
            (body.note or body.action == "owner")
            and not body.dry_run
            and org_rule_check.author_of(caller, settings.assistant_bot)
        ):
            with store.read() as c:
                pairs = []
                for tid in chosen:
                    r = H.task(c, tid)
                    try:
                        change = bulk_change(c, r, body) if r else None
                    except ValidationError:
                        change = None
                    if change is not None and rule_update_text(change, r):
                        pairs.append((r, change))
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8) as pool:
                for (r, _), verdict in zip(
                    pairs, pool.map(lambda p: rule_update_check(caller, p[0]["id"], p[1], p[0]), pairs), strict=False
                ):
                    if verdict:
                        rule_checks[r["id"]] = {**verdict, "operation": "bulk"}
        if (
            body.action == "owner"
            and body.owner
            and not body.dry_run
            and ask_ingest.gated(caller, settings.assistant_bot)
            and not saved_retry(request, caller)
        ):
            # A bot handing its own tasks to a person: each is judged by ask ingest first, a few calls at a time.
            with store.read() as c:
                mine = [
                    r
                    for r in (H.task(c, tid) for tid in chosen)
                    if r and r["owner_id"] == caller.actor and not org_rule_check.holds(rule_checks.get(tid))
                ]
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8) as pool:
                for r, verdict in zip(
                    mine, pool.map(lambda r: handoff_preflight(caller, r, body.owner, body.note), mine), strict=False
                ):
                    if verdict:
                        ingests[r["id"]] = verdict
        if body.action in ("step", "status"):
            for tid in chosen:
                try:
                    snapshots[tid] = review_preflight(caller, tid, step=body.step, status=body.status)
                except Problem as exc:
                    unchecked[tid] = exc

        def work(c):
            # Every task this call skips keeps its refusal on record, and the call counts once toward quarantine.
            token = H.BULK_CALL.set(H.new_id())
            try:
                return bulk_work(c)
            finally:
                H.BULK_CALL.reset(token)

        def bulk_work(c):
            # Chosen again under the write lock, so each task is judged as it is now.
            ids = bulk_select(c, caller, body.filter)
            changed, unchanged, skipped, judged = [], [], [], {}
            # Work queued to run after commit (a webhook's doorbell) goes with the rows a rollback takes back.
            queued = len(c.after_commit)
            if body.dry_run:
                c.execute("SAVEPOINT bulk_dry_run")
            for tid in ids:
                row = H.task(c, tid)
                try:
                    change = bulk_change(c, row, body)
                except ValidationError as exc:
                    skipped.append((tid, "invalid", exc.errors()[0]["msg"]))
                    continue
                if change is None:
                    unchanged.append(tid)
                    continue
                c.execute("SAVEPOINT bulk_task")
                before = len(c.after_commit)
                try:
                    if tid in unchecked or tid not in snapshots and body.action in ("step", "status"):
                        raise unchecked.get(tid) or Problem(
                            "changed", "Task changed during the bulk change; try again", 409
                        )
                    done = task_update_one(
                        c,
                        caller,
                        tid,
                        change,
                        snapshots.get(tid),
                        view=False,
                        ingest=None
                        if org_rule_check.holds(rule_checks.get(tid))
                        else [ingests[tid]]
                        if tid in ingests
                        else None,
                        rule=rule_checks.get(tid),
                    )
                except H.Refused as exc:
                    # Audited and answered as the single update's refusal would be; the other tasks go on.
                    del c.after_commit[before:]
                    problem = refused(c, caller, exc, savepoint="bulk_task")
                    skipped.append((tid, problem.code, problem.detail))
                except Problem as exc:
                    c.execute("ROLLBACK TO bulk_task")
                    c.execute("RELEASE bulk_task")
                    del c.after_commit[before:]
                    if isinstance(exc, ask_ingest.AskRefused):
                        ask_ingest.record_refusal(c, exc)
                    skipped.append((tid, exc.code, exc.detail))
                else:
                    c.execute("RELEASE bulk_task")
                    if done.get("held"):
                        skipped.append((tid, "held", done["held"]["text"]))  # its hold stays; the task does not change
                        continue
                    changed.append(tid)
                    if done.get("ingest"):
                        judged[tid] = done["ingest"]  # what ask ingest did with this handoff, and why
            # A handoff judged for a task this change did not land names its call: refused, or unchanged.
            held_tids, refused_tids = {t for t, code, *_ in skipped if code == "held"}, {t for t, *_ in skipped}
            for tid, verdict in ingests.items():
                if (
                    tid not in changed
                    and tid not in held_tids
                    and not any(t == tid and code == "ask_refused" for t, code, *_ in skipped)
                ):
                    ask_ingest.skipped(c, verdict, "refused" if tid in refused_tids else "not_applied")
            if body.dry_run:
                c.execute("ROLLBACK TO bulk_dry_run")
                c.execute("RELEASE bulk_dry_run")
                del c.after_commit[queued:]
            # Each task the caller can still read is named; one a change took out of their sight is only counted.
            visible = pipeline_rights.list_sql(c, caller.task_actor or caller.actor, auth.task_sql(c, caller))
            readable = {
                r[0]
                for r in c.execute(
                    f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible})",
                    (json.dumps(ids),),
                )
            }

            def line(tid, code=None, reason=None):
                row = H.task(c, tid)
                return {
                    "id": tid,
                    "number": row.get("number"),
                    "title": row["title"],
                    **({"code": code, "reason": reason} if code else {}),
                    **({"ingest": judged[tid]} if tid in judged else {}),
                }

            return {
                "dry_run": body.dry_run,
                "matched": len(ids),
                "cap": BULK_CAP,
                "changed": len(changed),
                "unchanged": len(unchanged),
                "skipped": len(skipped),
                "tasks": {
                    "changed": [line(t) for t in changed if t in readable],
                    "skipped": [line(t, code, reason) for t, code, reason in skipped if t in readable],
                },
            }

        return ask_ingest.judged(store, list(ingests.values()), lambda: mutate(request, body, work))

    @app.post("/api/v2/tasks/{tid}/run-now")
    def task_run_now(request: Request, tid: str, body: M.Empty):
        """Wake the task's bot for this task now, as the task (not a chat). A person, the task's
        requester, or the fleet maintainer's stuck-task sweep (never on a private bot's task)."""

        def work(c):
            who = request.state.identity
            maintainer = who.actor == H.bot_actor(H.FLEET_MAINTAINER)
            task_id = auth.resolve_task(c, who, tid, visible=auth.fleet_task_sql(c, who) if maintainer else None)
            if maintainer:
                row = H.task(c, task_id)
                if not row:
                    raise Problem("not_found", "Task not found", 404)
                if not privacy.task_readable(c, who, row):
                    raise Problem("not_found", "Task not found", 404)
                hidden = auth.unreadable_bots(c, who)
                if any(
                    str(a).startswith("bot:") and H.actor_id(a) in hidden
                    for a in (row["owner_id"], row["requester_id"])
                ):
                    raise Problem("forbidden", "This task involves a bot whose activity BotOps may not read", 403)
            else:
                row = auth.task(c, who, task_id)
                if (
                    who.role not in ("human", "owner")
                    and not H.task_represents(c, who.actor, row["requester_id"])
                    and not (who.actor != row["owner_id"] and H.task_represents(c, who.actor, row["owner_id"]))
                ):
                    raise Problem(
                        "forbidden",
                        "Only a person, the task's requester, a party's manager or BotOps starts it early",
                        403,
                    )
                # Running it wakes the bot: that is a request to it.
                if str(row["owner_id"]).startswith("bot:") and who.actor != row["owner_id"]:
                    auth.require_write(c, who, H.actor_id(row["owner_id"]))
            after, queued = H.task_run_now(c, who.actor, task_id)
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who), "queued": queued}

        return mutate(request, body, work)
