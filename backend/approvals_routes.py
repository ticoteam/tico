"""Approval routes: preferences, approval requests and decisions."""

import json
import re
from fastapi import Request
from . import lifecycle, models as M, proposals, strategy_bot, task_privacy as privacy, views
from .store import H, Problem, encode


# The defers the web's Send can keep (preference compose.send_way): the durations its menu offers (ui/app/pill.js).
SEND_WAY_DEFERS = frozenset("defer:" + f for f in ("30m", "1h", "4h", "24h", "2d", "7d", "never"))


def register(app, auth, mutate, settings, store):
    @app.get("/api/v2/preferences/{key}")
    def preference(request: Request, key: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (who.actor, key)).fetchone()
            return {"key": key, "value": json.loads(row[0]) if row else None}

    @app.post("/api/v2/preferences/{key}")
    def set_preference(request: Request, key: str, body: M.Preference):
        who = request.state.identity
        auth.domain(who)
        if key.startswith("assistant."):
            raise Problem("forbidden", "Your Assistant's settings are set in Settings > General", 403)
        if key == "meetings.auto_share":
            views.human_only(who)
            if not isinstance(body.value, bool):
                raise Problem("validation", "meetings.auto_share is a boolean", 422)
        # The way the web's Send goes (ui/app/pill.js): read by any client; sends that name no delivery ignore it.
        if key == "compose.send_way" and not (
            body.value in ("steer", "queue") or isinstance(body.value, str) and body.value in SEND_WAY_DEFERS
        ):
            raise Problem("validation", "compose.send_way is steer, queue or defer:<30m|1h|4h|24h|2d|7d|never>", 422)
        # Hide from my chart on the Strategy Bot: a person's own view
        if key == strategy_bot.HIDE_PREF:
            views.human_only(who)
            if not isinstance(body.value, bool):
                raise Problem("validation", strategy_bot.HIDE_PREF + " is a boolean", 422)
        if not re.fullmatch(r"[a-z0-9_.-]{1,64}", key):
            raise Problem("kind", "A preference key is lower-case letters, digits, dots, dashes", 422)
        if not request.headers.get("idempotency-key"):
            # An unchanged value (a page re-saving what it read) answers from a read: no writer wait.
            with store.read() as c:
                same = c.execute(
                    "SELECT 1 FROM preferences WHERE actor=? AND key=? AND value=?",
                    (who.actor, key, encode(body.value)),
                ).fetchone()
                if same:
                    privacy.guard_write(c, getattr(request.state, "privacy_source", who), request.url.path, auth)
            if same:
                return {"key": key, "value": body.value}

        def work(c):
            at = H.now()
            c.execute(
                "INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (who.actor, key, encode(body.value), at),
            )
            if key == strategy_bot.HIDE_PREF:
                strategy_bot.mirror_hide(c, who.actor, encode(body.value), at)
            return {"key": key, "value": body.value}

        return mutate(request, body, work)

    @app.post("/api/v2/approvals")
    def approval_request(request: Request, body: M.ApprovalCreate):
        who = request.state.identity
        if body.kind == "send":
            raise Problem(
                "email_send",
                "Email approvals come from the frozen send queue; submit the email to /api/v2/email/sends",
                422,
            )
        if body.kind == "support":
            from .support import require_on

            require_on(settings)  # nothing to draft where nothing can be sent (docs/support.md)

        def work(c):
            auth.domain(who)
            # What an approval clears is a send, a spend or a publication; an onboarding bot does none of them.
            lifecycle.refuse_send(c, who, None, what="anything")
            if body.task_id:
                auth.task(c, who, body.task_id)
            to = None
            from .company_page import editions

            if editions.own_click_approval(body.payload):
                # Only the Company page's own routes ask for these: nobody words what an outside edition approves.
                raise Problem("forbidden", "Ask for this from the Company page", 403)
            if body.kind in proposals.KINDS:  # a redesign proposal: checked now, decided by the top of the org
                proposals.check(c, body.kind, body.payload)
                to = proposals.decider(c)
            row = H.approval_request(
                c, who.actor, body.kind, body.payload, body.task_id, supersedes=body.supersedes_approval, to=to
            )
            # A proposal answers as a bot's cap PUT does too: the approval's id and that it waits on a person.
            return {**row, "approval": row["id"], "status": "pending"} if body.kind in proposals.KINDS else row

        draft = mutate(request, body, work)
        if body.kind == "support" and who.role == "bot":
            # The team rule `bot_reports_direct` (backend/support.py send_by_rule) decides the draft as the install's default
            # person and sends it now; off, or refused by the privacy check, it waits for a person. A same-key retry replays
            # the draft, and send_by_rule answers with the same ticket.
            def privacy_now(c):
                privacy.guard_write(c, getattr(request.state, "privacy_source", who), request.url.path, auth)

            sent = app.state.support.send_by_rule(who, draft["id"], check=privacy_now)
            if sent is not None:
                return sent
        return draft

    @app.get("/api/v2/approvals/{aid}")
    def approval(request: Request, aid: str):
        with store.read() as c:
            from .files import readable_page_refs

            row = auth.approval(c, request.state.identity, aid)
            privacy.require_payload(c, request.state.identity, row)
            privacy.require_message(c, request.state.identity, H.message(c, row["message_id"]))
            # Its history: asked, decided (by whom, and for whom when the person's Assistant decided), undone, withdrawn,
            # spent.
            history = [
                {
                    "created_at": e["created_at"],
                    "actor": e["actor"],
                    "action": e["action"].removeprefix("approval."),
                    **{
                        k: v
                        for k, v in readable_page_refs(
                            c, auth, request.state.identity, H._json(e["detail"], {}) or {}
                        ).items()
                        if k in ("decision", "note", "via", "was", "kind")
                    },
                }
                for e in c.execute(
                    "SELECT created_at,actor,action,detail FROM events WHERE target=? AND action IN "
                    "('approval.request','approval.decide','approval.undo','approval.withdraw','approval.consume') "
                    "ORDER BY created_at",
                    (aid,),
                )
            ]
            return {**row, "history": history}

    @app.post("/api/v2/approvals/{aid}")
    def decide(request: Request, aid: str, body: M.ApprovalDecision):
        who = request.state.identity
        boards = []

        def work(c):
            row = auth.approval(c, who, aid, decide=True)
            if (
                body.decision == "approved"
                and row["kind"] == "send"
                and (row.get("payload") or {}).get("email_send_id")
            ):
                from .email_sending import require_preview

                queued = c.execute(
                    "SELECT * FROM email_sends WHERE id=?", (row["payload"]["email_send_id"],)
                ).fetchone()
                require_preview(c, who.actor, queued)
            if row["kind"] == "support" and body.decision == "approved":
                # Sending off the install is a person's own tap, or their confirmed yes: not their token, nor an Assistant
                # that is not confirmed. A decline sends nothing, so anyone who may decide it may decline it.
                from .support import require_person

                require_person(who)
            from .company_page import editions

            own_click = editions.own_click_approval(row.get("payload"))
            if own_click:
                editions.require_own_click(who)  # what goes outside the company: never a token or the Assistant
            privacy.require_payload(c, who, row)
            decided = H.approval_decide(c, who.actor, aid, body.decision, body.note)
            if own_click:
                board = editions.approval_decided(c, decided)
                if board:
                    boards.append(board)
            if row["kind"] in proposals.KINDS and body.decision == "approved":
                proposals.apply(c, auth, settings, who, row)  # the hub applies it itself (CONTRACT §5)
            if row["kind"] in ("finance", "spend"):
                from .finance import actions as finance

                finance.approval_decided(c, aid)  # Finance acts on it in the same transaction
            return decided

        result = mutate(
            request, body, work, durable=True
        )  # approving a payment moves money: durable, like every decision
        app.state.letters.decided(aid)
        from .company_page import editions

        for board in boards:
            editions.release_board(store, board)  # after the commit: the rebuild takes its own transaction
        return result

    @app.post("/api/v2/approvals/{aid}/undo")
    def undo_decision(request: Request, aid: str, body: M.Empty):
        """The person takes back what their Assistant decided for them, before anything acted on it. Their own click."""
        who = request.state.identity
        if who.via:
            raise Problem("forbidden", "Only the person undoes this, in Settings > General", 403)

        def work(c):
            auth.approval(c, who, aid, decide=True)
            return H.approval_undo(c, who.actor, aid)

        return mutate(request, body, work)

    @app.post("/api/v2/approvals/{aid}/consume")
    def consume(request: Request, aid: str, body: M.ApprovalConsume):
        who = request.state.identity

        def work(c):
            row = auth.approval(c, who, aid)
            if row["requested_by_id"] != who.actor or row["payload_hash"] != body.payload_hash:
                raise Problem("approval", "Consume requires the requester and exact approved payload", 403)
            from .company_page import editions

            if editions.own_click_approval(row.get("payload")):
                raise Problem("approval", "The Company page spends this approval when it publishes", 403)
            return H.approval_consume(c, who.actor, aid)

        return mutate(request, body, work)
