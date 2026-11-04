"""Check health: the Maintenance run's plain-code step (no bot, no model). It finds what is wrong with this install now.

Four readers feed it, each one read in the run's snapshot:
  - the code checks in backend/maintenance_health.py (the Health Monitor's old checks), and the busy-process records a
    computer posts hourly (`local_busy`, written by the server route, read here while fresh);
  - the production checks, backend/invariants.py CHECKS, through their `read` functions. Their `sweep` side effects (owner
    tasks, closing tasks on clear) are not used: every finding goes through this step's lifecycle;
  - the Health page's own checks (backend/health.py `view`), warn and bad ones, read as the owner (a read, no write). The
    mail sending check is left out: the mail reader below files it with its approval;
  - mail asks (backend/mail_settings.py `alerts`): a bot that asks to send mail, or to forward it, without a person's approval.

Lifecycle, per finding (its local key) and per signature (what it is, never where):
  - an urgent check files on the first run that finds it; a noisy check files on the second run (`pending` holds the first);
  - a setup finding files one BotOps task per signature. A repeat adds to the task's count (a comment once a day, no wake),
    a new computer with the same problem comments once, and a problem that clears gets a "cleared" comment that wakes BotOps
    to close the task. A task BotOps has closed is opened again the next time the problem shows;
  - a mail ask also gets an approval, raised by BotOps on its task, that turns mail sending on when a person approves it;
  - a Tico bug is not a task: it goes through the report path (backend/maintenance_health.py `report`), which the caller runs
    outside the write transaction for the signatures in `details.reports_due`;
  - with no active BotOps bot nothing is written: the findings are listed in `details.unrouted` on the step's row.

Check health never writes to a person. The one exception is the Tico bug report path, which the team rule
`health_monitor_reports` decides: a support approval a person reads first when that rule is off.

The step result is {status, reason, checked, found, woke, cost_usd, details}; `woke` counts the BotOps tasks it opened or
commented on this run.
"""
import json
import logging
from datetime import datetime, timezone

from .. import health, hosted_updates, invariants, mail_settings, proposals, replication
from .. import maintenance_health as MH
from ..store import H

log = logging.getLogger("tico.maintenance.check_health")

BOTOPS = MH.BOTOPS
MAIL_CHECK = "mail_ask"
BUSY_FRESH_HOURS = 2         # a busy process not posted this long clears: its computer stopped sampling
REREPORT_HOURS = 24          # a Tico bug already reported is reported again (a comment) at most this often
COMMENT_HOURS = 24           # a repeat adds to a BotOps task's count at most this often

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
    MAIL_CHECK: "noisy",
}
# A Health page check the mail reader already files, so the page reader leaves it out.
PAGE_LEFT_OUT = {"mail_sending"}
FIX_PAGE = ("BotOps: open the check on Settings > Health for the detail, fix the cause it names, and the check clears on the "
            "next run. A fix only a person can make reaches a person as a Needs you card on this task.")
FIX_MAIL = ("BotOps: ask the person who manages this bot to approve turning mail sending on. The approval on this task turns "
            "the setting on when it is approved, and does nothing else.")
FIX_RUN = "BotOps: nothing to do by hand; the check clears when the next run no longer finds it."


def tag_of(check_id):
    """`urgent` or `noisy` for a finding's check id (a page check is tagged by its Health page id)."""
    base = check_id[len("page:"):].split(":", 1)[0] if check_id.startswith("page:") else check_id
    return TAGS.get(base, "noisy")


def task_of(c, signature):
    """The BotOps task open for a signature, or None (a closed one is not open)."""
    task = _open(c, _reg_get(c, _reg_key(signature)).get("task"))
    return task["id"] if task else None


def run(c, ctx):
    """The step, inside the run's transaction (the Maintenance engine's contract)."""
    checked, found, notes = gather(c, ctx)
    return apply(c, ctx, checked, found, notes)


def run_once(store, auth, settings):
    """The step on its own, for the scheduler's timer until the Maintenance run replaces it: the readers in one read
    snapshot (no write lock while the page and the checks read), then the lifecycle in one short write transaction."""
    ctx = context(auth, settings)
    with store.read_transaction() as r:
        checked, found, notes = gather(r, ctx)
    with store.transaction() as c:
        return apply(c, ctx, checked, found, notes)


def context(auth, settings):
    """The run context when there is no Maintenance run: the same shape as the run's ctx, with auth for the Health page."""
    now = H.now()
    return {"run_id": "timer", "window_start": None, "window_end": now, "settings": settings, "now": now, "cap": None,
            "auth": auth}


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
            out.append(MH._finding(f"inv:{check.id}", check.id, MH.SETUP, f"Production check: {check.label}", detail,
                                   f"{check.look} {FIX_RUN}", severity=MH.WARN))
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
        page = health.view(c, who, ctx["settings"], auth, None, config)
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
    woke = 0

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
        occurrences = (row["occurrences"] if row else 0) + 1
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen_at,last_seen_at,"
                  "present,occurrences,severity,pending) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "signature=excluded.signature,check_id=excluded.check_id,kind=excluded.kind,title=excluded.title,"
                  "detail=excluded.detail,fix=excluded.fix,first_seen_at=excluded.first_seen_at,"
                  "last_seen_at=excluded.last_seen_at,present=1,occurrences=excluded.occurrences,"
                  "severity=excluded.severity,pending=excluded.pending",
                  (key, item["signature"], item["check_id"], item["kind"], item["title"], item["detail"], item["fix"],
                   first_seen, now, occurrences, item["severity"], pending))
        if filed:
            details["filed"].append(key)
            filed_sigs.add(item["signature"])
        else:
            details["pending"].append(key)

    # 3. Setup findings: one BotOps task per signature. Tico bugs are reported (returned for the caller to file).
    for sig in sorted(filed_sigs | gone_sigs):
        rows = _filed_rows(c, sig)
        if not rows:                                   # nothing filed is left for this signature: it cleared
            woke += _cleared_comment(c, sig, now, details)
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
        woke += _task_for(c, sig, rows, now, details)

    details["open"] = c.execute("SELECT count(*) FROM health_findings WHERE present=1 AND pending=0").fetchone()[0]
    return {"status": "ran", "reason": "", "checked": checked, "found": len(seen), "woke": woke, "cost_usd": 0.0,
            "details": details}


def _task_for(c, sig, rows, now, details):
    """Opens or updates the BotOps task of a setup signature with its filed rows. Returns the number of tasks woken."""
    reg = _reg_get(c, _reg_key(sig))
    task = _open(c, reg.get("task"))
    total = sum(r["occurrences"] for r in rows)
    places = len(rows)
    if task is None:
        body = _task_body(sig, rows, total, now)
        made = _task_create(c, rows[0]["title"], body)
        if made is None:
            details["unrouted"].append({"signature": sig, "titles": [r["title"] for r in rows], "refused": True})
            return 0
        reg = {"task": made["id"], "keys": places, "count": total, "last_comment": now, "cleared": False,
               "approvals": reg.get("approvals", {})}
        _reg_set(c, _reg_key(sig), reg)
        details["tasks"][sig] = made["id"]
        _mail_approvals(c, sig, rows, made["id"], reg, details, now)
        return 1
    woke = 0
    if reg.get("cleared"):
        _comment(c, task["id"], f"Back: {rows[0]['title']} (seen {total} times, last at {now[:16]} UTC). "
                                f"The task was marked cleared; this is the same problem again.", wake=True)
        woke = 1
        reg["cleared"] = False
    elif places > reg.get("keys", 1):
        _comment(c, task["id"], f"Also on {places} places now (last seen {now[:16]} UTC): {rows[0]['title']}.", wake=False)
        reg["keys"] = places
    elif _elapsed(reg.get("last_comment"), now, COMMENT_HOURS):
        _comment(c, task["id"], f"Still there: seen {total} times since {rows[0]['first_seen_at'][:16]} UTC, last at "
                                f"{now[:16]} UTC.", wake=False)
        reg["last_comment"] = now
    reg["count"] = total
    _reg_set(c, _reg_key(sig), reg)
    details["tasks"][sig] = task["id"]
    _mail_approvals(c, sig, rows, task["id"], reg, details, now)
    return woke


def _cleared_comment(c, sig, now, details):
    """A setup signature with no filed finding left: its open task gets a cleared comment (BotOps closes it)."""
    reg = _reg_get(c, _reg_key(sig))
    if not reg or reg.get("cleared"):
        return 0
    task = _open(c, reg.get("task"))
    if task is None:
        reg["cleared"] = True
        _reg_set(c, _reg_key(sig), reg)
        return 0
    _comment(c, task["id"], f"Cleared at {now[:16]} UTC: the check passes now, so this problem is gone. "
                            "BotOps: close this task.", wake=True)
    reg["cleared"] = True
    _reg_set(c, _reg_key(sig), reg)
    return 1


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
    """A Tico bug the team should hear of: never reported, or reported and seen more since, at most once a day."""
    filed = c.execute("SELECT * FROM health_reports WHERE signature=?", (sig,)).fetchone()
    if filed is None:
        return True
    row = c.execute("SELECT max(occurrences) n FROM health_findings WHERE signature=? AND present=1 AND pending=0",
                    (sig,)).fetchone()
    return bool(row and row["n"] and row["n"] > filed["reported_occurrences"]
                and _elapsed(filed["created_at"], now, REREPORT_HOURS))


def _filed_rows(c, sig):
    return c.execute("SELECT * FROM health_findings WHERE signature=? AND present=1 AND pending=0 ORDER BY key",
                     (sig,)).fetchall()


def _task_body(sig, rows, total, now):
    lines = [f"{r['title']}\n{r['detail']}\n\nFix:\n{r['fix']}" for r in rows]
    return (f"{rows[0]['title']}\n\nSignature: {sig}\nSeen: {total} time(s) on {len(rows)} place(s), first at "
            f"{min(r['first_seen_at'] for r in rows)[:16]} UTC (opened {now[:16]} UTC).\n\n" + "\n\n".join(lines)
            + "\n\nThis task stays open while the check finds it. It gets a cleared comment when the check passes: close it then.")


def _task_create(c, title, body):
    try:
        c.execute("SAVEPOINT check_health_task")
        made = H.task_create(c, H.KEEPER, f"Health: {title}"[:200], body, H.bot_actor(BOTOPS), lint=False)
        c.execute("RELEASE check_health_task")
        return made
    except Exception:  # noqa: BLE001 - a refusal to file one task must not undo the check's record
        c.execute("ROLLBACK TO check_health_task")
        c.execute("RELEASE check_health_task")
        log.warning("Check health: could not file the BotOps task for %s", title, exc_info=True)
        return None


def _comment(c, task_id, text, wake):
    try:
        c.execute("SAVEPOINT check_health_comment")
        H.task_comment(c, H.KEEPER, task_id, text, wake=wake)
        c.execute("RELEASE check_health_comment")
    except Exception:  # noqa: BLE001 - a comment that cannot be written waits for the next change
        c.execute("ROLLBACK TO check_health_comment")
        c.execute("RELEASE check_health_comment")
        log.warning("Check health: could not comment on task %s", task_id, exc_info=True)


# ------------------------------------------------------------------------------------------------------------- helpers
def _open(c, task_id):
    """The task, when it is still open for BotOps (not done, closed or declined), else None."""
    row = H.task(c, task_id) if task_id else None
    return row if row and row["status"] not in ("done", "closed", "declined") else None


def _botops_active(c):
    return (H.bot(c, BOTOPS) or {}).get("state") == "active"


def _reg_key(sig):
    return "health-task:" + sig


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
