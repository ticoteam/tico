"""The engineering manager's one exceptions list, read from factory records (docs/software-factory.md).

Head of Engineering has no step on any ticket. A ticket that moves through its steps is nobody's news; the manager
reads, about hourly, the few things that did not move on their own and fixes their causes:

- `stuck`: a ticket, or the open check filed below it, with no live, queued or held run for STUCK_MINUTES and nothing
  recorded to wait on (a snooze, an ask, an approval, a blocker, a wait on a pull request or a release); and a ticket
  at a check step with no check filed and no wait for one. Each names its owner and since when it has had no run.
- `run`: the newest run on the ticket or its open check failed, was cut or is uncertain (waits for its owner's review),
  with no later run that finished. A second cut of the same work is the same entry.
- `pool`: a ticket waiting for a reviewer longer than STUCK_MINUTES, or at once when the step's pool is empty.
- `slots` / `backlog`: a builder holding more tickets than its step's `assign` slots, or a step over its `backlog`.
- `outage`: a review move Tico cannot retry because GitHub cannot be read (one entry, every ticket it holds); a pool bot
  that is crashed, quarantined, limited or offline.
- `refusal`: a refused move Tico is no longer retrying (move_retry gave up after RETRY_FOR), or one rule refusing a
  write on the ticket twice in a day with no retry pending.
- `merge`: an open pull request GitHub reports as conflicting, with its number and head.

An entry is one per subject (a ticket, a bot, a step or a service) and kind; `since` is when that cause began. Every
entry, a count included, is made only from the tickets its reader may read (`build`): a private ticket the manager does
not read adds nothing to a builder's slots, a step's backlog or the GitHub outage. The list is a read: it routes nothing. The hourly pass (`deliver`, system routine `engineering_exceptions`) wakes a manager
whose profile is `manager` with it only when an entry is new since the last list it was sent, so a week of tickets
that move on their own costs the manager no turn."""
from . import task_privacy as privacy
from .store import H

STUCK_MINUTES = 45
REFUSAL_HOURS = 24
FAILED = ("failed", "interrupted", "expired")
MAX_EXCEPTIONS = 500
MAX_LINES = 40                  # lines in the wake; the rest is in `hub pipeline`
EVENT = "exceptions.sent"
WAKE = "exceptions"
PROFILE = "manager"
BOT_HELD = ("crashed", "quarantined", "limited")


def reads_list(c, slug):
    """Whether this bot works from the exceptions list instead of per-ticket notices: its profile is `manager`."""
    from . import rules
    try:
        return bool(slug) and rules.profile_of(c, slug) == PROFILE
    except Exception:  # noqa: BLE001 - a profile that cannot be read keeps the notices it had
        return False


def _check_children(c, ids):
    """{child id: ticket id} for the open checks (Review, QA, Security) filed below these tickets."""
    from .check_pools import is_check_title
    if not ids:
        return {}
    out = {}
    for part in H.chunks(sorted(ids)):
        for row in c.execute(
                "SELECT t.id, t.title, r.to_task, p.type_id FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                "JOIN tasks p ON p.id=r.to_task "
                f"WHERE r.kind='parent' AND r.to_task IN ({','.join('?' * len(part))}) "
                f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))})", (*part, *H.ACTIVE_STATUSES)):
            if is_check_title(c, {"type_id": row["type_id"]}, row["title"]):
                out[row["id"]] = row["to_task"]
    return out


def _finished_check(c, ticket, step, since):
    """The newest check of this step's kind filed below the ticket since it entered the step and finished since, or
    None: the check was filed, and what stands is the ticket not moving on."""
    from . import step_flow
    for row in c.execute(
            "SELECT t.id, t.title, t.owner, coalesce(t.done_at, t.closed_at, t.updated_at) AS ended FROM task_relations r "
            "JOIN tasks t ON t.id=r.from_task WHERE r.kind='parent' AND r.to_task=? AND t.created_at>=? "
            f"AND t.status NOT IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) ORDER BY ended DESC, t.id",
            (ticket["id"], since, *H.ACTIVE_STATUSES)):
        found = step_flow.child_check(c, dict(row), ticket)
        if found and found["id"] == step["id"]:
            return dict(row)
    return None


def _entered(c, ids):
    """{task id: when it last entered its step or status}; a task created into it has its creation time."""
    out = {}
    for part in H.chunks(sorted(ids)):
        out.update(dict(c.execute(
            f"SELECT task_id, max(ts) FROM task_events WHERE task_id IN ({','.join('?' * len(part))}) "
            "AND field IN ('step','status') GROUP BY task_id", part).fetchall()))
    return out


def _runs(c, row):
    """(live, newest finished attempts newest first, held or queued job states) for the owner's work on one task: its
    attempts in the task's lanes, and the jobs written into its room or naming it."""
    slug = H.actor_id(row.get("owner") or "")
    if not H.is_bot(row.get("owner") or "") or not H._has_table(c, "attempts"):
        return False, [], []
    lanes, _root = H._session_lanes(c, row)
    attempts = H._rows(c.execute(
        "SELECT a.id, a.job_id, a.state, a.created_at, a.finished_at, a.result, a.stop_requested_at, d.end_reason "
        "FROM attempts a LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id WHERE a.bot=? AND a.lane IN (?,?) "
        "ORDER BY coalesce(a.finished_at,a.created_at) DESC, a.id DESC LIMIT 20", (slug, *lanes)))
    live = any(a["state"] in ("leased", "running") for a in attempts)
    jobs = [r[0] for r in c.execute(
        "SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? "
        "AND j.state IN ('queued','held','uncertain') AND (m.conversation_id=? OR json_extract(m.refs,'$.task')=?)",
        (slug, row.get("conversation_id") or "", row["id"]))]
    return live, [a for a in attempts if a["state"] not in ("leased", "running")], jobs


def _waits(c, row, at):
    """Why a quiet task is not stuck, or None: something is recorded that it waits on."""
    return H.recorded_wait(c, row, at)


def _label(c, row):
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return {"id": row["id"], "number": row.get("number"), "title": str(row.get("title") or "")[:150],
            "step": step[0] if step else None, "owner": H.actor_id(row.get("owner") or "") or None}


class _List:
    """Entries keyed by (subject, kind): a repeat keeps the earliest `since`."""

    def __init__(self):
        self.items = {}

    def add(self, subject, kind, cause, since, **extra):
        if not since:
            return
        key = (subject, kind)
        found = self.items.get(key)
        if found:
            found["since"] = min(found["since"], str(since))
            return
        self.items[key] = {"key": f"{subject}:{kind}", "kind": kind, "cause": cause, "since": str(since), **extra}


def _stuck(c, out, rows, children, at, reader):
    """Tickets and their open checks with no run for STUCK_MINUTES and nothing to wait on; failed or cut runs."""
    from . import check_pools
    from .pipeline_stats import cause_of
    tickets = {r["id"]: r for r in rows}
    by_ticket = {}
    for child_id, ticket_id in children.items():
        by_ticket.setdefault(ticket_id, []).append(child_id)
    work = list(rows) + [H.task(c, child_id) for child_id in children]
    entered = _entered(c, [w["id"] for w in work if w])
    cutoff = H.shift(at, minutes=-STUCK_MINUTES)
    for item in work:
        if not item or item["status"] not in H.ACTIVE_STATUSES:
            continue
        ticket = tickets.get(children.get(item["id"], item["id"]))
        if not ticket:
            continue
        if item["id"] in children and not H.bot_reads_task(c, reader, item):
            continue            # a check the manager cannot read is not named on its list
        live, finished, jobs = _runs(c, item)
        label = _label(c, ticket)
        check = None if item["id"] == ticket["id"] else {"id": item["id"], "title": str(item["title"] or "")[:150],
                                                         "owner": H.actor_id(item.get("owner") or "") or None}
        subject = "ticket:" + ticket["id"]
        extra = {"ticket": label, **({"check": check} if check else {})}
        start = entered.get(item["id"]) or item.get("created_at")
        newest = finished[0] if finished else None
        if "uncertain" in jobs:
            out.add(subject, "run", "a run is uncertain and waits for its owner's review",
                    (newest or {}).get("finished_at") or start, **extra)
        elif newest and newest["state"] in FAILED and (newest["finished_at"] or newest["created_at"]) >= start:
            streak = []
            for a in finished:
                if a["state"] not in FAILED:
                    break
                streak.append(a)
            first = streak[-1]
            word = "was cut" if newest["stop_requested_at"] or newest["state"] == "interrupted" else "failed"
            out.add(subject, "run", f"the last run {word} ({cause_of(newest)})"
                    + (f", {len(streak)} in a row" if len(streak) > 1 else ""),
                    first["finished_at"] or first["created_at"], **extra)
        if live or jobs or not H.is_bot(item.get("owner") or ""):
            continue
        if item["id"] == ticket["id"] and by_ticket.get(item["id"]):
            continue            # its open check is the work: that check is what is looked at
        if item["status"] != "doing" and item["id"] == ticket["id"]:
            found = check_pools.pool(c, item.get("step_id"))
            if (not found or not check_pools._prs(c, item, found[0])
                    or check_pools.waiting(c, item["id"]) or start > cutoff or _waits(c, item, at)):
                continue
            done = _finished_check(c, item, found[0], start)
            if not done:
                out.add(subject, "stuck", f"no check filed at {label['step']} and none waiting", start, **extra)
            elif done["ended"] <= cutoff:
                out.add(subject, "stuck", f"{done['title']} finished and the ticket has not moved on "
                        f"(owner {label['owner']})", done["ended"], **extra)
            continue
        last = max(x for x in (start, (newest or {}).get("finished_at") or (newest or {}).get("created_at")) if x)
        if last > cutoff or _waits(c, item, at):
            continue
        who = (check or label)["owner"]
        out.add(subject, "stuck", f"no run for {STUCK_MINUTES} minutes" + (f" on {check['title']}" if check else "")
                + f" (owner {who})", last, **extra)


def _pools(c, out, rows, at):
    """Tickets waiting for a reviewer: an empty pool at once, a busy one after STUCK_MINUTES."""
    from . import check_pools
    tickets = {r["id"]: r for r in rows}
    if not tickets or not H._has_table(c, "check_waits"):
        return
    cutoff = H.shift(at, minutes=-STUCK_MINUTES)
    for wait in c.execute("SELECT w.ticket_id, w.step_id, w.title, w.reason, w.created_at FROM check_waits w "
                          "WHERE w.state='waiting' ORDER BY w.created_at, w.id"):
        ticket = tickets.get(wait["ticket_id"])
        if not ticket or ticket.get("step_id") != wait["step_id"]:
            continue
        found = check_pools.pool(c, wait["step_id"])
        empty = not found or not found[1]
        if not empty and wait["created_at"] > cutoff:
            continue
        cause = (f"{wait['title']}: the step's pool is empty" if empty else
                 f"{wait['title']}: no reviewer free for {STUCK_MINUTES} minutes ({wait['reason'] or 'all busy'})")
        out.add("ticket:" + ticket["id"], "pool", cause, wait["created_at"], ticket=_label(c, ticket))


def _slots(c, out, typ, rows):
    """Builders over their step's slots, and steps over their backlog. Both count `rows` only: the allocator's own
    count (task_allocator.in_hand) reads every ticket, the ones the list's reader may not read included."""
    from . import step_flow, task_allocator
    steps = H._rows(c.execute("SELECT * FROM task_steps WHERE type_id=? ORDER BY position", (typ["id"],)))
    position = {s["id"]: s["position"] for s in steps}
    by_step = {}
    for r in rows:
        by_step.setdefault(r.get("step_id"), []).append(r)
    for step in steps:
        flow = step_flow.of(step) if step_flow._ready(c) else {}
        assign = flow.get("assign") or {}
        slots = int(assign.get("slots") or task_allocator.DEFAULT_SLOTS)
        after, before = task_allocator.held_between(c, typ["id"], step) if assign.get("pool") else (0, 0)
        for slug in assign.get("pool") or []:
            mine = sorted((r for r in rows if r.get("owner") == "bot:" + slug
                           and after < position.get(r.get("step_id"), after) < before),
                          key=lambda r: r.get("updated_at") or "")
            if len(mine) > slots:
                out.add("bot:" + slug, "slots", f"{slug} holds {len(mine)} tickets, over its {slots} slots at "
                        f"{step['name']}", mine[slots].get("updated_at") or step.get("updated") or H.now(), bot=slug)
        backlog = flow.get("backlog") or {}
        here = by_step.get(step["id"]) or []
        over = backlog.get("over")
        if isinstance(over, int) and len(here) > over:
            oldest = min((r.get("updated_at") or r.get("created_at") for r in here), default=None)
            out.add("step:" + step["id"], "backlog", f"{len(here)} tickets at {step['name']}, over its backlog of {over}",
                    oldest, step=step["name"])


def _pool_bots(c, typ):
    """Every bot named in this type's assign pools and check pools."""
    from . import check_pools, step_flow
    found = set()
    for step in H._rows(c.execute("SELECT * FROM task_steps WHERE type_id=?", (typ["id"],))):
        flow = step_flow.of(step) if step_flow._ready(c) else {}
        found.update((flow.get("assign") or {}).get("pool") or [])
        pooled = check_pools.pool(c, step["id"])
        if pooled:
            found.update(pooled[1])
    return sorted(found)


def _outages(c, out, typ, rows, at):
    """GitHub unreadable at a review move (one entry for every ticket it holds), and pool bots that cannot work."""
    from . import check_pools, move_retry
    held, since = [], None
    for row in rows:
        found = move_retry.remembered(c, row, H.parse_ts(at))
        if found and found[1].get("outage"):
            held.append(row.get("number") or row["id"])
            first = found[1].get("since") or found[0]["ts"]
            since = min(since, first) if since else first
    if held:
        out.add("service:github", "outage", f"GitHub cannot be read at a review move; Tico retries {len(held)} "
                "ticket(s) every few minutes", since, tickets=held[:50])
    for slug in _pool_bots(c, typ):
        row = c.execute("SELECT b.state, s.state AS status, s.updated_at FROM bots b LEFT JOIN bot_status s "
                        "ON s.bot=b.slug WHERE b.slug=?", (slug,)).fetchone()
        if not row or row["state"] != "active":
            continue            # a paused or archived bot is someone's choice, not an outage
        if row["status"] in BOT_HELD:
            out.add("bot:" + slug, "outage", f"{slug} is {row['status']}", row["updated_at"] or at, bot=slug)
        elif not check_pools.online(c, slug):
            seen = c.execute("SELECT max(r.last_seen_at) FROM assignments a JOIN runners r ON r.id=a.runner_id "
                             "WHERE a.bot=? AND r.revoked_at IS NULL", (slug,)).fetchone()[0]
            out.add("bot:" + slug, "outage", f"{slug}'s computer is offline", seen or at, bot=slug)


def _refusals(c, out, rows, at):
    """Refused moves no longer retried, and one rule refusing the same ticket twice in a day with no retry pending."""
    from . import move_retry
    tickets = {r["id"]: r for r in rows}
    if not tickets:
        return
    now = H.parse_ts(at)
    retried = set()
    for row in rows:
        last = move_retry._latest(c, row["id"])
        if not last or last["field"] != move_retry.FIELD or last["old"] != row.get("step_id"):
            continue
        detail = H._json(last["new"], {}) or {}
        if move_retry.remembered(c, row, now):
            retried.add(row["id"])
            continue
        if detail.get("state") == "waiting":
            out.add("ticket:" + row["id"], "refusal", f"the move to {detail.get('step')} was refused "
                    f"({detail.get('kind') or 'gate'}) and Tico stopped retrying it", detail.get("since") or last["ts"],
                    ticket=_label(c, row))
    counted = {}
    since = H.shift(at, hours=-REFUSAL_HOURS)
    for event in c.execute("SELECT ts, rule, detail FROM refusals WHERE ts>=? ORDER BY ts, id", (since,)):
        task_id = (H._json(event["detail"], {}) or {}).get("task")
        if task_id in tickets and task_id not in retried:
            counted.setdefault((task_id, str(event["rule"] or "a rule")), []).append(event["ts"])
    for (task_id, rule), times in counted.items():
        if len(times) >= 2:
            out.add("ticket:" + task_id, "refusal", f"refused {len(times)} times by {rule}", times[0],
                    ticket=_label(c, tickets[task_id]))


def _merges(c, out, rows):
    """Open pull requests GitHub reports as conflicting."""
    for row in rows:
        for link in c.execute("SELECT number, pr_sha, detail, mergeable, updated_at, created_at FROM task_links "
                              "WHERE task_id=? AND kind='pr' AND state IN ('open','draft') AND mergeable='conflict' "
                              "ORDER BY created_at", (row["id"],)):
            head = (H._json(link["detail"], {}) or {}).get("head_sha") or link["pr_sha"] or ""
            if link["number"] and head:
                out.add("ticket:" + row["id"], "merge", f"PR #{link['number']} cannot merge: it conflicts at {head[:12]}",
                        link["updated_at"] or link["created_at"], ticket=_label(c, row), pr=link["number"], head=head)


def build(c, typ, rows=None, at=None, reader=None):
    """Every current exception on an engineering type that `reader` (default: the type's manager) may know of, oldest
    first. `rows` are the type's open tickets the reader may read, when the caller has selected them; otherwise the
    open tickets are read here and kept only where the reader reads them (H.bot_reads_task), before anything is
    counted or capped. Every entry and count is made from that selection alone."""
    at = at or H.now()
    if not reader:
        manager = H.type_manager(c, {"type_id": typ["id"]})
        reader = H.bot_actor(manager) if manager else None
    if not reader:
        return []
    if rows is None:
        rows = [r for r in H._rows(c.execute(
            f"SELECT * FROM tasks WHERE type_id=? AND status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}) "
            "ORDER BY created_at", (typ["id"], *H.ACTIVE_STATUSES))) if H.bot_reads_task(c, reader, r)][:5000]
    rows = [dict(r) for r in rows if r["status"] in H.ACTIVE_STATUSES]
    out = _List()
    children = _check_children(c, {r["id"] for r in rows})
    _stuck(c, out, rows, children, at, reader)
    _pools(c, out, rows, at)
    _slots(c, out, typ, rows)
    _outages(c, out, typ, rows, at)
    _refusals(c, out, rows, at)
    _merges(c, out, rows)
    return sorted(out.items.values(), key=lambda e: (e["since"], e["key"]))[:MAX_EXCEPTIONS]


def report(c, auth, who, typ, rows=None, at=None):
    """The list for Pipeline: only the type's manager reads it, over the tickets it may read."""
    from .roadmap_flow import ENGINEERING
    if not typ or typ.get("role") != ENGINEERING:
        return None
    manager = H.type_manager(c, {"type_id": typ["id"]})
    if not manager or privacy.actor(who) != H.bot_actor(manager):
        return None             # someone else, or a caller acting for another reader
    if rows is None:
        rows = H.tasks(c, status=H.ACTIVE_STATUSES, type_id=typ["id"], limit=5000, visible=auth.task_sql(c, who))
    return build(c, typ, rows, at, reader=who.actor)


def render(entries, more=0):
    lines = []
    for e in entries:
        t = e.get("ticket") or {}
        subject = (f"#{t['number']} {t['title']}" if t.get("number") is not None else t.get("title")) if t else \
            (e.get("bot") or e.get("step") or e["key"].split(":", 1)[0])
        lines.append(f"- {subject}: {e['cause']}, since {str(e['since'])[:16].replace('T', ' ')}Z"
                     + (f" [{t['step']}]" if t.get("step") else ""))
    if more:
        lines.append(f"- and {more} more: `hub pipeline` lists every one.")
    return "\n".join(lines)


def deliver(c, at=None):
    """The hourly pass: each engineering type whose manager reads the list gets one wake with it, only when an entry
    is new since the last list it was sent. Notes held for it (verdicts, results, ticket news) wake nothing: they ride
    with its next run, whatever starts it. Returns the managers woken."""
    from . import rules
    from .roadmap_flow import ENGINEERING
    at = at or H.now()
    woke = []
    for typ in H._rows(c.execute("SELECT * FROM task_types WHERE role=? AND manager IS NOT NULL ORDER BY id",
                                 (ENGINEERING,))):
        slug = typ["manager"]
        if not reads_list(c, slug):
            continue
        entries = build(c, typ, at=at, reader=H.bot_actor(slug))
        keys = sorted(e["key"] for e in entries)
        last = c.execute("SELECT detail FROM events WHERE action=? AND target=? ORDER BY rowid DESC LIMIT 1",
                         (EVENT, typ["id"])).fetchone()
        sent = set((H._json(last[0], {}) or {}).get("keys") or []) if last else set()
        new = [k for k in keys if k not in sent]
        if new:
            shown = entries[:MAX_LINES]
            text = (f"Exceptions on {typ['name']}: {len(entries)} ({len(new)} new). Fix each cause: move a ticket by "
                    "hand, reassign it, or close a duplicate. Tickets that move on their own are not listed.\n\n"
                    + render(shown, len(entries) - len(shown)))
            ok, _detail = rules._tell_bot(c, WAKE, slug, text, {"wake": WAKE, "type": typ["id"]}, wake=True)
            if not ok:
                continue        # nobody could be told: the same entries are new again on the next pass
            woke.append(slug)
        if new or set(keys) != sent:
            # The list as it stands is what the next pass compares with; an entry that cleared and comes back is new.
            H.event(c, H.KEEPER, EVENT, typ["id"], {"keys": keys, "bot": slug, "woke": bool(new)})
    return woke
