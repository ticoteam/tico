"""Task Trigger actions, drained after the complete domain write, inside its transaction.

Message rules and built-in step notices retain their existing behavior. This queue is
request local; the execution ledger, pause state and errors are durable SQL state.
"""
import contextvars
import logging
from contextlib import contextmanager

from . import hubdb as H

TRIGGERS = ("task.created", "task.step_changed", "task.done", "task.declined", "task.closed")
CONDITIONS = ("type", "step", "tag", "owner", "requester")
ACTIONS = ("note", "set_wait", "relate", "move_step", "close", "wake_bot", "create_task", "send_webhook")
_PENDING = contextvars.ContextVar("task_trigger_pending", default=None)
_CHAIN = contextvars.ContextVar("task_trigger_chain", default=())
log = logging.getLogger(__name__)


@contextmanager
def from_action(rule_id):
    token = _CHAIN.set(_CHAIN.get() or (rule_id,))
    try:
        yield
    finally:
        _CHAIN.reset(token)


@contextmanager
def pending(conn):
    current = _PENDING.get()
    if current is not None and current["conn"] is conn:
        yield
        return
    state = {"conn": conn, "items": []}
    token = _PENDING.set(state)
    try:
        yield
        while state["items"]:
            # Freeze all facts from this completed write before any Action changes them.
            for item in state["items"]:
                if item["row"] is None:
                    item["row"] = {**(H.task(conn, item["task"]) or {}), **item["facts"]}
            item = state["items"].pop(0)
            judge(conn, item)
    finally:
        _PENDING.reset(token)


def enqueue(conn, event, task, actor, revision, *, field=None, new=None):
    if event not in TRIGGERS:
        return
    state = _PENDING.get()
    facts = {"step_id" if field == "step" else field: new} if field in ("step", "status") else {}
    item = {"event": event, "task": task, "actor": actor, "revision": str(revision),
            "chain": _CHAIN.get(), "facts": facts, "row": None}
    if state is not None and state["conn"] is conn:
        state["items"].append(item)
    else:
        # Upgrade helpers occasionally write task events directly, outside a public task write.
        item["row"] = {**(H.task(conn, task) or {}), **facts}
        judge(conn, item)


def created(conn, task, actor):
    state = _PENDING.get()
    if state is not None and state["conn"] is conn:
        # A plain SQLite connection cannot carry hubdb's `building` marker. Initial
        # placement still counts once, after created, on those connections too.
        state["items"][:] = [i for i in state["items"] if i["task"] != task]
    enqueue(conn, "task.created", task, actor, "created")
    if (H.task(conn, task) or {}).get("step_id"):
        enqueue(conn, "task.step_changed", task, actor, "created")


def state(conn, rule_id):
    row = conn.execute("SELECT * FROM rule_task_state WHERE rule_id=?", (rule_id,)).fetchone()
    result = dict(row) if row else {}
    return {"paused": bool(result.get("paused_at")),
            **{k: result.get(k) for k in ("paused_at", "pause_reason", "last_error", "last_error_at")}}


def error(conn, rule_id, detail):
    conn.execute("INSERT INTO rule_task_state(rule_id,last_error,last_error_at) VALUES(?,?,?) "
                 "ON CONFLICT(rule_id) DO UPDATE SET last_error=excluded.last_error,last_error_at=excluded.last_error_at",
                 (rule_id, detail, H.now()))


def webhook_result(conn, event_id, status):
    """Delivery runs later, after the triggering transaction, and reports safe status only."""
    # Keep the last failure visible; another delivery's success cannot resolve it.
    if status is not None and 200 <= status < 300:
        return
    for row in conn.execute("SELECT rule_id FROM rule_task_runs WHERE webhook_event_id=?", (event_id,)):
        reason = f"HTTP {status}" if isinstance(status, int) else "connection error"
        error(conn, row[0], f"Action: webhook delivery failed ({reason})")


def _history(conn, item, rule, action, result):
    text = f"Trigger: {item['event']}. Action: {action}. Rule {rule['id']}: {result}"
    H._task_event(conn, item["task"], H.KEEPER, "rule", None, rule["id"], text)
    H.event(conn, H.KEEPER, "rule.task", item["task"],
            {"rule": rule["id"], "trigger": item["event"], "action": action, "result": result})


def judge(conn, item):
    """Never let an Action failure undo its triggering change. Each row has its own savepoint."""
    from . import rules as R
    try:
        if not H._has_table(conn, "rules"):
            return
        row = item["row"]
        if not row:
            return
        ctx = R._Context(conn, item["actor"], row.get("owner_id") or "", {}, None, row)
        rows = conn.execute("SELECT r.* FROM rules r LEFT JOIN rule_task_state s ON s.rule_id=r.id "
                            "WHERE r.enabled=1 AND r.event=? AND s.paused_at IS NULL", (item["event"],)).fetchall()
        from . import redesign_migration
        paused = {r["rule_id"] for r in conn.execute("SELECT rule_id FROM rule_task_state WHERE paused_at IS NOT NULL")}
        rows += [r for r in redesign_migration.wake_rows(conn, item["event"])
                 if r["event"] == item["event"] and r["id"] not in paused]
        rows = sorted(rows, key=lambda r: (R.PRECEDENCE.index(r["scope_kind"]), r["position"], r["created_at"], r["id"]))
        for rule in rows:
            if not ctx.in_scope(rule) or not ctx.matches(H._json(rule["condition"], {})):
                continue
            params = H._json(rule["params"], {}) or {}
            action = params.get("action")
            if not conn.execute("INSERT OR IGNORE INTO rule_task_runs(rule_id,task_id,event,revision,ran_at) VALUES(?,?,?,?,?)",
                                (rule["id"], item["task"], item["event"], item["revision"], H.now())).rowcount:
                continue
            if item["chain"]:
                _history(conn, item, rule, action, "Skipped: an Action already caused this change; maximum chain depth is one")
                continue
            count = conn.execute("SELECT count(*) FROM rule_task_runs WHERE rule_id=? AND fired=1 AND ran_at>?",
                                 (rule["id"], H.shift(H.now(), hours=-1))).fetchone()[0]
            if count >= 50:
                conn.execute("INSERT INTO rule_task_state(rule_id,paused_at,pause_reason) VALUES(?,?,?) "
                             "ON CONFLICT(rule_id) DO UPDATE SET paused_at=excluded.paused_at,pause_reason=excluded.pause_reason",
                             (rule["id"], H.now(), "Paused: too many runs"))
                _history(conn, item, rule, action, "Paused: too many runs")
                continue
            conn.execute("UPDATE rule_task_runs SET fired=1 WHERE rule_id=? AND task_id=? AND event=? AND revision=?",
                         (rule["id"], item["task"], item["event"], item["revision"]))
            _run(conn, item, rule, params)
    except Exception:
        # Malformed legacy rows or unavailable automation storage never reject a task write.
        log.warning("Task Trigger evaluation failed", exc_info=True)


def _run(conn, item, rule, params):
    from . import rules as R, templating
    by, row, action = rule["action_by"], item["row"], params.get("action")
    queue = _PENDING.get()
    start = len(queue["items"]) if queue else 0
    token = _CHAIN.set((rule["id"],))
    conn.execute("SAVEPOINT task_trigger_action")
    try:
        current = H.task(conn, row["id"])
        if not by or not H.task_private_readable(conn, by, row) or not H.task_private_readable(conn, by, current):
            raise ValueError("writer cannot read task")
        row = {**row, "private": int(H.task_private(conn, row) or H.task_private(conn, current))}
        if action not in ACTIONS or rule["then_outcome"] != R.ACTION:
            raise ValueError("unsupported Action")
        values = {"event": item["event"], "actor": item["actor"], "task": {
            k: row.get(k) for k in ("id", "title", "body", "note", "status", "owner", "requester", "type_id", "step_id")}}
        if action in R.EVENT_RUNNERS:
            revision = f"task-rule:{rule['id']}:{item['event']}:{item['revision']}"
            result = R.EVENT_RUNNERS[action](conn, rule["id"], by, params, values, event=item["event"],
                                            subject=row["id"], revision=revision, task=row)
            if not result["ok"]:
                raise ValueError("event Action failed")
            if action == "send_webhook":
                conn.execute("UPDATE rule_task_runs SET webhook_event_id=? "
                             "WHERE rule_id=? AND task_id=? AND event=? AND revision=?",
                             (f"{params['type']}:{row['id']}:{revision}", rule["id"], row["id"], item["event"], item["revision"]))
            detail = "Completed: " + result["detail"]
        else:
            rendered = templating.render_json(params, values)
            # Any referenced task must still be readable by the Action's author.
            for key in ("task", "blocked_by"):
                if rendered.get(key) and not H.task_private_readable(conn, by, H.task(conn, rendered[key]) or {}):
                    raise ValueError("writer cannot read related task")
            text = str(rendered.get("note") or rendered.get("text") or "")[:1000]
            said = f"Trigger: {item['event']}. Action: {action}. Rule {rule['id']}: {text}"
            R.TASK_RUNNERS[action](conn, row["id"], rendered, said)
            detail = "Completed"
        conn.execute("RELEASE task_trigger_action")
    except Exception as exc:
        conn.execute("ROLLBACK TO task_trigger_action")
        conn.execute("RELEASE task_trigger_action")
        if queue:
            del queue["items"][start:]
        # Rule rows are shared settings; never persist task content or an exception's
        # arbitrary text there. The source task retains the rule's history.
        detail = f"Action failed ({type(exc).__name__})"
        error(conn, rule["id"], detail)
    finally:
        _CHAIN.reset(token)
    _history(conn, item, rule, action, detail)
