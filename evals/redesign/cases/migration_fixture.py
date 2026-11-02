"""The migration fixture (PLAN §7, scenarios 39-44): an Acme install as the release before this one left it, with one or
more of every object PLAN §8 maps. What this release still has routes for (the incoming trigger, the task rule, the
system jobs' controls, the inboxes, listening destinations, the goal) goes in through them; what only the old system
wrote (routines, wake rules, hourly limits, turn budgets below the guard, the CEO Bot's name) goes in by SQL, as those
tables held it. Last, the install's cutover record is cleared, so the next `upgrade` is this release's first start on
that data (backend/redesign_migration.py `cutover`).

Each call is one step under the key "fx:<name>". A call refused keeps an Unmet naming the call and its answer, so a bad
payload fails every check that needs that object instead of leaving a silent gap. install_fixture() returns the ids it
made (None for a call that failed) and keeps them in facts["fixture"].

What it makes:
- routines: two cron (Europe/London daily, UTC hourly at :30), one on an event, one on an incoming webhook, and the
  Librarian's market briefing (cron, America/Los_Angeles, weekdays); each with the next_due the old scheduler set
- the incoming trigger the webhook routine listens for; its secret is kept to post to the old address after the cutover
- custom wake rules: two on Builder (Claude) (wake, note), one on Builder (OpenAI) (batch)
- each bot's profile, as the release before's start set it (the Librarian's Custom rows join the wake rules)
- a task rule; the system jobs' controls (Stale ticket review off and hourly; Still wanted? daily at 10:00)
- two inboxes, Dana's and Priya's, each read by its own Inbox Manager bot (the roster's inbox_bot and mailbox)
- listening destinations: market (the Librarian) and leads (the Engineering Lead)
- hourly spend limits 3.00, 5.00 and 0.25; team turn budgets below the guard (120 an hour, 1,000 a day) and one bot
  above it (600 an hour)
- a goal; and the CEO Bot (named so), the Health Monitor and the Librarian as org bots (FIXTURE_BOTS)"""
import json

from ..harness import CLAUDE_ID, DANA_ID, LEAD_ID, OPENAI_ID, PRIYA_ID, RELEASE_ID, W5, Unmet

# The slugs the Strategy Bot and the system bots are built with (backend/strategy_bot.py SLUG, groups.SYSTEM_BOTS).
STRATEGY, LIBRARIAN, HEALTH = "goal-manager", "librarian", "health-monitor"
# The name an install before this release stored for the Strategy Bot; the migration renames it to "Strategy Bot".
OLD_NAME = "CEO Bot"
INBOX_DANA, INBOX_PRIYA = "inbox-manager-dana", "inbox-manager-priya"
FIXTURE_BOTS = {STRATEGY: ("strategy", "claude"), LIBRARIAN: ("librarian", "claude"), HEALTH: ("health", "claude"),
                INBOX_DANA: ("inbox", "claude"), INBOX_PRIYA: ("inbox", "claude")}

HOOK_ID = "deploy-failed"
# name -> (bot, the routine as the old `schedules` row held it: title, text, cron + timezone or an event)
ROUTINES = {
    "standup": (CLAUDE_ID, {"title": "Morning standup notes", "text": "Post yesterday's work and today's plan.",
                            "cron": "0 9 * * *", "timezone": "Europe/London"}),
    "pr_sweep": (OPENAI_ID, {"title": "Open PR sweep", "text": "Nudge every pull request that has waited a day.",
                             "cron": "30 * * * *", "timezone": "UTC"}),
    "release_notes": (RELEASE_ID, {"title": "Release notes", "text": "Write the notes for the new release.",
                                   "on": "release_published"}),
    "deploy_triage": (LEAD_ID, {"title": "Deploy failure triage", "text": "Find why the deploy failed and file a fix.",
                                "on": "webhook." + HOOK_ID}),
    "briefing": (LIBRARIAN, {"title": "Market briefing", "text": "Write today's market briefing from the graph.",
                             "cron": "0 7 * * 1-5", "timezone": "America/Los_Angeles"}),
}
CRON = ("standup", "pr_sweep", "briefing")
# name -> (bot, event, outcome): custom rows in the rules table, each a bot-scope wake rule (backend/rules.py)
WAKE_RULES = {
    "claude_comment": (CLAUDE_ID, "comment", "wake"),
    "claude_step": (CLAUDE_ID, "step_entered", "note"),
    "openai_comment": (OPENAI_ID, "comment", "batch"),
}
TASK_RULE = {"name": "Close 48h after done", "department": "engineering",
             "triggers": [{"key": "done_untouched", "hours": 48}], "actions": ["close"], "preset": "hourly"}
SYSTEM_JOBS = {"stale_review": {"on": False, "preset": "hourly"},           # PLAN §8: keeps Off + hourly
               "still_wanted": {"on": True, "preset": "daily", "migrated_at": "10:00"}}   # a control a built-in keeps
BUILTIN_JOBS = ("assigned_tasks", "assignment_recovery")   # "Start assigned tasks", "Recover unstarted assignments"
# person -> (their Inbox Manager bot, the mailbox it reads)
INBOXES = {DANA_ID: (INBOX_DANA, "dana@acme.example"), PRIYA_ID: (INBOX_PRIYA, "priya@acme.example")}
DESTINATIONS = {"market": {"category": "market", "threshold": 0.6, "receiver": LIBRARIAN},
                "leads": {"category": "lead", "threshold": 0.7, "receiver": LEAD_ID}}
HOURLY = {OPENAI_ID: 3.00, CLAUDE_ID: 5.00, RELEASE_ID: 0.25}
TEAM_BUDGET = {"turns_per_hour": 120, "turns_per_day": 1000}
GUARD = {"turns_per_hour": 300, "turns_per_day": 2500}   # CONTRACT §1 run guard defaults
BOT_BUDGET = (OPENAI_ID, {"turns_per_hour": 600})         # set above the guard: kept
GOAL = {"title": "Ship the v2 billing page", "owner": LEAD_ID}


def _call(org, name, method, path, payload=None):
    """One fixture write through a route this release has. Anything but a 2xx keeps an Unmet with the call and its
    answer."""
    reply = org.api(method, path, payload, wave=W5)
    if not 200 <= reply.status < 300:
        raise Unmet(W5, f"fixture {name} did not install on {method.upper()} {path}: {reply.status} {reply.excerpt()}")
    return reply


def _id(facts, key, *path):
    """A value from a fixture step's answer, or None when the step did not install."""
    value = facts.get(key)
    if value is None or isinstance(value, Unmet):
        return None
    value = value.body
    for part in path:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def routine_id(name):
    return "sch_" + name


def rule_id(name):
    return "rule_" + name


def old_rows(org):
    """What only the old system wrote, as its tables held it: no cutover record, routines (with the next_due the old
    scheduler set), wake rules, hourly limits, team and bot turn budgets, the CEO Bot's name."""
    from backend import profiles, redesign_migration as M, turn_budgets as TB
    from backend.scheduler import next_due, stamp
    from backend.store import H
    now, made = org.now(), {}
    with org.store.transaction() as c:
        # The release before never ran the cutover: no record, nothing marked.
        c.execute("DELETE FROM registry_metadata WHERE key=?", (M.CUTOVER,))
        c.execute("DELETE FROM redesign_migrated")
        # The org's bots joined after this server started; the release before's start gave each its profile, and a
        # Custom one its profile's rows in the rules table (the Librarian's), as they sit on an old install.
        profiles.migrate(c)
        for name, (bot, r) in ROUTINES.items():
            due = stamp(next_due(r["cron"], now, r["timezone"])) if "cron" in r else None
            c.execute("INSERT INTO schedules(id, bot, cron, event_name, title, playbook, next_due_at, updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (routine_id(name), bot, r.get("cron"), r.get("on"), r["title"],
                                                  r["text"], due, H.now()))
            c.execute("INSERT INTO schedule_config(schedule_id, timezone, enabled) VALUES(?,?,1)",
                      (routine_id(name), r.get("timezone", "UTC")))
            made[name] = due
        for n, (name, (bot, event, outcome)) in enumerate(WAKE_RULES.items()):
            c.execute("INSERT INTO rules(id, scope_kind, scope_id, event, then_outcome, position, created_by, created_at, "
                      "updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (rule_id(name), "bot", bot, event, outcome, n,
                                                             "human:" + DANA_ID, H.now(), H.now()))
        for bot, usd in HOURLY.items():
            c.execute("INSERT INTO usage_limits(bot, hourly_usd) VALUES(?,?) ON CONFLICT(bot) DO UPDATE SET "
                      "hourly_usd=excluded.hourly_usd", (bot, usd))
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (TB.TEAM_KEY, json.dumps(TEAM_BUDGET)))
        bot, own = BOT_BUDGET
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0] or "{}")
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (json.dumps({**config, **own}), bot))
        c.execute("UPDATE bots SET display_name=? WHERE slug=?", (OLD_NAME, STRATEGY))
        rule_bots = sorted({r[0] for r in c.execute("SELECT scope_id FROM rules WHERE scope_kind='bot'")})
    return {"due": made, "rule_bots": rule_bots}


def install_fixture(org, facts):
    """Install the fixture; returns {routines, hook, rules, task_rule, inboxes, destinations, goal, bots, due} ids."""
    step = lambda name, *args: org.step(facts, "fx:" + name, _call, org, name, *args)
    step("trigger", "post", "/api/v2/triggers", {"name": "Deploy failed", "id": HOOK_ID})
    step("task_rule", "post", "/api/v2/task-rules", TASK_RULE)
    for job, control in SYSTEM_JOBS.items():
        step("job:" + job, "put", f"/api/v2/system-routines/{job}", control)
    for person, (bot, mailbox) in INBOXES.items():
        step("inbox:" + person, "post", f"/api/v2/access/people/{person}", {"inbox_bot": bot, "mailbox": mailbox})
    for key, destination in DESTINATIONS.items():
        step("destination:" + key, "put", f"/api/v2/listening/destinations/{key}", destination)
    step("goal", "post", "/api/v2/goals", GOAL)
    org.step(facts, "fx:old", old_rows, org)
    secret = None
    url = _id(facts, "fx:trigger", "trigger", "url")
    if url:
        secret = url.rstrip("/").rsplit("/", 1)[-1]
    old = not isinstance(facts.get("fx:old"), Unmet)
    ids = {"routines": {name: routine_id(name) if old else None for name in ROUTINES},
           "due": facts["fx:old"]["due_at"] if old else {},
           "rule_bots": facts["fx:old"]["rule_bots"] if old else [],   # every bot with wake rules of its own
           "hook": {"id": _id(facts, "fx:trigger", "trigger", "id"), "secret": secret},
           "rules": {name: rule_id(name) if old else None for name in WAKE_RULES},
           "task_rule": _id(facts, "fx:task_rule", "job", "key"),
           "jobs": [job for job in SYSTEM_JOBS if _id(facts, "fx:job:" + job, "job", "key")],
           "inboxes": {person: {"bot": bot, "mailbox": mailbox} for person, (bot, mailbox) in INBOXES.items()
                       if _id(facts, "fx:inbox:" + person, "person")},
           "destinations": [key for key in DESTINATIONS if _id(facts, "fx:destination:" + key, "destination", "key")],
           "limits": dict(HOURLY) if old else {},
           "goal": _id(facts, "fx:goal", "goal", "id"),
           "bots": {"strategy": STRATEGY, "librarian": LIBRARIAN, "health": HEALTH}}
    facts["fixture"] = ids
    return ids


def upgrade(org):
    """This release's first start on the install, as Store.initialize runs it: the cutover (with no record yet)."""
    from backend import redesign_migration as M
    with org.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (M.CUTOVER,))
    return M.cutover(org.store)


def report(org):
    """backend/redesign_migration.py `report`: the cutover's record, counts and inventory, as the CLI prints it."""
    from backend import redesign_migration as M
    with org.store.read() as c:
        return M.report(c)


OLD_ROWS = ("routine", "rule", "limit", "budget")   # written by one SQL step, fx:old


def installed(facts, *names):
    """Raise the fixture step's Unmet when any named fixture object did not install ("trigger", "inbox:dana",
    "routine:standup", "limit:builder-openai")."""
    for name in names:
        key = "fx:old" if name.split(":", 1)[0] in OLD_ROWS else "fx:" + name
        value = facts.get(key)
        if value is None:
            raise Unmet(W5, f"fixture {name} was never installed")
        if isinstance(value, Unmet):
            raise value
