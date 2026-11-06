"""Task follower routes, comment edit, delete and resolve."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import org_rule_check
from . import task_privacy as privacy
from . import task_followers


def register(
    app, auth, mover, mutate, rule_check, rule_task_audience, store, task_followers_seen, task_place, visual_view
):
    def followers_answer(c, who, task_id):
        rows = task_followers_seen(c, who, H.task(c, task_id))
        mine = next((r for r in rows or [] if r["actor"] == who.actor), None)
        return {
            "task_id": task_id,
            "following": bool(mine),
            "notify": mine["notify"] if mine else None,
            "followers": rows,
        }

    @app.get("/api/v2/tasks/{tid}/followers")
    def get_task_followers(request: Request, tid: str):
        """Who follows the task, each at their level (docs/tasks.md, Followers)."""
        with store.read() as c:
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            return followers_answer(c, who, task_id)

    @app.put("/api/v2/tasks/{tid}/followers")
    def put_task_followers(request: Request, tid: str, body: M.TaskFollower):
        """Follow or unfollow the task, or set a level: yourself (no actor), or anyone who sees it when you may change
        the task."""

        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            from . import reach

            task_followers.change(
                c,
                who.actor,
                task_id,
                body.actor,
                following=body.following,
                notify=body.notify,
                mover=mover(c, who) or None,
                check=lambda target: reach.check(c, who, target, "task", task_id=task_id),
            )
            return followers_answer(c, who, task_id)

        return mutate(request, body, work)

    @app.delete("/api/v2/tasks/{tid}/links/{link_id}")
    def delete_task_link(request: Request, tid: str, link_id: M.ID):
        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            if who.via == "botops" and not who.confirmed:
                # BotOps acting for a person unlinks only on that person's own tap (a Confirm card).
                return app.state.botops_propose(
                    c,
                    who,
                    "DELETE",
                    f"/api/v2/tasks/{tid}/links/{link_id}",
                    {},
                    "Remove a link from “" + H.task(c, task_id)["title"] + "”",
                )
            H.task_unlink(c, who.actor, task_id, link_id, mover=mover(c, who))
            return {"links": H.task_links(c, task_id)}

        return mutate(request, M.Empty(), work)

    def own_comment(c, who, tid, mid, allow_deleted=False):
        """The task that comment `mid` is on, when the caller wrote the comment; a refusal otherwise."""
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        msg = H.comment_on(c, row, mid)
        if not msg or msg.get("deleted_at") and not allow_deleted:
            raise Problem("not_found", "No such comment on this task", 404)
        if not msg.get("deleted_at"):
            privacy.require_message(c, who, msg)
        # The Assistant and BotOps act with a person's rights, but the words are the person's own.
        if who.via:
            raise Problem("forbidden", "Only a comment's author edits or deletes it, signed in as themselves", 403)
        if not H.is_comment(msg):
            raise Problem(
                "forbidden",
                "Only a comment can be edited or deleted, not a question, an answer, a notice or a chat message",
                403,
            )
        if msg["sender_id"] != who.actor:
            raise Problem("forbidden", "Only the person or bot who wrote a comment can edit or delete it", 403)
        return row

    @app.post("/api/v2/tasks/{tid}/comments/{mid}")
    def task_comment_edit(request: Request, tid: str, mid: str, body: M.TaskComment):
        """The author changes a comment's text. It wakes nobody and is not sent again; the new text
        is new words to the bot on the task, so it needs what a new comment needs."""
        who = request.state.identity
        if body.ask is not None or body.attachments:
            raise Problem("validation", "An edit changes only a plain comment's text", 422)
        rc = rule_check(
            who,
            "comment_edit",
            body.text,
            lambda c: rule_task_audience(c, auth.task(c, who, auth.resolve_task(c, who, tid)), who.actor, body.text),
            obj=mid,
            place=task_place(who, tid),
        )
        return mutate(
            request,
            body,
            lambda c: comment_edit_write(c, who, tid, mid, body, rc),
            check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True),
        )

    def comment_edit_write(c, who, tid, mid, body, rc=None):
        row = own_comment(c, who, tid, mid)
        from .task_review import comment_rights

        comment_rights(c, auth, who, row["id"])
        # Held, the comment keeps its old text.
        rule = org_rule_check.apply(
            c, rc, body.text, {"task": row["id"], "message": mid, "body": body.model_dump(mode="json", by_alias=True)}
        )
        if rule and rule.get("held"):
            return org_rule_check.held_answer(c, who, rule, auth=auth)
        msg = H.task_comment_edit(
            c, who.actor, row["id"], mid, org_rule_check.warned_text(rule, body.text), approval_id=body.approval_id
        )
        return {
            "comment": visual_view(c, who, [msg])[0],
            "comments": visual_view(c, who, H.task_comments(c, row["id"], actor=privacy.actor(who))),
            "woke": False,
            **org_rule_check.answer(rule),
        }

    @org_rule_check.replayer("comment_edit")
    def rule_replay_comment_edit(c, bot, hold, replay, ingest, gate=None):
        body = M.TaskComment.model_validate(replay["body"])
        return comment_edit_write(c, bot, replay["task"], replay["message"], body, org_rule_check.decided(gate))

    @app.post("/api/v2/tasks/{tid}/comments/{mid}/delete")
    def task_comment_delete(request: Request, tid: str, mid: str, body: M.Empty):
        """The author takes a comment back: never listed again, never handed to a bot."""
        who = request.state.identity

        def work(c):
            row = own_comment(c, who, tid, mid)
            msg = H.task_comment_delete(c, who.actor, row["id"], mid)
            return {
                "comment": visual_view(c, request.state.identity, [msg])[0],
                "comments": visual_view(
                    c,
                    request.state.identity,
                    H.task_comments(c, row["id"], actor=privacy.actor(request.state.identity)),
                ),
                "woke": False,
            }

        return mutate(request, body, work, check=lambda c: own_comment(c, who, tid, mid, allow_deleted=True))

    @app.post("/api/v2/tasks/{tid}/comments/{mid}/resolve")
    def task_ask_resolve(request: Request, tid: str, mid: str, body: M.TaskAskResolve):
        """Settle an open question answered somewhere else (its asker or the task's owner bot, quoting the
        person), or withdraw it (its asker). It leaves every Needs you list; it wakes nobody."""
        who = request.state.identity

        def readable(c):
            task_id = auth.resolve_task(c, who, tid)
            row = auth.task(c, who, task_id)
            asked = H.comment_on(c, row, mid)
            if not asked or asked.get("deleted_at") or asked["kind"] != "ask":
                raise Problem("not_found", "No such question on this task", 404)
            privacy.require_message(c, who, asked)
            return row

        def work(c):
            row = readable(c)
            out = H.task_ask_resolve(c, who.actor, row["id"], mid, body.quote)
            return {
                "comment": visual_view(c, who, [out["answer"] or out["ask"]])[0],
                "comments": visual_view(c, who, H.task_comments(c, row["id"], actor=privacy.actor(who))),
                "woke": False,
            }

        return mutate(request, body, work, check=readable)
