"""System routines: the jobs Tico runs on its own timers (backend/scheduler.py), one registry and one run record per run,
for the one flat, searchable list in Settings > Routines (`GET /api/v2/system-routines`).

`JOBS` is every built-in job: its plain name, one line on what it does, its group (now a word search finds, not a
heading), its schedule in words and in seconds, whether it asks Tico Decide, what it does as catalog actions (`then`,
read as "WHEN <trigger> THEN <action>"), its one department tag, and whether a person may switch and
reschedule it (`control`). The trigger/action catalog (TRIGGERS, ACTIONS) is shared with task rules, so they read and are
searched the same way.

`CONTROLS` holds what a person may change: on/off and a schedule preset for the jobs that use AI or reach people.
Stale ticket review, Topic changed and Stalled task check keep their on/off in the team rules
(backend/team_rules.py), so the value they had in Settings > General carries over; the rest live here in
`registry_metadata` CONFIG_KEY. An owner or admin changes them; Health scan only the owner. `due` is when a job's
preset says it runs again, read by the scheduler and the job's next run alike. Run now runs a job once, through the same
code and caps as its scheduled run. A job's run is recorded by `recording` around its block in the scheduler: start,
finish, the result in words, or "Failed: <plain reason>", never an exception's text. A Decide job adds which route
answered (the Gateway, the company's own key, or the company's AI provider), the first failure when the Gateway did not
answer, and the run's cost. The Decide fallback itself is judge.py's; nothing here changes which route answers.

A job that runs every few seconds and finds nothing to do does not write a row each time: a quiet run with the same
result as the job's last row moves that row's times forward, at most once a minute. Each job keeps its last KEEP rows.
A month's spend is read from the `judge.spend` audit by question set (`label`), which the monthly cap already keeps, so
pruning runs never loses it."""

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time as clock, timedelta, timezone

from .store import H, Problem

LOG = logging.getLogger("tico.system_routines")

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_runs(
 id TEXT PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER NOT NULL DEFAULT 1,
 result TEXT NOT NULL DEFAULT '', route TEXT, fallback TEXT, cost_usd REAL, runs INTEGER NOT NULL DEFAULT 1,
 first_started_at TEXT);
CREATE INDEX IF NOT EXISTS system_runs_job ON system_runs(job, started_at);
"""


def ensure(c):
    """Add what SCHEMA's table gained since a database was made: `first_started_at`, the first of a folded row's quiet
    repeats (its `started_at` moves to the latest), so counts can spread `runs` over the time they happened."""
    if "first_started_at" not in {row[1] for row in c.execute("PRAGMA table_info(system_runs)")}:
        c.execute("ALTER TABLE system_runs ADD COLUMN first_started_at TEXT")

KEEP = 50                   # rows per job
# A repeat (a quiet run with the same words, or a failure with the same reason) moves the job's last row at most this
# often. Nothing times a job by a quiet repeat's start: a controlled job's shortest preset is 15 minutes.
QUIET_WRITE_SECONDS = 300

CHECKS, LEARNING, REMINDERS, HOUSEKEEPING, CHAT = "checks", "learning", "reminders", "housekeeping", "chat"
GROUPS = [(CHECKS, "Checks that suggest"), (LEARNING, "Learning"), (REMINDERS, "Reminders and deadlines"),
          (HOUSEKEEPING, "Housekeeping"), (CHAT, "Chat")]
GROUP_WORDS = dict(GROUPS)
DEPARTMENT = "Engineering"  # a built-in's tag until departments are edited (a label only: it changes no permission)
FINANCE = "Finance"         # the Bookkeeper's checks

# Which route answered a Decide call, from the answering provider (judge.py's order: Gateway, own key, company AI).
ROUTE_WORDS = {"gateway": "Gateway", "own_key": "Company key", "team_llm": "Company AI"}
FELL_BACK_TO = {"own_key": "Ran on your own key", "team_llm": "Ran on company AI"}
# Why the first route did not answer, from clients/judge.py's error codes.
FALLBACK_WORDS = {"unavailable": "unavailable", "auth": "refused the key", "unconfigured": "has no key",
                  "http": "answered with an error", "shape": "answer could not be read", "not_found": "not found",
                  "spend_cap": "at its spend cap", "invalid": "refused the request"}
PROVIDER_WORDS = {"gateway": "Gateway", "own_key": "Your own key", "team_llm": "Company AI"}


@dataclass(frozen=True)
class Job:
    key: str
    name: str
    what: str
    group: str
    schedule: str               # in words
    every: int | None = None    # seconds between runs, for the next run; None: `next_at` or unknown
    ai: bool = False            # asks Tico Decide
    control: bool = False       # a job a person will switch and reschedule
    labels: tuple = ()          # Decide question sets whose spend is this job's
    records: bool = True        # False: it writes no run, so its next run cannot be told
    then: tuple = ()            # ACTIONS keys: what it does
    when: str = "schedule"      # a TRIGGERS key; "schedule" reads the job's schedule
    department: str = DEPARTMENT
    # (c, settings) -> whether the job can do anything on this install (a feature it needs is set up). Cheap and
    # read-only: a setting or a one-row check. None: it always applies. A job that does not apply is left out of the
    # lists (`applies`), never deleted, so it shows again by itself once the feature is set up.
    applies: object = None


# ----------------------------------------------------------------------------- the trigger/action catalog
# A row reads "WHEN <trigger>[ AND ...] THEN <action>[ AND ...]". A trigger or an action is a key here with its words and
# the extra words search finds it by (what it touches: "done" finds what acts on done tasks).
TRIGGERS = {
    "schedule": ("", ()),                    # the job's schedule in words: "every hour", "every day at 09:00"
    "topic_changed": ("a message in a direct chat changes the topic", ("chat", "topic", "message")),
    "quiet_task": ("a bot's task is quiet, waits on nothing recorded and stands as at the bot's last checkpoint",
                   ("stalled", "tasks", "quiet")),
    "free_turn": ("a bot has a free turn and assigned work that is not snoozed or blocked", ("tasks", "assigned", "turn", "prerequisite")),
    "directory": ("a company directory is connected", ("directory",)),
}
ACTIONS = {
    "check_stale": ("check stale tickets", ("stale", "tasks", "finished", "forgotten", "done")),
    "suggest_owner": ("suggest to each owner: mark done, close or check it",
                      ("suggest", "done", "close", "owner", "decide")),
    "ask_kpi_keeper": ("ask a KPI's keeper for a reading when it goes out of date", ("kpi", "ask", "remind", "goal")),
    "ask_goal_owner": ("ask a goal's owner when a KPI goes off track", ("kpi", "goal", "ask", "remind")),
    "check_health": ("check Tico's health", ("health", "check")),
    "check_invariants": ("check production invariants (stuck runs and tasks, unreachable owners, refusal loops, "
                         "provider sign-in, webhooks, automations, relays) and raise one task per failing check",
                         ("health", "invariants", "production", "alert", "stuck")),
    "report_tasks": ("list each department's stalled, overdue and out-of-step tasks for the Task Manager",
                     ("tasks", "stalled", "overdue", "department", "report")),
    "wake_task_manager": ("open a Task check task for each department that has something to fix",
                          ("tasks", "wake", "task manager")),
    "check_books": ("read the Bookkeeper's Finance work queue: rows to categorize, bills, payments due, overdue "
                    "invoices, receipts, failed syncs, months ready to close", ("finance", "books", "bills", "inbox")),
    "wake_bookkeeper": ("open or update one Books check task for the Bookkeeper only when the queue has something new",
                        ("finance", "bookkeeper", "wake")),
    "check_watch": ("read the Bookkeeper's Watch checks: runway, unusual spend, duplicate charges, price increases",
                    ("finance", "watch", "runway", "spend", "duplicate", "price")),
    "wake_watch": ("open or update one Watch task for the Bookkeeper only when a check finds something new",
                   ("finance", "bookkeeper", "wake", "watch")),
    "check_rules_digest": ("list the posting rules made this week; none is a quiet run",
                           ("finance", "rules", "digest")),
    "wake_rules_digest": ("open the week's rules digest task for the Bookkeeper when there are new rules",
                          ("finance", "rules", "digest", "wake")),
    "propose_memory_tidy": ("propose memory and knowledge edits for the pilot bots", ("memory", "tidy", "pilot")),
    "remind_due": ("tell a bot when one of its tasks reaches its due time", ("due", "remind", "tasks")),
    "go_ahead": ("go ahead on a past-deadline question's default answer, or expire it",
                 ("ask", "question", "deadline", "expire")),
    "expire_approvals": ("take approvals and asks nobody will act on out of Needs you",
                         ("approval", "expire", "close", "done", "needs you")),
    "resend_questions": ("record questions unanswered for two business days as lines on their bots' task roundup",
                         ("question", "remind", "unanswered", "roundup")),
    "start_task": ("start the highest-ranked eligible assigned task in its own session", ("task", "start", "assigned", "On Deck", "prerequisite")),
    "recover_assignment": ("list an unattempted assignment once on its owner's task roundup after one hour; show it in Health and its manager's Needs you two hours later",
                           ("task", "assigned", "not started", "roundup", "health", "manager")),
    "free_dead_starts": ("free turns proven dead: process gone and lease lost, or stopped before they started",
                         ("turn", "start", "dead", "stuck", "free", "busy")),
    "turns_finding": ("keep one Health finding per busy-turns incident and hand what it cannot fix to BotOps",
                      ("health", "turns", "incident", "botops", "busy")),
    "send_task_roundup": ("send each bot one list of its tasks: a follow-up that came due, an unanswered question, an assignment "
                          "not started, a restate, a late ticket, an exception, a backlog, a missed update or a quiet task",
                         ("tasks", "roundup", "wake", "missed", "quiet", "update", "late", "backlog", "exceptions", "assigned")),
    "tell_earlier_messages": ("tell each manager of a bot once when its Home still holds closed history",
                              ("thread", "earlier", "closed", "history", "managers", "notice")),
    "wake_digest_bots": ("start one run for each bot with new messages in the threads it follows on digest",
                         ("bot", "digest", "thread", "wake", "batch")),
    "resume_capped_wakes": ("let bot wakes the loop guard paused go when a thread's hour has room",
                            ("loop", "guard", "capped", "paused", "resume", "wake", "thread", "bots")),
    "purge_thread_messages": ("delete messages past the time their thread keeps them, with what was copied from them",
                              ("retention", "purge", "delete", "thread", "messages", "keep")),
    "email_digest": ("email a person once an hour the threads they follow on digest that have new messages: titles, "
                     "unread counts and links, never the messages", ("email", "digest", "unread", "thread", "notify")),
    "drop_unreadable": ("drop queued work its bot can no longer read", ("queue", "drop")),
    "ask_to_task": ("turn a bot's plain ask to a person into a small task", ("ask", "tasks")),
    "end_snooze": ("bring snoozed tasks back when their date comes", ("snooze", "tasks")),
    "start_deferred": ("start deferred runs that are due", ("deferred", "runs")),
    "end_go_live": ("end a go-live waiting for a computer that never reports", ("go-live", "computer", "expire")),
    "goal_colours": ("work out every goal's colour again", ("goal", "colour", "color")),
    "check_gateway": ("check this server's Gateway key and the month's cap", ("gateway", "key", "cap")),
    "check_tools": ("call each proxied API tool's check with its keys", ("tools", "keys", "check")),
    "read_billing": ("read HQ's nightly billing check", ("billing",)),
    "check_pages": ("read the watched competitor pages and file an insight for each that changed", ("market", "pages", "competitors", "librarian")),
    "send_heartbeat": ("tell app.tico.dev this server's version", ("company", "version", "portal")),
    "check_update": ("update a hosted server to the release Tico support scheduled, once no bot turn runs",
                     ("update", "release", "hosting", "support")),
    "ask_updates": ("ask people and bots for today's updates", ("updates", "ask")),
    "sync_github": ("catch up on pull requests, merges and releases", ("github", "pr", "merge", "release")),
    "finish_repo_waits": ("finish a bot waiting for its repository or a worktree", ("repository", "worktree")),
    "tell_setup_stuck": ("tell a person about a bot stuck in setup", ("setup", "remind")),
    "check_release": ("check for a new Tico release", ("release", "update")),
    "fetch_feeds": ("fetch subscribed updates", ("feeds", "updates")),
    "sync_directory": ("bring people in from the company directory", ("directory", "people")),
    "sync_meetings": ("bring in meeting notes from each person's Granola", ("meetings", "granola", "notes")),
    "sync_docs": ("refresh linked documents from their sources", ("documents", "docs")),
    "start_fresh": ("start a fresh session, like Clear", ("chat", "fresh", "clear", "session")),
    "rank_threads": ("rank each thread with new messages from 1 to 10", ("threads", "rank", "important", "decide")),
    "mark_silent_runs": ("mark a silent run and end a run past this server's time limit", ("stall", "silent", "runs", "limit")),
    "end_lapsed_runs": ("end runs whose lease ran out", ("lease", "expire", "runs")),
    "settle_runs": ("settle interrupted runs, lift usage limits and cooled holds, and give an active bot a computer",
                    ("runs", "interrupted", "limits", "quarantine", "placement", "computer", "notes")),
    "mark_shipped": ("mark a merged pull request shipped once the running release has it", ("github", "merge", "shipped", "deploy")),
    "tell_task_loop": ("tell a looping task's owner and requester once a day", ("tasks", "loop", "status", "notice")),
    "note_queue_refusals": ("note why a bot's queued work is not starting", ("queue", "refused", "bots", "health")),
    "wake_milestone_waits": ("wake a task waiting for a merge or release that already happened", ("merge", "release", "wait", "wake")),
    "send_release_task": ("send the Release Manager one task for what is at Pending Release", ("release", "manager", "pending")),
    "start_editions": ("start the Company page editions that came due and send waiting reader notices",
                       ("company", "page", "edition", "readers")),
    "screen_waiting": ("screen outside text a restart left waiting", ("screening", "outside", "text")),
    "finance_upkeep": ("post Finance's waiting events, usage and prepaids, end holds and finished approvals, open the next period",
                       ("finance", "events", "usage", "holds", "period", "approvals")),
    "deadline_sweeps": ("turn off failing webhooks, retire replaced computers, hand on support tickets and screened items",
                        ("webhooks", "computers", "support", "screening", "deadline")),
    "start_strategy_note": ("start or pause the Strategy Bot's Monday note to match its level", ("strategy", "note", "monday")),
    "read_cloud_access": ("read Tico Cloud access from HQ and renew file hosting keys", ("cloud", "access", "storage", "hq")),
    "send_alerts": ("send people's opt-in alerts through HQ paging", ("alerts", "paging", "sms")),
    "check_workspace_key": ("check a pending or failing Google Workspace key again", ("google", "workspace", "key")),
    "sweep_calls": ("route missed live-meeting windows and end call access that ended", ("calls", "meetings", "live")),
    "fetch_mail": ("bring in mail and bills from HQ", ("mail", "inbound", "bills")),
    "purge_mail": ("delete inbound mail older than 90 days", ("mail", "retention", "delete")),
    "pull_webhooks": ("bring in webhooks waiting at Tico Gateway", ("webhooks", "gateway", "relay", "notices")),
    "route_letters": ("read and route physical mail", ("mail", "letters", "physical")),
    "send_letters": ("send approved letters and follow them until delivered", ("letters", "mail", "send")),
    "ask_sleep": ("ask HQ to put this server to sleep while nobody uses it", ("sleep", "idle", "hosting")),
    "manage_machines": ("start bots' cloud machines for work and stop them when idle", ("cloud", "machines", "computers")),
    "triage_quiet": ("not run any more: Decide no longer reads a quiet task (it used to set the wait it names, close it if finished, or let one wake through)",
                     ("decide", "stalled", "tasks", "wait", "close", "wake")),
}


# ----------------------------------------------------------------------------- whether a job applies here
# Each is cheap and read-only, and says yes when it cannot tell: a job is hidden only when it surely has nothing to do.
def _row_exists(c, sql, args=()):
    return c.execute(sql, args).fetchone() is not None


def _directory_connected(c, settings):
    return directory_minutes(c) is not None


def _granola_connected(c, settings):
    return _row_exists(c, "SELECT 1 FROM granola_connections LIMIT 1")


def _documents_linked(c, settings):
    # The sources the document worker refreshes (backend/doc_sources.py `claim`).
    return _row_exists(c, "SELECT 1 FROM doc_sources s JOIN linked_docs l ON l.id=s.source_id "
                          "WHERE l.archived=0 AND s.repo!='' LIMIT 1")


def _hosted(c, settings):
    from . import hosted_updates
    return settings is None or hosted_updates.on(settings)


def _billing_key(c, settings):
    from . import billing_check
    return settings is None or bool(billing_check.env_key(settings))


def _gateway_key(c, settings):
    from . import gateway_connection
    return settings is None or gateway_connection.connected(c, settings) or gateway_connection.maintained(c, settings)


def _company_linked(c, settings):
    from . import company_server
    return settings is None or company_server.linked(c, settings)


def _gateway_connected(c, settings):
    from . import gateway_connection
    return settings is None or gateway_connection.connected(c, settings)


def _bookkeeper(c, settings):
    row = H.bot(c, "bookkeeper")
    return bool(row) and row["state"] != "archived"


def _tools_checked(c, settings):
    return _row_exists(c, "SELECT 1 FROM tools WHERE delivery='proxy' AND base_url<>'' AND check_path<>'' LIMIT 1")


def _pages_watched(c, settings):
    return _row_exists(c, "SELECT 1 FROM market_watches WHERE active=1 LIMIT 1")


def _strategy_bot(c, settings):
    from . import strategy_bot
    row = H.bot(c, strategy_bot.SLUG)
    return bool(row) and row["state"] != "archived"


def _release_hour_on(c, settings):
    from . import release_flow
    return not getattr(settings, "rehearsal", False) and release_flow.load(c)["on"]


def _company_page_open(c, settings):
    from .company_page import schedule
    return schedule.transparency(c) != "closed"


def _cloud_company(c, settings):
    from . import cloud_machines
    return cloud_machines.cloud_company(c, settings)


def _sleep_set(c, settings):
    # Sleep when idle only looks on a cell, or once the owner set its hours (backend/sleep_idle.py `effective`).
    from . import cloud_access, sleep_idle
    current = sleep_idle.state(c)
    return cloud_access.strict_cell() or bool(current["changed_at"] is not None and current["hours"])


def _alerts_on(c, settings):
    return _row_exists(c, "SELECT 1 FROM alert_preferences LIMIT 1")


def _workspace_key(c, settings):
    return _row_exists(c, "SELECT 1 FROM google_workspaces LIMIT 1")


def _physical_mail(c, settings):
    return _row_exists(c, "SELECT 1 FROM mail_items LIMIT 1")


def _letters(c, settings):
    return _row_exists(c, "SELECT 1 FROM letters LIMIT 1")


JOBS = [
    Job("assigned_tasks", "Start assigned tasks",
        "Starts eligible assigned work on a free computer turn; respects task caps, usage holds and computer capacity. "
        "Each start is one run.",
        HOUSEKEEPING, "When a turn is free", then=("start_task",), when="free_turn"),
    Job("stale_review", "Stale ticket review",
        "Finds open tasks that look finished or forgotten and suggests to each owner what to do.",
        CHECKS, "Hourly", 3600, ai=True, control=True, labels=("stale-review",), then=("check_stale", "suggest_owner")),
    Job("kpi_asks", "KPI stale asks",
        "Asks a KPI's keeper for a reading when it goes out of date, and a goal's owner when a KPI goes off track.",
        CHECKS, "Hourly", 3600, control=True, then=("ask_kpi_keeper", "ask_goal_owner")),
    Job("task_manager_report", "Task Manager report",
        "Lists each department's quiet tasks, overdue asks, review tickets without a pull request, merged work that did "
        "not move and held bots in plain code, and opens one task for the Task Manager per department that has something.",
        CHECKS, "Hourly on weekdays, 08:00 to 18:00", 3600, control=True, then=("report_tasks", "wake_task_manager")),
    Job("bookkeeper_check", "Books check",
        "Reads the Bookkeeper's Finance work queue in plain code and opens one task for the Bookkeeper only when it has "
        "something new. Nothing new is a quiet run: no task, no model call.",
        CHECKS, "Hourly", 3600, control=True, then=("check_books", "wake_bookkeeper"), department=FINANCE,
        applies=_bookkeeper),
    Job("bookkeeper_watch", "Books watch",
        "Reads runway, unusual spend, duplicate charges and price increases in plain code, and opens one Watch task for "
        "the Bookkeeper only when a check finds something new. Nothing new is a quiet run: no task, no model call.",
        CHECKS, "Daily", 86400, then=("check_watch", "wake_watch"), department=FINANCE, applies=_bookkeeper),
    Job("bookkeeper_rules_digest", "Rules digest",
        "Once a week, lists the posting rules made that week and opens the Bookkeeper's digest task for them. No new "
        "rules is a quiet run.",
        CHECKS, "Weekly", 7 * 86400, then=("check_rules_digest", "wake_rules_digest"), department=FINANCE,
        applies=_bookkeeper),
    Job("memory_tidy", "Nightly memory tidy",
        "Proposes memory and knowledge edits for up to three pilot bots on their named models. Pilot stop: $50.",
        LEARNING, "Nightly at 03:30", control=True, then=("propose_memory_tidy",)),
    Job("due_reminders", "Due reminders", "Tells a bot when one of its tasks reaches its due time.",
        REMINDERS, "Every 10 seconds", 10, then=("remind_due",)),
    Job("ask_deadlines", "Ask go-ahead",
        "A bot's question past its deadline goes ahead on its default answer, or expires.",
        REMINDERS, "Hourly", 3600, control=True, then=("go_ahead",)),
    Job("approval_expiry", "Approval expiry", "Takes approvals and asks nobody will act on out of Needs you.",
        REMINDERS, "Hourly", 3600, then=("expire_approvals",)),
    Job("thread_followups", "Earlier messages",
        "Tells each manager of a bot once, in a direct message, when the bot's Home still holds closed history. Plain code: "
        "no model is called, and nothing new is a quiet run. Asks nobody answered are on the bot's task roundup.",
        REMINDERS, "Every 15 minutes", 900, then=("tell_earlier_messages",)),
    Job("thread_digest", "Email digest",
        "Emails a person once an hour the threads they follow on digest that have new messages: each title, its unread "
        "count and a link, never the messages. Nothing unread is a quiet run.",
        REMINDERS, "Hourly", 3600, then=("email_digest",)),
    Job("thread_bot_digest", "Bot thread digest",
        "Starts one run an hour for each bot that follows threads on digest and has new messages in them. Plain code: "
        "no model is called until a batch has something in it, and nothing new is a quiet run.",
        REMINDERS, "Hourly", 3600, then=("wake_digest_bots",)),
    Job("still_needed", "Unanswered questions",
        "On weekday mornings, questions unanswered for two business days go back to their bots as lines on the bots' "
        "task roundup, never as a wake.",
        REMINDERS, "Hourly", 3600, then=("resend_questions",)),
    Job("stall_checks", "Stall checks",
        "Every minute: allocates waiting tickets, closes timed-out checks, drops queued work its bot can no longer read, and "
        "alerts once when an Engineering task has been blocked over four hours. It wakes no bot about a quiet task: "
        "the task roundup lists those.",
        HOUSEKEEPING, "Every minute", 60, then=("drop_unreadable",)),
    Job("turn_fixer", "Turn fixer",
        "Frees turns proven dead, keeps one Health finding per busy-turns incident and hands what it cannot fix to "
        "BotOps.", HOUSEKEEPING, "Every 10 seconds", 10, then=("free_dead_starts", "turns_finding")),
    Job("waiting_checks", "Waiting checks",
        "Turns a bot's plain ask to a person into a small task. It no longer sends a waiting task back to work; a "
        "waiting task that waits on nothing is listed on the task roundup after a day.",
        HOUSEKEEPING, "Hourly", 3600, then=("ask_to_task",)),
    Job("snooze_ends", "Snooze ends",
        "Brings snoozed tasks back when their date comes and starts deferred runs that are due.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("end_snooze", "start_deferred", "start_task")),
    Job("thread_loop_guard", "Paused bot replies",
        "Lets bot replies the loop guard paused in a thread go, as many as its hour has room for, with no one having to post.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("resume_capped_wakes",)),
    Job("thread_retention", "Thread retention",
        "Deletes messages past the time their thread keeps them, with what was copied from them, in short batches. Plain "
        "code: nothing past its time is a quiet run.",
        HOUSEKEEPING, "Every 15 minutes", 900, then=("purge_thread_messages",)),
    Job("invariant_checks", "Production checks",
        "Reads a few production invariants in plain code and shows each failing one on Health. Check health files each "
        "one as a BotOps task, closed when the check passes.",
        HOUSEKEEPING, "Every 15 minutes", 900, then=("check_invariants",)),
    Job("go_live_expiry", "Go-live expiry", "Ends a go-live waiting for a computer that never reports.",
        HOUSEKEEPING, "Every 10 minutes", 600, then=("end_go_live",)),
    Job("goal_colours", "Goal colours", "Works out every goal's colour again as its data ages.",
        HOUSEKEEPING, "Hourly", 3600, then=("goal_colours",)),
    Job("gateway_check", "Gateway key check",
        "Asks the Tico AI Gateway whether this server's key still works and how much of the month's cap is used.",
        HOUSEKEEPING, "Hourly", 3600, then=("check_gateway",), applies=_gateway_key),
    Job("tool_checks", "Tool checks",
        "Calls each API tool's check with its keys every 30 minutes, for the tools on the proxy, so Tools shows a "
        "rejected key before a bot meets it.",
        HOUSEKEEPING, "Every 5 minutes", 300, then=("check_tools",), applies=_tools_checked),
    Job("billing_check", "Billing check", "Reads HQ's nightly billing check for the owner's Health page.",
        HOUSEKEEPING, "Hourly", 3600, then=("read_billing",), applies=_billing_key),
    Job("company_heartbeat", "Tico company heartbeat",
        "Tells app.tico.dev this server's version once a day while it is linked to a Tico company.",
        HOUSEKEEPING, "Daily", 86400, then=("send_heartbeat",), applies=_company_linked),
    Job("hosted_update", "Scheduled update",
        "On a hosted server: updates to the release Tico support scheduled, once no bot turn runs.",
        HOUSEKEEPING, "Every minute", 60, then=("check_update",), applies=_hosted),
    Job("market_watch", "Page monitors",
        "Reads each watched competitor page once a day and files an insight for the Librarian when one changed.",
        CHECKS, "Daily", 86400, then=("check_pages",), applies=_pages_watched),
    Job("updates", "Updates", "Asks people and bots for today's updates, one at a time.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("ask_updates",)),
    Job("github_sync", "GitHub sync",
        "Catches up on pull requests, merges and releases whose notice from GitHub never arrived.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("sync_github",)),
    Job("repository_waits", "Repository and setup waits",
        "Finishes a bot waiting for its repository or a worktree, and tells a person about a bot stuck in setup.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("finish_repo_waits", "tell_setup_stuck")),
    Job("release_check", "Release and feed check", "Checks for a new Tico release and fetches subscribed updates.",
        HOUSEKEEPING, "Every 10 seconds; a new release is looked for every 6 hours", 10, then=("check_release", "fetch_feeds")),
    Job("directory_sync", "Directory sync", "Brings people in from the company directory.",
        HOUSEKEEPING, "Hourly", then=("sync_directory",), applies=_directory_connected),
    Job("meeting_sync", "Meeting notes sync", "Brings in meeting notes from each person's connected Granola.",
        HOUSEKEEPING, "Every 30 seconds", 30, then=("sync_meetings",), applies=_granola_connected),
    Job("doc_sync", "Document sync", "Refreshes linked documents from their sources.",
        HOUSEKEEPING, "Every 5 seconds", 5, then=("sync_docs",), applies=_documents_linked),
    Job("topic_fresh", "Start fresh when the topic changes",
        "In a direct chat, a message on a new topic starts a fresh session, like Clear.",
        CHAT, "When it happens", when="topic_changed", then=("start_fresh",)),
    Job("thread_rank", "Thread ranking",
        "Ranks threads with new messages from 1 to 10 for the Important view in Threads, each at most once an hour.",
        CHAT, "Every 5 minutes", 300, ai=True, labels=("thread-rank",), then=("rank_threads",)),
    # The scheduler's other timed work (backend/scheduler.py `tick`), each one row.
    Job("stall_marks", "Silent run check",
        "Marks a run that has gone silent and ends a run past this server's time limit, before plain expiry looks at it.",
        HOUSEKEEPING, "Every minute", 60, then=("mark_silent_runs",)),
    Job("run_housekeeping", "Run housekeeping",
        "Ends runs whose lease ran out, settles interrupted runs, lifts usage limits and cooled holds, gives an active "
        "bot with no computer one, and settles run notes a result missed.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("end_lapsed_runs", "settle_runs")),
    Job("ship_deployed", "Shipped merges",
        "Marks a merged pull request shipped once the running release has it, when its push record came after the deploy.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("mark_shipped",)),
    Job("task_loops", "Task loop alerts",
        "Tells a task's owner and requester once a day when the task's status keeps changing back and forth.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("tell_task_loop",)),
    Job("queue_check", "Queue check",
        "Notes why a bot's queued work is not starting, for the bot's row and Health.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("note_queue_refusals",)),
    Job("milestone_waits", "Merge and release waits",
        "Wakes a task waiting for a merge or a release that already happened, once.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("wake_milestone_waits",)),
    Job("release_hour", "Daily release",
        "At the team's release hour, sends the Release Manager one task for what is at Pending Release.",
        HOUSEKEEPING, "At the release hour", then=("send_release_task",), applies=_release_hour_on),
    Job("company_page", "Company page editions",
        "Starts the Company page editions that came due and sends reader notices left waiting.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("start_editions",), applies=_company_page_open),
    Job("screening", "Outside text check", "Screens outside text a restart left waiting.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("screen_waiting",)),
    Job("finance_upkeep", "Finance upkeep",
        "Posts Finance's waiting events, Gateway usage and prepaid costs, ends expired holds and finished approvals, "
        "watches documents, and opens next month's period.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("finance_upkeep",), department=FINANCE),
    Job("hourly_deadlines", "Hourly deadlines",
        "Turns off a webhook that failed for five days, retires replaced computers, and hands a person a support ticket "
        "or screened item that waited an hour.",
        HOUSEKEEPING, "Hourly", 3600, then=("deadline_sweeps",)),
    Job("strategy_note", "Strategy note check",
        "Starts the Strategy Bot's Monday note once its level is above Passive, and pauses it at Passive.",
        CHECKS, "Hourly", 3600, then=("start_strategy_note",), applies=_strategy_bot),
    Job("cloud_access", "Tico Cloud check",
        "Reads this company's Tico Cloud access from HQ and renews its file hosting keys.",
        HOUSEKEEPING, "Every minute on Tico Cloud, else hourly", then=("read_cloud_access",), applies=_gateway_connected),
    # The server's background loops (backend/app.py `lifespan`), each one row.
    Job("personal_alerts", "Personal alerts",
        "Sends people's opt-in alerts through HQ paging, within their quiet hours and the monthly caps.",
        REMINDERS, "Every 10 seconds", 10, then=("send_alerts",), applies=_alerts_on),
    Job("workspace_check", "Workspace key check",
        "Checks a pending or failing Google Workspace key again until it works or its hour is up.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("check_workspace_key",), applies=_workspace_key),
    Job("call_sweeps", "Calls and live meetings",
        "Routes live-meeting windows a restart left behind, and ends call access for people and bots who lost it.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("sweep_calls",)),
    Job("inbound_mail", "Inbound mail",
        "Brings in mail and bills from HQ, and deletes inbound mail older than 90 days.",
        HOUSEKEEPING, "Every 15 seconds", 15, then=("fetch_mail", "purge_mail")),
    Job("gateway_webhooks", "Gateway webhooks", "Brings in webhooks and Tico notices waiting at Tico Gateway.",
        HOUSEKEEPING, "Every 15 seconds with a relay, else every minute", then=("pull_webhooks",), applies=_gateway_key),
    Job("physical_mail", "Physical mail", "Reads and routes letters the mail relay and the mail-intake inbox kept.",
        HOUSEKEEPING, "Every minute", 60, then=("route_letters",), applies=_physical_mail),
    Job("letters", "Letters sync", "Sends approved letters and follows each one until it is delivered.",
        HOUSEKEEPING, "Every 30 seconds", 30, then=("send_letters",), applies=_letters),
    Job("sleep_idle", "Sleep when idle", "Asks HQ to put this server to sleep once nobody has used it for the set time.",
        HOUSEKEEPING, "Every 5 minutes; every minute on a cell", then=("ask_sleep",), applies=_sleep_set),
    Job("cloud_machines", "Cloud machines", "Starts bots' cloud machines for work and stops them when idle.",
        HOUSEKEEPING, "Every 10 seconds", 10, then=("manage_machines",), applies=_cloud_company),
]
BY_KEY = {job.key: job for job in JOBS}


def applies(c, settings, job):
    """Whether `job` can do anything on this install (`Job.applies`); yes when it cannot be told."""
    if job.applies is None:
        return True
    try:
        return bool(job.applies(c, settings))
    except Exception:  # noqa: BLE001 - a table an older file lacks, a setting not read: the job stays listed
        return True


def not_applying(c, settings):
    """The keys of the jobs that cannot do anything on this install: the lists leave them out."""
    return {job.key for job in JOBS if not applies(c, settings, job)}


# ----------------------------------------------------------------------------- what a person may change
FIFTEEN, HOURLY, FOUR_HOURS, DAILY, BUSINESS, WEEKLY, THREE_DAILY = ("15m", "hourly", "4h", "daily", "business", "weekly",
                                                                  "three_daily")
PRESETS = {FIFTEEN: ("Every 15 minutes", 900), HOURLY: ("Hourly", 3600), FOUR_HOURS: ("Every 4 hours", 4 * 3600),
           DAILY: ("Daily at {at}", None), BUSINESS: ("Hourly on weekdays, 08:00 to 18:00", 3600),
           WEEKLY: ("Weekly", 7 * 86400), THREE_DAILY: ("Three times a day (6am, noon, 6pm company time)", None)}
BUSINESS_HOURS = (clock(8), clock(18))      # company time (updates.ZONE), Monday to Friday
EVERY_PRESET = (FIFTEEN, HOURLY, FOUR_HOURS, DAILY)
# The company-time times of a preset that runs more than once a day: a run is due at each of them (occurrence).
RUN_TIMES = {THREE_DAILY: ((6, 0), (12, 0), (18, 0))}
# A never-run job on one of these waits for its next time after the scheduler first sees it on, not a run at once.
WAITS_FOR_ANCHOR = (THREE_DAILY,)
AI_PRESETS = (HOURLY, FOUR_HOURS, DAILY)        # a job that asks Decide is never offered every 15 minutes
CONFIG_KEY = "system_routines"
AT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Control:
    rule: str | None = None     # its on/off is this team rule (backend/team_rules.py), else this registry's record
    on: bool = True             # the default when it is not a team rule
    presets: tuple = ()         # the schedule presets offered; () is a switch only
    preset: str | None = None   # the default preset
    at: str | None = None       # the default "HH:MM" (company time, updates.ZONE) for DAILY
    owner_only: bool = False    # only the owner changes it; no job uses that now


CONTROLS = {
    "stale_review": Control(rule="stale_review", presets=AI_PRESETS, preset=HOURLY, at="09:00"),
    "kpi_asks": Control(presets=EVERY_PRESET, preset=HOURLY, at="09:00"),
    "memory_tidy": Control(on=False, presets=(DAILY,), preset=DAILY, at="03:30"),
    "ask_deadlines": Control(presets=EVERY_PRESET, preset=HOURLY, at="09:00"),
    # Hourly while people are at work: a report outside business hours would wait on nobody to read it.
    "task_manager_report": Control(presets=(BUSINESS, HOURLY, FOUR_HOURS, DAILY), preset=BUSINESS, at="09:00"),
    "bookkeeper_check": Control(presets=EVERY_PRESET, preset=HOURLY, at="09:00"),
    "topic_fresh": Control(rule="topic_fresh"),
}


# Last runs read by backend/scheduler.py (QUICK and FIXED).
QUICK_KEYS = ("kpi_asks", "ask_deadlines", "task_manager_report",
              "bookkeeper_check", "bookkeeper_watch",
              "bookkeeper_rules_digest", "thread_followups", "thread_digest", "thread_bot_digest", "thread_retention")


def zone():
    from .updates import ZONE
    return ZONE


def _stored(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (CONFIG_KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def config(c, rules=None):
    """{job: {"on", "preset", "at"}} for every job in CONTROLS: what was saved, else its default. A team rule's on/off is
    the rule's value (its environment default included)."""
    from . import team_rules
    rules = rules if rules is not None else team_rules.load(c)
    stored, out = _stored(c), {}
    for key, ctl in CONTROLS.items():
        saved = stored.get(key) if isinstance(stored.get(key), dict) else {}
        preset = saved.get("preset") if saved.get("preset") in ctl.presets else ctl.preset
        at = saved.get("at") if isinstance(saved.get("at"), str) and AT_RE.match(saved["at"]) else ctl.at
        on = bool(rules[ctl.rule]) if ctl.rule else bool(saved["on"]) if isinstance(saved.get("on"), bool) else ctl.on
        out[key] = {"on": on, "preset": preset, "at": at}
    return out


def config_of(c, key):
    return config(c)[key]


def schedule_words(key, cfg):
    """A controlled job's schedule in words: "Every 4 hours", "Daily at 09:00"."""
    if not cfg or not cfg.get("preset"):
        return BY_KEY[key].schedule
    words = PRESETS[cfg["preset"]][0].format(at=cfg.get("at") or "")
    return "Nightly at " + cfg["at"] if key == "memory_tidy" else words


def may_change(auth, who, key):
    """An owner or an admin changes a job's switch, schedule and Run now. Raises a 403."""
    auth.domain(who)
    job = BY_KEY.get(key)
    if key not in CONTROLS or not job:
        raise Problem("not_found", "No such system routine", 404)
    if CONTROLS[key].owner_only:
        if who.role != "owner":
            raise Problem("forbidden", f"Only the owner changes {job.name}", 403)
    elif not auth.bot_admin(who):
        raise Problem("forbidden", "Only an owner or an admin changes system routines", 403)


def can_change(auth, who, key):
    if getattr(who, "support", None) == "app":
        return False            # Tico support reads as an admin but its app tier writes none of these (support_access)
    try:
        may_change(auth, who, key)
        return True
    except Problem:
        return False


def save_config(c, actor, key, on=None, preset=None, at=None):
    """Set a job's switch, preset or daily time; only what is sent changes. A team rule's switch is saved as that rule.
    Returns the job's config."""
    from . import team_rules
    ctl = CONTROLS[key]
    if preset is not None and preset not in ctl.presets:
        offered = ", ".join(PRESETS[p][0].format(at="a time") for p in ctl.presets) or "none"
        raise Problem("invalid", f"{BY_KEY[key].name} runs on one of: {offered}", 422)
    if at is not None and not AT_RE.match(str(at)):
        raise Problem("invalid", "Pick a time as HH:MM, from 00:00 to 23:59", 422)
    if (preset is not None or at is not None) and not ctl.presets:
        raise Problem("invalid", f"{BY_KEY[key].name} has no schedule to change", 422)
    before = config(c)[key]
    if on is not None and ctl.rule:
        team_rules.save(c, actor, {ctl.rule: bool(on)})
    stored = _stored(c)
    mine = dict(stored.get(key) or {}) if isinstance(stored.get(key), dict) else {}
    if on is not None and not ctl.rule:
        mine["on"] = bool(on)
    if preset is not None:
        mine["preset"] = preset
    if at is not None:
        mine["at"] = at
    if mine:
        stored[key] = mine
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (CONFIG_KEY, json.dumps(stored, sort_keys=True)))
    after = config(c)[key]
    if after != before:
        H.event(c, actor, "system_routines.updated", key, {"before": before, "after": after})
    return after


# ----------------------------------------------------------------------------- when a job is due
def _times(cfg):
    """The company-time (hour, minute) pairs a job runs at: its preset's (RUN_TIMES), else its own "at"."""
    if cfg.get("preset") in RUN_TIMES:
        return RUN_TIMES[cfg["preset"]]
    hour, minute = (int(x) for x in (cfg.get("at") or "00:00").split(":"))
    return ((hour, minute),)


def occurrence(cfg, at):
    """The latest run time at or before `at`, in company time."""
    local = at.astimezone(zone())
    points = [datetime.combine(local.date() + timedelta(days=day), clock(hour, minute), zone())
              for day in (-1, 0) for hour, minute in _times(cfg)]
    return max(point for point in points if point <= local)


def next_anchor(cfg, at):
    """The first run time after `at`, in company time."""
    local = at.astimezone(zone())
    points = [datetime.combine(local.date() + timedelta(days=day), clock(hour, minute), zone())
              for day in (0, 1) for hour, minute in _times(cfg)]
    return min(point for point in points if point > local)


def in_business_hours(at):
    """Whether `at` is a weekday between 08:00 and 18:00 company time."""
    local = at.astimezone(zone())
    return local.weekday() < 5 and BUSINESS_HOURS[0] <= local.time() < BUSINESS_HOURS[1]


def business_open(at):
    """`at` if it is in business hours, else the next time they open (08:00 the next weekday, or today's if it is early)."""
    if in_business_hours(at):
        return at
    local = at.astimezone(zone())
    day = local.date() if local.weekday() < 5 and local.time() < BUSINESS_HOURS[0] else local.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, BUSINESS_HOURS[0], zone()).astimezone(timezone.utc)


def due(cfg, at, last):
    """Whether a job on `cfg`'s preset runs at `at`, its last run having started at `last` (None: never). Its switch is
    not read here. The business-hours preset is hourly, and only inside the hours."""
    every = PRESETS.get(cfg.get("preset") or "", (None, None))[1]
    if cfg.get("preset") == BUSINESS and not in_business_hours(at):
        return False
    if every:
        return last is None or at - last >= timedelta(seconds=every)
    return last is None or last < occurrence(cfg, at)


def next_due(cfg, last, now):
    """When a job on `cfg`'s preset runs next: never in the past; None for an interval job that has not run yet."""
    every = PRESETS.get(cfg.get("preset") or "", (None, None))[1]
    if every and cfg.get("preset") == BUSINESS:
        return business_open(max(last + timedelta(seconds=every), now) if last else now)
    if every:
        return max(last + timedelta(seconds=every), now) if last else None
    if last is None and cfg.get("preset") in WAITS_FOR_ANCHOR:
        # Never run: its first run is its next time after the scheduler first sees it on (Scheduler.due), not now.
        return next_anchor(cfg, now)
    if due(cfg, now, last):
        return now
    return next_anchor(cfg, now)


def last_started(c, keys):
    """{job: the start of its newest run} for these jobs."""
    marks = ",".join("?" for _ in keys)
    rows = c.execute(f"SELECT job, max(started_at) FROM system_runs WHERE job IN ({marks}) GROUP BY job", tuple(keys))
    return {job: H.parse_ts(started) for job, started in rows.fetchall() if started}


# ----------------------------------------------------------------------------- the run record
@dataclass
class Run:
    """One run of a job, filled in by the job's block: `result` in words, and for a Decide job `ai(...)`."""
    job: str
    started: str = field(default_factory=H.now)
    result: str = ""
    error: str | None = None
    route: str | None = None
    fallback: str | None = None
    cost_usd: float | None = None
    quiet: bool = False         # nothing was done: may be folded into the job's last row

    def did(self, words, quiet=False):
        self.result, self.quiet = words, quiet
        return self

    def ai(self, routes=None, fallback=None, cost_usd=None):
        """The Decide calls of this run: `routes` counts calls per route (stale_review.route_of), `fallback` the
        first failure (`{"code", "from"}` or a bare code) when the first route did not answer, `cost_usd` the total."""
        routes = {k: v for k, v in (routes or {}).items() if v}
        if routes:
            self.route = max(routes, key=routes.get)
        self.fallback = fallback_words(fallback, self.route) if fallback and self.route != "gateway" else None
        self.cost_usd = round(float(cost_usd or 0), 6)
        return self


def fallback_words(fallback, route):
    """ "Ran on your own key: Gateway unavailable": what answered and why the first route did not."""
    if isinstance(fallback, dict):
        code, first = fallback.get("code") or "", fallback.get("from") or "gateway"
    else:
        code, first = str(fallback or ""), "gateway"
    lead = FELL_BACK_TO.get(route or "", "Fell back")
    return f"{lead}: {PROVIDER_WORDS.get(first, 'Gateway')} {FALLBACK_WORDS.get(code, 'unavailable')}"


# A failure's plain reason, from its kind; an exception's own text can carry a person's data and never shows.
FAILURE_WORDS = {"OperationalError": "the database was busy", "DatabaseError": "the database could not be read",
                 "IntegrityError": "a record conflicted", "TimeoutError": "it took too long", "Refused": "Tico refused it",
                 "Problem": "Tico refused it", "ConnectionError": "a service did not answer",
                 "OSError": "a file or connection failed", "JudgeError": "Decide did not answer"}


def failure_words(exc):
    name = type(exc).__name__
    return "Failed: " + FAILURE_WORDS.get(name, f"unexpected error ({name})")


def count_words(n, one, many=None):
    many = many or one + "s"
    return f"{n} {one if n == 1 else many}"


def words_of(found, one="change", none="Nothing to do", many=None):
    """A job's return in words: a count of a list or a number, or the counts of a dict of lists/numbers."""
    if isinstance(found, dict):
        parts = [f"{len(v) if isinstance(v, (list, tuple, set, dict)) else int(v or 0)} {k.replace('_', ' ')}"
                 for k, v in found.items() if isinstance(v, (list, tuple, set, dict, int)) and not isinstance(v, bool)]
        parts = [p for p in parts if not p.startswith("0 ")]
        return ", ".join(parts) or none
    n = len(found) if isinstance(found, (list, tuple, set)) else int(found or 0) if isinstance(found, int) else 0
    return count_words(n, one, many) if n else none


def _repeat(c, run, finished):
    """(the job's last row, whether it was moved under QUIET_WRITE_SECONDS ago) when `run` repeats it, else None: a
    quiet run with the last row's words, or a failure with the last row's reason. A failure every tick is one row that
    keeps its first time (`first_started_at`) and counts the repeats (`runs`)."""
    failed = run.error is not None
    if run.route is not None or not (run.quiet or failed):
        return None
    last = c.execute("SELECT id,ok,result,route,finished_at FROM system_runs WHERE job=? ORDER BY started_at DESC LIMIT 1",
                     (run.job,)).fetchone()
    if not last or last["route"] is not None or bool(last["ok"]) == failed \
            or last["result"] != (run.error if failed else run.result):
        return None
    moved = H.parse_ts(last["finished_at"])
    return last, bool(moved and H.parse_ts(finished) - moved < timedelta(seconds=QUIET_WRITE_SECONDS))


def _unseen(c, settings, run):
    """A quiet run of a job that does not apply here (`applies`): its row would be hidden and nothing reads it, so it
    is not written. A failure is always written, and so is a job whose last start times its next run (CONTROLS,
    QUICK_KEYS), or it would run every tick."""
    job = BY_KEY.get(run.job)
    if job is None or job.applies is None or not run.quiet or run.error is not None or run.route is not None:
        return False
    if job.key in CONTROLS or job.key in QUICK_KEYS:
        return False
    return not applies(c, settings, job)


def save(c, run, finished=None):
    """Write `run` in `c`: a new row, or a quiet repeat folded into the job's last row."""
    finished = finished or H.now()
    if repeat := _repeat(c, run, finished):
        last, recent = repeat
        if recent:
            return last["id"]
        c.execute("UPDATE system_runs SET started_at=?,finished_at=?,runs=runs+1 WHERE id=?", (run.started, finished, last["id"]))
        return last["id"]
    rid = H.new_id()
    c.execute("INSERT INTO system_runs(id,job,started_at,finished_at,ok,result,route,fallback,cost_usd,first_started_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (rid, run.job, run.started, finished, 0 if run.error else 1, run.error or run.result or "Done",
               run.route, run.fallback, run.cost_usd, run.started))
    return rid


def keep(c, run):
    """`save` inside a caller's transaction, where a failure to record must not undo the caller's work."""
    try:
        save(c, run)
    except Exception as exc:
        LOG.warning("Could not record the %s run: %s", run.job, type(exc).__name__)


def write(store, run):
    """`save` in its own short transaction; a failure to record never fails the job. A repeat moved under
    QUIET_WRITE_SECONDS ago, or a quiet run of a job that does not apply here (`_unseen`), takes no write lock at all."""
    try:
        with store.read() as c:
            if _unseen(c, getattr(store, "settings", None), run):
                return
            repeat = _repeat(c, run, H.now())
        if repeat and repeat[1]:
            return
        with store.transaction() as c:
            save(c, run)
    except Exception as exc:
        LOG.warning("Could not record the %s run: %s", run.job, type(exc).__name__)


@contextmanager
def recording(store, key, failures=None, name=None, c=None):
    """Around one job's block: yields its `Run`; records it on the way out, in `c` when given (a block inside the
    scheduler's own transaction), else in its own transaction. An exception is the run's failure and, as the
    scheduler always did, one `{name: kind}` entry in `failures`; it does not propagate when `failures` is given."""
    run = Run(key)
    try:
        yield run
    except Exception as exc:
        run.error = failure_words(exc)
        if failures is None:
            raise
        failures.append({name or key: type(exc).__name__})
    finally:
        if c is not None:
            keep(c, run)
        else:
            write(store, run)


def pass_of(store, key, work, *args, words=None):
    """One pass of a background loop (backend/app.py) as one run of `key`, and `work`'s return. `words(found)` puts what
    it did in words, or None for nothing (a quiet run that folds); without it every pass is a quiet "Done"."""
    with recording(store, key) as run:
        found = work(*args)
        said = words(found) if words else None
        run.did(said or "Done", quiet=not said)
    return found


def prune(c):
    """Each job keeps its last KEEP rows."""
    for (job,) in c.execute("SELECT DISTINCT job FROM system_runs").fetchall():
        c.execute("DELETE FROM system_runs WHERE job=? AND id NOT IN "
                  "(SELECT id FROM system_runs WHERE job=? ORDER BY started_at DESC LIMIT ?)", (job, job, KEEP))


# ----------------------------------------------------------------------------- the list
def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_at(job, last, now, settings=None, c=None, cfg=None):
    """When the job runs next, from its schedule and its last run: a controlled job by its preset (`due`), nothing while
    it is off; another `every` after the last start (never in the past), the directory's own interval. None when it
    cannot be told."""
    if cfg is not None:
        if not cfg["on"]:
            return None
        return next_due(cfg, H.parse_ts(last["started_at"]) if last else None, now)
    every = job.every
    if job.key == "directory_sync" and c is not None:
        every = (directory_minutes(c) or 0) * 60
    if not every:
        return None
    if not last:
        return None
    started = H.parse_ts(last["started_at"])
    return max(started + timedelta(seconds=every), now)


def directory_minutes(c):
    """The directory sync's interval in minutes, or None while no directory is connected (or its sync is off)."""
    from .directory import Sources, load
    cfg = load(c)
    if cfg["source"] not in Sources.FETCH or not cfg["interval_minutes"] or not cfg["confirmed"]:
        return None
    return int(cfg["interval_minutes"])


def spent_this_month(c, labels, at=None):
    """USD this month's `judge.spend` rows of these question sets cost (label `<set>@<version>`)."""
    if not labels:
        return None
    at = at or datetime.now(timezone.utc)
    month = at.astimezone(timezone.utc).strftime("%Y-%m-01T00:00:00")
    where = " OR ".join("json_extract(detail,'$.label') LIKE ?" for _ in labels)
    total = c.execute("SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events "
                      f"WHERE action='judge.spend' AND created_at>=? AND ({where})",
                      (month, *[f"{label}@%" for label in labels])).fetchone()[0]
    return round(float(total or 0), 4)


def last_runs(c):
    """The newest row per job."""
    rows = c.execute("SELECT r.* FROM system_runs r JOIN (SELECT job, max(started_at) AS started_at FROM system_runs "
                     "GROUP BY job) m ON m.job=r.job AND m.started_at=r.started_at").fetchall()
    return {r["job"]: dict(r) for r in rows}


def last_ai_runs(c):
    """The newest row with a route per Decide job: a run that asked nothing says nothing of the route."""
    keys = [job.key for job in JOBS if job.ai]
    out = {}
    for key in keys:
        row = c.execute("SELECT * FROM system_runs WHERE job=? AND route IS NOT NULL ORDER BY started_at DESC LIMIT 1",
                        (key,)).fetchone()
        if row:
            out[key] = dict(row)
    return out


def departments(c):
    """The org chart's departments: its top-level groups' names (backend/groups.py)."""
    from .groups import _roster
    try:
        groups = _roster(c).get("org_groups") or {}
    except Exception:
        return []
    return [g["name"] for g in groups.values() if not g.get("parent")]


def phrase(key, schedule):
    """A trigger's words; the schedule's own for a scheduled job ("every hour", "every day at 09:00")."""
    if key == "schedule":
        words = {"Hourly": "every hour", "Daily": "every day"}.get(schedule, schedule)
        return words[:1].lower() + words[1:]
    return TRIGGERS[key][0]


def search_words(job, when, then, department):
    """What search matches a row by: its name, trigger, actions, department tag, group and what they touch."""
    extra = [w for key in job.then for w in ACTIONS[key][1]] + list(TRIGGERS.get(job.when, ("", ()))[1])
    return " ".join([job.name, job.what, when, *then, department, GROUP_WORDS[job.group], *extra]).lower()


def listing(c, settings, now=None, who=None, auth=None, everything=False):
    """Every job that applies here (`not_applying`; `everything`: all of them) in one flat list (no headings): WHEN
    <trigger> THEN <actions>, its department tag, schedule, last and next run, on/off and, for a Decide job, its route
    and this month's spend; what this viewer may change, and the words search matches."""
    now = now or datetime.now(timezone.utc)
    last, ai_last, cfgs = last_runs(c), last_ai_runs(c), config(c)
    from . import task_rules
    chart = {d["name"].lower(): d["name"] for d in task_rules.departments(settings)}
    hidden = set() if everything else not_applying(c, settings)
    rows = []
    for job in JOBS:
        if job.key in hidden:
            continue                    # nothing to do on this install until its feature is set up
        row, cfg, ctl = last.get(job.key), cfgs.get(job.key), CONTROLS.get(job.key)
        schedule = schedule_words(job.key, cfg) if ctl and ctl.presets else job.schedule
        when = job.when
        if job.key == "directory_sync":
            minutes = directory_minutes(c)
            schedule = ("Off: no directory connected" if minutes is None else "Hourly" if minutes == 60
                        else f"Every {minutes} minutes")
            when = "directory" if minutes is None else "schedule"
        timed = cfg if ctl and ctl.presets else None
        following = next_at(job, row, now, settings, c, timed) if job.records else None
        when_words = phrase(when, schedule)
        then_words = [ACTIONS[key][0] for key in job.then]
        department = chart.get(job.department.lower(), job.department)
        item = {"key": job.key, "name": job.name, "what": job.what, "schedule": schedule, "ai": job.ai,
                "controllable": job.control, "needed": job.group == HOUSEKEEPING and not job.control,
                "records": job.records,
                "group": job.group, "when": [when_words], "then": then_words,
                "department": {"name": department, "in_chart": department.lower() in chart},
                "switch": bool(ctl), "on": cfg["on"] if cfg else True,
                "preset": cfg["preset"] if timed else None, "at": cfg["at"] if timed else None,
                "presets": [{"key": p, "label": PRESETS[p][0].format(at="a time")} for p in (ctl.presets if ctl else ())],
                "owner_only": bool(ctl and ctl.owner_only),
                "can_change": bool(ctl and who is not None and auth is not None and can_change(auth, who, job.key)),
                "last": None, "next": _stamp(following) if following else None}
        if job.key == "memory_tidy" and item["can_change"]:
            from . import memory_tidy_controls
            item["pilots"] = memory_tidy_controls.pilot_rows(c)
            item["pilot_models"] = memory_tidy_controls.model_options(c, settings)
        item["search"] = search_words(job, when_words, then_words, department)
        if job.key == "task_manager_report":
            from .task_manager import active as task_manager_active
            item["note"] = None if task_manager_active(c) else "Opens no tasks while the Task Manager is paused (Settings > Bots)"
        if job.key in ("bookkeeper_check", "bookkeeper_watch", "bookkeeper_rules_digest"):
            from .bookkeeper import active as bookkeeper_active
            item["note"] = None if bookkeeper_active(c) else "Opens no tasks while the Bookkeeper is paused (Settings > Bots)"
        if row:
            item["last"] = {"started_at": row["started_at"], "finished_at": row["finished_at"], "ok": bool(row["ok"]),
                            "result": row["result"], "runs": row["runs"]}
        if job.ai:
            answered = ai_last.get(job.key)
            item["route"] = ROUTE_WORDS.get(answered["route"], "Other") if answered else None
            item["fallback"] = answered["fallback"] if answered else None
            item["spent_this_month_usd"] = spent_this_month(c, job.labels, now)
        rows.append(item)
    rows.extend(task_rules.listing(c, settings, who, auth, now))
    from . import task_settings
    rows = [task_settings.describe_job(row) for row in rows]
    return {"jobs": rows, "departments": list(chart.values()), "task_rule_departments": task_rules.departments(settings),
            "task_rule_department": task_rules.department_of(c, settings, who.actor) if who else None,
            "can_create_task_rule": bool(who and auth and (task_rules.department_of(c, settings, who.actor) or auth.bot_admin(who)) and not who.support),
            "now": _stamp(now)}


def row_of(c, settings, key, who=None, auth=None):
    return next(job for job in listing(c, settings, who=who, auth=auth, everything=True)["jobs"] if job["key"] == key)


def install(app, store, auth, mutate):
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/system-routines")
    def system_routines(request: Request):
        """Every job Tico runs on its own, in one flat list: WHEN <trigger> THEN <actions>, its department tag, schedule,
        last and next run, on/off, for a Decide job which route answered and this month's spend, and `can_change`: what
        this viewer may switch, reschedule and run now. Everyone who can open Settings reads it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, store.settings, who=who, auth=auth)

    @app.put("/api/v2/system-routines/{key}")
    def system_routine_save(key: str, request: Request, body: M.SystemRoutineUpdate):
        """Switch a job on or off, or pick its schedule preset and daily time. An owner or an admin.
        Stale ticket review's and Topic changed's switch is their team rule (backend/team_rules.py)."""
        who = request.state.identity
        may_change(auth, who, key)
        if body.pilots is not None and key != "memory_tidy":
            raise Problem("invalid", "Only Nightly memory tidy has a pilot list", 422)

        def work(c):
            if body.pilots is not None:
                from . import memory_tidy_controls
                memory_tidy_controls.save(c, store.settings, who.actor, [p.model_dump() for p in body.pilots])
            return save_config(c, who.actor, key, body.on, body.preset, body.at)
        mutate(request, body, work)
        with store.read() as c:
            return {"job": row_of(c, store.settings, key, who, auth)}

    @app.post("/api/v2/system-routines/{key}/run")
    def system_routine_run(key: str, request: Request, body: M.Empty):
        """Run one of the controlled jobs once, now, through the code and caps of its scheduled run (a stale ticket
        review still makes at most its hourly Decide calls); its result lands on its row. `running` while a job that
        asks Decide works on its own thread. 409 while it is off or already running. The retired task_roundup and
        learnings jobs run Maintenance Find stuck and Learn respectively and return {run, started}."""
        who = request.state.identity
        if key in ("task_roundup", "learnings"):
            from . import maintenance
            maintenance.may_change(auth, who)
            step = "find_stuck" if key == "task_roundup" else "learn"
            run, started = maintenance.run_now(store, auth, who.actor, step=step,
                                               key=request.headers.get("idempotency-key"))
            return {"run": run, "started": started}
        may_change(auth, who, key)
        job = BY_KEY[key]
        if not job.control:
            raise Problem("invalid", f"{job.name} has nothing to run", 422)
        with store.read() as c:
            if not config_of(c, key)["on"]:
                raise Problem("conflict", f"{job.name} is off. Switch it on to run it.", 409)
        from .scheduler import run_now
        running = run_now(store, auth, key, who.actor)
        with store.read() as c:
            return {"job": row_of(c, store.settings, key, who, auth), "running": running}
