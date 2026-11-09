"""Update and message routes: updates, reads, replies, conversation messages."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import ask_ingest
from . import batch_routes
import json
from . import org_rule_check
import re
from . import updates
from . import views


def register(
    app,
    auth,
    may_edit_routines,
    message_checks,
    message_write,
    mutate,
    owner_only,
    rule_check,
    rule_message_audience,
    send,
    settings,
    store,
    task_view,
):
    # ------------------------------------------------------------------ updates (backend/updates.py)
    def update_visible(c, who, mine=False, scope=None):
        """The authors whose updates `who` may read (backend/updates.py visible_authors)."""
        return updates.visible_authors(c, auth, who, mine, scope)

    def update_scope(scope):
        if scope not in (None, "", "all", "direct"):
            raise Problem("scope", "scope is all or direct", 422)
        return scope or None

    def readable_update_ids(c, who, ids):
        """Validate the whole personal batch before a read/archive write can mutate any row."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return ids
        readable, meetings = update_visible(c, who), updates.meeting_reader(who)
        rows = c.execute(
            "SELECT id, bot, meetings FROM updates WHERE id IN (" + ",".join("?" * len(ids)) + ")", ids
        ).fetchall()
        if len(rows) != len(ids) or any(
            row["bot"] not in readable or (row["meetings"] and not meetings) for row in rows
        ):
            raise Problem("not_found", "Update not found", 404)
        return ids

    def require_update(c, who, row):
        """A bot's update takes the bot's own gate (404 unseen, 403 seen without Read); a person's
        is for everyone on the chart, so anyone else gets 404. One flagged `meetings` is not found for
        anyone but a meeting reader (`updates.meeting_reader`)."""
        if updates.withheld(c, who, row["id"]):
            raise Problem("not_found", "Update not found", 404)
        if not updates.is_person(row["bot"]):
            auth.require_read(c, who, row["bot"], "Update not found")
        elif row["bot"] not in update_visible(c, who):
            raise Problem("not_found", "Update not found", 404)

    @app.get("/api/v2/updates")
    def updates_list(
        request: Request,
        kind: str | None = None,
        bot: str | None = None,
        unread: bool = False,
        before: str | None = None,
        limit: int = 40,
        mine: bool = False,
        archive: bool = False,
        include_archive: bool = False,
        scope: str | None = None,
    ):
        who = request.state.identity
        auth.domain(who)
        if kind and kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            return updates.listing(
                c,
                who.actor,
                update_visible(c, who, mine, update_scope(scope)),
                kind=kind,
                bot=bot,
                unread=unread,
                before=before,
                limit=max(1, min(limit, 100)),
                archive=archive,
                include_archive=include_archive,
                may_read=update_visible(c, who),
                meetings=updates.meeting_reader(who),
            )

    @app.get("/api/v2/updates/unread")
    def updates_unread(request: Request, mine: bool = False, scope: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            from .meetings import pending_count

            return {
                "unread": updates.count_unread(
                    c,
                    who.actor,
                    update_visible(c, who, mine, update_scope(scope)),
                    meetings=updates.meeting_reader(who),
                ),
                "meetings_pending": pending_count(c, who),
            }

    @app.get("/api/v2/updates/tree")
    def updates_tree(request: Request, kind: str = "daily", day: str | None = None, scope: str | None = None):
        """One day's updates nested by the org chart: below you by default, or the whole chart."""
        who = request.state.identity
        auth.domain(who)
        if kind not in updates.KINDS:
            raise Problem("kind", "kind is daily or weekly", 422)
        if scope not in (None, "", "below", "all"):
            raise Problem("scope", "scope is below or all", 422)
        if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise Problem("day", "day is YYYY-MM-DD", 422)
        with store.read() as c:
            return updates.tree(
                c,
                who.actor,
                update_visible(c, who),
                kind,
                day or None,
                scope or None,
                meetings=updates.meeting_reader(who),
            )

    @app.get("/api/v2/updates/{uid}")
    def update_show(request: Request, uid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            require_update(c, who, row)
            read = c.execute("SELECT 1 FROM update_reads WHERE update_id=? AND actor=?", (uid, who.actor)).fetchone()
            # A reply lands in the replier's own room with the bot; show only rooms this caller may read.
            rooms_ok = {}

            def readable(message):
                cid = message["conversation_id"]
                if cid not in rooms_ok:
                    try:
                        auth.conversation(c, who, cid)
                        rooms_ok[cid] = True
                    except Problem:
                        rooms_ok[cid] = False
                return rooms_ok[cid]

            thread = [m for m in updates.thread(c, uid) if readable(m)]
            row = updates.with_inputs(c, row, update_visible(c, who), updates.meeting_reader(who))
            return {
                "update": {**row, "read": bool(read), "archived": updates.archived(c, who.actor, uid)},
                "thread": thread,
            }

    def update_kind(body):
        """The kind of update a post stores (updates.effective_kind): a daily drops any slides, a week in review its
        bullets."""
        return updates.effective_kind(body.kind, body.slides, body.day)

    def update_text(body):
        """What an update says, as the rule check reads it: what the post stores, a daily's bullets or every word of a
        week in review's slides."""
        if update_kind(body) == "weekly":
            slides = body.slides.model_dump(exclude_none=True) if body.slides else None
            return json.dumps(slides, sort_keys=True) if slides else ""
        return body.body.strip()

    def update_write(c, who, body, rc=None, replayed=False, named_day=True):
        """One update, as `POST /api/v2/updates` posts it and Send anyway replays it (`replayed`): a bot's is checked
        against the org rules for the whole company, which reads it. Held, nothing is posted; warned, the line leads
        the update (a week in review's goal, its headline). `body` names its day and kind (update_fixed), so a replay
        posts the update that was held, or nothing: a day that can no longer be posted is a 409, the hold kept. A live
        post that named no day (`named_day` False) is for the day it is written, since the rule check may have run past
        midnight; its kind stays as it arrived."""
        # A bot posts its own; a person posts their own, for today, as `human:<id>`. A person's
        # draft is written by the server (updates.write_draft), never posted through here.
        if who.role == "bot":
            author = H.actor_id(who.actor)
        elif who.role in ("owner", "human") and not who.via:
            author = who.actor
        else:
            raise Problem("identity", "A bot or a person posts their own update", 403)
        if not replayed and not named_day:
            body = body.model_copy(update={"day": updates.today()})
        slides = body.slides.model_dump(exclude_none=True) if body.slides else None
        try:
            updates.check_day(c, author, body.day)
        except Problem:
            if not replayed:
                raise
            raise Problem(
                "changed",
                f"The {body.kind or 'held'} update for {body.day} can no longer be posted; "
                "it stays held. Post it again for today to check it.",
                409,
            ) from None
        rule = org_rule_check.apply(c, rc, update_text(body), {"body": body.model_dump(mode="json")})
        if rule and rule.get("held"):
            return org_rule_check.answer(rule)
        # The warn line goes on after the update's own words pass the writing checks (updates.post's `warning`).
        return {
            "update": updates.post(
                c,
                author,
                body.body,
                kind=body.kind,
                day=body.day,
                slides=slides,
                replace_draft=body.replace_draft,
                reader=updates.meeting_reader(who),
                warning=org_rule_check.warn_line(rule),
                learnings_saved=body.learnings_saved,
                suggestions_seen=body.suggestions_seen,
                delivery_batch_id=body.delivery_batch_id,
            ),
            **org_rule_check.answer(rule),
        }

    def update_skip(c, who, body):
        """A bot's nothing-new answer for its request (`updates.skip`): nothing is posted and the request finishes skipped,
        never retried. A person's draft is skipped by the server (`updates.write_draft`), not here."""
        if who.role != "bot":
            raise Problem("identity", "A bot skips its own update", 403)
        bot = H.actor_id(who.actor)
        day = body.day or updates.today()
        updates.check_day(c, bot, day)
        kind = updates.effective_kind(body.kind, None, day)
        updates.skip(c, bot, kind, day)
        return {"skipped": True, "kind": kind, "day": day, "reason": updates.NOTHING_NEW}

    def update_fixed(body):
        """The update a post means, its day and kind named, before anything is checked: a hold keeps them, so Send
        anyway posts that day's update of that kind, never today's or a week in review a Friday would make it."""
        from datetime import date

        day = body.day or updates.today()
        try:
            date.fromisoformat(day)
        except ValueError:
            raise Problem("day", "An update is for today, or for the day Tico asked you to redo", 422) from None
        return body.model_copy(update={"day": day, "kind": updates.effective_kind(body.kind, body.slides, day)})

    @app.post("/api/v2/updates")
    def update_post(request: Request, body: M.UpdatePost):
        who = request.state.identity
        if body.skip:  # nothing is posted, so there is nothing for the org rules to check
            return mutate(request, body, lambda c: update_skip(c, who, body))
        fixed = update_fixed(body)
        rc = rule_check(who, "update", update_text(fixed), lambda c: org_rule_check.company(c))
        return mutate(request, body, lambda c: update_write(c, who, fixed, rc, named_day=body.day is not None))

    @org_rule_check.replayer("update", audience=lambda c, bot, replay: org_rule_check.company(c))
    def rule_replay_update_post(c, bot, hold, replay, ingest, gate=None):
        return update_write(
            c, bot, M.UpdatePost.model_validate(replay["body"]), org_rule_check.decided(gate), replayed=True
        )

    @app.post("/api/v2/updates/redo")
    def update_redo(request: Request, body: M.UpdateRedo):
        """Ask the bots again for a past day's update in the current shape (a week in review as slides)."""
        who = request.state.identity
        owner_only(who, "asks the bots to redo an update")

        def work(c):
            chosen = None if body.bots is None else [(H.resolve_bot(c, b) or {}).get("id", b) for b in body.bots]
            return {
                "kind": body.kind,
                "day": body.day,
                "bots": updates.redo(c, body.kind, body.day, chosen, body.managers),
            }

        return mutate(request, body, work)

    @app.post("/api/v2/updates/read")
    def updates_read(request: Request, body: M.UpdateRead):
        who = request.state.identity

        def work(c):
            views.human_only(who)
            if body.all:
                visible = update_visible(c, who)
                ids = updates.inbox_ids(
                    c,
                    who.actor,
                    visible,
                    created_after=H.shift(H.now(), days=-30),
                    meetings=updates.meeting_reader(who),
                )
            else:
                ids = readable_update_ids(c, who, body.ids)
            return {"marked": updates.mark(c, who.actor, ids, read=body.read), "read": body.read}

        return mutate(request, body, work)

    @app.post("/api/v2/updates/archive")
    def updates_archive(request: Request, body: M.UpdateArchive):
        who = request.state.identity
        auth.domain(who)

        def work(c):
            views.human_only(who)
            ids = readable_update_ids(c, who, body.ids)
            updates.set_archived(c, who.actor, ids, archived=body.archived)
            for uid in ids:
                H.event(c, who.actor, "update.archive" if body.archived else "update.restore", uid, {})
            return {
                "updated": len(ids),
                "archived": body.archived,
                "unread": updates.count_unread(
                    c, who.actor, update_visible(c, who), meetings=updates.meeting_reader(who)
                ),
            }

        return mutate(request, body, work)

    @app.post("/api/v2/updates/{uid}/reply")
    def update_reply(request: Request, uid: str, body: M.UpdateReply):
        who = request.state.identity

        def work(c):
            views.human_only(who)
            row = updates.one(c, uid)
            if not row:
                raise Problem("not_found", "Update not found", 404)
            require_update(c, who, row)
            if updates.is_person(row["bot"]):
                raise Problem("reply", "A person's update takes no replies here; message them instead", 422)
            # A reply is both a comment on the update and a message in the bot's
            # chat, so the bot's session gets it and it shows in both places.
            text = f"Re your update \"{row['headline']}\": {body.text}"
            message = send(c, who, M.MessageCreate(to="bot:" + row["bot"], text=text, refs={"update": uid}))
            updates.mark(c, who.actor, [uid], read=True)
            return {"message": message, "thread": updates.thread(c, uid)}

        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/updates")
    def update_settings_show(request: Request, bot: str):
        auth.domain(request.state.identity)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_read(c, request.state.identity, bot)
            return {"bot": bot, **updates.settings(c, bot)}

    @app.post("/api/v2/bots/{bot}/updates")
    def update_settings(request: Request, bot: str, body: M.UpdateSettings):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            may_edit_routines(c, who, bot)
            return {"bot": bot, **updates.set_settings(c, bot, body.daily, body.weekly)}

        return mutate(request, body, work)

    batch_routes.register(app, auth, mutate, send, store, task_view)

    def conversation_target(c, who, cid):
        conv = auth.conversation(c, who, cid, write=True)
        from . import threads

        people = threads.legacy_participants(c, conv)
        target = next((p for p in people if p != who.actor and p.startswith("bot:")), None)
        target = target or next((p for p in people if p != who.actor), None)
        if not target:
            raise Problem("recipient", "This conversation has no other recipient", 422)
        return target

    @app.post("/api/v2/conversations/{cid}/messages")
    def reply(request: Request, cid: str, body: M.ChatCreate):
        who = request.state.identity
        with store.read() as c:
            try:
                target = conversation_target(c, who, cid)
            except Problem:
                target = None  # the write refuses it, with its own reason
        message = M.MessageCreate(
            to=target or "-",
            text=body.text,
            conversation_id=cid,
            refs=body.refs,
            command=body.command,
            attachments=body.attachments,
            delivery=body.delivery,
            defer_for=body.defer_for,
        )
        # The same checks as `/messages`: a bot's message to this conversation is one message, however it is posted.
        rc, ingest = message_checks(who, message) if target else (None, None)

        def work(c):
            sent = message.model_copy(update={"to": conversation_target(c, who, cid)})
            check = rc
            if sent.to != target:
                # The recipient changed after the check: delivered unchecked (no engine call in the write), recorded.
                author = org_rule_check.author_of(who, settings.assistant_bot)
                recipients = rule_message_audience(c, who, sent)
                place = org_rule_check.place_for(c, author, recipients, sent.conversation_id or "") if author else None
                check = org_rule_check.changed(c, rc, author, "message", recipients, place=place)
            if ingest and sent.to != target:
                ask_ingest.skipped(c, ingest, "target")  # judged for the first recipient, delivered to another
            out = message_write(c, who, sent, check, ingest if sent.to == target else None)
            if out.get("held"):
                return out
            extra = {k: out.pop(k) for k in ("rule_warning",) if k in out}
            return {"message": out, **extra}

        return ask_ingest.judged(store, ingest, lambda: mutate(request, body, work))
