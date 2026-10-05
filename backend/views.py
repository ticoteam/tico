"""Existing frontend read models over cloud records. Never import the local hub server."""

import asyncio
import json
import re
from datetime import timezone

import yaml
from fastapi import Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response, StreamingResponse

from . import access as Access
from . import bot_access as A
from . import models as M
from . import providers, runner_versions
from . import rooms, team_rules, turns
from .execution import AWAKE_GAP, AWAKE_SETTLE, Execution
from pathlib import Path

from .store import H, P, Problem, bot_readiness, encode, message_page, readiness_document, repo_url
from . import task_privacy as privacy


MIN_RUNNER_VERSION = (0, 2, 0)
DOC_BOT = "doc-updater"         # the documentation agent: docs questions and doc PR reviews


def runner_version_supported(value):
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", str(value or ""))
    return bool(match and tuple(map(int, match.groups())) >= MIN_RUNNER_VERSION)


def since_time(value):
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([dhm])", value)
    if match:
        return H.shift(H.now(), seconds=-int(match[1]) * {"d": 86400, "h": 3600, "m": 60}[match[2]])
    at = H.parse_ts(value)
    if not at:
        raise Problem("date", "Use an ISO date/time or a duration such as 7d", 422)
    # Stored times are UTC to the microsecond and compared as text: "19:36:14Z" sorts after "19:36:14.5Z".
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def attempt_details(c, turn):
    attempt = c.execute("SELECT state,result_json,finished FROM attempts WHERE id=?", (turn["id"],)).fetchone()
    failed = bool(attempt and attempt["state"] in ("failed", "expired"))
    details = {"record_kind": "attempt" if failed else "run", "attempt_id": turn["id"]}
    if failed:
        try:
            result = json.loads(attempt["result_json"] or "{}")
        except (ValueError, TypeError):
            result = {}
        reason = result.get("error") or result.get("reason") or result.get("message") or (
            "Lease expired before completion" if attempt["state"] == "expired" else "Attempt failed before completion")
        details.update(exit=attempt["state"], finished=attempt["finished"] or turn.get("finished"),
                       failure_reason=str(reason))
    return details


def failed_attempts(c, who, auth, bot=None, since=None, limit=200):
    readable = auth.bot_accesses(c, who)
    rows = []
    for row in c.execute(
            "SELECT a.id,a.bot,a.started,a.created,a.finished,a.state,a.result_json,j.message_id FROM attempts a "
            "JOIN jobs j ON j.id=a.job_id WHERE a.state IN ('failed','expired') AND (? IS NULL OR a.bot=?) "
            "AND (? IS NULL OR coalesce(a.finished,a.created)>=?) AND NOT EXISTS "
            "(SELECT 1 FROM turns t WHERE t.id=a.id) ORDER BY coalesce(a.finished,a.created) DESC LIMIT ?",
            (bot, bot, since, since, limit)):
        if not (readable.get(row["bot"]) or {}).get("read"):
            continue
        message = H.message(c, row["message_id"])
        if not message:
            continue
        try:
            auth.conversation(c, who, message["conversation_id"])
            privacy.require_message(c, who, message)
            privacy.require_attempt(c, who, row["id"])
        except Problem:
            continue
        try:
            result = json.loads(row["result_json"] or "{}")
        except (ValueError, TypeError):
            result = {}
        reason = (result.get("error") or result.get("reason") or result.get("message")
                  or ("Lease expired before completion" if row["state"] == "expired" else "Attempt failed before completion"))
        rows.append({"id": row["id"], "attempt_id": row["id"], "bot": row["bot"],
                     "started": row["started"] or row["created"], "finished": row["finished"],
                     "exit": row["state"], "message_id": row["message_id"],
                     "record_kind": "attempt", "failure_reason": str(reason)})
    return rows


def computer_details(c, row, who, auth):
    value = dict(row)
    readiness = readiness_document(value.get("readiness_json"))
    access = auth.bot_accesses(c, who)
    assigned = {r[0] for r in c.execute(
        "SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
        "WHERE a.runner_id=? AND b.state<>'archived'", (row["id"],))}
    readiness["bots"] = {bot: {k: v for k, v in report.items() if k != "tools"}
                         for bot, report in readiness.get("bots", {}).items()
                         if bot in assigned and (access.get(bot) or {}).get("read") and isinstance(report, dict)}
    update = runner_versions.view(runner_versions.load(c).get(row["id"]))
    update["wanted_release"] = runner_versions.desired()["version"]
    from .repositories import metadata
    return {"repositories": metadata(c, "computer-repositories:" + row["id"]).get("repositories", "unknown"),
            "version": value.get("version") or "", "release": update.get("release") or "", "last_seen": value.get("last_seen"),
            "readiness": readiness, "update": update, "fix": "Open Settings > Computers to retry the update" if update.get("error") else "Open Settings > Computers",
            "services": [], "services_scope": "team"}


SERVICE_NAMES = {"connector:calendar": "Calendar Tool", "connector:mail": "Mail Tool"}


def team_services(c, who, auth):
    if who.role != "owner" and not auth.bot_admin(who):
        return []
    return [{**dict(r), "scope": "team", "name": SERVICE_NAMES.get(r["service"], r["service"]),
             "text": f"{SERVICE_NAMES.get(r['service'], r['service'])}: {r['last_error'] or 'No reported error'}",
             "fix": "Open Health for " + SERVICE_NAMES.get(r["service"], r["service"])} for r in c.execute(
                 "SELECT service,last_success,last_error FROM service_health ORDER BY service")]


def roster(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()
    if row:
        return P.load(json.loads(row[0]))
    return P.load({"people": H.humans(c)})


def entries(c, github_owner=""):
    result = {}
    for row in c.execute("SELECT bot,config_json,owner_ids_json,description,reports_to,repo,thread_mode,"
                         "onboarding_state FROM bot_config"):
        from .shared_bots import follow
        config = follow(c, row["bot"], json.loads(row["config_json"]))
        if row["onboarding_state"]:
            config["onboarding_state"] = row["onboarding_state"]
        repo = config.get("repo") or row["repo"] or ("emp-" + row["bot"])
        config.update({"description": row["description"] or "", "reports_to": row["reports_to"],
                       "repo": repo, "repo_url": repo_url(repo, github_owner)})
        if row["thread_mode"]:
            config["thread_mode"] = row["thread_mode"]
        if row["owner_ids_json"] is not None:
            config["owner_ids"] = json.loads(row["owner_ids_json"])
        result[row["bot"]] = config
    return result


def human_only(who):
    if who.role not in ("owner", "human"):
        raise Problem("identity", "This frontend endpoint requires a person", 403)


def may_chat(c, auth, who, bot):
    """Whether the caller may send this bot messages: Write on it (backend/bot_access.py). Who a
    bot "works for" (Can use) no longer decides it."""
    if not auth.bot_access(c, who, bot)["write"]:
        return False
    if who.role == "bot":
        return True
    # The assistant is chatted with in one place only: each person's own Assistant room, through
    # /api/v2/assistant (backend/assistant.py). It has no chat with anyone else and no shared or
    # per-bot chat, so every other route that asks "may I chat with it" says no.
    return bot != auth.settings.assistant_bot


def require_chat(c, auth, who, bot):
    """Write on the bot, and a bot that has a chat of its own (the Assistant has only the room
    /api/v2/assistant gives each person)."""
    auth.require_write(c, who, bot)
    if not may_chat(c, auth, who, bot):
        raise Problem("forbidden", auth.settings.assistant_name + " chats only in your own Assistant "
                      "(/api/v2/assistant); message one of your bots here", 403)


def default_bot(c, settings, botops="botops"):
    """Where work nobody was named for goes: the assistant when the company has set one up,
    else BotOps. The assistant is optional (the wizard's "Pick your bots"), and a planned or
    archived one cannot take work, so it must not be the answer then."""
    for slug in (settings.assistant_bot, botops):
        row = H.bot(c, slug)
        if row and row.get("state") not in ("planned", "archived"):
            return slug
    return settings.assistant_bot


def docs_room(conv, who):
    """A person's own "Ask the Librarian" room. Its follow-ups reach the documentation agent
    whether or not the person is one of that bot's assigned users, as the first question did."""
    return (conv.get("scope") == rooms.PERSONAL and conv.get("owner_actor") == who.actor
            and str(conv.get("room_key") or "").startswith("docs:")
            and "bot:" + DOC_BOT in conv["participants"])


def bot_owner_rows(c, registry, slug, people, configs, archived):
    """The people listed as a bot's owners: the creator and co-owners, and its operator. Whoever it reports
    up to and the Admins own it too, without being listed."""
    ids = list(dict.fromkeys([*A.owner_ids(registry["bot_owners_json"] if registry else None),
                              *([registry["operator"]] if registry and registry["operator"] else [])]))
    return [{"id": i, "name": (P.person(i, people) or {}).get("name") or i} for i in ids]


def machine(c, bot):
    from .agents import presence
    external = presence(c, bot)
    if external:
        return external
    row = c.execute("SELECT a.runner_id,a.generation,r.label,r.operator,r.last_seen,r.revoked_at,r.readiness_json,"
                    "r.platform,r.version,r.awake_since "
                    "FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (bot,)).fetchone()
    if not row:
        return {"online": False, "awake": False, "ready": False, "machine": None}
    now = H.now()
    online = bool(not row["revoked_at"] and row["last_seen"] and row["last_seen"] > H.shift(now, seconds=-AWAKE_GAP))
    # Online is contact; awake is contact that has lasted. A machine waking for a few seconds
    # is online and takes no work, so say which of the two a queued request is waiting for.
    awake = online and Execution.awake(row["awake_since"], now)
    # The declared access has its own route, behind the bot's visibility (backend/bot_tools.py).
    detail = {k: v for k, v in bot_readiness(row["readiness_json"], bot).items() if k != "tools"}
    return {"online": online, "awake": awake, "ready": online and detail.get("ready") is True,
            "readiness": detail,
            "machine": {k: row[k] for k in ("runner_id", "generation", "label", "operator", "last_seen",
                                                      "platform", "version")}}


def deploy_draining(c, bot):
    """When the deploy drained this bot (its latest drain was the deploy's, within the hour a
    deploy may hold it), else None. A drain a person set is theirs, not an update."""
    row = c.execute("SELECT actor, ts FROM events WHERE action IN ('bot.drain','bot.pause','bot.resume') "
                    "AND target=? ORDER BY ts DESC LIMIT 1", (bot,)).fetchone()
    if not row or row["actor"] != "system:deploy" or row["ts"] < H.shift(H.now(), seconds=-3600):
        return None
    return row["ts"]


def updating(c):
    """Tico updating itself right now: the bots a deploy is holding, and since when."""
    held = [r[0] for r in c.execute("SELECT bot FROM bot_control WHERE draining=1")]
    stamps = [ts for ts in (deploy_draining(c, bot) for bot in held) if ts]
    return {"since": min(stamps), "bots": len(stamps)} if stamps else None


def operation_issues(c, who, auth):
    """Human-facing failures derived from durable state; no external alarm service.

    Every issue says whether a person has to act (`needs_person`). The UI opens those in front of
    the person wherever they are; the rest stay in Settings for quieter diagnosis or review.
    """
    from .agents import still_reporting
    issues, seen = [], set()
    def add(kind, title, detail, *, bot=None, machine=None, severity="error", since=None,
            needs_person=True, action=None, **extra):
        key = (kind, bot, machine, detail)
        if key not in seen:
            seen.add(key)
            issues.append({"kind": kind, "title": title, "detail": detail, "bot": bot,
                           "machine": machine, "severity": severity, "since": since,
                           "needs_person": needs_person, "action": action, **extra})

    offline = {}    # runner_id -> {label, since, bots: [display names], queued}
    readable = auth.bot_accesses(c, who)
    for bot in H.bots(c):
        slug = bot["slug"]
        if not readable.get(slug, auth.FULL)["read"]:
            continue
        if bot["state"] == "archived":
            # An archived bot whose agent still holds a working credential goes on reporting in to nobody; it fails
            # quietly on its own box, so Health says it.
            if still_reporting(c, slug):
                add("agent", bot["display_name"] + "'s " + ("OpenClaw" if still_reporting(c, slug)["harness"] == "openclaw" else "Hermes")
                    + " agent is still reporting in, but the bot is archived",
                    "Restore it, or revoke its credential.", bot=slug, severity="warning",
                    needs_person=bool(auth.bot_manager(c, who, slug)),
                    action="Restore it, or revoke its credential, in Settings → Bots.")
            continue
        location = machine(c, slug)
        mac_offline = bool(location.get("machine") and not location.get("online") and not location.get("agent"))
        status = privacy.status(c, who, H.status(c, slug)) or {}
        if bot["state"] == "quarantined" or status.get("state") == "quarantined":
            add("bot", bot["display_name"] + " needs attention",
                status.get("focus") or "The bot is " + (status.get("state") or bot["state"]),
                bot=slug, needs_person=True,
                action="Review the refused action and interrupted work, then resume the bot on its page.")
        elif status.get("state") == "crashed" and not mac_offline:
            add("bot", bot["display_name"] + " needs attention",
                status.get("focus") or "The bot is crashed", bot=slug, needs_person=False)
        elif status.get("state") == "limited":
            # A usage limit retries on its own; only a fresh one is worth a glance, an
            # old row is a leftover nobody cleared. Three in a row is the subscription
            # window spent; Settings still shows the retry time without interrupting chat.
            from .execution import LIMIT_STRIKES, limit_cooldown, limit_streak
            since = H.parse_ts(status.get("since"))
            cooldown = limit_cooldown(c, slug)
            if since and since > H.parse_ts(H.shift(H.now(), seconds=-max(3600, cooldown))):
                config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
                runtime = providers.bot_choice(c, auth.settings, json.loads(config[0]) if config else {})[0] or "your"
                if limit_streak(c, slug) >= LIMIT_STRIKES:
                    retry = H.shift(status["since"], seconds=cooldown)
                    add("bot", bot["display_name"] + " needs attention",
                        f"{runtime} usage limit three times in a row; queued work waits until {retry[11:16]} UTC, "
                        "check the subscription or the API-key fallback on its Mac", bot=slug,
                        needs_person=False,
                        action="Check the subscription or set a fallback harness in Settings → Bots.")
                else:
                    add("bot", bot["display_name"] + " needs attention",
                        f"{runtime} usage limit; retrying automatically", bot=slug,
                        severity="warning", needs_person=False)
        queued = privacy.job_count(c, who, slug)
        if location.get("agent"):
            queued = sum(privacy.message_readable(c, privacy.actor(who), m) for m in c.execute(
                "SELECT * FROM messages WHERE to_actor=? AND read_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?) AND deleted_at IS NULL", ("bot:" + slug, H.now())))
        uncertain = privacy.job_count(c, who, slug, ("uncertain",))
        if uncertain and not mac_offline:
            noun = "run" if uncertain == 1 else "runs"
            queued_note = (f" {queued} other request{' is' if queued == 1 else 's are'} still queued."
                           if queued else " Other work can continue.")
            # A stopped run is held so an automatic retry cannot repeat an outside action. It is
            # useful in Settings, but age alone never turns it into an interruption for a person.
            add("uncertain_work", f"{bot['display_name']} saved {uncertain} stopped {noun} for later",
                "The bot stopped before returning an answer. Tico paused only that request so it "
                f"will not repeat a possible outside action.{queued_note} Review it whenever useful.",
                bot=slug, severity="warning", needs_person=False, action="review")
        agent = location.get("agent")
        if agent and bot["state"] == "active" and not agent["credential"]:
            add("agent", bot["display_name"] + " has no agent credential",
                "Create one in Settings → Bots and install it in the " + agent["harness"] + " profile.",
                bot=slug, needs_person=False)
        elif agent and bot["state"] == "active" and not location["online"]:
            detail = ("Last heartbeat " + agent["last_seen"] + "." if agent["last_seen"]
                      else "No heartbeat yet: install the credential and the heartbeat timer on its box.")
            if queued:
                detail += f" {queued} unread message{'s' if queued != 1 else ''} waiting in its inbox."
            add("agent", bot["display_name"] + "'s " + agent["harness"] + " agent has not reported in",
                detail, bot=slug, since=agent["last_seen"], needs_person=False)
        elif agent:
            pass    # an external agent that reported in lately: no computer to check
        elif bot["state"] == "active" and not location["machine"]:
            # Nothing waiting means nothing urgent: a bot nobody has placed yet is setup, not a fault.
            add("machine", bot["display_name"] + " has no computer",
                f"{queued} saved request{'s are' if queued != 1 else ' is'} waiting." if queued else "Assign a registered computer before new work arrives.",
                bot=slug, needs_person=False, severity="error" if queued else "warning",
                action="Assign a registered computer in Settings → Bots." if queued else None)
        elif bot["state"] == "active" and location["machine"] and not location["online"]:
            # One issue per machine, not one per bot: the person can only do one thing about it.
            entry = offline.setdefault(location["machine"]["runner_id"], {
                "label": location["machine"]["label"], "since": location["machine"]["last_seen"],
                "operator": location["machine"]["operator"], "bots": [], "slugs": [], "queued": 0})
            entry["bots"].append(bot["display_name"])
            entry["slugs"].append(slug)
            entry["queued"] += queued
        elif bot["state"] == "active" and location["online"] and not location["ready"]:
            problems = location.get("readiness", {}).get("problems") or ["Local setup is incomplete."]
            add("readiness", bot["display_name"] + " is not ready", problems[0], bot=slug,
                machine=location["machine"]["runner_id"], needs_person=False)
    for runner_id, entry in offline.items():
        names = entry["bots"]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        cannot = "cannot run until it reconnects"
        waiting = f" {entry['queued']} saved request{'s are' if entry['queued'] != 1 else ' is'} waiting." if entry["queued"] else ""
        # An offline Mac is a warning that clears itself when it reconnects. Do not put it
        # in "needs you": nobody here can wake Ben's laptop.
        add("machine", entry["label"] + " is offline", f"{listed} {cannot}.{waiting}",
            machine=runner_id, since=entry["since"], severity="warning", needs_person=False)
        issues[-1]["bots"] = entry["slugs"]     # the bot pages find their machine's issue by this
    if who.role == "owner":
        for row in c.execute("SELECT id,label,operator,last_seen,revoked_at,version,checkout_json,restart_requested "
                             "FROM runners WHERE revoked_at IS NULL"):
            online = row["last_seen"] and row["last_seen"] > H.shift(H.now(), seconds=-60)
            if online and not runner_version_supported(row["version"]):
                add("runner_version", row["label"] + " needs a runner update",
                    "Install the current " + auth.settings.app_name + " runner before assigning new work.",
                    machine=row["id"], action="Update this runner in Settings → Computers.")
            if online:
                checkout_issue(add, row, runner_busy(c, row["id"]))
        for row in c.execute("SELECT service,last_error,detail_json FROM service_health WHERE last_error IS NOT NULL"):
            detail = json.loads(row["detail_json"] or "{}")
            if row["service"] == "github:token":
                # Only the App itself failing is a system problem, and only while it is recent and unrecovered.
                from . import health
                failure = health.token_failure(c)
                if failure:
                    add("service", "GitHub needs attention", failure["message"] or "GitHub would not issue a token.",
                        needs_person=False, action=failure["action"])
                continue
            add("service", row["service"] + " needs attention", row["last_error"] or "The last check failed.",
                needs_person=bool(detail.get("needs_person")), action=detail.get("action"))
        # A connector that stopped refreshing (the Mac lost the network, or the sync hung) writes no
        # error, so its age is checked while a Mac is online: calendar refreshes every 45 s, mail
        # reports every 10 minutes.
        if c.execute("SELECT 1 FROM runners WHERE revoked_at IS NULL AND last_seen>?",
                     (H.shift(H.now(), seconds=-60),)).fetchone():
            for service, label, limit in (("connector:calendar", "Calendar", 15), ("connector:mail", "Mail", 90)):
                row = c.execute("SELECT last_success,last_error FROM service_health WHERE service=?", (service,)).fetchone()
                last = H.parse_ts(row["last_success"]) if row and row["last_success"] else None
                if last and not row["last_error"] and last < H.parse_ts(H.shift(H.now(), minutes=-limit)):
                    minutes = int((H.parse_ts(H.now()) - last).total_seconds() // 60)
                    add("service", SERVICE_NAMES[service] + " needs attention",
                        f"{label} last refreshed {minutes} minutes ago although a Mac is online; check its Tool log.",
                        needs_person=False)
        # A backup that stops running never writes an error, so age is checked here with
        # the same limits as the CloudWatch alarms; the hub is the notification channel.
        # Both run daily after 03:00 UTC and the restore check retries hourly, so 36 hours
        # means the retries are failing too.
        for service, label, limit in (("backup", "verified backup", 36), ("restore-check", "isolated restore check", 36)):
            row = c.execute("SELECT last_success FROM service_health WHERE service=?", (service,)).fetchone()
            last = H.parse_ts(row["last_success"]) if row and row["last_success"] else None
            if last and last < H.parse_ts(H.shift(H.now(), hours=-limit)):
                hours = int((H.parse_ts(H.now()) - last).total_seconds() // 3600)
                add("service", service + " needs attention",
                    "The last " + label + " succeeded " + str(hours) + " hours ago; check backup.log and the timers on the server.",
                    action="Check backup.log and the server timers.")
        for row in c.execute("SELECT id,bot,error FROM bot_transitions WHERE state='failed'"):
            # A version conflict means settings already moved; the failed checkpoint is leftover.
            if "while the checkpoint was being prepared" in (row["error"] or ""):
                continue
            add("settings", "A prepared settings change failed", row["error"] or "Review or cancel the change.",
                bot=row["bot"], action="Review or cancel the failed change in Settings → Bots.")
    return issues


def snapshot_mark(c, cid):
    """What changes when a conversation's snapshot does, in one cheap read: its newest message and
    count, and its latest job with that job's attempt (state, lease, steps written)."""
    return tuple(c.execute(
        "SELECT (SELECT coalesce(updated_at,'')||status FROM chat_goals WHERE conversation_id=?),"
        " (SELECT max(rowid) FROM messages WHERE conversation_id=?),"
        " (SELECT count(*) FROM messages WHERE conversation_id=?),"
        " (SELECT j.id||'|'||j.state||'|'||coalesce(a.state,'')||'|'||coalesce(a.last_seq,0)||'|'||coalesce(a.lease_until,'')"
        "  FROM jobs j JOIN messages m ON m.id=j.message_id LEFT JOIN attempts a ON a.id=j.attempt_id"
        "  WHERE m.conversation_id=? ORDER BY m.rowid DESC LIMIT 1)", (cid, cid, cid, cid)).fetchone())


def conversation_snapshot(c, cid, who=None):
    page = privacy.page(c, who, cid) if who else message_page(c, cid)
    job = c.execute("SELECT j.*,a.state AS attempt_state,a.lease_until FROM jobs j JOIN messages m ON m.id=j.message_id "
                    "LEFT JOIN attempts a ON a.id=j.attempt_id WHERE m.conversation_id=? "
                    "AND coalesce(json_extract(m.refs_json,'$.maintenance'),'')!='checkpoint' "
                    "ORDER BY m.rowid DESC LIMIT 1", (cid,)).fetchone()
    execution = None
    if job and who and (not privacy.message_readable(c, privacy.actor(who), H.message(c, job["message_id"]))
                        or job["attempt_id"] and not privacy.attempt_readable(c, privacy.actor(who), job["attempt_id"])):
        job = None
    if job:
        state = job["state"]
        input_added = state == "input"
        if state in ("running", "leased", "input") and job["lease_until"] <= H.now():
            state = "uncertain" if state in ("running", "input") else "queued"
        elif state == "input":
            state = "running"
        location = machine(c, job["bot"])
        bot_state = H.bot(c, job["bot"])["state"]
        label = {"queued": "Saved — queued", "leased": "Starting", "running": "Working",
                 "completed": "Complete", "cancelled": "Delivery dismissed", "uncertain": "Stopped — saved for later"}.get(state, state)
        if input_added and state == "running":
            label = "Working — follow-up added"
        if state == "uncertain" and job["attempt_state"] == "failed":
            label = "Reply failed — your message is saved"
        readiness_reason = ""
        if state == "queued":
            draining = c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (job["bot"],)).fetchone()
            # A drain is either Tico updating itself (the deploy finishes each
            # bot's current run, then holds new ones until it is back) or a person pausing it.
            if draining and deploy_draining(c, job["bot"]):
                label = "Saved — Tico is updating; this starts when the update finishes"
            elif draining:
                label = "Saved — this bot is paused; this starts when it's resumed"
            elif bot_state != "active":
                label = "Saved — bot " + bot_state
            elif not location["online"]:
                label = "Saved — waiting for " + ((location["machine"] or {}).get("label") or "a registered computer")
            elif not location["awake"]:
                label = "Saved — waiting for " + ((location["machine"] or {}).get("label")
                                                  or "a registered computer") + " to stay awake"
            elif not location["ready"]:
                problems = (location.get("readiness") or {}).get("problems") or []
                if any(str(p).startswith("No AI provider is chosen") for p in problems):
                    readiness_reason = "missing_provider"
                    label = "Saved — no AI provider is chosen"
                else:
                    label = "Saved — " + str(problems[0]) if problems else "Saved — waiting for computer setup"
        parts = []
        if state in ("leased", "running"):
            parts = turns.reply_parts([(e["kind"], json.loads(e["payload_json"]), e["created"]) for e in c.execute(
                "SELECT kind,payload_json,created FROM attempt_events WHERE attempt_id=? ORDER BY seq", (job["attempt_id"],))])
        execution = {"job_id": job["id"], "message_id": job["message_id"], "bot": job["bot"],
                     "attempt_id": job["attempt_id"], "state": state, "label": label,
                     "text": "\n\n".join(p["text"] for p in parts if p["kind"] != "tool"), "parts": parts, **location}
        if readiness_reason:
            execution["readiness_reason"] = readiness_reason
    from .chat_goals import current
    return {**page, "execution": execution, "goal": current(c, cid)}


# What needs a person, in the order they work it. Bot requests rank above inbox items from mail, since helping the bots matters more. A request
# from a bot about its own work comes first, the person's own tasks next, and what an inbox
# bot lifted out of mail last; within a source the decisive kinds lead, oldest first.
SOURCE_RANK = {"bot": 0, "self": 1, "mail": 2}
# A bot's task set waiting on the person blocks that bot just as its question does.
KIND_RANK = {"approval": 0, "question": 1, "waiting": 1, "declined": 2, "task": 3, "report": 4}


def needs_source(actor):
    actor = str(actor or "")
    if actor.startswith("bot:"):
        return "mail" if actor.endswith("-inbox") else "bot"
    return "self"


CHECKOUT_BEHIND_HOURS = 1


def runner_busy(c, runner_id):
    """Turns running on a computer now: what a restart would wait for."""
    return c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running') "
                     "AND lease_until>?", (runner_id, H.now())).fetchone()[0]


def restart_issue(add, row, busy, since=None):
    """Behind main, or pulled but not restarted: one plain line and a Restart button The runner also does this by itself; the button is for now."""
    if row["restart_requested"]:
        detail = "Restarting" + (f" after {busy} running turn{'s' if busy != 1 else ''} finish{'es' if busy == 1 else ''}."
                                 if busy else " now. Nothing is running, so nothing will be interrupted.")
    elif busy:
        detail = f"{busy} turn{'s are' if busy != 1 else ' is'} running; a restart waits for {'them' if busy != 1 else 'it'} to finish."
    else:
        detail = "Nothing is running, so nothing will be interrupted."
    add("runner_checkout", row["label"] + " has a Tico update", detail, machine=row["id"], since=since,
        severity="warning", restart=not row["restart_requested"])
    return True


def checkout_issue(add, row, busy=0):
    """A runner whose checkout is not what main says (#492): the production runner ran 28 commits
    behind for a day because a bot committed in it. Local commits never fast-forward, so they are
    said at once; being behind is said once it has lasted an hour (a pull is on its way before)."""
    checkout = H._json(row["checkout_json"], {}) or {}
    if not checkout:
        return
    ahead, behind = int(checkout.get("ahead") or 0), int(checkout.get("behind") or 0)
    if ahead:
        add("runner_checkout", row["label"] + "'s checkout has commits that are not on main",
            f"{ahead} local commit{'s' if ahead != 1 else ''}"
            + (f" and {behind} behind main" if behind else "")
            + ": it cannot fast-forward, so it will not pick up new code until someone sorts it out.",
            machine=row["id"], since=checkout.get("checked_at"),
            action="On that computer: push the commits through a pull request, or reset the checkout to main.")
    elif behind and checkout.get("blocked"):
        # It cannot update itself (uncommitted changes, another branch, a dependency change):
        # a restart would not help, so say what is in the way at once.
        add("runner_checkout", row["label"] + " can't update itself",
            f"{behind} commit{'s' if behind != 1 else ''} behind main: {checkout['blocked']}.",
            machine=row["id"], since=checkout.get("checked_at"), severity="warning",
            action="On that computer: commit or stash the changes (or run pip install and scripts/tico update "
                   "for a dependency change); the runner then updates itself.")
    elif behind:
        stamp = checkout.get("behind_since") or checkout.get("checked_at") or ""
        if stamp and stamp <= H.shift(H.now(), seconds=-CHECKOUT_BEHIND_HOURS * 3600):
            restart_issue(add, row, busy, since=stamp)
    elif checkout.get("running") and checkout.get("head") and checkout["running"] != checkout["head"]:
        restart_issue(add, row, busy)


def needs_order(source_actor, kind, created, rank=None):
    """Needs you is the person's queue: what blocks a bot
    first (an approval, a question), then declined work, then their own tasks in rank order.
    Who sent it no longer orders it; the rank does."""
    return (KIND_RANK.get(kind, 9), rank if rank is not None else float("inf"), str(created or ""))


def _clip(text, n=280):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def recent_bots(c, auth, who, since, limit, needs):
    """The bots `who` exchanged messages or tasks with since `since`, most recent first."""
    me, seen = who.actor, {}
    access = auth.bot_accesses(c, who)

    def touch(slug, ts):
        if slug and access.get(slug, auth.FULL)["see"] and ts and ts > seen.get(slug, ""):
            seen[slug] = ts
    for row in c.execute("SELECT * FROM messages WHERE created>=? AND "
                         "((from_actor=? AND to_actor LIKE 'bot:%') OR (to_actor=? AND from_actor LIKE 'bot:%'))",
                         (since, me, me)):
        if not privacy.message_readable(c, privacy.actor(who), row):
            continue
        touch(H.actor_id(row["to_actor"] if row["from_actor"] == me else row["from_actor"]), row["created"])
    for row in c.execute("SELECT * FROM tasks WHERE updated>=? AND "
                         "((requester=? AND owner LIKE 'bot:%') OR (owner=? AND requester LIKE 'bot:%'))",
                         (since, me, me)):
        if not privacy.task_readable(c, who, dict(row)):
            continue
        touch(H.actor_id(row["owner"] if row["requester"] == me else row["requester"]), row["updated"])
    waiting = {}
    for item in needs:
        # A task waiting on the person waits with the bot that owns it, whoever asked for it.
        actors = ((item.get("owner"),) if item.get("kind") == "waiting" else
                  (item.get("origin_actor"), (item.get("ask") or {}).get("from_actor"), item.get("requester"), item.get("owner")))
        for actor in actors:
            if str(actor or "").startswith("bot:"):
                waiting.setdefault(H.actor_id(actor), []).append(item.get("title") or item.get("first_line") or "")
                break
    out = []
    for slug, last in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:limit]:
        bot = H.bot(c, slug) or {}
        # The bot's status is its activity: a person who may only write to it hears what it said
        # to them, not what it is doing.
        status = (privacy.status(c, who, H.status(c, slug)) or {}) if access.get(slug, auth.FULL)["read"] else {}
        actor = H.bot_actor(slug)
        mine = next((m for m in c.execute("SELECT * FROM messages WHERE from_actor=? AND to_actor=? "
                    "ORDER BY created DESC", (me, actor)) if privacy.message_readable(c, privacy.actor(who), m)), None)
        theirs = next((m for m in c.execute("SELECT * FROM messages WHERE from_actor=? AND to_actor=? AND deleted_at IS NULL "
                      "ORDER BY created DESC", (actor, me)) if privacy.message_readable(c, privacy.actor(who), m)), None)

        conversation = next((m["conversation_id"] for m in sorted(filter(None, (mine, theirs)),
                             key=lambda m: m["created"], reverse=True) if m["conversation_id"]), None)
        tasks = c.execute("SELECT id, title, status, owner, updated FROM tasks WHERE status IN "
                          "('open','doing','waiting','review','ready') AND ((owner=? AND requester=?) OR (owner=? AND requester=?)) "
                          "ORDER BY updated DESC LIMIT 5", (actor, me, me, actor)).fetchall()
        current = H.task(c, status["task_id"]) if status.get("task_id") else None
        out.append({
            "bot": slug, "name": bot.get("display_name") or slug, "last_interaction": last,
            "status": {"state": status.get("state") or bot.get("state"), "focus": status.get("focus") or "",
                       "since": status.get("since"), "working_on": (current or {}).get("title"),
                       "last_turn_at": status.get("last_turn_at")},
            "conversation_id": conversation,
            "you_said": {"text": _clip(mine["body"]), "at": mine["created"]} if mine else None,
            "it_said": {"text": _clip(theirs["body"]), "at": theirs["created"]} if theirs else None,
            "open_tasks": [{"id": t["id"], "title": t["title"], "status": t["status"],
                            "owner": t["owner"], "updated": t["updated"]} for t in tasks],
            "needs_you": waiting.get(slug, []),
        })
    return out


def needs_items(c, auth, who, task_view):
    raw = H.needs_you(c, who.actor)
    H.hydrate_task_tags(c, raw["tasks"] + raw["waiting"] + raw["declined"])
    items = []
    for kind in ("tasks", "waiting", "declined"):
        for row in raw[kind]:
            try:
                auth.task(c, who, row["id"])
            except Problem:
                continue
            open_asks = H.open_task_asks(c, row, actor=privacy.actor(who))
            ask = next((a for a in open_asks if a["to_actor"] == who.actor), None) or next(iter(open_asks), None)
            items.append({**task_view(row),
                          "kind": kind if kind in ("declined", "waiting") else "question" if ask else "task",
                          "origin_actor": H.task_origin(c, row), "ask": ask, "open_asks": len(open_asks),
                          "first_line": (row.get("body") or "").split("\n")[0]})
    for row in raw["approvals"]:
        msg = H.message(c, row["message_id"])
        if msg and msg["to_actor"] == who.actor and privacy.message_readable(c, privacy.actor(who), msg):
            items.append({**row, "kind": "approval", "what": row["kind"],
                          "title": "Approve this " + row["kind"],
                          "requester": row["requested_by"], "conversation_id": msg["conversation_id"]})
    return sorted(items, key=lambda x: needs_order(
        x["requester"] if x["kind"] == "approval" else (x.get("ask") or {}).get("from_actor") or x.get("origin_actor") or x.get("requester"),
        x["kind"], x["created"], x.get("rank") if x["kind"] == "task" else None))


FLEET_CACHE_S = 15
_FLEET_CACHE = {}


def fleet_snapshot_cached(c, auth, identity, task_view, ttl=FLEET_CACHE_S):
    """Build a fresh snapshot so participant changes revoke every read immediately."""
    # Participant changes revoke reads immediately, including already warmed snapshots.
    return fleet_snapshot(c, auth, identity, task_view)


def fleet_snapshot(c, auth, identity, task_view):
    """One live, actor-scoped control-plane snapshot for Tico's private room."""
    privacy.snapshot(c)
    principal = auth.tico_principal(c, identity)
    access = auth.bot_accesses(c, principal)
    bot_rows = H.bots(c)
    allowed = {row["slug"] for row in bot_rows if access.get(row["slug"], auth.FULL)["write"]}
    task_rows = [dict(t) for t in c.execute("SELECT * FROM tasks")]
    bot_tasks = {}
    for task in task_rows:
        for actor in {task["owner"], task["requester"]}:
            if H.is_bot(actor):
                bot_tasks.setdefault(H.actor_id(actor), []).append(task)
    statuses = {s["bot"]: dict(s) for s in c.execute("SELECT * FROM bot_status")}
    queued = {}
    from .privacy_index import ReadIndex
    index = ReadIndex(c, principal)
    for m in c.execute("SELECT m.*,j.bot,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'"):
        if (not dict(m).get("deleted_at") and not dict(m).get("deleted") and
                index.message(m["id"], m["conversation_id"], m["refs_json"], m["in_reply_to"]) and
                (not m["attempt_id"] or index.attempt(m["attempt_id"]))):
            queued[m["bot"]] = queued.get(m["bot"], 0) + 1
    bots = []
    for row in bot_rows:
        slug = row["slug"]
        if slug not in allowed:
            continue
        if not access.get(slug, auth.FULL)["read"]:
            # A bot the person may only send requests to: its name and that it exists, not its work.
            bots.append({"slug": slug, "name": row["display_name"], "state": row["state"], "focus": "",
                         "task_id": None, "online": None, "ready": None, "queued": None,
                         "active_attempt": None})
            continue
        # Fleet only exposes focus/task identity, so counters/approval scans are unnecessary.
        source = {k: v for k, v in statuses.get(slug, {}).items() if k not in ("open_tasks", "needs_human")}
        status = privacy.status(c, principal, source, tasks=bot_tasks.get(slug, [])) or {}
        location = machine(c, slug)
        attempt = c.execute(
            "SELECT id,state,started,lease_until FROM attempts WHERE bot=? "
            "AND state IN ('leased','running') AND lease_until>? ORDER BY created DESC LIMIT 1",
            (slug, H.now())).fetchone()
        if attempt and not index.attempt(attempt["id"]):
            attempt = None
        bots.append({"slug": slug, "name": row["display_name"],
                     "state": status.get("state") or row["state"],
                     "focus": status.get("focus") or "", "task_id": status.get("task_id"),
                     "online": location["online"], "ready": location["ready"],
                     "queued": queued.get(slug, 0),
                     "active_attempt": dict(attempt) if attempt else None})
    tasks = []
    for row in H.tasks(c, status=H.ACTIVE_STATUSES + ("declined",), visible=auth.task_sql(c, principal)):
        actors = (row["owner"], row["requester"])
        relevant = principal.actor in actors or any(
            str(actor).startswith("bot:") and H.actor_id(actor) in allowed for actor in actors)
        if not relevant:
            continue
        tasks.append({k: row.get(k) for k in
                      ("id", "title", "owner", "requester", "status", "due", "note", "updated")})
    items = needs_items(c, auth, principal, task_view)
    return {"actor": principal.actor, "as_of": H.now(), "bots": bots, "tasks": tasks,
            "needs_you": items,
            "approvals": [row for row in items if row.get("kind") == "approval"]}


def _stored_product_updates(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='product_updates'").fetchone()
    return json.loads(row[0]) if row else []


def _packaged_product_updates(settings):
    path = Path(settings.registry_dir) / "product-updates.yaml"
    if not path.exists():
        return []
    rows = yaml.safe_load(path.read_text()) or []
    out = []
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict) or not str(row.get("title") or "").strip():
            continue
        bullets = [str(item).strip() for item in (row.get("bullets") or []) if str(item).strip()]
        if not bullets:
            continue
        out.append({"id": "packaged-" + str(i), "title": str(row["title"]).strip(),
                    "bullets": bullets, "shipped_at": str(row.get("shipped_at") or "")})
    return out


def _product_updates(c, settings):
    extra = _stored_product_updates(c)
    seen = {row.get("title") for row in extra}
    return extra + [row for row in _packaged_product_updates(settings) if row["title"] not in seen]


def install_views(app, store, auth, mutate, task_view):
    def visible_turns(c, who, bot=None, limit=200):
        auth.domain(who)
        readable = None
        if bot:
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
        else:
            readable = {slug for slug, level in auth.bot_accesses(c, who).items() if level["read"]}
        rows = []
        for turn in H.turns(c, bot, limit=limit):
            if readable is not None and turn["bot"] not in readable:
                continue
            message = H.message(c, turn["message_id"])
            if not message:
                continue
            try:
                auth.conversation(c, who, message["conversation_id"])
                privacy.require_message(c, who, message)
                privacy.require_attempt(c, who, turn["id"])
            except Problem:
                continue
            rows.append((turn, message))
        for attempt in failed_attempts(c, who, auth, bot, limit=limit):
            rows.append((attempt, H.message(c, attempt["message_id"])))
        rows.sort(key=lambda item: item[0].get("started") or "", reverse=True)
        return rows[:limit]

    def run_view(turn, c=None):
        turn = {**turn, **attempt_details(c, turn)} if c else turn
        started, finished = H.parse_ts(turn.get("started")), H.parse_ts(turn.get("finished"))
        duration = max(0, round((finished - started).total_seconds())) if started and finished else None
        success = turn.get("exit") == "completed"
        fallback = c.execute("SELECT json_extract(result_json,'$.fallback') FROM attempts WHERE id=?",
                             (turn["id"],)).fetchone() if c is not None else None
        return {"run": turn["id"], "bot": turn["bot"], "employee": turn["bot"], "issue": None,
                "record_kind": turn.get("record_kind", "run"), "attempt_id": turn["id"],
                "outcome": turn.get("exit"), "failure_reason": turn.get("failure_reason") or "",
                "finished": turn.get("finished") or turn.get("started"),
                "exit": 0 if success else 1, "duration_s": duration,
                "output_tokens": turn.get("tokens_out"), "cost_usd": turn.get("cost"),
                "resumed": False, "fallback": fallback[0] if fallback else None}

    def employee_rows(c, who):
        people, configs = roster(c), entries(c, store.settings.github_owner)
        archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
        company = providers.load(c, store.settings)
        rows = []
        access = auth.bot_accesses(c, who)
        # The Material Symbol a bot's avatar wears: its own, else its template's. A bot made from a `kind: helper` card
        # serves a person, and the UI shows it with the built-ins, apart from the org chart.
        from .onboarding import helper_templates, template_icons
        icons, helpers = template_icons(store.settings), helper_templates(store.settings)

        def icon_of(config):
            return str(config.get("icon") or "") or icons.get(str(config.get("template") or ""), "")

        def helper(config):
            return str(config.get("template") or "") in helpers
        for bot in H.bots(c):
            slug = bot["slug"]
            from .chat_goals import readable_active
            bot["goal_active"] = readable_active(c, auth, who, slug)
            level = access.get(slug, auth.FULL)
            if bot.get("state") == "archived" or not level["see"]:
                continue
            registry = c.execute("SELECT operator,revision,goals,access_json,bot_owners_json FROM bot_config WHERE bot=?",
                                 (slug,)).fetchone()
            # Who may manage the bot (`Auth.bot_manager`), with the audiences and its owners when they may.
            pid = H.actor_id(who.actor)
            manager = (who.role == "owner" or (who.role == "human" and (
                auth.bot_admin(who) or (registry and (registry["operator"] == pid
                                                      or pid in A.owner_ids(registry["bot_owners_json"])))
                or P.manages(pid, "bot", slug, people, configs, archived))))
            policy = {"can_manage": manager, "bot_owners": bot_owner_rows(c, registry, slug, people, configs, archived)}
            if manager:
                policy["access_policy"] = A.document(registry["access_json"] if registry else None)
            if not level["read"]:
                # Seen, not read: the name, role, who runs it, who it reports to. Its
                # configuration, routines, machine and goals are its activity.
                reports = (configs.get(slug, {}) or {}).get("reports_to") or ""
                if reports and not str(reports).startswith("human:") and not access.get(reports, auth.FULL)["see"]:
                    reports = ""
                rows.append({"name": slug, "display_name": bot["display_name"], "state": bot["state"],
                             "status": bot["state"], "description": (configs.get(slug, {}) or {}).get("description") or "",
                             "reports_to": reports, "my_access": level, **policy,
                             "private_tasks_default": H.private_tasks_default(c, "bot:" + slug),
                             "team": P.team_of(slug, configs, people),
                             "org_parent": P.org_parent("bot", slug, people, configs, archived),
                             "operator": registry["operator"] if registry else None,
                             "is_branch": bool((configs.get(slug) or {}).get("shared_from")),
                             "users": [P.brief(p) for p in P.primary_users(slug, people, configs)],
                             "icon": icon_of(configs.get(slug, {}) or {}),
                             "helper": helper(configs.get(slug, {}) or {}),
                             "can_chat": may_chat(c, auth, who, slug)})
                continue
            config = configs.get(slug, {})
            # Configuration contains connector requirements, never connector credentials.
            config = {k: v for k, v in config.items() if k not in ("cwd", "env", "token", "secrets")}
            from .routines import listing
            config['schedules'] = listing(c, slug, summary=True)
            location = machine(c, slug)
            # Read-only, for the quiet runtime label beside a bot's name; empty when nothing resolves.
            try:
                resolved_runtime, resolved_model = providers.resolve(company, config)
            except providers.ProviderError:
                resolved_runtime = resolved_model = ""
            own = bool(providers._named(config.get("runtime")) or providers._named(config.get("model")))
            rows.append({**config, "resolved_runtime": resolved_runtime, "resolved_model": resolved_model,
                         "model_source": "" if not resolved_runtime else "bot" if own else "company", "name": slug, "display_name": bot["display_name"],
                         "state": bot["state"], "status": bot["state"],
                         "private_tasks_default": H.private_tasks_default(c, "bot:" + slug),
                         "host": "keeper", "tasks": "hub", "has_repo": location["ready"],
                         "team": P.team_of(slug, configs, people),
                         "org_parent": P.org_parent("bot", slug, people, configs, archived),
                         "operator": registry["operator"] if registry else None,
                         "revision": registry["revision"] if registry else None,
                         "goals": (registry["goals"] if registry else "") or "",
                         "goal_active": bot["goal_active"],
                         **location,
                         "users": [P.brief(p) for p in P.primary_users(slug, people, configs)],
                         "icon": icon_of(config), "helper": helper(config),
                         "my_access": level, **policy, "can_chat": may_chat(c, auth, who, slug)})
        return rows

    @app.get("/api/me")
    def me(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            r = roster(c)
            person = P.person(H.actor_id(who.actor), r) or {}
            bots = [row["name"] for row in employee_rows(c, who) if row["can_chat"]]
            from .credentials import administrator, can_open
            from .onboarding import config_view
            from .mail import can_access
            admins = store.settings.credential_admins
            role = auth.company_role(who)
            domains_ = Access.company_domains(c, store.settings, auth.owner_email)
            return {**person, "id": H.actor_id(who.actor), "email": who.email or person.get("email"),
                    "company_role": role, "can_create_bots": Access.can_create_bots(person, role),
                    "can_add_people": Access.can_add_people(person, role, domains_),
                    "company_domains": domains_,
                    "member_bot_limit": Access.load_access(c, store.settings)["member_bot_limit"],
                    "role": "owner" if who.role == "owner" else "viewer", "local": False,
                    "cloud": True, "registered": True, "chat_bots": bots,
                    "developer": P.is_developer(H.actor_id(who.actor), r),
                    "bot_admin": auth.bot_admin(who), "owner_id": auth.owner_id(c),
                    "config": config_view(c, store.settings, who),
                    "credential_admin": administrator(c, who, admins),
                    # The SQL page: the owner's, and the Admins' unless the owner's rule says otherwise.
                    "can_see_sql": who.role == "owner" or (auth.bot_admin(who) and team_rules.load(c)["admin_sql"]),
                    "proxy_session": who.via_proxy,
                    "sign_in_name": auth.sign_in_name(request.headers) if who.via_proxy else "",
                    "credential_access": bool(can_open(c, who, admins)),
                    "mail_access": can_access(c, who),
                    # may move any task on the board
                    "mover": who.role == "owner" or H.can_move(c, who.actor)}

    @app.get("/api/employees")
    def employees(request: Request):
        human_only(request.state.identity)
        with store.read() as c:
            return employee_rows(c, request.state.identity)

    @app.get("/api/people")
    def people(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            r, configs = roster(c), entries(c)
            from . import people_photos
            archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
            view = P.org_view(r, configs, archived)
            access = auth.bot_accesses(c, who)
            return {**r, "by_team": P.by_team(r), "org_groups": view["org_groups"], "people": [{
                **p, "org_parent": P.org_parent("person", p["id"], r, configs, archived),
                **({"photo_url": "/api/humans/" + p["id"] + "/photo"}
                   if people_photos.may_have(store.settings, p.get("email"), p.get("photo")) else {}),
                "bots": [b for b in P.bots_of(p["id"], r, configs)
                         if access.get(b, auth.FULL)["see"] and b not in archived]}
                for p in r["people"]]}

    @app.get("/api/people/{pid}/photo")
    def person_photo(request: Request, pid: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            r = roster(c)
            row = P.person(pid, r)
            if not row or row.get("hidden"):
                raise Problem("not_found", "Person not found", 404)
            from . import people_photos
            fetched = people_photos.load(store.settings, row.get("email"),
                                         subject=store.settings.owner_email)
            if fetched:
                data, mime = fetched
                # A Workspace photo keeps for a week (the server's copy does too).
                return Response(content=data, media_type=mime,
                                headers={"Cache-Control": "private, max-age=604800"})
            kept = people_photos.remote_photo(store.settings, row.get("photo")) if row.get("photo") else None
            if kept:
                return Response(content=kept[0], media_type=kept[1],
                                headers={"Cache-Control": "private, max-age=604800"})
            if row.get("photo"):
                return RedirectResponse(row["photo"], status_code=302,
                                        headers={"Cache-Control": "private, max-age=86400"})
            raise Problem("not_found", "No photo", 404)

    @app.get("/api/issues")
    def legacy_issues(request: Request):
        human_only(request.state.identity)
        # The local app already retired GitHub Issues; shared tasks are at /api/v2/tasks.
        return []

    @app.get("/api/v2/operations")
    def operations(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            machines, fleet = [], runner_versions.load(c)
            wanted, assigned = providers.runtimes_needed(c, store.settings)
            for row in c.execute("SELECT id,label,operator,last_seen,revoked_at,platform,version,capacity,readiness_json,"
                                 "accepts_member_bots FROM runners ORDER BY created"):
                if not auth.bot_admin(who) and who.actor != "human:" + row["operator"]:
                    continue
                bots = [a[0] for a in c.execute("SELECT bot FROM assignments WHERE runner_id=? ORDER BY bot", (row["id"],))]
                value = dict(row)
                value["release"] = runner_versions.view(fleet.get(row["id"])).get("release") or ""
                value["accepts_member_bots"] = bool(row["accepts_member_bots"])
                value["readiness"] = readiness_document(value.pop("readiness_json"))
                from .repositories import metadata
                value["repositories"] = metadata(c, "computer-repositories:" + row["id"]).get("repositories", "unknown")
                assigned_here = {a[0] for a in c.execute(
                    "SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
                    "WHERE a.runner_id=? AND b.state<>'archived'", (row["id"],))}
                value["readiness"]["bots"] = {bot: report for bot, report in value["readiness"].get("bots", {}).items()
                                             if bot in assigned_here}
                for report in value["readiness"].get("bots", {}).values():
                    report.pop("tools", None)
                from .harness_actions import recent
                machines.append({**value, "bots": bots, "needed_runtimes": sorted(wanted | assigned.get(row["id"], set())),
                                 "update": runner_versions.view(fleet.get(row["id"])),
                                 "harness_actions": recent(c, row["id"]) if who.role == "owner" else []})
            health = [{**dict(r), "name": SERVICE_NAMES.get(r["service"], r["service"])}
                      for r in c.execute("SELECT service,last_success,last_error FROM service_health")]
            from .agents import listing as agent_listing
            # `machines` is the older name of `computers`, kept for older clients.
            return {"cloud": True, "computers": machines, "machines": machines, "agents": agent_listing(c, who, auth), "services": health,
                    "issues": operation_issues(c, who, auth),
                    "server_time": H.now(), "scheduler_enabled": store.settings.scheduler_enabled}

    @app.get("/api/status")
    def status(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            health = c.execute("SELECT * FROM service_health WHERE service='scheduler'").fetchone()
            tick = health["last_success"] if health else None
            age = max(0, (H.parse_ts(H.now()) - H.parse_ts(tick)).total_seconds()) if tick else None
            active, queued, schedules = [], [], []
            # Cancelled and uncertain jobs are not "up next": uncertain work already
            # surfaces as a health issue, cancelled work is history.
            readable = auth.bot_accesses(c, who)
            for row in c.execute("SELECT j.*,a.started,t.title AS task_title,m.from_actor FROM jobs j "
                                 "LEFT JOIN attempts a ON a.id=j.attempt_id JOIN messages m ON m.id=j.message_id "
                                 f"JOIN conversations cv ON cv.id=m.conversation_id LEFT JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
                                 "WHERE j.state IN ('queued','leased','running','input') ORDER BY j.created"):
                if readable.get(row["bot"], auth.FULL)["read"]:
                    if not privacy.message_readable(c, privacy.actor(who), H.message(c, row["message_id"])):
                        continue
                    if row["attempt_id"] and not privacy.attempt_readable(c, privacy.actor(who), row["attempt_id"]):
                        continue
                    # Never the message text: a shared bot's private chats belong to their people.
                    title = row["task_title"] or "Message from " + H.actor_id(row["from_actor"])
                    item = {"employee": row["bot"], "bot": row["bot"], "id": row["id"], "started": row["started"],
                            "state": row["state"], "created": row["created"],
                            "title": title if len(title) <= 80 else title[:79].rstrip() + "\u2026"}
                    (queued if row["state"] == "queued" else active).append(item)
            from .routines import listing
            schedules = [{**row, 'last_checked': tick} for row in listing(c, summary=True) if readable.get(row['bot'], auth.FULL)['read']]
            return {"cloud": True, "last_tick": tick, "active": active, "queued": queued,
                    "recent_runs": [], "keeper_alive": age is not None and age < 60,
                    "dispatcher_alive": False, "tick_age_s": age, "server_time": H.now(), "schedules": schedules,
                    "health_issues": operation_issues(c, who, auth)}

    @app.get("/api/runs")
    def runs(request: Request):
        human_only(request.state.identity)
        with store.read() as c:
            return [run_view(turn, c) for turn, _ in visible_turns(c, request.state.identity)]

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(request: Request, run_id: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            turn = c.execute("SELECT * FROM turns WHERE id=?", (run_id,)).fetchone()
            if not turn:
                turn = next((r for r in failed_attempts(c, who, auth) if r["id"] == run_id), None)
            if not turn:
                raise Problem("not_found", "Run not found", 404)
            rows = visible_turns(c, who, turn["bot"])
            message = next((message for allowed, message in rows if allowed["id"] == run_id), None)
            if not message:
                raise Problem("not_found", "Run not found or unavailable to this account", 404)
            attempt = c.execute("SELECT * FROM attempts WHERE id=?", (run_id,)).fetchone()
            events = c.execute("SELECT seq,kind,payload_json,created FROM attempt_events "
                               "WHERE attempt_id=? ORDER BY seq", (run_id,)).fetchall()
            lines = [store.settings.app_name + " cloud run " + run_id, "Bot: " + turn["bot"],
                     "Started: " + str(turn["started"]), "Finished: " + str(turn["finished"]),
                     "Outcome: " + str(turn["exit"])]
            result = json.loads(attempt["result_json"]) if attempt and attempt["result_json"] else {}
            if result.get("fallback"):
                lines.append("Fallback: " + str(result["fallback"]) + " (primary harness unavailable; the turn ran on the fallback)")
            lines += ["", "Request:", message["body"]]
            if events:
                lines.extend(["", "Persisted runtime events:"])
                lines.extend(encode(dict(row)) for row in events)
            if attempt and attempt["final_text"]:
                lines.extend(["", "Final reply:", attempt["final_text"]])
            return "\n".join(lines) + "\n"

    @app.get("/api/changelog")
    def changelog(request: Request):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            entries = []
            for row in _product_updates(c, store.settings):
                entries.append({**row, "area": "Product", "kind": "product"})
            for task in H.tasks(c, status="done"):
                if not str(task.get("owner", "")).startswith("bot:") or len((task.get("note") or "").strip()) < 20:
                    continue
                try:
                    auth.task(c, who, task["id"])
                except Problem:
                    continue
                note = re.sub(r"\s+", " ", task["note"]).strip()
                entries.append({"id": "activity-" + task["id"], "title": task["title"],
                                "bullets": [note[:280]], "area": "Agent activity", "kind": "activity",
                                "shipped_at": task.get("done_at") or task["updated"], "task_id": task["id"]})
            entries.sort(key=lambda row: row["shipped_at"] or "", reverse=True)
            return {"entries": entries[:120], "drafts": [], "can_review": False,
                    "can_add": who.role == "owner"}

    @app.post("/api/changelog")
    def add_product_update(request: Request, body: M.ChangelogPost):
        who = request.state.identity
        human_only(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner can publish a product update", 403)
        bullets = [item.strip() for item in body.bullets if item.strip()]
        if not bullets:
            raise Problem("changelog", "A product update needs at least one bullet", 422)
        def work(c):
            extra = _stored_product_updates(c)
            extra.insert(0, {"id": "product-" + H.now().replace(":", "").replace("-", ""),
                             "title": body.title.strip(), "bullets": bullets, "shipped_at": H.now()})
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('product_updates',?)",
                      (encode(extra[:80]),))
            return {"ok": True}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/instructions")
    def bot_instructions(request: Request, bot: str):
        who = request.state.identity
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            row = c.execute("SELECT content,updated FROM bot_agent_instructions WHERE bot=?", (bot,)).fetchone()
            if not row:
                row = c.execute("SELECT content,updated FROM mail_agent_instructions WHERE bot=?", (bot,)).fetchone()
            return {"bot": bot, "content": row["content"] if row else "",
                    "published": bool(row and row["content"].strip()), "updated": row["updated"] if row else None,
                    "source": "Computer snapshot" if row else "Not published yet"}

    @app.get("/api/employees/{bot}/files")
    def bot_files(request: Request, bot: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
            config = json.loads(row[0]) if row else {}
            safe = {k: v for k, v in config.items() if k not in ("cwd", "env", "token", "secrets", "secrets_file")}
            status = H.status(c, bot) or {}
            instruction = c.execute("SELECT content FROM bot_agent_instructions WHERE bot=?", (bot,)).fetchone()
            if not instruction:
                instruction = c.execute("SELECT content FROM mail_agent_instructions WHERE bot=?", (bot,)).fetchone()
            instructions = instruction["content"] if instruction and instruction["content"].strip() else ""
            state = "# Cloud status\n\nState: " + str(status.get("state") or H.bot(c, bot)["state"])
            if status.get("focus"):
                state += "\n\nFocus: " + status["focus"]
            if status.get("last_result"):
                state += "\n\nLast result: " + status["last_result"]
            from .memory_history import documents
            memory = documents(c, bot)
            return {"AGENT.md": instructions,
                    "state.md": state + "\n", "memory/learnings.md": memory.get("memory/learnings.md", ""),
                    "memory/decisions.md": memory.get("memory/decisions.md", ""),
                    "bot.yaml": yaml.safe_dump(safe, sort_keys=False, allow_unicode=True),
                    "employee.yaml": yaml.safe_dump(safe, sort_keys=False, allow_unicode=True),
                    "playbooks": {}, "source": "cloud registry and status snapshot"}

    @app.get("/api/employees/{bot}/session")
    def bot_session(request: Request, bot: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            rows = list(reversed(visible_turns(c, who, bot, limit=100)))
            turns = []
            for turn, message in rows:
                turns.append({"role": "user", "text": message["body"], "ts": turn["started"]})
                attempt = c.execute("SELECT final_text FROM attempts WHERE id=?", (turn["id"],)).fetchone()
                if attempt and attempt[0]:
                    turns.append({"role": "assistant", "text": attempt[0],
                                  "ts": turn["finished"] or turn["started"]})
            config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
            cfg = json.loads(config[0]) if config else {}
            runtime, model = providers.bot_choice(c, auth.settings, cfg)
            runtime = runtime or "local"
            session = c.execute("SELECT * FROM bot_sessions WHERE bot=? AND runtime=? AND model=?",
                                (bot, str(runtime), model)).fetchone() if runtime else None
            if session and any(not privacy.attempt_readable(c, privacy.actor(who), r[0]) for r in c.execute(
                    "SELECT id FROM attempts WHERE bot=? AND thread_id=?", (bot, session["thread_id"]))):
                session = None
            return {"cloud": True, "current": True, "runtime": runtime, "model": model,
                    "session_id": (session["thread_id"] if session
                                   else (rows[-1][0].get("thread_id") if rows else "cloud")),
                    "provider_session": dict(session) if session else None,
                    "first_ts": turns[0]["ts"] if turns else None,
                    "last_ts": turns[-1]["ts"] if turns else None,
                    "turn_count": len(turns), "shown": len(turns), "skipped": 0,
                    "path": "canonical cloud history",
                    "note": ("Provider session files stay on the assigned Mac; the hub keeps the "
                             "conversation, which the bot reads with `hub conversation show`."),
                    "runs": [{"run": turn["id"], "issue": None} for turn, _ in rows], "turns": turns}

    @app.get("/api/v2/me/recent")
    def my_recent(request: Request, days: int = 7, limit: int = 10):
        """The bots I have been working with lately, most recent first, each with
        its live status, the last thing I said and the last thing it said, the conversation to read
        on (`hub_conversation_show`), our open tasks and what it needs from me. `hub_bot_recent` over MCP, so an
        agent of mine (a Grok bot) can carry on where I left off."""
        who = request.state.identity
        human_only(who)
        days, limit = max(1, min(int(days), 90)), max(1, min(int(limit), 30))
        since = H.shift(H.now(), days=-days)
        with store.read() as c:
            return {"actor": who.actor, "since": since,
                    "bots": recent_bots(c, auth, who, since, limit, needs_items(c, auth, who, task_view))}

    @app.get("/api/v2/needs-you")
    def needs(request: Request, count: bool = False):
        """`count=1` is the number alone: the desktop app's tray asks every 30 s and shows only
        that, where the full list is over 100 KB."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            items = needs_items(c, auth, who, task_view)
            return {"actor": who.actor, "count": len(items)} if count else {"actor": who.actor, "items": items}

    @app.get("/api/v2/tico/fleet")
    def tico_fleet(request: Request):
        with store.read() as c:
            return fleet_snapshot(c, auth, request.state.identity, task_view)

    def task_chat(c, who, tid, text=None, expected_recipient=None):
        human_only(who)
        task = auth.task(c, who, tid)
        candidates = [task["owner"], task["requester"], H.task_origin(c, task)]
        # No assistant fallback since its chat was retired: a task with no bot has nobody to chat to.
        slug = next((H.actor_id(p) for p in candidates if str(p).startswith("bot:")
                     and auth.visible_bot(c, who, H.actor_id(p)) and H.bot(c, H.actor_id(p))), None)
        if slug:
            from .shared_bots import route
            slug = H.actor_id(route(c, who.actor, "bot:" + slug))
        if H.task_private(c, task) and slug and not H.task_private_readable(c, "bot:" + slug, task):
            slug = None
        bot = (H.bot(c, slug) if slug else None) or {}
        allowed = bool(slug) and may_chat(c, auth, who, slug)
        conv = rooms.chat_room(c, auth, who, slug) if allowed else None
        if H.task_private(c, task) and conv:
            conv = auth.conversation(c, who, task["conversation_id"])
        message = None
        if text is not None:
            if expected_recipient and expected_recipient != slug:
                raise Problem("recipient_changed", "Task ownership changed; refresh before sending", 409)
            if not slug:
                raise Problem("no_bot", "No bot owns or asked for this task; assign it to a bot "
                              "to discuss it, or comment on the task", 409)
            if not allowed:
                auth.require_write(c, who, slug)
                raise Problem("forbidden", "This bot takes requests in its own Assistant room, not here", 403)
            privacy.require_destination(c, who, "bot:" + slug, conv["id"], {"task": tid})
            message = H.say(c, who.actor, "bot:" + slug, text, conversation_id=conv["id"], refs={"task": tid})
            c.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)",
                      (tid, "bot:" + slug, who.actor, message["id"], H.shift(H.now(), hours=24)))
        tagged = [m for m in (privacy.page(c, who, conv["id"])["messages"] if conv else [])
                  if H.message_task_id(m) == tid]
        recipient = {"slug": slug, "name": bot.get("display_name") or slug, "can_chat": allowed} if slug else None
        return {"bot": slug, "recipient": recipient, "conversation": conv, "message": message, "messages": tagged}

    @app.get("/api/v2/tasks/{tid}/chat")
    def task_chat_read(request: Request, tid: str):
        with store.read() as c:
            return task_chat(c, request.state.identity, tid)

    @app.post("/api/v2/tasks/{tid}/chat")
    def task_chat_write(request: Request, tid: str, body: M.TaskChat):
        return mutate(request, body, lambda c: task_chat(c, request.state.identity, tid, body.text, body.expected_recipient))

    @app.post("/api/employees/{bot}/session/clear")
    def clear_session(request: Request, bot: str, body: M.Empty):
        who = request.state.identity
        human_only(who)
        def work(c):
            require_chat(c, auth, who, bot)
            conv = next((row for row in H.conversations_for(c, who.actor) if row["kind"] == "chat"
                         and not row.get("task_id") and not row["closed_at"]
                         and set(row["participants"]) == {who.actor, "bot:" + bot}), None)
            if not conv:
                return {"cleared": True}
            active = c.execute("SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                               "WHERE m.conversation_id=? AND a.state IN ('leased','running') AND a.lease_until>?", (conv["id"], H.now())).fetchone()
            if active:
                raise Problem("busy", "Wait for this conversation's current turn to finish", 409)
            c.execute("INSERT INTO session_epochs VALUES(?,1,?) ON CONFLICT(conversation_id) DO UPDATE SET "
                      "epoch=epoch+1,updated=excluded.updated", (conv["id"], H.now()))
            # Drop the provider pointer so the next turn tries a fresh thread and loads the
            # recovery pack from Hub history instead of resuming the cleared session.
            config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
            if config:
                cfg = json.loads(config[0] or "{}")
                c.execute("DELETE FROM bot_sessions WHERE bot=? AND runtime=? AND model=?",
                          (bot, *providers.bot_choice(c, auth.settings, cfg)))
            H.event(c, who.actor, "session.clear", conv["id"])
            return {"cleared": True, "conversation_id": conv["id"]}
        return mutate(request, body, work)

    @app.post("/api/v2/page-chat")
    def page_chat(request: Request, body: M.PageChat):
        who = request.state.identity
        human_only(who)
        def work(c):
            if not body.task_id:
                raise Problem("validation", "Page chat covers a single task", 422)
            if not body.text.strip():
                raise Problem("validation", "Write a message about this task", 422)
            return task_chat(c, who, body.task_id, body.text)
        return mutate(request, body, work)

    @app.get("/api/v2/conversations/{cid}/snapshot")
    def snapshot(request: Request, cid: str):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            snap = conversation_snapshot(c, cid, request.state.identity)
            turns.annotate(c, auth, request.state.identity, snap["messages"])
            return snap

    @app.get("/api/v2/conversations/{cid}/watch")
    async def watch(request: Request, cid: str):
        who = request.state.identity
        def read_snapshot():
            auth.authenticate(request.headers)
            with store.read() as c:
                auth.conversation(c, who, cid)
                snap = conversation_snapshot(c, cid, who)
                turns.annotate(c, auth, who, snap["messages"])
                return encode(snap)
        def read_mark():
            with store.read() as c:
                return snapshot_mark(c, cid)
        initial = await asyncio.to_thread(read_snapshot)
        async def generate():
            previous, mark = initial, await asyncio.to_thread(read_mark)
            previous_goal = json.loads(initial).get("goal")
            yield f"event: snapshot\ndata: {initial}\n\n"
            yield f"event: goal\ndata: {encode({'type': 'goal', 'goal': previous_goal})}\n\n"
            for tick in range(55):
                await asyncio.sleep(1)
                if await request.is_disconnected():
                    return
                # The full snapshot (the newest 200 messages, the run and its steps) is rebuilt
                # only when the conversation's mark moves, and every fifth second in any case for
                # what the mark does not see (a computer going offline, a card's refs changing).
                try:
                    now_mark = await asyncio.to_thread(read_mark)
                    if now_mark == mark and tick % 5 != 4:
                        yield ': keepalive\n\n'
                        continue
                    mark = now_mark
                    current = await asyncio.to_thread(read_snapshot)
                except Problem:
                    yield 'event: expired\ndata: {}\n\n'
                    return
                if current != previous:
                    yield f"event: snapshot\ndata: {current}\n\n"
                    goal = json.loads(current).get("goal")
                    if goal != previous_goal:
                        yield f"event: goal\ndata: {encode({'type': 'goal', 'goal': goal})}\n\n"
                        previous_goal = goal
                    previous = current
                else:
                    yield ': keepalive\n\n'
            # EventSource reconnects and receives a complete snapshot, not duplicate deltas.
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
