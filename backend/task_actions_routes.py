"""Task reads, writes, run models and org-rule replays."""

import json
from . import views
from . import read_cache
from . import person_asks
from . import teammate_access
from . import task_documents, task_followers
from . import sources as SRC
from . import ask_ingest, delivery
from . import org_rule_check
from .roadmaps import exact_approval
from . import bot_stops, holds, task_caps, reach
from . import check_models, task_run_models, usage_limits
from . import task_privacy as privacy
from . import task_relations as TR
from . import task_roles as TRo
from . import turns as turn_work
from . import goals as G
from .auth import Identity
from .onboarding import AGENT_OPS
from . import rooms
from . import item_sources as IS
from .store import H, Problem, encode
from . import bot_settings as BS
from . import (
    task_list_routes,
    task_bulk_routes,
    task_pipelines_routes,
    task_factories_routes,
    preferences_routes,
    release_schedule_routes,
    task_comments_routes,
    task_followers_routes,
)
from .api_contract import ASK_REFUSED


class ClaimGone(Exception):
    """A find-or-create whose ask-ingest judgment was skipped because a task held its item finds the item free at the
    write: the write is rolled back and the route judges the filing before trying again."""


from .api_answers.tasks import TaskDetail, TaskResult, TaskRunModelResult

from . import models as M
from .api_contract import api_v2
from fastapi import Request


def register(app, auth, mutate, settings_admin):
    @app.post(
        "/api/v2/tasks/{tid}/model",
        **api_v2(
            "Tasks",
            "setTaskModel",
            "Set the model this task's runs use, with its effort and harness (backend/task_run_models.py): an absent `model` keeps it and changes only what is named, and a null `model` clears it so the bot's task model runs the task again. Whoever may change the bot's model may change this; a bot never does. Its `readiness` says whether the bot's computer can run it now",
            TaskRunModelResult,
        ),
    )
    def task_run_model(request: Request, tid: str, body: M.TaskRunModel):
        """The model this task's runs use, with its effort and harness (backend/task_run_models.py). An absent
        `model` keeps it and changes only what is named; a null `model` clears it, so the bot's task model runs
        the task again.
        Whoever may change the bot's model may change this, and a bot never does. `readiness` says whether the bot's
        computer can run it now; `warning` is plain words for a cheaper pick on a bot that sends, spends, publishes or
        writes. It still saves."""

        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            return settings_admin.set_task_run_model(c, who, task_id, body)

        return mutate(request, body, work)


def filed_for(c, who, person, message_id):
    """`--for human:X --from-message M` (Ideas: say it once, #227): a bot files a person's own ask, and the task is the
    person's. Only when X sent M and the bot reads M; anything else is refused, and nothing is filed."""
    if who.role != "bot":
        raise Problem("for", "Only a bot files a task for a person; a person files their own", 422)
    if not H.is_human(person) or not H.human(c, person):
        raise Problem("for", f"{person} is not a person on this team", 422)
    message = H.message(c, message_id)
    from . import threads

    if not message or not threads.readable(c, who, message):  # its conversation, and the message in it
        raise Problem("from_message", "No message you can read has that id", 403)
    if message.get("sender_id") != person:
        raise Problem("from_message", f"That message was not sent by {person}", 403)
    return person


def register_core(
    app,
    assistant_direct,
    assistant_instruction,
    assistant_mod,
    auth,
    carried_request,
    delegated_identity,
    mover,
    mutate,
    person_only,
    rule_check,
    rule_comment_audience,
    rule_create_text,
    rule_public,
    rule_summary_allowance,
    rule_target,
    rule_task_audience,
    rule_update_check,
    rule_update_text,
    rule_warned_fields,
    send,
    settings,
    settings_admin,
    store,
    task_place,
    task_view,
    visible_tasks,
    visual_view,
):
    def task_filters(
        c,
        owner=None,
        requester=None,
        status=None,
        lane=None,
        label=None,
        number=None,
        updated_since=None,
        member=None,
        role=None,
        snoozed=None,
        by_id=False,
        exact=False,
    ):
        """The filters `GET /api/v2/tasks` takes, checked and resolved: (owner, requester, H.tasks keywords). A bulk
        change (`POST /api/v2/task-bulk`) selects with the same ones. `exact` refuses an owner or requester that names
        nobody, where a list would quietly drop the filter: a bulk change must never widen to every task."""
        found_owner = H.resolve_actor(c, owner) if owner else None
        found_requester = H.resolve_actor(c, requester) if requester else None
        # Given at all, even empty (`requester=`), a name must resolve: dropping it would widen the selection.
        if exact and (owner is not None and not found_owner or requester is not None and not found_requester):
            raise Problem(
                "owner" if owner is not None and not found_owner else "requester",
                "owner and requester are a person or a bot on the roster",
                422,
            )
        if member:
            member = H.resolve_actor(c, member)
            if not member:
                raise Problem("member", "member is a person or a bot on the roster", 422)
        if role and not TRo.role_name(role):
            raise Problem("role", "role is a short name in lower-case letters, digits, - or _", 422)
        if role and not member:
            raise Problem("role", "role filters member: pass both", 422)
        if lane and lane not in H.TASK_LANES:
            raise Problem("kind", "lane is company or product", 422)
        statuses = status.split(",") if status and status != "all" else None
        if statuses and any(s not in ("open", "done", "closed") for s in statuses):
            raise Problem("status", "status is open, done or closed", 422)
        if snoozed not in (None, "hide", "only", "all"):
            raise Problem("snoozed", "snoozed is hide, only or all", 422)
        if status == "all" and snoozed is None:
            snoozed = "all"  # every task means the snoozed ones too: they are open, only set aside
        if number is not None and not 1 <= number <= 999_999_999:
            raise Problem("number", "number is a task number, from 1", 422)
        since = H.parse_ts(updated_since) if updated_since else None
        if updated_since and (not since or since.tzinfo is None):
            raise Problem("date", "updated_since must be an ISO-8601 date/time with a timezone", 422)
        return (
            found_owner,
            found_requester,
            dict(
                status=statuses,
                lane=lane,
                label=label,
                number=number,
                updated_since=views.since_time(updated_since) if since else None,
                member=member,
                role=TRo.role_name(role) if role else None,
                snoozed=None if snoozed == "all" or (snoozed is None and by_id) else snoozed or "hide",
            ),
        )

    def task_idents(c, who, ids, visible_sql):
        """`ids` (1 to 100 task ids or numbers, comma-separated) as typed, each with the task it names for this reader
        or None (unknown, ambiguous or not readable)."""
        typed = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
        if not typed or len(typed) > 100:
            raise Problem("ids", "ids is 1 to 100 task ids or numbers, comma-separated", 422)
        found = {}
        for ident in typed:
            try:
                found[ident] = auth.resolve_task(c, who, ident, visible=visible_sql)
            except Problem:
                found[ident] = None
        return typed, found

    task_list_routes.register(
        app, auth, mutate, rule_check, rule_target, store, task_filters, task_idents, visible_tasks
    )

    @app.get(
        "/api/v2/tasks/{tid}",
        **api_v2(
            "Tasks",
            "getTask",
            "A task with its history, comments and messages: where it is (pipeline, stage), whose turn it is, what it takes to leave its stage (to_leave, each requirement with what the reader may do), the next stage and the tasks it filed",
            TaskDetail,
        ),
    )
    def task(request: Request, tid: str):
        from .github import refresh_task_prs

        # One snapshot finds the task, checks the reader and reads it. The PR refresh runs apart and this answer serves
        # the stored PR state either way, so it is scheduled once the reader is known to read the task.
        with store.read_transaction() as c:
            tid = auth.resolve_task(c, request.state.identity, tid)
            row = auth.task(c, request.state.identity, tid)
            refresh_task_prs(app.state.github_app, tid)
            # A bot task now lives in the bot's chat room. Only this task's messages
            # come back here; the rest of that room stays on Chat.
            who = request.state.identity
            # Each related task is read under the reader's own rule: reading this one grants no other.
            related_sql = auth.task_sql(c, who)
            child_rows = H._rows(
                c.execute(
                    # A check sub-task's role (backend/check_models.py): the task page shows a second review's state.
                    "SELECT id,title,status,owner_id,"
                    "(SELECT role FROM task_check_models m WHERE m.task_id=tasks.id) AS role FROM tasks WHERE id IN ("
                    + TR.CHILD_IDS
                    + ") AND ("
                    + related_sql
                    + ") ORDER BY (rank IS NULL), rank, created_at",
                    (tid,),
                )
            )
            child_refs = H.ticket_refs(c, [t["id"] for t in child_rows])
            children = [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "status": t["status"],
                    "owner_id": t["owner_id"],
                    **child_refs[t["id"]],
                    **({"role": t["role"]} if t["role"] else {}),
                }
                for t in child_rows
            ]
            # The parent ticket, when there is one. Its title is given only when the reader may read it: auth.task
            # refuses a task the reader cannot see, and then the parent is null.
            parent = None
            parent_id = TR.parent_of(c, tid)
            if parent_id:
                try:
                    parent_row = auth.task(c, who, parent_id)
                except Problem:
                    parent_row = None
                if parent_row:
                    parent = {"id": parent_id, "title": parent_row["title"], **H.ticket_refs(c, [parent_id])[parent_id]}
            from .task_reads import comment_rights
            from .views import task_wait

            try:
                comment_rights(c, auth, who, tid)
                can_comment = True
            except Problem:
                can_comment = False
            from . import task_reads

            history = task_reads.readable_history(c, who, H.task_history(c, tid, provenance=True), related_sql)
            lines = SRC.lookup(c, SRC.actors_of(history))
            for event in history:
                event["source"] = SRC.source_of(lines, event)  # reads `data`, which the answer leaves out
                event.pop("data", None)
            # An ask's words come from the pages it showed (body_sources): a reader who may not open one reads none.
            from .files import readable_page_refs

            history = readable_page_refs(c, auth, auth.run_viewer(c, who), history)
            for event in history:
                if isinstance(event, dict):
                    event.pop("body_sources", None)
            # Who follows it (backend/task_followers.py): null for a task with no thread of its own.
            return {
                "task": {
                    **task_view(row, c, visible_sql=related_sql, who=who),
                    "followers": task_followers_seen(c, who, row),
                    # Its documents, newest change first, with approval marks from this snapshot.
                    "documents": task_documents.listing(c, app.state.files, who, tid),
                    # Its label, as hubdb.ticket_refs says it ("O561"), from the row already read.
                    "label": f"{row['prefix']}{row['number']}"
                    if row.get("prefix") and row.get("number") is not None
                    else None,
                },
                "events": history,
                "can_comment": can_comment,
                "children": children,
                "parent": parent,
                # The task page reads as its session's chat: each bot reply carries the run behind it (`run`,
                # the steps one click away) and `execution` is the run it follows while it works: the owner's,
                # or the one for a person's message, whichever bot that went to. `comment_to` is who the reader's
                # next comment reaches, so the composer says before sending when no bot will hear it.
                "comments": visual_view(
                    c, who, turn_work.annotate(c, auth, who, H.task_comments(c, tid, actor=privacy.actor(who)))
                ),
                "execution": task_execution(c, who, row),
                "comment_to": comment_to(c, who, row) if can_comment else None,
                "comment_delivery": delivery_choices(c, who, row) if can_comment else None,
                "mover": mover(c, request.state.identity),
                "waiting_for": task_wait(c, tid, settings),
                "runs": task_runs(c, who, tid),
                # The model a bot's check subtask runs on (backend/check_models.py), or null.
                "check_model": H.check_model(c, tid),
                # The task's own model (backend/task_run_models.py), or null when its runs use the bot's task model.
                "run_model": task_run_models.of(c, tid),
                # Whether this reader may set that model: the answer POST /v2/tasks/{id}/model gives, so the task
                # page offers its form to whoever the server would accept. A person's task has no model to set.
                "can_set_run_model": H.is_bot(row["owner_id"])
                and task_run_models.may_set(c, auth, who, H.actor_id(row["owner_id"])),
                # The agent Tico stopped that this task is the Needs-you item for, when the reader may release it
                # (backend/bot_stops.py): the page puts its Release in place. Null on every other task.
                "stopped_agent": bot_stops.stopped_on(c, auth, settings_admin, who, tid),
                **privacy.page(c, who, row["conversation_id"], task_id=tid),
            }

    @app.get("/api/v2/tasks/{tid}/execution")
    def task_execution_read(request: Request, tid: str):
        """The run the task's page follows (`execution` of GET /api/v2/tasks/{tid}, the same value), alone: the page
        reads it as the run's steps come in, without reading the whole task each time."""
        who = request.state.identity
        with store.read_transaction() as c:
            row = auth.task(c, who, auth.resolve_task(c, who, tid))
            return {"execution": task_execution(c, who, row)}

    def task_execution(c, who, row):
        """The run the task's page follows in its own room, as a conversation snapshot shows it, or None: the owning
        bot's newest run, or a newer one for a message a person sent there, whichever bot it was routed to (the bot
        that asked a person's task, say). Another bot's run for a bot's comment is not it. A task still in a chat
        room (filed into a chat before task rooms) shows none: that room's runs are the chat's, and reading the task
        does not let one read the chat."""
        cid, owner = row["conversation_id"], str(row["owner_id"] or "")
        room = H.conversation(c, cid) if cid else None
        if not room or room.get("task_id") != row["id"]:
            return None
        from .views import conversation_execution

        return conversation_execution(
            c, cid, who, settings, bot=owner.removeprefix("bot:") if H.is_bot(owner) else None, auth=auth, people=True
        )

    def comment_bot(c, row):
        """The bot a plain comment on the task gives a turn first (threads._task_primary, with no @mention): its first
        live bot follower who still reads it, or, for a task in a shared chat (no followers), its owner bot. One
        read. None when there is none."""
        from . import task_followers as TF

        tid = TF.thread_id(c, row)
        if not tid:
            return row["owner_id"] if H.is_bot(row["owner_id"]) else None
        bots = [
            r[0]
            for r in c.execute(
                "SELECT actor FROM thread_followers WHERE thread_id=? AND following=1 AND "
                "notify='live' AND actor LIKE 'bot:%' ORDER BY created_at, actor",
                (tid,),
            )
        ]
        private = bots and H.task_private(c, row)
        return next((b for b in bots if not private or H.task_private_readable(c, b, row)), None)

    def comment_way_for(c, who, row):
        """The way a reader's comment with none named goes: Steer (D13) for whoever may direct the task's bots
        (chat_goals.may_direct), else Defer never, so a reader never spends a bot's turns on a task that isn't
        theirs."""
        from .chat_goals import may_direct

        return ("steer", None) if who.role == "bot" or may_direct(c, who, row) else ("defer", delivery.NEVER)

    def comment_to(c, who, row):
        """Who a plain comment from this reader wakes first (comment_bot): `bot`, or None when no bot follows the
        task; `wakes`, whether its default way wakes that bot (steer) rather than waiting for its next turn on the task
        (defer); `mention`, kept empty: an @mention always reaches whoever it names. None for a bot reader."""
        if who.role == "bot":
            return None
        bot = comment_bot(c, row)
        if not bot:
            return {"bot": None, "wakes": False, "mention": []}
        return {"bot": H.actor_id(bot), "wakes": comment_way_for(c, who, row)[0] != "defer", "mention": []}

    def delivery_choices(c, who, row):
        """How this reader's comment can reach the task's bots (docs/tasks.md, backend/delivery.py): `default`, the way
        a comment with none named goes (steer or defer; null when no bot follows the task) and `default_for`, a default
        defer's deadline (never); `steer` and `queue`, whether the reader may wake them (chat_goals.may_direct;
        anyone may defer); `steers`, whether the first bot's harness writes a steer into its running turn; `goal`,
        whether the reader may set the task session's goal (chat_goals.own_room and chat_goals.may_set). None for a
        bot reader."""
        if who.role == "bot":
            return None
        bot = comment_bot(c, row)
        if not bot:
            return {
                "default": None,
                "default_for": None,
                "steer": False,
                "queue": False,
                "steers": False,
                "goal": False,
            }
        way, defer_for = comment_way_for(c, who, row)
        config = read_cache.one(c, BS.ONE_SQL, (H.actor_id(bot),))
        config = json.loads(config[0] or "{}") if config else {}
        from .harnesses import steers
        from .chat_goals import may_direct, may_set, own_room

        directs = may_direct(c, who, row)
        return {
            "default": way,
            "default_for": defer_for if way == "defer" else None,
            "steer": directs,
            "queue": directs,
            "steers": steers(config, config.get("runtime")),
            "goal": own_room(c, row) and may_set(c, auth, who, row["conversation_id"]),
        }

    def task_runs(c, who, tid, limit=200):
        """The runs that worked this task, each with the model that actually ran it (`ran_on`). A review that
        weighs who built what reads this, never the bot's configured model: a fallback turn ran elsewhere."""
        from .usage_records import ran_on

        actor = privacy.actor(who)
        rows = H._rows(
            c.execute(
                "SELECT id,bot_id AS bot,created_at AS started_at,settled_at AS finished_at,exit "
                "FROM attempts WHERE task_id=? AND (job_id IS NULL OR trigger IS NOT NULL) "
                "ORDER BY created_at DESC LIMIT ?",
                (tid, limit),
            )
        )
        privacy.prime_attempts(c, [r["id"] for r in rows])
        rows = [r for r in rows if privacy.attempt_readable(c, actor, r["id"])]
        made = ran_on(c, [r["id"] for r in rows])
        return [
            {
                "run": r["id"],
                "bot": r["bot"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "outcome": r["exit"],
                "ran_on": made[r["id"]],
            }
            for r in rows
        ]

    def check_choice(c, who, body, owner, parent_id):
        """The check model a bot's subtask asks for (backend/check_models.py), or Problem. Only a bot files one, as
        its own subtask under its own task, so the verdict comes back to the session that asked."""
        if who.role != "bot" or who.via or owner != who.actor:
            raise Problem("check_model", "Only a bot runs a check model, on a subtask it owns", 422)
        parent = H.task(c, parent_id) if parent_id else None
        if not parent or parent["owner_id"] != who.actor:
            raise Problem("check_model", "A check model runs a subtask: file it under a task you own", 422)
        role = body.check_role or "check"
        try:
            return check_models.choose(
                c,
                settings,
                H.actor_id(owner),
                body.check_model,
                role,
                check_models.authors_of(c, settings, parent_id, owner if role == "review" else None),
            )
        except check_models.CheckModelError as exc:
            raise Problem(exc.code, str(exc), 409 if exc.code == "runner_not_ready" else 422)

    def reads_bot(c, person, bot):
        """Whether `person` may read `bot`'s activity, so a task the bot holds is one they can open (auth.task_row)."""
        try:
            them = auth.identity_for_actor(c, person)
        except Problem:
            return False
        level = auth.bot_accesses(c, them, [H.actor_id(bot)]).get(H.actor_id(bot))
        return not level or bool(level["read"])

    def task_create(c, who, body, lint=True, ingest=None, same=None, rule=None, wake_refs=None, judged=True):
        """`POST /api/v2/tasks`: the task is made and put in the first stage of its pipeline (H.task_create). A task
        for a bot wakes it; `wake_refs` go on that wake (an auto dream's release policy, backend/dream_auto.py)."""
        source = who
        actual_bot = source.task_actor or (source.actor if source.role == "bot" else "")
        if body.from_private:
            # Older clients' name for `private: false` from a private task's run.
            body = body.model_copy(update={"private": False, "from_private": False})
        elif body.private is None and privacy.private_execution(c, who):
            # What a run on private work files is private unless it asks for company-visible (`private: false`).
            body = body.model_copy(update={"private": True})
        request_id = body.request_id
        handoff = who.role == "bot" and who.actor != "bot:" + AGENT_OPS and not who.via
        if request_id and handoff:
            request_id = None  # a bot cites the person it is handing on; it never acts as them itself
        elif request_id and who.via == "assistant":
            pass  # the person's own words to their Assistant, checked once the owner is known
        elif request_id:
            who = delegated_identity(c, who, request_id, new_task=True)
        # Find-or-create (docs/dev/pipelines-v3.md "Intake"): a create naming its outside item's primary link returns
        # the task already holding it, after the same rule check, and files nothing; the checks that only filing needs
        # (the owner's cap, reach) are skipped for it when the caller may read it. One the caller may not read is never
        # named: the create is refused as already filed, after every check a filing makes. The item, not the title,
        # makes it one task. Read as whoever the create acts as (a person's own request: that person).
        primary = found = None
        if getattr(body, "primary_status", None) and not getattr(body, "primary_link", None):
            raise Problem("primary_status", "primary_status goes with primary_link", 422)
        if getattr(body, "primary_link", None):  # only a JSON create's (M.TaskFile): never an upload's form
            try:
                primary, held_by = IS.lookup(c, body.primary_link)
            except ValueError as error:
                raise Problem("primary_link", str(error), 422) from None
            found = held_by if held_by and _readable_task(c, who, held_by) else None
            hidden = bool(held_by) and not found
            if not held_by and not judged:
                # Ask ingest was skipped because a task held the item; it no longer does, so this is a filing after all:
                # nothing is written, and the route judges it and tries again (`create_task`).
                raise ClaimGone()
            same = lambda _c, _task_id: False  # another item's task with this title is other work
        else:
            hidden = False
        parent_id = auth.resolve_task(c, who, body.parent) if body.parent else None
        others = [(auth.resolve_task(c, who, r.task), r.kind) for r in body.relations]
        from .shared_bots import route

        # No owner: a first stage that picks from several picks one as the task arrives (backend/stage_pick.py). The
        # filer stands in as its requester and reach, as for a task a stage files, and it enters with no assignee.
        unassigned = not body.owner
        if unassigned:
            from . import stage_pick

            refused = stage_pick.no_owner_refusal(c, body.pipeline)
            if refused:
                raise Problem("owner", refused, 422)
            body = body.model_copy(update={"owner": who.actor})
        original = auth.target(c, who, body.owner, need="write")
        owner = auth.target(c, who, route(c, who.actor, original), need="write")
        from . import meeting_audience

        meeting_participants = (
            meeting_audience.check(c, auth, who, owner, live_id=body.live_meeting_id, roles=body.roles)
            if body.live_meeting_id
            else None
        )
        if meeting_participants is not None:
            if body.visibility == "public":
                raise Problem("meeting_audience", meeting_audience.REFUSAL, 409)
            body = body.model_copy(update={"private": True})
        if not found:
            task_caps.check(c, who.actor, owner)  # the first hub check on a send (CONTRACT §1): nothing is written
        # A bot's task for a teammate out of its reach, or its question to one (docs/reach.md), is refused next.
        if not found:
            reach.check(c, who, owner, "task", task_id=parent_id)
        asked_agent_ops = None
        if request_id and who.via == "assistant":
            # The Assistant handing a helper (Agent Ops, the Strategy Owner, the Librarian; assistant.HELPERS) what the
            # person asked for in their own Assistant chat, citing that message (`--request-id turn`), only while the
            # owner lets the Assistant act without asking. Agent Ops then acts with the person's rights
            # (delegated_identity, `assistant`); the Strategy Owner and the Librarian work it with their own rights, so
            # what only the owner may change (strategy, goals) still reaches the owner as a proposal or a card.
            # Any other owner is handed the task as the person files it, and the citation lends it nothing.
            if H.actor_id(owner) in assistant_mod.HELPERS and H.is_bot(owner):
                asked_agent_ops = assistant_instruction(c, who, request_id) if assistant_direct() else None
            request_id = None
        # A subtask for a person under a private task with no person on it, filed by the bot that owns it, makes them
        # its reader.
        reach_to = privacy.reach_for(c, source, owner, H.task(c, parent_id) if parent_id else None)
        if not found:
            auth.require_bot_contact(c, who, owner, task_id=parent_id, kind="task")
        # A subtask is the shape that parks the filer as `waiting` and makes it care when the
        # other one finishes. A bot set to `tasks` has opted out of that on both sides: it does
        # not wait, and its own completions are quiet, so parking on one would wait for ever.
        # Every other bot keeps the handoff flow in `policies/handoffs.md`.
        if (
            parent_id
            and who.role == "bot"
            and owner != who.actor
            and str(owner).startswith("bot:")
            and (H.tasks_only(c, who.actor) or H.tasks_only(c, owner))
        ):
            raise Problem(
                "subtask",
                "A bot set to tasks-only files work flat: give it its own "
                "task without a parent, and do not wait for it.",
                422,
            )
        if parent_id:
            auth.task(c, who, parent_id)
        check, unchecked = None, None
        if body.check_model is not None or body.check_role not in (None, "review"):
            check = check_choice(c, who, body, owner, parent_id)
        else:
            check, unchecked = check_models.review_default(c, settings, owner, parent_id)
        # The task's own model, checked before anything is written (the same rights as the bot's model, a bot never).
        run_model = None
        if body.model is not None or body.effort or body.harness or body.compact_at:
            if not H.is_bot(owner):
                raise Problem("owner", "Only a task a bot runs has a model to choose", 422)
            if body.model is None and (body.effort or body.harness):
                raise Problem("model", "Choose a model to pick an effort or harness for it", 422)
            if body.model is None:
                # A window alone: its runs keep the bot's model (backend/task_run_models.py).
                run_model = settings_admin.task_run_window(
                    c, who, H.actor_id(owner), "set where a new task's runs compact", body.compact_at
                )
            else:
                run_model = settings_admin.task_run_choice(
                    c, who, H.actor_id(owner), "set the model a new task runs on", body.model, body.effort, body.harness
                )
                run_model["compact_at"] = body.compact_at
        for other, _ in others:
            auth.task(c, who, other)
        parent = H.task(c, parent_id) if parent_id else None
        asked_public = body.private is False
        # Delegation retains the bot's sensitive default and cannot use a person's
        # explicit-public choice to override either creator or assignee defaults.
        if actual_bot and body.private is False:
            body = body.model_copy(update={"private": None})
        if actual_bot and H.private_tasks_default(c, actual_bot):
            body = body.model_copy(update={"private": True})
        defaults = H.private_tasks_default(c, who.actor) or H.private_tasks_default(c, owner)
        # `private: false` is company-visible under a private parent too, unless a sensitive default keeps a bot's
        # task private; the parent stays its readers' alone (task_relations shows it as "a private task").
        public = asked_public and body.private is not True and not (actual_bot and defaults)
        private = not public and bool(
            (body.private if body.private is not None else defaults) or parent and H.task_private(c, parent)
        )
        if body.due and (not (parsed := H.parse_ts(body.due)) or parsed.tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        # The org rule check (backend/org_rule_check.py), judged before the write: held, nothing is filed.
        rule_gate = (
            org_rule_check.apply(c, rule, rule_create_text(body), {"body": body.model_dump(mode="json", by_alias=True)})
            if rule
            else None
        )
        if rule_gate and rule_gate.get("held"):
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.answer(rule_gate)
        if hidden:
            if ingest:
                ask_ingest.skipped(c, ingest, "not_applied")
            raise Problem("primary_link", IS.ALREADY_FILED, 409)
        if found:
            if ingest:
                ask_ingest.skipped(c, ingest, "not_applied")
            held = H.task(c, found)
            return {
                "task": task_view(held, c, visible_sql=auth.task_sql(c, who), who=who),
                "found": True,
                **org_rule_check.answer(rule_gate),
            }
        public_warning = ""
        if rule_gate and rule_gate.get("warn"):
            approved = exact_approval(c, who.actor)
            body = body.model_copy(update=rule_warned_fields(rule_gate, body, approved=approved))
            # Whether the task is public is the store's to say (its type's default included): it adds the line there.
            public_warning = "" if approved else org_rule_check.warn_line(rule_gate)
        # Ask ingest (backend/ask_ingest.py): a bot's task for a person, judged before the write. SENT_BACK and FYI
        # refuse the whole write; a merged ask stays on the bot's task.
        gate = None
        if ingest and owner == ingest["person"]:
            gate = ask_ingest.apply(c, ingest)
            if gate["acts"]:
                owner, check, unchecked = who.actor, None, None
        elif ingest:
            ask_ingest.skipped(c, ingest, "not_applied")
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        requester_actor = None
        if who.role == "bot" and owner == who.actor and not unassigned and not parent_id and who.attempt_id and private:
            origin = c.execute(
                "SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                "JOIN messages m ON m.id=j.message_id WHERE a.id=? AND a.bot_id=?",
                (who.attempt_id, H.actor_id(who.actor)),
            ).fetchone()
            if origin and privacy.message_readable(c, privacy.actor(who), origin):
                if H.is_human(origin["sender_id"]):
                    requester_actor = origin["sender_id"]
                else:
                    message = H.message(c, origin["id"])
                    assert message is not None  # the origin message of this run
                    source_task = H.task(c, H.message_task_id(message, H.conversation(c, message["conversation_id"])))
                    if (
                        source_task
                        and H.task_private(c, source_task)
                        and source_task["owner_id"] == who.actor
                        and H.is_human(source_task["requester_id"])
                    ):
                        requester_actor = source_task["requester_id"]
        if body.for_actor:
            # A bot files a person's own ask: the person asked for it, so it is theirs (Ideas: say it once, #227).
            requester_actor = filed_for(c, who, body.for_actor, body.from_message)
        # The person's own Assistant turn files their private handoff as them (privacy.as_the_person): the Assistant
        # stays out of it, so it is no participant to require.
        person = privacy.as_the_person(who) if private else None
        if private and actual_bot and not person and actual_bot not in (requester_actor or who.actor, owner):
            raise Problem("privacy", "The acting bot must be a current participant of the private task", 403)
        if reach_to:
            H.task_reach(c, who.actor, parent, owner)
        # A bot's task for a person is the bot's own task with its question to them (backend/person_asks.py): Needs you
        # holds only what is asked of a person, so a plain task handed to them would reach no list they are told of.
        asks_person = ask_ingest.gated(who, settings.assistant_bot) and H.is_human(owner)
        # The bot holds it when the person may read its work; else the task stays the person's, with the bot's question.
        holder = who.actor if asks_person and reads_bot(c, owner, who.actor) else owner
        row = H.task_create(
            c,
            who.actor,
            body.title,
            body.body,
            holder,
            body.due,
            parent_id,
            requester_actor=requester_actor,
            requester_said=bool(body.for_actor),
            visibility=body.visibility,
            public_title=body.public_title,
            public_summary=body.public_summary,
            public_warning=public_warning,
            private=private,
            conversation_id=rooms.task_conversation_id(c, auth, holder, who.actor),
            lane=body.lane,
            labels=body.labels,
            top=body.top,
            lint=lint,
            goal_id=body.goal_id,
            pipeline=body.pipeline,
            number=body.number,
            mover=mover(c, who) or None,
            public=public,
            same=same,
            follow=body.follow,
            # Who else sees it, before it enters its first stage and before a meeting's audience is checked against
            # everyone who will.
            readers=body.readers or (),
            readers_check=lambda task_id, added: privacy.require_readers(c, who, task_id, added, shared=parent_id),
            unassigned=unassigned,
            wake_refs=wake_refs,
            human_id=body.human,
            asks=owner if asks_person and holder != owner else None,
        )
        c.execute("UPDATE tasks SET acceptance=? WHERE id=?", (encode(body.acceptance_criteria), row["id"]))
        if asks_person:
            person_asks.ask_on(c, who.actor, row, owner, body.title, person_asks.HANDED_WHY)
            row = {**row, **(H.task(c, row["id"]) or {})}
        if body.for_actor:
            H._task_event(
                c,
                row["id"],
                who.actor,
                "filed_for",
                None,
                body.for_actor,
                f"Filed for {H.actor_name(c, body.for_actor)}, from their message",
            )
        if run_model:
            task_run_models.set_choice(c, who.actor, row["id"], run_model, note="Set when the task was filed")
        # A send, counted against the sender's cap for this owner, at the moment the task was made.
        task_caps.record(c, row["id"], who.actor, owner, at=row["created_at"])
        if gate:
            ask_ingest.record(c, ingest, gate, row["id"])
        if check:
            # A check is its owner's (check_models.of): one asked for is filed by its owner (check_choice), and a
            # reviewer's default comes from its own setting.
            check_models.record(c, owner, row["id"], check, parent_id, f"{body.title}\n{body.body or ''}")
        elif body.check_model is None and body.check_role is None and unchecked:
            H._task_event(c, row["id"], who.actor, "check_model", None, None, unchecked)
        if handoff and owner == "bot:" + AGENT_OPS:
            carried = carried_request(c, who, body.request_id)
            if carried:
                H.event(c, who.actor, "botops.request_carried", row["id"], carried)
                if carried["lends"]:
                    # The person's own words on the task, so Agent Ops does what they asked, not what the bot read into it.
                    words = (H.message(c, carried["message_id"]) or {}).get("body") or ""
                    name = (H.human(c, H.actor_id(carried["person"])) or {}).get("name") or H.actor_id(
                        carried["person"]
                    )
                    H._write_message(
                        c,
                        H.TICO_ACTOR,
                        owner,
                        f"{name} asked {(H.bot(c, H.actor_id(who.actor)) or {}).get('slug') or H.actor_id(who.actor)} for this, in their words:\n\n> "
                        + "\n> ".join(words[:2000].splitlines() or [""]),
                        H.conversation(c, row["conversation_id"]),
                        "say",
                        {
                            "task": row["id"],
                            "comment": True,
                            "quiet": True,
                            "carried": True,
                            "source": SRC.ASSISTANT_FOR,
                            "for": carried["person"],
                        },
                        None,
                        None,
                    )
        elif handoff and body.request_id:
            raise Problem("request_id", "Only a task for Agent Ops carries a person's request", 422)
        if asked_agent_ops:
            request_id = asked_agent_ops["cited"]
            # Agent Ops' lending reads `botops.asked_via_assistant` (delegated_identity); the other helpers' record says
            # which helper, and lends nothing, except to the Tools Owner on a Tools change (delegated_identity).
            if owner == "bot:" + AGENT_OPS:
                H.event(c, who.actor, "botops.asked_via_assistant", row["id"], asked_agent_ops)
            else:
                H.event(c, who.actor, "assistant.asked_helper", row["id"], {**asked_agent_ops, "helper": owner})
            words = (H.message(c, request_id) or {}).get("body") or ""
            name = (H.human(c, H.actor_id(who.actor)) or {}).get("name") or H.actor_id(who.actor)
            H._write_message(
                c,
                H.TICO_ACTOR,
                owner,
                f"{name} asked the {settings.assistant_name} for this, in their words:\n\n> "
                + "\n> ".join(words[:2000].splitlines() or [""]),
                H.conversation(c, row["conversation_id"]),
                "say",
                {
                    "task": row["id"],
                    "comment": True,
                    "quiet": True,
                    "carried": True,
                    "source": SRC.ASSISTANT_FOR,
                    "for": who.actor,
                },
                None,
                None,
            )
        inherited_request = (
            parent.get("request_id")
            if parent and H.is_human(who.actor) and who.actor == parent["requester_id"]
            else None
        )
        if request_id in ("turn", "default"):
            # `turn` names the run's own request: store that message, which delegated_identity resolved, so the task's
            # own run (whose turn is the task notice) still finds the person's words.
            request_id = (H.DELEGATION.get() or {}).get("cited") or None
        if H.is_human(who.actor) and (request_id or inherited_request):
            c.execute("UPDATE tasks SET request_id=? WHERE id=?", (inherited_request or request_id, row["id"]))
        for url in body.links:
            H.task_link(c, who.actor, row["id"], url, mover=True, plain_when_unsure=True)
        if primary:
            # The item this task is filed for: its primary link (the partial unique index keeps it one task's).
            H.task_link(c, who.actor, row["id"], primary[1], item=primary[0], mover=True)
            IS.claim(c, row["id"], primary[0])
            if getattr(body, "primary_status", None):
                # The status it is filed in is its Ready: a withdrawal is a move to another To Do status.
                from . import release_closeout

                release_closeout.remember_ready(c, row["id"], body.primary_status)
        # A bot's finding on a pipeline task's own pull request is that task's review verdict, not a person's task.
        H.review_finding_route(c, who.actor, row["id"], row["owner_id"])
        # The parent it was filed under is a relation written here too, and reads back first.
        filed_under = TR.parent_of(c, row["id"])
        readback = TR.readback(c, row["id"], filed_under, "parent") if filed_under else []
        for other, kind in others:
            changed = TR.relate(c, who.actor, row["id"], other, kind, mover=mover(c, who) or None)
            readback += TR.readback(c, row["id"], other, kind, changed=changed)
        if body.live_meeting_id:
            filed_task = H.task(c, row["id"])
            assert filed_task is not None  # the task this call just filed
            meeting_audience.check(
                c,
                auth,
                who,
                owner,
                live_id=body.live_meeting_id,
                roles=body.roles,
                readers=H.task_readers(filed_task),
                requester=filed_task["requester_id"],
            )
            c.execute("UPDATE tasks SET live_meeting_id=? WHERE id=?", (body.live_meeting_id, row["id"]))
            meeting_audience.grant(c, row["id"], meeting_participants, who.actor)
        if body.roles:
            try:
                TRo.set_roles(c, who.actor, row["id"], body.roles, mover=mover(c, who) or None)
            except ValueError as exc:
                raise Problem("roles", str(exc), 422)
        note = " ".join(
            filter(
                None,
                (usage_limits.warning(c, owner) or holds.warning(c, owner) or H.setup_hold(c, owner),),
            )
        )
        # Read back as the person when the task is private now: it may have become private on entering its stage.
        created = H.task(c, row["id"])
        reader = privacy.as_the_person(who) if H.task_private(c, created) else None
        return {
            "task": task_view(created, c, visible_sql=auth.task_sql(c, reader or who), who=reader or who),
            **({"readback": readback} if readback else {}),
            **ask_ingest.reply(gate),
            **org_rule_check.answer(rule_gate),
            **({"warning": note} if note else {}),
            **({"warnings": row["warnings"]} if row.get("warnings") else {}),
            **({"private_readers": row["private_readers"]} if row.get("private_readers") else {}),
        }

    def cap_before_preflight(request, who, owner_ref, parent_ref=None, primary_link=None, request_id=None):
        """The task cap (CONTRACT §1) and reach (docs/reach.md) before any LLM preflight: a refused send spends no
        judgment and writes nothing. A retry whose Idempotency-Key already has a saved answer is not checked: `mutate`
        replays that answer."""
        if not owner_ref:
            return
        from .shared_bots import route

        with store.read() as c:
            if store.saved(c, who, request.url.path, request.headers.get("idempotency-key")):
                return
            if primary_link and not request_id and _held_item(c, who, primary_link) == "readable":
                return  # it returns the task holding the item: nothing is sent to the owner
            original = auth.target(c, who, owner_ref, need="write")
            owner = auth.target(c, who, route(c, who.actor, original), need="write")
            task_caps.check(c, who.actor, owner)
        reach.precheck(
            store,
            who,
            lambda c: reach.check(
                c, who, owner, "task", task_id=auth.resolve_task(c, who, parent_ref) if parent_ref else None
            ),
        )

    def saved_retry(request, who):
        """Whether this request's Idempotency-Key already has a saved answer: `mutate` replays it without the write, so
        ask ingest does not judge it again (a retry makes no judge call, and so writes no second row either)."""
        with store.read() as c:
            return store.saved(c, who, request.url.path, request.headers.get("idempotency-key"))

    @app.post(
        "/api/v2/tasks", **api_v2("Tasks", "createTask", "Create a task", TaskResult, responses={"422": ASK_REFUSED})
    )
    def create_task(request: Request, body: M.TaskFile):
        who = request.state.identity
        if who.via == "botops" and who.role == "bot" and H.is_bot(who.task_actor) and who.task_actor != who.actor:
            # Agent Ops filing for a bot that asked it: a bot's request lends nothing, so Agent Ops files the task as itself,
            # with its own rights, and is its requester (who filed it).
            who = Identity(who.task_actor, "bot", runner_id=who.runner_id, attempt_id=who.attempt_id, agent=who.agent)
            request.state.identity = who
        # A create carrying a person's request reads the item as that person, at the write: its cap is checked as ever.
        cap_before_preflight(request, who, body.owner, body.parent, body.primary_link, body.request_id)
        rc = rule_check(
            who,
            "task_create",
            rule_create_text(body),
            lambda c: [rule_target(c, who, body.owner), *(org_rule_check.company(c) if rule_public(body) else [])],
        )
        # A write the rule check holds, a saved retry, or a create whose item a task already holds (nothing is filed)
        # skips ask ingest's call.
        with store.read() as c:
            held = bool(body.primary_link) and _held_item(c, who, body.primary_link) is not None
        ingest = (
            None
            if org_rule_check.holds(rc) or saved_retry(request, who) or held
            else ask_ingest.preflight(store, app.state.judge, request.state.identity, body.owner, body.title, body.body)
        )
        # The hash leaves both out when absent (M.TaskFile.model_dump), as before they existed; a create saved while
        # only `primary_link` existed hashed it as null.
        plain = body.primary_link is None and body.primary_status is None
        legacy = {**body.model_dump(), "primary_link": None} if plain else None

        def attempt(ingest, judged):
            return ask_ingest.judged(
                store,
                ingest,
                lambda: mutate(
                    request,
                    body,
                    lambda c: task_create(c, request.state.identity, body, ingest=ingest, rule=rc, judged=judged),
                    receipt=handoff_receipt(request.state.identity),
                    retry_body=lambda _c, _result: legacy,
                ),
            )

        try:
            return attempt(ingest, not held or org_rule_check.holds(rc))  # a held create is never judged
        except ClaimGone:  # the task holding the item went before the write: judge the filing, then file it
            judged = ask_ingest.preflight(
                store, app.state.judge, request.state.identity, body.owner, body.title, body.body
            )
            return attempt(judged, True)

    def _held_item(c, who, primary_link):
        """`readable` when a task the caller may read already holds the primary link a create names, `hidden` when one
        it may not read does, else None (none, or a link that names no item: the create's own error)."""
        try:
            held_by = IS.lookup(c, primary_link)[1]
        except ValueError:
            return None
        if not held_by:
            return None
        return "readable" if _readable_task(c, who, held_by) else "hidden"

    def _readable_task(c, who, task_id):
        try:
            auth.task(c, who, task_id)
        except Problem:
            return False
        return True

    def handoff_receipt(who):
        """Who reads back a task the person's own Assistant turn filed (privacy.as_the_person): the person, when it is
        private, first time and on a retry; the turn's own identity is still the one validated under the write lock."""
        person = privacy.as_the_person(who)
        return (lambda made: person if (made.get("task") or {}).get("private") else None) if person else None

    def rule_prepare_task(store_, bot, replay):
        body = replay.get("body") or {}
        return ask_ingest.preflight(
            store_, app.state.judge, bot, body.get("owner"), body.get("title"), body.get("body")
        )

    @org_rule_check.replayer("task_create", prepare=rule_prepare_task)
    def rule_replay_task(c, bot, hold, replay, ingest, gate=None):
        return task_create(
            c, bot, M.TaskFile.model_validate(replay["body"]), ingest=ingest, rule=org_rule_check.decided(gate)
        )

    @app.post("/api/v2/tasks/dry-run", **api_v2("Tasks", "checkTask", "The checks a create would fail; writes nothing"))
    def task_dry_run(request: Request, body: M.TaskFile):
        """The checks a create would fail, nothing written and no refusal counted."""
        from .mcp import task_dry_run as check

        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return check(c, auth, who, body)

    def task_update_one(c, caller, tid, body, rule=None):
        """`POST /api/v2/tasks/{tid}`: field edits only (H.task_update). Where the task is and who has it change by
        its own routes (task_moves_routes)."""
        # A person's request to Agent Ops can change a task they own or asked for, as that person.
        who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        if body.private is False and H.task_private(c, row) and (caller.role == "bot" or caller.task_actor):
            raise Problem(
                "privacy",
                "Only the human requester can make a private task company-visible",
                422 if who.role == "bot" else 403,
            )
        if who is not caller:
            H.event(
                c,
                caller.actor,
                "task.update_delegated",
                task_id,
                {"on_behalf_of": who.actor, "message_id": body.on_behalf_of},
            )
        # A bot's edit carries the version it was shown, refused as the engine refuses a stale one (task_changed, with
        # the version the task has now); a person may leave it out. H.task_update checks a given one again.
        from . import task_engine

        task_engine.check_version(c, who.actor, row, body.version)
        if body.due and (not (parsed := H.parse_ts(body.due)) or parsed.tzinfo is None):
            raise Problem("date", "due must be an ISO-8601 date/time with a timezone", 422)
        if body.goal_id and not G.goal(c, body.goal_id):
            raise Problem("not_found", "Unknown goal", 404)
        # The org rule check (backend/org_rule_check.py): held, the task stays as it is.
        rule_gate = (
            org_rule_check.apply(
                c,
                rule,
                rule_update_text(c, body, row),
                {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)},
            )
            if rule
            else None
        )
        if rule_gate and rule_gate.get("held"):
            return org_rule_check.answer(rule_gate)
        allowance = 0
        if rule_gate and rule_gate.get("warn"):
            # The warning line goes on the change itself, where the recipient reads it.
            warned = rule_warned_fields(rule_gate, body, row, approved=exact_approval(c, who.actor))
            body, allowance = body.model_copy(update=warned), rule_summary_allowance(rule_gate, warned)
        fields = body.model_dump(exclude={"version", "on_behalf_of"})
        fields["human_id"] = fields.pop("human")  # the column's name: `human` is the API's, as `owner` is
        if "size" in body.model_fields_set and body.size is None:
            fields["clear_size"] = True
        after = H.task_update(
            c,
            who.actor,
            task_id,
            **fields,
            mover=mover(c, who) or None,
            public_allowance=allowance,
            seen_version=body.version,
        )
        warnings = after.get("warnings") if after else None
        return {
            "task": task_view(H.task(c, task_id), c, visible_sql=auth.task_sql(c, who), who=who),
            **({"warnings": warnings} if warnings else {}),
            **org_rule_check.answer(rule_gate),
        }

    @app.post(
        "/api/v2/tasks/{tid}",
        **api_v2(
            "Tasks",
            "updateTask",
            "Edit a task's fields (title, due, labels, privacy, goal, note); a task has no description. A bot sends the version it was shown (409 task_changed otherwise); a person may leave it out. Where it is and who has it change by done, move and owner",
            TaskResult,
            responses={"422": ASK_REFUSED},
        ),
    )
    def update_task(request: Request, tid: str, body: M.TaskUpdate):
        caller = request.state.identity
        rc = rule_update_check(caller, tid, body)
        return mutate(request, body, lambda c: task_update_one(c, caller, tid, body, rule=rc))

    @org_rule_check.replayer("task_update")
    def rule_replay_update(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskUpdate.model_validate(replay["body"])
        row = H.task(c, replay["task"])
        if row:
            # `send` refused it if the task changed since the hold (its version is the check's object), so this is
            # the version it was checked at; the route's guard ran there too, as the bot and its run.
            body = body.model_copy(update={"version": row["version"]})
        return task_update_one(c, bot, replay["task"], body, rule=org_rule_check.decided(gate))

    def handoff_preflight(caller, row, owner, note=None):
        """Ask ingest for a bot handing its own task `row` to `owner`: judged like a task filed for them."""
        with store.read() as c:
            asked = H.task_request_text(c, row["id"])
        return ask_ingest.preflight(
            store,
            app.state.judge,
            caller,
            owner,
            row["title"],
            "\n\n".join(filter(None, (asked, note))),
            kind="task",
        )

    task_bulk_routes.register(app, auth, mover, mutate, store, task_filters, task_idents, task_view)

    register(app, auth, mutate, settings_admin)
    task_pipelines_routes.register(app, auth, mutate, store)
    task_factories_routes.register(app, auth, mutate, store)
    preferences_routes.register(app, auth, mutate, store)
    release_schedule_routes.register(app, auth, mutate, store)

    def readable_task(who, tid):
        """The task `who` may read, or None: what an ingest preflight outside the write judges."""
        with store.read() as c:
            try:
                return auth.task(c, who, auth.resolve_task(c, who, tid))
            except Problem:
                return None

    task_comments_routes.register(
        app,
        auth,
        comment_bot,
        comment_way_for,
        mover,
        mutate,
        rule_check,
        rule_comment_audience,
        send,
        settings,
        store,
        task_place,
        visual_view,
    )

    def task_followers_seen(c, who, row):
        """The task's followers this reader may be told of, or None for a task with no thread of its own."""
        tid = task_followers.thread_id(c, row)
        if not tid:
            return None
        rows = task_followers.listed(c, row)
        known = teammate_access.Known(c, who, auth)
        if rows and not known.member(tid):
            unseen = known.unseen_of(r["actor"] for r in rows)
            rows = [r for r in rows if r["actor"] not in unseen]
        return rows

    task_followers_routes.register(
        app, auth, mover, mutate, rule_check, rule_task_audience, store, task_followers_seen, task_place, visual_view
    )

    @app.post(
        "/api/v2/tasks/{tid}/delete",
        **api_v2(
            "Tasks",
            "deleteTask",
            "Delete a task made by mistake, with its conversation, to the trash: its human requester or a mover, signed in as themselves, whatever bots did on it: that work stays in place, hidden until a restore",
        ),
    )
    def task_delete(request: Request, tid: str, body: M.Empty):
        """A person deletes a task: its human requester, or anyone who may move any task. Bots and
        delegated sessions close instead. Closing reads as finished work, so a person can always delete,
        whatever bots did on the task: that work stays in place, unreadable while the task is gone
        (task_delete.delete_tasks keep_work). The task goes to the trash, from which it can be restored."""
        who = request.state.identity
        person_only(who, "deletes a task; close it instead")

        def work(c):
            from .task_delete import delete_tasks

            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            if row["requester_id"] != who.actor and not mover(c, who):
                raise Problem(
                    "forbidden", "Only the task's requester, or someone who may move any task, deletes it", 403
                )
            report = delete_tasks(c, [task_id], apply=True, actor=who.actor, keep_work=True)
            if not report["applied"]:
                reasons = ", ".join(sorted(report["refused"])) or "unknown task"
                raise Problem("has_work", "This task carries work (" + reasons + "); close it instead", 409)
            return {"deleted": task_id}

        return mutate(request, body, work)

    return cap_before_preflight, create_task, handoff_preflight, saved_retry, task_create
