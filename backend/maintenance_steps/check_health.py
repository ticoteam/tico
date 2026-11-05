"""Check health: the Maintenance run's plain-code step (no bot, no model). It finds what is wrong with this install now.

Four readers feed it, each one read in the run's snapshot:
  - the code checks in backend/maintenance_health.py (the Health Monitor's old checks), and the busy-process records a
    computer posts hourly (`local_busy`, written by the server route, read here while fresh);
  - the production checks, backend/invariants.py CHECKS, through their `read` functions. The invariant sweep keeps only the
    Health page's state for them and files no task: every finding goes through this step's lifecycle, as `inv:<check id>`;
  - the Health page's own checks (backend/health.py `view`), warn and bad ones, read as the owner (a read, no write). The
    mail sending check is left out: the mail reader below files it with its approval;
  - mail asks (backend/mail_settings.py `alerts`): a bot that asks to send mail, or to forward it, without a person's approval.

Lifecycle, per finding (its local key) and per signature (what it is, never where):
  - an urgent check files on the first run that finds it; a noisy check files on the second run (`pending` holds the first);
  - a setup finding files one BotOps task per signature. A repeat adds to the task's count (a comment once a day), a new
    computer with the same problem comments once, and a problem that clears gets a "cleared" comment BotOps closes the task
    on. A task BotOps has closed is opened again the next time the problem shows. A task a person was given since is not
    BotOps's any more: nothing is written to it, and a new BotOps task opens when the problem shows;
  - BotOps is woken once per run, not once per task: a new task, a back, or a cleared one adds to the run's wake, and the
    wake is one comment, on one of those tasks, that lists them (`_wake`). The gate `_wake_allowed` may refuse it, or
    only a task at its daily cap: refused tasks wait in the marker `health-wake:pending` for the next allowed run. Every other comment is quiet;
  - a mail ask also gets an approval, raised by BotOps on its task, that turns mail sending on when a person approves it;
  - a Tico bug is not a task: it goes through the report path (backend/maintenance_health.py `report`). `run` calls it after
    its write transaction, for the signatures in `details.reports_due`, and the answers go to `details.reports`;
  - with no active BotOps bot nothing is written: the findings are listed in `details.unrouted` on the step's row.

Check health never writes to a person. The one exception is the Tico bug report path, which the team rule
`health_monitor_reports` decides: a support approval a person reads first when that rule is off.

The step result is {status, reason, checked, found, woke, cost_usd, details}; `woke` is 1 when the run's BotOps wake went
out, else 0.
"""
import json
import logging
from datetime import datetime, timezone

from .. import health, hosted_updates, invariants, mail_settings, maintenance_limits, proposals, replication
from .. import maintenance_health as MH
from .. import run_triggers as RT
from ..store import H

log = logging.getLogger("tico.maintenance.check_health")

BOTOPS = MH.BOTOPS
REGISTRY_PREFIX = "health-task:"  # registry_metadata key per signature: {task, keys, count, last_comment, cleared, approvals}
MAIL_CHECK = "mail_ask"
BUSY_FRESH_HOURS = 2         # a busy process not posted this long clears: its computer stopped sampling
REREPORT_HOURS = 24          # a Tico bug already reported is reported again (a comment) at most this often
COMMENT_HOURS = 24           # a repeat adds to a BotOps task's count at most this often
WAKE_KEY = "health-wake:pending"  # registry_metadata: the task ids whose BotOps wake waits (the gate refused it, or a comment failed)
WAKE_LIST = 20               # the wake comment names this many tasks; the rest are counted

# How fast a check files. "urgent": on the first run that finds it. Any check not listed is "noisy": it files on the second
# run that finds it, so a problem that comes and goes in one run does not wake anyone. Page checks are listed by their
# Health page id (the part before the first colon: `disk:<computer>` is `disk`).
TAGS = {
    # urgent: Tico's own pipeline is failing (a push, a worktree, the event stream, runner requests), a busy process
    # the runner already confirmed over two samples, the database and its write lock, backups, and sign-in
    "push_stuck": "urgent", "worktree_error": "urgent", "worktree_fallback": "urgent", "cannot_fetch": "urgent",
    "event_stream": "urgent", "readiness_403": "urgent", "local_busy": "urgent",
    "lease_overdue": "urgent", "task_stuck": "urgent", "sign_in_held": "urgent", "long_holds": "urgent",
    "runner_errors": "urgent",
    "backups": "urgent", "credential_kms": "urgent", "github_auth": "urgent", "computer_signin": "urgent",
    "models": "urgent", "gateway": "urgent", "hook_relay": "urgent",
    # noisy (the default for anything else): a state that can be a blip, a slow or idle computer, a usage limit, a version
    "runner_outdated": "noisy", "limited_no_fallback": "noisy", "default_login_limited": "noisy",
    "runner_restarting": "noisy", "stuck_setup": "noisy", "computer_silent": "noisy", "queue_idle": "noisy",
    "server_cpu": "noisy", "run_hold": "noisy", "new_sessions": "noisy", "turn_budget": "noisy",
    "reviewer_holds_tasks": "noisy", "responder_has_tasks": "noisy", "doer_idle": "noisy", "org_rules_unchecked": "noisy",
    "owner_unreachable": "noisy", "refused_again": "noisy", "webhook_stuck": "noisy", "automation_overdue": "noisy",
    "relay_silent": "noisy", "recorder_drops": "noisy", "queue": "noisy", "computer_logins": "noisy", "tokens": "noisy",
    "slow": "noisy", "runner_load": "noisy", "performance": "noisy", "disk": "noisy", "turns": "noisy",
    "stalled_tasks": "noisy",                  # main's "Tasks not progressing" (hubdb.stalled_tasks): a task that may move on its own
    MAIL_CHECK: "noisy",
}
# Health page checks this step files another way, so the page reader leaves them out: the mail reader files mail_sending
# with its approval, and `invariant:<id>` rows are the production checks, which the invariant reader files as `inv:<id>`.
PAGE_LEFT_OUT = {"mail_sending", "invariant"}
FIX_PAGE = ("BotOps: open the check on Settings > Health for the detail, fix the cause it names, and the check clears on the "
            "next run. A fix only a person can make reaches a person as a Needs you card on this task.")
FIX_MAIL = ("BotOps: ask the person who manages this bot to approve turning mail sending on. The approval on this task turns "
            "the setting on when it is approved, and does nothing else.")
FIX_RUN = "BotOps: nothing to do by hand; the check clears when the next run no longer finds it."


def tag_of(check_id):
    """`urgent` or `noisy` for a finding's check id: a page check by its Health page id (`page:disk:<computer>` is `disk`),
    a production check by its own id (`inv:lease_overdue` is `lease_overdue`)."""
    if check_id.startswith("page:"):
        base = check_id[len("page:"):].split(":", 1)[0]
    elif check_id.startswith("inv:"):
        base = check_id[len("inv:"):]
    else:
        base = check_id
    return TAGS.get(base, "noisy")


def task_of(c, signature):
    """The BotOps task open for a signature, or None (a closed one is not open)."""
    task = _open(c, _reg_get(c, _reg_key(signature)).get("task"))
    return task["id"] if task else None


def run(c, ctx):
    """The step, as the Maintenance engine calls it. `c` is a pooled read connection with no transaction open: every reader
    reads it. The lifecycle writes in the store's own write transaction (ctx["store"]). The Tico bug reports are filed after
    that transaction, outside any write lock, for the signatures it named (`details.reports_due`); their answers go to
    `details.reports`. The engine needs to know none of this."""
    store = ctx["store"]
    if store.settings.rehearsal or store.settings.demo:
        return {"status": "skipped", "reason": "rehearsal or demo copy", "checked": 0, "found": 0, "woke": 0,
                "cost_usd": 0.0, "details": {}}
    ctx = dict(ctx, settings=store.settings)     # the app's settings: the engine's ctx["settings"] is the step's own row
    checked, found, notes = gather(c, ctx)
    with store.transaction() as w:
        result = apply(w, ctx, checked, found, notes)
    support = getattr(store, "report_support", None)     # support.py sets it on the app's store; none without an app
    # Always called: it sends the reports a person approved since the last run, and the due ones (none, if nothing is due).
    result["details"]["reports"] = MH.file_reports(store, ctx["auth"], ctx["settings"], support,
                                                   result["details"].get("reports_due", []))
    return result


def run_once(store, auth, settings):
    """An explicit diagnostic pass: the readers in one read snapshot (no write lock while the page and the checks read),
    then the lifecycle in one short write transaction. The caller (MH.check) files the Tico bug reports."""
    ctx = context(auth, settings, store)
    with store.read_transaction() as r:
        checked, found, notes = gather(r, ctx)
    with store.transaction() as c:
        return apply(c, ctx, checked, found, notes)


def context(auth, settings, store=None):
    """The run context: the same shape the Maintenance run gives a step (with `store`, the write transaction and the
    report path go through it); with no store, the caller handles reads and writes."""
    now = H.now()
    return {"run_id": "diagnostic", "window_start": None, "window_end": now, "settings": settings, "now": now, "cap": None,
            "auth": auth, "store": store}


# --------------------------------------------------------------------------------------------------------------- readers
def gather(c, ctx):
    """Every reader, in the caller's snapshot. Returns (checks read, findings seen now, notes). A reader that fails is
    noted and skipped: the others still run, and nothing it would have cleared is cleared on its account."""
    settings, now, notes = ctx["settings"], _now(ctx), {}
    checked, found = 0, []
    found += _code_findings(c, settings, notes)
    checked += 1
    found += _busy_findings(c, now)
    count, items = _invariant_findings(c, now, _at(ctx), notes)
    checked += count
    found += items
    count, items = _page_findings(c, ctx, notes)
    checked += count
    found += items
    found += _mail_findings(c, notes)
    checked += 1
    return checked, found, notes


def _code_findings(c, settings, notes):
    try:
        return MH.findings(c, settings)
    except Exception as exc:  # noqa: BLE001 - one reader's failure must not stop the others or clear their findings
        log.warning("Check health: the code checks failed", exc_info=True)
        notes["code"] = f"failed: {type(exc).__name__}"
        _unread(notes, "code")
        return []


def _busy_findings(c, now):
    """Busy processes a computer posted in its hourly list (server route, MH.record_idle), while they are fresh. A busy
    process whose list stopped coming clears on its own once it is older than BUSY_FRESH_HOURS."""
    cutoff = H.shift(now, hours=-BUSY_FRESH_HOURS)
    out = []
    for row in c.execute("SELECT * FROM health_findings WHERE check_id='local_busy' AND present=1 AND last_seen_at>=?",
                         (cutoff,)).fetchall():
        out.append(_row_item(row))
    return out


def _invariant_findings(c, now, at, notes):
    """Each production check's readers' hits. The check is one finding: its count and examples are in its detail."""
    ictx = invariants.Ctx(now=now, at=at)
    out, read = [], 0
    for check in invariants.CHECKS:
        read += 1
        try:
            hits = check.read(c, ictx)
        except Exception:  # noqa: BLE001 - a check that cannot be read is not a finding, and not a clear either
            log.warning("Check health: production check %s could not be read", check.id, exc_info=True)
            _unread(notes, f"inv:{check.id}")
            continue
        if hits:
            shown = ", ".join(hits[:invariants.EXAMPLES])
            detail = f"{check.says}\n\n{len(hits)} found. Examples: {shown}."
            out.append(MH._finding(f"inv:{check.id}", f"inv:{check.id}", MH.SETUP, f"Production check: {check.label}",
                                   detail, f"{check.look} {FIX_RUN}", severity=MH.WARN))
    return read, out


def _page_findings(c, ctx, notes):
    """The Health page's warn and bad checks, read as the owner (a read). Their id and summary are the finding."""
    auth = ctx.get("auth")
    if auth is None:
        notes["page"] = "no auth in the step context"
        _unread(notes, "page")
        return 0, []
    try:
        who = auth.owner_identity(c)
        config = {"update": hosted_updates.notice(c, ctx["settings"]), "backup": replication.status()}
        github = getattr(ctx.get("store"), "github_app", None)   # install_github_app sets it: the App's health, no network
        page = health.view(c, who, ctx["settings"], auth, github, config)
    except Exception as exc:  # noqa: BLE001 - the page's own checks may fail on an install; the other readers still run
        log.warning("Check health: the Health page checks failed", exc_info=True)
        notes["page"] = f"failed: {type(exc).__name__}"
        _unread(notes, "page")
        return 0, []
    out = []
    for check in page.get("checks", []):
        base = str(check.get("id", "")).split(":", 1)[0]
        if check.get("status") not in ("warn", "bad") or base in PAGE_LEFT_OUT:
            continue
        fixes = "; ".join(str(f.get("label", "")) for f in check.get("fixes", []) if f.get("label"))
        fix = FIX_PAGE + (f" The page offers: {fixes}." if fixes else "")
        out.append(MH._finding(f"page:{check['id']}", f"page:{base}", MH.SETUP, str(check.get("label") or base),
                               str(check.get("summary", ""))[:1500], fix,
                               severity=MH.BAD if check.get("status") == "bad" else MH.WARN))
    return len(page.get("checks", [])), out


def _mail_findings(c, notes):
    """A bot that asks for mail sending (or forwarding) a person has not approved: one finding per bot, filed with its
    approval on the BotOps task."""
    try:
        asks = mail_settings.alerts(c)
    except Exception as exc:  # noqa: BLE001 - a read that fails is not an ask that cleared
        log.warning("Check health: mail asks could not be read", exc_info=True)
        notes["mail"] = f"failed: {type(exc).__name__}"
        _unread(notes, "mail")
        return []
    out = []
    for bot, extra in asks:
        name = (H.bot(c, bot) or {}).get("display_name") or bot
        out.append(MH._finding(f"mail:{bot}", MAIL_CHECK, MH.SETUP, "Mail sending needs a person's approval",
                               mail_settings.alert_text(name, extra), FIX_MAIL, severity=MH.WARN))
    return out


def _unread(notes, reader):
    """Marks a reader (or one production check) that failed this run: its findings were not read, so none of them clears."""
    notes.setdefault("unread", []).append(reader)


def _unread_key(key, unread):
    """Whether the reader of a finding failed this run, or the finding's own production check did."""
    if not unread:
        return False
    reader = next((name for prefix, name in (("page:", "page"), ("mail:", "mail"), ("inv:", "inv"), ("local_busy:", "busy"))
                   if key.startswith(prefix)), "code")
    return key in unread or reader in unread


def _row_item(row):
    return {"key": row["key"], "signature": row["signature"], "check_id": row["check_id"], "kind": row["kind"],
            "title": row["title"], "detail": row["detail"], "fix": row["fix"], "severity": row["severity"]}


# ------------------------------------------------------------------------------------------------------------- lifecycle
def apply(c, ctx, checked, found, notes):
    """Records what the readers found, files it as the tags say, and writes the tasks, comments and approvals. Returns the
    step result."""
    now, botops = _now(ctx), _botops_active(c)
    details = {"notes": notes, "botops": botops, "filed": [], "pending": [], "cleared": [], "tasks": {}, "unrouted": [],
               "reports_due": [], "approvals": []}
    seen = {item["key"]: item for item in found}
    wakes = []                                  # BotOps tasks whose change wakes BotOps this run: new, back, cleared

    # 1. A finding no reader saw now is gone. A filed one clears (its signature may get a comment below); a pending one just drops.
    gone_sigs = set()
    unread = set(notes.get("unread", []))
    for row in c.execute("SELECT key,signature,pending FROM health_findings WHERE present=1").fetchall():
        if row["key"] in seen or _unread_key(row["key"], unread):
            continue
        c.execute("UPDATE health_findings SET present=0, pending=0 WHERE key=?", (row["key"],))
        if not row["pending"]:
            gone_sigs.add(row["signature"])
            details["cleared"].append(row["key"])
        _forget_approval(c, row["key"])

    # 2. What the readers found now: urgent files now, noisy on its second run.
    filed_sigs = set()
    for key, item in seen.items():
        row = c.execute("SELECT * FROM health_findings WHERE key=?", (key,)).fetchone()
        was_present = bool(row and row["present"])
        urgent = tag_of(item["check_id"]) == "urgent"
        # Urgent files now. A noisy check files on its second run (its row was pending), and a repeat of a filed one counts.
        filed = was_present or urgent
        pending = 0 if filed else 1
        first_seen = row["first_seen_at"] if was_present else now
        # A busy process keeps the sample time its computer posted (MH.record_idle): a run does not refresh it, so the process
        # clears when that time is too old (_busy_findings), not when a run happens to see it.
        sampled = row["last_seen_at"] if row and item["check_id"] == "local_busy" else now
        occurrences = (row["occurrences"] if row else 0) + 1
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen_at,last_seen_at,"
                  "present,occurrences,severity,pending) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "signature=excluded.signature,check_id=excluded.check_id,kind=excluded.kind,title=excluded.title,"
                  "detail=excluded.detail,fix=excluded.fix,first_seen_at=excluded.first_seen_at,"
                  "last_seen_at=excluded.last_seen_at,present=1,occurrences=excluded.occurrences,"
                  "severity=excluded.severity,pending=excluded.pending",
                  (key, item["signature"], item["check_id"], item["kind"], item["title"], item["detail"], item["fix"],
                   first_seen, sampled, occurrences, item["severity"], pending))
        if filed:
            details["filed"].append(key)
            filed_sigs.add(item["signature"])
        else:
            details["pending"].append(key)

    # 3. Setup findings: one BotOps task per signature. Tico bugs are reported (returned for the caller to file). A task
    # registered for a signature with nothing filed left (a task seeded by the Health Monitor's retirement, or one whose
    # rows were lost) clears too, so BotOps is never left a task that nothing will ever close. Skipped on a run where a
    # reader failed: its rows are unread, not gone.
    registered = set() if unread else _registered_signatures(c)
    for sig in sorted(filed_sigs | gone_sigs | registered):
        rows = _filed_rows(c, sig)
        if not rows:                                   # nothing filed is left for this signature: it cleared
            _cleared_comment(c, sig, now, wakes)
            continue
        if sig not in filed_sigs:                      # another place still has it; nothing new was filed here
            continue
        if rows[0]["kind"] == MH.BUG:
            if _report_due(c, sig, now):
                details["reports_due"].append(sig)
            continue
        if not botops:
            details["unrouted"].append({"signature": sig, "titles": [r["title"] for r in rows]})
            continue
        _task_for(c, sig, rows, now, details, wakes)

    details["open"] = c.execute("SELECT count(*) FROM health_findings WHERE present=1 AND pending=0").fetchone()[0]
    woke = _wake(c, wakes, details)
    return {"status": "ran", "reason": "", "checked": checked, "found": len(seen), "woke": woke, "cost_usd": 0.0,
            "details": details}


def _task_for(c, sig, rows, now, details, wakes):
    """Opens or updates the BotOps task of a setup signature with its filed rows. A task that is new, comes back, or clears
    is added to `wakes` (one BotOps wake per run, `_wake`). A comment moves its marker only when it is written: a comment
    that fails is tried again on the next run."""
    reg = _reg_get(c, _reg_key(sig))
    task = _open(c, reg.get("task"))
    total = sum(r["occurrences"] for r in rows)
    places = len(rows)
    if task is None:
        body = _task_body(sig, rows, total, now)
        made = _task_create(c, rows[0]["title"], body)
        if made is None:
            details["unrouted"].append({"signature": sig, "titles": [r["title"] for r in rows], "refused": True})
            return
        reg = {"task": made["id"], "keys": places, "count": total, "last_comment": now, "cleared": False,
               "approvals": reg.get("approvals", {})}
        _reg_set(c, _reg_key(sig), reg)
        details["tasks"][sig] = made["id"]
        wakes.append(made["id"])
        _mail_approvals(c, sig, rows, made["id"], reg, details, now)
        return
    if reg.get("cleared"):
        if _comment(c, task["id"], f"Back: {rows[0]['title']} (seen {total} times, last at {now[:16]} UTC). "
                                   f"The task was marked cleared; this is the same problem again.", wake=False):
            reg["cleared"] = False
            wakes.append(task["id"])
    elif places > reg.get("keys", 1):
        if _comment(c, task["id"], f"Also on {places} places now (last seen {now[:16]} UTC): {rows[0]['title']}.",
                    wake=False):
            reg["keys"] = places
    elif _elapsed(reg.get("last_comment"), now, COMMENT_HOURS):
        if _comment(c, task["id"], f"Still there: seen {total} times since {rows[0]['first_seen_at'][:16]} UTC, last at "
                                   f"{now[:16]} UTC.", wake=False):
            reg["last_comment"] = now
    reg["count"] = total
    _reg_set(c, _reg_key(sig), reg)
    details["tasks"][sig] = task["id"]
    _mail_approvals(c, sig, rows, task["id"], reg, details, now)


def _cleared_comment(c, sig, now, wakes):
    """A setup signature with no filed finding left: its open task gets a cleared comment, and BotOps is woken for it. A task
    that is gone for BotOps (closed, or given to a person) is only marked cleared. The marker moves only with the comment."""
    reg = _reg_get(c, _reg_key(sig))
    if not reg or reg.get("cleared"):
        return
    task = _open(c, reg.get("task"))
    if task is None:
        reg["cleared"] = True
        _reg_set(c, _reg_key(sig), reg)
        return
    if not _comment(c, task["id"], f"Cleared at {now[:16]} UTC: the check passes now, so this problem is gone. "
                                   "BotOps: close this task.", wake=False):
        return
    wakes.append(task["id"])
    reg["cleared"] = True
    _reg_set(c, _reg_key(sig), reg)


def _mail_approvals(c, sig, rows, task_id, reg, details, now):
    """A mail ask gets one approval on its task, raised by BotOps, until a person decides it. It turns the setting on when
    approved (proposals.apply, mail_alert)."""
    if sig != MH.signature(MAIL_CHECK):
        return
    asked = reg.setdefault("approvals", {})
    for row in rows:
        bot = row["key"].split(":", 1)[1]
        if bot in asked:
            continue
        payload = _mail_payload(c, row)
        if payload is None:
            continue
        try:
            c.execute("SAVEPOINT check_health_approval")
            proposals.check(c, "mail_alert", payload)
            made = H.approval_request(c, "bot:" + BOTOPS, "mail_alert", payload, task_id=task_id,
                                      to=proposals.decider(c))
            c.execute("RELEASE check_health_approval")
        except Exception:  # noqa: BLE001 - an approval that cannot be raised waits for the next run, the task stays
            c.execute("ROLLBACK TO check_health_approval")
            c.execute("RELEASE check_health_approval")
            log.warning("Check health: could not raise the mail approval for %s", bot, exc_info=True)
            continue
        approval = made["id"] if isinstance(made, dict) else made
        asked[bot] = approval
        details["approvals"].append({"bot": bot, "approval": approval})
    _reg_set(c, _reg_key(sig), reg)


def _mail_payload(c, row):
    """The payload a person approves: the bot and the exact setting (outbound and forward targets) the bot asks for now."""
    bot = row["key"].split(":", 1)[1]
    asks = [extra for ask_bot, extra in mail_settings.alerts(c) if ask_bot == bot]
    if not asks:
        return None
    extra = asks[0]
    return {"bot": bot, "outbound_send": bool(extra["outbound_send"]), "forward_to": list(extra.get("forward_to") or [])}


def _forget_approval(c, key):
    """A mail ask that cleared: its approval entry goes, so the next ask for that bot raises a new one."""
    if not key.startswith("mail:"):
        return
    bot = key.split(":", 1)[1]
    row = c.execute("SELECT signature FROM health_findings WHERE key=?", (key,)).fetchone()
    sig = row["signature"] if row else MH.signature(MAIL_CHECK)
    reg = _reg_get(c, _reg_key(sig))
    if reg.get("approvals", {}).pop(bot, None) is not None:
        _reg_set(c, _reg_key(sig), reg)


def _report_due(c, sig, now):
    """A Tico bug the team should hear of: never reported, or seen more since its last report (a ticket or a comment), at most
    once a day from that report."""
    filed = c.execute("SELECT * FROM health_reports WHERE signature=?", (sig,)).fetchone()
    if filed is None:
        return True
    row = c.execute("SELECT max(occurrences) n FROM health_findings WHERE signature=? AND present=1 AND pending=0",
                    (sig,)).fetchone()
    return bool(row and row["n"] and row["n"] > filed["reported_occurrences"]
                and _elapsed(filed["reported_at"] or filed["created_at"], now, REREPORT_HOURS))


def _filed_rows(c, sig):
    return c.execute("SELECT * FROM health_findings WHERE signature=? AND present=1 AND pending=0 ORDER BY key",
                     (sig,)).fetchall()


def _task_body(sig, rows, total, now):
    lines = [f"{r['title']}\n{r['detail']}\n\nFix:\n{r['fix']}" for r in rows]
    return (f"{rows[0]['title']}\n\nSignature: {sig}\nSeen: {total} time(s) on {len(rows)} place(s), first at "
            f"{min(r['first_seen_at'] for r in rows)[:16]} UTC (opened {now[:16]} UTC).\n\n" + "\n\n".join(lines)
            + "\n\nThis task stays open while the check finds it. It gets a cleared comment when the check passes: close it then.")


def _task_create(c, title, body):
    """The BotOps task for a setup signature. Quiet: its owner's notice is written without a wake (`_wake` wakes BotOps once)."""
    try:
        c.execute("SAVEPOINT check_health_task")
        made = H.task_create(c, H.KEEPER, f"Health: {title}"[:200], body, H.bot_actor(BOTOPS), lint=False, quiet_notice=True)
        c.execute("RELEASE check_health_task")
        return made
    except Exception:  # noqa: BLE001 - a refusal to file one task must not undo the check's record
        c.execute("ROLLBACK TO check_health_task")
        c.execute("RELEASE check_health_task")
        log.warning("Check health: could not file the BotOps task for %s", title, exc_info=True)
        return None


def _comment(c, task_id, text, wake):
    """A comment as the keeper. True when written (and, for a wake, queued for BotOps); otherwise the caller keeps its
    marker as it was and the next run tries again."""
    try:
        c.execute("SAVEPOINT check_health_comment")
        refs = RT.stamp({"wake": "task_roundup", "no_mentions": True}, "task_roundup", "check_health") if wake else None
        message = H.task_comment(c, H.KEEPER, task_id, text, wake=wake, extra_refs=refs)
        queued = not wake or c.execute("SELECT 1 FROM jobs WHERE message_id=? AND bot_id=?",
                                       (message["id"], BOTOPS)).fetchone() is not None
        c.execute("RELEASE check_health_comment")
        return queued
    except Exception:  # noqa: BLE001 - a comment that cannot be written waits for the next run
        c.execute("ROLLBACK TO check_health_comment")
        c.execute("RELEASE check_health_comment")
        log.warning("Check health: could not comment on task %s", task_id, exc_info=True)
        return False


def _wake(c, wakes, details):
    """BotOps is woken once per run, however many of its tasks changed: one comment, on one of those tasks, that lists them.
    The tasks the gate refused (or whose comment failed) wait in the marker for the next run. Returns 1 when the wake went
    out, else 0."""
    waiting = _wake_waiting(c)
    ids = []
    for task_id in waiting + wakes:
        if task_id not in ids and _open(c, task_id):
            ids.append(task_id)
    if not ids:
        if waiting:
            c.execute("DELETE FROM registry_metadata WHERE key=?", (WAKE_KEY,))
        return 0
    allowed = _wake_allowed(c, ids)
    held = [task_id for task_id in ids if task_id not in allowed]
    if allowed and _comment(c, allowed[0], _wake_text(c, allowed), wake=True):
        maintenance_limits.record(c, BOTOPS, allowed)
        if held:
            _reg_set(c, WAKE_KEY, held)
            details["wake_waiting"] = held
        elif waiting:
            c.execute("DELETE FROM registry_metadata WHERE key=?", (WAKE_KEY,))
        details["woke_tasks"] = allowed
        return 1
    _reg_set(c, WAKE_KEY, ids)
    details["wake_waiting"] = ids
    return 0


def _wake_allowed(c, ids):
    """The tasks this run may wake BotOps for: the bot and fleet caps gate the whole wake, each task's daily cap only
    itself, so one capped task waits alone and never holds the rest of the group."""
    return [task_id for task_id in ids if maintenance_limits.allow(c, BOTOPS, task_id)]


def _wake_waiting(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (WAKE_KEY,)).fetchone()
    return json.loads(row["value"]) if row else []


def _wake_text(c, ids):
    # This list may land in a shared task's conversation: private tasks contribute neither their title nor their id.
    parts = ["a private health task" if H.task_private(c, task) else f"{task['title']} (task {task['id']})"
             for task in (H.task(c, task_id) for task_id in ids[:WAKE_LIST])]
    more = f" and {len(ids) - WAKE_LIST} more" if len(ids) > WAKE_LIST else ""
    return ("Health wake: these BotOps tasks changed since BotOps last looked: " + "; ".join(parts) + more
            + ". Each task says what changed; close the ones marked cleared.")


# ------------------------------------------------------------------------------------------------------------- helpers
def _open(c, task_id):
    """The task, when BotOps still owns it and it is open (not done, closed or declined), else None. A task given to a person
    since is not BotOps's to comment on: nothing is written to it, and a new BotOps task opens when the problem shows."""
    row = H.task(c, task_id) if task_id else None
    if row is None or row["owner_id"] != "bot:" + BOTOPS:
        return None
    return row if row["status"] not in ("done", "closed", "declined") else None


def _botops_active(c):
    return (H.bot(c, BOTOPS) or {}).get("state") == "active"


def _reg_key(sig):
    return REGISTRY_PREFIX + sig


def _registered_signatures(c):
    """Every signature with a BotOps task registered (open or not): the step's registry, one row per signature."""
    return {row["key"][len(REGISTRY_PREFIX):] for row in c.execute(
        "SELECT key FROM registry_metadata WHERE key LIKE ?", (REGISTRY_PREFIX + "%",)).fetchall()}


def _reg_get(c, key):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else {}


def _reg_set(c, key, value):
    c.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(value)))


def _elapsed(since, now, hours):
    """True when `now` is at least `hours` after `since` (an ISO UTC stamp), or `since` is missing."""
    if not since:
        return True
    return H.shift(since, hours=hours) <= now


def _now(ctx):
    now = ctx.get("now")
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return now or H.now()


def _at(ctx):
    now = ctx.get("now")
    return now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
