"""Batch routes: new chat, the batch review flow, live brief and stats."""

import json
from fastapi import Request
from . import batch, models as M, outcome_stats, rooms, task_privacy as privacy, views
from .store import H, Problem


def register(app, auth, mutate, send, store, task_view):
    @app.post("/api/v2/chat/{bot}/new")
    def new_chat(request: Request, bot: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            nonlocal bot
            if who.role not in ("owner", "human"):
                raise Problem("identity", "Only a person can start a fresh chat", 403)
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            from .shared_bots import route

            target_bot = H.actor_id(route(c, who.actor, "bot:" + bot))
            if rooms.thread_mode(c, target_bot) != rooms.PERSONAL:
                raise Problem("shared_room", "A shared bot room cannot be reset by one member", 409)
            views.require_chat(c, auth, who, target_bot)
            archived = rooms.archive_personal_room(c, who.actor, target_bot)
            return {"archived": archived["id"] if archived else None}

        return mutate(request, body, work)

    # A batch: what bots need from the person, frozen, walked one item at a time; responses are
    # collected and applied together on commit (backend/batch.py). Any front end runs it.
    def batch_send(c, who, text, refs, to):
        # Each bot hears about its own items in the person's chat with it.
        return send(c, who, M.MessageCreate(to=to, text=text, refs=refs))

    @app.post("/api/v2/batch")
    def batch_start(request: Request, body: M.BatchStart):
        who = request.state.identity
        views.human_only(who)

        def work(c):
            privacy.require_batch(c, who, batch.current(c, who.actor))
            items = views.needs_items(c, auth, who, task_view)
            snapshot = views.fleet_snapshot(c, auth, who, task_view)
            scope = body.bot if body.bot == batch.NEXT else (H.resolve_bot(c, body.bot) or {}).get("id", body.bot)
            return batch.start(c, who.actor, items, snapshot, scope=scope, reader=privacy.actor(who))

        return mutate(request, body, work)

    @app.get("/api/v2/live/brief")
    def live_brief(request: Request, since: str = ""):
        """One fast read for a person talking on the go (Live through Grok Bot).
        What is broken, who is waiting on them and how much, and what bots said to them since
        `since` (default: the last 12 hours). Reads only; consumes nothing."""
        who = request.state.identity
        views.human_only(who)
        if since and (not H.parse_ts(since) or H.parse_ts(since).tzinfo is None):
            raise Problem("date", "since must be an ISO-8601 date/time with a timezone", 422)
        start = views.since_time(since) or H.shift(H.now(), hours=-12)
        with store.read() as c:
            items = views.needs_items(c, auth, who, task_view)
            snapshot = views.fleet_snapshot(c, auth, who, task_view)
            rows, notes, reports = batch.candidates(c, who.actor, items)
            said = []
            for m in c.execute(
                "SELECT * FROM messages WHERE recipient_id=? AND "
                "sender_id LIKE 'bot:%' AND kind IN ('say','ask','answer') AND created_at>? "
                "AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 6",
                (who.actor, start),
            ).fetchall():
                if not privacy.message_readable(c, privacy.actor(who), m):
                    continue
                said.append(
                    {
                        "from": batch._name(c, m["sender_id"]),
                        "kind": m["kind"],
                        "when": m["created_at"],
                        "text": batch._clip(m["body"] or "", 240),
                    }
                )
            # A bot that cannot run, which this person can fix (waiting_on: a Paused cause).
            from . import waiting_on

            stuck = [n for n in waiting_on.needs(c, who.actor) if n["kind"] == "paused"]
            open_ = batch.current(c, who.actor)
            return {
                "now": H.now(),
                "since": start,
                "alerts": batch.alerts(snapshot),
                "lineup": batch.lineup(c, who.actor, notes + rows),
                "replies_waiting": len(reports),
                "said": said,
                "stuck": len(stuck),
                "open_batch": bool(open_),
            }

    @app.get("/api/v2/live/stats")
    def live_stats(request: Request, days: int = 7, via: str = ""):
        """How an outside assistant's tool calls perform (backend/mcp.py record_call), per tool:
        calls, median and p90 server time, typical answer size, errors. People and BotOps read all callers;
        a bot granted outcome stats reads only bot calls. Delegated turns are refused."""
        who = request.state.identity
        with store.read() as c:
            granted = outcome_stats.may_read(c, who) and who.role == "bot"
        if (
            who.via
            or who.task_actor
            or (who.role not in ("human", "owner") and who.actor != H.bot_actor(H.FLEET_MAINTAINER) and not granted)
        ):
            raise Problem(
                "forbidden", "Tool-call stats are for people, BotOps and a bot the owner granted outcome stats", 403
            )
        if granted and via:
            raise Problem("forbidden", "A bot granted outcome stats reads the totals, not one assistant's calls", 403)
        since = H.shift(H.now(), days=-max(1, min(days, 90)))
        tools = {}
        with store.read() as c:
            for row in c.execute("SELECT actor, detail FROM events WHERE action='mcp.call' AND created_at>?", (since,)):
                if granted and not (row["actor"] or "").startswith("bot:"):
                    continue
                d = json.loads(row["detail"] or "{}")
                if via and d.get("via") != via:
                    continue
                name = d.get("tool") or "?"
                # Unknown names are caller text; only registered tool names leave the event for a granted bot.
                if granted and name not in outcome_stats.TOOL_NAMES:
                    name = "other"
                t = tools.setdefault(name, {"ms": [], "bytes": [], "errors": 0})
                t["ms"].append(int(d.get("ms") or 0))
                t["bytes"].append(int(d.get("bytes") or 0))
                t["errors"] += bool(d.get("error"))

        def pct(values, p):
            values = sorted(values)
            return values[min(len(values) - 1, int(len(values) * p))] if values else 0

        rows = [
            {
                "tool": name,
                "calls": len(t["ms"]),
                "median_ms": pct(t["ms"], 0.5),
                "p90_ms": pct(t["ms"], 0.9),
                "median_bytes": pct(t["bytes"], 0.5),
                "max_bytes": max(t["bytes"] or [0]),
                "errors": t["errors"],
            }
            for name, t in tools.items()
        ]
        rows.sort(key=lambda r: -r["calls"])
        return {"since": since, "via": via or None, "tools": rows}

    @app.get("/api/v2/batch")
    def batch_current(request: Request):
        who = request.state.identity
        views.human_only(who)
        with store.read() as c:
            row = batch.current(c, who.actor)
            privacy.require_batch(c, who, row)
            return {"batch": batch.view(c, row, resumed=True) if row else None}

    @app.post("/api/v2/batch/{bid}/next")
    def batch_next(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        return mutate(request, body, lambda c: batch.next_item(c, who.actor, bid, reader=privacy.actor(who)))

    @app.post("/api/v2/batch/{bid}/respond")
    def batch_respond(request: Request, bid: str, body: M.BatchRespond):
        who = request.state.identity
        views.human_only(who)
        if body.until and (not H.parse_ts(body.until) or H.parse_ts(body.until).tzinfo is None):
            raise Problem("date", "until must be an ISO-8601 date/time with a timezone", 422)
        return mutate(
            request,
            body,
            lambda c: batch.respond(
                c,
                who.actor,
                bid,
                body.kind,
                body.text,
                n=body.item,
                decision=body.decision,
                heard=body.heard,
                until=body.until,
            ),
        )

    @app.post("/api/v2/batch/{bid}/commit")
    def batch_commit(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        boards = []

        def work(c):
            out = batch.commit(c, auth, who, bid, batch_send, boards)
            rows, notes, _ = batch.candidates(c, who.actor, views.needs_items(c, auth, who, task_view))
            line = batch.lineup(c, who.actor, notes + rows)
            return {**out, "lineup": line, "up_next": batch.up_next(line, out["scope"])}

        result = mutate(request, body, work, durable=True)  # a batch can approve a payment
        from .company_page import editions

        for board in boards:
            editions.release_board(store, board)  # after the commit, as the decide route does
        return result

    @app.post("/api/v2/batch/{bid}/abandon")
    def batch_abandon(request: Request, bid: str, body: M.Empty):
        who = request.state.identity
        views.human_only(who)
        return mutate(request, body, lambda c: batch.abandon(c, who.actor, bid))
