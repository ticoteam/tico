"""`hub health check`: what is wrong with the bots this person may see, most urgent first (docs/permissions.md scopes it).

Read from the records the server already holds, per bot: not on a computer, its computer offline, failing runs, a
credential it declares but does not have, setup that never finished, paused (and paused over a limit), quarantined.
Each issue says in a plain sentence what is wrong and the one command that fixes it, so BotOps can fix what it may and
report the rest.
"""
from . import bot_tools, usage_limits
from .getting_started import _online_runners
from .health import missing_repositories, repository_fix
from .statuses import is_parked
from .store import H

HIGH, MEDIUM, LOW = "high", "medium", "low"
STUCK_SETUP_HOURS = 1
FAILING_HOURS = 24
ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}


PAUSE_REASON_SQL = "SELECT reason FROM bot_status_history WHERE bot=? AND state='paused' ORDER BY since DESC, rowid DESC LIMIT 1"


def prime_pause_reasons(c, bots):
    """Each bot's latest pause reason, in one read for every bot (per 500). Outside a snapshot nothing is kept."""
    from .read_cache import cache, prime
    if cache(c) is None:
        return
    for part in H.chunks(sorted(set(bots))):
        marks = ",".join("?" * len(part))
        latest = {r["bot"]: r for r in c.execute(
            "SELECT bot,reason FROM (SELECT bot,reason,row_number() OVER (PARTITION BY bot ORDER BY since DESC, rowid DESC) AS n "
            f"FROM bot_status_history WHERE state='paused' AND bot IN ({marks})) WHERE n=1", part)}
        prime(c, PAUSE_REASON_SQL, {(s,): latest.get(s) for s in part})


def check(c, who, auth, settings):
    online = {r["id"] for r in _online_runners(c)}
    seen = auth.bot_accesses(c, who)
    since = H.shift(H.now(), hours=-FAILING_HOURS)
    stuck_before = H.shift(H.now(), hours=-STUCK_SETUP_HOURS)
    failed = {r["bot"]: r["n"] for r in c.execute(
        "SELECT bot, count(*) n FROM attempts WHERE state IN ('failed','expired') AND finished_at>? GROUP BY bot", (since,))}
    assigned = {r["bot"]: (r["runner_id"], r["label"]) for r in c.execute(
        "SELECT a.bot,a.runner_id,r.label FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE r.revoked_at IS NULL")}
    lacking = {bot: (label, why) for bot, label, why in missing_repositories(c, online)}
    issues = []
    # One company Default for every bot's spend check: without it each `over` rebuilds pools/groups.
    limits = usage_limits.company(c)

    def add(bot, kind, severity, text, fix):
        issues.append({"bot": bot["id"], "name": bot["display_name"] or bot["slug"], "kind": kind, "severity": severity,
                       "text": text, "fix": fix})

    from .agents import external_harness
    from .read_cache import one
    from . import events, views
    bots = c.execute("SELECT b.id,b.slug,b.display_name,b.state,b.created_at,bc.onboarding_state FROM bots b "
                     "JOIN bot_config bc ON bc.bot=b.id WHERE b.state<>'archived' ORDER BY b.slug").fetchall()
    # The rows each bot's check reads, read once for every bot (a snapshot keeps them; the loop finds them there).
    ids = [bot["id"] for bot in bots]
    views.prime_machines(c, ids)
    events.prime_bot_rows(c, ids)
    usage_limits.prime_own(c, ids)
    # The tool listing of every active or paused bot (bot_tools.listing) and each paused bot's last pause reason,
    # read together rather than once per bot.
    from .repositories import Reads
    listed = [bot["id"] for bot in bots if bot["state"] in ("active", "paused")]
    bot_tools.prime_state(c, listed)
    reads = Reads(c, listed)
    prime_pause_reasons(c, [bot["id"] for bot in bots if bot["state"] == "paused"])
    for bot in bots:
        slug, bid = bot["slug"], bot["id"]
        if not (seen.get(bid) or {}).get("see"):
            continue
        state, where = bot["state"], assigned.get(bid)
        name = bot["display_name"] or slug
        external = external_harness(c, bid)
        if state == "quarantined":
            add(bot, "quarantined", HIGH, f"{name} is stopped: it did something it may not.", f"hub api POST /api/v2/bots/{slug}/quarantine/clear")
        elif state == "planned":
            if (bot["created_at"] or "") < stuck_before:
                add(bot, "stuck_setup", MEDIUM, f"{name} is still being set up.", f"hub bot go-live {slug}")
        elif state == "paused":
            reason = (one(c, PAUSE_REASON_SQL, (bid,)) or {"reason": ""})["reason"] or ""
            if "limit" in reason.lower():
                add(bot, "paused_over_limit", HIGH, f"{name} is paused: it went over a limit.", f"hub bot resume {slug}")
            else:
                add(bot, "paused", LOW, f"{name} is paused.", f"hub bot resume {slug}")
        elif state == "active":
            if not where and not external:
                add(bot, "not_placed", HIGH, f"{name} is on, but no computer runs it.", f"hub bot place {slug}")
            elif where and where[0] not in online:
                add(bot, "computer_offline", HIGH, f"{name} cannot run: its computer, {where[1]}, is offline.", "hub computer list")
            if bid in lacking:
                label, why = lacking[bid]
                unpublished = "not on GitHub" in why or "does not exist" in why
                from .shared_bots import declared, source_of
                branch = bool(source_of(declared(c, bid)))
                _, clone = repository_fix(c, bid, label, settings.github_owner)
                add(bot, "repository_missing", HIGH, f"{name} cannot run on {label}: {why}",
                    f"hub bot repo-create {slug} --empty (or `hub bot place {slug}` back on the computer that held its repository)"
                    if unpublished and not branch else clone)
            if is_parked(bot["onboarding_state"]) and (bot["created_at"] or "") < H.shift(H.now(), hours=-24):
                add(bot, "needs_setup", MEDIUM, f"{name} is waiting for its first setup with its owner.", f"hub bot go-live {slug}")
        if state == "active":
            try:
                met = usage_limits.over(c, bid, default=limits)
            except Exception:
                met = None
            if met and met["scope"] == "bot":
                add(bot, "paused_over_limit", HIGH, f"{name} is paused: it reached its spending limit.",
                    f"hub api PUT usage/limits/{slug} (a card: raising a limit is their click)")
            elif met and met["scope"] == "budget":
                add(bot, "paused_over_limit", HIGH, f"{name} is paused: the {met['name']} budget is used up.",
                    "a person raises the budget in Finance (a card: raising a budget is their click)")
            elif met:
                where = "company" if met["scope"] == usage_limits.COMPANY else "groups/" + met["scope"].split(":", 1)[1]
                add(bot, "paused_over_limit", HIGH, f"{name} is paused: {met['name']} reached its {met['period']} spending limit.",
                    f"hub api PUT usage/pools/{where} (a card: raising a limit is their click)")
        if failed.get(bid):
            n = failed[bid]
            add(bot, "failing_runs", HIGH if n >= 3 else MEDIUM, f"{name} had {n} failed or expired attempt{'s' if n != 1 else ''} in the last 24 hours.",
                f"hub run list {slug} --since 24h")
        if state in ("active", "paused"):
            try:
                tools = bot_tools.listing(c, settings, bid, reads=reads)["tools"]
            except Exception:
                tools = []
            for tool in tools:
                if tool.get("status") == "problem" and tool.get("env"):
                    what = tool.get("name") or tool.get("service")
                    if tool.get("held"):                     # the computer has the key; the server has not named this bot
                        add(bot, "missing_credential", HIGH, f"{name} needs {what}: {tool['problem']}",
                            "docs/mail.md, 'Who can read the key'")
                        continue
                    if tool["env"] == "GOOGLE_SA_KEY":      # the key lives on the computer, not in a bot's environment
                        add(bot, "missing_credential", HIGH, f"{name} needs {what}: its computer has no Google service-account key and no connected Google account.",
                            "put the key in the computer's state directory (docs/mail.md, 'Who can read the key')")
                        continue
                    add(bot, "missing_credential", HIGH, f"{name} needs {what}: {tool['env']} is not set.",
                        f"hub credential request {tool['env']} --for-bot {slug}")
    issues.sort(key=lambda i: (ORDER[i["severity"]], i["name"].lower(), i["kind"]))
    counts = {level: sum(1 for i in issues if i["severity"] == level) for level in (HIGH, MEDIUM, LOW)}
    return {"issues": issues, "counts": counts, "checked": H.now(),
            "bots": sum(1 for s in seen.values() if s.get("see"))}
