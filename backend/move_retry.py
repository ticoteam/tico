"""A review move the gate refused is retried by Tico once what refused it changes (Dev 534 C).

The review gate (backend/review_gate.py) refuses a move to Review 1 or Review 2 while CI fails or cannot be read, or
while no passing receipt names the current head. Each of those clears on its own: the checks finish, a receipt is
posted, GitHub comes back. So the refusal is remembered on the ticket (task_events field `move_retry`, with the step it
was going to and a fingerprint of what the gate read), and this pass retries the same move as Tico when that
fingerprint changes: a PR link's head or checks, the ticket's note, or a new message on the ticket. An unreadable
GitHub is retried on a timer instead, and if it is still unreadable it goes to DevOps (backend/blockers.py). Nobody
has to ask for the retry, and Head of Engineering hears nothing about it.

A retry stops when the ticket leaves the step it was refused at, closes, or RETRY_FOR after the first refusal: each
retry that is refused again carries that first time forward (`since`), so a long outage still ends."""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import access as Access, hubdb as H

log = logging.getLogger(__name__)

FIELD = "move_retry"
RETRY_FOR = timedelta(days=3)
EVERY_S = 60                    # how often the pass looks
OUTAGE_EVERY = timedelta(minutes=5)
PER_PASS = 20
CLEARS = {"ci": "the checks on the current head pass",
          "receipt": "a passing test receipt or green checks name the current head",
          "outage": "GitHub can be read again"}


# What the gate's GitHub read reports about why it failed (backend/review_gate.py), kept with the remembered move.
CAUSE = ("problem_code", "temporary", "devops", "upstream_status")


def outage(text):
    return "cannot verify the current head and CI" in str(text or "")


def route_now(notice):
    """A GitHub read that failed for a reason only access can fix (permission, key, installation, repository
    access) goes to DevOps on the first refusal."""
    return bool(notice.get("devops"))


def route_twice(notice):
    """Any other failure that is not temporary (timeout, 5xx, 429), including one GitHub gave no cause for, goes to
    DevOps when it is still unreadable OUTAGE_EVERY later. A temporary one never does: the timer retries it."""
    return not notice.get("temporary") and not route_now(notice)


def _route(c, notice, row, twice=False):
    from . import blockers
    code = notice.get("problem_code")
    where = str(notice.get("pr") or "github")
    why = (f"Tico could not read the pull request head and checks at a review move, twice, "
           f"{int(OUTAGE_EVERY.total_seconds() // 60)} minutes apart" if twice else
           f"GitHub refused Tico's read of the pull request head and checks at a review move ({code})")
    blockers.route(c, "github_read:" + (f"{code}:" if code else "") + where,
                   "GitHub cannot be read for review moves", f"{why}: {notice.get('text')}", about=row)


def hint(kind, text=""):
    """What a refused review move adds: what clears it, and that Tico retries it then."""
    what = CLEARS["outage" if outage(text) else kind] if (kind in CLEARS or outage(text)) else None
    if not what:
        return ""
    return (f" Tico retries this move itself when {what}; nobody needs to ask for a retry or file a task to "
            "restore the review entry.")


def fingerprint(c, row):
    """What the gate reads that a later change can clear: each linked PR's head and checks, the note, the ticket's
    newest message from anyone but Tico."""
    from . import review_gate
    links = sorted((p["id"], p.get("checks") or "", str((H._json(p.get("detail_json"), {}) or {}).get("head_sha") or ""),
                    json.dumps((H._json(p.get("detail_json"), {}) or {}).get("checks") or {}, sort_keys=True))
                   for p in review_gate.links(c, row))
    newest = c.execute("SELECT max(rowid) FROM messages WHERE conversation_id=? AND from_actor<>? AND deleted_at IS NULL",
                       (row.get("conversation_id") or "", H.KEEPER)).fetchone()[0]
    raw = json.dumps([links, row.get("note") or "", newest], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def remember(c, actor, notice):
    """After a refused review move rolls back: record what would clear it, so the pass can retry it."""
    row = H.task(c, notice.get("task"))
    step = notice.get("step")
    if not row or not step or row["status"] in ("done", "closed", "declined"):
        return
    detail = {"step": step, "kind": notice.get("kind"), "head": notice.get("head"), "by": str(notice.get("by") or actor),
              "outage": outage(notice.get("text")), "seen": fingerprint(c, row), "at": H.now(),
              "since": notice.get("since") or H.now(),
              "tries": int(notice.get("tries") or 0), "state": "waiting",
              **{key: notice[key] for key in CAUSE if key in notice}}
    if notice.get("problem_code"):
        detail["outage"] = True
    H._task_event(c, row["id"], H.KEEPER, FIELD, row.get("step_id"), json.dumps(detail, sort_keys=True),
                  "Tico retries this move when " + CLEARS["outage" if detail["outage"] else detail["kind"]]
                  if detail["kind"] in CLEARS else "")
    if detail["outage"] and route_now(notice) and not detail["tries"]:
        _route(c, notice, row)          # once per refused move; Tico's own retries reuse that ask


def _latest(c, task_id):
    return c.execute("SELECT rowid, * FROM task_events WHERE task_id=? AND field IN ('step', ?) "
                     "ORDER BY ts DESC, rowid DESC LIMIT 1", (task_id, FIELD)).fetchone()


def pending(c, at=None):
    """[(task row, retry detail)] whose remembered move may now pass: still at the step it was refused at, still
    open, first refused less than RETRY_FOR ago, and with what the gate read changed (an outage: OUTAGE_EVERY since
    the last try)."""
    at = at or datetime.now(timezone.utc)
    since = (at - RETRY_FOR).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    out = []
    for (task_id,) in c.execute("SELECT DISTINCT task_id FROM task_events WHERE field=? AND ts>=?",
                                (FIELD, since)).fetchall():
        last = _latest(c, task_id)
        if not last or last["field"] != FIELD:
            continue                # the ticket moved since (the builder, a person, or a retry that passed)
        detail = H._json(last["new"], {}) or {}
        row = H.task(c, task_id)
        if (detail.get("state") != "waiting" or not row or row["status"] not in H.ACTIVE_STATUSES
                or row.get("step_id") != last["old"]):
            continue
        first = H.parse_ts(detail.get("since") or detail.get("at")) or H.parse_ts(last["ts"])
        if first and at - first >= RETRY_FOR:
            continue                # first refused RETRY_FOR ago: retries that kept failing do not extend it
        if detail.get("outage"):
            if at - H.parse_ts(detail.get("at") or last["ts"]) < OUTAGE_EVERY:
                continue
        elif fingerprint(c, row) == detail.get("seen"):
            continue
        out.append((row, detail))
    return out[:PER_PASS]


def retry(store, service, row, detail):
    """Make the remembered move as Tico. Returns True when the ticket moved."""
    from . import review_gate
    with store.read() as c:
        prs = review_gate.links(c, row)
    snapshots = review_gate.snapshot(service, prs) if prs and service else None
    with store.transaction() as c:
        now_row = H.task(c, row["id"])
        if not now_row or now_row.get("step_id") != row.get("step_id") or now_row["version"] != row["version"]:
            return False            # something else changed it while GitHub was read; the next pass looks again
        c.execute("SAVEPOINT move_retry")
        try:
            # The install owner's receipts count as they do on a person's move (backend/app.py).
            settings = getattr(service, "settings", None)
            H.task_update(c, H.KEEPER, row["id"], step=detail["step"], review_prs=snapshots,
                          review_reader=detail.get("by") or H.KEEPER,
                          receipt_owner=Access.load_owner(c, settings)["email"] if settings else None)
        except H.Refused as exc:
            c.execute("ROLLBACK TO move_retry")
            c.execute("RELEASE move_retry")
            notice = {**(getattr(exc, "review_gate", None) or {}), "task": row["id"], "step": detail["step"],
                      "tries": int(detail.get("tries") or 0) + 1, "by": detail.get("by"),
                      "since": detail.get("since") or detail.get("at")}
            notice.setdefault("kind", detail.get("kind"))
            notice.setdefault("text", exc.detail)
            if getattr(exc, "review_gate", None):
                review_gate.notify(c, H.KEEPER, notice)
            remember(c, H.KEEPER, notice)
            if outage(notice.get("text")) and detail.get("outage") and route_twice(notice):
                # Unreadable twice, OUTAGE_EVERY apart, for no temporary reason: not a blip.
                _route(c, notice, now_row, twice=True)
            return False
        c.execute("RELEASE move_retry")
        H._write_message(c, H.KEEPER, now_row["owner"],
                         f"Tico moved this ticket to {detail['step']}: what refused the move earlier "
                         f"({CLEARS['outage' if detail.get('outage') else detail.get('kind')] if detail.get('kind') in CLEARS else 'the gate'}) "
                         "has cleared.",
                         H.conversation(c, now_row["conversation_id"]), "say",
                         {"task": row["id"], "comment": True, "quiet": True, "move_retry": True}, None, None)
        return True


_lock = threading.Lock()
_next = {"at": 0.0}


def tick(service, store=None, at=None, force=False):
    """One pass, at most every EVERY_S (backend/app.py runs it with the GitHub sync)."""
    store = store or service.store
    with _lock:
        if not force and time.monotonic() < _next["at"]:
            return []
        _next["at"] = time.monotonic() + EVERY_S
    with store.read() as c:
        due = pending(c, at)
    moved = []
    for row, detail in due:
        try:
            if retry(store, service, row, detail):
                moved.append(row["id"])
        except Exception:
            # One ticket's failure must not hold back the others; it is due again on the next pass.
            log.exception("move retry failed for %s", row["id"])
    return moved
