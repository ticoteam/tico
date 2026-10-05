"""The Health page: what needs attention right now, computed from real state on every read.

Nothing here is stored but one note the owner still has to read (backend/access.py). A check is `ok`, `warn`, `bad`, `info` or `unknown`; `unknown` means the thing
that would tell us is not reporting, which is not the same as fine, and `info` is an optional thing
that is not set up (neither fine nor a problem). Each check may carry fixes,
which the page turns into one-click links. People who are not administrators see counts only.
"""

import json
import os
import shlex

from . import access, blob_s3, inbox_isolation, model_login, providers, releases, runner_versions, watchers
from .getting_started import _online_runners, _signed_in_runtime, _wanted_runtimes, _person


def _harness_name(runtime):
    """A runtime as people know it: `claude` is Claude Code (backend/bot_tools.py HARNESS_NAMES)."""
    from .bot_tools import HARNESS_NAMES
    return HARNESS_NAMES.get(runtime) or str(runtime).title()
from .store import H, Problem, readiness_document
from .views import roster

QUEUE_MINUTES = 10          # work that has waited this long on a computer that is up is stuck
BACKUP_STALE_HOURS = 6      # the replica normally trails by seconds
RECENT_HOURS = 24
GITHUB_HEALTH = "github:token"
PROXIES = {"cloudflare": "Cloudflare Access", "aws-alb": "an AWS load balancer", "oidc": "OpenID Connect"}


def _fix(label, href="", tab="", click=""):
    return {"label": label, "href": href, "tab": tab, "click": click}


def _check(key, label, status, summary, fixes=()):
    return {"id": key, "label": label, "status": status, "summary": summary, "fixes": list(fixes)}


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


def _computers(c, runners_online, settings):
    since = {r["id"] for r in runners_online}
    rows, fleet = [], runner_versions.load(c)
    wanted, assigned = providers.runtimes_needed(c, settings)
    default = providers.load(c, settings)["runtime"]
    for r in c.execute("SELECT r.id,r.label,r.last_seen,r.platform,r.readiness_json,coalesce(nullif(h.name,''),r.operator) "
                       "AS operator FROM runners r LEFT JOIN humans h ON h.id=r.operator WHERE r.revoked_at IS NULL "
                       "ORDER BY r.label"):
        document = readiness_document(r["readiness_json"])
        runtimes = document.get("runtimes") or {}
        rows.append({"id": r["id"], "label": r["label"], "online": r["id"] in since, "last_seen": r["last_seen"],
                     "platform": r["platform"] or "", "update": runner_versions.view(fleet.get(r["id"])),
                     "disk": document.get("disk"),
                     "operator": r["operator"], "container_exec": document.get("container_exec"),
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
                                   # A model this computer runs (a bot on it, or the company's default) that it has no
                                   # sign-in for, and that no key from the company gave it either.
                                   "sign_in": bool(v.get("installed")) and v.get("authenticated") == "missing"
                                   and n in model_login.RUNTIMES and (n in assigned.get(r["id"], ()) or n == default)}
                                  for n, v in sorted(runtimes.items())
                                  if v.get("installed") or n in wanted or n in assigned.get(r["id"], ())]})
    return rows


def _mail_key_exposed(c):
    """Labels of the computers that report holding the mail key where their bots can read it."""
    return [r["label"] for r in c.execute("SELECT label,readiness_json FROM runners WHERE revoked_at IS NULL ORDER BY label")
            if readiness_document(r["readiness_json"]).get("mail_key") == "exposed"]


def _unpublished(c):
    """Bots whose local history the runner could not give a GitHub repository (runner/service.py `publish`)."""
    out = []
    hosting = {(a["bot"], a["runner_id"]) for a in c.execute(
        "SELECT a.bot,a.runner_id FROM assignments a JOIN bots b ON b.slug=a.bot WHERE b.state<>'archived'")}
    for r in c.execute("SELECT id,readiness_json FROM runners WHERE revoked_at IS NULL"):
        for bot, row in ((readiness_document(r["readiness_json"]).get("bots")) or {}).items():
            if (bot, r["id"]) not in hosting:
                continue                  # a computer that no longer hosts the bot has nothing to publish for it
            for warning in (row or {}).get("warnings") or []:
                if str(warning).startswith("GitHub history not published: "):
                    out.append((bot, str(warning).split(": ", 1)[1][:160]))
    return sorted(set(out))


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


def _listening(settings):
    from . import listening
    from clients.judge import JudgeError
    dests = listening.destinations(settings, check_questions=False)
    if not dests:
        return None
    try:
        problems = listening.category_problems(dests, listening.question_set(settings))
    except JudgeError:
        problems = ["The listening-item question set could not be loaded. Check registry/questions/listening-item.json."]
    if not problems:
        return None
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


def _waiting(c, online_ids):
    """Active bots whose computer is offline (or that have none while work is queued), with the
    work stuck behind them, and bots whose queued work is old even though their computer is up."""
    cutoff = H.shift(H.now(), minutes=-QUEUE_MINUTES)
    queued = {r["bot"]: (r["n"], r["oldest"]) for r in c.execute(
        "SELECT bot, count(*) n, min(created) oldest FROM jobs WHERE state='queued' GROUP BY bot")}
    assigned = {r["bot"]: (r["runner_id"], r["label"]) for r in c.execute(
        "SELECT a.bot, a.runner_id, r.label FROM assignments a JOIN runners r ON r.id=a.runner_id "
        "WHERE r.revoked_at IS NULL")}
    # A starter bot still waiting for its first setup holds its work on purpose: not slow.
    parked = {r["bot"] for r in c.execute("SELECT bot FROM bot_config WHERE onboarding_state IN ('needs_setup','needs_onboarding')")}
    waiting, slow = [], []
    for bot in c.execute("SELECT slug,display_name FROM bots WHERE state='active' ORDER BY slug"):
        slug = bot["slug"]
        count, oldest = queued.get(slug, (0, None))
        where = assigned.get(slug)
        row = {"bot": slug, "name": bot["display_name"] or slug, "queued": count, "oldest": oldest,
               "computer": where[1] if where else ""}
        if where and where[0] not in online_ids:
            waiting.append({**row, "reason": "computer_offline"})
        elif not where and count and not online_ids:
            waiting.append({**row, "reason": "no_computer"})
        elif where and count and oldest and oldest < cutoff and slug not in parked:
            slow.append({**row, "reason": "slow"})
    return waiting, slow


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
    if mode == "off":
        return _check("backups", "Backups", "bad",
                      "Backups are off." + (KEY_LOST if key_here else ""), [fix])
    if mode in ("local-only", "local"):
        note = (" The credential key, which opens every saved credential, is only in this server's volumes too."
                if key_here else "")
        if key_here and not key_copied:
            note += " It has not been copied to the backup volume yet."
        if settings.loopback:
            return _check("backups", "Backups", "warn" if key_here and not key_copied else "info",
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
    return _check("backups", "Backups", "ok", f"Copied to {target or 'a remote'}.")


KEY_LOST = " The credential key, which opens every saved credential, is on this server's disk only."


def _signin(settings):
    kind = settings.proxy_kind
    if kind:
        return _check("signin", "Sign-in", "ok", "People sign in through " + PROXIES.get(kind, kind) + ".")
    if settings.loopback or settings.local_signin:
        return _check("signin", "Sign-in", "ok", "Local sign-in on this computer.")
    return _check("signin", "Sign-in", "warn",
                  "No identity proxy is configured for this address, so people cannot sign in safely.",
                  [_fix("Sign-in settings", "#/settings", "access")])


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


def _failed(c):
    since = H.shift(H.now(), hours=-RECENT_HOURS)
    rows = c.execute("SELECT bot,state,finished FROM attempts WHERE state IN ('failed','expired') AND finished>? "
                     "ORDER BY finished DESC LIMIT 5", (since,)).fetchall()
    total = c.execute("SELECT count(*) FROM attempts WHERE state IN ('failed','expired') AND finished>?",
                      (since,)).fetchone()[0]
    return total, [{"bot": r["bot"], "state": r["state"], "at": r["finished"]} for r in rows]


def _v(version):
    """A release as people write it, `v0.2.3`, whichever way the source spelled it."""
    version = str(version or "")
    return "v" + version if version[:1].isdigit() else version


def storage_view(c, settings):
    usage = c.execute("SELECT COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes FROM "
                      "(SELECT digest,MAX(size) AS size FROM blobs GROUP BY digest)").fetchone()
    row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-copy'").fetchone()
    detail = json.loads(row["detail_json"] or "{}") if row else {}
    row = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
    write_detail = json.loads(row["detail_json"] or "{}") if row else {}
    location = settings.blob_bucket + ("/" + settings.blob_prefix if settings.blob_prefix else "")
    credentials = write_detail.get("credentials") if write_detail.get("location") == location else None
    return {"mode": "s3" if settings.blob_bucket else "local", "bucket": settings.blob_bucket,
            **({"credentials": credentials} if credentials in ("keys", "backup", "role") else {}),
            "region": blob_s3.region(settings) or "", "files": usage["files"], "bytes": usage["bytes"],
            "copy": {key: detail.get(key, 0) for key in ("done", "total", "failed")}}


def view(c, who, settings, auth, github, config):
    _person(who)
    kind = "owner" if who.role == "owner" else "admin" if auth.bot_admin(who) else "human"
    full = kind != "human"
    online = _online_runners(c)
    online_ids = {r["id"] for r in online}
    computers = _computers(c, online, settings)
    waiting, slow = _waiting(c, online_ids)
    failed, failures = _failed(c)
    checks = []
    if full and (listening := _listening(settings)):
        checks.append(listening)
    if full and settings.blob_bucket:
        health = c.execute("SELECT * FROM service_health WHERE service='blob-copy'").fetchone()
        detail = json.loads(health["detail_json"] or "{}") if health else {}
        write_health = c.execute("SELECT detail_json FROM service_health WHERE service='blob-s3'").fetchone()
        write_detail = json.loads(write_health["detail_json"] or "{}") if write_health else {}
        location = settings.blob_bucket + ("/" + settings.blob_prefix if settings.blob_prefix else "")
        if write_detail.get("error") and write_detail.get("location") == location:
            checks.append(_check("blob_storage", "File storage", "warn", write_detail["error"],
                                 [_fix("Storage", "https://github.com/ticoteam/tico/blob/main/docs/files.md#storage")]))
        elif detail.get("running") or detail.get("error"):
            summary = f"Moving files to S3: {detail.get('done', 0)} of {detail.get('total', 0)}"
            if detail.get("error"):
                summary += ". " + detail["error"]
            checks.append(_check("blob_storage", "File storage", "bad" if detail.get("error") else "info", summary))
    if full and not settings.blob_bucket and not settings.loopback and not settings.demo and not settings.rehearsal:
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

    notice = access.bot_access_notice(c) if kind == "owner" else None
    if notice:
        # Stays until the owner dismisses it or saves any bot's access: the change reset every bot to Open.
        checks.append(_check("bot_access", "Bot access", "info", str(notice.get("message") or ""),
                             [_fix("Open bots", "#/settings", "bots")]))

    unpublished = _unpublished(c) if full else []
    if unpublished:
        checks.append(_check("publish", "Bot history", "warn",
                             "Some bots' history is not on GitHub yet: " + "; ".join(f"{bot} ({why})" for bot, why in unpublished[:3])
                             + ("." if len(unpublished) <= 3 else f"; and {len(unpublished) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots")]))
    if full:
        from .worktrees import supported
        if supported(c):
            usage, errors = {}, []
            for row in c.execute("SELECT t.owner,l.state,l.detail_json FROM task_links l JOIN tasks t ON t.id=l.task_id WHERE l.kind='worktree' AND l.state<>'removed'"):
                detail = json.loads(row['detail_json'] or '{}')
                bot = H.actor_id(row['owner'])
                usage[bot] = usage.get(bot, 0) + detail.get('size_mb', 0)
                if detail.get('error'):
                    errors.append(bot + ': ' + detail['error'])
            if usage:
                checks.append(_check('worktrees', 'Task worktrees', 'warn' if errors else 'ok',
                                     '; '.join(errors[:3]) if errors else '; '.join(f'{bot}: {size:g} MB' for bot, size in sorted(usage.items())),
                                     [_fix('Open tasks', '#/tasks')]))
    if full and (missing_tools := _missing_tool_credentials(c, online_ids)):
        checks.append(_check("tool_credentials", "Tool credentials", "warn",
                             "Missing Credential: " + "; ".join(f"{tool} on {label}" for tool, label in missing_tools[:5])
                             + ("." if len(missing_tools) <= 5 else f"; and {len(missing_tools) - 5} more."),
                             [_fix("Open Credentials", "#/credentials"), _fix("Open Computers", "#/settings", "devices")]))
    lacking = missing_repositories(c, online_ids) if full else []
    if lacking:
        checks.append(_check("repositories", "Bot repositories", "bad",
                             "Bots cannot run because their computer has no repository for them: "
                             + "; ".join(f"{bot} on {label}: {why}. {repository_fix(c, bot, label, settings.github_owner)[1]}"
                                         for bot, label, why in lacking[:3])
                             + ("." if len(lacking) <= 3 else f"; and {len(lacking) - 3} more."),
                             [_fix("Open bots", "#/settings", "bots")]))
    from .subscriptions import bot_subscription, effective, context
    subscription_context = context(c, settings)
    subscription_problems = []
    subscription_access = auth.bot_accesses(c, who)
    for row in c.execute("SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot WHERE b.state<>'archived'"):
        if not full and not (subscription_access.get(row['bot']) or {}).get('see'):
            continue
        if effective(c, row['bot'], subscription_context)[0] is None:
            continue
        subscription = bot_subscription(c, row['bot'], settings, subscription_context)
        if subscription['problem']:
            subscription_problems.append(row['bot'] + ': ' + subscription['problem'])
    if subscription_problems:
        checks.append(_check("subscriptions", "Subscriptions", "warn", "; ".join(subscription_problems[:5]),
                             [_fix("Open Computers", "#/settings", "devices")]))
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
            container = computer.get("container_exec") or {}
            if computer["online"] and container.get("ok") is False:
                checks.append(_check("container_exec:" + computer["id"], "Containers", "warn",
                                     f"{computer['label']} ({computer['operator']}): containers do not start "
                                     f"({container.get('error') or 'failed'}). Restart Docker there; bot container work stalls until then.",
                                     [_fix("Open Computers", "#/settings", "devices")]))
        wanted = _wanted_runtimes(providers.load(c, settings))
        signed = _signed_in_runtime(online, wanted)
        rejected = _rejected(computers)
        if not wanted:
            checks.append(_check("models", "Models", "warn", "No AI provider is chosen yet.",
                                 [_fix("Choose providers", "#/settings", "providers")]))
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
    if full and (versions := runner_versions.health_check(computers)):
        checks.append(versions)
    if waiting:
        names = ", ".join(w["name"] for w in waiting[:5])
        checks.append(_check("waiting", "Bots waiting", "warn" if online else "bad",
                             f"{_plural(len(waiting), 'bot')} cannot run because their computer is offline"
                             + (f": {names}." if full else "."),
                             [_fix("Open Computers", "#/settings", "devices")] if full else []))
    else:
        checks.append(_check("waiting", "Bots waiting", "ok", "Every active bot has a computer that is up."))
    if slow:
        checks.append(_check("queue", "Work queueing", "warn",
                             f"{_plural(len(slow), 'bot')} with work waiting more than {QUEUE_MINUTES} minutes"
                             + (": " + ", ".join(s["name"] for s in slow[:5]) + "." if full else "."),
                             [_fix("Open Runs", "#/runs")] if full else []))
    else:
        checks.append(_check("queue", "Work queueing", "ok", "No work is waiting long."))

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
                             [_fix("Open Runs", "#/runs")]))
    if full and (mixed := inbox_isolation.violations(c, roster(c))):
        checks.append(_check("inbox", "Inbox bots", "warn",
                             "An inbox bot shares a computer with " + "; ".join(
                                 f"{v['label']}: {', '.join(v['inbox'])} beside "
                                 + ", ".join(v["others"] or ["another inbox bot"]) for v in mixed[:3])
                             + ". Its mail key can open every mailbox, so any bot there could read it. "
                             "Add a computer for the inbox bot and move it there.",
                             [_fix("Add a computer", "#/settings", "devices")]))
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
    if full:
        checks.append(_github(c, github))
        slack = _slack(c)
        if slack:
            checks.append(slack)
        checks.append(_check("backups", "Backups", "ok", "Demo data: there is nothing to back up.")
                      if settings.demo else
                      _check("backups", "Backups", "ok", "Rehearsal: backups are off on purpose.")
                      if settings.rehearsal else _backups(config, settings))
        checks.append(_signin(settings))
        checks.extend(_server_settings(c, settings))
    checks.append(_check("failed", "Failed runs", "warn" if failed else "ok",
                         f"{_plural(failed, 'run')} failed in the last day." if failed else "No failed runs in the last day.",
                         [_fix("Open Runs", "#/runs")] if failed and full else []))
    return {**({"storage": storage_view(c, settings)} if kind == "owner" else {}), "audience": kind, "checks": checks, "attention": sum(1 for x in checks if x["status"] in ("warn", "bad")),
            "computers": computers if full else [], "waiting": waiting if full else [], "slow": slow if full else [],
            "failures": failures if full else [], "checked": H.now(),
            # The sidebar's notice reads the same fresh answer, so the two never disagree.
            "update": config.get("update") or {}}


def note_github_token(store, error=None, action=""):
    """Called around minting a GitHub token so the page can say the last attempt failed. Only a failure of the
    App itself belongs here (its key, installation or permissions), with what to do about it; a bot whose
    repository is not on GitHub yet is that bot's readiness problem."""
    now = H.now()
    with store.transaction() as c:
        if error:
            c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,NULL,?,?) "
                      "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail_json=excluded.detail_json",
                      (GITHUB_HEALTH, now, json.dumps({"message": error, "action": action})))
        else:   # only a recovery is written, so a healthy turn costs no extra write
            c.execute("UPDATE service_health SET last_success=?,last_error=NULL WHERE service=? AND last_error IS NOT NULL",
                      (now, GITHUB_HEALTH))


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
        _person(who)
        github = getattr(request.app.state, "github_app", None)
        with store.read() as c:
            return view(c, who, settings, auth, github, onboarding.config_view(c, settings, who))
