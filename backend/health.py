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
import sqlite3
from datetime import datetime, timezone

from . import (
    access,
    billing_check,
    blob_s3,
    gateway_connection,
    hook_relay,
    hosted_updates,
    inbox_isolation,
    login_holds,
    model_login,
    models_preset,
    providers,
    releases,
    replication,
    runner_versions,
    runs,
    setup_stuck,
    sleep_idle,
    usage_limits,
    watchers,
)
from .batch_work import report, storage_error
from .getting_started import _online_runners, _person, _wanted_runtimes, own_bots


def _harness_name(runtime):
    """A runtime as people know it: `claude` is Claude Code (backend/bot_tools.py HARNESS_NAMES)."""
    from .bot_tools import HARNESS_NAMES

    return HARNESS_NAMES.get(runtime) or str(runtime).title()


from .store import H, Problem, readiness_document
from .views import roster
from . import bot_settings as BS

QUEUE_MINUTES = 15  # work that has waited this long on a computer that is up is stuck
PERSON_QUEUE_MINUTES = 2  # a person's own message, sooner: someone is sitting there waiting for the reply
QUEUE_WAITS = (
    "updating",
    "restarting",
    "draining",
    "moving",
    "maintaining",
    "lane_held",
    "cooling_off",
    "task_wait",
    "task_turn_out",
    "handed_back",
    "scoped",
    "task_quarantine",
    "company_limit",
    "backoff",
    "bot_limit",
)
# Keep the stuck threshold compatible with older runners' twenty-minute claim window and fourteen-minute stop.
# New runners hand unstarted work back immediately and cap active turns at five minutes by default.
RESTART_STUCK_MINUTES = 34
SILENT_MINUTES = 10  # a computer this quiet while work is queued for it has stopped, not blinked
NEEDS_YOU_MINUTES = 30  # this quiet, its owner is asked to bring it back
# A backup location that stays behind the database is warned about and then marked bad (replication.behind_warn_seconds,
# replication.BEHIND_BAD_MIN_S). More local files than _unsent_limit() in Litestream's level-0 folder means uploads are not
# getting through. The floor is for the default one-minute sync; a longer interval holds more files between uploads.
BACKUP_UNSENT_FILES = 1000
RECENT_HOURS = 24
FAILED_RUNS = "#/settings/history?view=runs&failed=1"
GITHUB_HEALTH = "github:token"
PROXIES = {
    "cloudflare": "Cloudflare Access",
    "aws-alb": "an AWS load balancer",
    "oidc": "OpenID Connect",
    "owner-link": "the owner sign-in link Tico sends",
}


def _fix(label, href="", tab="", click=""):
    return {"label": label, "href": href, "tab": tab, "click": click}


def _check(key, label, status, summary, fixes=(), items=()):
    return {
        "id": key,
        "label": label,
        "status": status,
        "summary": summary,
        "fixes": list(fixes),
        **({"items": list(items)} if items else {}),
    }


DIAGNOSTIC_LIMIT = 50


def _diagnostic_summary(summary, items):
    lines = "; ".join(item["text"] for item in items[:5])
    return summary + (" " + lines if lines else "") + (f"; and {len(items) - 5} more." if len(items) > 5 else "")


def _queue_items(rows, full, bot_reader):
    items = []
    for row in rows[:DIAGNOSTIC_LIMIT]:
        if bot_reader and row['reason'] == 'no_slot':
            continue  # Capacity is an explained queue, never a "no more slots" alert to a bot.
        oldest = row.get("oldest_queued", row["oldest"])
        minutes = max(0, quiet_minutes(oldest))
        command = "tico run list " + shlex.quote(row["slug"])
        fixes = [row["fix"]] if full and row.get("fix") else []
        if bot_reader:
            fixes = [_fix(command, "#/bot/" + row["bot"])]
        # Check summaries enter support reports; private refusal context stays on the bot's display row.
        why = (
            "Quarantined: queued work waits for the quarantine to clear."
            if row["reason"] == "quarantined"
            else row["why"]
        )
        items.append(
            {
                "bot": row["bot"],
                "name": row["name"],
                "queued": row["queued"],
                "dead": row.get("dead", 0),
                "quarantine_held": row.get("quarantine_held", 0),
                "held": row.get("held", 0),
                "oldest": oldest,
                "oldest_minutes": minutes,
                "reason": row["reason"],
                "text": f"{row['name'][:120]} ({row['slug']}): {_queue_words(row, minutes)}; {why}",
                "fixes": fixes,
                "fix": command if bot_reader else (row.get("fix") or {}).get("label", ""),
            }
        )
    return items


def _queue_words(row, minutes):
    """A bot's queue in one phrase, split as Pipeline splits it: jobs that can start, jobs an interrupted run fences,
    jobs held on purpose, and refusal-count holds. The age is the oldest job named first."""
    words = []
    if row["queued"]:
        words.append(f"{_plural(row['queued'], 'job')} queued, oldest {minutes} min")
    if row.get("dead"):
        age = "" if row["queued"] else f", oldest {minutes} min"
        words.append(f"{_plural(row['dead'], 'job')} behind an interrupted run awaiting review{age}")
    if row.get("held"):
        words.append(f"{row['held']} held on purpose")
    if row.get("quarantine_held"):
        words.append(f"{_plural(row['quarantine_held'], 'job')} held after repeated refusals; resumes after cooldown")
    return "; ".join(words) or "nothing queued"


def _silent_words(computer):
    """The work waiting behind a silent computer, split as Pipeline splits it."""
    words = [f"{_plural(computer['queued'], 'job')} queued"] if computer["queued"] else []
    if computer["dead"]:
        words.append(f"{_plural(computer['dead'], 'job')} behind an interrupted run awaiting review")
    if computer.get("quarantine_held"):
        words.append(
            f"{_plural(computer['quarantine_held'], 'job')} held after repeated refusals; resumes after cooldown"
        )
    return " and ".join(words)


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


def _login_dependencies(c, settings):
    """Active bots' primary and fallback tools that actually use a computer login."""
    from .harnesses import harness_caps, normalize_fallback, resolve_harness
    from .shared_bots import follow

    record, found = providers.load(c, settings), {}
    for row in c.execute(
        f"SELECT a.runner_id,a.bot,{BS.CONFIG} AS config FROM assignments a JOIN bots b ON b.id=a.bot "
        f"AND b.state='active' LEFT JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN}"
    ):
        config = providers.fill(record, follow(c, row['bot'], H._json(row['config'], {}) or {}))
        fallback = normalize_fallback(config.get('fallback')) or {}
        for choice in (config, fallback):
            runtime = choice.get('runtime')
            caps = harness_caps(resolve_harness(choice, runtime), runtime)
            if (
                runtime
                and not gateway_connection.is_tico(choice.get('model'))
                and caps.get('readiness') is not False
                and caps.get('api_keys') != 'bot'
            ):
                found.setdefault(row['runner_id'], set()).add(runtime)
    return found


def _gateway_covered(c, settings, documents):
    """{runner_id: {runtime}}: the runtimes whose active bots' turns the Tico AI Gateway runs on each computer while its
    subscription is not signed in there. `documents` is each computer's readiness document; the rule is
    models_preset.gateway_line's, the claim's own."""
    from .shared_bots import follow

    record, covered = providers.load(c, settings), {}
    for row in c.execute(
        f"SELECT a.runner_id,a.bot,{BS.CONFIG} AS config FROM assignments a JOIN bots b ON b.id=a.bot "
        f"AND b.state='active' LEFT JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN}"
    ):
        config = providers.fill(record, follow(c, row['bot'], H._json(row['config'], {}) or {}))
        if models_preset.gateway_line(c, row['bot'], documents.get(row['runner_id'], {}), settings, config=config):
            covered.setdefault(row['runner_id'], set()).add(config.get('runtime'))
    return covered


def _login_problem(state):
    # A missing/rejected login's detail still needs the sign-in action; failed can mean a folder problem.
    return str(
        state.get("data_problem") or (state.get("detail") if state.get("authenticated") == "failed" else "") or ""
    )[:200]


def runner_route_latency(c, now=None):
    """Merge the last fifteen minutes' histograms; averaging minute percentiles hides stalls."""
    import time
    from .flight import _merge, quantile

    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='flight_requests'").fetchone():
        return None
    routes = {f"POST /api/v2/attempts/{{aid}}/{name}": {} for name in ("inputs", "renew", "events")}
    for row in c.execute(
        "SELECT route,hist FROM flight_requests WHERE created_at>=? AND span=60 AND route IN (?,?,?)",
        (int(now or time.time()) - 15 * 60, *routes),
    ):
        _merge(routes[row[0]], json.loads(row[1] or "{}"))
    slow = {
        route.rsplit("/", 1)[-1]: quantile(hist, 0.95) for route, hist in routes.items() if quantile(hist, 0.95) > 1000
    }
    if slow:
        return _check(
            "runner_route_latency",
            "Runner request latency",
            "warn",
            "Runner requests exceed 1 s at p95 over the last 15 minutes: "
            + ", ".join(f"{name} {ms / 1000:.2f} s" for name, ms in slow.items()),
        )
    return None


def _disk_row(fresh, document):
    """A computer's disk for Health: its newest one-minute sample (`fresh`) when there is one, else its readiness disk,
    with the last clean-up the computer ran by itself (readiness `disk_cleanup`) on it."""
    disk = {k: fresh[k] for k in ("total_bytes", "free_bytes")} if fresh else document.get("disk")
    if isinstance(disk, dict) and isinstance(document.get("disk_cleanup"), dict):
        disk = {**disk, "cleanup": document["disk_cleanup"]}
    return disk


def _computers(c, runners_online, settings, limits=None):
    from .readiness import computer_login, login_warning, SIGNED_OUT

    since = {r["id"] for r in runners_online}
    rows, fleet = [], runner_versions.load(c)
    _, assigned = providers.runtimes_needed(c, settings)
    run_on = _login_dependencies(c, settings)
    # The one default a computer installs for its first sign-in (providers.default_runtime), not every provider's
    # runtime while the company has no default model: the runner installs only that one.
    default = providers.default_runtime(providers.load(c, settings))
    wanted = {default} - {""}
    from .cloud_machines import resting as resting_machines
    from .model_subscriptions import KINDS, RUNNER_CAPABILITIES_SQL, in_effect
    from .performance import latest_disks
    from .read_cache import prime

    resting = resting_machines(c)
    found = c.execute(
        "SELECT r.id,r.label,r.last_seen_at,r.platform,r.readiness,r.capabilities,"
        "coalesce(nullif(h.name,''),r.owner_id) AS owner_id FROM runners_with_state r "
        "LEFT JOIN humans h ON h.id=r.owner_id WHERE r.revoked_at IS NULL ORDER BY r.label"
    ).fetchall()
    prime(c, RUNNER_CAPABILITIES_SQL, {(r["id"],): r for r in found})  # in_effect's runner read, for every computer
    # The disk as of its last minute's sample: readiness sends the disk again only when something else changed.
    disks = latest_disks(c, [r["id"] for r in found])
    # Runtimes whose bots the Gateway runs meanwhile, since their subscription is not signed in (models_preset).
    covered = _gateway_covered(c, settings, {r["id"]: readiness_document(r["readiness"]) for r in found})
    for r in found:
        document = readiness_document(r["readiness"])
        runtimes = document.get("runtimes") or {}
        # A runtime the company's plan signs in on this computer (model_subscriptions.in_effect, what a claim uses)
        # needs no sign-in of its own: its missing login is not a problem.
        plan = {n: KINDS[sub["kind"]]["billing"] for n in runtimes if (sub := in_effect(c, r["id"], n))}
        rows.append(
            {
                "id": r["id"],
                "label": r["label"],
                "online": r["id"] in since,
                "last_seen_at": r["last_seen_at"],
                "resting": r["id"] in resting and r["id"] not in since,
                "platform": r["platform"] or "",
                "update": runner_versions.view(fleet.get(r["id"])),
                # The disk as of its last minute's sample, else its readiness. The last clean-up the computer
                # ran by itself rides on its disk row (runner-rs/src/disk_clean.rs).
                "disk": _disk_row(disks.get(r["id"]), document),
                "owner_id": r["owner_id"],
                "container_exec": document.get("container_exec"),
                "github_auth": document.get("github_auth"),
                "browser_launch": document.get("browser_launch"),
                "engine": document.get("engine"),
                # Only what the company or an assigned bot uses, or what is installed anyway: the
                # other harnesses are not this computer's business, so they are not listed.
                "runtimes": [
                    {
                        "name": n,
                        "installed": bool(v.get("installed")),
                        "needed": n in wanted or n in assigned.get(r["id"], ()),
                        "ready": bool(v.get("installed")) and (v.get("authenticated") == "ready" or n in plan),
                        "rejected": v.get("authenticated") == "rejected" and n not in plan,
                        "plan": plan.get(n, ""),  # "subscription" or "api": the company's
                        "rejected_at": v.get("rejected_at") or "" if v.get("authenticated") == "rejected" else "",
                        "rejected_reason": v.get("rejected_reason") or ""
                        if v.get("authenticated") == "rejected"
                        else "",
                        "credential_source": v.get("credential_source") or "",
                        "login_warning": login_warning(document, n)
                        if n in run_on.get(r["id"], ()) and n not in plan
                        else "",
                        "login_unavailable": (
                            n in run_on.get(r["id"], ())
                            and n not in plan
                            and n not in covered.get(r["id"], ())
                            and computer_login(document, n).get("authenticated") in SIGNED_OUT
                        ),
                        "gateway_covered": n in covered.get(r["id"], ()),
                        "login_problem": _login_problem(computer_login(document, n)),
                        "signable": bool(v.get("installed"))
                        and v.get("authenticated") != "ready"
                        and n in model_login.RUNTIMES
                        and n not in plan,
                        # A model this computer runs that it has no sign-in for, and that no key from the
                        # company gave it either: one its active bots run on, or the company's default while
                        # it has none yet. A default its bots don't use is only `signable`.
                        "sign_in": bool(v.get("installed"))
                        and v.get("authenticated") == "missing"
                        and n in model_login.RUNTIMES
                        and n not in plan
                        and n in assigned.get(r["id"], {default}),
                    }
                    for n, v in sorted(runtimes.items())
                    if v.get("installed")
                    or n in wanted
                    or n in assigned.get(r["id"], ())
                    or n in run_on.get(r["id"], ())
                ],
            }
        )
    from . import org_turns
    from .start_places import summaries, describe

    # Below warning, Health's stuck and queue checks already say why a bot is not starting: the Turns line skips
    # claim_refusal per queued bot. At warning it names quota and readiness waits again.
    classify = org_turns.admission(c)['at_warning']
    counts = summaries(
        c, [row["id"] for row in rows], settings, limits, classify=classify
    )  # one pass, not per computer
    for row in rows:
        row["turn_counts"] = counts[row["id"]]
        row["turn_summary"] = describe(row["turn_counts"])
    return rows


def _mail_key_exposed(c):
    """Labels of the computers that report holding the mail key where their bots can read it."""
    return [
        r["label"]
        for r in c.execute("SELECT label,readiness FROM runners_with_state WHERE revoked_at IS NULL ORDER BY label")
        if readiness_document(r["readiness"]).get("mail_key") == "exposed"
    ]


PUBLISH_WARNING = "GitHub history not published: "  # runner-rs/src/readiness_extra.rs
DENIED_WARNING = "Claude Code denies what AGENTS.md asks for: "  # runner-rs/src/readiness_extra.rs
OLD_DENIED_WARNING = "Claude Code denies what AGENT.md asks for: "  # a computer on a runner from before AGENTS.md
DENIES_WARNING = "Claude Code settings deny: "  # runner-rs/src/readiness_extra.rs
# GitHub writes a bot's access may allow: the command, and the `can` verbs (backend/bot_tools.py) that allow it.
# Only a push: write access to a repository is given for pushing, and says nothing about whether the bot's work opens,
# edits or comments on pull requests or issues. A Release Owner given write to push fixes in Merge denies `gh pr create`
# on purpose, because Tico queues and merges the pull request itself. Whether a bot needs a `gh` command is what its
# AGENTS.md asks for, which the computer compares with the same denies (Health "Claude Code permissions").
GITHUB_WRITES = (("git push", ("write",)),)
WRITE_VERBS = {"write", "pull_requests", "issues"}


def _unpublished(c, withheld=()):
    """Bots whose local history the runner could not give a GitHub repository (runner-rs/src/execute.rs `publish`),
    leaving out those whose repository Tico does not use (`withheld`): a computer may still report its last failure."""
    return [(bot, why) for bot, why in _reported(c, PUBLISH_WARNING) if bot not in withheld]


def _withheld(c, settings):
    """{bot: why} for each bot whose history Tico does not back up to GitHub (repositories.home): the App is installed
    on chosen repositories and not the bot's, or another company made a repository with the bot's name."""
    from .execution import _github_app, _repository_of
    from .repositories import Homes

    configs = {
        r["bot"]: r
        for r in c.execute(f"SELECT bot,{BS.CONFIG} AS config,repo FROM bot_config bc {BS.JOIN}")  # read once
    }
    bots = [r["id"] for r in c.execute("SELECT id FROM bots WHERE state<>'archived' ORDER BY id")]
    app, homes, out = (_github_app(c),), Homes(c), {}
    for bot in bots:
        repository, owner = _repository_of(c, settings, bot, configs.get, app)
        why = homes.why(owner, repository) if repository else ""
        if why:
            out[bot] = why
    return out


def _history_check(c, withheld):
    """One plain line for every bot whose history is kept on its computer only: never a line per bot, never an item
    for a person. Bots work as usual; only the backup is off."""
    from .repositories import CHOSEN_ONLY, HOME_WORDS

    said = [HOME_WORDS[CHOSEN_ONLY]] if CHOSEN_ONLY in withheld.values() else []
    taken = sorted(_bot_name(c, bot) for bot, why in withheld.items() if why != CHOSEN_ONLY)
    if taken:
        said.append(
            "Bot history is not backed up to GitHub for "
            + ", ".join(taken[:3])
            + (f" and {len(taken) - 3} more" if len(taken) > 3 else "")
            + ": a repository with the bot's name was already there, made by someone else, so Tico leaves it alone"
        )
    return _check("bot_history", "Bot history", "info", ". ".join(said) + ".")


def _reported(c, prefix, limit=200):
    """(bot, the rest of the line) for each readiness warning starting with `prefix` from the computer hosting the bot."""
    out = []
    hosting = {
        (a["bot"], a["runner_id"])
        for a in c.execute(
            "SELECT a.bot,a.runner_id FROM assignments a JOIN bots b ON b.id=a.bot WHERE b.state<>'archived'"
        )
    }
    for r in c.execute("SELECT id,readiness FROM runners_with_state WHERE revoked_at IS NULL"):
        for bot, row in ((readiness_document(r["readiness"]).get("bots")) or {}).items():
            if (bot, r["id"]) not in hosting:
                continue  # a computer that no longer hosts the bot has nothing to publish for it
            for warning in (row or {}).get("warnings") or []:
                if str(warning).startswith(prefix):
                    out.append((bot, str(warning)[len(prefix) :][:limit]))
    return sorted(set(out))


def harness_denies(c, settings):
    """(bot, command, repositories) for each GitHub write in GITHUB_WRITES (a push) a bot's access allows on a
    repository other than its own but its Claude Code settings deny. Its own repository is left out, so a bot that
    only reads keeps its denies."""
    from .bot_tools import listing, prime_state
    from .github_app import repo_of
    from .repositories import Reads

    reported = _reported(c, DENIES_WARNING, limit=500)
    bots = sorted({bot for bot, _ in reported})
    # Every bot's configuration, computer, pending requests and tool access, read once for all of them. A failure
    # here falls back to the per-bot path, where each bot's own failure is skipped as it was before.
    try:
        prime_state(c, bots)
        reads = Reads(c, bots)
    except Exception:
        reads = None
    configs = {}
    for part in H.chunks(bots):
        configs.update(
            (r["bot"], r)
            for r in c.execute(
                f"SELECT bot,repo,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot IN ({','.join('?' * len(part))})",
                part,
            )
        )
    out = []
    for bot, rest in reported:
        prefixes = [p.strip() for p in rest.split(",") if p.strip()]
        try:
            tools = listing(c, settings, bot, reads=reads)["tools"]
        except Exception:
            continue
        row = configs.get(bot)
        own = (
            str(repo_of((row["repo"] if row else "") or "bot-" + (H.bot(c, bot) or {}).get("slug", bot), "owner") or "")
            .split("/")[-1]
            .lower()
        )
        mode = H._json(row["config"] if row else None, {}).get("repo_access_mode", "own")
        github = [
            t
            for t in tools
            if str(t.get("service") or "").lower() in ("github", "github-app")
            and t.get("id") != "repo"
            and "own repository" not in str(t.get("detail") or "")
            and str((t.get("scope") or {}).get("repo") or t.get("identity") or "").split("/")[-1].lower() != own
        ]
        declared = [t for t in github if not str(t.get("id") or "").startswith("github-extra-")]
        reads_only = not any(WRITE_VERBS & set(t.get("can") or []) for t in declared)
        # Write on every repository ('all', write by default) says nothing about this bot; a repository a person
        # chose for it with write does. A bot.yaml declaring no GitHub write means its denies are intended.
        granted = [t for t in github if t in declared or mode == "chosen" or not reads_only]
        for command, verbs in GITHUB_WRITES:
            if not any(command == p or command.startswith(p + " ") for p in prefixes):
                continue
            repos = sorted(
                {
                    str((t.get("scope") or {}).get("repo") or t.get("identity") or "")
                    for t in granted
                    if set(verbs) & set(t.get("can") or [])
                }
                - {""}
            )
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
    lines = [
        f"{_bot_name(c, bot)} may write to {', '.join(sorted(repos)[:2])}{' and more' if len(repos) > 2 else ''}, "
        f"but its Claude Code settings deny {', '.join(commands)}"
        for bot, (commands, repos) in sorted(by_bot.items())
    ]
    return _check(
        "harness_denies",
        "Harness permissions",
        "warn",
        "; ".join(lines[:3])
        + ("" if len(lines) <= 3 else f"; and {len(lines) - 3} more")
        + ". Its work stops there. Remove the deny from .claude/settings.json in its repository, "
        "or take away the access.",
        [_fix("Open bots", "#/settings", "bots")],
    )


def _missing_tool_credentials(c, online_ids):
    """Tool and Computer names only, for online assignments with effective credentials missing."""
    from .bot_tools import granted, service_name
    from .credentials import prime_granted

    out = set()
    online = [
        row
        for row in c.execute(
            "SELECT a.bot,r.id,r.label,r.readiness FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
            "JOIN bots b ON b.id=a.bot WHERE b.state<>'archived' AND r.revoked_at IS NULL"
        )
        if row["id"] in online_ids
    ]
    prime_granted(c, ["bot:" + row["bot"] for row in online])  # every bot's grants in one read, not one each
    for row in online:
        report = (readiness_document(row["readiness"]).get("bots") or {}).get(row["bot"]) or {}
        for tool in granted(c, row["bot"], report.get("tools") or []):
            if tool.get("credential") == "missing":
                out.add((service_name(tool.get("service")), row["label"]))
    return sorted(out)


def reserved_credentials(c, who=None, admins=(), auth=None):
    """(credential name, variable, bot) for each credential a bot holds under a name Tico reserves. The runner leaves
    such a credential out of the bot's turns, so the bot runs without it until someone renames the variable. Given
    `who`, never a key of a tool they may not see (backend/api_tools.py `visible`)."""
    from clients.access_entry import RESERVED_ENV, RESERVED_PREFIXES
    from .credentials import effective_grant

    hidden = set()
    if who is not None:
        from .api_tools import hidden_keys

        hidden = hidden_keys(c, who, admins, auth)
    out = []
    for row in c.execute(
        "SELECT DISTINCT v.id,v.name,v.env,g.subject FROM credentials v JOIN credential_grants g "
        "ON g.credential_id=v.id WHERE g.subject LIKE 'bot:%' AND g.revoked_at IS NULL "
        "AND v.ciphertext IS NOT NULL AND v.env<>'' ORDER BY v.name"
    ):
        if (
            (row["env"] in RESERVED_ENV or row["env"].startswith(RESERVED_PREFIXES))
            and row["id"] not in hidden
            and effective_grant(c, row["id"], row["subject"])
        ):
            out.append((row["name"], row["env"], H.actor_id(row["subject"])))
    return out


def missing_repositories(c, online_ids):
    """(bot, computer, why) for the active bots whose online computer says it has no repository for them.
    `why` is the runner's own sentence naming the cause (not on GitHub, GitHub refused, a clone failed)."""
    out = []
    for r in c.execute(
        "SELECT a.bot,r.id,r.label,r.readiness FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
        "JOIN bots b ON b.id=a.bot WHERE b.state='active' AND r.revoked_at IS NULL ORDER BY a.bot"
    ):
        report = (readiness_document(r["readiness"]).get("bots") or {}).get(r["bot"])
        if r["id"] in online_ids and isinstance(report, dict) and report.get("repository_present") is False:
            why = next((p for p in report.get("problems") or [] if "repositor" in p.lower()), "Missing bot repository")
            out.append((r["bot"], r["label"], why))
    return out


def failed_repository_copies(c, online_ids):
    """(computer, repository, why) for the repositories an online computer could not copy to its workspace: the
    runner's own words, git's reason included, so nobody has to guess between access, network and disk."""
    from .repositories import computer_repositories

    # One registry scan for every computer, shared with the computer cards in this snapshot.
    by_repos = computer_repositories(c)
    out = []
    for r in c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL ORDER BY label"):
        if r["id"] not in online_ids:
            continue
        rows = by_repos.get(r["id"])
        for row in rows if isinstance(rows, list) else ():
            if isinstance(row, dict) and row.get("state") == "failed" and row.get("full_name"):
                out.append((r["label"], str(row["full_name"]), str(row.get("error") or "the copy failed")[:500]))
    return out


def _listening(c, settings):
    """A listening that is on with a source whose last read was blocked, limited or failed, or with posts waiting
    unscored (decisions are off, or a spend cap is reached). Nothing listening, or all of it well: no check."""
    from . import listening

    trouble, waiting = listening.trouble(c), listening.stalled(c)
    if not trouble and not waiting:
        return None
    words = {"blocked": "was blocked", "rate_limited": "was rate limited", "error": "failed"}
    lines = [
        f"{row['name']}: {listening.site_label(row) or row['key']} {words[row['status']]}"
        + (f" ({row['last_error']})" if row["last_error"] else "")
        for row in trouble
    ]
    lines += [f"{name}: posts are waiting to be scored" for name in waiting]
    summary = "; ".join(lines[:5])
    if len(lines) > 5:
        summary += f"; and {len(lines) - 5} more"
    return _check("listening", "Listening", "warn", summary, [_fix("Listening", "#/listening")])


def repository_fix(c, bot, label, github_owner=""):
    """The original repository and the clone command on the computer that is missing it."""
    from .shared_bots import declared, follow
    from .store import repo_url

    config = follow(c, bot, declared(c, bot))
    source = config.get("shared_from") or bot
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source,)).fetchone()
    repo = str(
        (row["repo"] if row else "")
        or config.get("repo")
        or "emp-" + ((config.get("name") if source == bot else (H.bot(c, source) or {}).get("slug")) or source)
    )
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    address = repo_url(repo, (app["org"] if app else "") or github_owner) or config.get("repo_url") or repo
    repository = str(address).rstrip("/").removesuffix(".git").removeprefix("https://github.com/")
    row = c.execute(
        "SELECT r.readiness FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?", (bot,)
    ).fetchone()
    report = ((readiness_document(row[0]).get("bots") or {}).get(bot) or {}) if row else {}
    path = report.get("repository") or "<projects>/" + repo.rstrip("/").split("/")[-1].removesuffix(".git")
    command = f"gh repo clone {shlex.quote(repository)} {shlex.quote(path)}"
    return repository, f"Run `{command}` on {label}, or ask BotOps"


def _member_bots_beside_shared_keys(c):
    """Computers whose `_shared.env` holds keys every bot there receives, and that also host bots members
    created: a member's bot instructions could ask a run for them."""
    runners = [
        r
        for r in c.execute("SELECT id,label,readiness FROM runners_with_state WHERE revoked_at IS NULL ORDER BY label")
        if readiness_document(r["readiness"]).get("shared_env")
    ]
    out, members = [], {}
    if runners:
        marks = ",".join("?" * len(runners))
        for row in c.execute(
            "SELECT a.runner_id,a.bot FROM assignments a JOIN bot_config bc ON bc.bot=a.bot "
            f"WHERE a.runner_id IN ({marks}) AND bc.created_by LIKE 'human:%'",
            [r["id"] for r in runners],
        ):
            if _made_by_member(c, row["bot"]):
                members.setdefault(row["runner_id"], []).append(row["bot"])
    for runner in runners:
        found = members.get(runner["id"])
        if found:
            out.append((runner["label"], found))
    return out


def _made_by_member(c, bot):
    from .auth import Auth

    return Auth.member_bot_row(c, bot)


def _rejected(computers):
    """Online computers whose wanted harness refused its key or login: (computer, runtime, when, why, credential source)."""
    return [
        (x["label"], r["name"], r["rejected_at"], r["rejected_reason"], r["credential_source"])
        for x in computers
        if x["online"]
        for r in x["runtimes"]
        if r["rejected"] and r["needed"]
    ]


def _needs_sign_in(computers):
    """(computer, model CLI) for online computers that have to be signed in to a model they run, by hand or BotOps.
    A model whose bots the Tico AI Gateway runs meanwhile (`gateway_covered`) is not one of them."""
    return [
        (x["label"], providers.RUNTIME_LABELS.get(r["name"], r["name"]))
        for x in computers
        if x["online"]
        for r in x["runtimes"]
        if r["sign_in"] and not r["gateway_covered"]
    ]


def _rejected_summary(rows):
    """What to do about it; the reason is the harness's own sanitized words, never the key."""
    first = ", ".join(sorted({f"{name} on {label}" for label, name, _, _, _ in rows}))
    when, why = rows[0][2], rows[0][3]
    sources = {row[4] for row in rows}
    fix = (
        " Replace the model key in Tools > Keys."
        if sources == {"credentials"}
        else " Update the computer-local key or sign in again from Settings > Computers."
        if sources == {"computer"}
        else " If the model key is stored in Tools > Keys, replace it there. Otherwise update the "
        "computer-local key or sign in again from Settings > Computers."
    )
    return (
        f"Sign-in rejected for {first}"
        + (f" at {_clock(when)}" if when else "")
        + (f": {why}" if why else ".")
        + fix
        + " It takes no work that needs it until then."
    )


def silent_computers(c, minutes=SILENT_MINUTES):
    """Computers that have not checked in for `minutes` while work is queued for their active bots, longest silence
    first: [{id, label, operator, last_seen, minutes, queued, dead, quarantine_held, bots}]. `queued` can start,
    `dead` waits behind an interrupted run nobody reviewed, and `quarantine_held` waits for refusal cooldown, as
    Pipeline splits them. A computer that never checked in is setup, not this. A
    cloud machine stopped when idle is not silent: it starts when its work can run (cloud_machines.resting)."""
    from .cloud_machines import resting as resting_machines
    from .execution import queued_task_sql, unfenced_sql
    from .task_quarantine import free_sql

    resting = resting_machines(c)
    quarantine_free = free_sql(queued_task_sql())
    rows = c.execute(
        f"SELECT r.id, r.label, r.owner_id, r.last_seen_at, "
        f"sum(CASE WHEN ({quarantine_free}) AND NOT ({unfenced_sql()}) THEN 1 ELSE 0 END) dead, "
        f"sum(CASE WHEN NOT ({quarantine_free}) THEN 1 ELSE 0 END) quarantine_held, "
        "count(j.id) n, group_concat(DISTINCT a.bot) bots "
        "FROM runners_with_state r JOIN assignments a ON a.runner_id=r.id JOIN bots b ON b.id=a.bot AND b.state='active' "
        "JOIN jobs j ON j.bot_id=a.bot AND j.state='queued' AND j.held=0 "
        "LEFT JOIN messages m ON m.id=j.message_id LEFT JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE r.revoked_at IS NULL AND r.last_seen_at < ? GROUP BY r.id ORDER BY r.last_seen_at",
        (H.shift(H.now(), minutes=-minutes),),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "owner_id": r["owner_id"],
            "last_seen_at": r["last_seen_at"],
            "minutes": quiet_minutes(r["last_seen_at"]),
            "queued": r["n"] - r["dead"] - r["quarantine_held"],
            "dead": r["dead"],
            "quarantine_held": r["quarantine_held"],
            "bots": sorted(r["bots"].split(",")),
        }
        for r in rows
        if r["id"] not in resting
    ]


def quiet_minutes(last_seen):
    """Whole minutes since `last_seen_at`; 0 when it cannot be read."""
    seen = H.parse_ts(last_seen)
    return int((datetime.now(timezone.utc) - seen).total_seconds() // 60) if seen else 0


def _fenced_queue_reason(row, refused, first, person):
    """Name the interrupted-run fence when it is the only reason this queue cannot start."""
    if not row["queued"] and row["dead"] and refused in (None, 'lane_held'):
        return "uncertain", None, False
    return refused, first, person


def _claimable(c, settings, runner_id, slug, limits, **kw):
    """execution.bot_claimable, or None when a check of the claim raised. The claim passes such a bot over or holds the
    job, and Health names it (claim_holds.check_failures, the held job's item), so the bot is not explained here and
    the page still renders. A storage error still fails the read."""
    from .execution import bot_claimable

    try:
        return bot_claimable(c, settings, runner_id, slug, limits, **kw)
    except Exception as exc:
        if storage_error(exc):
            raise
        report("health claim check", slug, exc, logger="tico.health")
        return None


def queue_counts(c, bots, now=None):
    """Each bot's queue split over every job (backend/bot_rows.py `job_queues` for a reader), with the oldest queued,
    dead and quarantine-held job's time, so the queued count Health shows a bot is the one its page shows an operator
    who may read all of that bot's jobs."""
    from .bot_rows import queue_job_rows, tally_queues

    return tally_queues(bots, queue_job_rows(c, bots), now, None)


def _waiting(c, online_ids, settings=None, limits=None, loads=None):
    """Active bots whose computer is offline (or that have none while work is queued), with the
    work stuck behind them, and bots whose queued work is old even though their computer is up.
    The third list is every bot with queued work that is not starting, with the computer's own
    reason (`_stuck`). An idle bot's reason is the claim's own (execution.bot_refusal); only work the
    claim has no reason to hold is "slow", a Tico bug, and only once the job the claim would take is old: an old held job
    beside a young free one is neither."""
    from .execution import prime_claimables, queued_reasons

    cutoff = H.shift(H.now(), minutes=-QUEUE_MINUTES)
    person_cutoff = H.shift(H.now(), minutes=-PERSON_QUEUE_MINUTES)
    # Each bot's queue is split as its page splits it (queue_counts), so the counts match the bot's page:
    # `queued` can start, `dead` waits behind an interrupted run nobody reviewed, `quarantine_held` waits for refusal
    # cooldown, and `held` waits on purpose
    # (backend/wake_presets.py). Which bots are looked at is every job the claim reads, held ones never: a held job
    # behind an interrupted run counts as dead, and work held on purpose is not stuck.
    split = queue_counts(c, [r["id"] for r in c.execute("SELECT id FROM bots")])
    queued = {
        r["bot_id"]: (r["n"], r["oldest"])
        for r in c.execute(
            "SELECT bot_id, count(*) n, min(created_at) oldest FROM jobs WHERE state='queued' AND held=0 GROUP BY bot_id"
        )
    }
    asked = {
        r["bot_id"]: r["oldest"]
        for r in c.execute(
            "SELECT j.bot_id, min(j.created_at) oldest FROM jobs j JOIN messages m ON m.id=j.message_id "
            f"WHERE j.state='queued' AND {H.in_person_name_sql()} GROUP BY j.bot_id"
        )
    }
    assigned = {
        r["bot"]: (r["runner_id"], r["label"], r["readiness"])
        for r in c.execute(
            "SELECT a.bot, a.runner_id, r.label, r.readiness FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
            "WHERE r.revoked_at IS NULL"
        )
    }
    # A starter bot still waiting for its first setup holds its work on purpose: not slow (`unfinished_setup` warns
    # once the hold is old).
    parked = {
        r["bot"]
        for r in c.execute("SELECT bot FROM bot_config WHERE onboarding_state IN ('needs_setup','needs_onboarding')")
    }
    # A bot in the middle of a turn has its next message waiting behind it: busy, not stuck (partial index).
    from . import org_turns

    # Admission is enough for the company-stop gate: a full snapshot classifies every queued bot and held the
    # writer for seconds on a live install. Detailed wait counts stay on the company-turns check below.
    company_stopped = settings and org_turns.admission(c)['at_stop']
    busy, running, turns = set(), {}, {}
    for r in c.execute(
        "SELECT id,bot_id,runner_id,coalesce(started_at,created_at) since FROM attempts "
        "WHERE state IN ('leased','running') ORDER BY since"
    ):
        busy.add(r["bot_id"])
        running[r["runner_id"]] = running.get(r["runner_id"], 0) + 1
        turns.setdefault(r["runner_id"], []).append(r)
    # A computer whose places are all in use, or that holds as many claimed turns as it may lease, holds the rest back on
    # purpose: queued, not stuck.
    from . import start_places

    held_now, live_now = start_places.held_totals(c), start_places.live_totals(c)
    full = {
        r["id"]
        for r in c.execute("SELECT * FROM runners_with_state WHERE revoked_at IS NULL")
        if held_now.get(r["id"], 0) >= start_places.turn_limit(r)
        or live_now.get(r["id"], 0) >= start_places.live_limit(r)
    }
    # A computer due an update keeps claiming for a while, then claims nothing until its running turns end and it
    # updates (runner-rs follow.rs blocks_claims): its queued work waits on that, not on a Tico bug.
    updating = {
        r["runner_id"]: r["update_target"]
        for r in c.execute(
            "SELECT runner_id,update_target FROM runner_versions WHERE update_state IN ('waiting','updating')"
        )
    }
    # A requested Restart holds new work while running turns finish or reach the drain cap; older runners first
    # keep claiming for a while. Every heartbeat names the restart until it happens.
    restarting = {
        r["id"]
        for r in c.execute("SELECT id FROM runners_with_state WHERE restart_since IS NOT NULL AND revoked_at IS NULL")
    }
    documents = {}  # one parse per computer, and only for bots with queued work: Health is polled by every tab

    def report_for(slug, where):
        if not where:
            return {}
        if where[0] not in documents:
            documents[where[0]] = readiness_document(where[2])
        document = documents[where[0]]
        report = (document.get("bots") or {}).get(slug)
        if not isinstance(report, dict):
            return {}
        # A sign-in the Tico AI Gateway covers does not hold the turn (the claim's rule, models_preset.waiting): the
        # report reads ready, so an old queue is named with the claim's own reason below, not "login required".
        return models_preset.as_served(c, slug, document, report, settings) if report.get("ready") is False else report

    holding = {}

    def held(runner_id):  # one read per computer, however many of its bots wait on its update
        if runner_id not in holding:
            holding[runner_id] = holding_turns(c, turns.get(runner_id, []))
        return holding[runner_id]

    # A turn waiting for its shared login is busy, not stuck, unless what holds the login is no run Tico knows.
    from .login_waits import unknown_holder

    orphaned = {r["bot_id"] for rows in turns.values() for r in rows if unknown_holder(c, r["id"])}
    waiting, slow, stuck = [], [], []
    # The whole queue, not the next three jobs: queued_reasons reads every queued message.
    aged_bots = [slug for slug in queued if slug in assigned]
    prime_claimables(c, aged_bots, first=None)
    # Bots on a cloud machine (backend/cloud_machines.py): one stopped for want of work is not offline.
    from .cloud_machines import GOING, STALE, homes, problem_words

    on_cloud = homes(c)

    def queue_reasons(runner_id, slug, ignore_refusals=()):
        groups = {}
        for job, why in queued_reasons(
            c, settings, runner_id, slug, limits, loads=loads, ignore_refusals=ignore_refusals
        ):
            key = why or "slow"
            group = groups.setdefault(key, {"reason": key, "queued": 0, "oldest": job["created_at"]})
            group["queued"] += 1
            group["oldest"] = min(group["oldest"], job["created_at"])
        return list(groups.values())

    for bot in c.execute(
        "SELECT id,slug,display_name,state FROM bots WHERE state IN ('active','paused','quarantined') ORDER BY slug"
    ):
        slug = bot["id"]
        count, oldest = queued.get(slug, (0, None))
        where = assigned.get(slug)
        shown = split.get(slug, {})
        row = {
            "bot": slug,
            "slug": bot["slug"],
            "name": bot["display_name"] or bot["slug"],
            "queued": shown.get("queued", 0),
            "dead": shown.get("dead", 0),
            "held": shown.get("held", 0),
            "quarantine_held": shown.get("quarantine_held", 0),
            "oldest": shown.get("oldest_queued_at") or shown.get("oldest_dead_at") or shown.get("oldest_quarantine_at"),
            "computer": where[1] if where else "",
        }
        if bot["state"] != "active":
            if count:
                stuck.append({**row, "reason": bot["state"], "runner_id": where[0] if where else "", "runtime": ""})
            continue
        if slug in orphaned and where and where[0] in online_ids:
            stuck.append({**row, "reason": "login_holder_unknown", "runner_id": where[0], "runtime": ""})
            continue
        report = report_for(slug, where) if count else {}
        cloud = on_cloud.get(slug)
        if cloud and (not where or where[0] not in online_ids or cloud["state"] in GOING):
            if count and cloud["problem"]:
                stuck.append(
                    {
                        **row,
                        "reason": "cloud_machine",
                        "runner_id": where[0] if where else "",
                        "runtime": "",
                        "problem": problem_words(cloud["problem"]),
                    }
                )
            elif count and oldest and oldest < cutoff and slug not in parked:
                # An onboarding bot's queue waits on purpose (unfinished_setup says so), not on the machine.
                if where and cloud["state"] in GOING:
                    claimable = (
                        ('company_limit', None, False)
                        if company_stopped
                        else _claimable(c, settings, where[0], slug, limits, loads=loads, ignore_refusals=STALE)
                    )
                    if claimable is None:
                        continue
                    refused, first, person = _fenced_queue_reason(row, *claimable)
                    if refused:
                        row["queue_reasons"] = queue_reasons(where[0], slug, ignore_refusals=STALE)
                        # Ready jobs ("slow") and intentional waits must not hide a refusal that needs a fix.
                        refused = next(
                            (
                                group["reason"]
                                for group in row["queue_reasons"]
                                if group["reason"] not in ("slow", *QUEUE_WAITS)
                            ),
                            refused,
                        )
                        held_row = {**row, "reason": refused, "runner_id": where[0], "runtime": ""}
                        if refused in QUEUE_WAITS:
                            slow.append({**held_row, "resting": True})
                        else:
                            stuck.append(held_row)
                        continue
                    if not (first and first < (person_cutoff if person else cutoff)):
                        continue  # the next claimable job is young; older held jobs do not age its wake
                    row["oldest_queued"], row["oldest"] = row["oldest"], first
                problem = (
                    "Starting"
                    if cloud["state"] == "starting"
                    else f"Not started after {QUEUE_MINUTES} min"
                    if cloud["state"] in GOING
                    else f"Not connected after {QUEUE_MINUTES} min"
                )
                stuck.append(
                    {
                        **row,
                        "reason": "cloud_machine",
                        "runner_id": where[0] if where else "",
                        "runtime": "",
                        "problem": problem,
                    }
                )
            continue
        if where and where[0] not in online_ids:
            waiting.append({**row, "reason": "computer_offline"})
            if count:
                stuck.append(
                    {**row, "reason": "computer_offline", "runner_id": where[0], "runtime": report.get("runtime") or ""}
                )
        elif not where and count and (not online_ids or (oldest and oldest < cutoff and slug not in parked)):
            if not online_ids:
                waiting.append({**row, "reason": "no_computer"})
            stuck.append({**row, "reason": "no_computer", "runner_id": "", "runtime": ""})
        elif where and count and report.get("ready") is False and slug not in parked:
            # The computer says this bot cannot start (no sign-in, no repository, ...): stuck now, not in 15 minutes.
            stuck.append(
                {
                    **row,
                    "reason": "not_ready",
                    "runner_id": where[0],
                    "runtime": report.get("runtime") or "",
                    "problem": str((report.get("problems") or ["Not ready"])[0])[:200],
                }
            )
            if oldest and oldest < cutoff:
                slow.append({**row, "reason": "slow"})
        elif (
            where
            and count
            and slug not in parked
            and ((oldest and oldest < cutoff) or (asked.get(slug) and asked[slug] < person_cutoff))
        ):
            # A bot the claim holds on purpose (a spend limit, a usage-limit cooldown, a lapsed lease whose worker still
            # runs, interrupted work awaiting review, ...) is said with that reason, not "slow".
            claimable = (
                ('company_limit', None, False)
                if company_stopped
                else _claimable(c, settings, where[0], slug, limits, loads=loads)
            )
            if claimable is None:
                continue  # the claim's check for this bot raised: Health names that check instead
            refused, first, person = claimable
            if slug in busy and refused in (None, 'lane_held'):
                # Keep claim priority for age checks; only use oldest-job diagnostics when
                # that free-lane job is actually refused.
                diagnostic = _claimable(c, settings, where[0], slug, limits, oldest=True, loads=loads)
                if diagnostic and diagnostic[0] and diagnostic[0] != "lane_held":
                    refused, first, person = diagnostic
            # Only fenced work is left: the interrupted run holds it, not the running turn, so that is the reason.
            refused, first, person = _fenced_queue_reason(row, refused, first, person)
            # A running turn only explains this queue when it holds the queued lane. Free task
            # lanes still get the claim's actual refusal, even while another lane runs.
            idle = where[0] not in full and not (refused == 'lane_held' and slug in busy)
            if idle and not refused:
                if not (first and first < (person_cutoff if person else cutoff)):
                    continue  # the job the claim would take is young: the older ones wait behind it or are held
                row["oldest_queued"], row["oldest"] = row["oldest"], first
            # Counts belong to the individual job's task, not the representative oldest job.
            row["queue_reasons"] = queue_reasons(where[0], slug)
            reason = (
                "limit"
                if refused == "limit"
                else "updating"
                if where[0] in updating
                else "restarting"
                if where[0] in restarting
                else refused or "slow"
            )
            # A job its task's snooze holds waits on purpose: it starts when the snooze ends (Run now and Wake end it).
            # It is Work queueing, as on a sleeping cloud machine, never a stuck bot.
            resting = idle and reason == "task_wait"
            slow.append(
                {**row, "reason": reason if idle else "busy", "wait_reason": "no_slot" if where[0] in full else "busy"}
                | ({"resting": True, "runner_id": where[0], "runtime": report.get("runtime") or ""} if resting else {})
            )
            if idle and not resting:
                stuck.append(
                    {**row, "reason": reason, "runner_id": where[0], "runtime": report.get("runtime") or ""}
                    | (
                        {
                            "target": updating[where[0]],
                            "running": running.get(where[0], 0),
                            "turns": held(where[0]),
                            "drain_progress": runner_versions.drain_progress(c, where[0]),
                        }
                        if reason == "updating"
                        else {}
                    )
                )
    return waiting, slow, stuck


def refused_queues(c, settings, loads=None):
    from .queue_refusals import aged

    return list(aged(c, settings, loads))


WELCOME = "#/welcome"
SETUP_HOLD_MINUTES = 60  # a bot still onboarding holds its work; this long and nobody is finishing it


def company_import_failed(c):
    """The chosen chart from setup that could not be imported on server start (backend/company_import.py `start`):
    red until an import works, so a failed import never looks like a team that is simply empty."""
    from .company_import import failure

    found = failure(c)
    if not found:
        return None
    return _check(
        "company_import",
        "Company file from setup not imported",
        "bad",
        f"{found.get('reason') or 'It could not be applied'}. Nothing from it was created and no sample team "
        "took its place. Set your team up in setup, from the default team or a fixed company file.",
        [_fix("Open setup", WELCOME)],
    )


def unfinished_setup(c):
    """Active bots still onboarding whose held work is older than SETUP_HOLD_MINUTES: [(id, name, count, what, oldest)].
    An onboarding bot takes no task and no notice until a person marks its onboarding complete, so its watchers' tasks
    pile up out of sight."""
    from .statuses import PARKED_SQL

    parked = c.execute(
        "SELECT b.id,b.slug,b.display_name FROM bot_config bc JOIN bots b ON b.id=bc.bot "
        f"WHERE bc.onboarding_state IN {PARKED_SQL} AND b.state='active' ORDER BY b.slug"
    ).fetchall()
    cutoff, out = H.shift(H.now(), minutes=-SETUP_HOLD_MINUTES), []
    for row in parked:  # few bots are ever parked; each is two indexed reads
        tasks = c.execute(
            "SELECT count(*) n, min(created_at) oldest FROM tasks WHERE owner_id=? AND status='open'",
            ("bot:" + row["id"],),
        ).fetchone()
        notices = c.execute(
            "SELECT count(*) n, min(created_at) oldest FROM jobs WHERE bot_id=? AND state='queued'", (row["id"],)
        ).fetchone()
        count, what, oldest = (
            (tasks["n"], "task", tasks["oldest"]) if tasks["n"] else (notices["n"], "notice", notices["oldest"])
        )
        if count and oldest < cutoff:
            out.append((row["id"], row["display_name"] or row["slug"], count, what, oldest))
    return out


def _movable(c, who, online_ids):
    """(id, label) of the online computers `who` may move a bot to: any for the owner, their own for a Bot administrator."""
    me = H.actor_id(who.actor)
    return [
        (r["id"], r["label"])
        for r in c.execute("SELECT id,label,owner_id FROM runners WHERE revoked_at IS NULL ORDER BY label")
        if r["id"] in online_ids and (who.role == "owner" or r["owner_id"] == me)
    ]


def runtime_choice(c, bot, runtime, who, computers, harness="", model=""):
    """What Settings and Health show for running `runtime` on this bot's computer, from the one rule
    (backend/readiness.py `can_run`): the answer, a short label, and at most one way out: sign in there (a computer's
    shared sign-in for a runtime that signs in from the browser) or a computer `who` may move it to that can run it."""
    from .readiness import can_run

    answer = can_run(c, bot, runtime, harness=harness, model=model)
    view = {key: answer.get(key) for key in ("can_run", "problem", "fix", "link")}
    if answer.get("warning"):
        view["warning"] = answer["warning"]
    view["computer"] = answer.get("computer")
    if answer.get("can_run") is not False:
        return view
    problem = str(answer.get("problem") or "")
    here = answer.get("computer") or {}
    if here.get("label") and here["label"] not in problem and answer.get("link") != "#/tools/keys?show=credentials":
        view["problem"] = f"{problem} on {here['label']}"  # the runner's own words name no computer
    view["short"] = (
        "not installed"
        if "not installed" in problem
        else "no key"
        if answer.get("link") == "#/tools/keys?show=credentials"
        else "not on this computer"
        if "Tico Gateway work" in problem
        else "sign in"
    )
    if runtime in model_login.RUNTIMES and str(answer.get("fix") or "").startswith(f"Sign in {runtime} "):
        view["sign_in"] = {"runner_id": here.get("id"), "runtime": runtime, "computer": here.get("label")}
    view["move"] = next(
        (
            {"id": rid, "label": label}
            for rid, label in computers
            if rid != here.get("id")
            and can_run(c, bot, runtime, runner=rid, harness=harness, model=model)["can_run"] is True
        ),
        None,
    )
    return view


# Why the claim holds an idle bot's queued work on purpose (execution.claim_refusal, job_refusal): what Health says, and
# where the fix is. A reason not here is "Not started": a Tico bug (Health Monitor queue_idle).
RUNS, COMPUTERS, BOT_SETTINGS = (
    ("Open Runs", "#/settings", "runs"),
    ("Open Computers", "#/settings", "devices"),
    ("Open bot settings", "#/settings", "bots"),
)
BILLING = ("Open Billing", "#/settings", "billing")
HELD = {
    # A Tico Cloud company's access (backend/cloud_access.py): out of credit, or locked behind Pay to continue.
    "out_of_credit": ("Out of credit: its Tico model turns start once credit is added", BILLING),
    "cloud_locked": ("Pay to continue: bots run again once the plan is paid", BILLING),
    "company_limit": ("Your company is at its limit; Tico is fixing what's stuck.", RUNS),
    "task_wait": ("Its task is snoozed: its work starts when the snooze ends", RUNS),
    # The task was given to this bot while another bot's turn on it was out (execution.turn_moved_on).
    "task_turn_out": ("Its task was handed to it mid-turn: it starts when the earlier owner's turn ends", RUNS),
    "paused": ("Paused: its owner or BotOps must resume it", BOT_SETTINGS),
    "company_paused": ("All bots on Tico Team are paused: its work starts when someone resumes", BOT_SETTINGS),
    "no_slot": ("No slot on {computer}: queued behind running turns", RUNS),
    "busy": ("Queued behind this bot's running turn", RUNS),
    "draining": ("Paused, or held while Tico updates: its work starts when it is resumed", BOT_SETTINGS),
    "moving": ("Moving to its own cloud machine: its next turn starts there once its running turns end", COMPUTERS),
    "restarting": ("{computer} is restarting: it starts new work once its running turns end", COMPUTERS),
    "maintaining": ("{computer} is tidying this bot's task worktrees: its work starts when that finishes", COMPUTERS),
    "lane_held": (
        "{computer} still runs an earlier turn of this bot whose lease lapsed: the next starts when it ends",
        RUNS,
    ),
    "handed_back": ("{computer} handed its last try back before starting it: it tries again in a moment", RUNS),
    "uncertain": ("An interrupted run on the same task or chat waits for review in Runs", RUNS),
    "task_quarantine": ("Held after repeated refusals; resumes after cooldown or quarantine clear", RUNS),
    # A person limited the bot's claims to one task (execution.scoped_sql): the rest waits for the scope's release.
    "scoped": ("Limited to one task: its other work starts when the limit is lifted", BOT_SETTINGS),
    # The runner says the turn holding the shared login is a run Tico has no live record of (backend/login_waits.py).
    "login_holder_unknown": (
        "Its turn waits for {computer}'s ChatGPT login, held by a run that is not running in "
        "Tico: restart the runner on {computer} to free it",
        COMPUTERS,
    ),
    "cooling_off": ("Waiting out a usage limit, or a Tico AI Gateway outage, before it tries again", RUNS),
    "readiness_unknown": ("{computer} has not said whether it can run this bot", COMPUTERS),
    "sign_in_rejected": ("Its model sign-in was rejected on {computer}", COMPUTERS),
    # The computer read a plan's sign-in as ready, and no plan signs it in now (execution.signed_in_by_lapsed_plan).
    "sign_in_missing": ("Its model is not signed in on {computer}", COMPUTERS),
    "login_upgrade": (
        "Update {computer} to use one login per AI tool; its bots wait without changing accounts",
        COMPUTERS,
    ),
    # A bot with Browse through runs only on a runner that takes it there (backend/browser_exit.py CAPABILITY).
    "browser_exit_update": ("Update {computer} to use Browse through", COMPUTERS),
    # A Tico model runs only where bots run as their own user (execution.claim_refusal): the bot waits, it never fails.
    "not_isolated": (
        "{computer} can't run Tico Gateway work (it needs Tico's Docker runner): it waits for another "
        "model or computer",
        BOT_SETTINGS,
    ),
    "isolation_unknown": (
        "{computer} hasn't said it can run Tico Gateway work: it waits until its runner is updated",
        COMPUTERS,
    ),
    "profile": ("Its subscription profile is not signed in or not reported on {computer}", COMPUTERS),
    "config": ("Its settings cannot be read", BOT_SETTINGS),
    "check_model": ("Its check model does not run on {computer}, or is on hold", BOT_SETTINGS),
    # A private meeting's turn runs only on a runner with private lanes (execution.PRIVATE_LANES).
    "private_update": ("{computer} needs a Tico update before it can join private meetings", COMPUTERS),
    "make_page_upgrade": ("Update {computer} to make pages", COMPUTERS),
    "unreadable": ("Its queued message is one it may not read", RUNS),
    # Tico stopped the bot (backend/bot_stops.py): a person releases it. A back-off is a short pause after a failed run.
    "run_hold": (
        "Tico stopped it after five failed runs in a row and tries one run on its own now and then: check its last runs, or release it now",
        BOT_SETTINGS,
    ),
    "budget": (
        "Tico stopped it for too many runs in a short time: check its last runs, then release it or raise its budget",
        BOT_SETTINGS,
    ),
    "backoff": ("Pausing after a failed run: it tries again in a moment", RUNS),
    # The owner's "Turns at once" for the bot is full across every computer: its queued work starts when one ends.
    "bot_limit": ("At its turn limit: it starts when one of its running turns ends", BOT_SETTINGS),
}


def _stuck(c, rows, online_ids, who, full, auth=None):
    """Each stuck bot with a reason in plain words and one fix: sign in, move to a computer that can run it,
    or the settings page that can. Only administrators get fixes; a Bot administrator moves only to their own computers.
    A bot stuck in onboarding is the exception: whoever may manage it gets "Finish with defaults"."""
    from . import readiness
    from .readiness import own_harness

    if not rows:
        return []
    computers = _movable(c, who, online_ids) if full else []
    # Each stuck bot's runtime as its config declares it (filled below with the config it reads).
    configured = {}
    # Each offline computer's last check-in, read once for every bot waiting on one (not one read per bot).
    offline = sorted({row["runner_id"] for row in rows if row["reason"] == "computer_offline"})
    seen_at = (
        {
            r["id"]: r["last_seen_at"]
            for r in c.execute(
                f"SELECT id,last_seen_at FROM runners_with_state WHERE id IN ({','.join('?' * len(offline))})", offline
            )
        }
        if offline
        else {}
    )
    # Each online computer's row and each not-ready bot's computer, read together for the can_run checks below
    # (readiness.prime_runners, prime_placed), not one read per bot per computer.
    readiness.prime_runners(c, online_ids)
    readiness.prime_placed(c, [row["bot"] for row in rows if row["reason"] == "not_ready"])
    # Every quarantined bot's quarantine record, read together for its explanation (quarantine_display.explanation).
    H.prime_quarantines(c, [row["bot"] for row in rows if row["reason"] == "quarantined"])
    # Every stuck bot's configuration and operator, read together for its harness check and quarantine
    # explanation (readiness._config and auth.operator read them per bot otherwise).
    from .read_cache import prime
    from .auth import OPERATOR_SQL

    for part in H.chunks(sorted({row["bot"] for row in rows})):
        marks = ",".join("?" * len(part))
        found = {
            r["bot"]: r
            for r in c.execute(
                f"SELECT bot,{BS.CONFIG} AS config,owner_id FROM bot_config bc {BS.JOIN} WHERE bot IN ({marks})", part
            )
        }
        prime(c, readiness.CONFIG_SQL, {(s,): found.get(s) for s in part})
        prime(c, OPERATOR_SQL, {(s,): found.get(s) for s in part})
        configured.update({bot: (H._json(r["config"], {}) or {}).get("runtime") or "" for bot, r in found.items()})
    out = []
    for row in rows:
        label, reason = row["computer"], row["reason"]
        if reason == "setup":
            manages = auth is not None and auth.bot_manager(c, who, row["bot"])
            out.append(
                {key: row[key] for key in ("bot", "name", "queued", "oldest", "computer", "reason", "since")}
                | {
                    "why": f"Stuck in onboarding for {setup_stuck.held_for(row['since'])}",
                    "fix": {**_fix("Finish with defaults"), "finish": {"bot": row["bot"]}} if manages else None,
                }
            )
            continue
        runtime = row["runtime"] or configured.get(row["bot"], "")
        harness = own_harness(c, row["bot"], runtime)  # an Antigravity bot is not judged as Gemini CLI
        fix, move = None, None
        if reason == "no_computer":
            text = "No computer"
            fix = _fix("Choose a computer", "#/settings", "bots")
        elif reason == "computer_offline":
            quiet = quiet_minutes(seen_at.get(row["runner_id"]))
            text = (
                f"{label} has not checked in for {quiet} minutes" if quiet >= SILENT_MINUTES else f"{label} is offline"
            )
            fix = _fix("Open Computers", "#/settings", "devices")
            if full and runtime:
                move = next(
                    (
                        {"id": rid, "label": name}
                        for rid, name in computers
                        if rid != row["runner_id"] and _can(c, row["bot"], runtime, rid, harness)
                    ),
                    None,
                )
        elif reason == "not_ready":
            choice = runtime_choice(c, row["bot"], runtime, who, computers, harness=harness) if runtime else {}
            problem = row.get("problem") or "Not ready"
            problem = str(choice.get("problem") or problem) if choice.get("can_run") is False else problem
            text = problem if label and label in problem else f"{problem} on {label}"
            if choice.get("can_run") is False:
                # Signing in where it is beats moving it; otherwise a computer that can run it, else where the fix is.
                if choice.get("sign_in"):
                    login = choice["sign_in"]
                    fix = {
                        **_fix("Sign in"),
                        "login": {"runner_id": login["runner_id"], "runtime": runtime, "computer": label},
                    }
                else:
                    move = choice.get("move")
                    fix = (
                        _fix("Open Keys", "#/tools/keys?show=credentials")
                        if choice.get("link") == "#/tools/keys?show=credentials"
                        else _fix("Open Computers", "#/settings", "devices")
                    )
            else:
                fix = _fix("Open bot settings", "#/settings", "bots")
        elif reason == "cloud_machine":
            text = "Cloud machine: " + (row.get("problem") or "Starting")
            fix = _fix(*(BILLING if row.get("problem") == "Out of credit" else BOT_SETTINGS))
        elif reason == "limit":
            text = "Over its spending limit"
            fix = _fix("Raise limit", "#/usage")
        elif reason == "updating":
            text = updating_words({**row, "turns": visible_turns(c, row.get("turns") or [], who, auth)})
            fix = _fix("Open Computers", "#/settings", "devices")
        elif reason == "quarantined":
            from .quarantine_display import explanation

            text = explanation(c, row["bot"], who, auth)
            fix = _fix(*BOT_SETTINGS)
        elif reason in HELD:
            words, where = HELD[reason]
            text = words.format(computer=label or "its computer")
            fix = _fix(*where)
        else:
            text = f"Not started after {QUEUE_MINUTES} min"
            fix = _fix("Open Runs", "#/settings", "runs")
        queue_reasons = row.get("queue_reasons") or []
        if len(queue_reasons) > 1 and reason not in ("no_slot", "busy", "updating", "restarting"):
            # Keep one bot row, but never describe all its jobs with the oldest job's hold.
            words = {
                "task_wait": "waiting for a snooze to end",
                "slow": "ready to start",
            }
            text = "; ".join(
                _plural(item["queued"], "job")
                + ": "
                + words.get(
                    item["reason"],
                    HELD.get(item["reason"], ("not started",))[0].format(computer=label or "its computer"),
                )
                for item in queue_reasons
            )
        if move:
            fix = {
                **_fix(f"Move to {move['label']}"),
                "move": {"bot": row["bot"], "runner_id": move["id"], "computer": move["label"]},
            }
        out.append(
            {key: row[key] for key in ("bot", "slug", "name", "queued", "oldest", "computer", "reason")}
            | {
                "dead": row.get("dead", 0),
                "held": row.get("held", 0),
                "quarantine_held": row.get("quarantine_held", 0),
                "why": text if reason == "quarantined" else text[:320],
                "fix": fix if full else None,
                **({"queue_reasons": queue_reasons} if queue_reasons else {}),
                **({"oldest_queued": row["oldest_queued"]} if "oldest_queued" in row else {}),
            }
        )
    return out


def holding_turns(c, attempts):
    """The running turns a computer due an update waits for, oldest first: which bot, since when and, unless private,
    which task, so a person can tell a real job from a turn that only waits on something elsewhere (TK-SHNKOY4E)."""
    from .task_privacy import attempt_tasks

    names = {r["id"]: r["display_name"] or r["slug"] for r in c.execute("SELECT id,slug,display_name FROM bots")}
    out = []
    for a in attempts:
        own = c.execute("SELECT task_id FROM attempts WHERE id=? AND task_id IS NOT NULL", (a["id"],)).fetchone()
        ids = sorted(attempt_tasks(c, a["id"]) | ({own[0]} if own else set()))  # the run's own task, then what it read
        task = (
            c.execute(
                f"SELECT id,title FROM tasks WHERE id IN ({','.join('?' * len(ids))}) AND NOT private "
                "ORDER BY updated_at DESC LIMIT 1",
                ids,
            ).fetchone()
            if ids
            else None
        )
        out.append(
            {
                "run": a["id"],
                "bot": a["bot_id"],
                "name": names.get(a["bot_id"], a["bot_id"]),
                "since": a["since"],
                "task": task["id"] if task else "",
                "task_title": task["title"] if task else "",
            }
        )
    return out


def visible_turns(c, turns, who, auth):
    """`turns` as this caller may see them: a bot whose activity they may not read is "another bot", and a task they
    may not read (private, or hidden by a bot's activity controls: auth.task_sql) is not named. Without `auth`,
    neither is named."""
    hidden = auth.unreadable_bots(c, who) if auth is not None else None
    out = []
    for turn in turns:
        named = hidden is not None and turn["bot"] not in hidden
        readable = bool(
            named
            and turn["task"]
            and c.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({auth.task_sql(c, who)})", (turn["task"],)).fetchone()
        )
        out.append(
            {**turn, "name": turn["name"] if named else "", **({} if readable else {"task": "", "task_title": ""})}
        )
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
    if row.get("drain_progress"):
        return f"{row['computer']} is holding new work to update{to}: {row['drain_progress']}"
    if row.get("running"):
        held = row.get("turns") or []
        if len(held) == 1:
            turns = _turn_words(held[0]) + " ends"
        elif held:
            more = f" and {len(held) - 2} more" if len(held) > 2 else ""
            turns = (
                f"{len(held)} running turns end: " + "; ".join(_turn_words(t, titled=False) for t in held[:2]) + more
            )
        else:
            turns = "running turn finishes" if row["running"] == 1 else f"{row['running']} running turns finish"
            turns = "its " + turns
        return f"{row['computer']} is holding new work to update{to}: it starts once {turns}" + (
            f". {OUTSIDE_WAIT}" if advise else ""
        )
    return f"{row['computer']} is updating{to}"


def _can(c, bot, runtime, runner, harness=""):
    from .readiness import can_run

    return can_run(c, bot, runtime, runner=runner, harness=harness)["can_run"] is True


TOKEN_CACHE_SECONDS = 300
_TOKEN_CACHE = {}  # (database, threshold) -> (monotonic time, answer)


def _token_heavy(c, threshold):
    """(bot, tokens) for bots whose uncached input tokens passed `threshold` in the last day. A run records
    `input_tokens` without its cache reads (runner-rs/src/execute.rs), so cache reads, which are cheap, do not count;
    cache writes (`cache_write_tokens`, apart from input since they were counted apart) do.
    It reads the measured segments (usage_facts, indexed on `started_at`); the sum still visits every segment of the day,
    so one answer serves every tab's Health poll for five minutes."""
    if not threshold or threshold <= 0:
        return []
    import time

    key = (next((r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main"), ""), threshold)
    hit = _TOKEN_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TOKEN_CACHE_SECONDS:
        return hit[1]
    since = H.shift(H.now(), hours=-RECENT_HOURS)
    answer = [
        (r["bot"], r["n"])
        for r in c.execute(
            "SELECT bot_id AS bot, sum(input_tokens + coalesce(cache_write_tokens, 0)) AS n FROM usage_facts "
            "WHERE source='estimate' AND started_at>=? GROUP BY bot_id HAVING n>? ORDER BY n DESC",
            (since, threshold),
        )
    ]
    _TOKEN_CACHE.clear()
    _TOKEN_CACHE[key] = (time.monotonic(), answer)
    return answer


# A bot removed since its record was written: a person reads this, never the bot's id.
DELETED_BOT = "Deleted bot"


def _bot_name(c, bot, missing=None):
    """Its display name or slug; for a bot with no row, `missing`, else the id as given."""
    row = c.execute("SELECT display_name,slug FROM bots WHERE id=?", (bot,)).fetchone()
    return (row["display_name"] or row["slug"]) if row else bot if missing is None else missing


def _bot_names(c, bots):
    """_bot_name for many bots, {bot: name}, in one read per 500 bots."""
    ids = sorted(set(bots))
    found = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        found.update(
            (row["id"], row["display_name"] or row["slug"])
            for row in c.execute(
                f"SELECT id,display_name,slug FROM bots WHERE id IN ({','.join('?' * len(part))})", part
            )
        )
    return {bot: found.get(bot, bot) for bot in bots}


def _clock(at):
    """A stored time as Health's rows say one: "07:52 UTC" today, "Nov 11 07:52 UTC" on an earlier day, never the
    ISO stamp. Text that is not a stamp is returned as it is."""
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return str(at or "")
    day = "" if when.date() == datetime.now(timezone.utc).date() else f"{when:%b} {when.day} "
    return f"{day}{when:%H:%M} UTC"


def _tokens_words(n):
    return f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.0f}M" if n >= 1e6 else f"{n:,}"


def token_failure(c):
    """{"message", "action"} when the GitHub App itself failed to issue a token in the last day and has not
    succeeded since, else None. A row without an action predates App-only recording (0.2.21 also wrote a bot's
    own missing repository here), so it is not this."""
    health = c.execute(
        "SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service=?", (GITHUB_HEALTH,)
    ).fetchone()
    if not (health and health["last_error"]):
        return None
    try:
        detail = json.loads(health["detail"] or "{}")
    except ValueError:
        return None
    # last_error holds the message; when it failed is detail's failed_at (a row without one predates it).
    failed_at = str(detail.get("failed_at") or "")
    if not (failed_at > H.shift(H.now(), hours=-RECENT_HOURS) and failed_at > (health["last_succeeded_at"] or "")):
        return None
    return (
        {"message": str(detail.get("message") or ""), "action": str(detail["action"])} if detail.get("action") else None
    )


def _github(c, github, settings):
    """Not connected is "info" while the team has no bot of its own (getting_started.own_bots) at work, and "warn" once
    it has one: the built-in bots come with every install, so the warning waits for a bot someone made, and a planned
    one (a new company's live seats, until BotOps sets them up) keeps nothing yet."""
    from .github_app import account_word

    row = github.row(c) if github else None
    failure = token_failure(c) if row else None
    fix = _fix("Open GitHub", "#/tools/github")
    if not row:
        planned = {row[0] for row in c.execute("SELECT id FROM bots WHERE state='planned'")}
        bots = [bot for bot in own_bots(c, settings) if bot not in planned]
        return _check(
            "github",
            "GitHub",
            "warn" if bots else "info",
            "Not connected. Bots' files and memory are only on their computers, with no backup.",
            [_fix("Connect GitHub", "#/tools/github")],
        )
    if not row["installation_id"]:
        return _check(
            "github", "GitHub", "warn", f"The app is created but not installed on your {account_word(row)}.", [fix]
        )
    if failure is not None:
        return _check(
            "github",
            "GitHub",
            "bad",
            "GitHub would not give a bot a token in the last day"
            + (": " + failure["message"] if failure["message"] else "."),
            [fix],
        )
    return _check("github", "GitHub", "ok", f"Connected to {row['org']}.")


def _github_repo_create(c, github):
    """An App without Administration (write) cannot create bot repositories, so every new bot stops for a person. A personal
    account also needs the owner's authorization for the empty repositories Tico creates there. The stored flag follows the
    installation's live permission whenever Tico asks GitHub, so this reads no network."""
    from urllib.parse import urlencode

    from .github_app import PERSONAL, account_kind, links

    row = github.row(c) if github else None
    if not row:
        return None
    if account_kind(row) == PERSONAL and row["administration"] and not github.user_authorized(c):
        return _check(
            "github_repo_create",
            "GitHub can't create bot repositories",
            "warn",
            f"Tico can't create bot repositories on {row['org']} until you authorize it.",
            [_fix("Authorize Tico", "#/tools/github")],
        )
    if row["administration"]:
        return None

    word = "account" if account_kind(row) == PERSONAL else "organisation"
    summary = (
        "Bots BotOps builds cannot get a GitHub repository: the GitHub App lacks Administration (write). "
        f"Let BotOps create repositories (turn on Administration for the app, then accept it for the {word}), "
        "or create each bot's repository yourself as an empty private repository."
    )
    create_url = "https://github.com/new?" + urlencode({"owner": row["org"], "visibility": "private"})
    return _check(
        "github_repo_create",
        "GitHub can't create bot repositories",
        "warn",
        summary,
        [
            _fix("Let BotOps create repositories", links(row)["permissions_url"]),
            _fix("Create a repository", create_url),
            _fix("Open GitHub settings", "#/tools/github"),
        ],
    )


def _github_workflows(c, github):
    """A write grant an Owner or admin let run workflows gets a token with Actions: write only once the installation
    has accepted it. The token request records what the installation last said, so this reads no network."""
    from .github_app import links
    from .repositories import workflows_refused, workflows_waiting

    connected = bool(github and github.row(c))
    if waiting := workflows_waiting(c) if connected else []:
        return _check(
            "github_workflows",
            "Bots can't run workflows yet",
            "warn",
            "These bots may run workflows but get only the ordinary write token: the GitHub App's Actions "
            "permission is not Read and write on the installation. Set it in the App's permissions, then "
            "accept it for the organisation. Waiting: "
            + ", ".join(waiting[:3])
            + ("." if len(waiting) <= 3 else f"; and {len(waiting) - 3} more."),
            [
                _fix("Change the App's permissions", links(github.row(c))["permissions_url"]),
                _fix("Open GitHub settings", "#/tools/github"),
            ],
        )
    if refused := workflows_refused(c) if connected else []:
        # The installation reports Actions: Read and write, so the setting is not the cause and is not offered.
        return _check(
            "github_workflows",
            "GitHub refused workflow tokens",
            "warn",
            "These bots may run workflows but get only the ordinary write token: GitHub refused a token "
            "with Actions: write although the installation reports it. Tico asks again after a minute; "
            "if this stays, check the installation's repository access and the organisation's Actions "
            "policy on GitHub. Refused: "
            + ", ".join(refused[:3])
            + ("." if len(refused) <= 3 else f"; and {len(refused) - 3} more."),
            [_fix("Open GitHub settings", "#/tools/github")],
        )
    return None


def _app_lacks(c, permission, refused_since=None):
    """Whether the GitHub App's installation lacks `permission` at write: what GitHub last reported
    (github_app.PERMISSIONS_SEEN), unless GitHub refused for want of it (at `refused_since`) after that report, or there
    is none yet. Reads no network."""
    from .github_app import PERMISSIONS_SEEN, permission_seen
    from .repositories import metadata

    seen = permission_seen(c, permission)
    seen_at = str(metadata(c, PERMISSIONS_SEEN).get("at") or "")
    if refused_since and (seen is None or seen_at <= refused_since):
        return True
    return seen is not None and seen != "write"


def _github_release_cut(c, github):
    """Tico cuts a release when the Release Owner asks (item_sources/github_release.py), with a token naming Actions:
    write (a release workflow) or Contents: write (a tag). Only a person can grant that permission, so say so plainly,
    while it is true and matters: a pipeline's In a release is set to a way that needs a permission the installation
    lacks (as GitHub last reported it, or as it last refused an ask of that way), and a task waits in that stage now.
    It clears once the permission is there, the setting is cleared or changed, or no task waits any more (its release
    came). Reads what Tico recorded, no network."""
    from .github_app import links
    from .item_sources import github_release as GR

    row = github.row(c) if github else None
    if not row:
        return None
    needs = {"workflow": ("actions", GR.MISSING_ACTIONS), "tag": ("contents", GR.MISSING_CONTENTS)}
    waiting = {}  # kind -> {stage name: tasks waiting there}
    for settings, stage_id, stage in c.execute(
        "SELECT r.settings, s.id, s.name FROM requirements r JOIN stages s ON s.id = r.stage_id "
        "JOIN pipelines p ON p.id = s.pipeline_id WHERE r.withdrawn_at IS NULL AND r.kind='item' AND r.source=? "
        "AND r.condition='in_release' AND s.retired_at IS NULL AND p.retired_at IS NULL",
        (GR.SOURCE,),
    ).fetchall():
        try:
            how = GR.way(json.loads(settings or "{}") or {})
        except (TypeError, ValueError, AttributeError):
            how = None
        if how is None:
            continue
        count = c.execute(
            "SELECT count(*) FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
            "WHERE v.stage_id=? AND t.status='open'",
            (stage_id,),
        ).fetchone()[0]
        if count:
            places = waiting.setdefault(how[0], {})
            places[stage] = places.get(stage, 0) + count
    lines = []
    for kind, places in sorted(waiting.items()):
        permission, missing = needs[kind]
        refused = c.execute(
            "SELECT updated_at, last_error FROM effects WHERE source=? AND action=? AND kind='act' "
            "AND state IN ('done', 'failed') AND pinned LIKE ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (GR.SOURCE, GR.RELEASE, kind + ":%"),
        ).fetchone()
        since = str(refused[0]) if refused and str(refused[1] or "").startswith(GR.MISSING) else None
        if not _app_lacks(c, permission, since):
            continue
        total = sum(places.values())
        where = " and ".join(sorted(places))
        lines.append(
            f"{missing}. Until then {total} {'task' if total == 1 else 'tasks'} in {where} "
            f"{'waits' if total == 1 else 'wait'} for a release Tico can't cut"
        )
    if not lines:
        return None
    return _check(
        "github_release_cut",
        "Tico can't cut releases",
        "warn",
        ". ".join(lines) + ".",
        [
            _fix("Change the App's permissions", links(row)["permissions_url"]),
            _fix("Open GitHub settings", "#/tools/github"),
        ],
    )


def _github_merge_ok(c, github):
    """Tico's Merge stage posts its `tico/merge-ok` check run on each pull request it queues, and on each merge queue
    group (item_sources/github.py), with the server's own Checks: write token. Without it nothing is posted, so a
    ruleset that requires the check refuses the merge. One line, while a pipeline queues pull requests and the
    installation, as GitHub last reported it, lacks Checks: write. Reads no network."""
    from .github_app import links

    row = github.row(c) if github else None
    if not row:
        return None
    queues = c.execute(
        "SELECT 1 FROM stages s JOIN pipelines p ON p.id = s.pipeline_id WHERE s.retired_at IS NULL "
        "AND p.retired_at IS NULL AND json_valid(s.on_enter) AND json_extract(s.on_enter, '$.act') = 'github.queue' "
        "LIMIT 1"
    ).fetchone()
    if not queues or not _app_lacks(c, "checks"):
        return None
    return _check(
        "github_merge_ok",
        "Tico can't post its merge check",
        "warn",
        "Tico's GitHub App lacks Checks: write, so Tico posts no tico/merge-ok check on the pull requests it queues, "
        "and a ruleset that requires that check refuses their merge. A person sets Checks to Read and write in the "
        "App's permissions on GitHub and accepts the change for the installation.",
        [
            _fix("Change the App's permissions", links(row)["permissions_url"]),
            _fix("Open GitHub settings", "#/tools/github"),
        ],
    )


def _github_workflow_edits(c, github):
    """`_github_workflows` for write grants an Owner or admin let edit workflow files: their token carries Workflows:
    write only once the installation has accepted it."""
    from .github_app import links
    from .repositories import workflows_refused, workflows_waiting

    connected = bool(github and github.row(c))
    if waiting := workflows_waiting(c, 'edit_workflows') if connected else []:
        return _check(
            "github_workflow_edits",
            "Bots can't edit workflow files yet",
            "warn",
            "These bots may edit workflow files but get only the ordinary write token: the GitHub App's "
            "Workflows permission is not Read and write on the installation. Set it in the App's "
            "permissions, then accept it for the organisation. Waiting: "
            + ", ".join(waiting[:3])
            + ("." if len(waiting) <= 3 else f"; and {len(waiting) - 3} more."),
            [
                _fix("Change the App's permissions", links(github.row(c))["permissions_url"]),
                _fix("Open GitHub settings", "#/tools/github"),
            ],
        )
    if refused := workflows_refused(c, 'edit_workflows') if connected else []:
        return _check(
            "github_workflow_edits",
            "GitHub refused workflow-editing tokens",
            "warn",
            "These bots may edit workflow files but get only the ordinary write token: GitHub refused a "
            "token with Workflows: write although the installation reports it. Tico asks again after a "
            "minute; if this stays, check the installation's repository access on GitHub. Refused: "
            + ", ".join(refused[:3])
            + ("." if len(refused) <= 3 else f"; and {len(refused) - 3} more."),
            [_fix("Open GitHub settings", "#/tools/github")],
        )
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
        return _check(
            "slack",
            "Slack",
            "warn",
            "Tokens saved; the Slack service has not connected yet. Is the slack profile on in COMPOSE_PROFILES?",
            [fix],
        )
    return _check(
        "slack", "Slack", "bad", "Slack is disconnected" + (": " + state["message"] if state["message"] else "."), [fix]
    )


def _age_status(stamp):
    """ok, warn or bad by how old a status-file time is: `behind_since` (how long the backup location has been behind the
    database) or `checked_at` (when the loop last read it). A missing time is ok. The warning waits for ten sync intervals
    (replication.behind_warn_seconds); bad past an hour, or past that warning time when it is later. It is judged from the
    time itself, never from the age of the last copy: a quiet database writes nothing new and is not behind."""
    if not stamp:
        return "ok"
    at = H.parse_ts(stamp)
    if at is None:
        return "warn"
    age = (H.parse_ts(H.now()) - at).total_seconds()
    warn_after = replication.behind_warn_seconds()
    if age > max(replication.BEHIND_BAD_MIN_S, warn_after):
        return "bad"
    return "warn" if age > warn_after else "ok"


def _unsent_limit():
    """How many local level-0 files count as a pile-up. Litestream writes one local file a second on a busy database and
    uploads once per sync interval, so two sync intervals' worth of files is normal; the floor is the one-minute default."""
    return max(BACKUP_UNSENT_FILES, 2 * replication.sync_interval_seconds())


def _backups(config, settings):
    backup = config.get("backup")
    fix = _fix("Backup settings", "https://github.com/ticoteam/tico/blob/main/docs/install.md#backups-and-restore")
    if not isinstance(backup, dict) or not backup.get("mode"):
        return _check("backups", "Backups", "unknown", "This server does not report its backup state.")
    mode = str(backup["mode"]).replace("_", "-")
    last = backup.get("last_replicated_at") or ""
    since = backup.get("behind_since") or ""
    target = str(backup.get("target_kind") or "")
    key = backup.get("credential_key") if isinstance(backup.get("credential_key"), dict) else {}
    key_here = bool(key.get("present"))  # a local credential key (/data/credential.key) is in use
    key_copied = bool(key.get("current"))
    files = backup.get("key_files") if isinstance(backup.get("key_files"), dict) else {}
    # github-app.key and slack.key (backend/replication.py): present ones that the backup does not hold as they are now
    uncopied = sorted(
        name
        for name, state in files.items()
        if isinstance(state, dict) and state.get("present") and not state.get("current")
    )
    if mode == "off":
        return _check("backups", "Backups", "bad", "Backups are off." + (KEY_LOST if key_here else ""), [fix])
    # The loop writes checked_at every round, even when the listing fails; an old one means it has stopped or cannot reach
    # the backup location, so nothing it last reported (copies, lag) can be trusted.
    checked = _age_status(backup.get("checked_at"))
    if checked != "ok":
        return _check(
            "backups",
            "Backups",
            checked,
            f"Backup status not checked since {_clock(backup.get('checked_at'))}; the backup location may be unreachable.",
            [fix],
        )
    behind = _age_status(since) if mode == "remote" else "ok"
    unsent = backup.get("unsent_files") if isinstance(backup.get("unsent_files"), int) else 0
    if unsent > _unsent_limit():
        return _check(
            "backups",
            "Backups",
            "bad" if behind == "bad" else "warn",
            f"Backups are stuck: Litestream has {unsent:,} unsent files. Restart the server; if it continues, "
            "see the backup docs.",
            [fix],
        )
    if mode in ("local-only", "local"):
        note = (
            " The credential key, which opens every saved credential, is only in this server's volumes too."
            if key_here
            else ""
        )
        if key_here and not key_copied:
            note += " It has not been copied to the backup volume yet."
        if uncopied:
            note += " " + _key_files_words(uncopied) + " not been copied to the backup volume yet."
        if settings.loopback:
            return _check(
                "backups",
                "Backups",
                "warn" if (key_here and not key_copied) or uncopied else "info",
                "Copies stay on this computer. Set a backup bucket in .env to keep copies elsewhere." + note,
                [fix],
            )
        return _check(
            "backups", "Backups", "warn", "Copies stay on this server only. A lost disk loses everything." + note, [fix]
        )
    if not last:  # `reason` says why there is no copy at all (replication.NO_RECENT_COPY)
        return _check(
            "backups",
            "Backups",
            "bad" if behind == "bad" else "warn",
            backup.get("reason") or "Set up, but nothing has been copied yet.",
            [fix],
        )
    if behind != "ok":
        return _check(
            "backups",
            "Backups",
            behind,
            f"Copies to {target or 'the remote'} have been behind the database since {_clock(since)}. The newest copy is "
            f"from {_clock(last)}.",
            [fix],
        )
    if key_here and not key_copied:
        return _check(
            "backups",
            "Backups",
            "warn",
            f"The database is copied to {target or 'a remote'}, but the credential key has not been. A database restored "
            "without it cannot open any saved credential. Check the server log for the backup warning.",
            [fix],
        )
    if uncopied:
        return _check(
            "backups",
            "Backups",
            "warn",
            f"The database is copied to {target or 'a remote'}, but {_key_files_words(uncopied)} not been. A "
            "database restored without them cannot open the GitHub App, directory sync or Slack secrets. Check "
            "the server log for the backup warning.",
            [fix],
        )
    return _check("backups", "Backups", "ok", f"Copied to {target or 'a remote'}.")


def _key_files_words(names):
    return " and ".join(names) + (" has" if len(names) == 1 else " have")


SEALED_FIX = (
    "Copy {file} from the backup location (credential-key/{file}) into the data volume beside hub.sqlite, mode "
    "0600, and restart the server: `docker compose run --rm server restore --force` does it with the database. "
    "If no backup has it, {redo}."
)


def _sealed_secrets(c, settings, github):
    """Secrets sealed with a key file beside the database (the GitHub App and directory sync credentials with
    github-app.key, the Slack tokens with slack.key, every credential with credential.key) that this server cannot
    open: a restore or a move that left a key file behind. Read only: nothing here creates a key."""
    import hashlib
    import hmac

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from . import credentials, directory, slack_app

    still_needs = {}  # {key file: rows only it opens}, under KMS

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
        sources = (
            list(c.execute("SELECT * FROM directory_credentials")) if github and table("directory_credentials") else []
        )
        kms = bool(settings.credential_kms_key and github and github.vault is not None)
        vault = c.execute("SELECT kms_key,wrapped_key FROM credential_keys WHERE id='v1'").fetchone()
        if kms and not vault:
            return None  # the first save makes the vault's key; a Health read must not ask KMS for one
        if app or sources:
            key = github.secret_key(c, create=False)
            lost = []
            legacy = github.key_file() if kms else None
            if app and not (key and opens(key, app, b"tico-github-app:v1", legacy)):
                lost.append("The GitHub App's private key and webhook secret")
            if sources and not (
                key and all([opens(key, r, directory.AAD + r["source"].encode(), legacy) for r in sources])
            ):
                lost.append("the directory sync credentials" if lost else "The directory sync credentials")
            if lost:
                problems.append(
                    " and ".join(lost)
                    + " cannot be decrypted: "
                    + (
                        "github-app.key is missing."
                        if key is None
                        else "they were sealed with another key (github-app.key from before TICO_CREDENTIAL_KMS_KEY was set, "
                        "which is no longer on the data volume?)."
                        if kms
                        else "github-app.key does not open them."
                    )
                )
                fixes.append(
                    SEALED_FIX.format(
                        file="github-app.key",
                        redo="disconnect GitHub and connect it again, or save "
                        "the directory sync credentials again, and confirm replacing the lost key: "
                        "Tico drops the GitHub App and every directory source's credentials sealed "
                        "with it, and you enter them again"
                        if key is None
                        else "disconnect GitHub and connect it again, and save the directory sync credentials again",
                    )
                )
        slack = (
            c.execute("SELECT * FROM slack_credentials WHERE id='app'").fetchone()
            if table("slack_credentials")
            else None
        )
        if slack:
            # The key the Slack gateway opens them with: the vault's under KMS, else slack.key.
            key = slack_app._key(settings, github.vault if kms else None, c, create=False)
            if not (key and opens(key, slack, slack_app.AAD, slack_app.key_file(settings) if kms else None)):
                problems.append(
                    "The Slack tokens cannot be decrypted: "
                    + (
                        "slack.key is missing."
                        if key is None
                        else "the key they were sealed with is not this server's."
                    )
                )
                fixes.append(
                    SEALED_FIX.format(file="slack.key", redo="paste the Slack tokens again (Settings > Slack)")
                )
        if vault and vault["kms_key"] == credentials.LOCAL and not settings.credential_kms_key:
            path = settings.db_path.parent / credentials.KEY_FILE
            data = path.read_bytes() if path.is_file() else None
            if data is None or not hmac.compare_digest(
                hmac.new(data, credentials.KEY_CHECK, hashlib.sha256).digest(), bytes(vault["wrapped_key"])
            ):
                problems.append(
                    "Saved credentials cannot be decrypted: credential.key "
                    + ("is missing." if data is None else "does not belong to this database.")
                )
                fixes.append(SEALED_FIX.format(file="credential.key", redo="add each credential again (Credentials)"))
    except Problem:
        return None  # a KMS key that cannot be reached is the credential_kms check's to report
    fix = [
        _fix("Restore the key files", "https://github.com/ticoteam/tico/blob/main/docs/install.md#backups-and-restore")
    ]
    needed = ""
    if still_needs:
        counts = []
        for name, n in sorted(still_needs.items()):  # "2 secrets still need github-app.key and 1 still needs slack.key"
            noun = ("secret " if n == 1 else "secrets ") if not counts else ""
            counts.append(f"{n} {noun}still {'needs' if n == 1 else 'need'} {name}")
        needed = (
            " and ".join(counts) + ": sealed before TICO_CREDENTIAL_KMS_KEY was set, each is re-sealed with the KMS "
            "key the first time it is used. Keep " + (" and ".join(sorted(still_needs))) + " (and the backup's copy) "
            "until this clears."
        )
    if not problems:
        return _check("sealed_secrets", "Stored secrets", "info", needed, fix) if needed else None
    return _check(
        "sealed_secrets", "Stored secrets", "warn", " ".join(problems + fixes + ([needed] if needed else [])), fix
    )


KEY_LOST = " The credential key, which opens every saved credential, is on this server's disk only."


def _signin(settings):
    kind = settings.proxy_kind
    if kind:
        return _check("signin", "Sign-in", "ok", "People sign in through " + PROXIES.get(kind, kind) + ".")
    if settings.loopback or settings.local_signin:
        return _check("signin", "Sign-in", "ok", "Local sign-in on this computer.")
    return _check(
        "signin",
        "Sign-in",
        "warn",
        "No identity proxy is configured for this address, so people cannot sign in safely.",
        [_fix("Sign-in settings", "https://github.com/ticoteam/tico/blob/main/docs/install.md#sign-in")],
    )


def _server_settings(c, settings):
    checks = []
    if settings.block_external_invites or os.environ.get("TICO_BLOCK_EXTERNAL_INVITES") == "1":
        active = settings.block_external_invites
        checks.append(
            _check(
                "external_invites",
                "Outside calendar invites",
                "ok" if active else "warn",
                "Blocked for bots through the calendar Tool."
                if active
                else "TICO_BLOCK_EXTERNAL_INVITES=1 was requested but is not active. Recreate the server with the "
                "release's compose.yaml and check its environment.",
                []
                if active
                else [_fix("Calendar settings", "https://github.com/ticoteam/tico/blob/main/docs/mail.md")],
            )
        )
    key = settings.credential_kms_key.strip() or os.environ.get("TICO_CREDENTIAL_KMS_KEY", "").strip()
    row = c.execute("SELECT kms_key FROM credential_keys WHERE id='v1'").fetchone()
    stored = row["kms_key"] if row else ""
    if key or (stored and stored != "local"):
        active = bool(key and key == settings.credential_kms_key and stored == key)
        summary = (
            "The credential key is wrapped with the configured AWS KMS key."
            if active
            else "TICO_CREDENTIAL_KMS_KEY was requested but is not active. Recreate the server with the release's "
            "compose.yaml and check its environment."
            if key and not settings.credential_kms_key.strip()
            else "AWS KMS was requested but no credential key has been wrapped yet. Saving a credential will wrap it; "
            "check the server's AWS credentials, region and KMS permissions if saving fails."
            if key and not stored
            else "AWS KMS was requested but credentials still use the local key. The next credential save or read will "
            "wrap that same key; check the server's AWS credentials, region and KMS permissions if it fails."
            if key and stored == "local"
            else "Credentials use a different AWS KMS key than the server configuration. Restore the original "
            "TICO_CREDENTIAL_KMS_KEY in .env and recreate the server."
        )
        checks.append(
            _check(
                "credential_kms",
                "Credential encryption",
                "ok" if active else "warn",
                summary,
                [_fix("Credential settings", "https://github.com/ticoteam/tico/blob/main/docs/credential-vault.md")],
            )
        )
    return checks


def _failed(c):
    """How many jobs failed in the last day and the newest five of them (runs.failed): a retry that went back in the
    queue is not one. Each says why, read for all five at once (_failed_why)."""
    total, rows = runs.failed(c, H.shift(H.now(), hours=-runs.FAILED_HOURS), limit=5)
    ids = [r["id"] for r in rows]
    found = (
        {
            r["id"]: r
            for r in c.execute(
                "SELECT a.id,a.state,a.started_at,a.result,a.final_text,r.label AS computer,d.end_reason,"
                # The tries on the computer the line names, so "after N tries" is true of that computer.
                "(SELECT count(*) FROM attempts t WHERE t.job_id=a.job_id AND t.runner_id=a.runner_id "
                "AND t.started_at IS NULL) AS unstarted "
                "FROM attempts a LEFT JOIN runners r ON r.id=a.runner_id "
                "LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id "
                f"WHERE a.id IN ({','.join('?' * len(ids))})",
                ids,
            )
        }
        if ids
        else {}
    )
    return total, [
        {"bot": r["bot_id"], "state": r["state"], "at": r["finished_at"], "why": _failed_why(found.get(r["id"]))}
        for r in rows
    ]


def _failed_why(row):
    """Why one failed job failed, in one line: a job its computer never started "couldn't start on <computer> after N
    tries" (execution.settle_unstarted failed it), else the error its run stored, as Runs words it
    (views.attempt_details: the limit it hit, else run_diagnostics' end_reason). A job the claim refused because its bot
    may not read its task never reached a computer, so it says that (execution.REFUSED_RUN), not that a computer failed.
    Unlike Runs, no `result.error`/`reason`/`message`: Completion forbids fields it does not know, so a runner never
    sends them."""
    from .execution import REFUSED_RUN

    if not row:
        return "Attempt failed before completion"
    if not row["started_at"] and row["final_text"] == REFUSED_RUN:
        return REFUSED_RUN
    if not row["started_at"]:
        where = row["computer"] or "its computer"
        return f"couldn't start on {where} after {_plural(max(row['unstarted'], 1), 'try', 'tries')}"
    try:
        result = json.loads(row["result"] or "{}")
    except (ValueError, TypeError):
        result = {}
    reason = str(
        login_holds.failure_line(result)
        or row["end_reason"]
        or ("Lease expired before completion" if row["state"] == "expired" else "Attempt failed before completion")
    )
    return (reason.strip().splitlines() or ["Attempt failed before completion"])[0][:300]


def _fallbacks(c, loads=None):
    """(on fallback, limited, carrying): bots whose latest run in the last day ran on something other than what the bot
    is set to, with that harness, the model that did the work, the runtime it is set to when the claim moved it to the
    Gateway because that runtime is not signed in on its computer (`waiting_for_sign_in`, backend/models_preset.py),
    and when the unbroken run of such turns began; bots a usage limit still holds; and the bots with any turn in the
    last day that carries `waiting_for_sign_in` (a turn recorded before the field existed says nothing either way)."""
    from .execution import cooling_off

    since, latest, carrying = H.shift(H.now(), hours=-RECENT_HOURS), {}, set()
    for r in c.execute(
        "SELECT bot_id,coalesce(started_at,created_at) AS began,json_extract(result,'$.fallback') AS hop,"
        "json_extract(result,'$.waiting_for_sign_in') AS unsigned,usage_model "
        "FROM attempts WHERE state IN ('completed','failed','interrupted') AND finished_at>? "
        "ORDER BY bot_id,finished_at DESC",
        (since,),
    ):
        if r["unsigned"]:
            carrying.add(r["bot_id"])
        # The runner's own hop names its harness; a turn the claim moved to the Gateway ran on the Gateway's.
        harness = r["hop"] or (models_preset.GATEWAY_RUNTIME if r["unsigned"] else None)
        row = latest.setdefault(
            r["bot_id"],
            {
                "bot": r["bot_id"],
                "harness": harness,
                "model": r["usage_model"] or None,
                "waiting_for_sign_in": (r["unsigned"] or None) if not r["hop"] else None,
                "changed_at": r["began"],
                "open": bool(harness),
            },
        )
        if row["open"] and harness:
            row["changed_at"] = r["began"]
        else:
            row["open"] = False
    on = sorted(
        ({k: v for k, v in row.items() if k != "open"} for row in latest.values() if row["harness"]),
        key=lambda row: (row["changed_at"] or "", row["bot"]),
    )
    limited = [
        {"bot": r["bot"], "changed_at": r["changed_at"]}
        for r in c.execute("SELECT bot,changed_at FROM bot_status WHERE state='limited' ORDER BY changed_at,bot")
        if cooling_off(c, r["bot"], loads)
    ]
    return on, limited, carrying


# What a person calls the subscription a runtime signs in with, in Health's line about what bots run on.
SUBSCRIPTION_NAMES = {"claude": "Claude", "codex": "Codex"}


def _model_words(runtime, model):
    """`Codex (gpt-6-luna)`, or `Tico Medium` for a Tico model."""
    from . import gateway_tiers

    found = gateway_tiers.resolve(model)
    if found:
        return "Tico " + found["label"]
    return _harness_name(runtime) + (f" ({model})" if model else "") if runtime else str(model or "")


def _ran_on_words(row):
    """What a row of `_fallbacks` ran on: the model the runner counted, else the Gateway for a turn the claim moved
    there, else the fallback's harness."""
    if not row.get("model") and row.get("waiting_for_sign_in"):
        return "the Tico AI Gateway"
    return _model_words(row["harness"], row.get("model"))


def _named_lines(lines, most=5):
    """The first `most` lines, then how many bots the rest are, in words a person reads."""
    more = len(lines) - most
    tail = [f"And {more} more bot{'s' if more != 1 else ''}."] if more > 0 else []
    return " ".join(lines[:most] + tail)


def _fallback_lines(c, settings, on, carrying):
    """One plain line per bot in `on` (from `_fallbacks`) saying what it is set to and what it runs on instead, set on
    each row as `line`; and the summary for Health: those lines, or one line when every bot whose turns carry
    `waiting_for_sign_in` ran on the Gateway because its subscription is not signed in on its computer, naming those
    bots (at most five), with a line for any other bot in `on`."""
    from .shared_bots import follow

    if not on:
        return ""
    record, bots = providers.load(c, settings), [row["bot"] for row in on]
    configs = {}
    for part in H.chunks(bots):
        configs.update(
            (r["bot"], r["config"])
            for r in c.execute(
                f"SELECT bot,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot IN ({','.join('?' * len(part))})",
                part,
            )
        )
    for row in on:
        runtime, model = providers.bot_choice(
            c, settings, follow(c, row["bot"], H._json(configs.get(row["bot"]), {}) or {}), record
        )
        row["set_to"] = {"runtime": runtime, "model": model}
        ran_on = _ran_on_words(row)
        name = _bot_name(c, row["bot"])
        if row.get("waiting_for_sign_in"):
            unsigned = row["waiting_for_sign_in"]
            row["line"] = (
                f"{name} is set to {_model_words(runtime or unsigned, model)} and runs on {ran_on}: its computer "
                f"has no {SUBSCRIPTION_NAMES.get(unsigned, _harness_name(unsigned))} subscription."
            )
        else:
            row["line"] = (
                f"{name} is set to {_model_words(runtime, model)} and runs on its fallback, {ran_on}: "
                f"what it is set to was not available."
            )
    signed = {row["bot"] for row in on if row.get("waiting_for_sign_in")}
    if len(carrying) > 1 and signed == carrying:
        group = [row for row in on if row["bot"] in carrying]
        models = sorted({_ran_on_words(row) for row in group})
        unsigned = sorted({row["waiting_for_sign_in"] for row in group})
        names = " or ".join(SUBSCRIPTION_NAMES.get(r, _harness_name(r)) for r in unsigned)
        # Name the bots counted: a bot whose turns ran on what it is set to carries no `waiting_for_sign_in`, so the
        # group is not every bot on the team.
        who, more = [_bot_name(c, row["bot"]) for row in group[:5]], len(group) - 5
        who = (
            ", ".join(who[:-1]) + " and " + who[-1]
            if more <= 0
            else ", ".join(who) + f" and {more} more bot{'s' if more != 1 else ''}"
        )
        summary = (
            f"{who} run on {' or '.join(models)}, not the models they are set to: their computers have no {names} "
            "subscription."
        )
        rest = _named_lines([row["line"] for row in on if row["bot"] not in carrying])
        return f"{summary} {rest}" if rest else summary
    return _named_lines([row["line"] for row in on])


def _bot_computers(computers):
    """A bot checking the team's health sees which computers are up, their release and disk, never who operates
    them or their sign-in, container and browser errors."""
    keep = ("id", "label", "online", "resting", "last_seen_at", "platform", "disk", "turn_counts", "turn_summary")
    return [
        {**{k: x.get(k) for k in keep}, "update": {k: x["update"].get(k) for k in ("release", "state", "label")}}
        for x in computers
    ]


def _v(version):
    """A release as people write it, `v0.2.3`, whichever way the source spelled it."""
    version = str(version or "")
    return "v" + version if version[:1].isdigit() else version


def storage_settings(c, settings):
    """The storage files go to: Tico file hosting while it is on (backend/tico_storage.py), else the company's own
    bucket when the owner set one (backend/company_storage.py)."""
    from .blobs import Location
    from .company_storage import active_location
    from .tico_storage import active_location as hosting

    place = hosting(c) or active_location(c)
    return Location(settings, place["bucket"], place["prefix"], place["region"], keys=True) if place else settings


def storage_view(c, settings):
    settings = storage_settings(c, settings)
    usage = c.execute(
        "SELECT COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes FROM "
        "(SELECT digest,MAX(size) AS size FROM blobs GROUP BY digest)"
    ).fetchone()
    row = c.execute("SELECT detail FROM service_health WHERE service='blob-copy'").fetchone()
    detail = json.loads(row["detail"] or "{}") if row else {}
    row = c.execute("SELECT detail FROM service_health WHERE service='blob-s3'").fetchone()
    write_detail = json.loads(row["detail"] or "{}") if row else {}
    location = settings.blob_bucket + ("/" + settings.blob_prefix if settings.blob_prefix else "")
    credentials = write_detail.get("credentials") if write_detail.get("location") == location else None
    return {
        "mode": "s3" if settings.blob_bucket else "local",
        "bucket": settings.blob_bucket,
        **({"credentials": credentials} if credentials in ("keys", "backup", "role", "company") else {}),
        "region": blob_s3.region(settings) or "",
        "files": usage["files"],
        "bytes": usage["bytes"],
        "copy": {key: detail.get(key, 0) for key in ("done", "total", "failed")},
    }


def _aged_pr_checks(c, who, auth, full, kind):
    """Name visible bot PRs waiting over twelve hours, without contacting GitHub."""
    if not full and kind != "bot":
        return []
    from .github import health_wait_reason

    # task_sql binds visibility predicates to the tasks table name.
    rows = c.execute(
        "SELECT l.*,tasks.title task_title,tasks.owner_id,tasks.number ticket,tasks.status "
        "FROM task_links l "
        f"JOIN {H.TASKS} tasks ON tasks.id=l.task_id WHERE l.kind='pr' AND l.state IN ('open','draft') "
        "AND tasks.owner_id LIKE 'bot:%' AND coalesce(tasks.private,1)=0 "
        f"AND ({auth.task_sql(c, who)}) ORDER BY l.created_at,l.id"
    )
    waits, seen = [], set()
    for row in rows:
        link = dict(row)
        detail = H._json(link["detail"], {}) or {}
        wait = detail.get("health_wait") or {}
        reason = health_wait_reason(link, detail)
        if not reason or wait.get("reason") != reason or not H.parse_ts(wait.get("since")):
            continue
        minutes = quiet_minutes(wait["since"])
        if H.parse_ts(wait["since"]) >= H.parse_ts(H.shift(H.now(), hours=-12)) or link["url"] in seen:
            continue
        seen.add(link["url"])
        waits.append((link, reason, wait, minutes))
        if len(waits) >= DIAGNOSTIC_LIMIT:
            break
    items = []
    for link, reason, wait, minutes in waits:
        # The move is the task's owner's: it has the task's work (waiting_on), the pull request with it.
        actor = link["owner_id"]
        action = (
            f"{H.actor_name(c, actor)}: resolve the conflict on PR #{link['number']}"
            if reason == "conflict"
            else f"{H.actor_name(c, actor)}: merge PR #{link['number']} or say why it waits"
        )
        task_name = f"#{link['ticket']} " if link["ticket"] else ""
        items.append(
            {
                "bot": H.actor_id(link["owner_id"]),
                "name": f"PR #{link['number']}",
                "pr": link["url"],
                "task": link["task_id"],
                "reason": reason,
                "since": wait["since"],
                "age_minutes": minutes,
                "whose_move": actor,
                "text": f"PR #{link['number']} ({task_name}{link['task_title'][:200]}): "
                f"{'conflicting' if reason == 'conflict' else 'green and mergeable'} for {minutes // 60} h; {action}",
                "fix": action + "; tico task show " + link["task_id"],
                "fixes": [_fix("Open PR", link["url"]), _fix("Open task", "#/tasks/" + link["task_id"])],
            }
        )
    return (
        [
            _check(
                "aged_prs",
                "Pull requests not progressing",
                "warn",
                _diagnostic_summary("Bot pull requests waiting over 12 hours.", items),
                items=items,
                fixes=[_fix("Open tasks", "#/tasks")],
            )
        ]
        if items
        else []
    )


def _unreviewed_runs(c, who, auth):
    """Bots with a stopped run held two hours or more with no decision (execution.unreviewed), as `who` may read them:
    BotOps's review of it did not happen, so a person sees it here and in the operator's Needs you (Dev 543)."""
    from . import task_privacy as privacy
    from .execution import HELD_UNREVIEWED

    slugs = [
        row[0]
        for row in c.execute(
            "SELECT DISTINCT j.bot_id FROM jobs j LEFT JOIN attempts a ON a.id=j.attempt_id WHERE j.state='uncertain' "
            "AND coalesce(a.finished_at,j.created_at)<=? ORDER BY j.bot_id",
            (H.shift(H.now(), seconds=-HELD_UNREVIEWED),),
        )
    ]
    readable = auth.bot_accesses(c, who, slugs) if slugs else {}
    names = _bot_names(c, slugs) if slugs else {}
    items = []
    # One read of every held run, then the same per-run visibility check unreviewed does for one bot.
    held = {}
    if slugs:
        cutoff = H.shift(H.now(), seconds=-HELD_UNREVIEWED)
        marks = ",".join("?" * len(slugs))
        for row in c.execute(
            "SELECT m.*,j.bot_id,j.attempt_id,coalesce(a.finished_at,j.created_at) AS since FROM jobs j "
            "JOIN messages m ON m.id=j.message_id LEFT JOIN attempts a ON a.id=j.attempt_id "
            f"WHERE j.bot_id IN ({marks}) AND j.state='uncertain' AND coalesce(a.finished_at,j.created_at)<=? "
            "AND NOT EXISTS(SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id) "
            "ORDER BY since",
            (*slugs, cutoff),
        ):
            held.setdefault(row["bot_id"], []).append(row)
        privacy.prime_messages(c, [row for rows in held.values() for row in rows])
        privacy.prime_attempts(c, [row["attempt_id"] for rows in held.values() for row in rows])
    for slug in slugs:
        if not readable.get(slug, auth.FULL)["read"]:
            continue
        count, oldest, seen = 0, None, set()
        for row in held.get(slug, []):
            if not (
                privacy.message_readable(c, privacy.actor(who), row)
                and (not row["attempt_id"] or privacy.attempt_readable(c, privacy.actor(who), row["attempt_id"]))
            ):
                continue
            # Per stopped run: requests folded into one run share its attempt.
            key = row["attempt_id"] or row["id"]
            if key in seen:
                continue
            seen.add(key)
            count += 1
            oldest = oldest or row["since"]
        hours = int((H.parse_ts(H.now()) - H.parse_ts(oldest)).total_seconds() // 3600) if oldest else 0
        if count:
            text = f"{names[slug]}: {count} stopped run{'' if count == 1 else 's'} waiting {hours} h"
            items.append(
                {
                    "bot": slug,
                    "name": names[slug],
                    "count": count,
                    "hours": hours,
                    "text": text,
                    "fixes": [_fix("Review stopped runs", f"#/bot/{slug}/more")],
                }
            )
    return (
        _check(
            "stopped_runs",
            "Stopped runs nobody decided",
            "warn",
            _diagnostic_summary("Held for a check two hours or more; choose Try again or Don't retry.", items),
            [_fix("Open bots", "#/settings", "bots")],
            items=items,
        )
        if items
        else None
    )


NEEDS_PLATE_WARN = 10  # more items than this on one person's Needs you plate is a warning
NEEDS_PLATE_BAD = 25  # more than this is bad
NEEDS_PLATE_ASKERS = 3  # the bots named on a plate, most items first
NEVER_STUCK_COUNTS = ("waiting_on_nothing", "retry", "manager", "person", "cant_run", "no_one", "broken")
# registry_metadata: the hourly never-stuck reading (record_never_stuck). Its numbers sit under NEVER_STUCK_KEY, which an
# Admin's SQL reads; which tasks each counts, and the breaks' words, sit under NEVER_STUCK_TASKS_KEY, which no SQL reads
# (backend/sql.py), since they name tasks the Admin may not read.
NEVER_STUCK_KEY = "health:never_stuck"
NEVER_STUCK_TASKS_KEY = "health:never_stuck:tasks"
NEVER_STUCK_FRESH_MINUTES = 10  # an older reading says when it was read


def _plate_people(c, auth):
    """Everyone on the roster as the identity they sign in with. A person who has left, or whose sign-in is off, has no
    plate that anyone reads here."""
    found = []
    for row in H.humans(c):
        try:
            found.append(auth.identity_for_actor(c, "human:" + row["id"]))
        except Problem:
            continue
    return found


def _plate_tasks(item):
    """The tasks one Needs you item stands for: its task, or the tasks a Paused cause holds up."""
    return [str(t) for t in ([item.get("task_id")] if item.get("task_id") else item.get("tasks") or [])]


def _plate_check(c, who, auth, full):
    """Each person's Needs you count from the engine's read (waiting_on.needs: their asks, their own tasks' work and the
    Paused causes they fix). An administrator reads everyone's plate; anyone else reads only their own. Warn over
    NEEDS_PLATE_WARN items, bad over NEEDS_PLATE_BAD. Only the plates over the warning are listed, each with the bots
    that asked for most of its tasks."""
    from . import waiting_on

    over = []
    visible_sql = auth.task_sql(c, who) if full else None
    plates = waiting_on.needs_by_person(c)  # one pass over the open tasks for every person's plate
    for person in _plate_people(c, auth) if full else [who]:
        items = plates.get(person.actor, [])
        if visible_sql and person.actor != who.actor:
            # Another person's plate counts only what the reader may read: a private task is not theirs to count.
            readable = _readable(c, visible_sql, sorted({tid for item in items for tid in _plate_tasks(item)}))
            items = [item for item in items if any(tid in readable for tid in _plate_tasks(item))]
        if len(items) > NEEDS_PLATE_WARN:
            bots = {}
            for item in items:
                if not item.get("task_id"):
                    continue  # a Paused cause (a held run, a login) stands for tasks no bot asked this person for
                # The row Needs you's pass already read for this snapshot (waiting_on.prime): no read of its own.
                asker = (H._one(c, "SELECT * FROM tasks WHERE id=?", (item["task_id"],)) or {}).get("requester_id")
                if H.is_bot(asker):
                    bots[asker] = bots.get(asker, 0) + 1
            over.append((len(items), person.actor, bots))
    if not over:
        return None
    rows = []
    for count, actor, bots in sorted(over, key=lambda row: (-row[0], row[1])):
        askers = sorted(bots.items(), key=lambda kv: (-kv[1], kv[0]))[:NEEDS_PLATE_ASKERS]
        asked = [{"bot": bot, "name": H.actor_name(c, bot), "count": n} for bot, n in askers]
        name = H.actor_name(c, actor)
        most = ", most asked by " + ", ".join(f"{a['name']} ({a['count']})" for a in asked) if asked else ""
        rows.append(
            {
                "person": actor,
                "name": name,
                "count": count,
                "askers": asked,
                "text": f"{name}: {count} on Needs you{most}.",
                "fixes": [_fix("Open tasks", "#/tasks")],
            }
        )
    worst = max(count for count, _, _ in over)
    return _check(
        "needs_plate",
        "Needs you plate",
        "bad" if worst > NEEDS_PLATE_BAD else "warn",
        _diagnostic_summary(
            f"{_plural(len(over), 'person has', 'people have')} more than {NEEDS_PLATE_WARN} items on Needs you.",
            rows,
        ),
        [_fix("Open tasks", "#/tasks")],
        items=rows,
    )


def never_stuck_reading(c):
    """What the never-stuck check finds over every open task: the seven rules' breaks (never_stuck.rules_hold) and
    where the ladder has the tasks (S15): waiting on nothing, on a retry, with a manager, reached a person, can't run.
    `tasks` keeps which tasks each number counts, and `broken_tasks` the tasks each break is about (never_stuck.Break,
    tied when it was found), so someone who reads only some tasks is shown only those. A private task no person who
    reads it can move is held, not a break: it counts under `no_one`, a number only. It reads every open task, so it
    runs hourly (record_never_stuck), not on each page load."""
    from . import never_stuck, task_depends, waiting_on

    found = never_stuck.rules_hold(c)
    breaks = [line for line in found if not getattr(line, "held", False)]
    tasks = {key: [] for key in NEVER_STUCK_COUNTS}
    tasks["no_one"] = [tid for line in found if getattr(line, "held", False) for tid in getattr(line, "tasks", ())]
    for task_id, level in c.execute(
        "SELECT t.id, CASE WHEN v.level='owner' AND v.tries>0 THEN 'retry' ELSE v.level END "
        "FROM visits v JOIN tasks t ON t.id=v.task_id WHERE v.left_at IS NULL AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id=t.id AND s.ended_at IS NULL) "
        f"AND NOT {task_depends.BLOCKED % 't'}"
    ):
        if level in ("retry", "manager", "person"):
            tasks[level].append(task_id)
    ids = [row[0] for row in c.execute("SELECT id FROM tasks WHERE status='open'")]
    for part in H.chunks(ids):
        waits = waiting_on.waiting_on_many(c, part)
        tasks["cant_run"] += [tid for tid, found in waits.items() if found and found[0].state == "paused"]
    about = [list(getattr(line, "tasks", ())) for line in breaks]
    for line, ids in zip(breaks, about, strict=True):
        if line.startswith("1:"):
            tasks["waiting_on_nothing"] += ids
    return {
        "at": H.now(),
        "breaks": breaks[:DIAGNOSTIC_LIMIT],
        "broken": len(breaks),
        "waiting_on_nothing": sum(1 for line in breaks if line.startswith("1:")),
        "retry": len(tasks["retry"]),
        "manager": len(tasks["manager"]),
        "person": len(tasks["person"]),
        "cant_run": len(tasks["cant_run"]),
        "no_one": len(set(tasks["no_one"])),
        "tasks": {key: sorted(set(found)) for key, found in tasks.items() if key != "broken"},
        "broken_tasks": about,
    }


NEVER_STUCK_TASKS_FIELDS = ("breaks", "tasks", "broken_tasks")


def record_never_stuck(store):
    """The hourly reading (backend/scheduler.py), kept for Health to show: its numbers under NEVER_STUCK_KEY, what
    names a task under NEVER_STUCK_TASKS_KEY."""
    with store.read() as c:
        reading = never_stuck_reading(c)
    numbers = {k: v for k, v in reading.items() if k not in NEVER_STUCK_TASKS_FIELDS}
    named = {k: reading[k] for k in NEVER_STUCK_TASKS_FIELDS}
    with store.transaction() as c:
        for key, value in ((NEVER_STUCK_KEY, numbers), (NEVER_STUCK_TASKS_KEY, named)):
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
    return reading


def _readable(c, visible_sql, ids):
    """Of `ids`, those a reader with `visible_sql` may read, in one statement."""
    if not ids:
        return set()
    return {
        row[0]
        for row in c.execute(
            f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})", (json.dumps(ids),)
        )
    }


def _never_stuck_check(c, visible_sql=None):
    """Health · Tasks (S15): the last hourly reading, as numbers only. Bad when any of the rules that hold at all times
    is broken. A rule break names a task and a bot, so its words are never shown: the owner and Admins (no
    `visible_sql`) get the company's numbers; anyone else gets the numbers over the tasks they may read."""

    def stored(key):
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    reading = stored(NEVER_STUCK_KEY)
    named = stored(NEVER_STUCK_TASKS_KEY)
    if not reading or (visible_sql and not (named and "broken_tasks" in named)):
        return _check("never_stuck", "Tasks", "unknown", "The hourly never-stuck reading has not run yet.")
    if named and "broken_tasks" in named:
        # The reading is up to an hour old: a task done since then no longer counts, so the numbers never say a task
        # is stuck after it closed. A task that got stuck since then waits for the next reading.
        listed, breaks = named["tasks"], named["broken_tasks"]
        ids = {tid for found in listed.values() for tid in found} | {tid for found in breaks for tid in found}
        readable = _readable(c, visible_sql or "1", sorted(ids))
        still_open = _readable(c, f"status='open' AND ({visible_sql or '1'})", sorted(ids))
        counts = {key: sum(1 for tid in listed.get(key, []) if tid in still_open) for key in NEVER_STUCK_COUNTS}
        counts["no_one"] = len({tid for tid in listed.get("no_one", []) if tid in still_open})
        # A break can be about a done task (a wake queued after it closed), so it counts until the next reading. It
        # counts for a reader of every task it is about; one about no task is the company's alone.
        counts["broken"] = (
            sum(1 for found in breaks if found and all(tid in readable for tid in found))
            if visible_sql
            else int(reading.get("broken") or 0)
        )
    else:
        counts = {key: int(reading.get(key) or 0) for key in NEVER_STUCK_COUNTS}
    summary = (
        f"{counts['waiting_on_nothing']} waiting on nothing, {counts['retry']} on a retry, "
        f"{counts['manager']} with a manager, {counts['person']} reached a person, {counts['cant_run']} can't run."
    )
    if reading.get("at") and str(reading["at"]) < H.shift(H.now(), minutes=-NEVER_STUCK_FRESH_MINUTES):
        summary += f" Read at {str(reading['at'])[11:16]} UTC; tasks closed since are left out."
    if counts["no_one"]:
        # The company's owner and Admins learn how many, never which: no one who reads them can move them.
        them = "it" if counts["no_one"] == 1 else "them"
        summary += f" {_plural(counts['no_one'], 'private task has', 'private tasks have')} no one to move {them}."
    if counts["broken"]:
        summary += f" {_plural(counts['broken'], 'rule break')}."
    status = "bad" if counts["broken"] else "warn" if counts["no_one"] else "ok"
    check = _check("never_stuck", "Tasks", status, summary)
    # The same numbers as fields, so a screen reads them rather than the sentence.
    check["counts"] = counts
    check["at"] = reading.get("at")
    return check


def _claimed_oks_check(c):
    """How many reports to Tico support a person's OK click claimed (support.ok_given) and Maintenance has not
    delivered yet (support.claimed): a number only, for the owner and Admins. Finance actions commit in the click's own
    transaction, so none of them ever waits here."""
    from . import support

    try:
        waiting = len(support.claimed(c))
        refused = support.refused_lately(c)
    except sqlite3.OperationalError:  # an install that never made the tables: nothing was claimed
        waiting = refused = 0
    said = []
    if waiting:
        said.append(
            f"{_plural(waiting, 'report')} to Tico support OK'd and not sent yet; Maintenance tries again each run."
        )
    if refused:
        said.append(f"{_plural(refused, 'report')} OK'd this week could not be sent; its task says why.")
    if not said:
        check = _check("claimed_oks", "OK'd sends", "ok", "Nothing OK'd waits to be sent.")
    else:
        check = _check("claimed_oks", "OK'd sends", "warn", " ".join(said))
    check["counts"] = {"claimed_not_delivered": waiting, "claimed_refused": refused}
    return check


def _prime_claims(c, settings=None):
    """What the claim check reads (execution._claim_refusal) for every bot and every computer the page checks, read once
    in set queries (claim_loads.read) and handed to each check the page makes, so the page makes a few statements
    however many bots it checks. Inside a snapshot the bots' own rows and the computers' rows are also primed for the
    readers that take them from the snapshot (prime_bot_rows, RUNNER_SQL)."""
    from . import claim_loads
    from .events import prime_bot_rows
    from .execution import RUNNER_SQL, capability_set
    from . import browser_exit
    from .read_cache import cache, prime

    slugs = [row[0] for row in c.execute("SELECT id FROM bots")]
    runners = {row["id"]: row for row in c.execute("SELECT * FROM runners_with_state")}
    if cache(c) is not None:
        prime_bot_rows(c, slugs)
        prime(c, RUNNER_SQL, {(rid,): row for rid, row in runners.items()})
    exits = any(browser_exit.CAPABILITY not in capability_set(row["capabilities"]) for row in runners.values())
    return claim_loads.read(c, settings, runners, slugs, exits=exits)


def view(c, who, settings, auth, github, config):
    if who.role != "bot":
        _person(who)
    kind = "owner" if who.role == "owner" else "admin" if auth.bot_admin(who) else who.role
    full = kind in ("owner", "admin")
    online = _online_runners(c)
    online_ids = {r["id"] for r in online}
    loads = _prime_claims(c, settings)
    limits = usage_limits.company(c)  # one, so the spend of every period is summed once for all the bots
    computers = _computers(c, online, settings, limits)
    waiting, slow, stuck_rows = _waiting(c, online_ids, settings, limits, loads)
    # A bot onboarding for a day is stuck too, unless something else already holds it (backend/setup_stuck.py).
    listed = {row["bot"] for row in stuck_rows}
    stuck_rows += [row for row in setup_stuck.stuck(c) if row["bot"] not in listed]
    if not full and stuck_rows:  # someone who is not an administrator hears only about the bots they may read
        readable = auth.bot_accesses(c, who, [row["bot"] for row in stuck_rows])
        stuck_rows = [row for row in stuck_rows if readable.get(row["bot"], auth.FULL)["read"]]
    blocked = _stuck(c, stuck_rows, online_ids, who, full, auth)
    on_fallback, limited, carrying = _fallbacks(c, loads) if kind != "bot" else ([], [], set())
    if not full and (on_fallback or limited):  # the same rule as `stuck`: only bots the caller may read
        readable = auth.bot_accesses(c, who, sorted({row["bot"] for row in on_fallback + limited}))
        on_fallback = [row for row in on_fallback if readable.get(row["bot"], auth.FULL)["read"]]
        limited = [row for row in limited if readable.get(row["bot"], auth.FULL)["read"]]
    failed, failures = _failed(c)
    checks = []
    # What bots run on when it is not what they are set to: two reviewers set to different models that both run on
    # the Gateway are not independent, and a person reads that here.
    if running_on := _fallback_lines(c, settings, on_fallback, carrying):
        checks.append(
            _check("on_fallback", "What bots run on", "info", running_on, [_fix("Open Models", "#/settings", "models")])
        )
    from . import task_privacy

    refused, queued = [], refused_queues(c, settings, loads)
    # One access check for every refused bot, and one query a chunk of jobs for their oldest queued messages.
    readable = auth.bot_accesses(c, who, sorted({row['bot'] for row in queued})) if queued else {}
    queued = [row for row in queued if readable.get(row['bot'], auth.FULL)['read']]
    found = {}
    for part in H.chunks(sorted({row['job'] for row in queued})):
        for job in c.execute(
            "SELECT j.id AS job_id, j.bot_id AS job_bot, m.* FROM jobs j JOIN messages m ON m.id=j.message_id "
            f"WHERE j.id IN ({','.join('?' * len(part))}) AND j.state='queued' AND j.held=0",
            part,
        ):
            found[(job['job_id'], job['job_bot'])] = job
    msgs = [found[(row['job'], row['bot'])] for row in queued if (row['job'], row['bot']) in found]
    shown = {m['id'] for m in task_privacy.readable_messages(c, who.actor, msgs)} if msgs else set()
    for row in queued:
        msg = found.get((row['job'], row['bot']))
        if msg is None or msg['id'] not in shown:
            continue
        refused.append(
            {
                **row,
                'text': f"{row['name']}: oldest queued job refused for over 12 hours; {row['reason']}.",
                'fixes': [_fix(*RUNS)],
            }
        )
    if refused:
        checks.append(
            _check(
                'queue_refused',
                'Queued work refused over 12 hours',
                'warn',
                _diagnostic_summary('Queued jobs need attention.', refused),
                items=refused,
            )
        )
    if full and (company := company_import_failed(c)):
        checks.append(company)
    if (full or kind == "bot") and (latency := runner_route_latency(c)):
        checks.append(latency)
    if full:
        from . import org_turns

        incident = org_turns.saved(c, org_turns.INCIDENT)
        if incident.get('opened_at'):
            # in_use / warning / stop come from admission. The fixer's saved finding already holds the detailed
            # queue classification; rebuilding a fleet snapshot on every Health poll is not needed here.
            value = org_turns.admission(c)
            # The turn fixer's finding (backend/turn_fixer.py) once it has run: one per incident, rewritten in place.
            found = (incident.get('finding') or {}) if incident.get('active') else {}
            text = (
                (
                    found.get('text')
                    or f"{value['in_use']} company turns in use; warning {value['warning']}, stop {value['stop']}."
                )
                + ' '
                + (
                    'Incident opened ' + _clock(incident['opened_at'])
                    if incident.get('active')
                    else 'Incident resolved ' + _clock(incident.get('resolved_at', ''))
                )
            )
            fixes = [_fix(*COMPUTERS)]
            # Items share the shape of every other check's (bot, name, text, fixes) so issue lists can sort and
            # show them; a resolved incident is an ok summary with no items, like any other clear check.
            items = (
                [
                    {
                        "bot": "",
                        "name": "Company turns",
                        "text": text,
                        "incident": incident['id'],
                        "opened_at": incident['opened_at'],
                        "at_stop": bool(incident.get('at_stop')),
                        "in_use": value['in_use'],
                        "warning": value['warning'],
                        "stop": value['stop'],
                        "finding": found or None,
                        "task_id": incident.get('task_id'),
                        "fixes": fixes,
                    }
                ]
                if incident.get('active')
                else []
            )
            checks.append(
                _check(
                    'company_turns',
                    'Company turns',
                    'warn' if incident.get('active') else 'ok',
                    text,
                    fixes,
                    items=items,
                )
            )
    if full and (listening := _listening(c, settings)):
        checks.append(listening)
    storage = storage_settings(c, settings)
    if full and storage.blob_bucket:
        health = c.execute("SELECT * FROM service_health WHERE service='blob-copy'").fetchone()
        detail = json.loads(health["detail"] or "{}") if health else {}
        write_health = c.execute("SELECT detail FROM service_health WHERE service='blob-s3'").fetchone()
        write_detail = json.loads(write_health["detail"] or "{}") if write_health else {}
        location = storage.blob_bucket + ("/" + storage.blob_prefix if storage.blob_prefix else "")
        if write_detail.get("error") and write_detail.get("location") == location:
            checks.append(
                _check(
                    "blob_storage",
                    "File storage",
                    "warn",
                    write_detail["error"],
                    [_fix("Storage", "https://github.com/ticoteam/tico/blob/main/docs/files.md#storage")],
                )
            )
        elif detail.get("running") or detail.get("error"):
            summary = f"Moving files to S3: {detail.get('done', 0)} of {detail.get('total', 0)}"
            if detail.get("error"):
                summary += ". " + detail["error"]
            checks.append(_check("blob_storage", "File storage", "bad" if detail.get("error") else "info", summary))
    if full and not storage.blob_bucket and not settings.loopback and not settings.demo and not settings.rehearsal:
        checks.append(
            _check(
                "blob_storage",
                "File storage",
                "info",
                "Files are on this computer's disk. A file store (S3) is recommended.",
                [_fix("Storage", "https://github.com/ticoteam/tico/blob/main/docs/files.md#storage")],
            )
        )
    # Connection health belongs to the person; only the Team owner may see another person's.
    for row in c.execute("SELECT actor,metadata FROM granola_connections"):
        meta = json.loads(row["metadata"])
        if meta.get("needs_signin") and (row["actor"] == who.actor or who.role == "owner"):
            checks.append(
                _check(
                    "granola:" + row["actor"],
                    "Granola",
                    "warn",
                    "Granola needs sign-in again",
                    [_fix("Open Meetings", "#/meetings")],
                )
            )

    if full:
        notice = config.get("update") or {}
        if notice.get("scheduled"):  # Tico support's update of this hosted server (backend/hosted_updates.py)
            checks.append(
                _check(
                    "version",
                    "Version",
                    "info",
                    f"Tico will update this server to {_v(notice['scheduled'])} when idle.",
                    [_fix("What is new", "#/changelog")],
                )
            )
        elif notice.get("available") and notice.get("managed"):  # Tico runs this server's updates (TICO_UPDATES)
            checks.append(
                _check(
                    "version",
                    "Version",
                    "info",
                    f"{_v(notice.get('latest'))} is out. You are on {_v(releases.version())}. "
                    "Tico updates this server.",
                    [_fix("What is new", "#/changelog")],
                )
            )
        elif notice.get("held"):  # 0.6 starts fresh: not offered while this company has tasks (releases.offered)
            checks.append(
                _check(
                    "version",
                    "Version",
                    "info",
                    f"{_v(notice.get('latest'))} is out. {notice['held']}.",
                    [_fix("What is new", "#/changelog")],
                )
            )
        elif notice.get("available"):
            checks.append(
                _check(
                    "version",
                    "Version",
                    "warn",
                    f"{_v(notice.get('latest'))} is available. You are on {_v(releases.version())}.",
                    [_fix("Update", click="#new-version"), _fix("What is new", "#/changelog")],
                )
            )
        else:
            checks.append(
                _check(
                    "version",
                    "Version",
                    "ok",
                    f"Running {_v(releases.version())}, the latest we know of."
                    if notice.get("latest")
                    else f"Running {_v(releases.version())}.",
                )
            )
        # The image this server started from was not built from its release's tag (backend/flight.py).
        from .flight import last_start

        start = last_start(c)
        state = (start or {}).get("provenance")
        if state in ("mismatch", "modified") and start.get("version") == releases.version():
            checks.append(
                _check(
                    "image",
                    "Release image",
                    "warn",
                    f"This server's code differs from the published {_v(start['version'])} image."
                    if state == "modified"
                    else f"This image says {_v(start['version'])} but was built from commit "
                    f"{str(start.get('commit'))[:7]}, not the published release.",
                )
            )

    notice = access.bot_access_notice(c) if kind == "owner" else None
    if notice:
        # Stays until the owner dismisses it or saves any bot's access: the change reset every bot to Open.
        checks.append(
            _check(
                "bot_access",
                "Bot access",
                "info",
                str(notice.get("message") or ""),
                [_fix("Open bots", "#/settings", "bots")],
            )
        )

    # A go-live waiting for its computer to get the repository finishes by itself (backend/go_live.py); until then
    # the bot is placed but not on. Seen by the owner and admins, and by a person who manages that bot.
    from .go_live import EXPIRY_DAYS, waiting as waiting_go_lives

    pending = waiting_go_lives(c, who, auth, full)
    if pending:
        checks.append(
            _check(
                "go_live_waiting",
                "Going live",
                "info",
                "; ".join(
                    f"{_bot_name(c, bot)} is waiting for {computer} to get its repository (since {since})"
                    for bot, computer, since in pending[:3]
                )
                + ("" if len(pending) <= 3 else f"; and {len(pending) - 3} more")
                + f". Tico turns each on when its computer has the repository; after {EXPIRY_DAYS} days "
                "it stops and tells the person who asked.",
                [_fix("Open bots", "#/settings", "bots")],
            )
        )
    withheld = _withheld(c, settings) if full else {}
    if withheld:
        checks.append(_history_check(c, withheld))
    unpublished = _unpublished(c, withheld) if full else []
    if unpublished:
        checks.append(
            _check(
                "publish",
                "Bot history",
                "warn",
                "Some bots' history is not on GitHub yet: "
                + "; ".join(f"{_bot_name(c, bot)} ({why})" for bot, why in unpublished[:3])
                + ("." if len(unpublished) <= 3 else f"; and {len(unpublished) - 3} more."),
                [_fix("Open bots", "#/settings", "bots")],
            )
        )
    denied = (_reported(c, DENIED_WARNING) + _reported(c, OLD_DENIED_WARNING)) if full else []
    if denied:
        checks.append(
            _check(
                "claude_denies",
                "Claude Code permissions",
                "warn",
                "; ".join(f"{_bot_name(c, bot)}: {DENIED_WARNING}{rest}" for bot, rest in denied[:3])
                + ("." if len(denied) <= 3 else f"; and {len(denied) - 3} more."),
                [_fix("Open bots", "#/settings", "bots")],
            )
        )
    if full:
        from . import webhooks

        hooks = webhooks.health_summary(c)
        said = []
        if hooks["failed"]:
            said.append(
                "gave up on events for "
                + ", ".join(hooks["failed"][:3])
                + (f" and {len(hooks['failed']) - 3} more" if len(hooks["failed"]) > 3 else "")
            )
        if hooks["flaky"]:
            said.append("more than half of the attempts to " + ", ".join(hooks["flaky"][:3]) + " failed")
        if hooks["disabled"]:
            said.append("turned off " + ", ".join(hooks["disabled"][:3]) + " after five days of failed attempts")
        if hooks["skipped"]:
            said.append(
                f"{hooks['skipped']} events skipped endpoints past the limit of {webhooks.MAX_PER_EVENT} per event"
            )
        if said:
            checks.append(
                _check(
                    "webhooks",
                    "Outgoing webhooks",
                    "warn",
                    "In the last 24 hours Tico " + "; ".join(said) + ".",
                    [_fix("Open webhooks", "#/tools/webhooks")],
                )
            )
    if full and (denies := _harness_denies_check(c, settings)):
        checks.append(denies)
    if full:
        from .worktrees import current_error, supported, statuses

        if supported(c):
            usage, failing = {}, []
            for row in statuses(
                c,
                c.execute(
                    "SELECT t.owner_id,l.* FROM task_links l JOIN tasks t ON t.id=l.task_id WHERE l.kind='worktree' AND l.state<>'removed'"
                ),
            ):
                detail = json.loads(row['detail'] or '{}')
                bot = H.actor_id(row['owner_id'])
                usage[bot] = usage.get(bot, 0) + (0 if row['state'] == 'retired' else detail.get('size_mb', 0))
                if error := current_error(row['state'], detail):
                    failing.append((bot, error))
            names = _bot_names(c, [bot for bot, _ in failing])  # one read for the bots named, not one a worktree
            errors = [names[bot] + ': ' + error for bot, error in failing]
            if usage:
                checks.append(
                    _check(
                        'worktrees',
                        'Task worktrees',
                        'warn' if errors else 'ok',
                        '; '.join(errors[:3])
                        if errors
                        else '; '.join(f'{_bot_name(c, bot)}: {size:g} MB' for bot, size in sorted(usage.items())),
                        [_fix('Open tasks', '#/tasks')],
                    )
                )
            # A task waiting for room for a worktree is waiting, not failing (backend/worktree_waits.py). Named by bot
            # and repository only: a private task's title is not everyone's.
            from .worktree_waits import waiting as worktree_waiting

            if waits := worktree_waiting(c):

                def age(since):
                    minutes = int(max(0, (datetime.now(timezone.utc) - H.parse_ts(since)).total_seconds()) // 60)
                    return f'{minutes} min' if minutes < 120 else f'{minutes // 60} h'

                checks.append(
                    _check(
                        'worktree_waits',
                        'Waiting for a worktree',
                        'info',
                        '; '.join(
                            f"{H.actor_name(c, w['owner'])}: {w['repo']}, {'setting up' if w['state'] == 'reserved' else 'no room'} "
                            f"for {age(w['created_at'])} ({w['reason']})"
                            for w in waits[:3]
                        )
                        + ('.' if len(waits) <= 3 else f'; and {len(waits) - 3} more.'),
                        [_fix('Open tasks', '#/tasks')],
                    )
                )
            # What freed a place lately, and why (backend/worktrees.py released_event): finished work releasing its
            # tree is the normal way room comes back, so it is said, not warned about.
            since = H.shift(H.now(), hours=-24)
            reasons = {}
            for (reason,) in c.execute(
                "SELECT json_extract(detail,'$.reason') FROM events WHERE action='worktree.released' AND created_at>=?",
                (since,),
            ):
                reasons[reason or 'cleanup'] = reasons.get(reason or 'cleanup', 0) + 1
            if reasons:
                checks.append(
                    _check(
                        'worktrees_released',
                        'Worktrees released (24 h)',
                        'info',
                        '; '.join(f'worktree released: {reason} ({n})' for reason, n in sorted(reasons.items())) + '.',
                        [_fix('Open tasks', '#/tasks')],
                    )
                )
    if full and (missing_tools := _missing_tool_credentials(c, online_ids)):
        checks.append(
            _check(
                "tool_credentials",
                "Tool credentials",
                "warn",
                "Missing Credential: "
                + "; ".join(f"{tool} on {label}" for tool, label in missing_tools[:5])
                + ("." if len(missing_tools) <= 5 else f"; and {len(missing_tools) - 5} more."),
                [_fix("Open Keys", "#/tools/keys?show=credentials"), _fix("Open Computers", "#/settings", "devices")],
            )
        )
    if full and (reserved := reserved_credentials(c, who, tuple(settings.credential_admins or ()), auth)):
        checks.append(
            _check(
                "reserved_credentials",
                "Credential names",
                "warn",
                "Bots run without these credentials, because Tico keeps their variable names for itself: "
                + "; ".join(f"{name} ({env}) for {_bot_name(c, bot)}" for name, env, bot in reserved[:5])
                + ("." if len(reserved) <= 5 else f"; and {len(reserved) - 5} more.")
                + " Give each a variable name that does not start with TICO_, DYLD_ or LD_.",
                [_fix("Open Keys", "#/tools/keys?show=credentials")],
            )
        )
    lacking = missing_repositories(c, online_ids) if full else []
    if lacking:
        checks.append(
            _check(
                "repositories",
                "Bot repositories",
                "bad",
                "Bots cannot run because their computer has no repository for them: "
                + "; ".join(
                    f"{_bot_name(c, bot)} on {label}: {why}. {repository_fix(c, bot, label, settings.github_owner)[1]}"
                    for bot, label, why in lacking[:3]
                )
                + ("." if len(lacking) <= 3 else f"; and {len(lacking) - 3} more."),
                [_fix("Open bots", "#/settings", "bots")],
            )
        )
    copies = failed_repository_copies(c, online_ids) if full else []
    if copies:
        checks.append(
            _check(
                "repository_copies",
                "Repository copies",
                "warn",
                "A computer could not copy a repository its bots work in: "
                + "; ".join(f"{repository} on {label}: {why}" for label, repository, why in copies[:3])
                + ("" if len(copies) <= 3 else f"; and {len(copies) - 3} more")
                + ". Restart that computer's runner to retry now.",
                [_fix("Open Computers", "#/settings", "devices")],
            )
        )
    if unreviewed := _unreviewed_runs(c, who, auth):
        checks.append(unreviewed)
    if kind != "bot" and (plate := _plate_check(c, who, auth, full)):
        checks.append(plate)
    from .app_delivery import health_problems

    for key, summary in health_problems(c, who, auth, online_ids):
        checks.append(_check(key, "Desktop apps", "warn", summary, [_fix("Open Computers", "#/settings", "devices")]))
    fixes = [_fix("Add a computer", "#/settings", "devices")] if full else []
    if not computers:
        checks.append(_check("computers", "Computers", "bad", "No computer is set up. Bots need one to run.", fixes))
    elif not online and not any(x["resting"] for x in computers):
        checks.append(
            _check(
                "computers",
                "Computers",
                "bad",
                "Every computer is offline.",
                [_fix("Open Computers", "#/settings", "devices")] if full else [],
            )
        )
    elif off := [x for x in computers if not x["online"] and not x["resting"]]:
        checks.append(
            _check(
                "computers",
                "Computers",
                "warn",
                f"{len(online)} of {len(computers)} online. Offline: " + ", ".join(x["label"] for x in off) + ".",
                [_fix("Open Computers", "#/settings", "devices")] if full else [],
            )
        )
    else:
        asleep = sum(1 for x in computers if x["resting"])
        checks.append(
            _check(
                "computers",
                "Computers",
                "ok",
                " ".join(
                    ([f"{_plural(len(online), 'computer')} online."] if online else [])
                    + ([f"{_plural(asleep, 'cloud machine')} stopped until work comes."] if asleep else [])
                ),
            )
        )

    from .performance import load_statuses, recent_overloads
    from .start_places import MEMORY_HOLD, NOT_REPORTING, computer_hold, hold_rows
    from .cloud_machines import resting as resting_machines

    resting_ids = resting_machines(c)
    loads = load_statuses(c, [computer["id"] for computer in computers])  # one read for every computer
    rows = hold_rows(c, [computer["id"] for computer in computers])  # and their runner rows, once
    for computer in computers:
        if full or kind == "bot":
            checks.append(
                _check(
                    "turns:" + computer["id"],
                    "Turns",
                    "ok",
                    computer["label"] + ": " + computer["turn_summary"],
                    [_fix("Open Computers", "#/settings", "devices")] if full else [],
                )
            )
        # Its last boot's load cannot hold a machine that is asleep on purpose.
        hold = None
        if computer["id"] not in resting_ids:
            hold = computer_hold(c, computer["id"], status=loads[computer["id"]], row=rows.get(computer["id"]))
            if not hold:
                # Runner-local memory admission still reports a wait when diagnostics are off.
                local_memory = next(
                    (w['reason'] for w in computer['turn_counts']['waits'] if w['reason'].startswith(MEMORY_HOLD)), None
                )
                if local_memory:
                    hold = local_memory, local_memory
        if hold:
            words = (
                "overloaded now (CPU or memory above 90% for five minutes)."
                if hold[0] == "computer busy"
                else "not reporting: its last sample is over two minutes old."
                if hold[0] == NOT_REPORTING
                else f"{hold[1]}: new starts wait until it is below 80%."
            )
            checks.append(
                _check(
                    "runner_load:" + computer["id"],
                    "Load",
                    "warn",
                    f"{computer['label']}: {words}",
                    [_fix("Open Computers", "#/settings", "devices")],
                )
            )

    if full:
        overloads = recent_overloads(c, [computer["id"] for computer in computers])
        from .cloud_machines import disk_words

        cloud_disks = disk_words(c)  # a cloud machine grows to a bigger size instead
        for computer in computers:
            disk = computer.get("disk") or {}
            cloud = cloud_disks.get(computer["id"])
            if cloud is not None:
                # A cloud machine's own reading since it last started; none when it rests or has not read since.
                disk = {**disk, **cloud["disk"]} if cloud["disk"] and computer["id"] not in resting_ids else {}
            total, free = disk.get("total_bytes"), disk.get("free_bytes")
            if isinstance(total, int) and total > 0 and isinstance(free, int) and 1 - free / total > 0.85:
                cleanup = disk.get("cleanup") or {}
                done = (
                    f" Its own clean-up at {cleanup.get('at', '')[11:16]} UTC freed "
                    f"{(cleanup.get('freed_bytes') or 0) / 1024**3:.1f} GB; what is left is not safe for it to remove."
                    if cleanup.get("still_low")
                    else ""
                )
                advice = (cloud or {}).get("advice") or (
                    "Free space on this computer; for Docker, run `docker image prune -a` to remove unused images. "
                    "The update retries when space frees."
                )
                checks.append(
                    _check(
                        "disk:" + computer["id"],
                        "Disk space",
                        "warn",
                        f"{computer['label']}: disk is {100 * (1 - free / total):.0f}% full "
                        f"({free / 1024**3:.1f} GB free).{done} " + advice,
                        [_fix("Open Computers", "#/settings", "devices")],
                    )
                )
            # Diagnostics only: an overload is shown here and on the computer's Performance page; nothing acts on it.
            from .performance import describe

            overload = overloads[computer["id"]]
            if overload:
                checks.append(
                    _check(
                        "performance:" + computer["id"],
                        "Load",
                        "warn",
                        f"{computer['label']}: {'overloaded now' if not overload['ended_at'] else 'was overloaded'}"
                        f" ({describe(overload, lambda bot: _bot_name(c, bot, DELETED_BOT))}).",
                        [_fix("Open Performance", "#/computers/" + computer["id"] + "/performance")],
                    )
                )
            container = computer.get("container_exec") or {}
            if computer["online"] and container.get("ok") is False:
                checks.append(
                    _check(
                        "container_exec:" + computer["id"],
                        "Containers",
                        "warn",
                        f"{computer['label']} ({computer['owner_id']}): containers do not start "
                        f"({container.get('error') or 'failed'}). Restart Docker there; bot container work stalls until then.",
                        [_fix("Open Computers", "#/settings", "devices")],
                    )
                )
            engine = computer.get("engine") or {}
            if computer["online"] and engine.get("name") == "python":
                checks.append(
                    _check(
                        "engine:" + computer["id"],
                        "Runner",
                        "warn",
                        f"{computer['label']} runs the Python runner, which Tico no longer ships. "
                        "Update this computer.",
                        [_fix("Open Computers", "#/settings", "devices")],
                    )
                )
            if computer["online"] and (computer.get("github_auth") or {}).get("ok") is False:
                checks.append(
                    _check(
                        "github_auth:" + computer["id"],
                        "GitHub sign-in",
                        "warn",
                        f"{computer['label']}: GitHub sign-in failed ({computer['github_auth'].get('error') or 'HTTP 401'}). "
                        "Bots there cannot open pull requests. Check the GitHub connection.",
                        [
                            _fix("Open GitHub settings", "#/tools/github"),
                            _fix("Open Computers", "#/settings", "devices"),
                        ],
                    )
                )
            if computer["online"] and (computer.get("browser_launch") or {}).get("ok") is False:
                checks.append(
                    _check(
                        "browser_launch:" + computer["id"],
                        "Browser",
                        "warn",
                        f"{computer['label']}: browser does not start ({computer['browser_launch'].get('error') or 'failed'}). "
                        "Bots there cannot run browser checks. Update the computer, or install the browser's system libraries.",
                        [_fix("Open Computers", "#/settings", "devices")],
                    )
                )
        wanted = _wanted_runtimes(providers.load(c, settings))
        # Signed in by the computer's own login or by the company's plan given to it (`ready` on each runtime row).
        signed = next(
            (r["name"] for x in computers if x["online"] for r in x["runtimes"] if r["name"] in wanted and r["ready"]),
            "",
        )
        gateway_runs = next(
            (r["name"] for x in computers if x["online"] for r in x["runtimes"] if r["gateway_covered"]),
            "",
        )
        rejected = _rejected(computers)
        if not wanted:
            checks.append(
                _check(
                    "models",
                    "Models",
                    "warn",
                    "No AI provider is chosen yet.",
                    [_fix("Choose providers", "#/settings", "models")],
                )
            )
        elif online and not signed and rejected:
            checks.append(
                _check(
                    "models",
                    "Models",
                    "bad",
                    _rejected_summary(rejected),
                    [
                        _fix("Open Keys", "#/tools/keys?show=credentials"),
                        _fix("Open Computers", "#/settings", "devices"),
                    ],
                )
            )
        elif online and not signed and gateway_runs:
            # Its bots' turns run on the Tico AI Gateway until the subscription is signed in (models_preset.gateway_line).
            checks.append(
                _check(
                    "models",
                    "Models",
                    "ok",
                    f"Bots run on Tico AI Gateway until {_harness_name(gateway_runs)} is signed in.",
                )
            )
        elif online and not signed:
            checks.append(
                _check(
                    "models",
                    "Models",
                    "bad",
                    "No online computer is signed in to your model.",
                    [_fix("Open Computers", "#/settings", "devices")],
                )
            )
        elif signed and rejected:
            checks.append(
                _check(
                    "models",
                    "Models",
                    "warn",
                    f"{_harness_name(signed)} is signed in on another computer. " + _rejected_summary(rejected),
                    [
                        _fix("Open Keys", "#/tools/keys?show=credentials"),
                        _fix("Open Computers", "#/settings", "devices"),
                    ],
                )
            )
        elif signed:
            checks.append(_check("models", "Models", "ok", f"{_harness_name(signed)} is signed in."))
        else:
            checks.append(_check("models", "Models", "unknown", "Nothing to check until a computer is online."))

    if full and (unsigned := _needs_sign_in(computers)):
        checks.append(
            _check(
                "computer_signin",
                "Computer sign-in",
                "warn",
                "; ".join(f"{label}: sign in to {model}" for label, model in unsigned[:5])
                + ("." if len(unsigned) <= 5 else f"; and {len(unsigned) - 5} more.")
                + " Sign in from Settings > Computers (BotOps can start it). To have new computers sign in by "
                "themselves, store the company's model key in Tools > Keys and give it to every computer.",
                [_fix("Open Computers", "#/settings", "devices"), _fix("Open Keys", "#/tools/keys?show=credentials")],
            )
        )
    # A signed-in API key can be paid and usable, but it never spends a subscription's allowance. Keep this visible
    # before an upgrade moves bots to the computer login, even when none of those bots has queued work yet.
    logins = [
        (computer, runtime)
        for computer in computers
        if computer["online"]
        for runtime in computer["runtimes"]
        if runtime["login_warning"] or runtime["login_unavailable"]
    ]
    if logins:
        unavailable = any(runtime["login_unavailable"] for _, runtime in logins)
        summary = (
            "; ".join(
                f"{computer['label']} / {_harness_name(runtime['name'])}: "
                + (
                    "bots depend on an unavailable computer login. "
                    + (
                        runtime['login_problem'] + ". Check this tool in Settings > Computers"
                        if runtime['login_problem']
                        else "Sign in or switch account in Settings > Computers"
                    )
                    if runtime["login_unavailable"]
                    else runtime["login_warning"]
                )
                for computer, runtime in logins[:5]
            )
            + (f"; and {len(logins) - 5} more." if len(logins) > 5 else "")
            if full
            else f"{len(logins)} computer login(s) used by bots need attention. Ask a computer operator to check Settings > Computers."
        )
        checks.append(
            _check(
                "computer_logins",
                "Computer logins used by bots",
                "bad" if unavailable else "warn",
                summary,
                [_fix("Open Computers", "#/settings", "devices")] if full else [],
            )
        )
    # The Tico AI Gateway's word on the company's key, for the owner who manages the connection: 80% of the month's cap
    # used, the cap reached, or the key revoked (backend/gateway_connection.py).
    if kind == "owner" and (gateway := gateway_connection.health(c, settings)):
        checks.append(
            _check(
                "gateway",
                "Tico AI Gateway",
                gateway["status"],
                gateway["summary"],
                [_fix("Open Models", "#/settings", "models")],
            )
        )
    # Webhooks waiting at Tico Gateway that this server is not collecting (backend/hook_relay.py).
    if kind == "owner" and (relay := hook_relay.health(c, settings)):
        checks.append(
            _check(
                "hook_relay",
                "Webhooks through Tico Gateway",
                relay["status"],
                relay["summary"],
                [_fix("Open Webhooks", "#/settings/models/wh-gateway")],
            )
        )
    # Sleep when idle is on but Tico could not be asked to stop this server (backend/sleep_idle.py).
    if kind == "owner" and (sleep := sleep_idle.health(c, settings)):
        checks.append(
            _check(
                "sleep_idle",
                "Sleep when idle",
                sleep["status"],
                sleep["summary"],
                [_fix("Open Computers", "#/settings", "devices")],
            )
        )
    # Tico HQ's nightly billing check, on the Tico team's own install only (the one server holding HQ's read-only staff
    # key): each mismatch, and a check that missed a night or cannot be read, red (backend/billing_check.py).
    if kind == "owner":
        checks.extend(billing_check.health(c, settings))
    if full and (versions := runner_versions.health_check(computers)):
        checks.append(versions)
    from .cloud_machines import health as cloud_health

    cloud = cloud_health(c, settings) if full else None
    silent = silent_computers(c) if waiting else []
    waiting_status, waiting_reasons = "ok", []
    if waiting:
        names = ", ".join(w["name"] for w in waiting[:5])
        # Work piling up behind a computer that stopped checking in is an outage even while another computer is up.
        stopped = "".join(
            f" {x['label'] if full else 'A computer'} has not checked in for {x['minutes']} minutes "
            f"with {_silent_words(x)}."
            for x in silent[:3]
        )
        waiting_status = "bad" if silent or not online else "warn"
        waiting_reasons.append(
            f"{_plural(len(waiting), 'bot')} cannot run because their computer is offline"
            + (f": {names}." if full else ".")
            + stopped
        )
    if cloud:
        waiting_status = "bad" if "bad" in (waiting_status, cloud["status"]) else cloud["status"]
        waiting_reasons.append(cloud["summary"])
    checks.append(
        _check(
            "waiting",
            "Bots waiting",
            waiting_status,
            " ".join(waiting_reasons) if waiting_reasons else "Every active bot has a computer that is up.",
            [_fix("Open Computers", "#/settings", "devices")] if full and waiting_reasons else [],
        )
    )
    # An offline computer (or none) is "Bots waiting"; this check is everything else, so one cause is one item.
    held = {w["bot"] for w in waiting}
    # A bot stuck in onboarding has its own check below ("Onboarding not finished"). A quarantined bot is BotOps's to
    # review, so it has its own check (after this one): Settings > Health lists it, the person's status dot does not.
    quarantined = [s for s in blocked if s["bot"] not in held and s["reason"] == "quarantined"]
    blocking = [s for s in blocked if s["bot"] not in held and s["reason"] not in ("setup", "quarantined")]
    busy = [row for row in slow if row["reason"] == "busy"]
    resting = [row for row in slow if row.get("resting")]
    readable = (
        auth.bot_accesses(c, who, [row["bot"] for row in busy + resting]) if (busy or resting) and not full else {}
    )
    busy_rows = _stuck(
        c,
        [
            {**row, "reason": row["wait_reason"], "runtime": ""}
            for row in busy
            if full or readable.get(row["bot"], auth.FULL)["read"]
        ],
        online_ids,
        who,
        full,
        auth,
    )
    resting_rows = _stuck(
        c, [row for row in resting if full or readable.get(row["bot"], auth.FULL)["read"]], online_ids, who, full, auth
    )
    if blocking:
        items = _queue_items(blocking + busy_rows + resting_rows, full, kind == "bot") if full or kind == "bot" else []
        bad = any(s["reason"] not in ("slow", *QUEUE_WAITS) for s in blocking)
        checks.append(
            _check(
                "queue",
                "Stuck bots",
                "bad" if bad else "warn",
                _diagnostic_summary(f"{_plural(len(blocking), 'bot')} with work that is not starting.", items),
                [_fix("Open Runs", "#/settings", "runs")] if full else [],
                items=items,
            )
        )
    elif resting:
        items = _queue_items(resting_rows + busy_rows, full, kind == "bot") if full or kind == "bot" else []
        checks.append(
            _check(
                "queue",
                "Work queueing",
                "info",
                _diagnostic_summary("Work is waiting on purpose.", items),
                [_fix("Open Runs", "#/settings", "runs")] if full else [],
                items=items,
            )
        )
    elif slow and len(busy) == len(slow):
        items = _queue_items(busy_rows, full, kind == "bot") if full or kind == "bot" else []
        checks.append(
            _check(
                "queue",
                "Work queueing",
                "info",
                _diagnostic_summary("Queued behind other work.", items),
                [_fix("Open Runs", "#/settings", "runs")] if full else [],
                items=items,
            )
        )
    elif slow:
        checks.append(
            _check(
                "queue",
                "Work queueing",
                "warn",
                f"{_plural(len(slow), 'bot')} with work waiting more than {QUEUE_MINUTES} minutes"
                + (": " + ", ".join(s["name"] for s in slow[:5]) + "." if full else "."),
                [_fix("Open Runs", "#/settings", "runs")] if full else [],
            )
        )
    else:
        checks.append(_check("queue", "Work queueing", "ok", "No work is waiting long."))
    if quarantined:
        items = _queue_items(quarantined, full, kind == "bot") if full or kind == "bot" else []
        checks.append(
            _check(
                "quarantine",
                "Quarantined bots",
                "bad",
                _diagnostic_summary(
                    f"{_plural(len(quarantined), 'bot')} quarantined; queued work waits until the quarantine clears.",
                    items,
                ),
                [_fix("Open Runs", "#/settings", "runs")] if full else [],
                items=items,
            )
        )
    for slug, name, count, what, oldest in unfinished_setup(c) if full else ():
        checks.append(
            _check(
                "setup:" + slug,
                "Onboarding not finished",
                "warn",
                f"{name} is still onboarding; {_plural(count, what)} waiting since {oldest[:10]}.",
                [_fix("Open its page", "#/bot/" + slug)],
            )
        )
    if full and (heavy := _token_heavy(c, settings.token_alert_input)):
        checks.append(
            _check(
                "tokens",
                "Token use",
                "warn",
                "; ".join(
                    f"{name}: {_tokens_words(n)} uncached input tokens in 24 h"
                    for name, n in ((_bot_name(c, bot, DELETED_BOT), n) for bot, n in heavy[:5])
                )
                + ("." if len(heavy) <= 5 else f"; and {len(heavy) - 5} more."),
                [_fix("Open Usage", "#/usage")],
            )
        )

    # Tasks (S15): where the never-stuck ladder has them, from its hourly reading, as numbers: the company's for the
    # owner and Admins, the tasks they may read for anyone else.
    checks.append(_never_stuck_check(c, None if full else auth.task_sql(c, who)))
    if full:
        checks.append(_claimed_oks_check(c))

    checks.extend(_aged_pr_checks(c, who, auth, full, kind))

    if full and (stuck := watchers.problems(c, online_ids)):
        checks.append(
            _check(
                "watchers",
                "Watchers",
                "warn",
                "; ".join(f"{_bot_name(c, bot)}/{name} {why}" for bot, name, why in stuck[:3])
                + ("." if len(stuck) <= 3 else f"; and {len(stuck) - 3} more."),
                [_fix("Open Runs", "#/settings", "runs")],
            )
        )
    if full and (mixed := inbox_isolation.violations(c, roster(c))):
        checks.append(
            _check(
                "inbox",
                "Inbox bots",
                "warn",
                "A computer kept for inbox bots also runs "
                + "; ".join(f"{v['label']}: {', '.join(v['others'])}" for v in mixed[:3])
                + ". The inbox bots' mail key can open every mailbox, so those bots could read it. "
                "Move them to another computer.",
                [_fix("Open Computers", "#/settings", "devices")],
            )
        )
    from . import browser_commands

    if full and (browser := browser_commands.health_problem(c)):
        checks.append(
            _check("browser_host", "Browser host", "warn", browser, [_fix("Open Computers", "#/settings", "devices")])
        )
    beside = _member_bots_beside_shared_keys(c) if full else []
    if beside:
        checks.append(
            _check(
                "member_bots",
                "Members' bots",
                "warn",
                "Bots members created run on a computer that holds keys every bot there receives "
                "(secrets/_shared.env): "
                + "; ".join(f"{label}: {', '.join(_bot_name(c, b) for b in bots[:3])}" for label, bots in beside[:3])
                + ". A member's bot instructions could ask a run for them. Move those bots to a computer "
                "with no shared keys and open only that one to members' bots (Settings > Computers).",
                [_fix("Open Computers", "#/settings", "devices")],
            )
        )
    exposed = _mail_key_exposed(c) if full else []
    if exposed:
        checks.append(
            _check(
                "mail_key",
                "Mail key",
                "warn",
                "The company's Google mail key can be read by every bot on "
                + ", ".join(exposed[:3])
                + ". A bot talked into it could read every mailbox. Use a Linux Docker runner with the "
                "current runner.compose.yaml, where the runner keeps the key to itself (docs/mail.md).",
                [_fix("Open Computers", "#/settings", "devices")],
            )
        )
    if full:
        checks.append(_github(c, github, settings))
        if repo_create := _github_repo_create(c, github):
            checks.append(repo_create)
        if workflows := _github_workflows(c, github):
            checks.append(workflows)
        if release_cut := _github_release_cut(c, github):
            checks.append(release_cut)
        if merge_ok := _github_merge_ok(c, github):
            checks.append(merge_ok)
        if edits := _github_workflow_edits(c, github):
            checks.append(edits)
        slack = _slack(c)
        if slack:
            checks.append(slack)
        if sealed := _sealed_secrets(c, settings, github):
            checks.append(sealed)
        checks.append(
            _check("backups", "Backups", "ok", "Demo data: there is nothing to back up.")
            if settings.demo
            else _check("backups", "Backups", "ok", "Rehearsal: backups are off on purpose.")
            if settings.rehearsal
            else _backups(config, settings)
        )
        checks.append(_signin(settings))
        checks.extend(_server_settings(c, settings))
    if full:
        from . import invariants

        checks.extend(invariants.health_checks(c))
    from . import run_diagnostics

    stalled_runs = run_diagnostics.stalled_runs(c, who, auth)
    checks.append(
        _check(
            "stalled_runs",
            "Stalled runs",
            "warn" if stalled_runs else "ok",
            f"{len(stalled_runs)} runs have no heartbeat or event for {run_diagnostics.STALL_MINUTES} minutes."
            if stalled_runs
            else "No stalled runs.",
            [_fix("Open Runs", "#/settings", "runs")] if stalled_runs else [],
        )
    )
    checks.append(
        _check(
            "failed",
            "Failed runs",
            "warn" if failed else "ok",
            f"{_plural(failed, 'run')} failed in the last day." if failed else "No failed runs in the last day.",
            # Runs showing only these failures (backend/views.py runs), so none is lost among the day's other runs.
            [_fix("Open failed runs", FAILED_RUNS)] if failed and full else [],
        )
    )
    return {
        **({"storage": storage_view(c, settings)} if kind == "owner" else {}),
        "audience": kind,
        "checks": checks,
        "attention": sum(1 for x in checks if x["status"] in ("warn", "bad")),
        "computers": computers if full else _bot_computers(computers) if kind == "bot" else [],
        "waiting": waiting if full else [],
        "slow": slow if full else [],
        "stuck": blocked,
        "on_fallback": on_fallback,
        "limited": limited,
        # Why each one failed is for administrators, who may read every bot's runs; a bot hears which and when.
        "failures": failures
        if full
        else [{k: v for k, v in f.items() if k != "why"} for f in failures]
        if kind == "bot"
        else [],
        "stalled_runs": stalled_runs,
        # To the minute, so an unchanged answer keeps its ETag (backend/json_response.py) between polls.
        "checked": H.now()[:17] + "00.000000Z",
        # The sidebar's notice reads the same fresh answer, so the two never disagree.
        "update": config.get("update") or {},
    }


def note_github_token(store, error=None, action=""):
    """Called around minting a GitHub token so the page can say the last attempt failed. Only a failure of the
    App itself belongs here (its key, installation or permissions), with what to do about it; a bot whose
    repository is not on GitHub yet is that bot's readiness problem."""
    now = H.now()
    detail = json.dumps({"message": error, "action": action, "failed_at": now}) if error else None
    with store.read() as c:
        current = c.execute("SELECT last_error,detail FROM service_health WHERE service=?", (GITHUB_HEALTH,)).fetchone()
        if error and current and current["last_error"] == error:
            try:
                seen = json.loads(current["detail"] or "{}")
            except ValueError:
                seen = {}
            # The same failure is rewritten at most hourly, so one that keeps happening stays recent.
            if seen.get("action") == action and str(seen.get("failed_at") or "") > H.shift(now, hours=-1):
                return
        elif not error and (not current or not current["last_error"] and (current["detail"] or "{}") in ("{}", "")):
            return
    with store.transaction() as c:
        if error:
            c.execute(
                "INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,NULL,?,?) "
                "ON CONFLICT(service) DO UPDATE SET last_error=excluded.last_error,detail=excluded.detail",
                (GITHUB_HEALTH, error, detail),
            )
        else:  # only a recovery is written, so a healthy turn costs no extra write
            # The failure's message goes too, so nothing reads an old failure after a recovery.
            c.execute(
                "UPDATE service_health SET last_succeeded_at=?,last_error=NULL,detail='{}' WHERE service=? "
                "AND (last_error IS NOT NULL OR detail<>'{}')",
                (now, GITHUB_HEALTH),
            )


def install(app, store, auth, settings):
    from fastapi import Request

    from . import models as M
    from . import replication

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
        with store.read_transaction() as c:
            # Health reads only these two parts of the config; building all of it cost a changelog parse per poll.
            return view(
                c,
                who,
                settings,
                auth,
                github,
                {"update": hosted_updates.notice(c, settings), "backup": replication.status()},
            )
