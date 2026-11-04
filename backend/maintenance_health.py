"""Check health: what is wrong with this install right now, found by plain code (no bot, no model).

The Maintenance run's step (backend/maintenance_steps/check_health.py) reads these checks, plus the production invariants
and the Health page's own checks, and files each problem as a finding: one BotOps task per problem, a count on repeats,
and a cleared comment when it passes (docs/health-monitor.md). This module holds the checks themselves, the record of
busy processes a runner reports, and the Tico bug report path: `health_findings` -> `health_reports` dedupe -> a support
ticket to the Tico team, or a support approval a person reads first when the team rule `health_monitor_reports` is off.

Each finding has a key (what and where, on this install: one per computer for a computer's problem) and a signature (what,
without where: the same on every install for the same problem). The signature groups the BotOps task and the ticket.
"""
import calendar
import hashlib
import json
import logging
import re
import time
from datetime import timezone
from types import SimpleNamespace

from fastapi import Request
from pydantic import Field

from . import diagnostics, login_holds, models_preset, providers, runner_versions, team_rules
from . import models as M
from .models import Contract
from .store import H, Problem
from . import bot_settings as BS

log = logging.getLogger("tico.maintenance_health")

BOTOPS = "botops"           # the bot that takes every setup finding (a task per signature), and asks a person for it
RULE = "health_monitor_reports"
SCHEMA = """
CREATE TABLE IF NOT EXISTS health_findings(
 key TEXT PRIMARY KEY, signature TEXT NOT NULL, check_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '', fix TEXT NOT NULL DEFAULT '', first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 present INTEGER NOT NULL DEFAULT 1, occurrences INTEGER NOT NULL DEFAULT 1, last_woke_at TEXT NOT NULL DEFAULT '',
 severity TEXT NOT NULL DEFAULT 'warn', pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS health_reports(
 signature TEXT PRIMARY KEY, ticket_id TEXT, approval_id TEXT, created_at TEXT NOT NULL, reported_occurrences INTEGER NOT NULL DEFAULT 1,
 reported_at TEXT NOT NULL DEFAULT '');
"""
SETUP, BUG = "setup", "tico-bug"
# A finding's severity, lowest first. `bad` where the Health page says bad for the same thing; a rise wakes the bot.
WARN, BAD = "warn", "bad"
SEVERITY = (WARN, BAD)
REPORTS_PER_DAY = 5          # new tickets the monitor files in a day; a repeat on a filed ticket is not counted
SERVER_CPU = 40.0            # the server's average CPU over an hour, in percent, while hardly anyone used it
QUIET_REQUESTS = 600         # requests in that hour below which the server counts as idle
REPEATED = 3                 # a runner error line seen this often in its last 50 is a pattern, not a blip
CHECK_HELD_HOURS = 12        # a check subtask whose every turn this long stopped on its model's usage limit
LOGIN_LIMITS = 2             # turns in a row on a computer's default login that stopped on a usage limit: out of quota
# A server start (a deploy, an update, a restart) ends every runner's event stream: a drop from this long before a start
# to this long after it is the restart, not the stream. The span allows for the old server stopping first and for clocks.
RESTART_BEFORE, RESTART_AFTER = 300, 120
# A drop the runner says was back this soon cost nothing: it polled for those seconds and caught up on reconnect. A proxy
# in between (a tunnel's edge) resets a long-lived stream now and then; only a drop that stays down is the stream's fault.
BRIEF_DROP_S = 30
BACK = re.compile(r"event stream back after (\d+) s", re.I)
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?)Z")
# Error lines a runner reports (runner_support/outage.py RECENT, sent as `recent_errors`) that point at a known problem.
LOG_PATTERNS = (
    ("worktree_fallback", BUG, re.compile(r"runs in the bot's checkout \(.*permission denied", re.I), 1,
     "A task's turn ran in the bot's main checkout because its worktree could not be made (Permission denied)",
     "Tico bug: the runner could not make a task worktree it owns."),
    ("push_stuck", SETUP, re.compile(r"unpushed commits and push failed", re.I), 1,
     "A bot's checkout has commits it cannot push (the branch has diverged)",
     "BotOps: on that computer, fetch the bot's repository, rebase its unpushed commits onto the remote branch "
     "(or save them on a new branch) and push, so the runner can pull again."),
    ("cannot_fetch", SETUP, re.compile(r"cannot fetch \S+ onto this computer", re.I), REPEATED,
     "A computer keeps failing to fetch a bot's repository",
     "BotOps: check the bot's repository exists and the GitHub App or credential that computer uses can read it."),
    # One drop logs "lost" and then, once per runner process, "unavailable" as the reconnect fails: count the drops.
    ("event_stream", BUG, re.compile(r"event stream lost", re.I), REPEATED,
     "A runner keeps losing its event stream to the server and falls back to polling",
     "Tico bug: the event stream should stay up between deploys."),
    ("readiness_403", BUG, re.compile(r"(readiness|heartbeat).{0,120}\b403\b", re.I), REPEATED,
     "A runner's readiness report is refused (403)",
     "Tico bug: the server refuses the readiness report of a computer it lists."),
)
# What each check means in words that hold on every install: a report says this, never a bot's, computer's or person's name.
MEANING = {
    "runner_outdated": "A computer runs an older Tico release than its server, or failed to update.",
    "limited_no_fallback": "A bot's turns stopped on a usage limit and it has no fallback model.",
    "runner_restarting": "A computer has been restarting for over 34 minutes since a person pressed Restart.",
    "default_login_limited": "A computer's default AI login (no subscription) keeps stopping on a usage limit that has "
                             "no reset time or has not reset yet.",
    "stuck_setup": "A bot has been in setup for over an hour while work waits for it.",
    "computer_silent": "A computer has not checked in for 10 minutes or more while work is queued for its bots.",
    "queue_idle": "Work a claim would hand out stayed queued 15 minutes or more (a person's message: 2) on an online "
                  "computer with room and no limit.",
    "worktree_error": "A task worktree failed on its computer.",
    "server_cpu": f"The server averaged {SERVER_CPU:g}% CPU or more for an hour with under {QUIET_REQUESTS} requests.",
    "local_busy": "A process or container on a computer running Tico was busy in two hourly checks in a row while idle.",
    "run_hold": "Tico stopped a bot after five runs in a row failed within a minute of starting.",
    "new_sessions": "A bot started more than 2 new sessions in a day because its old one was lost or would not continue.",
    "turn_budget": "Tico stopped a bot that started more runs in an hour or a day than its budget allows.",
    "reviewer_holds_tasks": "A bot with the Reviewer profile owns open tasks that are not review or QA requests.",
    "responder_has_tasks": "A bot with the Responder profile, which takes no tasks, has open tasks.",
    "doer_idle": "A Doer's runs for a week all did nothing: its profile may not fit what it is asked to do.",
    "org_rules_unchecked": "A fifth or more of today's bot outputs that rules apply to went out without a rule check.",
    **{p[0]: p[4] + "." for p in LOG_PATTERNS},
}
# What a Tico bug report says the fix is: one constant per tico-bug check, never a finding's own text.
BUG_FIX = {
    "queue_idle": "Tico bug: queued work should start within a minute while its computer has room.",
    "server_cpu": "Tico bug: a background loop is working while nobody uses the server.",
    "local_busy": "Tico bug: a Tico process or container should be quiet while no turn runs.",
    **{p[0]: p[5] for p in LOG_PATTERNS if p[1] == BUG},
}
# Names a report may carry for a busy process or container: Tico's own. Anything else (a container named after a
# company or project, another app) goes out as "a process" or "a Tico container".
TICO_PROCESSES = {"python", "python3", "uvicorn", "node", "git", "docker", "caddy", "cloudflared", "tico"}
TICO_SERVICES = {"server", "slack", "caddy", "cloudflared", "updater", "runner"}   # compose.yaml, docker/runner.compose.yaml


def public_process(process):
    """`process:<name>` or `container:<name>` as a report may name it: a known Tico name, never a local one."""
    what, name = process.split(":", 1)
    if what == "process":
        return f"process {name}" if name in TICO_PROCESSES else "a process"
    parts = [p for p in name.lower().split("-") if not p.isdigit()]    # compose: <project>-<service>-<n>
    return f"the Tico {parts[-1]} container" if parts and parts[-1] in TICO_SERVICES else "a Tico container"


def signature(check_id, subject=""):
    """The same on every install for the same problem: what it is, never where."""
    return "hm-" + hashlib.sha256((check_id + "\n" + subject).encode()).hexdigest()[:12]


def _finding(key, check_id, kind, title, detail, fix, subject="", severity=WARN):
    return {"key": key, "signature": signature(check_id, subject), "check_id": check_id, "kind": kind,
            "title": title[:200], "detail": detail[:1500], "fix": fix[:600], "severity": severity}


def _rank(severity):
    return SEVERITY.index(severity) if severity in SEVERITY else 0


def _holding_fix(label):
    """A computer that only waits for its running turns to end updates by itself: nothing to update by hand, and
    nothing to watch. A turn that watches it (or any other computer's update) holds its own computer's update."""
    return (f"Nothing to update by hand: {label} updates by itself once its running turns end. Do not watch it from "
            f"a turn: read its update once, record the wait on your task and end the turn. A turn that only waits on "
            f"something elsewhere can do the same. Stopping a running turn is a person's choice (Runs), never automatic.")


# ---------------------------------------------------------------------------------------------------- the checks
def _runner_checks(c, settings):
    from . import health
    from .store import readiness_document
    out = []
    online = health._online_runners(c)
    computers = health._computers(c, online, settings)
    # Work a computer holds while it waits to update or restart is said here, never as a queue_idle Tico bug.
    held, restarting = {}, {}
    for row in health._waiting(c, {r["id"] for r in online}, settings)[2]:
        if row["reason"] in ("updating", "restarting"):
            (held if row["reason"] == "updating" else restarting).setdefault(row["runner_id"], []).append(row)
    for machine in computers:
        update = machine["update"]
        rows = held.get(machine["id"], [])
        holds = (f" {health.updating_words(rows[0], advise=False)}; it holds {sum(r['queued'] for r in rows)} queued "
                 "job(s) for " + ", ".join(r["name"] for r in rows) + ".") if rows else ""
        if rows and (since := runner_versions.holding_since(c, machine["id"])):
            minutes = max(0, int((H.parse_ts(H.now()) - H.parse_ts(since)).total_seconds() // 60))
            holds += f" It has taken no new work since {since[11:16]} UTC ({minutes} min), waiting to update."
        if machine["online"] and (update["state"] in ("needs_update", "incompatible") or update["error"] or rows):
            out.append(_finding("runner_outdated:" + machine["id"], "runner_outdated", SETUP,
                                f"{machine['label']} runs an older Tico release ({update['release'] or 'unknown'})",
                                f"Computer {machine['label']} reports release {update['release'] or 'unknown'}, state "
                                f"{update['state']}" + (f"; last update error: {update['error']}" if update["error"] else "")
                                + "." + holds,
                                _holding_fix(machine["label"]) if rows and rows[0]["running"]
                                and update["state"] != "incompatible"
                                and not update["error"] else
                                f"BotOps: update the runner on {machine['label']} (Settings > Computers, or "
                                "`scripts/tico install` once on that computer so it updates itself).",
                                # Its bots are paused until it updates: bad, as Health says (runner_versions.health_check).
                                severity=BAD if update["state"] == "incompatible" else WARN))
    cutoff = H.shift(H.now(), minutes=-health.RESTART_STUCK_MINUTES)
    for r in c.execute("SELECT id,label,restart_since FROM runners_with_state WHERE revoked_at IS NULL AND restart_since<? "
                       "ORDER BY label", (cutoff,)).fetchall():
        if r["id"] not in {x["id"] for x in online}:
            continue        # a computer that stopped checking in is computer_silent
        rows = restarting.get(r["id"], [])
        running = c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running')",
                            (r["id"],)).fetchone()[0]
        since = str(r["restart_since"])[:16]
        holds = (f"; it holds {sum(x['queued'] for x in rows)} queued job(s) for " + ", ".join(x["name"] for x in rows)
                 if rows else "")
        out.append(_finding("runner_restarting:" + r["id"], "runner_restarting", SETUP,
                            f"{r['label']} has been restarting since {since}",
                            f"Computer {r['label']} has said since {since} UTC that it is restarting, which takes at most "
                            f"{health.RESTART_STUCK_MINUTES} minutes; {running} turn(s) still run there{holds}.",
                            f"BotOps: look at the turns still running on {r['label']} (Runs); stopping one is a person's "
                            f"choice. If none runs, restart Tico on {r['label']} by hand (`tico restart`)."))
    for x in health.silent_computers(c):
        out.append(_finding("computer_silent:" + x["id"], "computer_silent", SETUP,
                            f"{x['label']} has not checked in for {x['minutes']} minutes",
                            f"Computer {x['label']} was last heard from at {(x['last_seen_at'] or '')[:16]}; "
                            f"{x['queued'] + x['dead']} job(s) wait for {', '.join(x['bots'])}.",
                            f"Ask the person who runs {x['label']} to wake it and check Tico there "
                            "(`tico status`; `tico restart` starts it again), or move its bots to an online computer."))
    starts = None
    for r in c.execute("SELECT id,label,readiness FROM runners_with_state WHERE revoked_at IS NULL").fetchall():
        lines = [str(x) for x in (readiness_document(r["readiness"]).get("recent_errors") or [])]
        for check_id, kind, pattern, least, title, fix in LOG_PATTERNS:
            hits = [line for line in lines if pattern.search(line)]
            if check_id == "event_stream" and hits:
                starts = _server_starts(c) if starts is None else starts
                brief = _brief_drops(lines)
                hits = [line for i, line in enumerate(lines) if pattern.search(line) and i not in brief
                        and not _near_start(line, starts)]
            if len(hits) >= least:
                out.append(_finding(f"{check_id}:{r['id']}", check_id, kind, f"{title} ({r['label']})",
                                    f"{len(hits)} of the last {len(lines)} error lines from {r['label']} match. "
                                    "Latest: " + hits[-1][:280], fix))
    return out


def _login(c, settings, bot, result, configs):
    """(runtime, harness) of the login that ended a finished turn: the fallback's when the turn hopped (its usage names
    only the segments that spent tokens, so a hop refused at once is not in it), the check model's on a check, else
    what its usage or the bot's config names. An empty harness means the runtime's own CLI."""
    from .harnesses import resolve_harness, runtime_of
    if result.get("fallback"):
        return runtime_of(str(result["fallback"])), str(result["fallback"])
    if result.get("check"):
        return str(result["check"]), ""
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    if usage.get("runtime"):
        return str(usage["runtime"]), str(usage.get("harness") or "")
    if bot not in configs:
        row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        configs[bot] = json.loads(row[0] or "{}") if row else {}
    return providers.bot_choice(c, settings, configs[bot])[0], resolve_harness(configs[bot])


def _computer_login(runtime, harness):
    """Whether a turn with no subscription profile ran on the computer's own sign-in. A harness whose key each bot
    holds (`api_keys = "bot"`) signs in with the bot's own Credential instead: one bot's quota is not the computer's."""
    from .harnesses import harness_caps
    return bool(runtime) and harness_caps(harness, runtime).get("api_keys") != "bot"


def out_of_quota_logins(c, settings=None):
    """Each computer's default login (a turn with no subscription profile, on a harness the computer signs in rather
    than each bot's own key) whose latest turns in the last day, on one runtime, stopped on a usage limit LOGIN_LIMITS
    times or more with no turn there completing since, and whose provider gave no reset time or one still ahead. A turn
    that failed for another reason says nothing either way. [{runner_id, runtime, limits, since, resets_at}]"""
    now = H.now()
    seen, configs, out = {}, {}, []
    for r in c.execute("SELECT bot_id,runner_id,state,finished_at,result FROM attempts WHERE finished_at>? "
                       "AND state IN ('completed','failed','interrupted') AND result IS NOT NULL "
                       "AND json_extract(result,'$.profile_used') IS NULL ORDER BY finished_at DESC",
                       (H.shift(now, hours=-24),)):
        result = json.loads(r["result"])
        runtime, harness = _login(c, settings, r["bot_id"], result, configs)
        if not _computer_login(runtime, harness):
            continue
        login = seen.setdefault((r["runner_id"], runtime), {"runner_id": r["runner_id"], "runtime": runtime,
                                                            "limits": 0, "since": "", "resets_at": None, "open": True})
        if not login["open"]:
            continue
        if result.get("limited") and (result.get("limit_source") or {}).get("kind") == login_holds.CAPACITY_KIND:
            continue                                                    # the provider was busy: nothing of quota
        if result.get("limited") and r["state"] != "completed":
            if not login["limits"]:
                login["resets_at"] = result.get("limit_resets_at")    # the latest limit's reset is the one that counts
            login["limits"] += 1
            login["since"] = r["finished_at"]
        elif r["state"] == "completed":
            login["open"] = False                                       # it worked after its last limit
    for login in seen.values():
        reset = H.parse_ts(login["resets_at"])            # a reset Tico cannot read counts as none
        reset = reset.replace(tzinfo=timezone.utc) if reset and not reset.tzinfo else reset
        if login["limits"] >= LOGIN_LIMITS and (reset is None or reset > H.parse_ts(now)):
            out.append({**{k: login[k] for k in ("runner_id", "runtime", "limits", "since")},
                        "resets_at": reset.strftime("%Y-%m-%dT%H:%M:%SZ") if reset else None})
    return out


def _login_checks(c, settings):
    from .store import readiness_document
    runners = {r["id"]: r for r in c.execute("SELECT id,label,readiness FROM runners_with_state WHERE revoked_at IS NULL")}
    labels = {rid: r["label"] for rid, r in runners.items()}
    record = providers.load(c, settings)
    out = []
    for x in out_of_quota_logins(c, settings):
        label = labels.get(x["runner_id"])
        if not label:
            continue                                  # a removed computer runs nothing more
        login = models_preset.LOGIN_NAMES.get(x["runtime"])
        if (login and models_preset.fallback_row(record) and record.get("auto_fallback")
                and models_preset.runs_gateway(readiness_document(runners[x["runner_id"]]["readiness"]))):
            # Under the two Gateway choices the bots there finish on Tico Gateway, last: one plain line.
            out.append(_finding(f"default_login_limited:{x['runner_id']}:{x['runtime']}", "default_login_limited", SETUP,
                                f"{label}'s {login} login is used up",
                                f"{label}'s {login} login is used up. Bots are on Tico Gateway until it resets.",
                                f"Sign in another {login} login on {label} in Settings > Computers, or wait for it "
                                "to reset."))
            continue
        name = providers.RUNTIME_LABELS.get(x["runtime"], x["runtime"])
        reset = (f"; the provider says it resets at {x['resets_at'][:16].replace('T', ' ')} UTC" if x["resets_at"]
                 else "; the provider gave no reset time")
        out.append(_finding(f"default_login_limited:{x['runner_id']}:{x['runtime']}", "default_login_limited", SETUP,
                            f"{label}'s default {name} login is out of quota",
                            f"{label}'s default {name} login is out of quota: {x['limits']} turns on it since "
                            f"{x['since'][:16].replace('T', ' ')} UTC stopped on a usage limit and none has completed "
                            f"since{reset}. Bots on {label} "
                            f"without a subscription that run on {name}, or fall back to it, will fail there.",
                            f"BotOps: on {label}, sign the default {name} login in to an account with quota, or top up "
                            "its plan or billing; or give the bots there a subscription with quota (Settings > AI "
                            "providers > Subscriptions)."))
    return out


def _check_hold_checks(c):
    """A check subtask whose queued turn has stopped on its model's usage limit on every try for CHECK_HELD_HOURS: the
    claim hands it out again after each reset (check_models.hold) and nothing else tells anyone. A turn on it that
    ends any other way, the turn completing, or the task leaving the board clears it. One finding per check task."""
    from . import check_models
    now = H.now()
    out = []
    limited = "(coalesce(json_extract(result,'$.limited'),0) AND state<>'completed')"
    for t in c.execute("SELECT t.id,t.title,t.private,t.owner_id FROM registry_metadata r "
                       "JOIN tasks t ON t.id=substr(r.key,15) WHERE r.key>='check-limited:' AND r.key<'check-limited;' "
                       f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) AND t.owner_id LIKE 'bot:%'",
                       H.ACTIVE_STATUSES).fetchall():
        bot = H.actor_id(t["owner_id"])
        check = check_models.of(c, t["id"], bot)
        if not check:
            continue
        # The limits on its queued jobs since the last turn on any of them that ended another way. One pool, not the
        # oldest job: the claim hands out a person's message before an older notice, and one turn takes several.
        queued = ("SELECT j.id FROM jobs j CROSS JOIN messages m JOIN conversations cv ON cv.id=m.conversation_id "
                  f"WHERE j.bot_id=? AND j.state='queued' AND m.id=j.message_id AND {H.MESSAGE_TASK_SQL}=?")
        since, tries = c.execute(
            f"WITH a AS (SELECT finished_at,{limited} AS hit FROM attempts WHERE job_id IN ({queued}) "
            "AND finished_at IS NOT NULL) SELECT min(finished_at),count(*) FROM a WHERE hit "
            "AND finished_at>coalesce((SELECT max(finished_at) FROM a WHERE NOT hit),'')", (bot, t["id"])).fetchone()
        if not tries or since > H.shift(now, hours=-CHECK_HELD_HOURS):
            continue
        hours = int((H.parse_ts(now) - H.parse_ts(since)).total_seconds() // 3600)
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        model = providers.RUNTIME_LABELS.get(check["runtime"], check["runtime"]) + (
            f" ({check['model']})" if check.get("model") else "")
        title = "a private task" if t["private"] else t["title"]
        out.append(_finding("check_limited:" + t["id"], "check_limited", SETUP,
                            f"{name}'s check on {model} is held by its usage limit",
                            f"{name}'s check on {model} has been held by its usage limit for {hours} h (since "
                            f"{since[:16].replace('T', ' ')} UTC); {title}. Every turn on it since then ({tries}) "
                            "stopped on the limit, and Tico retries it after every reset.",
                            f"BotOps: wait for {model}'s limit to reset; or switch the check model: {name} declines "
                            "this check and files it again on another model (hub task child --check-model); or drop "
                            f"the check: {name} declines it. A declined check's queued turn is let go."))
    return out


def _release_approval_checks(c, settings):
    """A release's publish approval ready longer than the setting (release_approval_wait_minutes) with nobody's decision:
    one finding per approval, so the same wait seen again only counts (scan) and a re-cut release's new ask is its own.
    The person decides it in Needs you; nothing here approves or changes who may."""
    minutes = getattr(settings, "release_approval_wait_minutes", 60)
    if not minutes:
        return []
    at = H.now()
    out = []
    for row in H._rows(c.execute("SELECT * FROM approvals WHERE decision IS NULL AND kind='publish' "
                                 "AND coalesce(ready_since,created_at)<? ORDER BY created_at",
                                 (H.shift(at, minutes=-minutes),))):
        row["payload"] = H._json(row.get("payload"), {}) or {}
        if not H.is_release_approval(row["kind"], row["payload"]):
            continue
        H.approval_wait(c, row, at)
        who = row["decision_owner_name"] or "a person"
        version, commit = str(row["payload"]["version"])[:40], str(row["payload"]["commit"])[:12]
        out.append(_finding("release_approval_wait:" + row["id"], "release_approval_wait", SETUP,
                            f"The release {version} approval has waited {H.wait_words(row['age_s'])} on {who}",
                            f"The publish approval for {version} (commit {commit}) has been ready since "
                            f"{row['ready_since'][:16].replace('T', ' ')} UTC, {H.wait_words(row['age_s'])}, with no "
                            f"decision. It is {who}'s to decide; the release waits until then.",
                            f"{who}: approve or decline it in Needs you (or `hub approval show {row['id']}`). Tico does "
                            "not decide it for anyone."))
    return out


def _brief_drops(lines):
    """Where the "lost" lines are whose next word on the stream, before another drop, is that it was back within
    BRIEF_DROP_S."""
    brief, open_drop = set(), None
    for i, line in enumerate(lines):
        if "event stream lost" in line.lower():
            open_drop = i
        elif open_drop is not None and (m := BACK.search(line)):
            if int(m.group(1)) <= BRIEF_DROP_S:
                brief.add(open_drop)
            open_drop = None
    return brief


def _server_starts(c):
    """When this server started, as epoch seconds: the flight recorder's start events, and this process."""
    starts = [r[0] for r in c.execute("SELECT created_at FROM flight_events WHERE kind='start'")]
    return starts + [calendar.timegm(time.strptime(diagnostics.RING.started, "%Y-%m-%dT%H:%M:%SZ"))]


def _near_start(line, starts):
    """Whether a runner's error line was logged while the server was restarting. A line without a valid time is not."""
    m = STAMP.match(line)
    if not m:
        return False
    stamp = m.group(1)
    try:
        at = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S" if stamp.count(":") == 2 else "%Y-%m-%dT%H:%M"))
    except ValueError:              # a runner's line is its own text: a bad date must not stop the scan
        return False
    return any(start - RESTART_BEFORE <= at <= start + RESTART_AFTER for start in starts)


def _company_fallback(c, settings, declared, company):
    """Whether a bot that names no fallback of its own still has the company's (Settings > Computers, *Fall back
    when a plan runs out*): automatic fallback is on and lists a model on another runtime than the bot's. The runner
    picks from the same rows (runner-rs/src/fallback.rs default_choice); a bot that turned fallback off, or names a
    harness without a model, gets none."""
    own = declared.get("fallback")
    if isinstance(own, dict) and (own.get("off") or own.get("harness")):
        return False
    runtime = providers.bot_choice(c, settings, declared)[0]
    return any(row["runtime"] and row["model"] and row["runtime"] != runtime for row in company)


def _bot_checks(c, settings=None):
    from . import health
    from .harnesses import normalize_fallback
    out = []
    since = H.shift(H.now(), hours=-24)
    limited = {}
    for r in c.execute("SELECT detail FROM events WHERE action='attempt.limited' AND created_at>?", (since,)):
        detail = json.loads(r["detail"] or "{}")
        if (detail.get("source") or {}).get("kind") == login_holds.CAPACITY_KIND:
            continue                    # a busy model for two minutes, not a spent subscription or spend limit
        if detail.get("bot"):
            limited[detail["bot"]] = limited.get(detail["bot"], 0) + 1
    record = providers.load(c, settings)
    company = providers.default_fallbacks(record)
    for bot, n in sorted(limited.items()):
        label = (H.bot(c, bot) or {}).get("slug", bot)
        config = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        declared = json.loads(config["config"] or "{}") if config and config["config"] else {}
        # The Gateway row counts only where the bot's computer can run it (a Mac skips it: runner-rs/src/fallback.rs).
        computer = models_preset.computer_of(c, bot)
        here = models_preset.runnable_fallbacks(record, computer, company)
        if normalize_fallback(declared.get("fallback")) or _company_fallback(c, settings, declared, here):
            continue
        login = models_preset.LOGIN_NAMES.get(providers.bot_choice(c, settings, declared)[0])
        if login and models_preset.fallback_row(record) and record.get("auto_fallback") \
                and not models_preset.runs_gateway(computer):
            out.append(_finding("limited_no_fallback:" + bot, "limited_no_fallback", SETUP,
                                f"{label} stopped: its {login} login is used up",
                                f"{label} stopped: its {login} login is used up, and no computer here can run Tico "
                                f"Gateway work. {n} turn(s) stopped in the last 24 hours.",
                                f"Sign in another {login} login, or add a Docker runner."))
        else:
            out.append(_finding("limited_no_fallback:" + bot, "limited_no_fallback", SETUP,
                                f"{label} stopped on a usage limit and has no fallback model",
                                f"{n} turn(s) of {label} stopped on a subscription or spend limit in the last 24 hours, "
                                "and the bot has no fallback model to finish them on.",
                                f"BotOps: give {label} a fallback model on another provider (Settings > Bots > {label} > "
                                "Fallback), or raise its limit if the team chose one."))
    for slug, name, count, what, oldest in health.unfinished_setup(c):
        out.append(_finding("stuck_setup:" + slug, "stuck_setup", SETUP, f"{name} has been stuck in setup since {oldest[:16]}",
                            f"{name} is still in setup and holds {count} {what}(s), the oldest from {oldest[:16]} UTC.",
                            f"BotOps: finish {name}'s setup with the person it serves, or pause it so its work is not held."))
    online_ids = {r["id"] for r in health._online_runners(c)}
    for row in health.refused_queues(c, settings):
        out.append(_finding('queue_refused:' + row['bot'], 'queue_refused', SETUP,
                            f"{row['name']}'s oldest queued job has been refused for over 12 hours",
                            f"Queued since {row['oldest'][:16]} UTC; refusal reason: {row['reason']}.",
                            'BotOps: inspect the refusal in Health and resolve its execution gate.'))
    for row in health._waiting(c, online_ids, settings)[2]:
        # Only work the claim has no reason to hold (execution.bot_refusal) is a Tico bug; a held bot's row on Health
        # names its reason instead.
        if row["reason"] == "slow":
            out.append(_finding("queue_idle:" + row["bot"], "queue_idle", BUG,
                                f"{row['name']}'s work is queued but no turn starts",
                                f"{row['queued']} job(s) for {row['name']} queued since {str(row['oldest'])[:16]} UTC on "
                                f"{row['computer']}, which is online, not full and not limited. The claim's own checks "
                                "(draining, worktree maintenance, a lapsed lease still running, interrupted work, usage "
                                "limit, readiness, sign-in, subscription profile, check model, lane) hold none of it.",
                                BUG_FIX["queue_idle"]))
    from .worktrees import current_error, supported
    if supported(c):
        for r in c.execute("SELECT t.owner_id,l.state,l.detail FROM task_links l JOIN tasks t ON t.id=l.task_id "
                           "WHERE l.kind='worktree' AND l.state<>'removed'"):
            error = current_error(r["state"], json.loads(r["detail"] or "{}"))
            if error:
                bot = H.actor_id(r["owner_id"])
                label = (H.bot(c, bot) or {}).get("slug", bot)
                digest = hashlib.sha256(error.encode()).hexdigest()[:8]
                out.append(_finding(f"worktree_error:{bot}:{digest}", "worktree_error", SETUP,
                                    f"A task worktree of {label} failed",
                                    f"{label}: {error[:400]}",
                                    "BotOps: save any unique work in that worktree on a branch, then remove its link so "
                                    "the computer can make it again (docs/worktrees.md)."))
    out.extend(_quiet_work_checks(c))
    out.extend(_stop_checks(c))
    out.extend(_session_checks(c))
    out.extend(_profile_checks(c))
    return out


def _profile_checks(c):
    """Profiles that do not fit the work a bot has (backend/profiles.py mismatches): one finding per bot."""
    from . import profiles
    say = {"reviewer_holds_tasks": (lambda name, n: (
               f"{name} is a Reviewer but owns {n} open task{'s' if n != 1 else ''} that {'are' if n != 1 else 'is'} not review or QA requests",
               "A Reviewer takes review and QA requests only and does not pull other open tasks by itself, so "
               "these wait for a person to start them.",
               f"BotOps: give those tasks to a bot that does the work, or change {name}'s profile to Doer.")),
           "responder_has_tasks": (lambda name, n: (
               f"{name} is a Responder but has {n} open task{'s' if n != 1 else ''}",
               "A Responder wakes for people in chat only and never starts a task, so these sit untouched.",
               f"BotOps: give those tasks to a bot that does the work, or change {name}'s profile to Doer.")),
           "doer_idle": (lambda name, n: (
               f"{name} did nothing in all {n} of its runs this week",
               f"Every run {name} had in the last week ended without writing anything to Tico.",
               f"BotOps: ask {name}'s owner what it should be doing; a Responder, Monitor or Lead profile may fit better."))}
    out = []
    for kind, bot, count in profiles.mismatches(c):
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        title, detail, fix = say[kind](name, count)
        out.append(_finding(f"{kind}:{bot}", kind, SETUP, title, detail, fix))
    return out


def _stop_checks(c):
    """A bot Tico stopped for failed runs or for running away (backend/bot_stops.py): one finding per bot until it is
    released. The signature carries the error class or the window, never the bot."""
    from . import claim_holds, run_backoff, turn_budgets
    out = []
    for hold in run_backoff.held(c):
        bot, kind = hold["bot"], hold["error_class"] or run_backoff.OTHER
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        out.append(_finding(f"run_hold:{bot}", "run_hold", SETUP,
                            f"{name} was stopped after {hold['fails']} failed runs in a row",
                            f"{name}'s last {hold['fails']} runs each failed within a minute of starting, with "
                            f"{run_backoff.CLASS_WORDS.get(kind, run_backoff.CLASS_WORDS[run_backoff.OTHER])}. Held "
                            f"since {str(hold['held_at'])[:16]} UTC. Last error: {hold['error'] or 'none reported'}.",
                            f"BotOps: read {name}'s last runs, fix the cause, then release it (hub bot release {row.get('slug', bot)}). "
                            "Tico also tries one run on its own at growing intervals and releases it when one completes. "
                            "Its queued work is kept.", subject=kind, severity=BAD))
    for hold in turn_budgets.held(c):
        bot = hold["bot_id"]
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        out.append(_finding(f"turn_budget:{bot}", "turn_budget", SETUP,
                            f"{name} was stopped: {hold['runs']} runs in the last {hold['window']}",
                            f"{name} started {hold['runs']} runs in the last {hold['window']}, over its budget of "
                            f"{hold['budget']}. Held since {str(hold['held_at'])[:16]} UTC. This looks like a runaway "
                            "loop, not normal work.",
                            f"BotOps: read {name}'s last runs for what keeps waking it, then release it "
                            f"(hub bot release {row.get('slug', bot)}) or raise its budget (hub bot budget {row.get('slug', bot)} --{hold['window']} N).",
                            subject=hold["window"], severity=BAD))
    for hold in claim_holds.held(c):
        bot = hold["bot_id"]
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        out.append(_finding(f"claim_failed:{bot}", "claim_failed", SETUP,
                            f"Tico could not start {hold['jobs']} queued run{'s' if hold['jobs'] != 1 else ''} "
                            f"for {name}",
                            f"{name} has {hold['jobs']} queued run{'s' if hold['jobs'] != 1 else ''} held, "
                            f"queued since {str(hold['since'])[:16]} UTC: an error happened while Tico prepared each "
                            "one to start. "
                            "Each waits, then Tico tries it again on its own; the wait doubles with each failed try, "
                            "up to an hour. The server log has the traceback.",
                            f"BotOps: read the server log for the traceback and fix its cause. To retry the held runs "
                            f"now, release {name} (hub bot release {row.get('slug', bot)}); one that fails again is held with a longer "
                            "wait.",
                            severity=BAD))
    for failure in claim_holds.check_failures(c):
        bot, error = failure["bot"], failure["error"]
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        out.append(_finding(f"claim_check:{bot}", "claim_check", SETUP,
                            f"{name}'s claim check is failing: {error}",
                            f"Tico could not check {name}'s queued runs for a claim: the check raised {error} at "
                            f"{str(failure['failed_at'])[:16]} UTC. The claim passes {name} over, so its runs wait "
                            "while the other bots' runs are unaffected. The server log has the traceback.",
                            f"Fix the cause in the server log. This finding clears when one of {name}'s runs starts.",
                            subject=error, severity=BAD))
    return out


NEW_SESSIONS = 2             # new sessions in a day, for a lost or failed old one, above which a bot is a finding


def _session_checks(c):
    """A bot whose old session keeps being lost (backend/run_triggers.py SESSION_LOST): more than NEW_SESSIONS runs in
    the last 24 h started a new one because the old was not found, would not resume or compact, was interrupted, or was
    too large to send even after compacting. A first run, a person's Clear and a topic change do not count. One grouped
    read of the last day's runs (`attempts.created_at` is indexed)."""
    from .run_triggers import SESSION_LOST
    since = H.shift(H.now(), hours=-24)
    marks = ",".join("?" * len(SESSION_LOST))
    out = []
    for r in c.execute(f"SELECT bot_id AS bot, count(*) AS n, group_concat(DISTINCT session_why) AS why FROM attempts "
                       f"WHERE created_at>=? AND session_kind='new' AND session_why IN ({marks}) "
                       f"GROUP BY bot_id HAVING count(*)>? ORDER BY n DESC, bot", (since, *SESSION_LOST, NEW_SESSIONS)):
        bot = r["bot"]
        row = H.bot(c, bot) or {}
        name = row.get("display_name") or row.get("slug") or bot
        out.append(_finding(f"new_sessions:{bot}", "new_sessions", SETUP,
                            f"{name} keeps losing its session: {r['n']} new ones in a day",
                            f"{name} started {r['n']} new sessions in the last 24 hours because its old one could not be "
                            f"continued ({r['why']}), so each run began without the last one's context.",
                            f"BotOps: read {name}'s last runs for why its session is lost (the computer it runs on may have "
                            "changed, its session files may be cleared, or its runs may be cut short)."))
    return out


def _quiet_work_checks(c):
    """A deferred task handed to a bot's run over two hours ago and not worked since, with no time set to pick it up
    (backend/quiet_work.py stale): one finding per bot and task. It names them; it asks no person anything."""
    from . import quiet_work
    out = []
    for item in quiet_work.stale(c):
        task, bot = item["task"], item["bot"]
        label = (H.bot(c, bot) or {}).get("slug", bot)
        # A private task's title stays out of a finding other people read; its number or short id names it.
        name = quiet_work._task_name(task) if H.task_private(c, task) else f"{quiet_work._task_name(task)} {task['title']}"
        out.append(_finding(f"quiet_unworked:{bot}:{task['id']}", "quiet_unworked", SETUP,
                            f"{label} was handed a deferred task and has not worked it",
                            f"{label} was handed the next-run task {name} at {item['since'][:16]} UTC (run "
                            f"{str(item['run'] or '')[:8]}) and has not worked it since, with no time set to pick it "
                            "up again.",
                            f"{label}: work it, or say when to pick it up (hub task later {task['id']} --after <task> | "
                            "--at <time>). Anyone may press Run now on it to give it a run of its own."))
    return out


def _server_checks(c):
    from . import flight
    from .health import runner_route_latency
    latency = runner_route_latency(c)
    out = ([_finding("runner_route_latency", "runner_route_latency", BUG,
                     "Runner requests are waiting too long", latency["summary"],
                     "Inspect the flight recorder's route wait/work times and write-lock holders; shorten the longest transactions.")]
           if latency else [])
    try:
        seen = flight.view(c, minutes=60, sections=("requests", "process", "sql"))
    except Exception:
        return out                                  # no process samples on this server
    cpu, requests = (seen.get("process") or {}).get("cpu_avg"), (seen.get("requests") or {}).get("n") or 0
    if cpu is None or cpu < SERVER_CPU or requests >= QUIET_REQUESTS:
        return out
    top = [q["sql"][:120] for q in (seen.get("sql") or {}).get("queries", [])[:3]]
    return out + [_finding("server_cpu", "server_cpu", BUG, f"The server used {cpu:g}% CPU for an hour while idle",
                     f"Average CPU {cpu:g}% over the last hour with {requests} requests. Busiest SQL: "
                     + ("; ".join(top) or "none recorded") + ".",
                     BUG_FIX["server_cpu"])]


def _org_rule_checks(c, settings):
    """Rule checks that keep going unchecked (backend/org_rule_check.py): the rules are not being enforced."""
    from .org_rule_check import health_findings
    return [_finding(f["key"], f["check_id"], SETUP, f["title"], f["detail"], f["fix"])
            for f in health_findings(c, settings)]


def findings(c, settings):
    """Everything wrong right now, each once by key."""
    out = {}
    for part in (_runner_checks(c, settings), _login_checks(c, settings), _check_hold_checks(c),
                 _bot_checks(c, settings), _release_approval_checks(c, settings), _server_checks(c),
                 _org_rule_checks(c, settings)):
        for item in part:
            out.setdefault(item["key"], item)
    return list(out.values())




# ---------------------------------------------------------------------------------------------------- the runner's idle load
IDLE_MAX = 50                # busy names one computer's hourly sample lists; the runner keeps the busiest
IDLE_STALE_HOURS = 2         # a busy name not sampled this long clears: its runner stopped sampling


class IdleEntry(Contract):
    """One name busy in two hourly samples in a row on a computer (runner-rs idle sampler): a name and two numbers."""
    name: str = Field(pattern=r"^(process|container):[A-Za-z0-9._-]{1,40}$")
    cpu: float = Field(ge=0, le=100000)
    mb: float = Field(ge=0, le=10000000)


class IdleLoad(Contract):
    """A computer's hourly busy list. An empty list is how the runner says nothing is busy (it clears earlier names)."""
    sampled_at: str = Field(max_length=40)
    busy: list[IdleEntry] = Field(default_factory=list, max_length=IDLE_MAX)


def record_idle(c, runner_id, body):
    """Records one computer's hourly busy list. Each name in it is a local finding for that computer, seen now; a name
    the list no longer has clears. Returns the keys busy now. An old runner never sends this, so nothing is recorded."""
    now, busy = H.now(), {}
    for entry in body.busy:
        what, name = entry.name.split(":", 1)
        key = f"local_busy:{runner_id}:{entry.name}"
        item = _finding(key, "local_busy", BUG, f"{what.capitalize()} {name} is busy while idle",
                        f"{what.capitalize()} {name} used {entry.cpu:.0f}% CPU and {entry.mb:.0f} MB of memory in two hourly "
                        "checks in a row on a computer running Tico.",
                        BUG_FIX["local_busy"], subject=public_process(entry.name))
        busy[key] = item
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen_at,last_seen_at,"
                  "present,occurrences,severity,pending) VALUES(?,?,?,?,?,?,?,?,?,1,1,?,0) ON CONFLICT(key) DO UPDATE SET "
                  "title=excluded.title,detail=excluded.detail,last_seen_at=excluded.last_seen_at,present=1,pending=0",
                  (key, item["signature"], item["check_id"], item["kind"], item["title"], item["detail"], item["fix"],
                   now, now, item["severity"]))
    # This computer's rows only: the keys between "local_busy:<runner>:" and the same prefix with ";" (the next character).
    for row in c.execute("SELECT key FROM health_findings WHERE present=1 AND check_id='local_busy' AND key>=? AND key<?",
                         (f"local_busy:{runner_id}:", f"local_busy:{runner_id};")).fetchall():
        if row["key"] not in busy:
            c.execute("UPDATE health_findings SET present=0, pending=0 WHERE key=?", (row["key"],))
    return sorted(busy)


# ---------------------------------------------------------------------------------------------------- reporting
def _where(c, row):
    """`bot-N on runner-M` for a queue_idle finding: the labels of the diagnostics bundle sent with it
    (diagnostics.bot_label, runner_label), so the Tico team can find the bot and computer in it without their names."""
    if row["check_id"] != "queue_idle":
        return ""
    bot_id = row["key"].split(":", 1)[1]
    bot = diagnostics.bot_label(c, bot_id)
    place = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot_id,)).fetchone()
    computer = diagnostics.runner_label(c, place["runner_id"]) if place else None
    return f"{bot} on {computer}" if bot and computer else bot or ""


def report_message(c, row):
    """The ticket text, written here from the check alone: no finding text (it names bots and computers), task, message
    or person's words go in it."""
    from . import releases
    seen = c.execute("SELECT count(*) n, sum(occurrences) times FROM health_findings WHERE signature=?",
                     (row["signature"],)).fetchone()
    process = public_process(row["key"].split(":", 2)[2]) if row["check_id"] == "local_busy" else ""
    where = _where(c, row)
    return (f"[Health Monitor] {row['check_id']}: {MEANING.get(row['check_id'], row['check_id'])}\n\n"
            + (f"Process: {process}\n" if process else "")
            + (f"Where: {where}\n" if where else "")
            + f"Signature: {row['signature']}\nKind: {row['kind']}\nTico: {releases.version()}\n"
            f"First seen: {row['first_seen_at'][:16]} UTC; seen {seen['times'] or 1} time(s) in {seen['n']} place(s).\n\n"
            "What it means: " + BUG_FIX.get(row["check_id"], "Tico bug.") + "\n\nThe redacted diagnostics bundle is attached.")


def report(c_factory, support, auth, settings, who, key):
    """File the finding `key` as a Tico bug: a ticket, a message on its ticket, or an approval. `c_factory` is the
    store; HQ is called outside any transaction."""
    store = c_factory
    with store.read() as c:
        row = c.execute("SELECT * FROM health_findings WHERE key=? OR signature=? ORDER BY present DESC LIMIT 1",
                        (key, key)).fetchone()
        if not row:
            raise Problem("not_found", "No such finding", 404)
        row = dict(row)
        if row["kind"] != BUG:
            # A setup finding is this install's to fix (BotOps), and its text names local bots and computers: it never
            # goes to HQ, not even as an approval a person could send.
            raise Problem("not_a_tico_bug", "Only a Tico bug is reported to the Tico team; BotOps fixes a setup finding.", 409)
        filed = c.execute("SELECT * FROM health_reports WHERE signature=?", (row["signature"],)).fetchone()
        filed = dict(filed) if filed else None
        cleared = None
        allowed = team_rules.load(c)[RULE]
        owner = auth.owner_identity(c)
        message = report_message(c, row)
        if filed and not filed["ticket_id"] and filed["approval_id"]:
            done = c.execute("SELECT id FROM support_tickets WHERE request_key=?",
                             ("approval:" + filed["approval_id"],)).fetchone()
            if done:
                filed["ticket_id"] = done["id"]
            else:
                asked = c.execute("SELECT decision FROM approvals WHERE id=?", (filed["approval_id"],)).fetchone()
                if not asked or (asked["decision"] and asked["decision"] != "approved"):
                    cleared = filed["approval_id"]   # declined, withdrawn or gone: the signature can be reported again
        today = _reports_today(c)
    if cleared:
        with store.transaction() as c:
            c.execute("DELETE FROM health_reports WHERE signature=? AND approval_id=? AND ticket_id IS NULL",
                      (row["signature"], cleared))
        filed = None
    if filed and filed["ticket_id"]:
        if row["occurrences"] <= filed["reported_occurrences"]:
            return {"signature": row["signature"], "ticket": filed["ticket_id"], "again": True}
        # HQ hears it with the ticket's own secret, whoever filed it: the owner, or the person who approved it
        # (Support.file_approved). The person's chat does not list such a ticket (support.OWN): this line is the team's.
        with store.read() as c:
            kept_ticket = c.execute("SELECT * FROM support_tickets WHERE id=?", (filed["ticket_id"],)).fetchone()
        if kept_ticket:
            support.note(kept_ticket, f"[Health Monitor] Seen again ({row['occurrences']} times), last at "
                                      f"{row['last_seen_at'][:16]} UTC. Signature {row['signature']}.")
        with store.transaction() as c:
            c.execute("UPDATE health_reports SET reported_occurrences=?, reported_at=? WHERE signature=?",
                      (row["occurrences"], H.now(), row["signature"]))
            H.event(c, who.actor, "health_monitor.reported_again", row["signature"], {"ticket": filed["ticket_id"]})
        return {"signature": row["signature"], "ticket": filed["ticket_id"], "comment": True}
    if filed and filed["approval_id"]:
        return {"signature": row["signature"], "approval": filed["approval_id"], "again": True}
    if not allowed:
        # The person reads the report first: a `support` approval, sent with `hub support send --approval <id>`.
        digest = hashlib.sha256(message.encode()).hexdigest()
        now = H.now()
        with store.transaction() as c:
            approval = H.approval_request(c, who.actor, "support",
                                          {"message": message, "content_sha256": digest})
            approval_id = approval["id"] if isinstance(approval, dict) else approval
            c.execute("INSERT INTO health_reports(signature,approval_id,created_at,reported_occurrences,reported_at) "
                      "VALUES(?,?,?,?,?)", (row["signature"], approval_id, now, row["occurrences"], now))
        return {"signature": row["signature"], "approval": approval_id}
    if today >= REPORTS_PER_DAY:
        raise Problem("support_busy", f"The Health Monitor files at most {REPORTS_PER_DAY} new reports a day.", 429)
    from .support import SupportTicket
    bundle = diagnostics.build(store, settings, auth, owner, support.census)
    attachment = support.previews.keep(owner.actor, bundle)
    ticket = support.file(owner, SupportTicket(message=message, include_ids=True, diagnostics=attachment),
                          "health:" + row["signature"], source="health_monitor")
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO health_reports(signature,ticket_id,created_at,reported_occurrences,reported_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(signature) DO UPDATE SET ticket_id=excluded.ticket_id, reported_at=excluded.reported_at",
                  (row["signature"], ticket["id"], now, row["occurrences"], now))
        H.event(c, who.actor, "health_monitor.reported", row["signature"], {"ticket": ticket["id"]})
    return {"signature": row["signature"], "ticket": ticket["id"]}


def _reports_today(c):
    """New tickets sent in the last day, including approvals requested on an earlier day. Repeat comments do not count."""
    return c.execute("SELECT count(*) FROM health_reports h LEFT JOIN support_tickets t ON t.id=h.ticket_id "
                     "WHERE h.ticket_id IS NOT NULL AND coalesce(t.created_at,h.created_at)>?",
                     (H.shift(H.now(), hours=-24),)).fetchone()[0]



def file_reports(store, auth, settings, support, signatures):
    """HQ calls for the Tico bugs the step found, outside any transaction, as BotOps: the approvals it asks are BotOps's. The
    same report path as the route (`report`): a ticket once, a comment when it comes back worse, an approval when the team
    rule is off, and at most REPORTS_PER_DAY new tickets a day (the rest wait for the next run). First the reports a person
    approved since the last run are sent (`_send_approved`). With support off (TICO_SUPPORT, a demo or a rehearsal copy)
    nothing leaves for HQ and nothing is asked. Returns {signature: answer}."""
    from .support import require_on
    if support is None:
        return {}
    try:
        require_on(settings)
    except Problem:
        return {signature: {"signature": signature, "deferred": "support_off"} for signature in signatures}
    answers = _send_approved(store, support)
    for signature in signatures:
        if signature in answers:
            continue
        try:
            answers[signature] = report(store, support, auth, settings, SimpleNamespace(actor="bot:" + BOTOPS), signature)
        except Problem as exc:
            answers[signature] = {"signature": signature, "deferred": exc.code if hasattr(exc, "code") else str(exc)}
    return answers


def _send_approved(store, support):
    """Approved reports, sent once as their requester (BotOps, or the keeper for older requests). A failed or over-cap send
    stays queued. Its ticket and current occurrence baseline are kept on its health report. Returns {signature: answer}."""
    with store.read() as c:
        waiting = [dict(r) for r in c.execute(
            "SELECT h.signature, h.approval_id, a.requested_by_id FROM health_reports h JOIN approvals a ON a.id=h.approval_id "
            "WHERE h.ticket_id IS NULL AND a.requested_by_id IN (?,?) AND a.decision='approved' ORDER BY h.created_at",
            ("bot:" + BOTOPS, H.KEEPER)).fetchall()]
        today = _reports_today(c)
    sent = {}
    for row in waiting:
        if today >= REPORTS_PER_DAY:
            sent[row["signature"]] = {"signature": row["signature"], "deferred": "support_busy"}
            continue
        try:
            answer = support.file_approved(SimpleNamespace(actor=row["requested_by_id"]), row["approval_id"])
        except Exception as exc:  # noqa: BLE001 - a report that cannot be sent waits for the next run
            log.warning("Health report %s could not be sent yet", row["signature"], exc_info=True)
            sent[row["signature"]] = {"signature": row["signature"], "deferred": type(exc).__name__}
            continue
        ticket = answer["ticket"]["id"]
        with store.transaction() as c:
            c.execute("UPDATE health_reports SET ticket_id=?, reported_at=?, reported_occurrences="
                      "(SELECT coalesce(max(occurrences),0) FROM health_findings WHERE signature=?) "
                      "WHERE signature=? AND approval_id=?",
                      (ticket, H.now(), row["signature"], row["signature"], row["approval_id"]))
            H.event(c, row["requested_by_id"], "health_monitor.reported", row["signature"], {"ticket": ticket})
        today += 1
        sent[row["signature"]] = {"signature": row["signature"], "ticket": ticket, "approval": row["approval_id"]}
    return sent


def check(store, auth, settings, support=None):
    """An explicit diagnostic pass through Check health, with reports filed outside its write transaction.
    Scheduled checks run only through Maintenance. A rehearsal or demo copy checks nothing."""
    from .maintenance_steps import check_health
    if settings.rehearsal or settings.demo:
        return {"status": "skipped", "reason": "rehearsal or demo copy"}
    result = check_health.run_once(store, auth, settings)
    support = support or getattr(store, "report_support", None)     # support.py sets it on the app's store
    file_reports(store, auth, settings, support, result["details"].get("reports_due", []))
    return result


# ---------------------------------------------------------------------------------------------------- the routes
def findings_view(c):
    """Open findings, for the Health page (Settings > Health): a finding waiting on its confirmation run is not listed."""
    from .maintenance_steps import check_health
    rows = [dict(r) for r in c.execute(
        "SELECT f.key,f.signature,f.check_id,f.kind,f.title,f.detail,f.fix,f.severity,f.first_seen_at,f.last_seen_at,"
        "f.occurrences,r.ticket_id,r.approval_id FROM health_findings f LEFT JOIN health_reports r ON r.signature=f.signature "
        "WHERE f.present=1 AND f.pending=0 ORDER BY f.last_seen_at DESC LIMIT 50")]
    for row in rows:
        row["tag"] = check_health.tag_of(row["check_id"])
        row["task_id"] = check_health.task_of(c, row["signature"])
    botops = (H.bot(c, BOTOPS) or {}).get("state") == "active"
    return {"findings": rows, "botops": botops, "reports_without_asking": team_rules.load(c)[RULE]}


def install(app, store, auth, mutate, settings):
    @app.get("/api/v2/health/findings")
    def health_findings(request: Request):
        who = request.state.identity
        if who.role not in ("owner", "human") and who.actor != "bot:" + BOTOPS:
            raise Problem("forbidden", "People and BotOps read the health findings", 403)
        with store.read() as c:
            if who.role == "human" and not auth.bot_admin(who):
                raise Problem("forbidden", "Owners and admins read the health findings", 403)
            return findings_view(c)

    @app.post("/api/v2/health/findings/{key}/report")
    def report_finding(request: Request, key: str, body: M.Empty):
        who = request.state.identity
        if who.actor != "bot:" + BOTOPS and who.role != "owner":
            raise Problem("forbidden", "Only BotOps or the owner reports a finding", 403)
        support = getattr(app.state, "support", None)
        if support is None:
            raise Problem("support_off", "Contact support is not available here.", 409)
        from .support import require_on
        require_on(settings)
        return report(store, support, auth, settings, who, key)

    @app.post("/api/v2/runners/idle-load")
    def idle_load(request: Request, body: IdleLoad):
        """The runner's hourly busy list (runner-rs idle sampler). Optional: an old server has no such route, and an old
        runner never calls it. Off the heartbeat path, so a failed post never holds a heartbeat or a claim."""
        who = request.state.identity
        if who.role != "runner" or not getattr(who, "runner_id", None):
            raise Problem("forbidden", "Only a computer running Tico reports its busy processes", 403)
        return mutate(request, body, lambda c: {"busy": record_idle(c, who.runner_id, body)})
