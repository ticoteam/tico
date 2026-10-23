"""The Health page: what needs attention right now, computed from real state on every read.

Nothing here is stored but one note the owner still has to read (backend/access.py). A check is `ok`, `warn`, `bad`, `info` or `unknown`; `unknown` means the thing
that would tell us is not reporting, which is not the same as fine, and `info` is an optional thing
that is not set up (neither fine nor a problem). Each check may carry fixes,
which the page turns into one-click links. People who are not administrators see counts only; a bot sees the same
counts plus which computers are up and the day's failed runs, so it can check the team's health itself.
"""

import json
import os
import shlex
from datetime import datetime, timezone

from . import (access, billing_check, blob_s3, gateway_connection, inbox_isolation, model_login, providers, releases, roadmaps, runner_versions,
               setup_stuck, watchers)
from .getting_started import _online_runners, _signed_in_runtime, _wanted_runtimes, _person


def _harness_name(runtime):
    """A runtime as people know it: `claude` is Claude Code (backend/bot_tools.py HARNESS_NAMES)."""
    from .bot_tools import HARNESS_NAMES
    return HARNESS_NAMES.get(runtime) or str(runtime).title()
from .store import H, Problem, readiness_document
from .views import roster

QUEUE_MINUTES = 15          # work that has waited this long on a computer that is up is stuck
PERSON_QUEUE_MINUTES = 2    # a person's own message, sooner: someone is sitting there waiting for the reply
# A Restart a person pressed claims for 20 minutes, then waits up to 14 for its running turns (runner-rs runner.rs
# RESTART_DRAIN_S + STOP_DRAIN_S): past that it is stuck, and Health Monitor says so.
RESTART_STUCK_MINUTES = 34
SILENT_MINUTES = 10         # a computer this quiet while work is queued for it has stopped, not blinked
NEEDS_YOU_MINUTES = 30      # this quiet, its owner is asked to bring it back
BACKUP_STALE_HOURS = 6      # the replica normally trails by seconds
RECENT_HOURS = 24
GITHUB_HEALTH = "github:token"
PROXIES = {"cloudflare": "Cloudflare Access", "aws-alb": "an AWS load balancer", "oidc": "OpenID Connect",
           "owner-link": "the owner sign-in link Tico sends"}


def _fix(label, href="", tab="", click=""):
    return {"label": label, "href": href, "tab": tab, "click": click}


def _check(key, label, status, summary, fixes=()):
    return {"id": key, "label": label, "status": status, "summary": summary, "fixes": list(fixes)}


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


def _computers(c, runners_online, settings):
    since = {r["id"] for r in runners_online}
    rows, fleet = [], runner_versions.load(c)
    _, assigned = providers.runtimes_needed(c, settings)
    # The one default a computer installs for its first sign-in (providers.default_runtime), not every provider's
    # runtime while the company has no default model: the runner installs only that one.
    default = providers.default_runtime(providers.load(c, settings))
    wanted = {default} - {""}
    for r in c.execute("SELECT r.id,r.label,r.last_seen,r.platform,r.readiness_json,coalesce(nullif(h.name,''),r.operator) "
                       "AS operator FROM runners r LEFT JOIN humans h ON h.id=r.operator WHERE r.revoked_at IS NULL "
                       "ORDER BY r.label"):
        document = readiness_document(r["readiness_json"])
        runtimes = document.get("runtimes") or {}
        rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in since, "last_seen": r["last_seen"],
                     "platform": r["platform"] or "", "update": runner_versions.view(fleet.get(r["id"])),
                     "disk": document.get("disk"),
                     "operator": r["operator"], "container_exec": document.get("container_exec"),
                     "github_auth": document.get("github_auth"), "browser_launch": document.get("browser_launch"),
                     "engine": document.get("engine"),
                     # Only what the company or an assigned bot uses, or what is installed anyway: the
                     # other harnesses are not this computer's business, so they are not listed.
                     "runtimes": [{"name": n, "installed": bool(v.get("installed")),
                                   "needed": n in wanted or n in assigned.get(r["id"], ()),
                                   "ready": bool(v.get("installed")) and v.get("authenticated") == "ready",
                                   "rejected": v.get("authenticated") == "rejected",
                                   "rejected_at": v.get("rejected_at") or "" if v.get("authenticated") == "rejected" else "",
                                   "rejected_reason": v.get("rejected_reason") or "" if v.get("authenticated") == "rejected" else "",
                                   "credential_source": v.get("credential_source") or "",
                                   "signable": bool(v.get("installed")) and v.get("authenticated") != "ready"
                                   and n in model_login.RUNTIMES,
                                   # A model this computer runs that it has no sign-in for, and that no key from the
                                   # company gave it either: one its active bots run on, or the company's default while
                                   # it has none yet. A default its bots don't use is only `signable`.
                                   "sign_in": bool(v.get("installed")) and v.get("authenticated") == "missing"
                                   and n in model_login.RUNTIMES
                                   and n in assigned.get(r["id"], {default})}
                                  for n, v in sorted(runtimes.items())
                                  if v.get("installed") or n in wanted or n in assigned.get(r["id"], ())]})
    return rows


def _mail_key_exposed(c):
    """Labels of the computers that report holding the mail key where their bots can read it."""
    return [r["label"] for r in c.execute("SELECT label,readiness_json FROM runners WHERE revoked_at IS NULL ORDER BY label")
            if readiness_document(r["readiness_json"]).get("mail_key") == "exposed"]


PUBLISH_WARNING = "GitHub history not published: "                     # runner-rs/src/readiness_extra.rs
DENIED_WARNING = "Claude Code denies what AGENT.md asks for: "          # runner-rs/src/readiness_extra.rs
DENIES_WARNING = "Claude Code settings deny: "                         # runner-rs/src/readiness_extra.rs
# GitHub writes a bot's access may allow: the command, and the `can` verbs (backend/bot_tools.py) that allow it.
# Merging and reviewing are left out: denying them to a bot that opens pull requests is a policy (a person or
# another bot merges; no bot approves its own work), not a leftover.
GITHUB_WRITES = (("gh pr create", ("write", "pull_requests")), ("gh pr comment", ("write", "pull_requests")),
                 ("gh pr edit", ("write", "pull_requests")), ("gh issue create", ("write", "issues")),
                 ("gh issue comment", ("write", "issues")), ("git push", ("write",)))
WRITE_VERBS = {"write", "pull_requests", "issues"}


def _unpublished(c):
    """Bots whose local history the runner could not give a GitHub repository (runner-rs/src/execute.rs `publish`)."""
    return _reported(c, PUBLISH_WARNING)


def _reported(c, prefix, limit=200):
    """(bot, the rest of the line) for each readiness warning starting with `prefix` from the computer hosting the bot."""
    out = []
    hosting = {(a["bot"], a["runner_id"]) for a in c.execute(
        "SELECT a.bot,a.runner_id FROM assignments a JOIN bots b ON b.slug=a.bot WHERE b.state<>'archived'")}
    for r in c.execute("SELECT id,readiness_json FROM runners WHERE revoked_at IS NULL"):
        for bot, row in ((readiness_document(r["readiness_json"]).get("bots")) or {}).items():
            if (bot, r["id"]) not in hosting:
                continue                  # a computer that no longer hosts the bot has nothing to publish for it
            for warning in (row or {}).get("warnings") or []:
                if str(warning).startswith(prefix):
                    out.append((bot, str(warning)[len(prefix):][:limit]))
    return sorted(set(out))


def harness_denies(c, settings):
    """(bot, command, repositories) for each GitHub write a bot's access allows on a repository other than its own
    but its Claude Code settings deny. Its own repository is left out, so a bot that only reads keeps its denies."""
    from .bot_tools import listing
    from .github_app import repo_of
    out = []
    for bot, rest in _reported(c, DENIES_WARNING, limit=500):
        prefixes = [p.strip() for p in rest.split(",") if p.strip()]
        try:
            tools = listing(c, settings, bot)["tools"]
        except Exception:
            continue
        row = c.execute("SELECT repo,config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        own = str(repo_of((row["repo"] if row else "") or "bot-" + bot, "owner") or "").split("/")[-1].lower()
        mode = H._json(row["config_json"] if row else None, {}).get("repo_access_mode", "own")
        github = [t for t in tools if str(t.get("service") or "").lower() in ("github", "github-app")
                  and t.get("id") != "repo" and "own repository" not in str(t.get("detail") or "")
                  and str((t.get("scope") or {}).get("repo") or t.get("identity") or "").split("/")[-1].lower() != own]
        declared = [t for t in github if not str(t.get("id") or "").startswith("github-extra-")]
        reads_only = not any(WRITE_VERBS & set(t.get("can") or []) for t in declared)
        # Write on every repository ('all', write by default) says nothing about this bot; a repository a person
        # chose for it with write does. A bot.yaml declaring no GitHub write means its denies are intended.
        granted = [t for t in github if t in declared or mode == "chosen" or not reads_only]
        for command, verbs in GITHUB_WRITES:
            if not any(command == p or command.startswith(p + " ") for p in prefixes):
                continue
            repos = sorted({str((t.get("scope") or {}).get("repo") or t.get("identity") or "") for t in granted
                            if set(verbs) & set(t.get("can") or [])} - {""})
            if repos:
                out.append((bot, command, repos))
    return out


def _harness_denies_check(c, settings):
    rows = harness_denies(c, settings)
    if not rows:
        return None
    by_bot = {}
    for bot, command, repos in rows:
        by_bot.setdefault(bot, ([], set()))[0].append(command)
        by_bot[bot][1].update(repos)
    lines = [f"{_bot_name(c, bot)} may write to {', '.join(sorted(repos)[:2])}{' and more' if len(repos) > 2 else ''}, "
             f"but its Claude Code settings deny {', '.join(commands)}" for bot, (commands, repos) in sorted(by_bot.items())]
    return _check("harness_denies", "Harness permissions", "warn",
                  "; ".join(lines[:3]) + ("" if len(lines) <= 3 else f"; and {len(lines) - 3} more")
                  + ". Its work stops there. Remove the deny from .claude/settings.json in its repository, "
                  "or take away the access.", [_fix("Open bots", "#/settings", "bots")])


def _missing_tool_credentials(c, online_ids):
    """Tool and Computer names only, for online assignments with effective credentials missing."""
    from .bot_tools import granted, service_name
    out = set()
    for row in c.execute("SELECT a.bot,r.id,r.label,r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                         "JOIN bots b ON b.slug=a.bot WHERE b.state<>'archived' AND r.revoked_at IS NULL"):
        if row["id"] not in online_ids:
            continue
        report = (readiness_document(row["readiness_json"]).get("bots") or {}).get(row["bot"]) or {}
        for tool in granted(c, row["bot"], report.get("tools") or []):
            if tool.get("credential") == "missing":
                out.add((service_name(tool.get("service")), row["label"]))
    return sorted(out)


def reserved_credentials(c):
    """(credential name, variable, bot) for each credential a bot holds under a name Tico reserves. The runner leaves
    such a credential out of the bot's turns, so the bot runs without it until someone renames the variable."""
    from clients.access_entry import RESERVED_ENV, RESERVED_PREFIXES
    from .credentials import effective_grant
    out = []
    for row in c.execute("SELECT DISTINCT v.id,v.name,v.env,g.subject FROM credentials v JOIN credential_grants g "
                         "ON g.credential_id=v.id WHERE g.subject LIKE 'bot:%' AND g.revoked IS NULL "
                         "AND v.ciphertext IS NOT NULL AND v.env<>'' ORDER BY v.name"):
        if (row["env"] in RESERVED_ENV or row["env"].startswith(RESERVED_PREFIXES)) \
                and effective_grant(c, row["id"], row["subject"]):
            out.append((row["name"], row["env"], H.actor_id(row["subject"])))
    return out


def missing_repositories(c, online_ids):
    """(bot, computer, why) for the active bots whose online computer says it has no repository for them.
    `why` is the runner's own sentence naming the cause (not on GitHub, GitHub refused, a clone failed)."""
    out = []
    for r in c.execute("SELECT a.bot,r.id,r.label,r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                       "JOIN bots b ON b.slug=a.bot WHERE b.state='active' AND r.revoked_at IS NULL ORDER BY a.bot"):
        report = (readiness_document(r["readiness_json"]).get("bots") or {}).get(r["bot"])
        if r["id"] in online_ids and isinstance(report, dict) and report.get("repository_present") is False:
            why = next((p for p in report.get("problems") or [] if "repositor" in p.lower()), "Missing bot repository")
            out.append((r["bot"], r["label"], why))
    return out


def failed_repository_copies(c, online_ids):
    """(computer, repository, why) for the repositories an online computer could not copy to its workspace: the
    runner's own words, git's reason included, so nobody has to guess between access, network and disk."""
    from .repositories import metadata
    out = []
    for r in c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL ORDER BY label"):
        if r["id"] not in online_ids:
            continue
        rows = metadata(c, "computer-repositories:" + r["id"]).get("repositories")
        for row in rows if isinstance(rows, list) else ():
            if isinstance(row, dict) and row.get("state") == "failed" and row.get("full_name"):
                out.append((r["label"], str(row["full_name"]), str(row.get("error") or "the copy failed")[:500]))
    return out


LISTENING_ACTIVE_DAYS = 14       # Listening still saving posts this recently is still in use


def _listening_lost(c, settings):
    """Listening once sent posts to inboxes and still saves them, but the registry no longer routes any: what a
    server move that left `listening.yaml` behind looks like. A company that stopped listening is not flagged."""
    from . import listening
    routed = c.execute("SELECT count(*) n, max(created_at) last FROM intake_items").fetchone()
    if not routed or not routed["n"]:
        return None
    last = routed["last"]
    saved = c.execute("SELECT count(*) n FROM listen_items WHERE first_seen_at>?", (last,)).fetchone()["n"]
    if not c.execute("SELECT 1 FROM listen_items WHERE first_seen_at>? LIMIT 1",
                     (H.shift(H.now(), days=-LISTENING_ACTIVE_DAYS),)).fetchone():
        return None
    present = (settings.registry_dir / listening.LISTENING_FILE).is_file()
    return _check("listening", "Listening", "warn" if present else "bad",
                  f"Listening routes nowhere: listening.yaml {'has no valid destinations' if present else 'is missing from the company registry'}. "
                  f"It last sent a post to an inbox on {last[:10]}; {_plural(saved, 'post')} saved since went to no inbox. "
                  "Restore listening.yaml and questions/listening-item.json from the registry's previous copy.",
                  [_fix("Listening settings", "https://github.com/ticoteam/tico/blob/main/docs/listening.md")])


def _listening_questions_lost(c, settings, qset):
    """The registry has no questions of its own, yet posts were scored lately with a later version of the set than
    the shipped one now in use: the company's own questions were there and are gone."""
    from . import listening
    if (settings.registry_dir / "questions" / (listening.QUESTION_SET + ".json")).is_file():
        return None
    since = H.shift(H.now(), days=-30)
    for row in c.execute("SELECT question_set, max(judged_at) last FROM listen_judgments WHERE judged_at>? "
                         "GROUP BY question_set", (since,)):
        name, _, version = str(row["question_set"]).partition("@")
        if name == qset["id"] and version.isdigit() and int(version) > int(qset["version"]):
            return _check("listening", "Listening", "warn",
                          f"Listening scores posts with Tico's built-in questions ({qset['label']}), but until "
                          f"{row['last'][:10]} it used the company's own ({row['question_set']}). "
                          "Restore questions/listening-item.json in the company registry.",
                          [_fix("Listening settings", "https://github.com/ticoteam/tico/blob/main/docs/listening.md")])
    return None


def _listening(c, settings):
    from . import listening
    from clients.judge import JudgeError
    dests = listening.destinations(settings, check_questions=False)
    if not dests:
        return _listening_lost(c, settings)
    try:
        qset = listening.question_set(settings)
        problems = listening.category_problems(dests, qset)
    except JudgeError:
        problems = ["The listening-item question set could not be loaded. Check registry/questions/listening-item.json."]
    if not problems:
        return _listening_questions_lost(c, settings, qset)
    summary = "; ".join(problems[:5])
    if len(problems) > 5:
        summary += f"; and {len(problems) - 5} more"
    return _check("listening", "Listening", "warn", summary,
                  [_fix("Listening settings", "https://github.com/ticoteam/tico/blob/main/docs/listening.md")])


def repository_fix(c, bot, label, github_owner=""):
    """The original repository and the clone command on the computer that is missing it."""
    from .shared_bots import declared, follow
    from .store import repo_url
    config = follow(c, bot, declared(c, bot))
    source = config.get("shared_from") or bot
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source,)).fetchone()
    repo = str((row["repo"] if row else "") or config.get("repo") or "emp-" + source)
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    address = repo_url(repo, (app["org"] if app else "") or github_owner) or config.get("repo_url") or repo
    repository = str(address).rstrip("/").removesuffix(".git").removeprefix("https://github.com/")
    row = c.execute("SELECT r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?",
                    (bot,)).fetchone()
    report = ((readiness_document(row[0]).get("bots") or {}).get(bot) or {}) if row else {}
    path = report.get("repository") or "<projects>/" + repo.rstrip("/").split("/")[-1].removesuffix(".git")
    command = f"gh repo clone {shlex.quote(repository)} {shlex.quote(path)}"
    return repository, f"Run `{command}` on {label}, or ask BotOps"


def _member_bots_beside_shared_keys(c):
    """Computers whose `_shared.env` holds keys every bot there receives, and that also host bots members
    created: a member's bot instructions could ask a run for them."""
    out = []
    for r in c.execute("SELECT id,label,readiness_json FROM runners WHERE revoked_at IS NULL ORDER BY label"):
        if not readiness_document(r["readiness_json"]).get("shared_env"):
            continue
        members = [a["bot"] for a in c.execute(
            "SELECT a.bot FROM assignments a JOIN bot_config bc ON bc.bot=a.bot WHERE a.runner_id=? "
            "AND bc.created_by LIKE 'human:%'", (r["id"],)) if _made_by_member(c, a["bot"])]
        if members:
            out.append((r["label"], members))
    return out


def _made_by_member(c, bot):
    from .auth import Auth
    return Auth.member_bot_row(c, bot)


def _rejected(computers):
    """Online computers whose wanted harness refused its key or login: (computer, runtime, when, why, credential source)."""
    return [(x["label"], r["name"], r["rejected_at"], r["rejected_reason"], r["credential_source"])
            for x in computers if x["online"] for r in x["runtimes"] if r["rejected"] and r["needed"]]


def _needs_sign_in(computers):
    """(computer, model CLI) for online computers that have to be signed in to a model they run, by hand or BotOps."""
    return [(x["label"], providers.RUNTIME_LABELS.get(r["name"], r["name"]))
            for x in computers if x["online"] for r in x["runtimes"] if r["sign_in"]]


def _rejected_summary(rows):
    """What to do about it; the reason is the harness's own sanitized words, never the key."""
    first = ", ".join(sorted({f"{name} on {label}" for label, name, _, _, _ in rows}))
    when, why = rows[0][2], rows[0][3]
    sources = {row[4] for row in rows}
    fix = (" Replace the model key in Tools > Credentials." if sources == {"credentials"} else
           " Update the computer-local key or sign in again from Settings > Computers." if sources == {"computer"} else
           " If the model key is stored in Credentials, replace it in Tools > Credentials. Otherwise update the "
           "computer-local key or sign in again from Settings > Computers.")
    return (f"Sign-in rejected for {first}" + (f" at {when}" if when else "") + (f": {why}" if why else ".")
            + fix + " It takes no work that needs it until then.")


def silent_computers(c, minutes=SILENT_MINUTES):
    """Computers that have not checked in for `minutes` while work is queued for their active bots, longest silence
    first: [{id, label, operator, last_seen, minutes, queued, bots}]. A computer that never checked in is setup, not this."""
    rows = c.execute(
        "SELECT r.id, r.label, r.operator, r.last_seen, count(j.id) queued, group_concat(DISTINCT a.bot) bots "
        "FROM runners r JOIN assignments a ON a.runner_id=r.id JOIN bots b ON b.slug=a.bot AND b.state='active' "
        "JOIN jobs j ON j.bot=a.bot AND j.state='queued' AND j.held=0 "
        "WHERE r.revoked_at IS NULL AND r.last_seen < ? GROUP BY r.id ORDER BY r.last_seen",
        (H.shift(H.now(), minutes=-minutes),)).fetchall()
    return [{"id": r["id"], "label": r["label"], "operator": r["operator"], "last_seen": r["last_seen"],
             "minutes": quiet_minutes(r["last_seen"]), "queued": r["queued"], "bots": sorted(r["bots"].split(","))}
            for r in rows]


def quiet_minutes(last_seen):
    """Whole minutes since `last_seen`; 0 when it cannot be read."""
    seen = H.parse_ts(last_seen)
    return int((datetime.now(timezone.utc) - seen).total_seconds() // 60) if seen else 0


def _waiting(c, online_ids, settings=None):
    """Active bots whose computer is offline (or that have none while work is queued), with the
    work stuck behind them, and bots whose queued work is old even though their computer is up.
    The third list is every bot with queued work that is not starting, with the computer's own
    reason (`_stuck`). An idle bot's reason is the claim's own (execution.bot_refusal); only work the
    claim has no reason to hold is "slow", a Tico bug, and only once the job the claim would take is old: an old held job
    beside a young free one is neither."""
    from .execution import bot_claimable
    cutoff = H.shift(H.now(), minutes=-QUEUE_MINUTES)
    person_cutoff = H.shift(H.now(), minutes=-PERSON_QUEUE_MINUTES)
    # A job its wake preset holds is waiting on purpose (backend/wake_presets.py), not stuck.
    queued = {r["bot"]: (r["n"], r["oldest"]) for r in c.execute(
        "SELECT bot, count(*) n, min(created) oldest FROM jobs WHERE state='queued' AND held=0 GROUP BY bot")}
    asked = {r["bot"]: r["oldest"] for r in c.execute(
        "SELECT j.bot, min(j.created) oldest FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.state='queued' AND m.from_actor LIKE 'human:%' GROUP BY j.bot")}
    assigned = {r["bot"]: (r["runner_id"], r["label"], r["readiness_json"]) for r in c.execute(
        "SELECT a.bot, a.runner_id, r.label, r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
        "WHERE r.revoked_at IS NULL")}
    # A starter bot still waiting for its first setup holds its work on purpose: not slow (`unfinished_setup` warns
    # once the hold is old).
    parked = {r["bot"] for r in c.execute("SELECT bot FROM bot_config WHERE onboarding_state IN ('needs_setup','needs_onboarding')")}
    # A bot in the middle of a turn has its next message waiting behind it: busy, not stuck (partial index).
    busy, running, turns = set(), {}, {}
    for r in c.execute("SELECT id,bot,runner_id,coalesce(started,created) since FROM attempts "
                       "WHERE state IN ('leased','running') ORDER BY since"):
        busy.add(r["bot"])
        running[r["runner_id"]] = running.get(r["runner_id"], 0) + 1
        turns.setdefault(r["runner_id"], []).append(r)
    # A computer running as many turns as it takes holds the rest back on purpose: queued, not stuck.
    full = {r["id"] for r in c.execute("SELECT id,capacity FROM runners WHERE revoked_at IS NULL")
            if running.get(r["id"], 0) >= (r["capacity"] or 1)}
    # A computer due an update keeps claiming for a while, then claims nothing until its running turns end and it
    # updates (runner-rs follow.rs blocks_claims): its queued work waits on that, not on a Tico bug.
    updating = {r["runner_id"]: r["update_target"] for r in c.execute(
        "SELECT runner_id,update_target FROM runner_versions WHERE update_state IN ('waiting','updating')")}
    # A computer a person pressed Restart for claims for a while, then claims nothing until its running turns end and
    # it restarts (runner-rs runner.rs RESTART_DRAIN_S); it says so in its heartbeat until it has.
    restarting = {r["id"] for r in c.execute("SELECT id FROM runners WHERE restart_since IS NOT NULL AND revoked_at IS NULL")}
    documents = {}      # one parse per computer, and only for bots with queued work: Health is polled by every tab

    def report_for(slug, where):
        if not where:
            return {}
        if where[0] not in documents:
            documents[where[0]] = readiness_document(where[2]).get("bots") or {}
        report = documents[where[0]].get(slug)
        return report if isinstance(report, dict) else {}
    holding = {}

    def held(runner_id):     # one read per computer, however many of its bots wait on its update
        if runner_id not in holding:
            holding[runner_id] = holding_turns(c, turns.get(runner_id, []))
        return holding[runner_id]
    # A turn waiting for its shared login is busy, not stuck, unless what holds the login is no run Tico knows.
    from .login_waits import unknown_holder
    orphaned = {r["bot"] for rows in turns.values() for r in rows if unknown_holder(c, r["id"])}
    waiting, slow, stuck = [], [], []
    for bot in c.execute("SELECT slug,display_name FROM bots WHERE state='active' ORDER BY slug"):
        slug = bot["slug"]
        count, oldest = queued.get(slug, (0, None))
        where = assigned.get(slug)
        row = {"bot": slug, "name": bot["display_name"] or slug, "queued": count, "oldest": oldest,
               "computer": where[1] if where else ""}
        if slug in orphaned and where and where[0] in online_ids:
            stuck.append({**row, "reason": "login_holder_unknown", "runner_id": where[0], "runtime": ""})
            continue
        report = report_for(slug, where) if count else {}
        if where and where[0] not in online_ids:
            waiting.append({**row, "reason": "computer_offline"})
            if count:
                stuck.append({**row, "reason": "computer_offline", "runner_id": where[0], "runtime": report.get("runtime") or ""})
        elif not where and count and (not online_ids or (oldest and oldest < cutoff and slug not in parked)):
            if not online_ids:
                waiting.append({**row, "reason": "no_computer"})
            stuck.append({**row, "reason": "no_computer", "runner_id": "", "runtime": ""})
        elif where and count and report.get("ready") is False and slug not in parked:
            # The computer says this bot cannot start (no sign-in, no repository, ...): stuck now, not in 15 minutes.
            stuck.append({**row, "reason": "not_ready", "runner_id": where[0], "runtime": report.get("runtime") or "",
                          "problem": str((report.get("problems") or ["Not ready"])[0])[:200]})
            if oldest and oldest < cutoff:
                slow.append({**row, "reason": "slow"})
        elif where and count and slug not in parked and (
                (oldest and oldest < cutoff) or (asked.get(slug) and asked[slug] < person_cutoff)):
            # A bot the claim holds on purpose (a spend limit, a usage-limit cooldown, a lapsed lease whose worker still
            # runs, interrupted work awaiting review, ...) is said with that reason, not "slow".
            idle = slug not in busy and where[0] not in full
            refused, first, person = bot_claimable(c, settings, where[0], slug) if idle else (None,) * 3
            if idle and not refused:
                if not (first and first < (person_cutoff if person else cutoff)):
                    continue        # the job the claim would take is young: the older ones wait behind it or are held
                row["oldest"] = first
            reason = ("limit" if refused == "limit" else "updating" if where[0] in updating
                      else "restarting" if where[0] in restarting else refused or "slow")
            slow.append({**row, "reason": reason if idle else "busy"})
            if idle:
                stuck.append({**row, "reason": reason, "runner_id": where[0], "runtime": report.get("runtime") or ""}
                             | ({"target": updating[where[0]], "running": running.get(where[0], 0),
                                  "turns": held(where[0])} if reason == "updating" else {}))
    return waiting, slow, stuck


SETUP_HOLD_MINUTES = 60     # a bot still in setup holds its work; this long and nobody is finishing it


def unfinished_setup(c):
    """Active bots still in setup whose held work is older than SETUP_HOLD_MINUTES: [(slug, name, count, what, oldest)].
    A parked bot takes no task and no notice until its setup is done, so its watchers' tasks pile up out of sight."""
    from .statuses import PARKED_SQL
    parked = c.execute("SELECT b.slug,b.display_name FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                       f"WHERE bc.onboarding_state IN {PARKED_SQL} AND b.state='active' ORDER BY b.slug").fetchall()
    cutoff, out = H.shift(H.now(), minutes=-SETUP_HOLD_MINUTES), []
    for row in parked:      # few bots are ever parked; each is two indexed reads
        tasks = c.execute("SELECT count(*) n, min(created) oldest FROM tasks WHERE owner=? AND status IN ('open','doing','ready')",
                          ("bot:" + row["slug"],)).fetchone()
        notices = c.execute("SELECT count(*) n, min(created) oldest FROM jobs WHERE bot=? AND state='queued'",
                            (row["slug"],)).fetchone()
        count, what, oldest = (tasks["n"], "task", tasks["oldest"]) if tasks["n"] else (notices["n"], "notice", notices["oldest"])
        if count and oldest < cutoff:
            out.append((row["slug"], row["display_name"] or row["slug"], count, what, oldest))
    return out


def _movable(c, who, online_ids):
    """(id, label) of the online computers `who` may move a bot to: any for the owner, their own for a Bot administrator."""
    me = H.actor_id(who.actor)
    return [(r["id"], r["label"]) for r in c.execute("SELECT id,label,operator FROM runners WHERE revoked_at IS NULL ORDER BY label")
            if r["id"] in online_ids and (who.role == "owner" or r["operator"] == me)]


def runtime_choice(c, bot, runtime, who, computers, harness="", model=""):
    """What Settings and Health show for running `runtime` on this bot's computer, from the one rule
    (backend/readiness.py `can_run`): the answer, a short label, and at most one way out: sign in there (a computer's
    shared sign-in for a runtime that signs in from the browser) or a computer `who` may move it to that can run it."""
    from .readiness import can_run
    answer = can_run(c, bot, runtime, harness=harness, model=model)
    view = {key: answer.get(key) for key in ("can_run", "problem", "fix", "link")}
    view["computer"] = answer.get("computer")
    if answer.get("can_run") is not False:
        return view
    problem = str(answer.get("problem") or "")
    here = (answer.get("computer") or {})
    if here.get("label") and here["label"] not in problem and answer.get("link") != "#/credentials":
        view["problem"] = f"{problem} on {here['label']}"      # the runner's own words name no computer
    view["short"] = ("not installed" if "not installed" in problem else "no key" if answer.get("link") == "#/credentials"
                     else "not on this computer" if "Tico Gateway work" in problem else "sign in")
    if (runtime in model_login.RUNTIMES and str(answer.get("fix") or "").startswith(f"Sign in {runtime} ")):
        view["sign_in"] = {"runner_id": here.get("id"), "runtime": runtime, "computer": here.get("label")}
    view["move"] = next(({"id": rid, "label": label} for rid, label in computers
                         if rid != here.get("id") and can_run(c, bot, runtime, runner=rid, harness=harness, model=model)["can_run"] is True), None)
    return view


# Why the claim holds an idle bot's queued work on purpose (execution.claim_refusal, job_refusal): what Health says, and
# where the fix is. A reason not here is "Not started": a Tico bug (Health Monitor queue_idle).
RUNS, COMPUTERS, BOT_SETTINGS = ("Open Runs", "#/settings", "runs"), ("Open Computers", "#/settings", "devices"), \
    ("Open bot settings", "#/settings", "bots")
HELD = {
    "task_wait": ("Its assigned task waits for a snooze, prerequisite, person or check", RUNS),
    "task_cap": ("Its active task limit is full; waiting work frees a place", BOT_SETTINGS),
    "auto_start_off": ("Automatic assigned-task starts are off for this bot", BOT_SETTINGS),
    "draining": ("Paused, or held while Tico updates: its work starts when it is resumed", BOT_SETTINGS),
    "restarting": ("{computer} is restarting: it starts new work once its running turns end", COMPUTERS),
    "maintaining": ("{computer} is tidying this bot's task worktrees: its work starts when that finishes", COMPUTERS),
    "lane_held": ("{computer} still runs an earlier turn of this bot whose lease lapsed: the next starts when it ends",
                  RUNS),
    "uncertain": ("An interrupted run on the same task or chat waits for review in Runs", RUNS),
    # The runner says the turn holding the shared login is a run Tico has no live record of (backend/login_waits.py).
    "login_holder_unknown": ("Its turn waits for {computer}'s ChatGPT login, held by a run that is not running in "
                             "Tico: restart the runner on {computer} to free it", COMPUTERS),
    "cooling_off": ("Waiting out a usage limit, or a Tico AI Gateway outage, before it tries again", RUNS),
    "readiness_unknown": ("{computer} has not said whether it can run this bot", COMPUTERS),
    "sign_in_rejected": ("Its model sign-in was rejected on {computer}", COMPUTERS),
    "login_upgrade": ("Update {computer} to use one login per AI tool; its bots wait without changing accounts",
                      COMPUTERS),
    # A Tico model runs only where bots run as their own user (execution.claim_refusal): the bot waits, it never fails.
    "not_isolated": ("{computer} can't run Tico Gateway work (it needs Tico's Docker runner): it waits for another "
                     "model or computer", BOT_SETTINGS),
    "isolation_unknown": ("{computer} hasn't said it can run Tico Gateway work: it waits until its runner is updated",
                          COMPUTERS),
    "profile": ("Its subscription profile is not signed in or not reported on {computer}", COMPUTERS),
    "config": ("Its settings cannot be read", BOT_SETTINGS),
    "check_model": ("Its check model does not run on {computer}, or is on hold", BOT_SETTINGS),
    "unreadable": ("Its queued message is one it may not read", RUNS),
}


def _stuck(c, rows, online_ids, who, full, auth=None):
    """Each stuck bot with a reason in plain words and one fix: sign in, move to a computer that can run it,
    or the settings page that can. Only administrators get fixes; a Bot administrator moves only to their own computers.
    A bot stuck in setup is the exception: whoever may manage it gets "Finish with defaults"."""
    from .readiness import own_harness
    if not rows:
        return []
    computers = _movable(c, who, online_ids) if full else []
    configured = {r["slug"]: r["runtime"] or "" for r in c.execute("SELECT slug,runtime FROM bots")}
    out = []
    for row in rows:
        label, reason = row["computer"], row["reason"]
        if reason == "setup":
            manages = auth is not None and auth.bot_manager(c, who, row["bot"])
            out.append({key: row[key] for key in ("bot", "name", "queued", "oldest", "computer", "reason", "since")}
                       | {"why": f"Stuck in setup for {setup_stuck.held_for(row['since'])}",
                          "fix": {**_fix("Finish with defaults"), "finish": {"bot": row["bot"]}} if manages else None})
            continue
        runtime = row["runtime"] or configured.get(row["bot"], "")
        harness = own_harness(c, row["bot"], runtime)     # an Antigravity bot is not judged as Gemini CLI
        fix, move = None, None
        if reason == "no_computer":
            text = "No computer"
            fix = _fix("Choose a computer", "#/settings", "bots")
        elif reason == "computer_offline":
            seen = c.execute("SELECT last_seen FROM runners WHERE id=?", (row["runner_id"],)).fetchone()
            quiet = quiet_minutes(seen["last_seen"] if seen else None)
            text = f"{label} has not checked in for {quiet} minutes" if quiet >= SILENT_MINUTES else f"{label} is offline"
            fix = _fix("Open Computers", "#/settings", "devices")
            if full and runtime:
                move = next(({"id": rid, "label": name} for rid, name in computers if rid != row["runner_id"] and
                                     _can(c, row["bot"], runtime, rid, harness)), None)
        elif reason == "not_ready":
            choice = runtime_choice(c, row["bot"], runtime, who, computers, harness=harness) if runtime else {}
            problem = row.get("problem") or "Not ready"
            problem = str(choice.get("problem") or problem) if choice.get("can_run") is False else problem
            text = problem if label and label in problem else f"{problem} on {label}"
            if choice.get("can_run") is False:
                # Signing in where it is beats moving it; otherwise a computer that can run it, else where the fix is.
                if choice.get("sign_in"):
                    login = choice["sign_in"]
                    fix = {**_fix("Sign in"), "login": {"runner_id": login["runner_id"], "runtime": runtime, "computer": label}}
                else:
                    move = choice.get("move")
                    fix = _fix("Open Credentials", "#/credentials") if choice.get("link") == "#/credentials" \
                        else _fix("Open Computers", "#/settings", "devices")
            else:
                fix = _fix("Open bot settings", "#/settings", "bots")
        elif reason == "limit":
            text = "Over its spending limit"
            fix = _fix("Raise limit", "#/usage")
        elif reason == "updating":
            text = updating_words({**row, "turns": visible_turns(c, row.get("turns") or [], who, auth)})
            fix = _fix("Open Computers", "#/settings", "devices")
        elif reason in HELD:
            words, where = HELD[reason]
            text = words.format(computer=label or "its computer")
            fix = _fix(*where)
        else:
            text = f"Not started after {QUEUE_MINUTES} min"
            fix = _fix("Open Runs", "#/settings", "runs")
        if move:
            fix = {**_fix(f"Move to {move['label']}"), "move": {"bot": row["bot"], "runner_id": move["id"], "computer": move["label"]}}
        out.append({key: row[key] for key in ("bot", "name", "queued", "oldest", "computer", "reason")}
                   | {"why": text[:320], "fix": fix if full else None})
    return out


def holding_turns(c, attempts):
    """The running turns a computer due an update waits for, oldest first: which bot, since when and, unless private,
    which task, so a person can tell a real job from a turn that only waits on something elsewhere (TK-SHNKOY4E)."""
    from .task_privacy import attempt_tasks
    names = {r["slug"]: r["display_name"] or r["slug"] for r in c.execute("SELECT slug,display_name FROM bots")}
    out = []
    for a in attempts:
        ids = sorted(attempt_tasks(c, a["id"]))
        task = c.execute(f"SELECT id,title FROM tasks WHERE id IN ({','.join('?' * len(ids))}) AND NOT private "
                         "ORDER BY updated DESC LIMIT 1", ids).fetchone() if ids else None
        out.append({"run": a["id"], "bot": a["bot"], "name": names.get(a["bot"], a["bot"]), "since": a["since"],
                    "task": task["id"] if task else "", "task_title": task["title"] if task else ""})
    return out


def visible_turns(c, turns, who, auth):
    """`turns` as this caller may see them: a bot whose activity they may not read is "another bot", and a task they
    may not read (private, or hidden by a bot's activity controls: auth.task_sql) is not named. Without `auth`,
    neither is named."""
    hidden = auth.unreadable_bots(c, who) if auth is not None else None
    out = []
    for turn in turns:
        named = hidden is not None and turn["bot"] not in hidden
        readable = bool(named and turn["task"] and c.execute(
            f"SELECT 1 FROM tasks WHERE id=? AND ({auth.task_sql(c, who)})", (turn["task"],)).fetchone())
        out.append({**turn, "name": turn["name"] if named else "",
                    **({} if readable else {"task": "", "task_title": ""})})
    return out


def _turn_words(turn, titled=True):
    title = turn["task_title"] if titled else ""
    on = f" on \u201c{title[:47] + '...' if len(title) > 50 else title}\u201d" if title else ""
    whose = f"{turn['name']}'s turn" if turn["name"] else "another bot's turn"
    return f"{whose}{on} (running since {(turn['since'] or '')[11:16]} UTC)"


# A turn that only watches something elsewhere holds this computer's update for nothing. Stopping it stays a person's
# choice (Runs): Tico never stops it for them.
OUTSIDE_WAIT = "A turn only waiting on something elsewhere can note the wait and end, and pick it up next run."


def updating_words(row, advise=True):
    """Why work waits on a computer that is updating: the update, and the running turns it waits for, by bot, task
    and start. `advise` adds what a turn that only watches something elsewhere should do."""
    to = f" to Tico {row['target']}" if row.get("target") else ""
    if row.get("running"):
        held = row.get("turns") or []
        if len(held) == 1:
            turns = _turn_words(held[0]) + " ends"
        elif held:
            more = f" and {len(held) - 2} more" if len(held) > 2 else ""
            turns = f"{len(held)} running turns end: " + "; ".join(_turn_words(t, titled=False) for t in held[:2]) + more
        else:
            turns = "running turn finishes" if row["running"] == 1 else f"{row['running']} running turns finish"
            turns = "its " + turns
        return f"{row['computer']} is holding new work to update{to}: it starts once {turns}" + (f". {OUTSIDE_WAIT}" if advise else "")
    return f"{row['computer']} is updating{to}"


def _can(c, bot, runtime, runner, harness=""):
    from .readiness import can_run
    return can_run(c, bot, runtime, runner=runner, harness=harness)["can_run"] is True


TOKEN_CACHE_SECONDS = 300
_TOKEN_CACHE = {}   # (database, threshold) -> (monotonic time, answer)


def _token_heavy(c, threshold):
    """(bot, tokens) for bots whose uncached input tokens passed `threshold` in the last day. A run records
    `input_tokens` without its cache reads (runner-rs/src/execute.rs), so cache reads, which are cheap, do not count.
    Both halves filter on `turns.started` (indexed) before summing; the sum still visits every turn of the day
    (about 90 ms at 30,000 turns), so one answer serves every tab's Health poll for five minutes."""
    if not threshold or threshold <= 0:
        return []
    import time
    key = (next((r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main"), ""), threshold)
    hit = _TOKEN_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TOKEN_CACHE_SECONDS:
        return hit[1]
    since = H.shift(H.now(), hours=-RECENT_HOURS)
    answer = [(r["bot"], r["n"]) for r in c.execute(
        "SELECT bot, sum(n) AS n FROM ("
        "SELECT t.bot, coalesce(s.input_tokens,0) AS n FROM turns t "
        "JOIN turn_usage_segments s ON s.turn_id=t.id WHERE t.started>=? "
        "UNION ALL SELECT t.bot, coalesce(t.input_tokens,0) FROM turns t "
        "WHERE t.started>=? AND NOT EXISTS (SELECT 1 FROM turn_usage_segments s WHERE s.turn_id=t.id)) "
        "GROUP BY bot HAVING sum(n)>? ORDER BY sum(n) DESC", (since, since, threshold))]
    _TOKEN_CACHE.clear()
    _TOKEN_CACHE[key] = (time.monotonic(), answer)
    return answer


def _bot_name(c, bot):
    row = c.execute("SELECT display_name FROM bots WHERE slug=?", (bot,)).fetchone()
    return (row["display_name"] if row else "") or bot


def _tokens_words(n):
    return f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.0f}M" if n >= 1e6 else f"{n:,}"


def token_failure(c):
    """{"message", "action"} when the GitHub App itself failed to issue a token in the last day and has not
    succeeded since, else None. A row without an action predates App-only recording (0.2.21 also wrote a bot's
    own missing repository here), so it is not this."""
    health = c.execute("SELECT last_success,last_error,detail_json FROM service_health WHERE service=?",
                       (GITHUB_HEALTH,)).fetchone()
    recent = H.shift(H.now(), hours=-RECENT_HOURS)
    if not (health and health["last_error"] and health["last_error"] > recent
            and health["last_error"] > (health["last_success"] or "")):
        return None
    try:
        detail = json.loads(health["detail_json"] or "{}")
    except ValueError:
        return None
    return {"message": str(detail.get("message") or ""), "action": str(detail["action"])} if detail.get("action") else None


def _github(c, github):
    row = github.row(c) if github else None
    failure = token_failure(c) if row else None
    fix = _fix("Open GitHub settings", "#/settings", "cloud")
    if not row:
        return _check("github", "GitHub", "info", "Not connected. Optional: connect it to keep bot work in your GitHub.", [fix])
    if not row["installation_id"]:
        return _check("github", "GitHub", "warn", "The app is created but not installed on your organization.", [fix])
    if failure is not None:
        return _check("github", "GitHub", "bad",
                      "GitHub would not give a bot a token in the last day" + (": " + failure["message"] if failure["message"] else "."), [fix])
    return _check("github", "GitHub", "ok", f"Connected to {row['org']}.")


def _github_repo_create(c, github):
    """An App without Administration (write) cannot create bot repositories, so every new bot stops for a person.
    The stored flag follows the installation's live permission whenever Tico asks GitHub, so this reads no network."""
    row = github.row(c) if github else None
    if not row or row["administration"]:
        return None
    from .repo_waits import fixes, waits
    waiting = waits(c)
    named = sorted(waiting)
    fix = fixes(c, row, named[0] if len(named) == 1 else "")
    summary = ("Bots BotOps builds cannot get a GitHub repository: the GitHub App lacks Administration (write). "
               "Let BotOps create repositories (turn on Administration for the app, then accept it for the organisation), "
               "or create each bot's repository yourself as an empty private repository.")
    if named:
        summary += " Waiting: " + ", ".join((waiting[bot].get("repository") or bot) for bot in named[:3]) \
                   + ("." if len(named) <= 3 else f"; and {len(named) - 3} more.")
    return _check("github_repo_create", "GitHub can't create bot repositories", "warn", summary,
                  [_fix("Let BotOps create repositories", fix["permissions_url"]),
                   _fix("Create " + fix["repository"] if fix["repository"] else "Create a repository", fix["create_url"]),
                   _fix("Open GitHub settings", "#/integrations")])


def _github_workflows(c, github):
    """A write grant an Owner or admin let run workflows gets a token with Actions: write only once the installation
    has accepted it. The token request records what the installation last said, so this reads no network."""
    from .repo_waits import permissions_url
    from .repositories import workflows_refused, workflows_waiting
    connected = bool(github and github.row(c))
    if waiting := workflows_waiting(c) if connected else []:
        return _check("github_workflows", "Bots can't run workflows yet", "warn",
                      "These bots may run workflows but get only the ordinary write token: the GitHub App's Actions "
                      "permission is not Read and write on the installation. Set it in the App's permissions, then "
                      "accept it for the organisation. Waiting: " + ", ".join(waiting[:3])
                      + ("." if len(waiting) <= 3 else f"; and {len(waiting) - 3} more."),
                      [_fix("Change the App's permissions", permissions_url(c, github.row(c))),
                       _fix("Open GitHub settings", "#/integrations")])
    if refused := workflows_refused(c) if connected else []:
        # The installation reports Actions: Read and write, so the setting is not the cause and is not offered.
        return _check("github_workflows", "GitHub refused workflow tokens", "warn",
                      "These bots may run workflows but get only the ordinary write token: GitHub refused a token "
                      "with Actions: write although the installation reports it. Tico asks again after a minute; "
                      "if this stays, check the installation's repository access and the organisation's Actions "
                      "policy on GitHub. Refused: " + ", ".join(refused[:3])
                      + ("." if len(refused) <= 3 else f"; and {len(refused) - 3} more."),
                      [_fix("Open GitHub settings", "#/integrations")])
    return None


def _slack(c):
    """Only once the owner has pasted tokens: the gateway reports its own state (backend/slack_app.py)."""
    from . import slack_app
    state = slack_app.status(c)
    if not state["configured"]:
        return None
    fix = _fix("Open Slack settings", "#/settings", "cloud")
    if state["state"] == "connected":
        return _check("slack", "Slack", "ok", "Connected.")
    if state["state"] == "waiting":
        return _check("slack", "Slack", "warn", "Tokens saved; the Slack service has not connected yet. "
                      "Is the slack profile on in COMPOSE_PROFILES?", [fix])
    return _check("slack", "Slack", "bad", "Slack is disconnected" + (": " + state["message"] if state["message"] else "."), [fix])


def _backups(config, settings):
    backup = config.get("backup")
    fix = _fix("Backup settings", "https://github.com/ticoteam/tico/blob/main/docs/install.md#backups-and-restore")
    if not isinstance(backup, dict) or not backup.get("mode"):
        return _check("backups", "Backups", "unknown", "This server does not report its backup state.")
    mode = str(backup["mode"]).replace("_", "-")
    last = backup.get("last_replicated_at") or ""
    target = str(backup.get("target_kind") or "")
    key = backup.get("credential_key") if isinstance(backup.get("credential_key"), dict) else {}
    key_here = bool(key.get("present"))                  # a local credential key (/data/credential.key) is in use
    key_copied = bool(key.get("current"))
    files = backup.get("key_files") if isinstance(backup.get("key_files"), dict) else {}
    # github-app.key and slack.key (backend/replication.py): present ones that the backup does not hold as they are now
    uncopied = sorted(name for name, state in files.items() if isinstance(state, dict) and state.get("present")
                      and not state.get("current"))
    if mode == "off":
        return _check("backups", "Backups", "bad",
                      "Backups are off." + (KEY_LOST if key_here else ""), [fix])
    if mode in ("local-only", "local"):
        note = (" The credential key, which opens every saved credential, is only in this server's volumes too."
                if key_here else "")
        if key_here and not key_copied:
            note += " It has not been copied to the backup volume yet."
        if uncopied:
            note += " " + _key_files_words(uncopied) + " not been copied to the backup volume yet."
        if settings.loopback:
            return _check("backups", "Backups", "warn" if (key_here and not key_copied) or uncopied else "info",
                          "Copies stay on this computer. Set a backup bucket in .env to keep copies elsewhere." + note,
                          [fix])
        return _check("backups", "Backups", "warn",
                      "Copies stay on this server only. A lost disk loses everything." + note, [fix])
    if last and last < H.shift(H.now(), hours=-BACKUP_STALE_HOURS):
        return _check("backups", "Backups", "warn", f"Last copy to {target or 'the remote'} was {last}.", [fix])
    if not last:
        return _check("backups", "Backups", "warn", "Set up, but nothing has been copied yet.", [fix])
    if key_here and not key_copied:
        return _check("backups", "Backups", "warn",
                      f"The database is copied to {target or 'a remote'}, but the credential key has not been. A database restored "
                      "without it cannot open any saved credential. Check the server log for the backup warning.", [fix])
    if uncopied:
        return _check("backups", "Backups", "warn",
                      f"The database is copied to {target or 'a remote'}, but {_key_files_words(uncopied)} not been. A "
                      "database restored without them cannot open the GitHub App, directory sync or Slack secrets. Check "
                      "the server log for the backup warning.", [fix])
    return _check("backups", "Backups", "ok", f"Copied to {target or 'a remote'}.")


def _key_files_words(names):
    return " and ".join(names) + (" has" if len(names) == 1 else " have")


SEALED_FIX = ("Copy {file} from the backup location (credential-key/{file}) into the data volume beside hub.sqlite, mode "
              "0600, and restart the server: `docker compose run --rm server restore --force` does it with the database. "
              "If no backup has it, {redo}.")


def _sealed_secrets(c, settings, github):
    """Secrets sealed with a key file beside the database (the GitHub App and directory sync credentials with
    github-app.key, the Slack tokens with slack.key, every credential with credential.key) that this server cannot
    open: a restore or a move that left a key file behind. Read only: nothing here creates a key."""
    import hashlib
    import hmac

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from . import credentials, directory, slack_app

    still_needs = {}         # {key file: rows only it opens}, under KMS

    def opens(key, row, aad, legacy=None):
        # Under KMS a row sealed before TICO_CREDENTIAL_KMS_KEY was set still opens with its key file (`legacy`),
        # and the next read re-seals it with the vault key (credentials.open_or_reseal). Health only looks, and counts
        # such rows so nobody deletes the file while it is still needed.
        for candidate in (key, credentials.legacy_key(legacy) if legacy else None):
            try:
                AESGCM(candidate).decrypt(row["nonce"], row["ciphertext"], aad)
            except Exception:
                continue
            if candidate is not key:
                still_needs[legacy.name] = still_needs.get(legacy.name, 0) + 1
            return True
        return False

    def table(name):
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()

    problems, fixes = [], []
    try:
        app = github.row(c) if github else None
        sources = list(c.execute("SELECT * FROM directory_credentials")) if github and table("directory_credentials") else []
        kms = bool(settings.credential_kms_key and github and github.vault is not None)
        vault = c.execute("SELECT kms_key,wrapped_key FROM credential_keys WHERE id='v1'").fetchone()
        if kms and not vault:
            return None      # the first save makes the vault's key; a Health read must not ask KMS for one
        if app or sources:
            key = github.secret_key(c, create=False)
            lost = []
            legacy = github.key_file() if kms else None
            if app and not (key and opens(key, app, b"tico-github-app:v1", legacy)):
                lost.append("The GitHub App's private key and webhook secret")
            if sources and not (key and all([opens(key, r, directory.AAD + r["source"].encode(), legacy) for r in sources])):
                lost.append("the directory sync credentials" if lost else "The directory sync credentials")
            if lost:
                problems.append(" and ".join(lost) + " cannot be decrypted: " + (
                    "github-app.key is missing." if key is None else
                    "they were sealed with another key (github-app.key from before TICO_CREDENTIAL_KMS_KEY was set, "
                    "which is no longer on the data volume?)." if kms else
                    "github-app.key does not open them."))
                fixes.append(SEALED_FIX.format(file="github-app.key", redo="disconnect GitHub and connect it again, or save "
                                               "the directory sync credentials again, and confirm replacing the lost key: "
                                               "Tico drops the GitHub App and every directory source's credentials sealed "
                                               "with it, and you enter them again" if key is None else
                                               "disconnect GitHub and connect it again, and save the directory sync "
                                               "credentials again"))
        slack = c.execute("SELECT * FROM slack_credentials WHERE id='app'").fetchone() if table("slack_credentials") else None
        if slack:
            # The key the Slack gateway opens them with: the vault's under KMS, else slack.key.
            key = slack_app._key(settings, github.vault if kms else None, c, create=False)
            if not (key and opens(key, slack, slack_app.AAD, slack_app.key_file(settings) if kms else None)):
                problems.append("The Slack tokens cannot be decrypted: " + (
                    "slack.key is missing." if key is None else "the key they were sealed with is not this server's."))
                fixes.append(SEALED_FIX.format(file="slack.key", redo="paste the Slack tokens again (Settings > Slack)"))
        if vault and vault["kms_key"] == credentials.LOCAL and not settings.credential_kms_key:
            path = settings.db_path.parent / credentials.KEY_FILE
            data = path.read_bytes() if path.is_file() else None
            if data is None or not hmac.compare_digest(hmac.new(data, credentials.KEY_CHECK, hashlib.sha256).digest(),
                                                       bytes(vault["wrapped_key"])):
                problems.append("Saved credentials cannot be decrypted: credential.key "
                                + ("is missing." if data is None else "does not belong to this database."))
                fixes.append(SEALED_FIX.format(file="credential.key", redo="add each credential again (Credentials)"))
    except Problem:
        return None          # a KMS key that cannot be reached is the credential_kms check's to report
    fix = [_fix("Restore the key files", "https://github.com/ticoteam/tico/blob/main/docs/install.md#backups-and-restore")]
    needed = ""
    if still_needs:
        counts = []
        for name, n in sorted(still_needs.items()):     # "2 secrets still need github-app.key and 1 still needs slack.key"
            noun = ("secret " if n == 1 else "secrets ") if not counts else ""
            counts.append(f"{n} {noun}still {'needs' if n == 1 else 'need'} {name}")
        needed = (" and ".join(counts) + ": sealed before TICO_CREDENTIAL_KMS_KEY was set, each is re-sealed with the KMS "
                  "key the first time it is used. Keep " + (" and ".join(sorted(still_needs))) + " (and the backup's copy) "
                  "until this clears.")
    if not problems:
        return _check("sealed_secrets", "Stored secrets", "info", needed, fix) if needed else None
    return _check("sealed_secrets", "Stored secrets", "warn", " ".join(problems + fixes + ([needed] if needed else [])), fix)


KEY_LOST = " The credential key, which opens every saved credential, is on this server's disk only."


def _roadmap_suggestions(c):
    """A public roadmap whose suggestions reach no bot, or not the bot the owner chose. It reads the one rule that
    routes suggestions (roadmaps.suggestions_receiver), so this line and the visitor's refusal never disagree, and
    it clears by itself once a bot is receiving them again. Without an enabled label nobody can suggest anything."""
    if not roadmaps.settings(c)['labels']:
        return None
    receiver = roadmaps.settings_view(c)['suggestions_receiver']
    fix = [_fix("Open Public roadmap", "#/settings/roadmap")]
    if not receiver['bot']:
        return _check("roadmap_suggestions", "No bot receives roadmap suggestions", "warn",
                      "Visitors who suggest a feature on your public roadmap are being turned away.", fix)
    why = {'chosen_archived': "is archived", 'chosen_inactive': "isn't active"}.get(receiver['source'])
    if why:
        return _check("roadmap_suggestions",
                      f"The bot chosen for roadmap suggestions {why}; suggestions now go to {receiver['name']}", "warn",
                      "To clear this, make the chosen bot active again, or choose another bot or Automatic.", fix)
    return None


def _signin(settings):
    kind = settings.proxy_kind
    if kind:
        return _check("signin", "Sign-in", "ok", "People sign in through " + PROXIES.get(kind, kind) + ".")
    if settings.loopback or settings.local_signin:
        return _check("signin", "Sign-in", "ok", "Local sign-in on this computer.")
    return _check("signin", "Sign-in", "warn",
                  "No identity proxy is configured for this address, so people cannot sign in safely.",
                  [_fix("Sign-in settings", "https://github.com/ticoteam/tico/blob/main/docs/install.md#sign-in")])


def _server_settings(c, settings):
    checks = []
    if settings.block_external_invites or os.environ.get("TICO_BLOCK_EXTERNAL_INVITES") == "1":
        active = settings.block_external_invites
        checks.append(_check("external_invites", "Outside calendar invites", "ok" if active else "warn",
                             "Blocked for bots through the calendar Tool." if active else
                             "TICO_BLOCK_EXTERNAL_INVITES=1 was requested but is not active. Recreate the server with the "
                             "release's compose.yaml and check its environment.",
                             [] if active else [_fix("Calendar settings", "https://github.com/ticoteam/tico/blob/main/docs/mail.md")]))
    key = settings.credential_kms_key.strip() or os.environ.get("TICO_CREDENTIAL_KMS_KEY", "").strip()
    row = c.execute("SELECT kms_key FROM credential_keys WHERE id='v1'").fetchone()
    stored = row["kms_key"] if row else ""
    if key or (stored and stored != "local"):
        active = bool(key and key == settings.credential_kms_key and stored == key)
        summary = ("The credential key is wrapped with the configured AWS KMS key." if active else
                   "TICO_CREDENTIAL_KMS_KEY was requested but is not active. Recreate the server with the release's "
                   "compose.yaml and check its environment." if key and not settings.credential_kms_key.strip() else
                   "AWS KMS was requested but no credential key has been wrapped yet. Saving a credential will wrap it; "
                   "check the server's AWS credentials, region and KMS permissions if saving fails." if key and not stored else
                   "AWS KMS was requested but credentials still use the local key. The next credential save or read will "
                   "wrap that same key; check the server's AWS credentials, region and KMS permissions if it fails." if key and stored == "local" else
                   "Credentials use a different AWS KMS key than the server configuration. Restore the original "
                   "TICO_CREDENTIAL_KMS_KEY in .env and recreate the server.")
        checks.append(_check("credential_kms", "Credential encryption", "ok" if active else "warn", summary,
                             [_fix("Credential settings", "https://github.com/ticoteam/tico/blob/main/docs/credential-vault.md")]))
    return checks


SILENT_WAIT_HOURS = 1       # a task set waiting on a person this long with nothing said to them is stuck unseen


def silent_waits(c, at=None):
    """Tasks waiting on a person who was never asked: no open question to them in the task's thread and no message
    from the task's owner to them since it started waiting. A bot whose ask failed can still set the task waiting,
    and then nobody knows. Indexed lookups only: Health is read by every open tab."""
    cutoff = H.shift(at or H.now(), hours=-SILENT_WAIT_HOURS)
    return [dict(r) for r in c.execute(
        "SELECT * FROM (SELECT t.id, t.title, t.owner, t.waiting_on, t.conversation_id, t.private, "
        "coalesce((SELECT max(e.ts) FROM task_events e WHERE e.task_id=t.id AND e.field='waiting_on' "
        "AND e.new=t.waiting_on), t.updated) AS since FROM tasks t "
        "WHERE t.waiting_on IS NOT NULL AND t.status='waiting') w WHERE w.since<? "
        "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.to_actor=w.waiting_on AND m.from_actor=w.owner "
        "AND m.created>=w.since AND m.deleted_at IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=w.conversation_id AND m.kind='ask' "
        f"AND m.to_actor=w.waiting_on AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}) "
        "ORDER BY w.since LIMIT 20", (cutoff,))]


def _failed(c):
    since = H.shift(H.now(), hours=-RECENT_HOURS)
    rows = c.execute("SELECT bot,state,finished FROM attempts WHERE state IN ('failed','expired') AND finished>? "
                     "ORDER BY finished DESC LIMIT 5", (since,)).fetchall()
    total = c.execute("SELECT count(*) FROM attempts WHERE state IN ('failed','expired') AND finished>?",
                      (since,)).fetchone()[0]
    return total, [{"bot": r["bot"], "state": r["state"], "at": r["finished"]} for r in rows]


def _fallbacks(c):
    """(on fallback, limited): bots whose latest run in the last day ran on a fallback harness, with that
    harness and when the unbroken run of fallback turns began; and bots a usage limit still holds."""
    from .execution import cooling_off
    since, latest = H.shift(H.now(), hours=-RECENT_HOURS), {}
    for r in c.execute("SELECT bot,coalesce(started,created) AS began,json_extract(result_json,'$.fallback') AS harness "
                       "FROM attempts WHERE state IN ('completed','failed','interrupted') AND finished>? "
                       "ORDER BY bot,finished DESC", (since,)):
        row = latest.setdefault(r["bot"], {"bot": r["bot"], "harness": r["harness"], "since": r["began"], "open": bool(r["harness"])})
        if row["open"] and r["harness"]:
            row["since"] = r["began"]
        else:
            row["open"] = False
    on = sorted(({"bot": row["bot"], "harness": row["harness"], "since": row["since"]}
                 for row in latest.values() if row["harness"]), key=lambda row: (row["since"] or "", row["bot"]))
    limited = [{"bot": r["bot"], "since": r["since"]} for r in
               c.execute("SELECT bot,since FROM bot_status WHERE state='limited' ORDER BY since,bot") if cooling_off(c, r["bot"])]
    return on, limited


def _bot_computers(computers):
    """A bot checking the team's health sees which computers are up, their release and disk, never who operates
    them or their sign-in, container and browser errors."""
    keep = ("id", "label", "online", "last_seen", "platform", "disk")
    return [{**{k: x.get(k) for k in keep}, "update": {k: x["update"].get(k) for k in ("release", "state", "label")}}
            for x in computers]


def _v(version):
    """A release as people write it, `v0.2.3`, whichever way the source spelled it."""
    version = str(version or "")
    return "v" + version if version[:1].isdigit() else version


def storage_settings(c, settings):
    """The storage files go to: the company's own bucket when the owner set one (backend/company_storage.py)."""
    from .blobs import Location
    from .company_storage import active_location
    place = active_location(c)
    return Location(settings, place["bucket"], place["prefix"], place["region"], keys=True) if place else settings


def storage_view(c, settings):
    settings = storage_settings(c, settings)
    usage = c.execute("SELECT COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes FROM "
                      "(SELECT digest,MAX(size) AS size FROM blobs GROUP BY digest)").fetchone()
    row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-copy'").fetchone()
    detail = json.loads(row["detail_json"] or "{}") if row else {}
    row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
    write_detail = json.loads(row["detail_json"] or "{}") if row else {}
    location = settings.blob_bucket + ("/" + settings.blob_prefix if settings.blob_prefix else "")
    credentials = write_detail.get("credentials") if write_detail.get("location") == location else None
    return {"mode": "s3" if settings.blob_bucket else "local", "bucket": settings.blob_bucket,
            **({"credentials": credentials} if credentials in ("keys", "backup", "role", "company") else {}),
            "region": blob_s3.region(settings) or "", "files": usage["files"], "bytes": usage["bytes"],
            "copy": {key: detail.get(key, 0) for key in ("done", "total", "failed")}}


def view(c, who, settings, auth, github, config):
    if who.role != "bot":
        _person(who)
    kind = "owner" if who.role == "owner" else "admin" if auth.bot_admin(who) else who.role
    full = kind in ("owner", "admin")
    online = _online_runners(c)
    online_ids = {r["id"] for r in online}
    computers = _computers(c, online, settings)
    waiting, slow, stuck_rows = _waiting(c, online_ids, settings)
    # A bot parked in setup for a day is stuck too, unless something else already holds it (backend/setup_stuck.py).
    listed = {row["bot"] for row in stuck_rows}
    stuck_rows += [row for row in setup_stuck.stuck(c) if row["bot"] not in listed]
    if not full and stuck_rows:      # someone who is not an administrator hears only about the bots they may read
        readable = auth.bot_accesses(c, who, [row["bot"] for row in stuck_rows])
        stuck_rows = [row for row in stuck_rows if readable.get(row["bot"], auth.FULL)["read"]]
    blocked = _stuck(c, stuck_rows, online_ids, who, full, auth)
    on_fallback, limited = _fallbacks(c) if kind != "bot" else ([], [])
    if not full and (on_fallback or limited):     # the same rule as `stuck`: only bots the caller may read
        readable = auth.bot_accesses(c, who, sorted({row["bot"] for row in on_fallback + limited}))
        on_fallback = [row for row in on_fallback if readable.get(row["bot"], auth.FULL)["read"]]
        limited = [row for row in limited if readable.get(row["bot"], auth.FULL)["read"]]
    failed, failures = _failed(c)
    checks = []
    if full and (listening := _listening(c, settings)):
        checks.append(listening)
    storage = storage_settings(c, settings)
    if full and storage.blob_bucket:
        health = c.execute("SELECT * FROM service_health WHERE service='blob-copy'").fetchone()
        detail = json.loads(health["detail_json"] or "{}") if health else {}
        write_health = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
        write_detail = json.loads(write_health["detail_json"] or "{}") if write_health else {}
        location = storage.blob_bucket + ("/" + storage.blob_prefix if storage.blob_prefix else "")
        if write_detail.get("error") and write_detail.get("location") == location:
            checks.append(_check("blob_storage", "File storage", "warn", write_detail["error"],
                                 [_fix("Storage", "https://github.com/ticoteam/tico/blob/main/docs/files.md#storage")]))
        elif detail.get("running") or detail.get("error"):
            summary = f"Moving files to S3: {detail.get('done', 0)} of {detail.get('total', 0)}"
            if detail.get("error"):
                summary += ". " + detail["error"]
            checks.append(_check("blob_storage", "File storage", "bad" if detail.get("error") else "info", summary))
    if full and not storage.blob_bucket and not settings.loopback and not settings.demo and not settings.rehearsal:
        checks.append(_check("blob_storage", "File storage", "info",
                             "Files are on this computer's disk. A file store (S3) is recommended.",
                             [_fix("Storage", "https://github.com/ticoteam/tico/blob/main/docs/files.md#storage")]))
    # Connection health belongs to the person; only the Team owner may see another person's.
    for row in c.execute("SELECT actor,metadata_json FROM granola_connections"):
        meta = json.loads(row["metadata_json"])
        if meta.get("needs_signin") and (row["actor"] == who.actor or who.role == "owner"):
            checks.append(_check("granola:" + row["actor"], "Granola", "warn",
                                 "Granola needs sign-in again", [_fix("Open Meetings", "#/meetings")]))

    if full:
        notice = config.get("update") or {}
        if notice.get("available"):
            checks.append(_check("version", "Version", "warn",
                                 f"{_v(notice.get('latest'))} is available. You are on {_v(releases.version())}.",
                                 [_fix("Update", click="#new-version"), _fix("What is new", "#/changelog")]))
        else:
            checks.append(_check("version", "Version", "ok", f"Running {_v(releases.version())}, the latest we know of."
                                 if notice.get("latest") else f"Running {_v(releases.version())}."))
        # The image this server started from was not built from its release's tag (backend/flight.py).
        from .flight import last_start
        start = last_start(c)
        state = (start or {}).get("provenance")
        if state in ("mismatch", "modified") and start.get("version") == releases.version():
            checks.append(_check("image", "Release image", "warn",
                                 f"This server's code differs from the published {_v(start['version'])} image."
                                 if state == "modified" else
                                 f"This image says {_v(start['version'])} but was built from commit "
                                 f"{str(start.get('commit'))[:7]}, not the published release."))

    notice = access.bot_access_notice(c) if kind == "owner" else None
    if notice:
        # Stays until the owner dismisses it or saves any bot's access: the change reset every bot to Open.
        checks.append(_check("bot_access", "Bot access", "info", str(notice.get("message") or ""),
                             [_fix("Open bots", "#/settings", "bots")]))

    # A bot.yaml asking to send without approval, or to forward to new addresses, has no effect until a person turns
    # it on in Tico (backend/mail_settings.py); the request itself is worth a look, since a bot or BotOps may write it.
    # Whoever may turn it on sees it: the owner and admins for every bot, a person who manages a bot for that bot only.
    from .mail_settings import alert_text, alerts as mail_alerts
    asking = mail_alerts(c) if full or who.role == "human" else []
    if not full:
        asking = [(bot, extra) for bot, extra in asking if auth.bot_manager(c, who, bot)]
    if asking:
        checks.append(_check("mail_sending", "Mail sending", "warn",
                             "; ".join(alert_text(bot, extra) for bot, extra in asking[:3])
                             + ("." if len(asking) <= 3 else f"; and {len(asking) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots"),
                              _fix("How", "https://github.com/ticoteam/tico/blob/main/docs/mail.md#turning-sending-on")]))
    # A go-live waiting for its computer to get the repository finishes by itself (backend/go_live.py); until then
    # the bot is placed but not on. Seen by the owner and admins, and by a person who manages that bot.
    from .go_live import EXPIRY_DAYS, waiting as waiting_go_lives
    pending = waiting_go_lives(c, who, auth, full)
    if pending:
        checks.append(_check("go_live_waiting", "Going live", "info",
                             "; ".join(f"{bot} is waiting for {computer} to get its repository (since {since})"
                                       for bot, computer, since in pending[:3])
                             + ("" if len(pending) <= 3 else f"; and {len(pending) - 3} more")
                             + f". Tico turns each on when its computer has the repository; after {EXPIRY_DAYS} days "
                             "it stops and tells the person who asked.",
                             [_fix("Open bots", "#/settings", "bots")]))
    unpublished = _unpublished(c) if full else []
    if unpublished:
        checks.append(_check("publish", "Bot history", "warn",
                             "Some bots' history is not on GitHub yet: " + "; ".join(f"{bot} ({why})" for bot, why in unpublished[:3])
                             + ("." if len(unpublished) <= 3 else f"; and {len(unpublished) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots")]))
    denied = _reported(c, DENIED_WARNING) if full else []
    if denied:
        checks.append(_check("claude_denies", "Claude Code permissions", "warn",
                             "; ".join(f"{bot}: {DENIED_WARNING}{rest}" for bot, rest in denied[:3])
                             + ("." if len(denied) <= 3 else f"; and {len(denied) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots")]))
    if full and (denies := _harness_denies_check(c, settings)):
        checks.append(denies)
    if full:
        from .worktrees import current_error, supported
        if supported(c):
            usage, errors = {}, []
            for row in c.execute("SELECT t.owner,l.state,l.detail_json FROM task_links l JOIN tasks t ON t.id=l.task_id WHERE l.kind='worktree' AND l.state<>'removed'"):
                detail = json.loads(row['detail_json'] or '{}')
                bot = H.actor_id(row['owner'])
                usage[bot] = usage.get(bot, 0) + detail.get('size_mb', 0)
                if error := current_error(row['state'], detail):
                    errors.append(bot + ': ' + error)
            if usage:
                checks.append(_check('worktrees', 'Task worktrees', 'warn' if errors else 'ok',
                                     '; '.join(errors[:3]) if errors else '; '.join(f'{bot}: {size:g} MB' for bot, size in sorted(usage.items())),
                                     [_fix('Open tasks', '#/tasks')]))
            # A task waiting for room for a worktree is waiting, not failing (backend/worktree_waits.py). Named by bot
            # and repository only: a private task's title is not everyone's.
            from .worktree_waits import waiting as worktree_waiting
            if waits := worktree_waiting(c):
                def age(since):
                    minutes = int(max(0, (datetime.now(timezone.utc) - H.parse_ts(since)).total_seconds()) // 60)
                    return f'{minutes} min' if minutes < 120 else f'{minutes // 60} h'
                checks.append(_check('worktree_waits', 'Waiting for a worktree', 'info', '; '.join(
                    f"{H.actor_id(w['owner'])}: {w['repo']}, {'setting up' if w['state'] == 'reserved' else 'no room'} "
                    f"for {age(w['created'])} ({w['reason']})" for w in waits[:3])
                    + ('.' if len(waits) <= 3 else f'; and {len(waits) - 3} more.'), [_fix('Open tasks', '#/tasks')]))
            # What freed a place lately, and why (backend/worktrees.py released_event): finished work releasing its
            # tree is the normal way room comes back, so it is said, not warned about.
            since = H.shift(H.now(), hours=-24)
            reasons = {}
            for (reason,) in c.execute("SELECT json_extract(detail_json,'$.reason') FROM events "
                                       "WHERE action='worktree.released' AND ts>=?", (since,)):
                reasons[reason or 'cleanup'] = reasons.get(reason or 'cleanup', 0) + 1
            if reasons:
                checks.append(_check('worktrees_released', 'Worktrees released (24 h)', 'info', '; '.join(
                    f'worktree released: {reason} ({n})' for reason, n in sorted(reasons.items())) + '.',
                    [_fix('Open tasks', '#/tasks')]))
    if full:
        from . import task_loops
        if loops := task_loops.looping(c):
            # A private task is named only to someone who may read it.
            checks.append(_check("task_loops", "Task loops", "warn", "; ".join(
                f"{item['title'] if not item['private'] or H.task_private_readable(c, who.actor, H.task(c, item['task_id'])) else 'A private task'}"
                f" {task_loops.describe(item)}" for item in loops[:3]) + ". Likely a bug; nothing is blocked.",
                [_fix("Open tasks", "#/tasks")]))
    if full and (silent := silent_waits(c)):
        named = [(row, H.task_private_readable(c, who.actor, row)) for row in silent]
        checks.append(_check("silent_waits", "Waiting on people", "warn", "; ".join(
            f"{row['title'] if readable else 'A private task'} waits on "
            f"{(H.human(c, row['waiting_on']) or {}).get('name') or H.actor_id(row['waiting_on'])} but nothing was asked"
            for row, readable in named[:3]) + ("." if len(named) <= 3 else f"; and {len(named) - 3} more."),
            [_fix("Open " + (row["title"][:40] if readable else "task"), "#/task/" + row["id"]) for row, readable in named[:3]]))
    if full and (missing_tools := _missing_tool_credentials(c, online_ids)):
        checks.append(_check("tool_credentials", "Tool credentials", "warn",
                             "Missing Credential: " + "; ".join(f"{tool} on {label}" for tool, label in missing_tools[:5])
                             + ("." if len(missing_tools) <= 5 else f"; and {len(missing_tools) - 5} more."),
                             [_fix("Open Credentials", "#/credentials"), _fix("Open Computers", "#/settings", "devices")]))
    if full and (reserved := reserved_credentials(c)):
        checks.append(_check("reserved_credentials", "Credential names", "warn",
                             "Bots run without these credentials, because Tico keeps their variable names for itself: " + "; ".join(f"{name} ({env}) for {bot}" for name, env, bot in reserved[:5])
                             + ("." if len(reserved) <= 5 else f"; and {len(reserved) - 5} more.")
                             + " Give each a variable name that does not start with TICO_, DYLD_ or LD_.",
                             [_fix("Open Credentials", "#/credentials")]))
    lacking = missing_repositories(c, online_ids) if full else []
    if lacking:
        checks.append(_check("repositories", "Bot repositories", "bad",
                             "Bots cannot run because their computer has no repository for them: "
                             + "; ".join(f"{bot} on {label}: {why}. {repository_fix(c, bot, label, settings.github_owner)[1]}"
                                         for bot, label, why in lacking[:3])
                             + ("." if len(lacking) <= 3 else f"; and {len(lacking) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots")]))
    copies = failed_repository_copies(c, online_ids) if full else []
    if copies:
        checks.append(_check("repository_copies", "Repository copies", "warn",
                             "A computer could not copy a repository its bots work in: "
                             + "; ".join(f"{repository} on {label}: {why}" for label, repository, why in copies[:3])
                             + ("" if len(copies) <= 3 else f"; and {len(copies) - 3} more")
                             + ". Restart that computer's runner to retry now.",
                             [_fix("Open Computers", "#/settings", "devices")]))
    from .app_delivery import health_problems
    for key, summary in health_problems(c, who, auth, online_ids):
        checks.append(_check(key, "Desktop apps", "warn", summary, [_fix("Open Computers", "#/settings", "devices")]))
    fixes = [_fix("Add a computer", "#/settings", "devices")] if full else []
    if not computers:
        checks.append(_check("computers", "Computers", "bad", "No computer is set up. Bots need one to run.", fixes))
    elif not online:
        checks.append(_check("computers", "Computers", "bad", "Every computer is offline.",
                             [_fix("Open Computers", "#/settings", "devices")] if full else []))
    elif len(online) < len(computers):
        off = [x for x in computers if not x["online"]]
        checks.append(_check("computers", "Computers", "warn",
                             f"{len(online)} of {len(computers)} online. Offline: "
                             + ", ".join(x["label"] for x in off) + ".",
                             [_fix("Open Computers", "#/settings", "devices")] if full else []))
    else:
        checks.append(_check("computers", "Computers", "ok", f"{_plural(len(online), 'computer')} online."))

    from .performance import load_status
    for computer in computers:
        load = load_status(c, computer["id"])
        if load["overloaded"]:
            checks.append(_check("runner_load:" + computer["id"], "Load", "warn",
                                 f"{computer['label']}: overloaded now (CPU or memory above 90% for five minutes).",
                                 [_fix("Open Computers", "#/settings", "devices")]))

    if full:
        for computer in computers:
            disk = computer.get("disk") or {}
            total, free = disk.get("total_bytes"), disk.get("free_bytes")
            if isinstance(total, int) and total > 0 and isinstance(free, int) and 1 - free / total > .85:
                checks.append(_check("disk:" + computer["id"], "Disk space", "warn",
                                     f"{computer['label']}: disk is {100 * (1 - free / total):.0f}% full "
                                     f"({free / 1024**3:.1f} GB free). Free space on this computer; for Docker, run "
                                     "`docker image prune -a` to remove unused images. The update retries when space frees.",
                                     [_fix("Open Computers", "#/settings", "devices")]))
            # Diagnostics only: an overload is shown here and on the computer's Performance page; nothing acts on it.
            from .performance import recent_overload, describe
            overload = recent_overload(c, computer["id"])
            if overload:
                checks.append(_check("performance:" + computer["id"], "Load", "warn",
                                     f"{computer['label']}: {'overloaded now' if not overload['ended'] else 'was overloaded'}"
                                     f" ({describe(overload)}).",
                                     [_fix("Open Performance", "#/computers/" + computer["id"] + "/performance")]))
            container = computer.get("container_exec") or {}
            if computer["online"] and container.get("ok") is False:
                checks.append(_check("container_exec:" + computer["id"], "Containers", "warn",
                                     f"{computer['label']} ({computer['operator']}): containers do not start "
                                     f"({container.get('error') or 'failed'}). Restart Docker there; bot container work stalls until then.",
                                     [_fix("Open Computers", "#/settings", "devices")]))
            engine = computer.get("engine") or {}
            if computer["online"] and engine.get("name") == "python":
                checks.append(_check("engine:" + computer["id"], "Runner", "warn",
                                     f"{computer['label']} runs the Python runner, which Tico no longer ships. "
                                     "Update this computer.",
                                     [_fix("Open Computers", "#/settings", "devices")]))
            if computer["online"] and (computer.get("github_auth") or {}).get("ok") is False:
                checks.append(_check("github_auth:" + computer["id"], "GitHub sign-in", "warn",
                                     f"{computer['label']}: GitHub sign-in failed ({computer['github_auth'].get('error') or 'HTTP 401'}). "
                                     "Bots there cannot open pull requests. Check the GitHub connection.",
                                     [_fix("Open GitHub settings", "#/settings", "cloud"), _fix("Open Computers", "#/settings", "devices")]))
            if computer["online"] and (computer.get("browser_launch") or {}).get("ok") is False:
                checks.append(_check("browser_launch:" + computer["id"], "Browser", "warn",
                                     f"{computer['label']}: browser does not start ({computer['browser_launch'].get('error') or 'failed'}). "
                                     "Bots there cannot run browser checks. Update the computer, or install the browser's system libraries.",
                                     [_fix("Open Computers", "#/settings", "devices")]))
        wanted = _wanted_runtimes(providers.load(c, settings))
        signed = _signed_in_runtime(online, wanted)
        rejected = _rejected(computers)
        if not wanted:
            checks.append(_check("models", "Models", "warn", "No AI provider is chosen yet.",
                                 [_fix("Choose providers", "#/settings", "models")]))
        elif online and not signed and rejected:
            checks.append(_check("models", "Models", "bad", _rejected_summary(rejected),
                                 [_fix("Open Credentials", "#/credentials"), _fix("Open Computers", "#/settings", "devices")]))
        elif online and not signed:
            checks.append(_check("models", "Models", "bad", "No online computer is signed in to your model.",
                                 [_fix("Open Computers", "#/settings", "devices")]))
        elif signed and rejected:
            checks.append(_check("models", "Models", "warn", f"{_harness_name(signed)} is signed in on another computer. " + _rejected_summary(rejected), [_fix("Open Credentials", "#/credentials"), _fix("Open Computers", "#/settings", "devices")]))
        elif signed:
            checks.append(_check("models", "Models", "ok", f"{_harness_name(signed)} is signed in."))
        else:
            checks.append(_check("models", "Models", "unknown", "Nothing to check until a computer is online."))

    if full and (unsigned := _needs_sign_in(computers)):
        checks.append(_check("computer_signin", "Computer sign-in", "warn",
                             "; ".join(f"{label}: sign in to {model}" for label, model in unsigned[:5])
                             + ("." if len(unsigned) <= 5 else f"; and {len(unsigned) - 5} more.")
                             + " Sign in from Settings > Computers (BotOps can start it). To have new computers sign in by "
                             "themselves, store the company's model key in Credentials and give it to every computer.",
                             [_fix("Open Computers", "#/settings", "devices"), _fix("Open Credentials", "#/credentials")]))
    # The Tico AI Gateway's word on the company's key, for the owner who manages the connection: 80% of the month's cap
    # used, the cap reached, or the key revoked (backend/gateway_connection.py).
    if kind == "owner" and (gateway := gateway_connection.health(c, settings)):
        checks.append(_check("gateway", "Tico AI Gateway", gateway["status"], gateway["summary"],
                             [_fix("Open Models", "#/settings", "models")]))
    # Tico HQ's nightly billing check, on the Tico team's own install only (the one server holding HQ's read-only staff
    # key): each mismatch, and a check that missed a night or cannot be read, red (backend/billing_check.py).
    if kind == "owner":
        checks.extend(billing_check.health(c, settings))
    if full and (versions := runner_versions.health_check(computers)):
        checks.append(versions)
    silent = silent_computers(c) if waiting else []
    if waiting:
        names = ", ".join(w["name"] for w in waiting[:5])
        # Work piling up behind a computer that stopped checking in is an outage even while another computer is up.
        stopped = "".join(f" {x['label'] if full else 'A computer'} has not checked in for {x['minutes']} minutes "
                          f"with {_plural(x['queued'], 'job')} queued." for x in silent[:3])
        checks.append(_check("waiting", "Bots waiting", "bad" if silent or not online else "warn",
                             f"{_plural(len(waiting), 'bot')} cannot run because their computer is offline"
                             + (f": {names}." if full else ".") + stopped,
                             [_fix("Open Computers", "#/settings", "devices")] if full else []))
    else:
        checks.append(_check("waiting", "Bots waiting", "ok", "Every active bot has a computer that is up."))
    # An offline computer (or none) is "Bots waiting"; this check is everything else, so one cause is one item.
    held = {w["bot"] for w in waiting}
    # A bot stuck in setup has its own check below ("Setup not finished").
    blocking = [s for s in blocked if s["bot"] not in held and s["reason"] != "setup"]
    if blocking:
        # A count: each bot, its reason and its fix are listed once, under `stuck` (Health's Stuck list, Overview).
        bad = any(s["reason"] not in ("slow", "updating", "restarting", "draining", "maintaining", "lane_held", "cooling_off", "task_wait", "task_cap", "auto_start_off")
                  for s in blocking)
        checks.append(_check("queue", "Stuck bots", "bad" if bad else "warn",
                             f"{_plural(len(blocking), 'bot')} with work that is not starting.", []))
    elif slow and all(s["reason"] == "busy" for s in slow):
        checks.append(_check("queue", "Work queueing", "info", "Queued behind other work"
                             + (": " + ", ".join(s["name"] for s in slow[:5]) + "." if full else ".")))
    elif slow:
        checks.append(_check("queue", "Work queueing", "warn",
                             f"{_plural(len(slow), 'bot')} with work waiting more than {QUEUE_MINUTES} minutes"
                             + (": " + ", ".join(s["name"] for s in slow[:5]) + "." if full else "."),
                             [_fix("Open Runs", "#/settings", "runs")] if full else []))
    else:
        checks.append(_check("queue", "Work queueing", "ok", "No work is waiting long."))
    for slug, name, count, what, oldest in unfinished_setup(c) if full else ():
        checks.append(_check("setup:" + slug, "Setup not finished", "warn",
                             f"{name} hasn't finished setup; {_plural(count, what)} waiting since {oldest[:10]}.",
                             [_fix("Finish setup", "#/bot/" + slug)]))
    if full and (heavy := _token_heavy(c, settings.token_alert_input)):
        checks.append(_check("tokens", "Token use", "warn",
                             "; ".join(f"{name}: {_tokens_words(n)} uncached input tokens in 24 h"
                                       for name, n in ((_bot_name(c, bot), n) for bot, n in heavy[:5]))
                             + ("." if len(heavy) <= 5 else f"; and {len(heavy) - 5} more."),
                             [_fix("Open Usage", "#/usage")]))

    # Successful model turns can still make no task progress. Infrastructure and failed-run
    # checks alone miss that loop. Use the same task visibility as the rest of Tico.
    stalled = list(c.execute(
        "SELECT id,title FROM tasks WHERE owner LIKE 'bot:%' AND status IN ('open','doing') "
        f"AND coalesce(private,1)=0 AND ({auth.task_sql(c, who)}) "
        "AND EXISTS (SELECT 1 FROM events e WHERE e.action='task.stall_escalated' "
        "AND e.target=tasks.id AND e.ts>=tasks.updated) ORDER BY updated"))
    if stalled:
        checks.append(_check("stalled_tasks", "Tasks not progressing", "warn",
                             f"{_plural(len(stalled), 'task')} did not progress after repeated automatic runs. "
                             "Recovery was requested; check the diagnosis or record the missing dependency."
                             + (" " + "; ".join(row['title'] for row in stalled[:3]) if full else ""),
                             [_fix("Open tasks", "#/tasks")] if full else []))

    if full and (stuck := watchers.problems(c, online_ids)):
        checks.append(_check("watchers", "Watchers", "warn",
                             "; ".join(f"{bot}/{name} {why}" for bot, name, why in stuck[:3])
                             + ("." if len(stuck) <= 3 else f"; and {len(stuck) - 3} more."),
                             [_fix("Open Runs", "#/settings", "runs")]))
    if full and (mixed := inbox_isolation.violations(c, roster(c))):
        checks.append(_check("inbox", "Inbox bots", "warn",
                             "A computer kept for inbox bots also runs " + "; ".join(
                                 f"{v['label']}: {', '.join(v['others'])}" for v in mixed[:3])
                             + ". The inbox bots' mail key can open every mailbox, so those bots could read it. "
                             "Move them to another computer.",
                             [_fix("Open Computers", "#/settings", "devices")]))
    beside = _member_bots_beside_shared_keys(c) if full else []
    if beside:
        checks.append(_check("member_bots", "Members' bots", "warn",
                             "Bots members created run on a computer that holds keys every bot there receives "
                             "(secrets/_shared.env): " + "; ".join(f"{label}: {', '.join(bots[:3])}" for label, bots in beside[:3])
                             + ". A member's bot instructions could ask a run for them. Move those bots to a computer "
                             "with no shared keys and open only that one to members' bots (Settings > Computers).",
                             [_fix("Open Computers", "#/settings", "devices")]))
    exposed = _mail_key_exposed(c) if full else []
    if exposed:
        checks.append(_check("mail_key", "Mail key", "warn",
                             "The company's Google mail key can be read by every bot on " + ", ".join(exposed[:3])
                             + ". A bot talked into it could read every mailbox. Use a Linux Docker runner with the "
                             "current runner.compose.yaml, where the runner keeps the key to itself (docs/mail.md).",
                             [_fix("Open Computers", "#/settings", "devices")]))
    if full and (suggestions := _roadmap_suggestions(c)):
        checks.append(suggestions)
    if full:
        checks.append(_github(c, github))
        if repo_create := _github_repo_create(c, github):
            checks.append(repo_create)
        if workflows := _github_workflows(c, github):
            checks.append(workflows)
        slack = _slack(c)
        if slack:
            checks.append(slack)
        if sealed := _sealed_secrets(c, settings, github):
            checks.append(sealed)
        checks.append(_check("backups", "Backups", "ok", "Demo data: there is nothing to back up.")
                      if settings.demo else
                      _check("backups", "Backups", "ok", "Rehearsal: backups are off on purpose.")
                      if settings.rehearsal else _backups(config, settings))
        checks.append(_signin(settings))
        checks.extend(_server_settings(c, settings))
    checks.append(_check("failed", "Failed runs", "warn" if failed else "ok",
                         f"{_plural(failed, 'run')} failed in the last day." if failed else "No failed runs in the last day.",
                         [_fix("Open Runs", "#/settings", "runs")] if failed and full else []))
    return {**({"storage": storage_view(c, settings)} if kind == "owner" else {}), "audience": kind, "checks": checks, "attention": sum(1 for x in checks if x["status"] in ("warn", "bad")),
            "computers": computers if full else _bot_computers(computers) if kind == "bot" else [],
            "waiting": waiting if full else [], "slow": slow if full else [], "stuck": blocked,
            "on_fallback": on_fallback, "limited": limited,
            "failures": failures if full or kind == "bot" else [], "checked": H.now(),
            # The sidebar's notice reads the same fresh answer, so the two never disagree.
            "update": config.get("update") or {}}


def note_github_token(store, error=None, action=""):
    """Called around minting a GitHub token so the page can say the last attempt failed. Only a failure of the
    App itself belongs here (its key, installation or permissions), with what to do about it; a bot whose
    repository is not on GitHub yet is that bot's readiness problem."""
    now = H.now()
    detail = json.dumps({"message": error, "action": action}) if error else None
    with store.read() as c:
        current = c.execute("SELECT last_error,detail_json FROM service_health WHERE service=?", (GITHUB_HEALTH,)).fetchone()
        if error:
            if current and current["last_error"] == error and current["detail_json"] == detail:
                return
        elif not current or not current["last_error"] and (current["detail_json"] or "{}") in ("{}", ""):
            return
    with store.transaction() as c:
        if error:
            c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,NULL,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail_json=excluded.detail_json",
                      (GITHUB_HEALTH, error, detail))
        else:   # only a recovery is written, so a healthy turn costs no extra write
            # The failure's message goes too, so nothing reads an old failure after a recovery.
            c.execute("UPDATE service_health SET last_success=?,last_error=NULL,detail_json='{}' WHERE service=? "
                      "AND (last_error IS NOT NULL OR detail_json<>'{}')", (now, GITHUB_HEALTH))


def install(app, store, auth, settings):
    from fastapi import Request

    from . import models as M
    from . import onboarding

    @app.post("/api/v2/health/bot-access/dismiss")
    def dismiss_bot_access(request: Request, body: M.Empty):
        """The owner has read the note about the retired private/routing lists."""
        who = request.state.identity
        def work(c):
            if who.role != "owner":
                raise Problem("forbidden", "Only the owner reads this note", 403)
            access.clear_bot_access_notice(c)
            return {"dismissed": True}
        result = store.mutate(who, request.url.path, request.headers.get("idempotency-key"), body.model_dump(), work)
        return result

    @app.get("/api/v2/health")
    def read(request: Request):
        who = request.state.identity
        if who.role != "bot":
            _person(who)
        github = getattr(request.app.state, "github_app", None)
        with store.read() as c:
            return view(c, who, settings, auth, github, onboarding.config_view(c, settings, who))
