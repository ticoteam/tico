"""Bulk task routes: one change to many tasks (people only), and task run-now."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
import json
from . import task_privacy as privacy
from .store import refused
from . import org_rule_check, task_rule_gate


def register(app, auth, mover, mutate, store, task_filters, task_idents, task_view):
    # One bulk change holds the write lock for every task it changes, and answers with a line for each:
    # past this many matching tasks it is refused whole, and the caller narrows the filter.
    BULK_CAP = 200

    def bulk_select(c, who, f):
        """The ids of the tasks `f` (M.TaskFilter) selects that `who` may read, in the list's order. A filter that
        names nothing, or more than BULK_CAP matches, is refused."""
        named = (
            f.owner,
            f.exclude_owner,
            f.requester,
            f.status not in (None, "", "all") or None,
            f.label,
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
            number=f.number,
            updated_since=f.updated_since,
            member=f.member,
            role=f.role,
            snoozed=f.snoozed,
            by_id=f.ids is not None or f.number is not None,
            exact=True,
        )
        exclude_owner = None
        if f.exclude_owner:
            # An owner who is nobody would exclude nothing and widen the filter to every task, so it is refused.
            exclude_owner = H.resolve_actor(c, f.exclude_owner)
            if not exclude_owner:
                raise Problem("exclude_owner", "exclude_owner is a person or a bot on the roster", 422)
        visible_sql = auth.task_sql(c, who)
        if f.ids is not None:
            _, found = task_idents(c, who, ",".join(f.ids), visible_sql)
            filters["ids"] = [tid for tid in found.values() if tid]
        rows = H.tasks(
            c,
            owner=owner,
            exclude_owner=exclude_owner,
            requester=requester,
            limit=BULK_CAP + 1,
            visible=visible_sql,
            **filters,
        )
        if len(rows) > BULK_CAP:
            raise Problem(
                "too_many",
                f"More than {BULK_CAP} tasks match; a bulk change takes at most {BULK_CAP}: narrow the filter",
                422,
                extra={"cap": BULK_CAP},
            )
        return [row["id"] for row in rows]

    def bulk_one(c, who, row, body):
        """The one task call `body` (M.TaskBulk) makes on the task `row`, or False when the task already is that way."""
        moves = mover(c, who) or None
        if body.action == "close" or body.action == "move" and body.to == "closed":
            if row["status"] == "closed":
                return False
            H.task_close(c, who.actor, row["id"], body.why or "", None, mover=moves)
        elif body.action == "move":
            if body.to == "done" and row["status"] == "done":
                return False
            # A move to where the task already is changes nothing (the engine leaves it as it is).
            after = H.task_move(c, who.actor, row["id"], body.to, body.why, None, mover=moves)
            return not after or after.get("version") != row.get("version")
        elif body.action == "owner":
            target = auth.target(c, who, body.owner, need="write")
            if target == row["owner_id"]:
                return False
            H.task_reassign(c, who.actor, row["id"], target, body.why, None, mover=moves)
        else:
            labels = H.task_labels(row)
            add = [key for key in dict.fromkeys(str(t).strip().lower() for t in body.add) if key not in labels]
            drop = {str(t).strip().lower() for t in body.remove}
            changed = [key for key in labels if key not in drop] + add
            if changed == labels:
                return False
            H.task_update(c, who.actor, row["id"], labels=changed, mover=moves)
        return True

    @app.post("/api/v2/task-bulk")
    def task_bulk(request: Request, body: M.TaskBulk):
        """One change to every task a filter selects (the filters of `GET /api/v2/tasks`), each made by the task call
        a single change would make: a task the caller may not change is skipped with the reason, the rest still
        change. A dry run makes every change and rolls all of them back, so its counts are the real ones. People only,
        signed in as themselves."""
        caller = request.state.identity
        if caller.role == "bot" or getattr(caller, "via", "") or caller.task_actor:
            raise Problem("forbidden", "Only a person, signed in as themselves, changes many tasks at once", 403)
        auth.domain(caller)
        # The why goes on every task the filter selects: checked once against the company's rules
        # (backend/task_rule_gate.py) for all their people and a new owner, outside the write, applied inside it. Held,
        # no task changes; warned, each task's why carries the line. A dry run writes nothing, so it is not checked.
        rules = request.app.state.task_rules
        said = [("Why", body.why or "")]

        def reached(c):
            """Each selected task with the people the why reaches through it: its owner, its requester, a new owner."""
            new = [rules.rule_target(c, caller, body.owner)] if body.action == "owner" and body.owner else []
            for tid in bulk_select(c, caller, body.filter):
                row = H.task(c, tid)
                if row:  # selected inside this same write, so it is there
                    yield row, [a for a in (*new, row["owner_id"], row["requester_id"]) if a]

        def reach(c):
            return list(dict.fromkeys(a for _, people in reached(c) for a in people))

        def private_to(c):
            # The judge reads everyone (`reach`); a hold lists a private task's people only to whoever may read it.
            tasks = list(reached(c))
            seen = {a for row, people in tasks if not H.task_private(c, row) for a in people}
            hidden = {
                row["id"]: [a for a in people if a not in seen] for row, people in tasks if H.task_private(c, row)
            }
            return {tid: people for tid, people in hidden.items() if people}

        verdict = None if body.dry_run else rules.check_words(caller, "task_bulk", said, reach)

        def work(c):
            gate = rules.apply_words(c, caller, verdict, said, reach, private_to=private_to)
            if task_rule_gate.held(gate):
                return org_rule_check.answer(gate)
            if gate and gate.get("warn"):
                checked = body.model_copy(update={"why": task_rule_gate.warn_on(gate, body.why or "")})
            else:
                checked = body
            # Every task this call skips keeps its refusal on record, and the call counts once toward quarantine.
            token = H.BULK_CALL.set(H.new_id())
            try:
                return {**bulk_work(c, checked), **org_rule_check.answer(gate)}
            finally:
                H.BULK_CALL.reset(token)

        def bulk_work(c, body):
            # Chosen under the write lock, so each task is judged as it is now.
            ids = bulk_select(c, caller, body.filter)
            changed, unchanged, skipped = [], [], []
            # Work queued to run after commit (a webhook's doorbell) goes with the rows a rollback takes back.
            queued = len(c.after_commit)
            if body.dry_run:
                c.execute("SAVEPOINT bulk_dry_run")
            for tid in ids:
                row = H.task(c, tid)
                c.execute("SAVEPOINT bulk_task")
                before = len(c.after_commit)
                try:
                    done = bulk_one(c, caller, row, body)
                except H.Refused as exc:
                    # Audited and answered as the single change's refusal would be; the other tasks go on.
                    del c.after_commit[before:]
                    problem = refused(c, caller, exc, savepoint="bulk_task")
                    skipped.append((tid, problem.code, problem.detail))
                except Problem as exc:
                    c.execute("ROLLBACK TO bulk_task")
                    c.execute("RELEASE bulk_task")
                    del c.after_commit[before:]
                    skipped.append((tid, exc.code, exc.detail))
                else:
                    c.execute("RELEASE bulk_task")
                    (changed if done else unchanged).append(tid)
            if body.dry_run:
                c.execute("ROLLBACK TO bulk_dry_run")
                c.execute("RELEASE bulk_dry_run")
                del c.after_commit[queued:]
            # Each task the caller can still read is named; one a change took out of their sight is only counted.
            readable = {
                r[0]
                for r in c.execute(
                    f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({auth.task_sql(c, caller)})",
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

        return mutate(request, body, work)

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
            # A run on this task that stopped partway fences its queued work: Run now runs it again, or says who
            # decides, never a quiet "already waiting" (execution.run_now_held).
            if after and H.is_bot(after["owner_id"]) and request.app.state.execution.run_now_held(c, who, after):
                queued = True
            if not queued and after and H.is_bot(after["owner_id"]):
                # Its run was already waiting: on a cloud machine that was started for it once and ran nothing, Run now
                # starts the machine again instead of answering yes and doing nothing.
                from . import cloud_machines

                cloud_machines.asked_again(c, H.actor_id(after["owner_id"]))
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who), "queued": queued}

        return mutate(request, body, work)
