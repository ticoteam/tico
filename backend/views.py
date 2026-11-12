"""Existing frontend read models over cloud records. Never import the local hub server."""

import hashlib
import json
from copy import deepcopy
import os
import re
from datetime import timezone
from typing import Literal

import yaml
from fastapi import Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from clients import gateway, sessions

from . import access as Access
from . import cloud_access, start_places
from . import lifecycle
from . import bot_access as A
from . import models as M
from . import changelog as CL
from . import releases
from . import visuals
from . import app_delivery, login_holds, needs_lane, providers, runner_versions, runs
from .runs import LIVE as LIVE_RUN_STATES
from .harnesses import resolve_harness
from . import personal_tokens, profiles, rooms, team_rules, turns, wake_presets
from . import bot_rows, bot_stops, run_triggers

from . import read_cache, roster_clock
from .store import (
    H,
    P,
    SNAPSHOT_BACKUP_ROWS,
    Problem,
    bot_readiness,
    encode,
    message_page,
    readiness_document,
    repo_url,
)
from . import task_privacy as privacy
from .usage_records import ran_on
from . import task_relations as TR
from . import bot_settings as BS


MIN_RUNNER_VERSION = (0, 2, 0)
DOC_BOT = "doc-updater"  # the documentation agent: docs questions and doc PR reviews
# the Strategy Owner (backend/strategy_bot.py SLUG): of people, only the owner chats with it
STRATEGY_BOT = "strategy-owner"


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
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    ) + "Z"


ATTEMPT_DETAIL_SQL = "SELECT state,result,finished_at FROM attempts WHERE id=?"


def prime_attempt_details(c, ids):
    """Read the rows attempt_details asks for, one table at a time, before a page walks each turn."""
    if read_cache.cache(c) is None:
        return
    wanted = [aid for aid in dict.fromkeys(ids) if aid]
    for start in range(0, len(wanted), 250):
        chunk = wanted[start : start + 250]
        marks = ",".join("?" * len(chunk))
        attempts = {
            row["id"]: row
            for row in c.execute(f"SELECT id,state,result,finished_at FROM attempts WHERE id IN ({marks})", chunk)
        }
        read_cache.prime(c, ATTEMPT_DETAIL_SQL, {(aid,): attempts.get(aid) for aid in chunk})
        diags = {
            row["attempt_id"]: row
            for row in c.execute(
                "SELECT attempt_id,end_reason,ended_at,last_event,last_event_at,heartbeat_at,stalled_at "
                f"FROM attempt_diagnostics WHERE attempt_id IN ({marks})",
                chunk,
            )
        }
        read_cache.prime(
            c,
            "SELECT end_reason,ended_at,last_event,last_event_at,heartbeat_at,stalled_at "
            "FROM attempt_diagnostics WHERE attempt_id=?",
            {(aid,): diags.get(aid) for aid in chunk},
        )
        snaps = {
            row["attempt_id"]: row
            for row in c.execute(
                f"SELECT attempt_id,at_start,at_end FROM attempt_load_snapshots WHERE attempt_id IN ({marks})", chunk
            )
        }
        read_cache.prime(
            c,
            "SELECT at_start,at_end FROM attempt_load_snapshots WHERE attempt_id=?",
            {(aid,): snaps.get(aid) for aid in chunk},
        )


def attempt_details(c, turn):
    """What a run's attempt row adds to its turn: `state` (run.state, backend/runs.py; None for a turn with no attempt
    row), its diagnostics and load, and why it failed."""
    attempt = read_cache.one(c, ATTEMPT_DETAIL_SQL, (turn["id"],))
    failed = bool(attempt and attempt["state"] in ("failed", "expired"))
    from .run_diagnostics import metadata
    from .performance import attempt_snapshots

    details = {
        "state": runs.state(attempt["state"]) if attempt else None,
        **metadata(c, turn["id"]),
        **attempt_snapshots(c, turn["id"]),
    }
    if failed:
        try:
            result = json.loads(attempt["result"] or "{}")
        except (ValueError, TypeError):
            result = {}
        reason = (
            login_holds.failure_line(result)
            or result.get("error")
            or result.get("reason")
            or result.get("message")
            or (
                "Lease expired before completion"
                if attempt["state"] == "expired"
                else "Attempt failed before completion"
            )
        )
        details.update(
            exit=attempt["state"],
            finished_at=attempt["finished_at"] or turn.get("finished_at"),
            failure_reason=login_holds.failure_line(result) or details["end_reason"] or str(reason),
        )
    return details


def failed_attempts(c, who, auth, bot=None, since=None, limit=200):
    readable = auth.bot_accesses(c, who)
    rows = []
    # One bot's runs as their own query text: `? IS NULL OR a.bot=?` would hide attempts_bot_state.
    found = list(
        c.execute(
            "SELECT a.id,a.bot_id,a.started_at,a.created_at,a.finished_at,a.state,a.result,j.message_id FROM attempts a "
            "JOIN jobs j ON j.id=a.job_id WHERE a.state IN ('failed','expired') "
            + ("AND a.bot_id=? " if bot is not None else "")
            + "AND (? IS NULL OR coalesce(a.finished_at,a.created_at)>=?) AND a.trigger IS NULL "
            "ORDER BY coalesce(a.finished_at,a.created_at) DESC LIMIT ?",
            ((bot,) if bot is not None else ()) + (since, since, limit),
        )
    )
    stored = H.prime_messages(c, [row["message_id"] for row in found])
    privacy.prime_messages(c, list(stored.values()))
    from . import threads

    threads.prime_members(c, who, [row["conversation_id"] for row in stored.values()])
    privacy.prime_attempts(c, [row["id"] for row in found])
    prime_attempt_details(c, [row["id"] for row in found])
    for row in found:
        if not (readable.get(row["bot_id"]) or {}).get("read"):
            continue
        message = H.message(c, row["message_id"])
        if not message:
            continue
        try:
            auth.run_conversation(c, who, row["bot_id"], message)
            privacy.require_message(c, who, message)
            privacy.require_attempt(c, who, row["id"])
        except Problem:
            continue
        try:
            result = json.loads(row["result"] or "{}")
        except (ValueError, TypeError):
            result = {}
        reason = (
            login_holds.failure_line(result)
            or result.get("error")
            or result.get("reason")
            or result.get("message")
            or ("Lease expired before completion" if row["state"] == "expired" else "Attempt failed before completion")
        )
        rows.append(
            {
                "id": row["id"],
                "bot": row["bot_id"],
                "started_at": row["started_at"] or row["created_at"],
                "finished_at": row["finished_at"],
                "exit": row["state"],
                "message_id": row["message_id"],
                "failure_reason": str(reason),
                **attempt_details(c, {"id": row["id"], "finished_at": row["finished_at"]}),
            }
        )
    return rows


def computer_details(c, row, who, auth):
    value = dict(row)
    readiness = readiness_document(value.get("readiness"))
    access = auth.bot_accesses(c, who)
    assigned = assignments_by_runner(c)[1].get(row["id"]) or set()
    shared = shared_logins(c, row["id"], readiness, assigned, access)
    readiness["bots"] = {
        bot: {k: v for k, v in report.items() if k != "tools"}
        for bot, report in readiness.get("bots", {}).items()
        if bot in assigned and (access.get(bot) or {}).get("read") and isinstance(report, dict)
    }
    from .subscriptions import may_see_accounts, without_accounts

    if not may_see_accounts(auth, who, value):
        without_accounts(readiness.get("runtimes") or {}, windows=True)
    update = runner_versions.view(runner_versions.load(c).get(row["id"]))
    update["wanted_release"] = runner_versions.desired()["version"]
    from .repositories import computer_repositories

    return {
        "repositories": computer_repositories(c).get(row["id"], "unknown"),
        "version": value.get("version") or "",
        "release": update.get("release") or "",
        "last_seen_at": value.get("last_seen_at"),
        "readiness": readiness,
        "update": update,
        "fix": "Open Settings > Computers to retry the update" if update.get("error") else "Open Settings > Computers",
        "services": [],
        "services_scope": "team",
        "shared_logins": shared,
    }


def subscription_login(runtime, detail):
    """Whether a sign-in detail names a subscription login, as the runner bills it (runner-rs/src/execute.rs
    billing_for): "Signed in ..." naming the harness's `subscription_marker` and no API key."""
    from .harnesses import harness_caps

    marker = str(harness_caps(runtime).get("subscription_marker") or "").lower()
    detail = str(detail or "")
    return (
        bool(marker)
        and detail.lower().startswith("signed in")
        and marker in detail.lower()
        and not re.search(r"(?i)api[ _-]?key", detail)
    )


def assignments_by_runner(c):
    """One pass of assignments: {runner_id: [bot, ...]} ordered, and {runner_id: {non-archived bots}}.

    Operations and Computers listing used to SELECT assignments once (or twice) per computer. Cached for the read
    snapshot (`computer_details` asks once per computer); callers only read it.
    """
    from . import read_cache

    values = read_cache.cache(c)
    if values is not None and "views.assignments_by_runner" in values:
        return values["views.assignments_by_runner"]
    ordered, active = {}, {}
    for row in c.execute(
        "SELECT a.runner_id, a.bot, b.state FROM assignments a JOIN bots b ON b.id=a.bot ORDER BY a.bot"
    ):
        ordered.setdefault(row["runner_id"], []).append(row["bot"])
        if row["state"] != "archived":
            active.setdefault(row["runner_id"], set()).add(row["bot"])
    if values is not None:
        values["views.assignments_by_runner"] = (ordered, active)
    return ordered, active


def shared_logins(c, runner_id, readiness, assigned, access, names=None):
    """The sign-ins on this computer that several bots run on, for a harness whose subscription login serves one turn
    at a time (`one_turn_per_login`): those bots take turns (runner-rs/src/login_turns.rs). Codex opts out and is
    omitted: its bots run concurrently on one ChatGPT login. One row per login: its
    runtime, its subscription (`profile`, '' for the computer's own sign-in) and `display_name`, how many bots share
    it and the ones this reader may see. A key login runs its bots side by side and is left out.

    `names` is `{connection_id: display_name}` from `subscription_identity.display_names` when the caller already
    loaded every rename (Operations listing).
    """
    from .harnesses import harness_caps
    from .subscription_identity import connection_id, identity

    runtimes = readiness.get("runtimes") or {}
    logins = {}
    for bot, report in (readiness.get("bots") or {}).items():
        if bot not in assigned or not isinstance(report, dict):
            continue
        runtime, profile = str(report.get("runtime") or ""), str(report.get("profile") or "")
        if not harness_caps(runtime).get("one_turn_per_login"):
            continue
        row = runtimes.get(runtime) or {}
        login = ((row.get("profiles") or {}).get(profile) if profile and row.get("profiles") else row) or {}
        if not subscription_login(runtime, login.get("detail")):
            continue
        logins.setdefault((runtime, profile), []).append(bot)
    rows = []
    for (runtime, profile), bots in sorted(logins.items()):
        if len(bots) <= 1:
            continue
        if not profile:
            label = ""
        elif names is not None:
            label = names.get(connection_id(runner_id, profile)) or profile
        else:
            label = identity(c, runner_id, profile)["display_name"]
        rows.append(
            {
                "runtime": runtime,
                "profile": profile,
                "display_name": label,
                "count": len(bots),
                "bots": sorted(b for b in bots if (access.get(b) or {}).get("read")),
            }
        )
    return rows


SERVICE_NAMES = {"connector:calendar": "Calendar Tool", "connector:mail": "Mail Tool"}


def team_services(c, who, auth):
    if who.role != "owner" and not auth.bot_admin(who):
        return []
    return [
        {
            **dict(r),
            "scope": "team",
            "name": SERVICE_NAMES.get(r["service"], r["service"]),
            "text": f"{SERVICE_NAMES.get(r['service'], r['service'])}: {r['last_error'] or 'No reported error'}",
            "fix": "Open Health for " + SERVICE_NAMES.get(r["service"], r["service"]),
        }
        for r in c.execute("SELECT service,last_succeeded_at,last_error FROM service_health ORDER BY service")
    ]


def roster(c, shared=False):
    return read_cache.value(c, ("roster",), lambda: _roster(c), shared)


def _roster(c):
    row = read_cache.metadata(c, "people")
    if row:
        return P.load(json.loads(row[0]))
    return P.load({"people": H.humans(c)})


def entries(c, github_owner="", shared=False):
    # Built from bot_config alone (follow reads the branch's original's row there too), so it is shared between
    # snapshots until that table changes (backend/roster_clock.py), keyed on the GitHub owner it names repositories by.
    key = ("view_entries", github_owner)
    found = read_cache.value(
        c, key, lambda: roster_clock.shared(c, key, lambda: _entries(c, github_owner)), shared=True
    )
    # A caller that enriches its copy gets its own: read_cache hands out the shared object outside a snapshot too.
    return found if shared else deepcopy(found)


def seat_parents(c, settings, auth, who):
    """{open seat id: where it hangs on the team chart for this caller}, as GET /api/people draws it
    (backend/lifecycle.py `chart_seats`): GET /api/v2/seats carries the same, so a client that refreshes the seats from
    there still hangs a seat under a closed seat where that seat answered to."""
    from . import strategy_bot

    r, configs = roster(c), entries(c)
    archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
    chart = strategy_bot.chart_entries(c, settings, r, configs, archived, hidden=strategy_bot.hidden_for(c, who))
    access = auth.bot_accesses(c, who)
    return {
        seat["id"]: seat["org_parent"]
        for seat in lifecycle.chart_seats(c, r, chart, archived, lambda slug: access.get(slug, auth.FULL)["see"])
    }


def _entries(c, github_owner=""):
    result = {}
    for row in read_cache.configs(c):
        from .shared_bots import follow

        config = follow(c, row["bot"], json.loads(row["config"]))
        if row["onboarding_state"]:
            config["onboarding_state"] = row["onboarding_state"]
        repo = config.get("repo") or row["repo"] or ("emp-" + (config.get("name") or row["bot"]))
        config.update(
            {
                "description": row["description"] or "",
                "reports_to": row["reports_to"],
                "repo": repo,
                "repo_url": repo_url(repo, github_owner),
            }
        )
        if row["thread_mode"]:
            config["thread_mode"] = row["thread_mode"]
        if row["owner_ids"] is not None:
            config["owner_ids"] = json.loads(row["owner_ids"])
        result[row["bot"]] = config
    return result


def human_only(who):
    if who.role not in ("owner", "human"):
        raise Problem("identity", "This frontend endpoint requires a person", 403)


def person_or_view(who):
    """The page's own start-up reads: a person, or a bot's read-only view of the app (backend/app_view.py), which reads
    them as that bot."""
    if not who.view:
        human_only(who)


# What a bot's read-only view is never offered in /api/me, whatever the bot may do elsewhere.
VIEW_OFF = (
    "can_create_bots",
    "can_add_people",
    "bot_admin",
    "credential_admin",
    "can_see_sql",
    "can_create_tokens",
    "has_tokens",
    "credential_access",
    "mail_access",
    "mover",
)


def may_chat(c, auth, who, bot):
    """Whether the caller may send this bot messages: Write on it (backend/bot_access.py). Who a
    bot "works for" (Can use) no longer decides it."""
    if not auth.bot_access(c, who, bot)["write"]:
        return False
    if who.role == "bot":
        return True
    # The assistant is chatted with in one place only: each person's own Assistant room, through
    # /api/v2/assistant (backend/assistant.py). It has no chat with anyone else and no shared or
    # per-bot chat, so every other route that asks "may I chat with it" says no. The Strategy Owner the
    # same: no person chats with it, their Assistant hands it work; an earlier chat with it anyone had
    # stays readable, read-only.
    return bot not in (auth.settings.assistant_bot, STRATEGY_BOT)


def require_chat(c, auth, who, bot):
    """Write on the bot, and a bot that has a chat of its own (the Assistant has only the room
    /api/v2/assistant gives each person, and no person chats with the Strategy Owner)."""
    auth.require_write(c, who, bot)
    if not may_chat(c, auth, who, bot):
        if bot == STRATEGY_BOT:
            from . import strategy_bot

            raise Problem(
                "forbidden",
                "Nobody chats with the "
                + strategy_bot.name(c)
                + " directly; ask your "
                + auth.settings.assistant_name
                + " (/api/v2/assistant), which hands it the work",
                403,
            )
        raise Problem(
            "forbidden",
            auth.settings.assistant_name + " chats only in your own Assistant "
            "(/api/v2/assistant); message one of your bots here",
            403,
        )


def direct_chat(c, actor, bot):
    """The person's open direct chat with `bot`: kind chat, no task, just the two of them. None when there is none."""
    return next(
        (
            row
            for row in H.conversations_for(c, actor)
            if row["kind"] == "chat"
            and not row.get("task_id")
            and not row["closed_at"]
            and set(row["participants"]) == {actor, "bot:" + bot}
        ),
        None,
    )


def chat_running(c, conversation_id):
    """Whether a turn on this conversation is leased or running now."""
    return bool(
        c.execute(
            "SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            "WHERE m.conversation_id=? AND a.state IN ('leased','running') AND a.lease_until>?",
            (conversation_id, H.now()),
        ).fetchone()
    )


def clear_chat_session(c, settings, actor, bot, conversation_id, detail=None):
    """Clear a direct chat's session: a person's Clear (POST /api/employees/{bot}/session/clear) and a topic change
    (backend/topic_change.py) alike. The conversation's epoch moves, so its history starts after the clear; the next
    turn starts a fresh provider thread. The caller checks no turn is running."""
    c.execute(
        "INSERT INTO session_epochs VALUES(?,1,?) ON CONFLICT(conversation_id) DO UPDATE SET "
        "epoch=epoch+1,updated_at=excluded.updated_at",
        (conversation_id, H.now()),
    )
    # Drop the provider pointer so the next turn tries a fresh thread instead of resuming the cleared session.
    config = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    cfg = json.loads(config[0] or "{}") if config else {}
    if config:
        c.execute(
            "DELETE FROM bot_sessions WHERE bot=? AND runtime=? AND model=?",
            (bot, *providers.bot_choice(c, settings, cfg)),
        )
    # This chat's turns continue the bot's thread every direct chat shares: the next turn of any of them
    # starts a new one, while each other chat keeps its own history.
    from .shared_bots import follow
    from . import threads

    room = threads.thread_row(c, conversation_id) or {}
    key = sessions.session_key(
        follow(c, bot, cfg),
        bot,
        None,
        conversation_id,
        room.get("root_id") or conversation_id,
        "private" if threads.private_task_thread(c, room) else threads.audience_class(room),
    )
    if key in (sessions.BOT_THREAD, sessions.PUBLIC_THREAD):
        from .execution import bump_bot_session_epoch  # local: keeps execution out of the selector's fan-in

        bump_bot_session_epoch(c, bot)
    H.event(c, actor, "session.clear", conversation_id, detail)
    # A new session is said in the task's history: a task room's own, or the task of the run that failed (run_triggers).
    reason = (detail or {}).get("reason") or "person_clear"
    task_id = (detail or {}).get("task")
    if not task_id:
        room = c.execute("SELECT task_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        task_id = room[0] if room else None
    run_triggers.new_session(c, task_id, reason, actor)


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
    return (
        conv.get("scope") == rooms.PERSONAL
        and conv.get("owner_id") == who.actor
        and str(conv.get("room_key") or "").startswith("docs:")
        and "bot:" + DOC_BOT in conv["participants"]
    )


def bot_owner_rows(c, registry, slug, people, configs, archived):
    """The people listed as a bot's owners: the creator and co-owners, and its operator. Whoever it reports
    up to and the Admins own it too, without being listed."""
    ids = list(
        dict.fromkeys(
            [
                *A.owner_ids(registry["bot_owners"] if registry else None),
                *([registry["owner_id"]] if registry and registry["owner_id"] else []),
            ]
        )
    )
    return [{"id": i, "name": (P.person(i, people) or {}).get("name") or i} for i in ids]


# A bot's computer, as machine() reads it; prime_machines reads it for many bots at once.
ASSIGNED_COLUMNS = (
    "SELECT a.runner_id,a.generation,r.label,r.owner_id,r.last_seen_at,r.revoked_at,r.readiness,"
    "r.platform,r.version,r.awake_since,r.screen,r.capabilities"
)
ASSIGNED_SQL = ASSIGNED_COLUMNS + " FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?"


def prime_machines(c, slugs):
    """Read what machine() and H.status read for each of these bots one table at a time (one query per 500 bots
    each), so Health's pass over every bot reads none of them again per bot. Outside a snapshot nothing is kept."""
    from .agents import CONFIG_SQL, ROW_SQL
    from .auth import OPERATOR_SQL

    if read_cache.cache(c) is None:
        return
    for part in H.chunks(sorted(set(slugs))):
        marks = ",".join("?" * len(part))
        declared = {
            row["bot_id"]: row for row in c.execute(f"SELECT * FROM bot_settings WHERE bot_id IN ({marks})", part)
        }
        read_cache.prime(c, BS.ROW_SQL_ONE, {(s,): declared.get(s) for s in part})
        for statement, query in (
            (CONFIG_SQL, f"SELECT {BS.CONFIG} AS config,bot FROM bot_config bc {BS.JOIN} WHERE bot IN ({marks})"),
            (OPERATOR_SQL, f"SELECT bot,owner_id FROM bot_config WHERE bot IN ({marks})"),
            (ROW_SQL, f"SELECT * FROM agents WHERE bot IN ({marks})"),
            (
                ASSIGNED_SQL,
                ASSIGNED_COLUMNS + ",a.bot FROM assignments a "
                f"JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot IN ({marks})",
            ),
            ("SELECT * FROM bot_status WHERE bot=?", f"SELECT * FROM bot_status WHERE bot IN ({marks})"),
        ):
            found = {row["bot"]: row for row in c.execute(query, part)}
            read_cache.prime(c, statement, {(s,): found.get(s) for s in part})


def machine(c, bot):
    from .agents import presence
    from .execution import AWAKE_GAP, Execution  # local: keeps execution out of the selector's fan-in
    from .screen import listing as screen_listing

    external = presence(c, bot)
    if external:
        return external
    row = read_cache.one(c, ASSIGNED_SQL, (bot,))
    if not row:
        return {"online": False, "awake": False, "ready": False, "machine": None}
    now = H.now()
    online = bool(
        not row["revoked_at"] and row["last_seen_at"] and row["last_seen_at"] > H.shift(now, seconds=-AWAKE_GAP)
    )
    # Online is contact; awake is contact that has lasted. A machine waking for a few seconds
    # is online and takes no work, so say which of the two a queued request is waiting for.
    awake = online and Execution.awake(row["awake_since"], now)
    # The declared access has its own route, behind the bot's visibility (backend/bot_tools.py). A missing
    # subscription sign-in the Gateway covers meanwhile is no problem (backend/models_preset.py as_served).
    from .models_preset import as_served

    # One parse of the computer's report: /api/employees and Health call this once per bot.
    document = readiness_document(row["readiness"])
    detail = {k: v for k, v in bot_readiness(document, bot).items() if k != "tools"}
    detail = as_served(c, bot, document, _lapsed_plan(c, bot, row["runner_id"], document, detail))
    return {
        "online": online,
        "awake": awake,
        "ready": online and detail.get("ready") is True,
        "readiness": detail,
        "machine": {
            **{
                k: row[k]
                for k in ("runner_id", "generation", "label", "owner_id", "last_seen_at", "platform", "version")
            },
            **screen_listing(row),
        },
    }


def _lapsed_plan(c, bot, runner_id, document, detail):
    """The bot's readiness row, not ready when its computer read a plan's sign-in as ready and no plan signs it in
    now (execution.signed_in_by_lapsed_plan, which the claim refuses on): the cheap check first, so the plans are
    read only for a row that looks that way."""
    from .execution import signed_in_by_lapsed_plan

    if detail.get("ready") is not True or not signed_in_by_lapsed_plan(document, detail, {}):
        return detail
    from . import model_subscriptions, providers

    # Read once per snapshot: every bot on one computer asks for the same row (read_cache.one).
    runner = read_cache.one(c, "SELECT id, capabilities FROM runners_with_state WHERE id=?", (runner_id,))
    filled = providers.fill(providers.load(c), BS.read_in_snapshot(c, bot))
    if not runner or not signed_in_by_lapsed_plan(
        document, detail, model_subscriptions.covered_runtimes(c, dict(runner), filled)
    ):
        return detail
    from .models_preset import LOGIN_NAMES

    runtime = str(detail.get("runtime") or "")
    name = LOGIN_NAMES.get(runtime) or runtime.title()
    return {
        **detail,
        "ready": False,
        "sign_in": "missing",
        "problems": [*(detail.get("problems") or []), f"{name} sign-in needed"],
    }


def deploy_draining(c, bot):
    """When the deploy drained this bot (its latest drain was the deploy's, within the hour a
    deploy may hold it), else None. A drain a person set is theirs, not an update."""
    row = c.execute(
        "SELECT actor, created_at FROM events WHERE action IN ('bot.drain','bot.pause','bot.resume') "
        "AND target=? ORDER BY created_at DESC LIMIT 1",
        (bot,),
    ).fetchone()
    if not row or row["actor"] != "system:deploy" or row["created_at"] < H.shift(H.now(), seconds=-3600):
        return None
    return row["created_at"]


def updating(c):
    """Tico updating itself right now: the bots a deploy is holding, and since when."""
    held = [r[0] for r in c.execute("SELECT bot FROM bot_control WHERE draining=1")]
    stamps = [ts for ts in (deploy_draining(c, bot) for bot in held) if ts]
    return {"since": min(stamps), "bots": len(stamps)} if stamps else None


def operation_issues(c, who, auth):
    """Human-facing failures derived from durable state; no external alarm service.

    Every issue says whether a person has to act (`needs_person`); only those light the account's status dot.
    Stopped runs and quarantined bots are BotOps's to review, so they say no, and Settings > Health lists them.
    """
    from . import cloud_machines
    from .agents import still_reporting

    issues, seen = [], set()

    def add(
        kind,
        title,
        detail,
        *,
        bot=None,
        machine=None,
        severity="error",
        since=None,
        needs_person=True,
        action=None,
        **extra,
    ):
        key = (kind, bot, machine, detail)
        if key not in seen:
            seen.add(key)
            issues.append(
                {
                    "kind": kind,
                    "title": title,
                    "detail": detail,
                    "bot": bot,
                    "machine": machine,
                    "severity": severity,
                    "since": since,
                    "needs_person": needs_person,
                    "action": action,
                    **extra,
                }
            )

    offline = {}  # runner_id -> {label, since, bots: [display names], queued}
    readable = auth.bot_accesses(c, who)
    bots = H.bots(c)
    shown = [b["id"] for b in bots if readable.get(b["id"], auth.FULL)["read"]]
    status_inputs = privacy.status_inputs(c, shown)
    # Each bot's computer, status and queue read together, not a few queries per bot.
    prime_machines(c, shown)
    queues = bot_rows.job_queues(
        c, who, [b["id"] for b in bots if b["id"] in status_inputs and b["state"] != "archived"]
    )
    # Every stopped run of every bot that has one, read together: its checks below read each bot's runs from here.
    from .execution import prime_readable_held

    prime_readable_held(c, who, auth, [slug for slug, found in queues.items() if found["uncertain"]])
    for bot in bots:
        slug = bot["id"]
        if not readable.get(slug, auth.FULL)["read"]:
            continue
        if bot["state"] == "archived":
            # An archived bot whose agent still holds a working credential goes on reporting in to nobody; it fails
            # quietly on its own box, so Health says it.
            reporting = still_reporting(c, slug)
            if reporting:
                add(
                    "agent",
                    bot["display_name"]
                    + "'s "
                    + ("OpenClaw" if reporting["harness"] == "openclaw" else "Hermes")
                    + " agent is still reporting in, but the bot is archived",
                    "Restore it, or revoke its credential.",
                    bot=slug,
                    severity="warning",
                    needs_person=bool(auth.bot_manager(c, who, slug)),
                    action="Restore it, or revoke its credential, in Settings → Bots.",
                )
            continue
        location = machine(c, slug)
        mac_offline = bool(location.get("machine") and not location.get("online") and not location.get("agent"))
        status = privacy.status(c, who, H.status(c, slug), **status_inputs[slug]) or {}
        # A quarantine is repeated refusals (backend/hubdb.py rule 8); BotOps gets the refused-writes review for it.
        if bot["state"] == "quarantined" or status.get("state") == "quarantined":
            add(
                "bot",
                bot["display_name"] + " needs attention",
                status.get("focus") or "The bot is " + (status.get("state") or bot["state"]),
                bot=slug,
                needs_person=False,
                action="Review the refused action and interrupted work, then resume the bot on its page.",
            )
        elif status.get("state") == "crashed" and not mac_offline:
            add(
                "bot",
                bot["display_name"] + " needs attention",
                status.get("focus") or "The bot is crashed",
                bot=slug,
                needs_person=False,
            )
        elif status.get("state") == "limited":
            # A usage limit retries on its own; only a fresh one is worth a glance, an
            # old row is a leftover nobody cleared. Three in a row is the subscription
            # window spent; Settings still shows the retry time without interrupting chat.
            # An API key's quota or billing refusal is not a subscription window; the person checks billing.
            from .execution import (
                LIMIT_COOLDOWN_LONG,
                LIMIT_STRIKES,
                billing_limit,
                last_limit,
                limit_cooldown,
                limit_streak,
                provider_reset,
            )

            since = H.parse_ts(status.get("changed_at"))
            cooldown = limit_cooldown(c, slug)
            if since and since > H.parse_ts(H.shift(H.now(), seconds=-max(3600, cooldown))):
                from .agents import CONFIG_SQL

                config = read_cache.one(c, CONFIG_SQL, (slug,))  # prime_machines read it with every bot's
                runtime = (
                    providers.bot_choice(c, auth.settings, H._json(config["config"], {}) or {} if config else {})[0]
                    or "your"
                )
                # The login the limit held (backend/login_holds.py) says whose limit it was: a fallback's quota text
                # is not the bot's own provider's billing.
                from . import login_holds
                from .execution import BILLING_LIMIT_RE

                last = last_limit(c, slug)
                hold = login_holds.for_attempt(c, last["id"]) if last else {}
                billing = bool(BILLING_LIMIT_RE.search(hold.get("error") or "")) if hold else billing_limit(c, slug)
                own = runtime
                if hold:
                    runtime = hold["runtime"] + (f" (the {hold['profile']} login)" if hold.get("profile") else "")
                if last:
                    # Named from what the turn kept when it finished, not the bot's runtime or the login's hold now:
                    # a fallback's quota must not read as the primary's, nor a past limit as a newly chosen provider's.
                    result = json.loads(
                        (c.execute("SELECT result FROM attempts WHERE id=?", (last["id"],)).fetchone() or ["{}"])[0]
                        or "{}"
                    )
                    src = login_holds.recorded(c, last["id"], result, own)
                    runtime, billing = login_holds.label(src), src["billing"]
                streak = hold.get("streak", 0) if hold else limit_streak(c, slug)
                reset = (
                    hold.get("reset") if hold else bool(last and provider_reset(last, H.parse_ts(last["finished_at"])))
                )
                resume = login_holds.fallback_resume_reason(c, last['id']) if last else None
                if resume:
                    add(
                        "bot",
                        bot["display_name"] + " needs attention",
                        f"{runtime}; {resume}",
                        bot=slug,
                        severity="warning",
                        needs_person=False,
                    )
                elif billing:
                    retry = H.shift(status["changed_at"], seconds=cooldown)
                    add(
                        "bot",
                        bot["display_name"] + " needs attention",
                        f"{runtime}; queued work waits until {retry[:10]} {retry[11:16]} UTC, "
                        "check the API key's plan and billing",
                        bot=slug,
                        needs_person=False,
                        **({} if streak >= LIMIT_STRIKES else {"severity": "warning"}),
                        action="Check the API key's plan and billing, or set a fallback harness in Settings → Bots.",
                    )
                elif cooldown > LIMIT_COOLDOWN_LONG:
                    retry = H.shift(status["changed_at"], seconds=cooldown)
                    add(
                        "bot",
                        bot["display_name"] + " needs attention",
                        f"{runtime}: subscription quota exhausted; queued work waits until {retry[:10]} "
                        f"{retry[11:16]} UTC",
                        bot=slug,
                        severity="warning",
                        needs_person=False,
                        action="Wait for the subscription reset, or set a fallback harness in Settings → Bots.",
                    )
                # A reported reset says when the limit lifts, so a streak is no reason to check the subscription.
                elif streak >= LIMIT_STRIKES and not reset:
                    retry = H.shift(status["changed_at"], seconds=cooldown)
                    add(
                        "bot",
                        bot["display_name"] + " needs attention",
                        f"{runtime}, three times in a row; queued work waits until {retry[11:16]} UTC, "
                        "check the subscription or the API-key fallback on its Mac",
                        bot=slug,
                        needs_person=False,
                        action="Check the subscription or set a fallback harness in Settings → Bots.",
                    )
                else:
                    retry = H.shift(status["changed_at"], seconds=cooldown)
                    add(
                        "bot",
                        bot["display_name"] + " needs attention",
                        f"{runtime}; resumes at {retry[11:16]} UTC",
                        bot=slug,
                        severity="warning",
                        needs_person=False,
                    )
        jobs = queues[slug]
        queued = jobs["queued"]
        if location.get("agent"):
            # A synced agent is handed its messages at a sync, which marks them delivered; it never reads them in Tico.
            # Batch the privacy check (readable_messages primes once); a per-row message_readable re-fetched each
            # message and scaled with the agent's unread inbox on every /status and /operations poll.
            unread = "delivered_at IS NULL" if location["agent"].get("synced") else "read_at IS NULL"
            waiting = [
                {"id": row["id"]}
                for row in c.execute(
                    "SELECT id FROM messages WHERE recipient_id=? AND " + unread + " "
                    "AND (expires_at IS NULL OR expires_at > ?) AND deleted_at IS NULL",
                    ("bot:" + slug, H.now()),
                )
            ]
            queued = len(privacy.readable_messages(c, privacy.actor(who), waiting))
        uncertain = jobs["uncertain"]
        if uncertain:
            from .execution import tico_chosen

            uncertain -= tico_chosen(c, who, slug)
        if uncertain and not mac_offline:
            noun = "run" if uncertain == 1 else "runs"
            queued_note = (
                f" {queued} other request{' is' if queued == 1 else 's are'} still queued."
                if queued
                else " Other work can continue."
            )
            # A stopped run is held so an automatic retry cannot repeat an outside action. It is
            # useful in Settings, but age alone never turns it into an interruption for a person.
            from .execution import held_back, held_runs, isolation_refused, left_by_keeper, stopped_rows, unreviewed

            runs = held_runs(c, who, auth, slug)
            named = "; ".join(f"\"{h['title']}\" (owner {h['owner_name']})" for h in runs)
            held_note = f" Held: {named}." if named else ""
            waiting, waiting_on = held_back(c, who, auth, slug)
            left = left_by_keeper(c, who, slug)
            if isolation_refused(c, who, auth, slug):
                # Not a possible outside action: the bot's model can't run on its computer, and its later work waits
                # behind the held run until a person acts.
                add(
                    "uncertain_work",
                    f"{bot['display_name']} can't run on its computer",
                    gateway.run_status(gateway.NOT_ISOLATED) + f".{held_note} Its other work waits behind this run.",
                    bot=slug,
                    action="Pick another model in Settings → Bots, or move the bot to a computer with "
                    "Tico's Docker runner, then review its stopped run.",
                )
            elif waiting or left:
                # Work is stuck behind it (the claim's own fence), or the keeper settled the rest of its run and left
                # it: only a review lets it go. BotOps holds that review (its held-run task), so the person's status
                # dot stays out of it and Health lists it. One short line per run, with its own Try again / Don't
                # retry: the detail says only why they stopped and what waits; the rows name them.
                rows, run_count = stopped_rows(c, who, auth, slug)
                count = run_count or uncertain
                can_review = who.actor == "bot:botops" or auth.operator(c, who, slug)
                if not can_review:
                    # Only the operator or BotOps may decide (reconcile_job): nobody else is handed the handles.
                    rows = [{k: v for k, v in row.items() if k != "jobs"} for row in rows]
                noun = "run" if count == 1 else "runs"
                behind = (
                    f"{waiting} queued request{' waits' if waiting == 1 else 's wait'} until you decide."
                    if waiting
                    else "Tico can't safely decide it on its own."
                )
                causes = {row["cause"] for row in rows}
                why = ""
                # Only when every run is listed: a run past the first few may have stopped for another reason.
                if rows and len(rows) == count and len(causes) == 1 and rows[0]["routine"]:
                    cause, these = causes.pop(), "this run" if count == 1 else "these runs"
                    stamps = sorted(H.parse_ts(row["at"]) for row in rows if row["at"])
                    # One restart names its time; stops spread over a while (a busy evening) do not share one.
                    at = (
                        stamps[0].strftime("%H:%M")
                        if stamps and len(stamps) == len(rows) and (stamps[-1] - stamps[0]).total_seconds() <= 600
                        else None
                    )
                    when = f" at {at} UTC" if at else ""
                    why = {
                        "update": f"{these.capitalize()} stopped during Tico's update{when}",
                        "interrupted": f"Its computer interrupted {these}{when}, usually for a restart",
                        "quota": f"{these.capitalize()} stopped at a usage limit",
                        "capacity": f"{these.capitalize()} stopped because the AI service was busy",
                        "timeout": f"Its computer stopped answering during {these}",
                    }[cause]
                    why += ", not a bug. "
                add(
                    "uncertain_work",
                    f"{bot['display_name']} has {count} stopped {noun} holding up work",
                    why + behind,
                    bot=slug,
                    needs_person=False,
                    action="review",
                    held_back=waiting,
                    held_tasks=waiting_on,
                    runs=rows,
                    more_runs=max(0, count - len(rows)),
                    can_review=can_review,
                )
            elif (late := unreviewed(c, who, slug))[0]:
                # Two hours with no decision: Health lists it for whoever may read the bot, and BotOps's held-run review
                # decides it. Nobody is interrupted for it, so only the operator's severity differs.
                count, hours = late
                them = "it" if count == 1 else "them"
                newer = uncertain - count
                more = f" {newer} more stopped since." if newer > 0 else ""
                operator = auth.operator(c, who, slug)
                # The same rows as the card above, so Try again / Don't retry act on each run here too.
                rows, run_count = stopped_rows(c, who, auth, slug)
                can_review = who.actor == "bot:botops" or operator
                if not can_review:
                    rows = [{k: v for k, v in row.items() if k != "jobs"} for row in rows]
                add(
                    "uncertain_work",
                    f"{bot['display_name']} has {count} stopped run{'' if count == 1 else 's'} waiting {hours} h",
                    f"Nobody has decided {them} yet.{more}{queued_note} Choose Try again or Don't retry.",
                    bot=slug,
                    severity="error" if operator else "warning",
                    needs_person=False,
                    action="review",
                    waiting_hours=hours,
                    runs=rows,
                    more_runs=max(0, run_count - len(rows)),
                    can_review=can_review,
                )
            else:
                add(
                    "uncertain_work",
                    f"{bot['display_name']} saved {uncertain} stopped {noun} for later",
                    "The bot stopped before returning an answer. Tico paused only that request so it "
                    f"will not repeat a possible outside action.{held_note}{queued_note} Review it whenever useful.",
                    bot=slug,
                    severity="warning",
                    needs_person=False,
                    action="review",
                )
        agent = location.get("agent")
        if agent and bot["state"] == "active" and not agent["credential"]:
            add(
                "agent",
                bot["display_name"] + " has no agent credential",
                "Create one in Settings → Bots and install it in the " + agent["harness"] + " profile.",
                bot=slug,
                needs_person=False,
            )
        elif agent and bot["state"] == "active" and not location["online"] and agent.get("synced"):
            detail = (
                "Last sync " + agent["last_seen_at"] + "." if agent["last_seen_at"] else "No history has synced yet."
            )
            if queued:
                detail += (
                    f" {queued} message{'s' if queued != 1 else ''} wait{'s' if queued == 1 else ''} for the next sync."
                )
            add(
                "agent",
                bot["display_name"] + "'s history has not synced lately",
                detail,
                bot=slug,
                since=agent["last_seen_at"],
                severity="warning",
                needs_person=False,
                action="Run the Tico sync in " + (agent.get("label") or "the agent") + " to refresh this copy.",
            )
        elif agent and bot["state"] == "active" and not location["online"]:
            detail = (
                "Last heartbeat " + agent["last_seen_at"] + "."
                if agent["last_seen_at"]
                else "No heartbeat yet: install the credential and the heartbeat timer on its box."
            )
            if queued:
                detail += f" {queued} unread message{'s' if queued != 1 else ''} waiting in its inbox."
            add(
                "agent",
                bot["display_name"] + "'s " + agent["harness"] + " agent has not reported in",
                detail,
                bot=slug,
                since=agent["last_seen_at"],
                needs_person=False,
            )
        elif agent:
            pass  # an external agent that reported in lately: no computer to check
        elif bot["state"] == "active" and not location["machine"] and (cloud := cloud_machines.coming(c, slug)):
            # A Tico Cloud bot goes on its cloud machine's computer once it enrolls: nothing to assign. Only a
            # machine that failed needs a person; out of credit is the company's strip.
            failed = cloud["status"] == "failed"
            add(
                "machine",
                f"{bot['display_name']} is waiting for {cloud['label']} to start",
                cloud["words"] + (f". {queued} saved request{'s' if queued != 1 else ''} waiting." if queued else "."),
                bot=slug,
                needs_person=failed,
                severity="error" if failed else "warning",
                action="Replace it in Settings → Computers." if failed else None,
            )
        elif bot["state"] == "active" and not location["machine"]:
            # Nothing waiting means nothing urgent: a bot nobody has placed yet is setup, not a fault.
            add(
                "machine",
                bot["display_name"] + " has no computer",
                f"{queued} saved request{'s are' if queued != 1 else ' is'} waiting."
                if queued
                else "Assign a registered computer before new work arrives.",
                bot=slug,
                needs_person=False,
                severity="error" if queued else "warning",
                action="Assign a registered computer in Settings → Bots." if queued else None,
            )
        elif bot["state"] == "active" and location["machine"] and not location["online"]:
            # One issue per machine, not one per bot: the person can only do one thing about it.
            entry = offline.setdefault(
                location["machine"]["runner_id"],
                {
                    "label": location["machine"]["label"],
                    "since": location["machine"]["last_seen_at"],
                    "owner_id": location["machine"]["owner_id"],
                    "bots": [],
                    "slugs": [],
                    "queued": 0,
                },
            )
            entry["bots"].append(bot["display_name"])
            entry["slugs"].append(slug)
            entry["queued"] += queued
        elif bot["state"] == "active" and location["online"] and not location["ready"]:
            problems = location.get("readiness", {}).get("problems") or ["Local setup is incomplete."]
            add(
                "readiness",
                bot["display_name"] + " is not ready",
                problems[0],
                bot=slug,
                machine=location["machine"]["runner_id"],
                needs_person=False,
            )
    from .health import NEEDS_YOU_MINUTES, quiet_minutes, silent_computers

    # Whether a computer has stopped is the shared rule over all its queued work, not only what this viewer may read.
    silent = {x["id"] for x in silent_computers(c)} if offline else set()
    for runner_id, entry in offline.items():
        names = entry["bots"]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        cannot = "cannot run until it reconnects"
        waiting = (
            f" {entry['queued']} saved request{'s are' if entry['queued'] != 1 else ' is'} waiting."
            if entry["queued"]
            else ""
        )
        quiet = quiet_minutes(entry["since"])
        if runner_id in silent:
            # Work piling up behind a computer that stopped checking in is an outage, not a blip. Its owner is the one
            # who can bring it back (the company owner when no current person enrolled it), so after a while it is
            # theirs to handle; the title stays the same as the minutes pass, so it opens for them once.
            operator = entry["owner_id"] or ""
            person = P.person(operator, roster(c)) if operator else None
            mine = who.actor == "human:" + operator or (
                who.role == "owner" and (not person or bool(person.get("hidden")))
            )
            add(
                "machine",
                entry["label"] + " stopped checking in",
                f"No word from it for {quiet} minutes. {listed} {cannot}.{waiting}",
                machine=runner_id,
                since=entry["since"],
                needs_person=mine and quiet >= NEEDS_YOU_MINUTES,
                action=f"Wake {entry['label']} and check that {auth.settings.app_name} runs there: "
                "`tico-computer status` says what stopped, `tico-computer restart` starts it again.",
            )
        else:
            # A computer offline for a moment (asleep, a restart) clears itself when it reconnects.
            add(
                "machine",
                entry["label"] + " is offline",
                f"{listed} {cannot}.{waiting}",
                machine=runner_id,
                since=entry["since"],
                severity="warning",
                needs_person=False,
            )
        issues[-1]["bots"] = entry["slugs"]  # the bot pages find their machine's issue by this
    if who.role == "owner":
        # Container backups report through replication; imported VM timer history stays historical.
        managed_backups = bool(os.environ.get("TICO_BACKUP_MODE"))
        runners = c.execute(
            "SELECT id,label,owner_id,last_seen_at,revoked_at,version,checkout,restart_requested_at "
            "FROM runners_with_state WHERE revoked_at IS NULL"
        ).fetchall()
        busy = runner_busy_many(c, [row["id"] for row in runners])  # every computer's running turns in one read
        for row in runners:
            online = row["last_seen_at"] and row["last_seen_at"] > H.shift(H.now(), seconds=-60)
            if online and not runner_version_supported(row["version"]):
                add(
                    "runner_version",
                    row["label"] + " needs a runner update",
                    "Install the current " + auth.settings.app_name + " runner before assigning new work.",
                    machine=row["id"],
                    action="Update this runner in Settings → Computers.",
                )
            if online:
                checkout_issue(add, row, busy[row["id"]])
        for row in c.execute("SELECT service,last_error,detail FROM service_health WHERE last_error IS NOT NULL"):
            if managed_backups and row["service"] in SNAPSHOT_BACKUP_ROWS:
                continue
            detail = json.loads(row["detail"] or "{}")
            if row["service"] == "github:token":
                # Only the App itself failing is a system problem, and only while it is recent and unrecovered.
                from . import health

                failure = health.token_failure(c)
                if failure:
                    add(
                        "service",
                        "GitHub needs attention",
                        failure["message"] or "GitHub would not issue a token.",
                        needs_person=False,
                        action=failure["action"],
                    )
                continue
            add(
                "service",
                row["service"] + " needs attention",
                row["last_error"] or "The last check failed.",
                needs_person=bool(detail.get("needs_person")),
                action=detail.get("action"),
            )
        # Google connections: the Workspace key that stopped working, and a person's grant held on a computer other
        # than their message bot's (backend/google_connect.py, google_workspace.py).
        from . import google_rights, google_workspace

        key = google_workspace.active(c)
        if key and key["status"] == "failing":
            add(
                "google_workspace",
                "The Google Workspace key is failing",
                key["last_error"] or "Google has refused the Workspace key for too long.",
                needs_person=True,
                action="Check the key in Tools > Settings > Email.",
            )
        sync_owners = google_workspace.workspace_sync_owners(c)
        for box in google_workspace.workspace_mailboxes(c):
            if box["address"] not in sync_owners:
                add(
                    "google_workspace",
                    box["address"] + " has no sync computer",
                    "Mail and calendar for "
                    + box["address"]
                    + " are not syncing because no eligible computer is online.",
                    needs_person=False,
                    severity="warning",
                    action="Bring an isolated company server online."
                    if box["kind"] == "shared"
                    else "Bring the person's computer, their Assistant's host or an isolated company server online.",
                )
        labels = {row["id"]: row["label"] for row in c.execute("SELECT id,label FROM runners_with_state")}
        hosts = google_rights.inbox_hosts(c)
        for grant in c.execute(
            "SELECT person_id,mailbox,holder FROM google_connects WHERE status='connected' "
            "AND holder IS NOT NULL AND grant_hash IS NOT NULL"
        ).fetchall():
            host = (hosts.get(grant["person_id"]) or {}).get("runner")
            if host and host != grant["holder"]:
                holder = labels.get(grant["holder"]) or "another computer"
                bot_computer = labels.get(host) or "the computer that runs their message bot"
                add(
                    "google_grant",
                    grant["mailbox"] + "'s Google grant is on " + holder,
                    grant["mailbox"]
                    + "'s Google grant is on "
                    + holder
                    + ". Reconnect in Tools > Settings > Email to move it "
                    "to " + bot_computer + ", where their message bot runs.",
                    machine=grant["holder"],
                    needs_person=True,
                    action="Open Tools > Settings > Email",
                )
        # A connector that stopped refreshing (the Mac lost the network, or the sync hung) writes no
        # error, so its age is checked while a Mac is online: calendar refreshes every 45 s, mail
        # reports every 10 minutes.
        if c.execute(
            "SELECT 1 FROM runners_with_state WHERE revoked_at IS NULL AND last_seen_at>?",
            (H.shift(H.now(), seconds=-60),),
        ).fetchone():
            for service, label, limit in (("connector:calendar", "Calendar", 15), ("connector:mail", "Mail", 90)):
                row = c.execute(
                    "SELECT last_succeeded_at,last_error FROM service_health WHERE service=?", (service,)
                ).fetchone()
                last = H.parse_ts(row["last_succeeded_at"]) if row and row["last_succeeded_at"] else None
                if last and not row["last_error"] and last < H.parse_ts(H.shift(H.now(), minutes=-limit)):
                    minutes = int((H.parse_ts(H.now()) - last).total_seconds() // 60)
                    add(
                        "service",
                        SERVICE_NAMES[service] + " needs attention",
                        f"{label} last refreshed {minutes} minutes ago although a Mac is online; check its Tool log.",
                        needs_person=False,
                    )
        # A backup that stops running never writes an error, so age is checked here with
        # the same limits as the CloudWatch alarms; the hub is the notification channel.
        # Both run daily after 03:00 UTC and the restore check retries hourly, so 36 hours
        # means the retries are failing too.
        if managed_backups:
            from . import health, replication

            check = health._backups({"backup": replication.status()}, auth.settings)
            if check["status"] in ("warn", "bad"):
                add(
                    "service",
                    "Backups need attention",
                    check["summary"],
                    severity="error" if check["status"] == "bad" else "warning",
                    action="Review Backups in Settings → Health.",
                )
        for service, label, limit in (
            ()
            if managed_backups
            else (("backup", "verified backup", 36), ("restore-check", "isolated restore check", 36))
        ):
            row = c.execute("SELECT last_succeeded_at FROM service_health WHERE service=?", (service,)).fetchone()
            last = H.parse_ts(row["last_succeeded_at"]) if row and row["last_succeeded_at"] else None
            if last and last < H.parse_ts(H.shift(H.now(), hours=-limit)):
                hours = int((H.parse_ts(H.now()) - last).total_seconds() // 3600)
                add(
                    "service",
                    service + " needs attention",
                    "The last "
                    + label
                    + " succeeded "
                    + str(hours)
                    + " hours ago; check backup.log and the timers on the server.",
                    action="Check backup.log and the server timers.",
                )
        for row in c.execute("SELECT id,bot,error FROM bot_transitions WHERE state='failed'"):
            # A version conflict means settings already moved; the failed checkpoint is leftover.
            if "while the checkpoint was being prepared" in (row["error"] or ""):
                continue
            add(
                "settings",
                "A prepared settings change failed",
                row["error"] or "Review or cancel the change.",
                bot=row["bot"],
                action="Review or cancel the failed change in Settings → Bots.",
            )
    return issues


def own_sign_in(c, bot, settings=None):
    """Why this bot's model is not signed in on its own computer (backend/readiness.py, the rule Health's stuck
    check and the model picker use), or '' when its computer is signed in or it waits on something else."""
    from .readiness import can_run, own_harness

    config = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    runtime = providers.bot_choice(c, settings, json.loads(config[0] or "{}") if config else {})[0]
    verdict = can_run(c, bot, runtime, harness=own_harness(c, bot, runtime)) if runtime else {}
    return (verdict.get("problem") or "its model is not signed in") if verdict.get("sign_in") else ""


def free_turn(c, bot, location, human=False):
    """The computer where no turn can start now, or '': its start places are all taken by running and starting turns
    (start_places.held), or it already holds as many claimed turns as it may lease (start_places.live_limit)."""
    machine = location.get("machine") or {}
    row = c.execute("SELECT * FROM runners_with_state WHERE id=?", (machine.get("runner_id"),)).fetchone()
    if not row:
        return ""
    from .start_places import held, live, live_limit, turn_limit

    rid = machine["runner_id"]
    full = sum(held(c, rid).values()) >= turn_limit(row) or sum(live(c, rid).values()) >= live_limit(row)
    return (machine.get("label") or "its computer") if full else ""


def cloud_machine_wait(c, bot):
    from .cloud_machines import waiting

    return waiting(c, bot)


def queued_label(c, bot, location, settings=None, human=False, behind=False, queued=False):
    """(label, readiness_reason) for a bot's queued work: what it is waiting for, in a person's words. `human`: a
    person's message; `behind`: the bot is still answering earlier work in this same conversation; `queued`: it was
    sent with queue, so it waits for that turn to end rather than steering into it (backend/delivery.py)."""
    from .start_places import computer_hold
    from .cloud_machines import resting
    from . import org_turns

    asleep = (location.get("machine") or {}).get("runner_id") in resting(c)
    bot_state = H.bot(c, bot)["state"]
    label, readiness_reason = "Saved — queued", ""
    draining = c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot,)).fetchone()
    from . import company_pause

    company = company_pause.record(c)
    # A drain is either Tico updating itself (the deploy finishes each
    # bot's current run, then holds new ones until it is back) or a person pausing it.
    if company is not None:
        label = "Saved — waiting: " + company_pause.waiting_words(company)
    elif draining and deploy_draining(c, bot):
        label = "Saved — Tico is updating; this starts when the update finishes"
    elif draining:
        label = "Saved — this bot is paused; this starts when it's resumed"
    elif bot_state != "active":
        label = "Saved — bot " + bot_state
    elif stop := bot_stops.queued_reason(c, bot, human):
        # Tico stopped the bot (failed runs, run budget) or it waits out a failed run: the words say why.
        label = "Saved — " + stop
    elif (not location["online"] or asleep) and (cloud := cloud_machine_wait(c, bot)):
        # Its own cloud machine is stopped or starting (backend/cloud_machines.py): the words say which, or why not.
        label = "Saved — " + cloud
    elif not location["online"]:
        label = "Saved — waiting for " + ((location["machine"] or {}).get("label") or "a registered computer")
    elif not location["awake"]:
        label = (
            "Saved — waiting for "
            + ((location["machine"] or {}).get("label") or "a registered computer")
            + " to stay awake"
        )
    elif settings and org_turns.admission(c)['at_stop']:
        # Admission only: queued labels must not classify every bot's readiness and spend on each chat/status poll.
        label = 'Saved — ' + org_turns.REASON
    elif (location.get("machine") or {}).get("runner_id") and (
        hold := computer_hold(c, location["machine"]["runner_id"], person=human)
    ):
        # On Linux, a person's priority cannot make memory available for another process.
        label = "Saved — waiting: " + hold[1]
    elif not location["ready"]:
        problems = (location.get("readiness") or {}).get("problems") or []
        if any(str(p).startswith("No AI provider is chosen") for p in problems):
            readiness_reason = "missing_provider"
            label = "Saved — no AI provider is chosen"
        elif verdict := own_sign_in(c, bot, settings):
            # Only this bot's own model, on its own computer: a bot that waits on anything else keeps its own reason.
            readiness_reason = "model_sign_in"
            label = "Saved — " + verdict
        else:
            label = "Saved — " + str(problems[0]) if problems else "Saved — waiting for computer setup"
    elif behind:
        # Whether the running turn takes it (`steer`) or it waits for the next one; chat offers Stop and redirect.
        from .harnesses import steers

        config = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        config = json.loads(config[0]) if config else {}
        label = (
            "Saved — steering this turn"
            if not queued and steers(config, config.get("runtime"))
            else "Saved — queued for the next turn"
        )
    elif runner_versions.holding_since(c, (location.get("machine") or {}).get("runner_id")):
        # Its computer takes no new work until its running turns end and it updates (docs/updates.md).
        progress = runner_versions.drain_progress(c, (location.get("machine") or {}).get("runner_id"))
        label = "Saved — waiting for the computer to update" + (f": {progress}" if progress else "")
    elif start_places.at_turn_limit(c, bot):
        label = "Saved — at its turn limit"
    elif computer := free_turn(c, bot, location, human):
        label = "Saved — waiting for a free turn on " + computer
    return label, readiness_reason


# The queued job of a task. INDEXED BY jobs_queued: the expression on the message cannot use an index, and with the
# stale sqlite_stat1 row for messages the planner scanned all of them (40-44 ms a call on TIDY v0.5.1, 545 an hour).
TASK_WAIT_SQL = (
    f"SELECT j.bot_id,{H.in_person_name_sql()} AS person_sent FROM jobs j INDEXED BY jobs_queued "
    "JOIN messages m ON m.id=j.message_id "
    "JOIN conversations cv ON cv.id=m.conversation_id "
    f"WHERE j.state='queued' AND {H.MESSAGE_TASK_SQL}=? ORDER BY j.created_at LIMIT 1"
)


def task_wait(c, task_id, settings=None):
    """What this task's queued run waits for ("Waiting for a free turn on Studio Mac"), or '' when none is queued."""
    job = c.execute(TASK_WAIT_SQL, (task_id,)).fetchone()
    if not job:
        return ""
    label = queued_label(c, job["bot_id"], machine(c, job["bot_id"]), settings, human=bool(job["person_sent"]))[0]
    reason = label.removeprefix("Saved — ")
    return "Queued" if reason == "queued" else reason[:1].upper() + reason[1:]


# A run's live reply in a snapshot: its newest pieces, each cut to a length. A long run has hundreds of steps and the
# snapshot is read again as it works; the finished reply arrives as a message.
LIVE_PARTS_MAX = 40
LIVE_PART_CHARS = 8000


def page_options(limit=None, max_bytes=None):
    """A conversation page's `limit` (1 to 200, default 200) and `max_bytes` (at least 16 KB, at most 1 MB, default
    none) from a request: the web app opens a chat at 50 messages and 160 KB; other callers keep the old page."""
    out = {"limit": max(1, min(int(limit), 200)) if limit else 200}
    if max_bytes:
        out["max_bytes"] = max(16_384, min(int(max_bytes), 1 << 20))
    return out


def live_parts(parts):
    """The newest LIVE_PARTS_MAX pieces of a run's reply, each at most LIVE_PART_CHARS (its end kept: the newest text),
    and how many earlier pieces were left out."""
    kept = parts[-LIVE_PARTS_MAX:]
    cut = [p if len(p["text"]) <= LIVE_PART_CHARS else {**p, "text": "…" + p["text"][-LIVE_PART_CHARS:]} for p in kept]
    return cut, len(parts) - len(kept)


def conversation_snapshot(c, cid, who=None, settings=None, page_args=None, auth=None):
    page = privacy.page(c, who, cid, **(page_args or {})) if who else message_page(c, cid)
    from .chat_goals import current

    return {**page, "execution": conversation_execution(c, cid, who, settings, auth=auth), "goal": current(c, cid)}


def conversation_execution(c, cid, who=None, settings=None, bot=None, auth=None, people=False):
    """The newest run in this conversation as the chat shows it while it works: its state, a label and its reply so
    far, or None. `bot` keeps to that bot's runs (a task's page follows its owner's session); `people` also takes the
    run for a message a person sent, whichever bot it went to (a person's task answered by the bot that asked it).
    A run waiting for memory or its ChatGPT login (backend/login_waits.py) says so in `waiting` and its label; `auth` lets it
    name the bot holding it."""
    person = H.in_person_name_sql()
    which = (
        f"AND (j.bot_id=? OR {person}) "
        if bot and people
        else "AND j.bot_id=? "
        if bot
        else f"AND {person} "
        if people
        else ""
    )
    # A send the duplicate trigger dismissed (wake_presets.HOLD_TRIGGER) repeats a run still going: the chat shows that run.
    # person_sent: the message is a person's, or the keeper carrier of one (the sender alone is the keeper).
    job = c.execute(
        f"SELECT j.*,m.sender_id AS sender,{person} AS person_sent,a.state AS attempt_state,a.lease_until,"
        "a.stop_requested_at,a.started_at AS attempt_started_at,p.wait_reason AS start_wait_reason "
        "FROM jobs j JOIN messages m ON m.id=j.message_id "
        "LEFT JOIN attempts a ON a.id=j.attempt_id LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE m.conversation_id=? "
        "AND coalesce(json_extract(m.refs,'$.maintenance'),'')!='checkpoint' "
        "AND NOT (j.state='canceled' AND coalesce(j.wake_class,'')='duplicate') "
        + which
        + "ORDER BY m.rowid DESC LIMIT 1",
        (cid, bot) if bot else (cid,),
    ).fetchone()
    execution = None
    if (
        job
        and who
        and (
            not privacy.message_readable(c, privacy.actor(who), H.message(c, job["message_id"]))
            or job["attempt_id"]
            and not privacy.attempt_readable(c, privacy.actor(who), job["attempt_id"])
        )
    ):
        job = None
    if job:
        state = job["state"]
        input_added = state == "input"
        if state in ("running", "leased", "input") and job["lease_until"] <= H.now():
            state = "uncertain" if state in ("running", "input") else "queued"
        elif state == "input":
            state = "running"
        location = machine(c, job["bot_id"])
        label = {
            "queued": "Saved — queued",
            "leased": "Starting",
            "running": "Working",
            "completed": "Complete",
            "canceled": "Delivery dismissed",
            "uncertain": "Stopped — saved for later",
        }.get(state, state)
        if state == "canceled" and job["stop_requested_at"]:
            label = "Stopped"
        if input_added and state == "running":
            label = "Working — follow-up added"
        if state == "uncertain" and job["attempt_state"] == "failed":
            label = "Reply failed — your message is saved"
        waiting = None
        if state in ("leased", "running") and job["attempt_id"]:
            # Before it starts, or after: a fallback hop takes its own login once the turn has started.
            from .login_waits import waiting as login_wait

            waiting = login_wait(c, job["attempt_id"], job["bot_id"], who, auth)
            if state == "leased" and (job["start_wait_reason"] or "").startswith(start_places.MEMORY_HOLD):
                waiting = "Waiting: " + job["start_wait_reason"]
            label = waiting or label
        readiness_reason = ""
        if state == "queued":
            message = H.message(c, job["message_id"]) or {}
            behind = c.execute(
                "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN attempts a ON a.id=j.attempt_id "
                "WHERE m.conversation_id=? AND j.bot_id=? AND a.state IN ('leased','running') LIMIT 1",
                (cid, job["bot_id"]),
            ).fetchone()
            label, readiness_reason = queued_label(
                c,
                job["bot_id"],
                location,
                settings,
                human=bool(job["person_sent"]),
                behind=bool(behind),
                queued=(message.get("refs") or {}).get("delivery") == "queue",
            )
            if (H.status(c, job['bot_id']) or {}).get('state') == 'limited' and (
                reason := login_holds.fallback_resume_reason(c, job['attempt_id'])
            ):
                label = 'Saved — ' + reason
        parts = []
        if state in ("leased", "running"):
            parts = turns.reply_parts(
                [
                    (e["kind"], json.loads(e["payload"]), e["created_at"])
                    for e in c.execute(
                        "SELECT kind,payload,created_at FROM attempt_events WHERE attempt_id=? ORDER BY seq",
                        (job["attempt_id"],),
                    )
                ]
            )
        if parts:
            # A bot that org rules apply to shows that it works and its tool steps, never its reply before the reply
            # is checked and delivered (backend/org_rule_check.py).
            from .org_rule_check import watched

            if watched(c, job["bot_id"]):
                parts = [p for p in parts if p["kind"] == "tool"]
        parts, omitted = live_parts(parts)
        execution = {
            "job_id": job["id"],
            "message_id": job["message_id"],
            "bot_id": job["bot_id"],
            "attempt_id": job["attempt_id"],
            "state": state,
            "label": label,
            "text": "\n\n".join(p["text"] for p in parts if p["kind"] != "tool"),
            "parts": parts,
            "from_person": bool(job["person_sent"]),
            **location,
        }
        if omitted:
            execution["parts_omitted"] = omitted
        if waiting:
            execution["waiting"] = waiting
        if readiness_reason:
            execution["readiness_reason"] = readiness_reason
        if state in ("leased", "running") and job["attempt_started_at"]:
            execution["started_at"] = job["attempt_started_at"]  # the chat counts the turn's time from it
    return execution


# What needs a person, in the order they work it. Work that traces back to a person leads: a person asked for it,
# or it hangs under a task a person asked for. Work bots made for themselves or each other follows, unless it is
# pressing: due within a day or overdue, or waiting PRESSING_DAYS (DECISIVE_DAYS for an approval or a question,
# which holds its bot up). Within a tier the decisive kinds lead, then the queue in rank order, oldest first.
PRESSING_DAYS = 3
DECISIVE_DAYS = 1
# A bot's task set waiting on the person blocks that bot just as its question does.
KIND_RANK = {
    "approval": 0,
    "question": 1,
    "ask": 1,
    "waiting": 1,
    "setup": 1,
    "declined": 2,
    "stale": 3,
    "task": 3,
    "report": 4,
    # Thread items (backend/thread_attention.py): an ask, a held message or a paused thread blocks a bot; a
    # mention or an unread DM is a person's to read.
    "thread_ask": 1,
    "thread_held": 1,
    "thread_capped": 1,
    "thread_mention": 2,
    "thread_dm": 2,
}


_VISIBLE_TASK_SQL = f"SELECT * FROM {H.TASKS} tasks WHERE id=? AND (%s)"


def _prime_person_origins(c, visible_sql, ids):
    """The rows person_origin reads, each task as `visible_sql` lets this reader see it or None, checked in one query
    per 500 with that same rule rather than one query per card and ancestor. Returns the ids the rule lets this
    reader see, so the caller needs no second check of them.

    The check answers one row, the readable ids as JSON, and the rows are the snapshot's task rows (H.prime_tasks; Needs
    you has read them all just before). Reading `SELECT *, (rule)` here stepped every row again, each step the GIL given
    up and taken back: on a busy server that waits up to the 5 ms switch interval a row, and a 2 ms read took 50 ms to
    1.4 s on Tico Team."""
    readable = set()
    for part in H.chunks(sorted({tid for tid in ids if tid})):
        found = json.loads(
            c.execute(
                "SELECT json_group_array(id) FROM tasks WHERE id IN (%s) AND (%s)"
                % (",".join("?" * len(part)), visible_sql),
                part,
            ).fetchone()[0]
        )
        readable.update(found)
        if read_cache.cache(c) is not None:
            H.prime_tasks(c, found, with_tags=False)
            # The row `SELECT * FROM tasks WHERE id=? AND (rule)` answers: the task's row as stored, or None.
            rows = {tid: read_cache.one(c, H.TASK_SQL, (tid,)) for tid in found}
            read_cache.prime(
                c, _VISIBLE_TASK_SQL % visible_sql, {(tid,): dict(rows[tid]) if rows.get(tid) else None for tid in part}
            )
    return readable


def prime_list_origins(c, visible_sql, rows):
    """Before person_origin runs for each row of a task list: the list's rows were read under `visible_sql` in this
    snapshot, so they stand for that rule's answer as they are; their ancestry is read a level at a time and the
    ancestors off the page are read under the same rule, so a long list walks no chain one query at a time."""
    if read_cache.cache(c) is None or not rows:
        return
    read_cache.prime(c, _VISIBLE_TASK_SQL % visible_sql, {(row["id"],): row for row in rows})
    TR.prime_ancestry(c, [row["id"] for row in rows])
    # Only rows whose requester is not a person walk their ancestors (person_origin).
    chain = {tid for row in rows if not H.is_human(row.get("requester_id")) for tid in TR.ancestors(c, row["id"])}
    _prime_person_origins(c, visible_sql, chain - {row["id"] for row in rows})


def person_origin(c, row, visible_sql, me=None):
    """The person a task traces back to: its requester when that is a person, else the nearest ancestor's.
    `visible_sql` is the reader's task rule (Auth.task_sql), so the walk stops at a task they may not open and
    the order never says what it holds. `me`'s own to-do (they asked themselves) does not count: it would lift
    their whole list over every bot. None for work bots made for themselves or each other."""

    def readable(tid):
        return read_cache.one(c, _VISIBLE_TASK_SQL % visible_sql, (tid,))

    if not row or not readable(row["id"]):
        return None
    if me and row.get("requester_id") == row.get("owner_id") == me:
        return None
    if H.is_human(row.get("requester_id")):
        return row["requester_id"]
    from .task_relations import ancestors

    for tid in ancestors(c, row["id"]):
        parent = readable(tid)
        if not parent:
            return None
        if H.is_human(parent["requester_id"]):
            return parent["requester_id"]
    return None


def pressing(it, now=None):
    """A bot's item that may go ahead of a person's: due within a day or overdue, or waiting long enough."""
    now = now or H.now()
    due = it.get("due_at")
    if due and str(due) <= H.shift(now, days=1):
        return True
    days = DECISIVE_DAYS if KIND_RANK.get(it.get("kind"), 9) <= 1 else PRESSING_DAYS
    return bool(it.get("created_at")) and str(it["created_at"]) <= H.shift(now, days=-days)


CHECKOUT_BEHIND_HOURS = 1


def runner_busy(c, runner_id):
    """Turns running on a computer now: what a restart would wait for."""
    return c.execute(
        "SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running') AND lease_until>?",
        (runner_id, H.now()),
    ).fetchone()[0]


def runner_busy_many(c, runner_ids):
    """runner_busy for many computers, {runner: turns}, in one read per 500 computers."""
    now, found = H.now(), {}
    ids = sorted(set(runner_ids))
    for part in H.chunks(ids):
        found.update(
            c.execute(
                "SELECT runner_id,count(*) FROM attempts WHERE runner_id IN (%s) "
                "AND state IN ('leased','running') AND lease_until>? GROUP BY runner_id" % ",".join("?" * len(part)),
                (*part, now),
            ).fetchall()
        )
    return {rid: found.get(rid, 0) for rid in runner_ids}


def restart_issue(add, row, busy, since=None):
    """Behind main, or pulled but not restarted: one plain line and a Restart button The runner also does this by itself; the button is for now."""
    if row["restart_requested_at"]:
        detail = "Restarting" + (
            f" after {busy} running turn{'s' if busy != 1 else ''} finish{'es' if busy == 1 else ''}."
            if busy
            else " now. Nothing is running, so nothing will be interrupted."
        )
    elif busy:
        detail = f"{busy} turn{'s are' if busy != 1 else ' is'} running; a restart waits for {'them' if busy != 1 else 'it'} to finish."
    else:
        detail = "Nothing is running, so nothing will be interrupted."
    add(
        "runner_checkout",
        row["label"] + " has a Tico update",
        detail,
        machine=row["id"],
        since=since,
        severity="warning",
        restart=not row["restart_requested_at"],
    )
    return True


def checkout_issue(add, row, busy=0):
    """A runner whose checkout is not what main says (#492): the production runner ran 28 commits
    behind for a day because a bot committed in it. Local commits never fast-forward, so they are
    said at once; being behind is said once it has lasted an hour (a pull is on its way before)."""
    checkout = H._json(row["checkout"], {}) or {}
    if not checkout:
        return
    ahead, behind = int(checkout.get("ahead") or 0), int(checkout.get("behind") or 0)
    if ahead:
        add(
            "runner_checkout",
            row["label"] + "'s checkout has commits that are not on main",
            f"{ahead} local commit{'s' if ahead != 1 else ''}"
            + (f" and {behind} behind main" if behind else "")
            + ": it cannot fast-forward, so it will not pick up new code until someone sorts it out.",
            machine=row["id"],
            since=checkout.get("checked_at"),
            action="On that computer: push the commits through a pull request, or reset the checkout to main.",
        )
    elif behind and checkout.get("blocked"):
        # It cannot update itself (uncommitted changes, another branch, a dependency change):
        # a restart would not help, so say what is in the way at once.
        add(
            "runner_checkout",
            row["label"] + " can't update itself",
            f"{behind} commit{'s' if behind != 1 else ''} behind main: {checkout['blocked']}.",
            machine=row["id"],
            since=checkout.get("checked_at"),
            severity="warning",
            action="Tico repairs these links on the next update. No local work needs a decision."
            if str(checkout["blocked"]).startswith("only flattened Tico links:")
            else "On that computer: commit or stash the changes (or run pip install and scripts/tico-computer update "
            "for a dependency change); the runner then updates itself.",
        )
    elif behind:
        stamp = checkout.get("behind_since") or checkout.get("checked_at") or ""
        if stamp and stamp <= H.shift(H.now(), seconds=-CHECKOUT_BEHIND_HOURS * 3600):
            restart_issue(add, row, busy, since=stamp)
    elif checkout.get("running") and checkout.get("head") and checkout["running"] != checkout["head"]:
        restart_issue(add, row, busy)


DUE_SOON_DAYS = 7


def needs_order(kind, created, rank=None, person=None, urgent=False, ask_missing=False, due=None):
    """Needs you is the person's queue. A card still waiting for its bot to say what it needs goes last, whatever
    else it is; above that, what is overdue or due within DUE_SOON_DAYS first, soonest first (a date further off
    does not jump the queue); then what traces back to a person, or presses; then within that what blocks a bot (an
    approval, a question), then declined work, then their own tasks in rank order."""
    soon = str(due) if due and str(due) <= H.shift(H.now(), days=DUE_SOON_DAYS) else ""
    return (
        1 if ask_missing else 0,
        0 if soon else 1,
        soon,
        0 if person or urgent else 1,
        KIND_RANK.get(kind, 9),
        rank if rank is not None else float("inf"),
        str(created or ""),
    )


def deadline(item):
    """When the card is due: a person's task's due date. A bot's own task's due is when it looks again, not a date
    anyone owes it by."""
    return item.get("due_at") if item.get("kind") != "approval" and not H.is_bot(item.get("owner_id")) else None


def _clip(text, n=280):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def recent_bots(c, auth, who, since, limit, needs):
    """The bots `who` exchanged messages or tasks with since `since`, most recent first."""
    me, seen = who.actor, {}
    access = auth.bot_accesses(c, who)

    def touch(slug, ts):
        if slug and access.get(slug, auth.FULL)["see"] and ts and ts > seen.get(slug, ""):
            seen[slug] = ts

    # Discovery window: one readable_messages prime (not message_readable per row), then task_sql for tasks.
    recent_msgs = [
        dict(r)
        for r in c.execute(
            "SELECT id, sender_id, recipient_id, created_at FROM messages WHERE created_at>=? AND "
            "((sender_id=? AND recipient_id LIKE 'bot:%') OR (recipient_id=? AND sender_id LIKE 'bot:%'))",
            (since, me, me),
        )
    ]
    for row in privacy.readable_messages(c, privacy.actor(who), recent_msgs):
        touch(H.actor_id(row["recipient_id"] if row["sender_id"] == me else row["sender_id"]), row["created_at"])
    visible = auth.task_sql(c, who)
    for row in c.execute(
        "SELECT id, owner_id, requester_id, updated_at FROM tasks WHERE updated_at>=? AND "
        f"((requester_id=? AND owner_id LIKE 'bot:%') OR (owner_id=? AND requester_id LIKE 'bot:%')) AND ({visible})",
        (since, me, me),
    ):
        touch(H.actor_id(row["owner_id"] if row["requester_id"] == me else row["requester_id"]), row["updated_at"])
    waiting = {}
    for item in needs:
        actor = needs_actor(item, me)
        if H.is_bot(actor):
            waiting.setdefault(H.actor_id(actor), []).append(item.get("line") or "")
    out = []
    recent = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    status_inputs = privacy.status_inputs(c, [slug for slug, _ in recent if access.get(slug, auth.FULL)["read"]])
    for slug, last in recent:
        bot = H.bot(c, slug) or {}
        # The bot's status is its activity: a person who may only write to it hears what it said
        # to them, not what it is doing.
        status = (
            (privacy.status(c, who, H.status(c, slug), **status_inputs[slug]) or {})
            if access.get(slug, auth.FULL)["read"]
            else {}
        )
        actor = H.bot_actor(slug)
        # Newest readable line each way. Lazy, not a fixed window: deleted or unreadable newest rows must not
        # hide an older readable one, and the first row is readable almost always.
        mine = next(
            (
                m
                for m in c.execute(
                    "SELECT * FROM messages WHERE sender_id=? AND recipient_id=? ORDER BY created_at DESC", (me, actor)
                )
                if privacy.message_readable(c, privacy.actor(who), m)
            ),
            None,
        )
        theirs = next(
            (
                m
                for m in c.execute(
                    "SELECT * FROM messages WHERE sender_id=? AND recipient_id=? AND deleted_at IS NULL "
                    "ORDER BY created_at DESC",
                    (actor, me),
                )
                if privacy.message_readable(c, privacy.actor(who), m)
            ),
            None,
        )

        conversation = next(
            (
                m["conversation_id"]
                for m in sorted(filter(None, (mine, theirs)), key=lambda m: m["created_at"], reverse=True)
                if m["conversation_id"]
            ),
            None,
        )
        tasks = c.execute(
            "SELECT id, title, status, owner_id, updated_at FROM tasks WHERE status='open' "
            "AND ((owner_id=? AND requester_id=?) OR (owner_id=? AND requester_id=?)) "
            "ORDER BY updated_at DESC LIMIT 5",
            (actor, me, me, actor),
        ).fetchall()
        current = H.task(c, status["task_id"]) if status.get("task_id") else None
        out.append(
            {
                "bot": slug,
                "name": bot.get("display_name") or bot.get("slug") or slug,
                "last_interaction": last,
                "status": {
                    "state": status.get("state") or bot.get("state"),
                    "focus": status.get("focus") or "",
                    "changed_at": status.get("changed_at"),
                    "working_on": (current or {}).get("title"),
                    "last_turn_at": status.get("last_turn_at"),
                },
                "conversation_id": conversation,
                "you_said": {"text": _clip(mine["body"]), "at": mine["created_at"]} if mine else None,
                "it_said": {"text": _clip(theirs["body"]), "at": theirs["created_at"]} if theirs else None,
                "open_tasks": [
                    {
                        "id": t["id"],
                        "title": t["title"],
                        "status": t["status"],
                        "owner_id": t["owner_id"],
                        "updated_at": t["updated_at"],
                    }
                    for t in tasks
                ],
                "needs_you": waiting.get(slug, []),
            }
        )
    return out


def _readable_task_ids(c, visible, ids):
    """Task ids among `ids` that pass `visible` (`auth.task_sql`), in one IN-chunked pass.

    Needs you used to call `auth.task` once per card (and again for merged / approval / stale
    ids); the SQL predicate is the same rule lists elsewhere use.
    """
    out = set()
    wanted = [tid for tid in dict.fromkeys(ids) if tid]
    for part in H.chunks(wanted):
        marks = ",".join("?" * len(part))
        out.update(r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({marks}) AND ({visible})", part))
    return out


BRIEF_FIELDS = (
    "id",
    "number",
    "title",
    "status",
    "version",
    "pipeline_id",
    "stage_id",
    "owner_id",
    "assignee_id",
    "requester_id",
    "due_at",
    "turn",
    "late",
    "snoozed",
    "pushed_past",
)

# Every @ of a person by another person that they have not answered since in the same conversation (C28): on a task
# (its own room, or a comment in a chat the task was filed into) or in a channel. A bot's @ only marks a task unread.
MENTIONS_SQL = (
    "SELECT m.id, m.conversation_id, coalesce(cv.root_id, cv.id) AS root_id, m.sender_id, m.body, m.created_at, "
    "coalesce(cv.task_id, json_extract(CASE WHEN json_valid(m.refs) THEN m.refs END, '$.task')) AS task_id "
    "FROM message_deliveries d JOIN messages m ON m.id=d.message_id JOIN conversations cv ON cv.id=m.conversation_id "
    "WHERE d.recipient=? AND d.reason='mention' AND m.sender_id LIKE 'human:%' AND m.sender_id<>? "
    "AND m.deleted_at IS NULL AND m.superseded_at IS NULL AND cv.closed_at IS NULL AND cv.archived_at IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id AND r.sender_id=? "
    "AND r.created_at>m.created_at AND r.deleted_at IS NULL) "
    "ORDER BY m.created_at, m.id"
)


def _mentions(c, who):
    """The @s of `who` by a person that they have not replied to and may still read, oldest first."""
    rows = [dict(r) for r in c.execute(MENTIONS_SQL, (who.actor, who.actor, who.actor))]
    # Each mention's message, rooms and references read together, not a few statements per mention.
    stored = H.prime_messages(c, [row["id"] for row in rows])
    privacy.prime_messages(c, list(stored.values()))
    from . import threads

    threads.prime_members(c, who, [row["conversation_id"] for row in rows])
    seen, out = set(), []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        message = H.message(c, row["id"])
        if message and privacy.message_readable(c, privacy.actor(who), message):
            out.append(row)
    return out


def _briefs(c, ids, visible):
    """The brief task (docs/dev/pipelines-v3-cutover.md, "The task as JSON") of each id, read together."""
    from . import task_reads

    if not ids:
        return {}
    rows = H.tasks(c, ids=sorted(ids), limit=None, order="finished", visible=visible)
    task_reads.brief(c, rows, visible)
    return {row["id"]: {k: row.get(k) for k in BRIEF_FIELDS} for row in rows}


def needs_items(c, auth, who, task_view=None, count_only=False):
    """Needs you, one list for a person (docs/dev/pipelines-v3-cutover.md, "Needs you"), oldest first:
    `waiting_on.needs(person)` (every requirement with their name, every task whose work is theirs, every Paused cause
    they can fix) and every @ of them by a person, on a task or in a channel, until they reply (C28). Only what the
    person may read is listed. Each item has `kind` (task, ask, paused, mention), `id`, `line`, `since` and the brief
    `task`; an ask also has the fields of its requirement in `to_leave` (`ask`). `count_only` leaves out the brief
    task and the ask's fields: a count needs neither. `task_view` is the callers' and not read here."""
    from . import strategy_merge, teammate_access, waiting_on

    if not H.is_human(who.actor):
        return []
    visible = auth.task_sql(c, who)
    # The asks the Strategy Owner merged into one item stay out while that item is open; the item names them (`merged`).
    merged = strategy_merge.hidden_by_item(c, who.actor)
    hidden = {rid for ids in merged.values() for rid in ids}
    raw = [i for i in waiting_on.needs(c, who.actor) if i.get("requirement_id") not in hidden]
    mentions = _mentions(c, who)
    wanted = {i["task_id"] for i in raw if i.get("task_id")} | {t for i in raw for t in i.get("tasks") or ()}
    wanted |= {m["task_id"] for m in mentions if m.get("task_id")}
    readable = _readable_task_ids(c, visible, wanted)
    briefs = {} if count_only else _briefs(c, readable, visible)
    items = []
    for it in raw:
        if it["kind"] == "paused":
            tasks = [t for t in it["tasks"] if t in readable]
            if not tasks:
                continue
            items.append(
                {
                    "kind": "paused",
                    "id": "paused:" + hashlib.sha1(it["line"].encode()).hexdigest()[:12],
                    "line": it["line"],
                    "since": it["since"],
                    "bots": it["bots"],
                    "task_ids": tasks,
                    "task": briefs.get(tasks[0]),
                    **({"href": it["href"]} if it.get("href") else {}),
                }
            )
            continue
        if it["task_id"] not in readable:
            continue
        items.append(
            {
                "kind": it["kind"],
                "id": waiting_on.ask_id(it) if it["kind"] == "ask" else it["task_id"],
                "task_id": it["task_id"],
                "requirement_id": it["requirement_id"],
                "line": it["line"],
                "missing": it["missing"],
                "since": it["since"],
                "task": briefs.get(it["task_id"]),
                **({"merged": merged[it["task_id"]]} if it["task_id"] in merged else {}),
            }
        )
    # A person the reader may not see is named only to someone in the same conversation (teammate_access.Known).
    known = teammate_access.Known(c, who, auth)  # reads nothing until it is asked
    for m in mentions:
        if m.get("task_id") and m["task_id"] not in readable:
            continue
        first = (m["body"] or "").strip().splitlines()
        items.append(
            {
                "kind": "mention",
                "id": m["id"],
                "message_id": m["id"],
                "conversation_id": m["conversation_id"],
                "root_id": m["root_id"],  # the thread a rail row stands for: a sub-thread's mention moves its root's
                "task_id": m.get("task_id"),
                "from_id": known.name(m["sender_id"], m["conversation_id"]),
                "line": _clip(first[0] if first else "", 160),
                "since": m["created_at"],
                "task": briefs.get(m.get("task_id")),
            }
        )
    if not count_only:
        # An ask carries its requirement as the task's own read gives it (`to_leave`), with what this person may do:
        # that entry alone (task_calls.ask_entry), not the whole view of its task.
        from . import task_calls

        # Every ask's task read together: the list costs what one task's view does.
        task_calls.prime_views(c, {item["task_id"] for item in items if item["kind"] == "ask"}, who.actor)
        for item in items:
            if item["kind"] != "ask":
                continue
            item["ask"] = task_calls.ask_entry(c, item["task_id"], item["requirement_id"], who.actor)
            # What the task waits on says only "a person's OK" for a bound OK (waiting_on); its own line is this
            # person's to read when they are one of its parties, as their read of the task gives it.
            if item["ask"] and item["ask"]["kind"] == "ok":
                item["line"] = item["ask"]["line"]
                item["missing"] = [item["ask"]["line"]]
    return sorted(items, key=needs_key)


def needs_key(x):
    """An item's place in the list: oldest first."""
    return (str(x.get("since") or ""), str(x["id"]))


def needs_marks(items):
    """({id: fingerprint}, version) of a Needs-you list as needs_items orders it. A fingerprint covers the item and its
    place in the order, so an item whose mark is unchanged kept its content and its order against the others: a page
    that holds the list mends it from the changed ids alone (the live `needs` event, `GET /api/v2/needs-you?ids=`)."""
    marks = {}
    for x in items:
        marks[str(x["id"])] = hashlib.sha1(
            (json.dumps(x, sort_keys=True, default=str) + repr(needs_key(x))).encode()
        ).hexdigest()[:12]
    version = hashlib.sha1("|".join(f"{k}:{v}" for k, v in marks.items()).encode()).hexdigest()[:16]
    return marks, version


NEEDS_IDS_MAX = 100  # the most a live `needs` event names; more changed than that reads the list whole
NEEDS_PAGE = 50  # a page of Needs you when `cursor` comes without `limit`
NEEDS_PAGE_MAX = 200


def needs_cursor(version, offset):
    """The `next` of a Needs-you page: where the next page starts in the list at `version`. The app's hub-v2.ts
    needsCursor writes the same, to page on from a held list it mended."""
    return f"{version}.{offset}"


def needs_page(items, version, limit, cursor):
    """(page, next cursor, restarted) of a Needs-you list. A cursor names the version it was read from: once the list
    moved, its offset points nowhere, so the page starts from the top again (`restarted`)."""
    offset, restarted = 0, False
    if cursor:
        held, _, at = cursor.partition(".")
        if not at.isdigit():
            raise Problem("validation", "Unknown cursor", 422)
        offset, restarted = (int(at), False) if held == version else (0, True)
    limit = max(1, min(int(limit or NEEDS_PAGE), NEEDS_PAGE_MAX))
    end = offset + limit
    return items[offset:end], (needs_cursor(version, end) if end < len(items) else None), restarted


def needs_bots(items, me):
    """How many Needs-you items each bot is behind ({"bot:slug": n}, needs_actor). A page carries this for the whole
    list, so the sidebar's numbers do not wait for every page."""
    counts = {}
    for it in items:
        actor = needs_actor(it, me)
        if H.is_bot(actor):
            counts[actor] = counts.get(actor, 0) + 1
    return counts


# The items a stage's review walks (the ▶ on a stage): asks, tasks and @s on a task in that stage. A Paused cause is the
# person's to fix in settings, whatever stage its first task is in.
NEEDS_STAGE_KINDS = ("ask", "task", "mention")


def needs_stage(it):
    """The stage one Needs-you item is in, for a stage's review: its task's stage, or None."""
    if it.get("kind") not in NEEDS_STAGE_KINDS:
        return None
    return (it.get("task") or {}).get("stage_id") or None


def needs_stages(items):
    """How many Needs-you items wait in each stage ({"<stage id>": n}, needs_stage) over the whole list, so a stage's ▶
    shows its count before every page is read."""
    counts = {}
    for it in items:
        stage = needs_stage(it)
        if stage:
            counts[stage] = counts.get(stage, 0) + 1
    return counts


def needs_kinds(items):
    """Whole-list counts by kind."""
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return counts


def needs_plate_count(items):
    """The one count on the Tasks badge: the length of the list."""
    return len(items)


def needs_actor(it, me):
    """The bot one Needs-you item goes with, or None: the bot that asked, for an ask; the bot that asked for the task,
    for a task; the first bot that cannot run, for a Paused cause. A mention is a person's."""
    task = it.get("task") or {}
    if it.get("kind") == "paused":
        return next(iter(it.get("bots") or ()), None)
    if it.get("kind") == "ask":
        asked = (it.get("ask") or {}).get("asked_by")
        options = [asked, task.get("owner_id"), task.get("requester_id")]
    elif it.get("kind") == "task":
        options = [task.get("requester_id")]
    else:
        return None
    return next((a for a in options if H.is_bot(a) and a != me), None)


NEEDS_SUMMARY_ITEMS = 10  # the newest items listed under each bot (at most)
NEEDS_SUMMARY_BOTS = 25  # the bots listed, most waiting first; the rest are counted in `bots_left`


def needs_summary(c, auth, who, task_view, limit):
    """What the caller's Needs you holds: the total, the number per kind, and per bot (`by_bot`, needs_actor): its
    count, the ones made this week and the ones older than three days, and its newest `limit` items. Items no bot put
    there are grouped under `other`. Ages are days from each item's `since`."""
    items = needs_items(c, auth, who, task_view)
    now = H.now()
    week, older = H.shift(now, days=-7), H.shift(now, days=-3)
    groups, kinds, this_week = {}, {}, 0
    for it in items:
        key = needs_actor(it, who.actor) or "other"
        created = it.get("since") or now
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        this_week += created >= week
        group = groups.setdefault(key, {"count": 0, "this_week": 0, "older_than_3_days": 0, "items": []})
        group["count"] += 1
        group["this_week"] += created >= week
        group["older_than_3_days"] += created < older
        group["items"].append(
            {
                "id": str(it["id"]),
                "kind": it["kind"],
                "task_id": it.get("task_id"),
                "line": _clip(it.get("line"), 80),
                "since": created,
                "age_days": round(age_days(created, now), 1),
            }
        )
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    by_bot = []
    for key, group in ordered[:NEEDS_SUMMARY_BOTS]:
        group["items"].sort(key=lambda x: x["since"], reverse=True)
        by_bot.append(
            {
                "bot": key,
                "name": H.actor_name(c, key) if key != "other" else "Other",
                **{k: v for k, v in group.items() if k != "items"},
                "items": group["items"][:limit],
            }
        )
    return {
        "actor": who.actor,
        "total": len(items),
        "this_week": this_week,
        "by_kind": kinds,
        "by_bot": by_bot,
        "bots_left": max(0, len(ordered) - len(by_bot)),
    }


def age_days(created, now):
    """Whole-day age in days (fractions kept) from an ISO time to `now`; 0 when it does not parse."""
    start, end = H.parse_ts(created), H.parse_ts(now)
    return max(0.0, (end - start).total_seconds() / 86400) if start and end else 0.0


def fleet_snapshot(c, auth, identity, task_view):
    """One live, actor-scoped control-plane snapshot for Tico's private room."""
    privacy.snapshot(c)
    principal = auth.tico_principal(c, identity)
    access = auth.bot_accesses(c, principal)
    bot_list = H.bots(c)
    allowed = {row["id"] for row in bot_list if access.get(row["id"], auth.FULL)["write"]}
    task_rows = [
        dict(t) for t in c.execute(f"SELECT {privacy.STATUS_TASK_SELECT} FROM tasks WHERE {privacy.STATUS_TASKS_SQL}")
    ]
    bot_tasks = {}
    for task in task_rows:
        for actor in {task["owner_id"], task["requester_id"]}:
            if H.is_bot(actor):
                bot_tasks.setdefault(H.actor_id(actor), []).append(task)
    statuses = {s["bot"]: dict(s) for s in c.execute("SELECT * FROM bot_status")}
    queued = {}
    from .privacy_index import ReadIndex

    index = ReadIndex(c, principal)
    for m in c.execute(
        "SELECT m.*,j.bot_id,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'"
    ):
        if (
            not dict(m).get("deleted_at")
            and not dict(m).get("deleted")
            and index.message(m["id"], m["conversation_id"], m["refs"], m["in_reply_to"])
            and (not m["attempt_id"] or index.attempt(m["attempt_id"]))
        ):
            queued[m["bot_id"]] = queued.get(m["bot_id"], 0) + 1
    # One pass for every live attempt: the per-bot SELECT below used to pay once per writable bot.
    now = H.now()
    live_by_bot = {}
    for row in c.execute(
        "SELECT id,bot_id,state,started_at,lease_until,created_at FROM attempts "
        "WHERE state IN ('leased','running') AND lease_until>? ORDER BY created_at DESC",
        (now,),
    ):
        live_by_bot.setdefault(row["bot_id"], []).append(row)
    # machine() / presence used to re-SELECT assignment+agent rows once per readable bot; Health already primes.
    shown = [row["id"] for row in bot_list if row["id"] in allowed and access.get(row["id"], auth.FULL)["read"]]
    prime_machines(c, shown)
    bots = []
    for row in bot_list:
        slug = row["id"]
        if slug not in allowed:
            continue
        if not access.get(slug, auth.FULL)["read"]:
            # A bot the person may only send requests to: its name and that it exists, not its work.
            bots.append(
                {
                    "id": slug,
                    "slug": row["slug"],
                    "name": row["display_name"],
                    "state": row["state"],
                    "focus": "",
                    "task_id": None,
                    "online": None,
                    "ready": None,
                    "queued": None,
                    "active_run": None,
                    "active_runs": 0,
                }
            )
            continue
        # Fleet only exposes focus/task identity, so counters/approval scans are unnecessary.
        source = {k: v for k, v in statuses.get(slug, {}).items() if k not in ("open_tasks", "needs_human")}
        status = privacy.status(c, principal, source, tasks=bot_tasks.get(slug, [])) or {}
        location = machine(c, slug)
        # A bot with task lanes can have several turns out; the newest is the one shown, beside the count.
        active = [a for a in live_by_bot.get(slug, ()) if index.attempt(a["id"])]
        attempt = active[0] if active else None
        bots.append(
            {
                "id": slug,
                "slug": row["slug"],
                "name": row["display_name"],
                "state": status.get("state") or row["state"],
                "focus": status.get("focus") or "",
                "task_id": status.get("task_id"),
                "online": location["online"],
                "ready": location["ready"],
                "queued": queued.get(slug, 0),
                "active_run": {
                    "id": attempt["id"],
                    "state": runs.state(attempt["state"]),
                    "started_at": attempt["started_at"],
                    "lease_until": attempt["lease_until"],
                }
                if attempt
                else None,
                "active_runs": len(active),
            }
        )
    tasks = []
    for row in H.tasks(c, status=H.ACTIVE_STATUSES, visible=auth.task_sql(c, principal)):
        actors = (row["owner_id"], row["requester_id"])
        relevant = principal.actor in actors or any(
            str(actor).startswith("bot:") and H.actor_id(actor) in allowed for actor in actors
        )
        if not relevant:
            continue
        tasks.append(
            {
                k: row.get(k)
                for k in ("id", "title", "owner_id", "requester_id", "status", "due_at", "note", "updated_at")
            }
        )
    items = needs_items(c, auth, principal, task_view)
    return {
        "actor": principal.actor,
        "as_of": H.now(),
        "bots": bots,
        "tasks": tasks,
        "needs_you": items,
    }


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
        rows.sort(key=lambda item: item[0].get("started_at") or "", reverse=True)
        return rows[:limit]

    def run_view(turn, c=None, made=None):
        turn = {**turn, **attempt_details(c, turn)} if c else turn
        started, finished = H.parse_ts(turn.get("started_at")), H.parse_ts(turn.get("finished_at"))
        duration = max(0, round((finished - started).total_seconds())) if started and finished else None
        success = turn.get("exit") == "completed"
        # A live run (runs.LIVE) has no result yet: no exit code and no finished time, not a failure ending at its start.
        live = turn.get("state") in LIVE_RUN_STATES
        if made is None and c is not None:
            made = ran_on(c, [turn["id"]])[turn["id"]]
        return {
            "id": turn["id"],
            "bot": turn["bot"],
            "employee": turn["bot"],
            "issue": None,
            "state": turn.get("state"),
            "outcome": turn.get("exit"),
            "failure_reason": turn.get("failure_reason") or "",
            **{
                k: turn.get(k)
                for k in ("end_reason", "ended_at", "last_event", "last_event_at", "stalled", "stalled_at")
            },
            "finished_at": None if live else turn.get("finished_at") or turn.get("started_at"),
            "exit": None if live else 0 if success else 1,
            "duration_s": duration,
            "output_tokens": turn.get("output_tokens"),
            "cost_usd": turn.get("est_cost_usd"),
            "resumed": False,
            "fallback": made["fallback"] if made else None,
            "ran_on": made,
            "load_start": turn.get("load_start"),
            "load_end": turn.get("load_end"),
        }

    def employee_rows(c, who):
        people, configs = roster(c), entries(c, store.settings.github_owner)
        bots = H.bots(c)
        archived = {row["id"] for row in bots if row.get("state") == "archived"}
        registries = {r["bot"]: r for r in read_cache.configs(c)}
        from .browser_exit import bot_exits

        exits = bot_exits(c)  # the computer each bot browses through, when one is set
        goals = {}
        for bot, conversation in c.execute("SELECT bot,conversation_id FROM chat_goals WHERE status='active'"):
            goals.setdefault(bot, []).append(conversation)
        company = providers.load(c, store.settings)
        from . import cloud_machines

        # Each bot's cloud machine, own or shared, Tico Cloud only (backend/cloud_machines.py): one read for every bot.
        cloud_rows = cloud_machines.visible_rows(c) if cloud_machines.offered(c, store.settings) else None
        # Each bot's last finished run, one attempts_bot_settled seek per bot rather than a sort of every run there is.
        latest_run = {
            slug: tid
            for slug, tid in c.execute(
                "SELECT b.id,(SELECT t.id FROM attempts t WHERE t.bot_id=b.id AND t.settled_at IS NOT NULL "
                "ORDER BY t.settled_at DESC,t.id DESC LIMIT 1) FROM bots b"
            )
            if tid
        }
        from .usage_records import ran_on

        run_views = ran_on(c, list(latest_run.values()))
        latest_models = {}
        for slug, turn_id in latest_run.items():
            segments = run_views[turn_id].get("segments") or []
            families = {part["family"] for part in segments if part.get("family")}
            family = (
                next(iter(families)) if len(families) == 1 and all(part.get("family") for part in segments) else None
            )
            models = list(dict.fromkeys(part.get("model") for part in segments if part.get("model")))
            latest_models[slug] = (
                family,
                ", ".join(providers.MODEL_BY_ID.get(model, {}).get("label") or model for model in models),
            )
        rows = []
        access = auth.bot_accesses(c, who)
        # The viewer's own access to each bot, {see, read, write, manage}, from the teammate rules in one pass (the
        # `my_access` a row shows). The gates below still read `access`, which applies the same rules.
        from . import teammate_access

        mine = teammate_access.levels_many(c, who, ["bot:" + bot["id"] for bot in bots], auth=auth)
        # What the loop reads per bot, read for every bot at once: its computer and its declared configuration from
        # the rows above.
        shown = [
            bot["id"] for bot in bots if bot.get("state") != "archived" and access.get(bot["id"], auth.FULL)["see"]
        ]
        prime_machines(c, [slug for slug in shown if access.get(slug, auth.FULL)["read"]])
        from .shared_bots import _json as config_of

        def declared(slug):
            row = registries.get(slug)
            return config_of(row["config"]) if row else {}

        from .report_order import positions

        places = positions(c)  # its place among its manager's reports on the chart, when someone set one
        # The Material Symbol a bot's avatar wears: its own, else its template's. A bot made from a `kind: helper` card
        # serves a person, and the UI shows it with the built-ins, apart from the org chart.
        from .onboarding import helper_templates, template_icons

        icons, helpers = template_icons(store.settings), helper_templates(store.settings)
        # Where each bot hangs on the chart; the Strategy Owner switch moves it there and nowhere else
        # (backend/strategy_bot.py). A person who hid the Strategy Owner from their chart sees nothing moved, and its row
        # says so (`chart_hidden`).
        from . import strategy_bot

        hide_strategy = strategy_bot.hidden_for(c, who)
        chart = strategy_bot.chart_entries(c, store.settings, people, configs, archived, hidden=hide_strategy)
        bare = True  # a bot's bare person id in reports_to is that person (backend/people.py)

        def icon_of(config):
            return str(config.get("icon") or "") or icons.get(str(config.get("template") or ""), "")

        def helper(config):
            return str(config.get("template") or "") in helpers

        from .chat_goals import readable_active

        for bot in bots:
            slug = bot["id"]
            level = access.get(slug, auth.FULL)
            mine_level = mine.get("bot:" + slug) or {**level, "manage": False}
            if bot.get("state") == "archived" or not level["see"]:
                continue
            registry = registries.get(slug)
            # Who may manage the bot (`Auth.bot_manager`), with the audiences and its owners when they may.
            pid = H.actor_id(who.actor)
            manager = who.role == "owner" or (
                who.role == "human"
                and (
                    auth.bot_admin(who)
                    or (registry and (registry["owner_id"] == pid or pid in A.owner_ids(registry["bot_owners"])))
                    or P.manages(pid, "bot", slug, people, configs, archived, bare)
                )
            )
            policy = {"can_manage": manager, "bot_owners": bot_owner_rows(c, registry, slug, people, configs, archived)}
            if manager:
                policy["access_policy"] = A.document(registry["access"] if registry else None)
            if not level["read"]:
                # Seen, not read: the name, role, who runs it, who it reports to. Its
                # configuration, routines, machine and goals are its activity.
                reports = (configs.get(slug, {}) or {}).get("reports_to") or ""
                if reports and not str(reports).startswith("human:") and not access.get(reports, auth.FULL)["see"]:
                    reports = ""
                rows.append(
                    {
                        "id": bot["id"],
                        "slug": bot["slug"],
                        "name": bot["slug"],
                        "display_name": bot["display_name"],
                        "state": bot["state"],
                        "status": bot["state"],
                        "description": (configs.get(slug, {}) or {}).get("description") or "",
                        "reports_to": reports,
                        "my_access": mine_level,
                        **policy,
                        "private_tasks_default": H.private_tasks_default(c, "bot:" + slug, declared),
                        "team": P.team_of(slug, configs, people),
                        "org_parent": P.org_parent("bot", slug, people, chart, archived, bare),
                        **strategy_bot.chart_hidden_row(slug, hide_strategy),
                        "report_position": places.get("bot:" + slug),
                        "owner_id": registry["owner_id"] if registry else None,
                        "is_branch": bool((configs.get(slug) or {}).get("shared_from")),
                        "users": [P.brief(p) for p in P.primary_users(slug, people, configs)],
                        "icon": icon_of(configs.get(slug, {}) or {}),
                        "helper": helper(configs.get(slug, {}) or {}),
                        "can_chat": level["write"] and slug not in (auth.settings.assistant_bot, STRATEGY_BOT),
                    }
                )
                continue
            config = configs.get(slug, {})
            # Configuration contains connector requirements, never connector credentials.
            config = {k: v for k, v in config.items() if k not in ("cwd", "env", "token", "secrets")}
            location = machine(c, slug)
            # Read-only, for the quiet runtime label beside a bot's name; empty when nothing resolves.
            try:
                resolved_runtime, resolved_model = providers.resolve(company, config)
            except providers.ProviderError:
                resolved_runtime = resolved_model = ""
            resolved_provider = (providers.MODEL_BY_ID.get(resolved_model or "") or {}).get("provider", "")
            resolved_provider_label = providers.PROVIDER_BY_ID.get(resolved_provider, {}).get("label", "")
            resolved_model_label = (providers.MODEL_BY_ID.get(resolved_model or "") or {}).get(
                "label"
            ) or resolved_model
            latest_family, latest_model = latest_models.get(slug, (None, ""))
            show_latest = bool(latest_model) and (not latest_family or latest_family != resolved_provider)
            own = bool(providers._named(config.get("runtime")) or providers._named(config.get("model")))
            # The effort it runs at, said beside the model on hover; empty when the bot has none.
            resolved_effort = (
                str(config.get("reasoning_effort") or config.get("effort") or bot.get("effort") or "")
                if resolved_runtime
                else ""
            )
            rows.append(
                {
                    **config,
                    "resolved_runtime": resolved_runtime,
                    "resolved_model": resolved_model,
                    "resolved_model_label": resolved_model_label,
                    "resolved_provider": resolved_provider_label,
                    "resolved_effort": resolved_effort,
                    "latest_runtime_model": latest_model if show_latest else "",
                    "latest_runtime_provider": providers.PROVIDER_BY_ID.get(latest_family, {}).get("label")
                    if show_latest and latest_family
                    else "Unknown provider"
                    if show_latest
                    else "",
                    "model_source": "" if not resolved_runtime else "bot" if own else "company",
                    "id": bot["id"],
                    "slug": bot["slug"],
                    "name": bot["slug"],
                    "display_name": bot["display_name"],
                    "state": bot["state"],
                    "status": bot["state"],
                    "private_tasks_default": H.private_tasks_default(c, "bot:" + slug, declared),
                    "host": "keeper",
                    "tasks": "hub",
                    "has_repo": location["ready"],
                    "turns_at_once": start_places.bot_limit(config),
                    "team": P.team_of(slug, configs, people),
                    "org_parent": P.org_parent("bot", slug, people, chart, archived, bare),
                    **strategy_bot.chart_hidden_row(slug, hide_strategy),
                    "report_position": places.get("bot:" + slug),
                    "owner_id": registry["owner_id"] if registry else None,
                    "revision": registry["revision"] if registry else None,
                    "goals": (registry["goals"] if registry else "") or "",
                    "goal_active": readable_active(c, auth, who, slug, goals.get(slug, [])),
                    **location,
                    "browser_exit": exits.get(slug),
                    "users": [P.brief(p) for p in P.primary_users(slug, people, configs)],
                    "icon": icon_of(config),
                    "helper": helper(config),
                    # What Settings and the branch editor show and pick from (backend/wake_presets.py).
                    "wake": wake_presets.effective(config),
                    "profile": profiles.effective(config),
                    "my_access": mine_level,
                    **policy,
                    "can_chat": level["write"] and slug not in (auth.settings.assistant_bot, STRATEGY_BOT),
                    # Onboarding or Active, and whether this person may mark onboarding complete (backend/lifecycle.py).
                    "stage": lifecycle.stage(config.get("onboarding_state")),
                    "can_complete_onboarding": (
                        lifecycle.stage(config.get("onboarding_state")) == lifecycle.ONBOARDING
                        and lifecycle.may_complete(auth, c, who, slug)
                    ),
                    # Labelled as on the bot list, so its computer is named to one who cannot see it.
                    "app_delivery": app_delivery.view(c, config),
                    "app_delivery_app": app_delivery.HARNESS_APP.get(resolve_harness(config, config.get("runtime"))),
                    "cloud_machine": None
                    if cloud_rows is None or location.get("agent")
                    else cloud_machines.shown(
                        slug, cloud_rows.get(slug), bool(location.get("machine")), cloud_rows.get(cloud_machines.SHARED)
                    ),
                }
            )
        return rows

    @app.get("/api/me")
    def me(request: Request):
        who = request.state.identity
        person_or_view(who)
        with store.read() as c:
            r = roster(c)
            person = P.person(H.actor_id(who.actor), r) or {}
            # Sign-in needs chat choices, not every bot's routines, machine and setup state.
            access = auth.bot_accesses(c, who)
            bots = [
                row["id"]
                for row in H.bots(c)
                if row["state"] != "archived"
                and row["id"] not in (store.settings.assistant_bot, STRATEGY_BOT)
                and access.get(row["id"], auth.FULL)["see"]
                and access.get(row["id"], auth.FULL)["write"]
            ]
            from .credentials import administrator, can_open
            from .onboarding import config_view
            from .mail import can_access

            admins = store.settings.credential_admins
            role = auth.company_role(who)
            domains_ = Access.company_domains(c, store.settings, auth.owner_email)
            return {
                **person,
                "id": H.actor_id(who.actor),
                "email": who.email or person.get("email"),
                "company_role": role,
                "can_create_bots": Access.can_create_bots(person, role),
                "can_add_people": Access.can_add_people(person, role, domains_),
                "company_domains": domains_,
                "member_bot_limit": Access.load_access(c, store.settings)["member_bot_limit"],
                "role": "owner" if who.role == "owner" else "viewer",
                "local": False,
                "cloud": True,
                "registered": True,
                "chat_bots": bots,
                "developer": P.is_developer(H.actor_id(who.actor), r),
                "bot_admin": auth.bot_admin(who),
                "owner_id": auth.owner_id(c),
                "config": config_view(c, store.settings, who),
                "credential_admin": administrator(c, who, admins),
                # The SQL page: the owner's, and the Admins' unless the owner's rule says otherwise.
                "can_see_sql": who.role == "owner" or (auth.bot_admin(who) and team_rules.load(c)["admin_sql"]),
                "can_create_tokens": personal_tokens.can_create(c, auth, who),
                "has_tokens": personal_tokens.has_live(c, who),
                "proxy_session": who.via_proxy,
                "sign_in_name": auth.sign_in_name(request.headers) if who.via_proxy else "",
                "credential_access": bool(can_open(c, who, admins)),
                "mail_access": can_access(c, who),
                # may move any task on the board
                "mover": who.role == "owner" or H.can_move(c, who.actor),
            } | (
                {key: False for key in VIEW_OFF} | {"read_only_view": True, "name": H.actor_name(c, who.actor)}
                if who.view
                else {}
            )

    @app.get("/api/employees")
    def employees(request: Request):
        person_or_view(request.state.identity)
        # One snapshot from the start, so read_cache serves the roster, configs and access rows once.
        with store.read_transaction() as c:
            from . import teammate_access

            # The people and groups this caller may not see are not named on a bot either.
            return teammate_access.redact_bots(
                c, request.state.identity, employee_rows(c, request.state.identity), auth
            )

    @app.get("/api/people")
    def people(request: Request):
        who = request.state.identity
        person_or_view(who)
        with store.read() as c:
            r, configs = roster(c), entries(c)
            from . import people_photos

            archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
            bare = True  # a bot's bare person id in reports_to is that person (backend/people.py)
            view = P.org_view(r, configs, archived, bare_people=bare)
            # The Strategy Owner switch hangs the Heads' groups under it on the chart (backend/strategy_bot.py).
            from . import strategy_bot

            # this person hid it from their chart: nothing moves
            hide_strategy = strategy_bot.hidden_for(c, who)
            chart = strategy_bot.chart_entries(c, store.settings, r, configs, archived, hidden=hide_strategy)
            view["org_groups"] = strategy_bot.chart_groups(
                r, configs, chart, view["org_groups"], archived, hidden=hide_strategy
            )
            access = auth.bot_accesses(c, who)
            from .report_order import positions

            places = positions(c)
            from . import lifecycle

            seats = lifecycle.chart_seats(c, r, chart, archived, lambda slug: access.get(slug, auth.FULL)["see"])
            out = {
                **r,
                "by_team": P.by_team(r),
                "org_groups": view["org_groups"],
                "seats": seats,
                "people": [
                    {
                        **p,
                        "org_parent": P.org_parent("person", p["id"], r, configs, archived, bare),
                        "report_position": places.get("human:" + p["id"]),
                        **(
                            {"photo_url": "/api/humans/" + p["id"] + "/photo"}
                            if people_photos.may_have(store.settings, p.get("email"), p.get("photo"))
                            else {}
                        ),
                        "bots": [
                            b
                            for b in P.bots_of(p["id"], r, configs)
                            if access.get(b, auth.FULL)["see"] and b not in archived
                        ],
                    }
                    for p in r["people"]
                ],
            }
            # The people and groups this caller may not see are not on the roster, the chart or the @mention picker.
            from . import teammate_access

            # The caller's own access to each, from the teammate rules in one pass, as a bot's row has it: what a page
            # offers (edit a profile, add to a thread), which the server checks again.
            mine = teammate_access.levels_many(
                c,
                who,
                ["human:" + p["id"] for p in out["people"]] + ["group:" + g["id"] for g in out["org_groups"]],
                auth=auth,
            )
            out["people"] = [{**p, "my_access": mine["human:" + p["id"]]} for p in out["people"]]
            out["org_groups"] = [{**g, "my_access": mine["group:" + g["id"]]} for g in out["org_groups"]]
            people_hidden, groups_hidden = teammate_access.unseen(c, who, auth)
            if people_hidden or groups_hidden:
                out = teammate_access.redact_chart(out, r, people_hidden, groups_hidden)
                out["by_team"] = {
                    gid: [pid for pid in ids if pid not in people_hidden]
                    for gid, ids in out["by_team"].items()
                    if gid not in groups_hidden
                }
                out["developers"] = [pid for pid in out.get("developers") or [] if pid not in people_hidden]
            return out

    @app.get("/api/people/{pid}/photo")
    def person_photo(request: Request, pid: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            r = roster(c)
            row = P.person(pid, r)
            from . import teammate_access

            if not row or row.get("hidden") or not teammate_access.can(c, who, "see", "human:" + pid, auth):
                raise Problem("not_found", "Person not found", 404)
            from . import people_photos

            fetched = people_photos.load(store.settings, row.get("email"), subject=store.settings.owner_email)
            if fetched:
                data, mime = fetched
                # A Workspace photo keeps for a week (the server's copy does too).
                return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=604800"})
            kept = people_photos.remote_photo(store.settings, row.get("photo")) if row.get("photo") else None
            if kept:
                return Response(
                    content=kept[0], media_type=kept[1], headers={"Cache-Control": "private, max-age=604800"}
                )
            if row.get("photo"):
                return RedirectResponse(
                    row["photo"], status_code=302, headers={"Cache-Control": "private, max-age=86400"}
                )
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
        with store.read_transaction() as c:
            machines, fleet = [], runner_versions.load(c)
            _, assigned = providers.runtimes_needed(c, store.settings)
            default = {providers.default_runtime(providers.load(c, store.settings))} - {""}
            run_on = providers.runtimes_run_on(c, store.settings)
            from . import subscriptions, inbox_isolation
            from .computer_keys import serving, shared_listing

            subscription_context = subscriptions.context(c, store.settings)
            # One pass for every computer's bot_connections (not assignment+config+identity per bot per runner).
            connections_map = subscriptions.connections_by_runner(c, store.settings, subscription_context)
            # One pass for every computer's subscription profiles (not per-runner SELECTs + metadata).
            profiles_map = subscriptions.profiles_by_runner(c)
            kept_for_inbox = inbox_isolation.inbox_only(c, roster(c))
            shared_keys = serving(c)
            # A company key's name and preview are a credential administrator's to read, as in Credentials; a
            # computer's operator reads its own key's.
            from .credentials import administrator

            key_admin = administrator(c, who, store.settings.credential_admins)
            access = auth.bot_accesses(c, who)
            # The bots a member may not even see (an admin sees them all): left off another person's computer.
            unseen = set() if auth.bot_admin(who) else {bot for bot, a in access.items() if not a["see"]}
            from .harness_actions import choices_by_runner, recent_by_runner

            # One pass each for every computer's harness choices and owner action history (not once per runner).
            harness_choice = choices_by_runner(c)
            harness_recent = recent_by_runner(c) if who.role == "owner" else {}
            # One pass for every computer's assignments (not two SELECTs per runner).
            placed, active_placed = assignments_by_runner(c)
            from .model_login import cleared_by_runner, key_cleared_by_runner

            # One pass each for signed-out logins and cleared keys (not two SELECTs per computer).
            signed_out, key_cleared = cleared_by_runner(c), key_cleared_by_runner(c)
            from .repositories import computer_repositories

            # One pass for every computer's repository status (not one registry_metadata lookup per runner).
            repo_status = computer_repositories(c)
            from .computer_keys import listing_by_runner

            # One pass for every computer's own keys (not one SELECT per runner).
            keys_by_runner = listing_by_runner(c)
            from .subscription_identity import display_names

            # One pass for every renamed subscription (not one registry_metadata lookup per shared login).
            login_names = display_names(c)
            from . import cloud_machines

            # {runner: {bot, shared, size, state}} for computers that are a cloud machine: a bot's own (`bot`), or the
            # company's shared one (`shared`, bot None).
            cloud_of = {
                r["runner_id"]: {
                    "bot": None if r["bot"] == cloud_machines.SHARED else r["bot"],
                    "shared": r["bot"] == cloud_machines.SHARED,
                    "size": r["machine_size"] or r["size"],
                    "state": r["state"],
                }
                for r in cloud_machines.visible_rows(c).values()
                if r["runner_id"]
            }
            from . import browser_exit

            # One pass each for every computer's internet sharing and today's bytes through it.
            exit_policies, exit_used = browser_exit.policies(c), browser_exit.used_today_all(c)
            runners = list(
                c.execute(
                    "SELECT id,label,owner_id,last_seen_at,revoked_at,platform,version,capacity,readiness,"
                    "accepts_member_bots,turns_at_once,screen,performance,capabilities,"
                    "restart_since,"
                    "readiness_at FROM runners_with_state ORDER BY created_at"
                )
            )
            from . import usage_limits
            from .start_places import describe, summaries, turn_limit

            # One company Default for every computer's queue classify: without it each summary rebuilds
            # pools/groups/spend for every queued bot (same waste Health's snapshot already dropped).
            limits = usage_limits.company(c)
            # One pass for every computer's turn counts (not one start_places.summary per runner).
            turn_counts = summaries(c, [row["id"] for row in runners], store.settings, limits)
            for row in runners:
                # Every person sees every working computer; only an admin or its operator may change it, and only
                # they read its signed-in accounts, own login's usage and key previews (#610, as in /v2/computers).
                # Another member gets the bots they may see on it.
                manage = auth.bot_admin(who) or who.actor == "human:" + row["owner_id"]
                if not manage and row["revoked_at"]:
                    continue
                hidden = set() if manage else unseen
                bots = [bot for bot in placed.get(row["id"]) or [] if bot not in hidden]
                value = dict(row)
                value["turn_counts"] = turn_counts[row["id"]]
                value["turn_summary"] = describe(value["turn_counts"])
                value["release"] = runner_versions.view(fleet.get(row["id"])).get("release") or ""
                value["accepts_member_bots"] = bool(row["accepts_member_bots"])
                # What the computer may run at once now: its setting, or its capacity (start_places.turn_limit).
                value["turn_limit"] = turn_limit(row)
                # Since when it says it is restarting after a person pressed Restart; null when it is not.
                value["restarting"] = value.pop("restart_since")
                from .screen import listing as screen_listing
                from .performance import listing as performance_listing

                value.update(screen_listing(value))
                value.update(performance_listing(value))
                value.pop("capabilities")
                value["readiness"] = readiness_document(value.pop("readiness"))
                value["repositories"] = repo_status.get(row["id"], "unknown")
                assigned_here = active_placed.get(row["id"]) or set()
                value["readiness"]["bots"] = {
                    bot: report
                    for bot, report in value["readiness"].get("bots", {}).items()
                    if bot in assigned_here and bot not in hidden
                }
                for report in value["readiness"].get("bots", {}).values():
                    report.pop("tools", None)
                # A computer installs only what its bots run on, so only those are its business; with no bot yet,
                # the company's one default, which it installs for its first sign-in (config_view default_runtime).
                # `bot_runtimes` adds the bots' fallbacks: what it installs for them, so their on/off stays locked.
                # Each named subscription it still reports from before one login per computer (unused, for one
                # release), with its sign-in and usage windows; its account only for whoever may change the computer
                # (`may_manage`).
                # `bot_connections`: the connection each of its bots actually runs on. `signed_out`:
                # {runtime: [profile, ...]} a person signed out here ('' is its own login); they stay signed out.
                # `key_cleared`: [runtime, ...] of those, the computer's own login whose key a person cleared.
                # Copy per computer so stripping accounts for non-operators does not mutate the shared batch map.
                profiles = [
                    {
                        **p,
                        "runtimes": {
                            k: dict(v) if isinstance(v, dict) else v for k, v in (p.get("runtimes") or {}).items()
                        },
                    }
                    for p in (profiles_map.get(row["id"]) or [])
                ]
                # Copy per computer so stripping previews for non-operators does not mutate the shared batch map.
                keys = {runtime: dict(info) for runtime, info in (keys_by_runner.get(row["id"]) or {}).items()}
                # `shared_keys`: {runtime: {env, preview}} of the shared key it runs on; `shared_with`: that key's
                # name, whether it is the "Every computer" one, and every computer it serves.
                served = shared_keys.get(row["id"]) or {}
                shared = shared_listing(c, row)
                shared_with = {
                    runtime: {
                        "id": key["id"],
                        "name": key["name"],
                        "every": key["every"],
                        "computers": [dict(m) for m in key["computers"]],
                    }
                    for runtime, key in served.items()
                }
                if not manage:
                    for profile in profiles:
                        subscriptions.without_accounts(profile["runtimes"])
                    subscriptions.without_accounts(value["readiness"].get("runtimes") or {}, windows=True)
                    for key in keys.values():
                        key.pop("preview", None)
                if not key_admin:
                    for key in shared.values():
                        key.pop("preview", None)
                    for key in shared_with.values():
                        key.pop("name", None)
                        key.pop("id", None)
                machines.append(
                    {
                        **value,
                        "bots": bots,
                        "needed_runtimes": sorted(assigned.get(row["id"]) or default),
                        "may_manage": manage,
                        "profiles": profiles,
                        "bot_connections": [
                            link for link in (connections_map.get(row["id"]) or []) if link["bot"] not in hidden
                        ],
                        "signed_out": signed_out.get(row["id"]) or {},
                        "key_cleared": key_cleared.get(row["id"]) or [],
                        "inbox_only": row["id"] in kept_for_inbox,
                        # Its internet shared with bots (backend/browser_exit.py): the owner alone changes it.
                        "browser_exit": browser_exit.listing(
                            exit_policies.get(row["id"]),
                            manage,
                            browser_exit.is_owner(who, row),
                            hidden,
                            exit_used.get(row["id"], 0),
                        ),
                        # {runtime: {env, preview}}: its own keys stored from here, and the company's shared
                        # ones, for the rows a key signs in (backend/computer_keys.py). Never a value, and a
                        # preview only for whoever may change the computer.
                        "keys": keys,
                        "shared_keys": shared,
                        "shared_with": shared_with,
                        # The sign-ins several of its bots share, which they take turns on (counting only
                        # the bots this reader may see).
                        "shared_logins": shared_logins(
                            c, row["id"], value["readiness"], assigned_here - hidden, access, names=login_names
                        ),
                        "bot_runtimes": sorted(run_on.get(row["id"], ())),
                        "update": runner_versions.view(fleet.get(row["id"])),
                        "harness_actions": harness_recent.get(row["id"]) or [],
                        "harness_choices": harness_choice.get(row["id"]) or {},
                        "cloud_machine": cloud_of.get(row["id"]),
                    }
                )
            health = [
                {**dict(r), "name": SERVICE_NAMES.get(r["service"], r["service"])}
                for r in c.execute("SELECT service,last_succeeded_at,last_error FROM service_health")
            ]
            from .agents import listing as agent_listing

            # `machines` is the older name of `computers`, kept for older clients.
            return {
                "cloud": True,
                "computers": machines,
                "machines": machines,
                # Cloud machines not enrolled yet (being made, starting, failed): listed before they are computers.
                "cloud_coming": [
                    row
                    for row in (cloud_machines.coming_rows(c) if cloud_machines.offered(c, store.settings) else [])
                    if row["bot"] not in unseen
                ],
                "runnable": runnable_choices(c, who, auth, store.settings),
                "agents": agent_listing(c, who, auth),
                "services": health,
                "issues": operation_issues(c, who, auth),
                "server_time": H.now(),
                "scheduler_enabled": store.settings.scheduler_enabled,
            }

    @app.get("/api/status")
    def status(request: Request):
        who = request.state.identity
        person_or_view(who)
        # One snapshot from the start, so the roster and access rows are read once (read_cache), not again
        # after the first privacy check opens a transaction.
        with store.read_transaction() as c:
            # The loop is alive by its last beat or pass, whichever is fresher, within one interval and a minute
            # (backend/heartbeat.py `liveness`): a 60 s beat does not read as stopped between beats.
            from . import heartbeat

            tick, age, alive = heartbeat.liveness(c)
            active, queued = [], []
            # Canceled and uncertain jobs are not "up next": uncertain work already
            # surfaces as a health issue, canceled work is history.
            readable = auth.bot_accesses(c, who)
            jobs = [
                row
                for row in c.execute(
                    "SELECT j.*,a.started_at,t.title AS task_title,m.sender_id FROM jobs j "
                    "LEFT JOIN attempts a ON a.id=j.attempt_id JOIN messages m ON m.id=j.message_id "
                    f"JOIN conversations cv ON cv.id=m.conversation_id LEFT JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
                    "WHERE j.state IN ('queued','leased','running','input') ORDER BY j.created_at"
                )
                if readable.get(row["bot_id"], auth.FULL)["read"]
            ]
            # Every job's message and run are read and checked together, not a few queries per job. A deleted
            # message is not readable, as H.message (which leaves it out) made it.
            shown = {
                m["id"]
                for m in privacy.readable_messages(c, privacy.actor(who), [{"id": row["message_id"]} for row in jobs])
            }
            privacy.prime_attempts(c, [row["attempt_id"] for row in jobs if row["message_id"] in shown])
            for row in jobs:
                if row["message_id"] not in shown:
                    continue
                if row["attempt_id"] and not privacy.attempt_readable(c, privacy.actor(who), row["attempt_id"]):
                    continue
                # Never the message text: a shared bot's private chats belong to their people.
                title = row["task_title"] or "Message from " + H.actor_id(row["sender_id"])
                item = {
                    "employee": row["bot_id"],
                    "bot_id": row["bot_id"],
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "state": row["state"],
                    "created_at": row["created_at"],
                    "title": title if len(title) <= 80 else title[:79].rstrip() + "\u2026",
                }
                (queued if row["state"] == "queued" else active).append(item)
            return {
                "cloud": True,
                "last_tick": tick,
                "active": active,
                "queued": queued,
                "recent_runs": [],
                "keeper_alive": alive,
                "dispatcher_alive": False,
                "tick_age_s": age,
                "server_time": H.now(),
                "health_issues": operation_issues(c, who, auth),
                # A Tico Cloud company's access, for the banner and Pay to continue (backend/cloud_access.py).
                "cloud_access": cloud_access.view(cloud_access.current(store)),
            }

    @app.get("/api/runs")
    def runs(request: Request):
        human_only(request.state.identity)
        with store.read() as c:
            rows = [turn for turn, _ in visible_turns(c, request.state.identity)]
            made = ran_on(c, [turn["id"] for turn in rows])
            return [run_view(turn, c, made[turn["id"]]) for turn in rows]

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(request: Request, run_id: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            turn = H.turn(c, run_id)
            if not turn:
                turn = next((r for r in failed_attempts(c, who, auth) if r["id"] == run_id), None)
            if not turn:
                raise Problem("not_found", "Run not found", 404)
            rows = visible_turns(c, who, turn["bot"])
            message = next((message for allowed, message in rows if allowed["id"] == run_id), None)
            if not message:
                raise Problem("not_found", "Run not found or unavailable to this account", 404)
            attempt = c.execute("SELECT * FROM attempts WHERE id=?", (run_id,)).fetchone()
            events = c.execute(
                "SELECT seq,kind,payload,created_at FROM attempt_events WHERE attempt_id=? ORDER BY seq", (run_id,)
            ).fetchall()
            # A live run of a bot org rules apply to keeps its reply out until it is checked and delivered.
            from .org_rule_check import hides_run

            if hides_run(c, attempt):
                events = [e for e in events if e["kind"] not in ("delta", "message")]
            lines = [
                store.settings.app_name + " cloud run " + run_id,
                "Bot: " + turn["bot"],
                "Started: " + str(turn["started_at"]),
                "Finished: " + str(turn["finished_at"]),
                "Outcome: " + str(turn["exit"]),
            ]
            result = json.loads(attempt["result"]) if attempt and attempt["result"] else {}
            if result.get("fallback"):
                lines.append(
                    "Fallback: "
                    + str(result["fallback"])
                    + " (primary harness unavailable; the turn ran on the fallback)"
                )
            if result.get("limited"):
                from . import login_holds

                lines.append("Limit: " + login_holds.label(login_holds.recorded(c, run_id, result, "")))
            lines += ["", "Request:", message["body"]]
            if events:
                lines.extend(["", "Persisted runtime events:"])
                lines.extend(encode(dict(row)) for row in events)
            if attempt and attempt["final_text"]:
                lines.extend(["", "Final reply:", attempt["final_text"]])
            return "\n".join(lines) + "\n"

    @app.get("/api/changelog")
    @app.get("/api/v2/changelog")
    def changelog(
        request: Request,
        kind: Literal["product", "activity", "all"] = "all",
        unread: bool = False,
        q: str = "",
        limit: int = Query(250, ge=1, le=250),
        offset: int = Query(0, ge=0),
    ):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            read = CL.read_ids(c, who.actor)
            products = [{**row, "unread": row["id"] not in read} for row in CL.products(c, store.settings)]
            entries = []
            # Newest readable bot-owned done tasks with a long enough note: what the owner said when it said done (its
            # standing answer to the stage's "owner says done"), else the task's last note. Filter with task_sql in
            # one pass (not auth.task per candidate); 120 is the activity cap beside product updates.
            said = (
                "COALESCE((SELECT a.notes FROM answers a JOIN visits v ON v.id=a.visit_id "
                "JOIN requirements r ON r.id=a.requirement_id WHERE v.task_id=tasks.id AND r.kind='owner_done' "
                "AND a.answer='done' AND a.undone_at IS NULL AND length(a.notes)>=20 "
                "ORDER BY a.created_at DESC LIMIT 1), note)"
            )
            activity = (
                [
                    dict(r)
                    for r in c.execute(
                        f"SELECT id,title,owner_id,{said} AS note,done_at,updated_at FROM tasks WHERE status='done' "
                        f"AND substr(owner_id,1,4)='bot:' AND length({said})>=20 AND ({auth.task_sql(c, who)}) "
                        "ORDER BY COALESCE(NULLIF(done_at,''),updated_at) DESC LIMIT 120"
                    )
                ]
                if kind != "product" and not unread
                else []
            )
            for task in activity:
                if len((task.get("note") or "").strip()) < 20:
                    continue
                note = re.sub(r"\s+", " ", task["note"]).strip()
                entries.append(
                    {
                        "id": "activity-" + task["id"],
                        "title": task["title"],
                        "bullets": [note[:280]],
                        "area": "Agent activity",
                        "kind": "activity",
                        "shipped_at": task.get("done_at") or task["updated_at"],
                        "task_id": task["id"],
                    }
                )
            entries.sort(key=lambda row: row["shipped_at"] or "", reverse=True)
            # Busy bot activity cannot push product updates out of the product view.
            entries = products + entries
            entries.sort(key=lambda row: row["shipped_at"] or "", reverse=True)
            if kind != "all":
                entries = [row for row in entries if row["kind"] == kind]
            if unread:
                entries = [row for row in entries if row.get("unread")]
            if q.strip():
                words = q.lower().split()
                entries = [
                    row
                    for row in entries
                    if all(word in (row["title"] + " " + " ".join(row["bullets"])).lower() for word in words)
                ]
            next_offset = offset + limit if offset + limit < len(entries) else None
            return {
                "entries": entries[offset : offset + limit],
                "next_offset": next_offset,
                "unread_count": sum(row["unread"] for row in products),
                "current_version": releases.version(),
                "drafts": [],
                "can_review": False,
                "can_add": who.role == "owner",
            }

    @app.post("/api/changelog/read")
    @app.post("/api/v2/changelog/read")
    def read_changelog(request: Request, body: M.ChangelogRead):
        who = request.state.identity
        human_only(who)
        return mutate(request, body, lambda c: CL.mark_read(c, store.settings, who.actor, body.ids))

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
            extra = CL.stored_products(c)
            extra.insert(
                0,
                {
                    "id": "product-" + H.now().replace(":", "").replace("-", ""),
                    "title": body.title.strip(),
                    "bullets": bullets,
                    "shipped_at": H.now(),
                },
            )
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('product_updates',?)", (encode(extra[:80]),))
            return {"ok": True}

        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/instructions")
    def bot_instructions(request: Request, bot: str):
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            row = c.execute("SELECT content,updated_at FROM bot_agent_instructions WHERE bot=?", (bot,)).fetchone()
            if not row:
                row = c.execute("SELECT content,updated_at FROM mail_agent_instructions WHERE bot=?", (bot,)).fetchone()
            return {
                "bot": bot,
                "content": row["content"] if row else "",
                "published": bool(row and row["content"].strip()),
                "updated_at": row["updated_at"] if row else None,
                "source": "Computer snapshot" if row else "Not published yet",
            }

    @app.get("/api/employees/{bot}/files")
    def bot_files(request: Request, bot: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
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
            return {
                "AGENT.md": instructions,
                "state.md": state + "\n",
                "memory/learnings.md": memory.get("memory/learnings.md", ""),
                "memory/decisions.md": memory.get("memory/decisions.md", ""),
                "bot.yaml": yaml.safe_dump(safe, sort_keys=False, allow_unicode=True),
                "employee.yaml": yaml.safe_dump(safe, sort_keys=False, allow_unicode=True),
                "playbooks": {},
                "source": "cloud registry and status snapshot",
            }

    @app.get("/api/employees/{bot}/session")
    def bot_session(request: Request, bot: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            rows = list(reversed(visible_turns(c, who, bot, limit=100)))
            turns = []
            for turn, message in rows:
                turns.append({"role": "user", "text": message["body"], "ts": turn["started_at"]})
                attempt = c.execute("SELECT final_text FROM attempts WHERE id=?", (turn["id"],)).fetchone()
                if attempt and attempt[0]:
                    turns.append(
                        {"role": "assistant", "text": attempt[0], "ts": turn["finished_at"] or turn["started_at"]}
                    )
            config = c.execute(BS.ONE_SQL, (bot,)).fetchone()
            cfg = json.loads(config[0]) if config else {}
            runtime, model = providers.bot_choice(c, auth.settings, cfg)
            runtime = runtime or "local"
            session = (
                c.execute(
                    "SELECT * FROM bot_sessions WHERE bot=? AND runtime=? AND model=?", (bot, str(runtime), model)
                ).fetchone()
                if runtime
                else None
            )
            if session and any(
                not privacy.attempt_readable(c, privacy.actor(who), r[0])
                for r in c.execute(
                    "SELECT id FROM attempts WHERE bot_id=? AND thread_id=?", (bot, session["thread_id"])
                )
            ):
                session = None
            return {
                "cloud": True,
                "current": True,
                "runtime": runtime,
                "model": model,
                "session_id": (
                    session["thread_id"] if session else (rows[-1][0].get("thread_id") if rows else "cloud")
                ),
                "provider_session": dict(session) if session else None,
                "first_ts": turns[0]["ts"] if turns else None,
                "last_ts": turns[-1]["ts"] if turns else None,
                "turn_count": len(turns),
                "shown": len(turns),
                "skipped": 0,
                "path": "canonical cloud history",
                "note": (
                    "Provider session files stay on the assigned Mac; the hub keeps the "
                    "conversation, which the bot reads with `tico conversation show`."
                ),
                "runs": [{"run": turn["id"], "issue": None} for turn, _ in rows],
                "turns": turns,
            }

    @app.get("/api/v2/me/recent")
    def my_recent(request: Request, days: int = 7, limit: int = 10):
        """The bots I have been working with lately, most recent first, each with
        its live status, the last thing I said and the last thing it said, the conversation to read
        on (`tico_conversation_show`), our open tasks and what it needs from me. `tico_bot_recent` over MCP, so an
        agent of mine (a Grok bot) can carry on where I left off."""
        who = request.state.identity
        human_only(who)
        days, limit = max(1, min(int(days), 90)), max(1, min(int(limit), 30))
        since = H.shift(H.now(), days=-days)
        with store.read() as c:
            return {
                "actor": who.actor,
                "since": since,
                "bots": recent_bots(c, auth, who, since, limit, needs_items(c, auth, who, task_view)),
            }

    @app.get("/api/v2/needs-you")
    async def needs(
        request: Request,
        count: bool = False,
        ids: str | None = None,
        version: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ):
        """Needs you (needs_items): every requirement with the person's name, every task whose work is theirs, every
        Paused cause they can fix and every @ of them by a person until they reply. `count` is the length of the list,
        the one number on the Tasks badge. The list's `version` changes whenever an item or its place does:
        `version=<the one held>` answers `unchanged` with no items when it still holds, and `ids=a,b` sends only those
        items, with each one's place in the whole list (`positions`), to mend a held copy after a live `needs` event.
        `limit` (at most NEEDS_PAGE_MAX) sends a page, with `next` to pass as `cursor` for the one after it and `bots`,
        each bot's count over the whole list. A cursor from an older version starts again from the top (`restart`)."""
        who = request.state.identity
        person_or_view(who)
        return await needs_lane.run(request.app, needs_answer, who, count, ids, version, limit, cursor)

    def needs_answer(who, count, ids, version, limit, cursor):
        with store.read_transaction() as c:
            if count:
                items = needs_items(c, auth, who, count_only=True)
                return {"actor": who.actor, "count": len(items)}
            items = needs_items(c, auth, who)
            current = needs_marks(items)[1]
            if version and version == current and not cursor:
                return {"actor": who.actor, "count": len(items), "version": current, "unchanged": True}
            if ids is not None:
                wanted = {part.strip() for part in ids.split(",") if part.strip()}
                if len(wanted) > NEEDS_IDS_MAX:
                    raise Problem("validation", f"At most {NEEDS_IDS_MAX} ids", 422)
                positions = {str(x["id"]): n for n, x in enumerate(items) if str(x["id"]) in wanted}
                return {
                    "actor": who.actor,
                    "count": len(items),
                    "version": current,
                    "positions": positions,
                    "items": [x for x in items if str(x["id"]) in positions],
                }
            out = {
                "actor": who.actor,
                "count": len(items),
                "by_kind": needs_kinds(items),
                "items": items,
                "version": current,
                "next": None,
            }
            if limit is not None or cursor:
                out["items"], out["next"], restarted = needs_page(items, current, limit, cursor)
                out["bots"] = needs_bots(items, who.actor)
                out["stages"] = needs_stages(items)
                if restarted:
                    out["restart"] = True
            return out

    @app.get("/api/v2/needs-you/summary")
    async def needs_summary_route(request: Request, limit: int = NEEDS_SUMMARY_ITEMS):
        """What the Needs you list holds: the total, per kind and per bot, with each bot's newest `limit` items (at
        most 10). `tico needs-you show` over MCP. A person only: the list is theirs."""
        who = request.state.identity
        human_only(who)
        return await needs_lane.run(request.app, summary_answer, who, max(0, min(int(limit), NEEDS_SUMMARY_ITEMS)))

    def summary_answer(who, limit):
        with store.read_transaction() as c:
            return needs_summary(c, auth, who, task_view, limit)

    @app.get("/api/v2/tico/fleet")
    def tico_fleet(request: Request):
        with store.read() as c:
            return fleet_snapshot(c, auth, request.state.identity, task_view)

    def task_chat(c, who, tid, text=None, expected_recipient=None):
        human_only(who)
        task = auth.task(c, who, tid)
        candidates = [task["owner_id"], task["requester_id"], H.task_origin(c, task)]
        # No assistant fallback since its chat was retired: a task with no bot has nobody to chat to.
        slug = next(
            (
                H.actor_id(p)
                for p in candidates
                if str(p).startswith("bot:") and auth.visible_bot(c, who, H.actor_id(p)) and H.bot(c, H.actor_id(p))
            ),
            None,
        )
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
            if expected_recipient:
                expected_recipient = (H.resolve_bot(c, expected_recipient) or {}).get("id", expected_recipient)
            if expected_recipient and expected_recipient != slug:
                raise Problem("recipient_changed", "Task ownership changed; refresh before sending", 409)
            if not slug:
                raise Problem(
                    "no_bot",
                    "No bot owns or asked for this task; assign it to a bot to discuss it, or comment on the task",
                    409,
                )
            if not allowed:
                auth.require_write(c, who, slug)
                raise Problem("forbidden", "This bot takes requests in its own Assistant room, not here", 403)
            privacy.require_destination(c, who, "bot:" + slug, conv["id"], {"task": tid})
            message = H.say(c, who.actor, "bot:" + slug, text, conversation_id=conv["id"], refs={"task": tid})
            c.execute(
                "INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)",
                (tid, "bot:" + slug, who.actor, message["id"], H.shift(H.now(), hours=24)),
            )
        tagged = [
            m for m in (privacy.page(c, who, conv["id"])["messages"] if conv else []) if H.message_task_id(m) == tid
        ]
        recipient = (
            {
                "id": slug,
                "slug": bot.get("slug") or slug,
                "name": bot.get("display_name") or bot.get("slug") or slug,
                "can_chat": allowed,
            }
            if slug
            else None
        )
        return {"bot": slug, "recipient": recipient, "conversation": conv, "message": message, "messages": tagged}

    @app.get("/api/v2/tasks/{tid}/chat")
    def task_chat_read(request: Request, tid: str):
        with store.read() as c:
            return task_chat(c, request.state.identity, tid)

    @app.post("/api/v2/tasks/{tid}/chat")
    def task_chat_write(request: Request, tid: str, body: M.TaskChat):
        return mutate(
            request, body, lambda c: task_chat(c, request.state.identity, tid, body.text, body.expected_recipient)
        )

    @app.post("/api/employees/{bot}/session/clear")
    def clear_session(request: Request, bot: str, body: M.Empty):
        who = request.state.identity
        human_only(who)

        def work(c):
            bot_id = (H.resolve_bot(c, bot) or {}).get("id", bot)
            require_chat(c, auth, who, bot_id)
            conv = direct_chat(c, who.actor, bot_id)
            if not conv:
                return {"cleared": True}
            if chat_running(c, conv["id"]):
                raise Problem("busy", "Wait for this conversation's current turn to finish", 409)
            clear_chat_session(c, auth.settings, who.actor, bot_id, conv["id"])
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
    def snapshot(request: Request, cid: str, limit: int | None = None, max_bytes: int | None = None):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            snap = conversation_snapshot(
                c, cid, request.state.identity, auth.settings, page_options(limit, max_bytes), auth=auth
            )
            turns.annotate(c, auth, request.state.identity, snap["messages"])
            visuals.for_reader(app, auth.settings, c, request.state.identity, snap["messages"])
            return snap


def runnable_choices(c, who, auth, settings):
    """{bot: {harness: answer}} for the harnesses the model picker offers that the bot's computer cannot run, for the bots
    this person manages (backend/readiness.py `can_run`, via health.runtime_choice). A harness not listed is not refused."""
    from .harnesses import HARNESS_BY_ID
    from .health import _movable, runtime_choice
    from .getting_started import _online_runners

    enabled = providers.load(c, settings)["enabled"]
    harnesses = sorted(
        {
            h
            for model in providers.MODEL_CATALOG
            if not model.get("deprecated")
            and (not enabled or not model.get("provider") or model["provider"] in enabled)
            for h in model.get("harnesses") or ()
            if not (HARNESS_BY_ID.get(h) or {}).get("external")
        }
    )
    computers = _movable(c, who, {r["id"] for r in _online_runners(c)})
    tico = {
        model["id"]
        for model in providers.MODEL_CATALOG
        if model.get("provider") == "tico" and not model.get("deprecated") and (not enabled or "tico" in enabled)
    }
    out = {}
    from .harnesses import resolve_harness
    from . import read_cache
    from .readiness import CONFIG_SQL, PLACED_SQL, RUNNER_SQL

    # `can_run` judges a bot once per harness and per computer it may move to: each bot's computer (one row per
    # computer, shared by its bots) and each bot's config are read once here, not once per check.
    for row in c.execute("SELECT bot,runner_id FROM assignments").fetchall():
        read_cache.prime(c, PLACED_SQL, {(row["bot"],): read_cache.one(c, RUNNER_SQL, (row["runner_id"],))})
    rows = c.execute(
        f"SELECT a.bot,{BS.CONFIG} AS config FROM assignments a JOIN bots b ON b.id=a.bot "
        f"LEFT JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} WHERE b.state<>'archived' ORDER BY a.bot"
    ).fetchall()
    read_cache.prime(c, CONFIG_SQL, {(row["bot"],): row for row in rows})
    for row in rows:
        bot = row["bot"]
        if not auth.bot_manager(c, who, bot):
            continue
        # The bot's own harness too: the picker keeps it shown when its provider is no longer offered.
        own = resolve_harness(H._json(row["config"], {}) or {}, None)
        for harness in sorted(
            set(harnesses) | ({own} if own in HARNESS_BY_ID and not HARNESS_BY_ID[own].get("external") else set())
        ):
            runtime = HARNESS_BY_ID[harness]["runtime"]
            answer = runtime_choice(c, bot, runtime, who, computers, harness=harness)
            if answer.get("can_run") is False:
                out.setdefault(bot, {})[harness] = answer
        # A Tico model on a computer that cannot run Gateway work, under its model id: the picker marks it "not on this
        # computer" while the harness itself (pi on the bot's own key) stays offered.
        if tico:
            answer = runtime_choice(c, bot, "pi", who, computers, harness="pi", model=sorted(tico)[0])
            if answer.get("can_run") is False:
                # `tico`: any pick of the Gateway's lineup, which the picker cannot list one by one.
                out.setdefault(bot, {}).update({model: answer for model in tico} | {"tico": answer})
    return out
