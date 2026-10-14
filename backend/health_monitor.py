"""The Health Monitor: an optional built-in helper that looks over this install once an hour, has the setup problems it
finds fixed by BotOps, and reports Tico bugs to the Tico team (docs/health-monitor.md).

It is off until the owner turns it on (Settings > Health). While its bot is active, the scheduler runs `scan` once an
hour. The scan is plain code over what the server already knows (computers' versions and the error lines their runners
report, turns stopped by a usage limit, bots held in setup, routines on paused bots, queued work that does not start,
task worktrees that failed, the server's own CPU): it costs no model. Each finding has a key (what and where, on this
install) and a signature (what, without where, the same on every install). A finding the last scan did not have opens
one task for the Health Monitor, or wakes it on the task it already has, through the same path as a watcher's events
(backend/watchers.py); a finding still there an hour later says nothing. The bot's own watcher, `software/health-scan`,
looks at the computer it runs on (processes using CPU or memory while idle) the same way.

A Tico bug goes to HQ as a support ticket with `report`: one ticket per signature, a message written here from the
finding (never task or message text) and the redacted diagnostics bundle (backend/diagnostics.py). The team rule
`health_monitor_reports` (on by default) lets it file without asking; with it off, the report is a `support` approval a
person reads first. The ticket is the owner's: its replies show on the owner's Support page. A finding that comes back
after its ticket was filed adds a message to that ticket instead of filing another.
"""
import hashlib
import json
import logging
import re

from fastapi import Request
from pydantic import Field

from . import diagnostics, team_rules
from .models import Contract
from .store import H, Problem

log = logging.getLogger("tico.health_monitor")

SLUG = "health-monitor"
NAME = "Health Monitor"
RULE = "health_monitor_reports"
SCHEMA = """
CREATE TABLE IF NOT EXISTS health_findings(
 key TEXT PRIMARY KEY, signature TEXT NOT NULL, check_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '', fix TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 present INTEGER NOT NULL DEFAULT 1, occurrences INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS health_reports(
 signature TEXT PRIMARY KEY, ticket_id TEXT, approval_id TEXT, created TEXT NOT NULL, reported_occurrences INTEGER NOT NULL DEFAULT 1);
"""
SETUP, BUG = "setup", "tico-bug"
REPORTS_PER_DAY = 5          # new tickets the monitor files in a day; a repeat on a filed ticket is not counted
SERVER_CPU = 40.0            # the server's average CPU over an hour, in percent, while hardly anyone used it
QUIET_REQUESTS = 600         # requests in that hour below which the server counts as idle
REPEATED = 3                 # a runner error line seen this often in its last 50 is a pattern, not a blip
# Error lines a runner reports (runner/outage.py RECENT, sent as `recent_errors`) that point at a known problem.
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
    ("event_stream", BUG, re.compile(r"event stream (lost|unavailable)", re.I), REPEATED,
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
    "stuck_setup": "A bot has been in setup for over an hour while work waits for it.",
    "paused_routines": "A paused bot still has routines on.",
    "queue_idle": "Work stayed queued 15 minutes or more on an online computer with room and no limit.",
    "worktree_error": "A task worktree failed on its computer.",
    "server_cpu": f"The server averaged {SERVER_CPU:g}% CPU or more for an hour with under {QUIET_REQUESTS} requests.",
    "local_busy": "A process or container on a computer running Tico was busy in two hourly checks in a row while idle.",
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


class LocalFinding(Contract):
    """A process the bot's watcher saw busy on its computer: a name and two numbers, never free text."""
    process: str = Field(pattern=r"^(process|container):[A-Za-z0-9._-]{1,40}$")
    cpu: float = Field(ge=0, le=100000)
    mb: float = Field(ge=0, le=10000000)


def public_process(process):
    """`process:<name>` or `container:<name>` as a report may name it: a known Tico name, never a local one."""
    what, name = process.split(":", 1)
    if what == "process":
        return f"process {name}" if name in TICO_PROCESSES else "a process"
    parts = [p for p in name.lower().split("-") if not p.isdigit()]    # compose: <project>-<service>-<n>
    return f"the Tico {parts[-1]} container" if parts and parts[-1] in TICO_SERVICES else "a Tico container"


def record_local(c, body):
    """The bot's local finding as a server finding, so `report` files it like any other. Returns its key."""
    what, name = body.process.split(":", 1)
    item = _finding("local_busy:" + body.process, "local_busy", BUG, f"{what.capitalize()} {name} is busy while idle",
                    f"{what.capitalize()} {name} used {body.cpu:.0f}% CPU and {body.mb:.0f} MB of memory in two hourly "
                    "checks in a row on a computer running Tico.",
                    BUG_FIX["local_busy"], subject=public_process(body.process))
    now = H.now()
    c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen,last_seen,present,"
              "occurrences) VALUES(?,?,?,?,?,?,?,?,?,0,1) ON CONFLICT(key) DO UPDATE SET detail=excluded.detail,"
              "last_seen=excluded.last_seen,occurrences=health_findings.occurrences+1",
              (item["key"], item["signature"], item["check_id"], item["kind"], item["title"], item["detail"],
               item["fix"], now, now))
    return item["key"]


def signature(check_id, subject=""):
    """The same on every install for the same problem: what it is, never where."""
    return "hm-" + hashlib.sha256((check_id + "\n" + subject).encode()).hexdigest()[:12]


def _finding(key, check_id, kind, title, detail, fix, subject=""):
    return {"key": key, "signature": signature(check_id, subject), "check_id": check_id, "kind": kind,
            "title": title[:200], "detail": detail[:1500], "fix": fix[:600]}


def active(c):
    row = H.bot(c, SLUG)
    return bool(row) and row["state"] == "active"


# ---------------------------------------------------------------------------------------------------- the checks
def _runner_checks(c, settings):
    from . import health
    from .store import readiness_document
    out = []
    online = health._online_runners(c)
    computers = health._computers(c, online, settings)
    for machine in computers:
        update = machine["update"]
        if machine["online"] and (update["state"] in ("needs_update", "incompatible") or update["error"]):
            out.append(_finding("runner_outdated:" + machine["id"], "runner_outdated", SETUP,
                                f"{machine['label']} runs an older Tico release ({update['release'] or 'unknown'})",
                                f"Computer {machine['label']} reports release {update['release'] or 'unknown'}, state "
                                f"{update['state']}" + (f"; last update error: {update['error']}" if update["error"] else "") + ".",
                                f"BotOps: update the runner on {machine['label']} (Settings > Computers, or "
                                "`scripts/tico install` once on that computer so it updates itself)."))
    for r in c.execute("SELECT id,label,readiness_json FROM runners WHERE revoked_at IS NULL"):
        lines = [str(x) for x in (readiness_document(r["readiness_json"]).get("recent_errors") or [])]
        for check_id, kind, pattern, least, title, fix in LOG_PATTERNS:
            hits = [line for line in lines if pattern.search(line)]
            if len(hits) >= least:
                out.append(_finding(f"{check_id}:{r['id']}", check_id, kind, f"{title} ({r['label']})",
                                    f"{len(hits)} of the last {len(lines)} error lines from {r['label']} match. "
                                    "Latest: " + hits[-1][:280], fix))
    return out


def _bot_checks(c):
    from . import health
    from .harnesses import normalize_fallback
    out = []
    since = H.shift(H.now(), hours=-24)
    limited = {}
    for r in c.execute("SELECT detail_json FROM events WHERE action='attempt.limited' AND ts>?", (since,)):
        detail = json.loads(r["detail_json"] or "{}")
        if detail.get("bot"):
            limited[detail["bot"]] = limited.get(detail["bot"], 0) + 1
    for bot, n in sorted(limited.items()):
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        declared = json.loads(config["config_json"] or "{}") if config and config["config_json"] else {}
        if not normalize_fallback(declared.get("fallback")):
            out.append(_finding("limited_no_fallback:" + bot, "limited_no_fallback", SETUP,
                                f"{bot} stopped on a usage limit and has no fallback model",
                                f"{n} turn(s) of {bot} stopped on a subscription or spend limit in the last 24 hours, "
                                "and the bot has no fallback model to finish them on.",
                                f"BotOps: give {bot} a fallback model on another provider (Settings > Bots > {bot} > "
                                "Fallback), or raise its limit if the team chose one."))
    for slug, name, count, what, oldest in health.unfinished_setup(c):
        out.append(_finding("stuck_setup:" + slug, "stuck_setup", SETUP, f"{name} has been stuck in setup since {oldest[:16]}",
                            f"{name} is still in setup and holds {count} {what}(s), the oldest from {oldest[:16]} UTC.",
                            f"BotOps: finish {name}'s setup with the person it serves, or pause it so its work is not held."))
    for r in c.execute("SELECT b.slug,b.display_name,count(*) n FROM schedules s JOIN bots b ON b.slug=s.bot "
                       "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE b.state='paused' "
                       "AND coalesce(sc.enabled,1)=1 GROUP BY b.slug ORDER BY b.slug"):
        name = r["display_name"] or r["slug"]
        out.append(_finding("paused_routines:" + r["slug"], "paused_routines", SETUP,
                            f"{name} is paused but has {r['n']} routine(s) on",
                            f"{name} is paused, so its {r['n']} enabled routine(s) do not run, and nobody is told.",
                            f"BotOps: resume {name} if it should be working; otherwise turn its routines off."))
    online_ids = {r["id"] for r in health._online_runners(c)}
    for row in health._waiting(c, online_ids)[2]:
        if row["reason"] == "slow":
            out.append(_finding("queue_idle:" + row["bot"], "queue_idle", BUG,
                                f"{row['name']}'s work is queued but no turn starts",
                                f"{row['queued']} job(s) for {row['name']} queued since {str(row['oldest'])[:16]} UTC on "
                                f"{row['computer']}, which is online, not full and not limited.",
                                BUG_FIX["queue_idle"]))
    from .worktrees import supported
    if supported(c):
        for r in c.execute("SELECT t.owner,l.detail_json FROM task_links l JOIN tasks t ON t.id=l.task_id "
                           "WHERE l.kind='worktree' AND l.state<>'removed'"):
            error = str(json.loads(r["detail_json"] or "{}").get("error") or "")
            if error:
                bot = H.actor_id(r["owner"])
                digest = hashlib.sha256(error.encode()).hexdigest()[:8]
                out.append(_finding(f"worktree_error:{bot}:{digest}", "worktree_error", SETUP,
                                    f"A task worktree of {bot} failed",
                                    f"{bot}: {error[:400]}",
                                    "BotOps: save any unique work in that worktree on a branch, then remove its link so "
                                    "the computer can make it again (docs/worktrees.md)."))
    return out


def _server_checks(c):
    from . import flight
    try:
        seen = flight.view(c, minutes=60, sections=("requests", "process", "sql"))
    except Exception:
        return []                                   # no flight recorder on this server
    cpu, requests = (seen.get("process") or {}).get("cpu_avg"), (seen.get("requests") or {}).get("n") or 0
    if cpu is None or cpu < SERVER_CPU or requests >= QUIET_REQUESTS:
        return []
    top = [q["sql"][:120] for q in (seen.get("sql") or {}).get("queries", [])[:3]]
    return [_finding("server_cpu", "server_cpu", BUG, f"The server used {cpu:g}% CPU for an hour while idle",
                     f"Average CPU {cpu:g}% over the last hour with {requests} requests. Busiest SQL: "
                     + ("; ".join(top) or "none recorded") + ".",
                     BUG_FIX["server_cpu"])]


def findings(c, settings):
    """Everything wrong right now, each once by key."""
    out = {}
    for part in (_runner_checks(c, settings), _bot_checks(c), _server_checks(c)):
        for item in part:
            out.setdefault(item["key"], item)
    return list(out.values())


# ---------------------------------------------------------------------------------------------------- the scan
def _event(item, occurrences):
    from .watchers import WatcherEvent
    body = (f"{item['title']}\n\nKind: {item['kind']}\nCheck: {item['check_id']}\nSignature: {item['signature']}\n"
            f"Seen: {occurrences} time(s)\n\nWhat was seen:\n{item['detail']}\n\nFix:\n{item['fix']}\n\n"
            "Follow playbooks/finding.md.")
    text = (f"Seen again ({occurrences} times): {item['title']}. Signature {item['signature']}. "
            f"{item['detail']}")
    return WatcherEvent(op="comment", key="hm:" + item["key"], ref=f"{item['key']}#{occurrences}",
                        title=("Health: " + item["title"])[:300], body=body, text=text[:8000])


def scan(c, auth, settings):
    """One pass. Returns the keys that opened or woke a task; nothing when the Health Monitor is off."""
    if not active(c):
        return []
    from .watchers import deliver
    now, woke = H.now(), []
    found = findings(c, settings)
    keys = {item["key"] for item in found}
    for item in found:
        row = c.execute("SELECT * FROM health_findings WHERE key=?", (item["key"],)).fetchone()
        if row and row["present"]:
            c.execute("UPDATE health_findings SET last_seen=?,detail=? WHERE key=?", (now, item["detail"], item["key"]))
            continue                                         # still there: already said
        occurrences = row["occurrences"] + 1 if row else 1
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen,last_seen,present,"
                  "occurrences) VALUES(?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(key) DO UPDATE SET signature=excluded.signature,"
                  "kind=excluded.kind,title=excluded.title,detail=excluded.detail,fix=excluded.fix,last_seen=excluded.last_seen,"
                  "present=1,occurrences=excluded.occurrences",
                  (item["key"], item["signature"], item["check_id"], item["kind"], item["title"], item["detail"],
                   item["fix"], now, now, occurrences))
        if deliver(c, auth, SLUG, _event(item, occurrences)):
            woke.append(item["key"])
    gone = [r["key"] for r in c.execute("SELECT key FROM health_findings WHERE present=1") if r["key"] not in keys]
    for key in gone:
        c.execute("UPDATE health_findings SET present=0 WHERE key=?", (key,))
    if woke or gone:
        H.event(c, H.KEEPER, "health_monitor.scan", SLUG, {"new": woke, "cleared": gone})
    return woke


# ---------------------------------------------------------------------------------------------------- reporting
def report_message(c, row):
    """The ticket text, written here from the check alone: no finding text (it names bots and computers), task, message
    or person's words go in it."""
    from . import releases
    seen = c.execute("SELECT count(*) n, sum(occurrences) times FROM health_findings WHERE signature=?",
                     (row["signature"],)).fetchone()
    process = public_process(row["key"].split(":", 1)[1]) if row["check_id"] == "local_busy" else ""
    return (f"[Health Monitor] {row['check_id']}: {MEANING.get(row['check_id'], row['check_id'])}\n\n"
            + (f"Process: {process}\n" if process else "")
            + f"Signature: {row['signature']}\nKind: {row['kind']}\nTico: {releases.version()}\n"
            f"First seen: {row['first_seen'][:16]} UTC; seen {seen['times'] or 1} time(s) in {seen['n']} place(s).\n\n"
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
        since = H.shift(H.now(), hours=-24)
        today = c.execute("SELECT count(*) FROM health_reports WHERE created>? AND ticket_id IS NOT NULL",
                          (since,)).fetchone()[0]
    if cleared:
        with store.transaction() as c:
            c.execute("DELETE FROM health_reports WHERE signature=? AND approval_id=? AND ticket_id IS NULL",
                      (row["signature"], cleared))
        filed = None
    if filed and filed["ticket_id"]:
        if row["occurrences"] <= filed["reported_occurrences"]:
            return {"signature": row["signature"], "ticket": filed["ticket_id"], "again": True}
        support.write(owner, filed["ticket_id"],
                      f"[Health Monitor] Seen again ({row['occurrences']} times), last at {row['last_seen'][:16]} UTC. "
                      f"Signature {row['signature']}.")
        with store.transaction() as c:
            c.execute("UPDATE health_reports SET reported_occurrences=? WHERE signature=?",
                      (row["occurrences"], row["signature"]))
            H.event(c, who.actor, "health_monitor.reported_again", row["signature"], {"ticket": filed["ticket_id"]})
        return {"signature": row["signature"], "ticket": filed["ticket_id"], "comment": True}
    if filed and filed["approval_id"]:
        return {"signature": row["signature"], "approval": filed["approval_id"], "again": True}
    if not allowed:
        # The person reads the report first: a `support` approval, sent with `hub support send --approval <id>`.
        digest = hashlib.sha256(message.encode()).hexdigest()
        with store.transaction() as c:
            approval = H.approval_request(c, who.actor, "support",
                                          {"message": message, "content_sha256": digest})
            approval_id = approval["id"] if isinstance(approval, dict) else approval
            c.execute("INSERT INTO health_reports(signature,approval_id,created,reported_occurrences) VALUES(?,?,?,?)",
                      (row["signature"], approval_id, H.now(), row["occurrences"]))
        return {"signature": row["signature"], "approval": approval_id}
    if today >= REPORTS_PER_DAY:
        raise Problem("support_busy", f"The Health Monitor files at most {REPORTS_PER_DAY} new reports a day.", 429)
    from .support import SupportTicket
    bundle = diagnostics.build(store, settings, auth, owner, support.census)
    attachment = support.previews.keep(owner.actor, bundle)
    ticket = support.file(owner, SupportTicket(message=message, include_ids=True, diagnostics=attachment),
                          "health:" + row["signature"])
    with store.transaction() as c:
        c.execute("INSERT INTO health_reports(signature,ticket_id,created,reported_occurrences) VALUES(?,?,?,?) "
                  "ON CONFLICT(signature) DO UPDATE SET ticket_id=excluded.ticket_id",
                  (row["signature"], ticket["id"], H.now(), row["occurrences"]))
        H.event(c, who.actor, "health_monitor.reported", row["signature"], {"ticket": ticket["id"]})
    return {"signature": row["signature"], "ticket": ticket["id"]}


# ---------------------------------------------------------------------------------------------------- the routes
def view(c):
    row = H.bot(c, SLUG)
    rows = [dict(r) for r in c.execute(
        "SELECT f.key,f.signature,f.check_id,f.kind,f.title,f.first_seen,f.last_seen,f.occurrences,r.ticket_id,r.approval_id "
        "FROM health_findings f LEFT JOIN health_reports r ON r.signature=f.signature WHERE f.present=1 "
        "ORDER BY f.last_seen DESC LIMIT 50")]
    return {"state": row["state"] if row else "absent", "on": bool(row) and row["state"] == "active",
            "reports_without_asking": team_rules.load(c)[RULE], "findings": rows}


def install(app, store, auth, mutate, onboarding, settings_admin, settings):
    from . import models as M

    def owner(request):
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner turns the " + NAME + " on or off", 403)
        return who

    @app.get("/api/v2/health-monitor")
    def health_monitor(request: Request):
        who = request.state.identity
        if who.role not in ("owner", "human") and who.actor != "bot:" + SLUG:
            raise Problem("forbidden", "People and the " + NAME + " read this", 403)
        with store.read() as c:
            if who.role == "human" and not auth.bot_admin(who):
                raise Problem("forbidden", "Owners and admins read this", 403)
            return view(c)

    @app.post("/api/v2/health-monitor/turn-on")
    def turn_on(request: Request, body: M.Empty):
        who = owner(request)
        return mutate(request, body, lambda c: onboarding.turn_on_health_monitor(c, who))

    @app.post("/api/v2/health-monitor/turn-off")
    def turn_off(request: Request, body: M.Empty):
        who = owner(request)

        def work(c):
            row = H.bot(c, SLUG)
            if not row or row["state"] != "active":
                return {"bot": SLUG, "state": row["state"] if row else "absent"}
            settings_admin.update_bot(c, who, SLUG, M.BotDefinitionUpdate(
                status="paused", expected_revision=settings_admin._config(c, SLUG)["revision"]))
            H.event(c, who.actor, "health_monitor.turned_off", SLUG, {})
            return {"bot": SLUG, "state": H.bot(c, SLUG)["state"]}
        return mutate(request, body, work)

    @app.post("/api/v2/health-monitor/local-findings")
    def local_finding(request: Request, body: LocalFinding):
        who = request.state.identity
        if who.actor != "bot:" + SLUG:
            raise Problem("forbidden", "Only the " + NAME + " records what its watcher saw", 403)
        return mutate(request, body, lambda c: {"key": record_local(c, body)})

    @app.post("/api/v2/health-monitor/findings/{key}/report")
    def report_finding(request: Request, key: str, body: M.Empty):
        who = request.state.identity
        if who.actor != "bot:" + SLUG and who.role != "owner":
            raise Problem("forbidden", "Only the " + NAME + " or the owner reports a finding", 403)
        support = getattr(app.state, "support", None)
        if support is None:
            raise Problem("support_off", "Contact support is not available here.", 409)
        from .support import require_on
        require_on(settings)
        return report(store, support, auth, settings, who, key)
