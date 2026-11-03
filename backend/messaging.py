"""Bot-first view of scheduled email and Slack work.

The source of truth stays with the mail connector, Slack gateway, and automations. This read
model joins their records for people reviewing what a bot covers and what it saw.
"""

import json

from fastapi import Request

from . import routines, runs
from .mail import message_public, visible_addresses
from . import slack_channels as SC
from .slack_gateway import permalink
from .store import H, P, Problem
from .views import human_only, roster


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _sources(c, who, settings, bot, config, people, allowed, channels, mailboxes, reads):
    sources = []
    for address in routines.inbox_of(bot, config, people)["mailboxes"]:
        if address not in allowed:
            continue
        row = mailboxes.get(address) or {}
        sources.append({"id": "email:" + address, "kind": "email", "name": address,
                        "synced_at": row.get("synced_at"), "error": row.get("error"),
                        "message_count": row.get("message_count") or 0,
                        "needs_review": row.get("needs_review") or 0})
    if who.role == "owner":
        for cid, channel in channels.items():
            if bot not in channel["readers"]:
                continue
            read = reads.get((cid, bot)) or {}
            sources.append({"id": "slack:" + cid, "kind": "slack", "name": "#" + (channel["name"] or cid),
                            "purpose": channel["purpose"], "post": channel["post"],
                            "last_read": read.get("last_run_at"), "read_count": read.get("digests") or 0})
    return sources


def _catalog(c, who, auth, settings):
    allowed = set(visible_addresses(c, who))
    people = roster(c)
    channels = SC.channel_map(c, settings) if who.role == "owner" else {}
    mailboxes = {row["address"]: dict(row) for row in c.execute("SELECT * FROM mail_mailboxes")
                 if row["address"] in allowed}
    if mailboxes:
        marks = ",".join("?" * len(mailboxes))
        for row in c.execute(
                "SELECT m.mailbox,count(*) AS n FROM mail_messages m WHERE m.deleted_at IS NULL "
                f"AND m.mailbox IN ({marks}) AND EXISTS (SELECT 1 FROM json_each(m.labels) "
                "WHERE value LIKE 'hub/needs-%' OR value='hub/drafted') GROUP BY m.mailbox",
                tuple(mailboxes)):
            mailboxes[row["mailbox"]]["needs_review"] = row["n"]
    reads = {(row["channel"], row["reader"]): dict(row) for row in c.execute("SELECT * FROM slack_reads")}
    # Each bot's own automations (a mail bot's checks and schedules): when one last ran and when the next is due.
    schedules = {}
    for row in c.execute("SELECT a.id, a.owner_id, a.enabled, a.next_due_at, (SELECT max(started_at) FROM automation_runs r "
                         "WHERE r.automation_id=a.id) AS last_fired_at FROM automations a "
                         "WHERE a.deleted_at IS NULL AND a.owner_id LIKE 'bot:%'"):
        schedules.setdefault(row["owner_id"][4:], []).append(dict(row))
    latest_digest = {row["reader"]: row["last"] for row in c.execute(
        "SELECT reader,max(created_at) AS last FROM slack_digests GROUP BY reader")}
    bots = []
    covered_mail, covered_slack = set(), set()
    readable = auth.bot_accesses(c, who)
    for row in H.bots(c):
        bot = row["id"]
        if row["state"] == "archived" or not readable.get(bot, auth.FULL)["read"]:
            continue
        config_row = c.execute("SELECT config,description,owner_id,revision FROM bot_config WHERE bot=?",
                               (bot,)).fetchone()
        config = _json(config_row["config"], {}) if config_row else {}
        sources = _sources(c, who, settings, bot, config, people, allowed, channels, mailboxes, reads)
        if not sources:
            continue
        covered_mail.update(s["name"] for s in sources if s["kind"] == "email")
        covered_slack.update(s["id"][6:] for s in sources if s["kind"] == "slack")
        jobs = schedules.get(bot, [])
        fired = [r["last_fired_at"] for r in jobs if r["last_fired_at"]]
        due = [r["next_due_at"] for r in jobs if r["enabled"] and r["next_due_at"]]
        turn = c.execute("SELECT max(coalesce(finished_at,started_at)) FROM turns WHERE bot=?", (bot,)).fetchone()[0]
        last = max(fired + ([latest_digest[bot]] if bot in latest_digest else []) + ([turn] if turn else []), default=None)
        status = H.status(c, bot) or {}
        bots.append({"bot": bot, "name": row["display_name"] or row["slug"],
                     "description": (config_row["description"] if config_row else "") or config.get("role") or "",
                     "state": row["state"], "focus": status.get("focus") or "",
                     "last_result": status.get("last_result") or "",
                     "sources": sources, "automation_count": len(jobs),
                     "last_run": last, "next_run": min(due, default=None),
                     "slack_interval_minutes": settings.slack_digest_minutes if any(s["kind"] == "slack" for s in sources) else None,
                     # Mailbox review labels belong to its primary inbox bot. Other bots
                     # may read the same mailbox without owning that review queue.
                     "needs_review": (sum(s["needs_review"] for s in sources if s["kind"] == "email")
                                      if P.inbox_person(bot, people) else 0),
                     "can_manage": auth.operator(c, who, bot) or auth.manages(c, who, "bot", bot),
                     "revision": config_row["revision"] if config_row else None})
    bots.sort(key=lambda b: (b["state"] != "active", -b["needs_review"], b["name"].lower()))
    unassigned = []
    if who.role == "owner":
        for address in sorted(allowed - covered_mail):
            if address in mailboxes:
                unassigned.append({"id": "email:" + address, "kind": "email", "name": address})
        for cid, entry in channels.items():
            if cid not in covered_slack:
                unassigned.append({"id": "slack:" + cid, "kind": "slack",
                                   "name": "#" + (entry["name"] or cid)})
    return {"bots": bots, "unassigned": unassigned}


def _workspace_url(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key='slack_workspace_url'").fetchone()
    return _json(row["value"], "") if row else ""


def _slack_link(team_url, channel, ts, thread_ts=None):
    return (permalink(team_url, channel, ts, thread_ts) if team_url else
            "https://slack.com/app_redirect?channel=" + channel)


def _slack_message(row, team_url=""):
    return {"event_id": row["event_id"], "channel": row["channel"], "thread_ts": row["thread_ts"],
            "ts": row["ts"], "author": row["author_name"] or row["user_id"] or row["author"],
            "text": row["text"] if not row["deleted_at"] else "", "deleted": bool(row["deleted_at"]),
            "edited": bool(row["edited_at"]), "state": row["state"], "reason": row["reason"],
            "url": _slack_link(team_url, row["channel"], row["ts"], row["thread_ts"])}


def install_messaging(app, store, auth):
    @app.get("/api/v2/messaging/bots")
    def messaging_bots(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            return _catalog(c, who, auth, store.settings)

    @app.get("/api/v2/messaging/bots/{bot}")
    def messaging_bot(request: Request, bot: str, source: str = ""):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            catalog = _catalog(c, who, auth, store.settings)
            found = next((row for row in catalog["bots"] if row["bot"] == bot), None)
            if not found:
                raise Problem("not_found", "Messaging bot not found", 404)
            sources = found["sources"]
            if source and source not in {item["id"] for item in sources}:
                raise Problem("forbidden", "This source is not assigned to this bot", 403)
            chosen = [item for item in sources if not source or item["id"] == source]
            mailboxes = [item["name"] for item in chosen if item["kind"] == "email"]
            slack_ids = [item["id"][6:] for item in chosen if item["kind"] == "slack"]
            instruction = c.execute("SELECT content,updated_at FROM bot_agent_instructions WHERE bot=?",
                                    (bot,)).fetchone()
            if not instruction:
                instruction = c.execute("SELECT content,updated_at FROM mail_agent_instructions WHERE bot=?",
                                        (bot,)).fetchone()
            instructions = {"content": instruction["content"], "updated_at": instruction["updated_at"],
                            "published": True} if instruction and instruction["content"].strip() else {
                                "content": found["description"], "updated_at": None, "published": False}
            jobs = [dict(r) for r in c.execute(
                "SELECT id, name, enabled, trigger_type, next_due_at FROM automations WHERE owner_id=? AND deleted_at IS NULL "
                "ORDER BY name, id LIMIT 50", ("bot:" + bot,))]
            mail = []
            if mailboxes:
                marks = ",".join("?" * len(mailboxes))
                for row in c.execute(f"SELECT * FROM mail_messages WHERE deleted_at IS NULL AND mailbox IN ({marks}) "
                                     "ORDER BY epoch DESC LIMIT 40", tuple(mailboxes)):
                    item = message_public(row)
                    item["rule_hits"] = _json(row["rule_hits"], [])
                    mail.append(item)
            slack = []
            digests = []
            posts = []
            if slack_ids and who.role == "owner":
                team_url = _workspace_url(c)
                marks = ",".join("?" * len(slack_ids))
                slack = [_slack_message(row, team_url) for row in c.execute(
                    f"SELECT * FROM slack_events WHERE channel IN ({marks}) AND deleted_at IS NULL "
                    "ORDER BY ts DESC LIMIT 40", tuple(slack_ids))]
                for row in c.execute("SELECT * FROM slack_digests WHERE reader=? ORDER BY created_at DESC LIMIT 15",
                                     (bot,)):
                    event_ids = _json(row["event_ids"], [])
                    if source and event_ids:
                        marks = ",".join("?" * len(event_ids))
                        matching = c.execute(f"SELECT count(*) FROM slack_events WHERE event_id IN ({marks}) "
                                             "AND channel IN (" + ",".join("?" * len(slack_ids)) + ")",
                                             (*event_ids, *slack_ids)).fetchone()[0]
                        if not matching:
                            continue
                    attempt = c.execute("SELECT a.state,a.started_at,a.finished_at,a.final_text FROM jobs j "
                                        "LEFT JOIN attempts a ON a.job_id=j.id WHERE j.message_id=? "
                                        "ORDER BY a.created_at DESC LIMIT 1", (row["message_id"],)).fetchone()
                    digests.append({"id": row["id"], "created_at": row["created_at"], "count": row["count"],
                                    "skipped": row["skipped"], "channels": _json(row["channels"], []),
                                    "event_ids": event_ids,
                                    "run": {**dict(attempt), "state": runs.state(attempt["state"])} if attempt else None})
                posts = [{"channel": row["channel"], "thread_ts": row["thread_ts"],
                          "text": row["text"], "state": row["state"], "created_at": row["created_at"],
                          "error": row["error"], "url": _slack_link(team_url, row["channel"], row["slack_ts"] or row["thread_ts"])}
                         for row in c.execute(f"SELECT * FROM slack_posts WHERE bot=? AND channel IN ({marks}) "
                                              "ORDER BY created_at DESC LIMIT 20", (bot, *slack_ids))]
            return {"bot": found, "source": source, "instructions": instructions,
                    "automations": jobs, "slack_digests": digests, "slack_posts": posts,
                    "mail": mail, "slack": slack}

    @app.get("/api/v2/messaging/bots/{bot}/slack-thread")
    def messaging_slack_thread(request: Request, bot: str, channel: str = "", thread_ts: str = ""):
        who = request.state.identity
        human_only(who)
        if who.role != "owner":
            raise Problem("forbidden", "Slack channel history is available only to the owner", 403)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            channels = SC.channel_map(c, store.settings)
        if not channel or bot not in (channels.get(channel) or {}).get("readers", []):
            raise Problem("forbidden", "This bot does not read this channel", 403)
        if not thread_ts:
            raise Problem("thread", "thread_ts is required", 422)
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Messaging bot not found", 404)
            auth.require_read(c, who, bot, "Messaging bot not found")
            rows = c.execute("SELECT * FROM slack_events WHERE channel=? AND thread_ts=? "
                             "ORDER BY ts LIMIT 100", (channel, thread_ts)).fetchall()
            if not rows:
                raise Problem("not_found", "Slack thread not found", 404)
            team_url = _workspace_url(c)
            return {"channel": channel, "thread_ts": thread_ts,
                    "messages": [_slack_message(row, team_url) for row in rows]}

    @app.get("/api/v2/messaging/bots/{bot}/slack-digests/{digest_id}")
    def messaging_slack_digest(request: Request, bot: str, digest_id: str):
        who = request.state.identity
        human_only(who)
        if who.role != "owner":
            raise Problem("forbidden", "Slack channel history is available only to the owner", 403)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            channels = SC.channel_map(c, store.settings)
        covered = {cid for cid, entry in channels.items() if bot in entry["readers"]}
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Messaging bot not found", 404)
            auth.require_read(c, who, bot, "Messaging bot not found")
            digest = c.execute("SELECT * FROM slack_digests WHERE id=? AND reader=?",
                               (digest_id, bot)).fetchone()
            if not digest:
                raise Problem("not_found", "Slack pass not found", 404)
            ids = _json(digest["event_ids"], [])[:500]
            messages = []
            if ids:
                marks = ",".join("?" * len(ids))
                team_url = _workspace_url(c)
                messages = [_slack_message(row, team_url) for row in c.execute(
                    f"SELECT * FROM slack_events WHERE event_id IN ({marks}) ORDER BY ts", tuple(ids))
                            if row["channel"] in covered]
            return {"id": digest_id, "created_at": digest["created_at"], "count": digest["count"],
                    "skipped": digest["skipped"], "messages": messages}
