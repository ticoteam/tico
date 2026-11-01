"""History replay: what happened between two moments, in time order, for a person to play back (`GET /api/v2/replay`).

Nothing new is recorded. The items are read from the history Tico already keeps: task creation and the
status and owner rows of `task_events`, messages (chats and task comments), runs (`turns`), each bot's
status rows (`bot_status_history`) and goal status changes (`goal_events`). Each source is read by its
time-bounded index and capped, so a long history costs what the window holds, not what the tables hold.
Each source also stops after SCAN_MAX rows read, shown or not (rows the viewer may not read, or outside the
scope, count too), and the answer is then cut short there like any other cap: `truncated`, with `next`.

Who sees what is decided by the checks the pages already use, one source at a time: the task list's
`auth.task_sql` for tasks and their history, `auth.conversation` and `task_privacy.message_readable`
for messages (a deleted message is left out), the bot turns page's checks for runs, `task_privacy.status`
on Read bots for status rows, and the Goals page's Read rule for goals. A replay never shows a viewer
anything one of those pages would not. Nothing is stored: the page's link carries the query.
"""

import heapq
from datetime import timedelta, timezone

from fastapi import Request

from . import task_privacy as privacy
from . import views
from .store import H, P, Problem

MAX_DAYS = 31
LIMIT = 5000
SCAN_MAX = 20000    # rows read per source, shown or not; past it the replay is cut short
TEXT_MAX = 1200
SNAPSHOT_MAX = 300
MESSAGE_KINDS = ("say", "ask", "answer")
TASK_FIELDS = ("status", "owner")


def stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def window(start, end):
    """(from, to) as stored timestamps: ISO times with a zone, `to` after `from`, at most MAX_DAYS apart."""
    first, last = H.parse_ts(start), H.parse_ts(end)
    if not first or not last or first.tzinfo is None or last.tzinfo is None:
        raise Problem("range", "from and to are ISO-8601 times with a zone", 422)
    if last <= first:
        raise Problem("range", "to is after from", 422)
    if last - first > timedelta(days=MAX_DAYS):
        raise Problem("range", f"A replay is at most {MAX_DAYS} days", 422)
    try:
        return stamp(first), stamp(last)
    except OverflowError:       # e.g. 0001-01-01T00:00:00+14:00 has no UTC time
        raise Problem("range", "from and to are out of range: their UTC times fall outside years 1 to 9999", 422) from None


def scope_of(bot=None, person=None, team=None):
    """One (kind, id) from the query's bot=, person= or team=; ("", "") is everyone."""
    given = [(k, v) for k, v in (("bot", bot), ("person", person), ("team", team)) if v]
    if len(given) > 1:
        raise Problem("scope", "Pass one of bot, person or team", 422)
    return given[0] if given else ("", "")


def clip_text(value, n=TEXT_MAX):
    value = str(value or "").strip()
    return value if len(value) <= n else value[: n - 1].rstrip() + "…"


def install(app, store, auth):
    def members(c, who, kind, ident):
        """The actors a scope covers, or None for everyone. A bot scope needs Read on the bot, as its pages do."""
        if not kind:
            return None
        if kind == "bot":
            auth.require_read(c, who, ident)
            return {"bot:" + ident}
        if kind == "person":
            if not H.human(c, ident):
                raise Problem("not_found", "Person not found", 404)
            return {"human:" + ident}
        roster = views.roster(c)
        groups = P.group_and_below(ident, roster)
        if not groups:
            raise Problem("not_found", "Team not found", 404)
        entries = views.entries(c)
        return ({"human:" + p["id"] for p in roster.get("people") or [] if p.get("team") in groups}
                | {"bot:" + slug for slug in entries if P.team_of(slug, entries, roster) in groups})

    def build(c, who, first, last, kind, ident):
        actors = members(c, who, kind, ident)
        reader = privacy.actor(who)
        within = (lambda *names: any(n in actors for n in names)) if actors is not None else (lambda *names: True)
        visible_sql = auth.task_sql(c, who)
        access = auth.bot_accesses(c, who)
        readable_bot = lambda slug: (access.get(slug) or auth.FULL)["read"]  # noqa: E731
        task_memo = {}

        def tasks_by_id(ids):
            """The rows of these tasks this viewer may read (the task list's own filter), memoized."""
            wanted = [tid for tid in set(ids) if tid and tid not in task_memo]
            for part in H.chunks(wanted):
                for tid in part:
                    task_memo[tid] = None
                for row in c.execute("SELECT id,title,owner_id,requester_id,status,created_at FROM tasks WHERE id IN (%s) AND (%s)"
                                     % (",".join("?" * len(part)), visible_sql), part):
                    task_memo[row["id"]] = dict(row)
            return {tid: task_memo.get(tid) for tid in ids}

        def task_ref(tid):
            row = tasks_by_id([tid]).get(tid) if tid else None
            return {"id": row["id"], "title": row["title"]} if row else None

        sources = []
        # Where each source stopped early. Items at or after the earliest of these may be missing from that source,
        # so the answer ends just before it and `next` starts there: continuing from `next` skips nothing.
        caps = []

        # Tasks made in the window, then their status and owner changes.
        created = []
        for scanned, row in enumerate(c.execute(f"SELECT id,title,owner_id,requester_id,status,created_at FROM tasks WHERE created_at>=? "
                                                f"AND created_at<? AND ({visible_sql}) ORDER BY created_at", (first, last)), 1):
            if scanned > SCAN_MAX:
                caps.append(row["created_at"])
                break
            task_memo[row["id"]] = dict(row)
            if within(row["owner_id"], row["requester_id"]):
                created.append(dict(row))
            if len(created) > LIMIT:
                caps.append(created[-1]["created_at"])
                break
        changes, seen = [], 0
        marks = ",".join("?" * len(TASK_FIELDS))
        cursor = c.execute(f"SELECT task_id,created_at,actor,field,old,new,note FROM task_events WHERE field IN ({marks}) "
                           "AND created_at>=? AND created_at<? ORDER BY created_at", (*TASK_FIELDS, first, last))
        cut = False
        while len(changes) <= LIMIT and not cut:
            batch = cursor.fetchmany(500)
            if not batch:
                break
            cut = seen + len(batch) > SCAN_MAX
            if cut:
                caps.append(batch[SCAN_MAX - seen]["created_at"])    # the first row not read
                batch = batch[:SCAN_MAX - seen]
            seen += len(batch)
            rows = tasks_by_id([r["task_id"] for r in batch])
            for event in batch:
                task = rows.get(event["task_id"])
                if event["field"] == "status" and not event["old"]:
                    continue        # the status a task is made with: its task.created says it
                if task and within(task["owner_id"], task["requester_id"], event["actor"]):
                    changes.append((dict(event), task))
        if len(changes) > LIMIT:
            caps.append(changes[-1][0]["created_at"])
        first_status = {}
        for event, task in changes:
            if event["field"] == "status":
                first_status.setdefault(task["id"], event["old"])
        sources.append([{"at": t["created_at"], "kind": "task.created", "who": t["requester_id"], "owner_id": t["owner_id"],
                         "task": {"id": t["id"], "title": t["title"]},
                         "status": first_status.get(t["id"]) or t["status"] or "open"} for t in created])
        items = []
        for event, task in changes:
            note = event["note"] or ""
            if note and not privacy.content_readable(c, reader, note):
                note = ""
            item = {"at": event["created_at"], "kind": "task." + event["field"], "who": event["actor"],
                    "task": {"id": task["id"], "title": task["title"]}, "old": event["old"], "new": event["new"]}
            if note:
                item["text"] = clip_text(note, 400)
            items.append(item)
        sources.append(items)

        # Messages: the rooms with any message since `from`, each read by its (conversation, created) index and
        # merged in time order, so a cap (items or rows read) cuts every room at the same moment.
        rooms = []
        for conv in H._rows(c.execute("SELECT * FROM conversations WHERE COALESCE(last_message_at, created_at)>=?", (first,))):
            try:
                auth.conversation(c, who, conv["id"])
            except Problem:
                continue
            conv["participants"] = H._json(conv.get("participants"), []) or []
            rooms.append(conv)
        marks = ",".join("?" * len(MESSAGE_KINDS))

        def room_rows(conv):
            for row in c.execute(f"SELECT * FROM messages WHERE conversation_id=? AND created_at>=? AND created_at<? "
                                 f"AND kind IN ({marks}) AND deleted_at IS NULL ORDER BY created_at",
                                 (conv["id"], first, last, *MESSAGE_KINDS)):
                yield row["created_at"], conv, row

        items = []
        for scanned, (_, conv, row) in enumerate(heapq.merge(*(room_rows(conv) for conv in rooms), key=lambda r: r[0]), 1):
            if scanned > SCAN_MAX or len(items) > LIMIT:
                caps.append(row["created_at"])
                break
            if not within(row["sender_id"], row["recipient_id"]):
                continue
            if not privacy.message_readable(c, reader, row, current=True):
                continue
            message = {**dict(row), "refs": H._json(row["refs"], {}) or {}}
            tid = H.message_task_id(message, conv)
            comment = bool(conv.get("task_id")) or H.is_comment(message)
            item = {"at": row["created_at"], "kind": "task.comment" if comment and tid else "chat.message",
                    "who": row["sender_id"], "to": row["recipient_id"], "room": conv["id"],
                    "text": clip_text(row["body"])}
            ref = task_ref(tid)
            if ref:
                item["task"] = ref
            items.append(item)
        sources.append(items)

        # Runs, with the bot turns page's checks: Read on the bot, then its message, its room and its tasks.
        items = []
        for scanned, turn in enumerate(c.execute("SELECT id,bot,started_at,finished_at,exit,summary,message_id,task_id FROM turns "
                                                 "WHERE started_at>=? AND started_at<? ORDER BY started_at", (first, last)), 1):
            if scanned > SCAN_MAX:
                caps.append(turn["started_at"])
                break
            if not readable_bot(turn["bot"]) or not within("bot:" + turn["bot"]):
                continue
            msg = H.message(c, turn["message_id"]) if turn["message_id"] else None
            if not msg:
                continue
            try:
                auth.conversation(c, who, msg["conversation_id"])
                privacy.require_message(c, who, msg)
                privacy.require_attempt(c, who, turn["id"])
            except Problem:
                continue
            ref = task_ref(turn["task_id"] or H.message_task_id(msg, H.conversation(c, msg["conversation_id"])))
            start = {"at": turn["started_at"], "kind": "run.started", "who": "bot:" + turn["bot"], "bot": turn["bot"]}
            if ref:
                start["task"] = ref
            items.append(start)
            if turn["finished_at"] and turn["finished_at"] < last:
                done = {**start, "at": turn["finished_at"], "kind": "run.finished", "exit": turn["exit"] or "",
                        "text": clip_text(turn["summary"], 600)}
                items.append(done)
            if len(items) > LIMIT:
                caps.append(turn["started_at"])
                break
        sources.append(items)

        # Each Read bot's status rows, with what `task_privacy.status` hides from this viewer.
        slugs = sorted(row["id"] for row in H.bots(c) if readable_bot(row["id"]) and within("bot:" + row["id"]))
        inputs = privacy.status_inputs(c, slugs) if slugs else {}
        items, start_states = [], {}
        for slug in slugs:
            rows = H._rows(c.execute("SELECT bot,state,focus,task_id,started_at FROM bot_status_history WHERE bot=? "
                                     "AND started_at>=? AND started_at<? ORDER BY started_at LIMIT ?", (slug, first, last, LIMIT + 1)))
            current = H._one(c, "SELECT bot,state,focus,task_id,changed_at FROM bot_status WHERE bot=?", (slug,))
            if len(rows) > LIMIT:
                caps.append(rows[-1]["since"])
            if current and first <= (current["changed_at"] or "") < last:
                rows.append(current)
            before = H._one(c, "SELECT bot,state,focus,task_id,started_at FROM bot_status_history WHERE bot=? AND started_at<? "
                               "ORDER BY started_at DESC LIMIT 1", (slug, first))
            if current and (current["changed_at"] or "") < first:
                before = current
            if before:
                shown = privacy.status(c, who, before, **inputs[slug])
                start_states[slug] = {"state": shown["state"], "focus": clip_text(shown.get("focus"), 200)}
            for row in rows:
                shown = privacy.status(c, who, row, **inputs[slug])
                items.append({"at": row["since"], "kind": "bot.status", "who": "bot:" + slug, "bot": slug,
                              "state": shown["state"], "text": clip_text(shown.get("focus"), 200)})
        sources.append(items)

        # Goals: a bot's own goals for whoever may Read it (the Goals page's rule); the company's for everyone.
        hidden = {"bot:" + slug for slug in auth.unreadable_bots(c, who)}
        items = []
        for goal in H._rows(c.execute("SELECT id,title,owner_id FROM goals")):
            if goal["owner_id"] in hidden:
                continue
            for event in c.execute("SELECT created_at,actor,field,old,new FROM goal_events WHERE goal_id=? AND field='status' "
                                   "AND created_at>=? AND created_at<? ORDER BY created_at", (goal["id"], first, last)):
                if within(goal["owner_id"], event["actor"]):
                    items.append({"at": event["created_at"], "kind": "goal.status", "who": event["actor"],
                                  "goal": {"id": goal["id"], "title": goal["title"]},
                                  "old": event["old"], "new": event["new"]})
        sources.append(items)

        merged = sorted((item for source in sources for item in source), key=lambda item: item["at"])
        if len(merged) > LIMIT:
            caps.append(merged[LIMIT]["at"])
        cutoff = min(caps) if caps else None
        if cutoff:
            merged = [item for item in merged if item["at"] < cutoff]

        # The board as it stood at `from`: tasks open then, each in the status it had.
        candidates = {}
        for row in c.execute(f"SELECT id,title,owner_id,requester_id,status,created_at FROM tasks WHERE created_at<? "
                             f"AND status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) AND ({visible_sql}) "
                             f"ORDER BY created_at DESC LIMIT ?", (first, *H.ACTIVE_STATUSES, SNAPSHOT_MAX)):
            candidates[row["id"]] = dict(row)
        lanes = ",".join("?" * len(H.TASK_LANES))
        for row in c.execute(f"SELECT id,title,owner_id,requester_id,status,created_at FROM tasks WHERE lane IN ({lanes}) "
                             f"AND status IN ('done','closed') AND COALESCE(closed_at,done_at,updated_at,created_at)>=? "
                             f"AND created_at<? AND ({visible_sql}) LIMIT ?", (*H.TASK_LANES, first, first, SNAPSHOT_MAX)):
            candidates.setdefault(row["id"], dict(row))
        board = []
        for task in candidates.values():
            if not within(task["owner_id"], task["requester_id"]):
                continue
            later = c.execute("SELECT old FROM task_events WHERE task_id=? AND field='status' AND created_at>=? ORDER BY created_at LIMIT 1",
                              (task["id"], first)).fetchone()
            status = (later["old"] if later and later["old"] else task["status"]) or "open"
            if status in H.ACTIVE_STATUSES:
                board.append({"id": task["id"], "title": task["title"], "owner_id": task["owner_id"], "status": status})
        board = sorted(board, key=lambda t: t["id"])[:SNAPSHOT_MAX]

        # Names for every actor shown, so a reader (a page, or a bot writing a recap) needs nothing else.
        shown = {item.get("who") for item in merged} | {item.get("to") for item in merged} \
            | {item.get("owner_id") for item in merged} | {t["owner_id"] for t in board} \
            | {"bot:" + slug for slug in start_states}
        bots = {row["id"]: row for row in H.bots(c)}
        people = {p["id"]: p for p in views.roster(c).get("people") or []}
        names = {}
        for actor in sorted(a for a in shown if a):
            ident = H.actor_id(actor)
            if actor.startswith("bot:") and ident in bots:
                names[actor] = bots[ident].get("display_name") or bots[ident]["slug"]
            elif actor.startswith("human:"):
                names[actor] = (people.get(ident) or H.human(c, ident) or {}).get("name") or ident
        return {"from": first, "to": last, "scope": f"{kind}:{ident}" if kind else "", "items": merged,
                "truncated": bool(cutoff), "next": cutoff,
                "start": {"tasks": board, "bots": start_states}, "names": names}

    @app.get("/api/v2/replay")
    def replay(request: Request, bot: str | None = None, person: str | None = None, team: str | None = None):
        """Time-ordered history items for `from`..`to` (at most MAX_DAYS, LIMIT items; when `truncated`,
        `next` is the `from` that continues with nothing skipped), the board and bot states at `from`, and the names of everyone shown."""
        who = request.state.identity
        auth.domain(who)        # a person or a bot, each with its own rights; never a runner's credential
        with store.read_transaction() as c:
            first, last = window(request.query_params.get("from"), request.query_params.get("to"))
            kind, ident = scope_of(bot, person, team)
            if kind == "bot":
                ident = (H.resolve_bot(c, ident) or {}).get("id", ident)
            return build(c, who, first, last, kind, ident)
