"""Deleted task, restore, links, relations, and the status route."""

from .store import H
from . import models as M
from .store import Problem, refusal_problem
from fastapi import Request
from . import task_relations as TR
from . import task_privacy as privacy
from . import reach
from . import task_caps
from . import task_merge as TM
from .api_contract import api_v2
from .api_answers.tasks import PullRequestList, TaskMergeChecks, TaskMergeResult, TaskResult


MAX_IDLE_HOURS = 8760  # a year: every cutoff it makes stays a date


def _pr_where(visible_sql, repo, state, idle_hours):
    where, args = ["l.kind='pr'"], []
    if repo:
        where.append("(lower(l.repo)=lower(?) OR lower(substr(l.repo, instr(l.repo, '/') + 1))=lower(?))")
        args += [repo.strip(), repo.strip()]
    if state:
        where.append("coalesce(l.state,'')=?")
        args.append(state.strip().lower())
    if idle_hours is not None:
        where.append("coalesce(l.state,'') IN ('open','draft') AND coalesce(l.activity_at, l.created_at)<=?")
        args.append(H.shift(H.now(), hours=-idle_hours))
    where.append(f"l.task_id IN (SELECT id FROM tasks WHERE ({visible_sql}))")
    return " AND ".join(where), args


def pr_rows(c, visible_sql, repo=None, state=None, idle_hours=None, limit=100, offset=0):
    """Pull request links on the tasks `visible_sql` (Auth.task_sql) admits, oldest activity first: `GET /api/v2/prs`
    and an automation's Only if (backend/automation_only_if.py) read the same rows. `repo` is owner/name or a name."""
    where, args = _pr_where(visible_sql, repo, state, idle_hours)
    rows = c.execute(
        "SELECT l.id, l.task_id, l.url, l.title, l.state, l.repo, l.number, l.created_at, l.activity_at "
        f"FROM task_links l WHERE {where} ORDER BY coalesce(l.activity_at, l.created_at), l.id LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows]


def pr_count(c, visible_sql, repo=None, state=None, idle_hours=None):
    """How many rows `pr_rows` selects, in one COUNT."""
    where, args = _pr_where(visible_sql, repo, state, idle_hours)
    return c.execute(f"SELECT count(*) FROM task_links l WHERE {where}", args).fetchone()[0]


def register(app, auth, mover, mutate, person_only, settings, store, task_view):
    @app.get(
        "/api/v2/deleted-tasks",
        **api_v2("Tasks", "listDeletedTasks", "Deleted tasks this person may restore, newest first"),
    )
    def deleted_tasks(request: Request):
        """Deleted tasks, newest first: all of them for someone who may move any task, otherwise the
        ones this person deleted or asked for."""
        who = request.state.identity
        person_only(who, "sees deleted tasks")
        from .task_delete import trash

        with store.read() as c:
            return {"tasks": trash(c, None if mover(c, who) else who.actor, audience=auth.task_sql(c, who))}

    @app.post(
        "/api/v2/tasks/{tid}/restore",
        **api_v2(
            "Tasks",
            "restoreTask",
            "Put a deleted task back with its conversation, comments, links and number: whoever deleted it, its requester or a mover, signed in as themselves",
        ),
    )
    def task_restore(request: Request, tid: str, body: M.Empty):
        """Put a deleted task back with its conversation, comments, links and number: whoever deleted
        it, its requester, or anyone who may move any task."""
        who = request.state.identity
        person_only(who, "restores a task")

        def work(c):
            from .task_delete import restore_tasks, trashed

            row = trashed(c, tid, audience=auth.task_sql(c, who))
            if not row or row["deleted_by"] != who.actor and row["requester_id"] != who.actor and not mover(c, who):
                raise Problem("not_found", "No deleted task " + str(tid) + " you may restore", 404)
            report = restore_tasks(c, [row["task_id"]], actor=who.actor)
            if report.get("conflict"):
                raise Problem("conflict", "This task cannot go back: something newer has taken its place", 409)
            if report.get("not_shared"):
                held = report["not_shared"][row["task_id"]]
                raise Problem("task_not_shared", f"{held['cause']}. {held['fix']}. It stays in Deleted tasks", 409)
            if report.get("unplaced"):
                fields = ", ".join(
                    f"{table}.{key}" for table, keys in report["unplaced"][row["task_id"]].items() for key in keys
                )
                raise Problem(
                    "unplaced",
                    "This task was deleted before an upgrade and holds data this version has no "
                    f"place for ({fields}). It stays in Deleted tasks",
                    409,
                )
            return {
                "restored": row["task_id"],
                "unlinked": report["unlinked"].get(row["task_id"], []),
                "skipped": report["skipped"],
            }

        return mutate(request, body, work)

    @app.get(
        "/api/v2/prs",
        **api_v2(
            "Tasks",
            "listPullRequests",
            "Pull requests linked to tasks the caller can read, oldest activity first",
            PullRequestList,
            parameters={
                "repo": "owner/name, or a name alone: that repository's pull requests",
                "state": "open, draft, merged, closed or shipped",
                "idle_hours": "Open and draft pull requests with no activity for this many hours",
            },
        ),
    )
    def pull_requests(
        request: Request,
        repo: str | None = None,
        state: str | None = None,
        idle_hours: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """Pull request links on tasks this reader may read. A query parameter that is not one of these answers 422,
        so a misspelled filter never lists every pull request."""
        who = request.state.identity
        auth.domain(who)
        unknown = sorted(set(request.query_params.keys()) - {"repo", "state", "idle_hours", "limit", "offset"})
        if unknown:
            raise Problem("filter", f"Unknown filter: {', '.join(unknown)}. Filters: idle_hours, repo, state.", 422)
        if limit < 1 or limit > 500:
            raise Problem("limit", "limit is between 1 and 500", 422)
        if offset < 0:
            raise Problem("filter", "offset is 0 or more", 422)
        if idle_hours is not None and not 0 <= idle_hours <= MAX_IDLE_HOURS:
            raise Problem("filter", f"idle_hours is from 0 to {MAX_IDLE_HOURS}", 422)
        with store.read() as c:
            rows = pr_rows(c, auth.task_sql(c, who), repo, state, idle_hours, limit + 1, offset)
        return {"prs": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    @app.post("/api/v2/tasks/{tid}/links")
    def task_links(request: Request, tid: str, body: M.TaskLink):
        who = request.state.identity

        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            if who.via == "botops" and not who.confirmed and (body.remove or body.url):
                # Agent Ops acting for a person adds or removes a link only on that person's own tap (a Confirm card).
                what = (
                    ("Remove a link from " if body.remove else "Add a link to ")
                    + "“"
                    + H.task(c, task_id)["title"]
                    + "”"
                )
                return app.state.agent_ops_propose(
                    c, who, "POST", f"/api/v2/tasks/{tid}/links", body.model_dump(exclude_none=True), what
                )
            if body.remove:
                H.task_unlink(c, who.actor, task_id, body.remove, mover=mover(c, who))
            elif body.url:
                H.task_link(
                    c,
                    who.actor,
                    task_id,
                    body.url,
                    body.title,
                    kind=body.kind,
                    mover=mover(c, who),
                    wake_once=body.wake_once,
                    release_repo=settings.release_repo,
                    connection_id=body.connection_id or "",
                )
            else:
                raise Problem("kind", "Send a url to add or remove with a link id", 422)
            return {"links": H.task_links(c, task_id)}

        # A retry of a link sent before `connection_id` existed hashed the body without it.
        legacy = body.model_dump(exclude={"connection_id"}) if body.connection_id is None else None
        return mutate(request, body, work, retry_body=lambda _c, _result: legacy)

    @app.post(
        "/api/v2/tasks/{tid}/relations",
        **api_v2(
            "Tasks",
            "relateTask",
            "Relate another task to this one ({task, kind}: related, duplicate_of, follow_up, after, depends_on), or take the relation off with remove. A task waits on after, in Merge (its PRs are not queued until task merged), and on depends_on, in any stage (Blocked by task until it ends; with until: released, until production runs a release that holds task's merged PRs)",
            TaskResult,
        ),
    )
    def task_relation(request: Request, tid: str, body: M.TaskRelation):
        """Relate another task to this one (`{id} kind {task}`), or take the relation off with `remove`."""
        who = request.state.identity

        def work(c):
            task_id = auth.resolve_task(c, who, tid)
            other_id = auth.resolve_task(c, who, body.task)
            auth.task(c, who, task_id)
            auth.task(c, who, other_id)
            changed = TR.relate(
                c,
                who.actor,
                task_id,
                other_id,
                body.kind,
                remove=body.remove,
                mover=mover(c, who) or None,
                until=body.until,
            )
            # The stored direction in words, from both ends (Dev 256): a reversed write shows in the reply.
            return {
                "readback": TR.readback(c, task_id, other_id, body.kind, remove=body.remove, changed=changed),
                "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who),
            }

        return mutate(request, body, work)

    # ------------------------------------------------------------------ drop a task on another (task_merge)
    def _drop_pair(c, who, tid, other):
        task_id = auth.resolve_task(c, who, tid)
        other_id = auth.resolve_task(c, who, other)
        auth.task(c, who, task_id)
        auth.task(c, who, other_id)
        return task_id, other_id

    @app.get(
        "/api/v2/tasks/{tid}/merge",
        **api_v2(
            "Tasks",
            "taskMergeChecks",
            "Before dropping this task on ?into=: both tasks, why each choice (subtask, merge) cannot be done or null, and what a merge would move. People only",
            TaskMergeChecks,
        ),
    )
    def task_merge_checks(request: Request, tid: str, into: str):
        """Before the board's dialog confirms a drop of this task on `into`: both tasks, why each choice cannot be
        done (null when it can) and what a merge would move. People only."""
        who = request.state.identity
        person_only(who, "merges tasks")
        with store.read() as c:
            task_id, into_id = _drop_pair(c, who, tid, into)
            try:
                return TM.checks(c, who.actor, task_id, into_id, mover=mover(c, who) or None)
            except H.Refused as exc:
                raise refusal_problem(exc) from None

    @app.post(
        "/api/v2/tasks/{tid}/merge",
        **api_v2(
            "Tasks",
            "mergeTask",
            "Merge this task into another as its duplicate ({into, owner?}), in one write: the other gains its details and open subtasks, and this one is Closed and linked as a duplicate. People only",
            TaskMergeResult,
        ),
    )
    def task_merge(request: Request, tid: str, body: M.TaskMerge):
        """Merge this task into `into` as its duplicate, in one write: `into` gains its details, its open subtasks
        move under `into`, and this task is Closed and linked as a duplicate. `owner` (either task's owner) keeps
        `into`, through the usual hand-over checks. People only."""
        who = request.state.identity
        person_only(who, "merges tasks")

        def work(c):
            task_id, into_id = _drop_pair(c, who, tid, body.into)

            def reassign(c, kept_id, owner):
                # The checks POST /tasks/{id}/owner makes: someone the person may give work to and reach, within
                # their task cap.
                target = auth.target(c, who, owner, need="write")
                auth.require_bot_contact(c, who, target, task_id=kept_id, kind="task")
                reach.check(c, who, target, "task", task_id=kept_id)
                task_caps.check(c, who.actor, target)
                H.task_reassign(c, who.actor, kept_id, target, "Kept the merged task", mover=mover(c, who) or None)
                task_caps.record(c, kept_id, who.actor, target)

            owner = H.resolve_actor(c, body.owner) if body.owner else None
            if body.owner and not owner:
                raise Problem("not_found", f"No one called {body.owner} owns either task", 422)
            done = TM.merge(c, who.actor, task_id, into_id, owner=owner, reassign=reassign, mover=mover(c, who) or None)
            view = auth.task_sql(c, who)
            return {
                "task": task_view(done["task"], c, visible_sql=view, who=who),
                "closed": task_view(done["closed"], c, visible_sql=view, who=who),
                "note": done["note"],
            }

        return mutate(request, body, work)

    @app.post(
        "/api/v2/tasks/{tid}/parent",
        **api_v2(
            "Tasks",
            "setTaskParent",
            "Put this task under another as its subtask ({parent}); both keep their owners and stages. People only",
            TaskResult,
        ),
    )
    def task_parent(request: Request, tid: str, body: M.TaskParent):
        """Put this task under `parent` as its subtask. Both keep their owners, stages and pipelines; the parent waits
        on it. People only."""
        who = request.state.identity
        person_only(who, "sets a task's parent")

        def work(c):
            task_id, parent_id = _drop_pair(c, who, tid, body.parent)
            row = TM.set_parent(c, who.actor, task_id, parent_id, mover=mover(c, who) or None)
            return {"task": task_view(row, c, visible_sql=auth.task_sql(c, who), who=who)}

        return mutate(request, body, work)

    @app.get("/api/v2/status")
    def statuses(request: Request, bot: str | None = None, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read_transaction() as c:
            from . import usage_limits

            default = usage_limits.company(c)

            from .events import status_line  # the same line live events send

            if bot:
                bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
                auth.target(c, who, bot, need="read")
                from .views import since_time

                inputs = privacy.status_inputs(c, [bot])[bot]
                return {
                    "bot": bot,
                    "status": status_line(c, who, bot, default, inputs, auth=auth),
                    "history": [
                        privacy.status(c, who, r, **inputs) for r in H.status_history(c, bot, since=since_time(since))
                    ],
                }
            from .views import updating

            readable = auth.bot_accesses(c, who)
            # A first-turn waiter may not have a bot_status row until /started. Include live candidates;
            # status_line keeps only an actual wait when no stored status exists.
            statuses = [
                s
                for s in c.execute(
                    "SELECT bot FROM bot_status UNION SELECT bot_id FROM attempts "
                    "WHERE state IN ('leased','running') ORDER BY bot"
                )
                if readable.get(s["bot"], auth.FULL)["read"]
            ]
            inputs = privacy.status_inputs(c, [s["bot"] for s in statuses])
            from .events import prime_status_lines

            prime_status_lines(c, [s["bot"] for s in statuses])
            return {
                "bots": [
                    line
                    for s in statuses
                    if (line := status_line(c, who, s["bot"], default, inputs[s["bot"]], auth=auth)) is not None
                ],
                # Tico updating itself, shown beside the status line
                "updating": updating(c),
            }
