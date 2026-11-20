"""Transactions over the existing Hub domain layer."""

import hashlib
import heapq
import logging
import json
import os
import sqlite3
import threading
import time
import weakref
from collections import OrderedDict, deque
from contextlib import ExitStack, contextmanager
from typing import cast

import orjson
import yaml

from . import bot_settings as BS
from . import hubdb as H
from . import message_links
from . import people as P
from . import sources as SRC

# A message to a bot queues its job in Python, in the writer's transaction: backend/enqueue.py.


def refused(c, identity, exc, savepoint="domain_write"):
    """Undo a refused domain write, keep its audit (and rule 8's count), and say what answers it. `savepoint` is the
    write undone: the whole request's, or one task's of a bulk change (app.py task_bulk), which goes on to the next."""
    c.execute("ROLLBACK TO " + savepoint)
    c.execute("RELEASE " + savepoint)
    from . import strategy_acts

    if isinstance(exc, strategy_acts.Proposed):
        # Not a mistake to count toward quarantine: what stays the owner's becomes their proposal instead.
        strategy_acts.settle(c, exc)
        return Problem(exc.rule, exc.detail, 409)
    # Preserve refusal auditing, but never a partial domain operation.
    token = H.PRIVATE_WRITE.set(getattr(exc, 'private', False))
    try:
        H.refuse(
            c,
            identity.actor,
            exc.rule,
            exc.detail,
            exc.severity,
            task=getattr(exc, 'task', None),
            scope=getattr(exc, 'scope', None),
            context=getattr(exc, 'context', None),
            stored=getattr(exc, 'stored', None),
        )
    except H.Refused:
        pass
    finally:
        H.PRIVATE_WRITE.reset(token)
    return refusal_problem(exc)


def refusal_problem(exc):
    """The Problem a refusal answers with, for a write that does not go through `mutate` (it has no savepoint to undo
    and keeps no audit of its own): a refusal is always a clean 4xx, never an uncaught error."""
    # The engine's refusals (a rule that starts with `task_`) are 409: the task is not where the caller thought, or
    # the move is not allowed now. What is missing, the commands allowed next and the task's version ride in `extra`
    # (docs/dev/pipelines-v3-cutover.md, "HTTP").
    if str(exc.rule).startswith("task_"):
        status = 409
    elif exc.rule == "not-found":
        status = 404
    elif exc.rule in ("identity", "label", "escape", "quarantined", "close"):
        status = 403
    else:
        status = 422
    return Problem(
        exc.rule, exc.detail, status, retryable=getattr(exc, "retryable", False), extra=getattr(exc, "extra", None)
    )


def replayed_refusal(c, principal, result):
    """A replayed receipt. One of a refusal that kept less than its caller was told (a private task's words, the name
    of a private sub-task) says what is missing and the commands allowed next again, from the task as it is now, when
    `principal` may read it (task_engine.refusal_extra). Anything else is the receipt as kept."""
    refusal = result.get("_refusal") if isinstance(result, dict) else None
    again = refusal.get("again") if isinstance(refusal, dict) else None
    if not again or not isinstance(refusal, dict):
        return result
    from . import task_engine

    extra = task_engine.refusal_extra(c, principal, again)
    if extra is None:
        return result
    return {**result, "_refusal": {**refusal, "extra": {**(refusal.get("extra") or {}), **extra}}}


# A client retries a failed write with the same Idempotency-Key within seconds, so two
# days of stored responses is generous. Without a bound the table kept every mutating
# request's response forever: most of the database file. Operation bindings share the table
# (binding is not NULL) and are never swept: an ambiguous external call is never repeated.
IDEMPOTENCY_RETENTION_HOURS = 48


# Polls carry nothing a retry could double: a replayed heartbeat or lease renewal just
# answers again, and an empty claim handed out no lease. They arrive every 15 s per runner
# (the heartbeat reply carries every assignment), and grew to most of the database file. A claim that leased a job stays replayable so a lost reply cannot
# strand the lease.
# A running turn asks for steering input every few seconds, and almost every answer is empty; an answer that
# took input stays replayable. Output events replay by their own sequence numbers (execution.events), so a
# stored receipt for each batch only grew the table: together they were most of its writes.
def is_poll(operation, result):
    if operation in ("/api/v2/runners/heartbeat", "/api/v2/runners/performance"):
        return True
    if operation == "/api/v2/jobs/claim":
        return isinstance(result, dict) and result.get("attempt") is None
    if not operation.startswith("/api/v2/attempts/"):
        return False
    if operation.endswith("/inputs"):
        return isinstance(result, dict) and not result.get("messages")
    if operation.endswith("/complete"):
        from .call_sessions import CLOSED_COMPLETION

        # A call's closed lease has a content-free, repeatable acknowledgement.
        return result == CLOSED_COMPLETION
    return operation.endswith(("/renew", "/events"))


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Health rows nothing writes any more. The old snapshot backup timers (backend/operations.py) are
# replaced by Litestream wherever the container entrypoint sets TICO_BACKUP_MODE, and Health reads
# that from backend/replication.py; `processing:<runner>` has no writer at all.
SNAPSHOT_BACKUP_ROWS = ("backup", "backup-daily", "backup-monthly", "restore-check")


def retire_health_rows(c):
    """Delete health rows from retired mechanisms so they are neither shown nor alerted on. Idempotent."""
    c.execute("DELETE FROM service_health WHERE service LIKE 'processing:%'")
    if os.environ.get("TICO_BACKUP_MODE"):
        c.execute(
            f"DELETE FROM service_health WHERE service IN ({','.join('?' * len(SNAPSHOT_BACKUP_ROWS))})",
            SNAPSHOT_BACKUP_ROWS,
        )


# Rows the idempotency, delta and token sweeps delete per write transaction, as EVENT_SWEEP_BATCH (below) does for
# `events`: 5000 held the one writer for seconds on TIDY, and every request queued behind it. Each sweep still counts
# and caps in its batches of 5000 (`batch`), so a pass deletes the same rows and leaves its mark in the same place, in
# ten times as many short writes (`_delete_in_writes`).
SWEEP_BATCH = 500


def _delete_in_writes(store, sql, args, batch, write=None):
    """`sql` (a DELETE ending in `LIMIT ?`, `args` without it) for up to `batch` rows, as one write of `batch` rows did,
    in writes of at most `write` rows; returns how many it deleted. A short write ends it: nothing more matched."""
    write = write or SWEEP_BATCH
    removed = 0
    while removed < batch:
        step = min(write, batch - removed)
        with store.transaction() as c:
            took = c.execute(sql, (*args, step)).rowcount
        removed += took
        if took < step:
            break
    return removed


def sweep_idempotency(store, now=None, batch=5000):
    """Delete idempotency rows past the retry window in short transactions; returns the count.

    Each batch is its own write transaction so the API's writers only ever wait for a
    few hundred deletions, never for the whole backlog.
    """
    cutoff = H.shift(now or H.now(), hours=-IDEMPOTENCY_RETENTION_HOURS)
    deleted = 0
    while True:
        removed = _delete_in_writes(
            store,
            "DELETE FROM idempotency WHERE rowid IN "
            "(SELECT rowid FROM idempotency WHERE binding IS NULL AND created_at<? LIMIT ?)",
            (cutoff,),
            batch,
        )
        deleted += removed
        if removed < batch:
            return deleted


DELTA_RETENTION_DAYS = 1
DELTA_SWEEP_MAX = 200_000  # rows one hourly pass deletes at most, so a first backlog drains over hours


def sweep_deltas(store, now=None, batch=5000, most=DELTA_SWEEP_MAX):
    """Delete the streamed text pieces (`attempt_events` kind 'delta', not a model's thought) of runs finished more
    than DELTA_RETENTION_DAYS ago, in short transactions like `sweep_idempotency`; returns the count.

    A run's pieces go only when its reply is whole elsewhere: `attempts.final_text` or a final `message` event holds
    it. A run still held for review (a job 'uncertain') keeps them: review_jobs shows its output, and
    auto_reconcile reads the deltas (hop_failed, empty_fallback_interruption) to decide. The pieces only typed the
    live reply (turns.reply_parts, read while a run is live). Thought deltas stay: a turn's steps show them (turns.py).
    Attempts are walked in creation order from a mark kept in registry_metadata, so a pass never rescans the table;
    one created before the cutoff and still unfinished, or not whole, is passed over for good (its pieces stay)."""
    at = now or H.now()
    cutoff = H.shift(at, days=-DELTA_RETENTION_DAYS)
    created_before = H.shift(at, days=-DELTA_RETENTION_DAYS - 1)  # a run finishing a day after it began is late
    with store.read() as c:
        mark = c.execute("SELECT value FROM registry_metadata WHERE key='delta-sweep-mark'").fetchone()
    mark = json.loads(mark[0]) if mark else ["", ""]
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(
                "SELECT a.id,a.created_at,a.finished_at,"
                "((a.final_text IS NOT NULL AND a.final_text<>'') OR EXISTS(SELECT 1 FROM attempt_events m "
                "WHERE m.attempt_id=a.id AND m.kind='message' AND json_valid(m.payload) "
                "AND json_extract(m.payload,'$.final')=1)) "
                "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.attempt_id=a.id AND j.state='uncertain') AS whole "
                "FROM attempts a WHERE a.created_at<? AND (a.created_at,a.id)>(?,?) ORDER BY a.created_at,a.id LIMIT 200",
                (created_before, *mark),
            ).fetchall()
        done = [r["id"] for r in rows if r["whole"] and r["finished_at"] and r["finished_at"] < cutoff]
        while done and deleted < most:
            removed = _delete_in_writes(
                store,
                "DELETE FROM attempt_events WHERE id IN (SELECT id FROM attempt_events WHERE attempt_id IN (%s) "
                "AND kind='delta' AND CASE WHEN json_valid(payload) THEN "
                "json_extract(payload,'$.delta_kind') END IS NOT 'thought' LIMIT ?)" % ",".join("?" * len(done)),
                tuple(done),
                batch,
            )
            deleted += removed
            if removed < batch:
                break
        else:
            if done:
                break  # the cap was reached inside this group: it is taken up again next pass
        if not rows:
            break
        mark = [rows[-1]["created_at"], rows[-1]["id"]]
        with store.transaction() as c:
            c.execute(
                "INSERT INTO registry_metadata VALUES('delta-sweep-mark',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(mark),),
            )
        if len(rows) < 200:
            break
    return deleted


TOKEN_RETENTION_DAYS = 7


def sweep_tokens(store, now=None, batch=5000, most=DELTA_SWEEP_MAX):
    """Delete the `attempt_events` kind 'tokens' rows of runs finished more than TOKEN_RETENTION_DAYS ago, in short
    transactions like `sweep_deltas`; returns the count.

    Money and usage never come from these rows: a run's totals are its attempts row (input_tokens, cached_tokens,
    output_tokens, est_cost_usd, written by turn_finish), usage_facts and attempts.result. The rows go only
    once that run is settled with its totals written, and no job of the run is 'uncertain': auto_reconcile's hop_failed
    and review_jobs read them there. Attempts are walked in creation order from the mark 'tokens-sweep-mark', as
    sweep_deltas does; one still unfinished when the walk passes it keeps its rows."""
    at = now or H.now()
    cutoff = H.shift(at, days=-TOKEN_RETENTION_DAYS)
    created_before = H.shift(at, days=-TOKEN_RETENTION_DAYS - 1)  # a run finishing a day after it began is late
    with store.read() as c:
        mark = c.execute("SELECT value FROM registry_metadata WHERE key='tokens-sweep-mark'").fetchone()
    mark = json.loads(mark[0]) if mark else ["", ""]
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(
                "SELECT a.id,a.created_at,a.finished_at,(a.settled_at IS NOT NULL "
                "AND (a.input_tokens IS NOT NULL OR a.usage_reason IS NOT NULL)) "
                "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.attempt_id=a.id AND j.state='uncertain') AS recorded "
                "FROM attempts a WHERE a.created_at<? AND (a.created_at,a.id)>(?,?) ORDER BY a.created_at,a.id LIMIT 200",
                (created_before, *mark),
            ).fetchall()
        if not rows:
            break
        done = [r["id"] for r in rows if r["recorded"] and r["finished_at"] and r["finished_at"] < cutoff]
        while done:
            removed = _delete_in_writes(
                store,
                "DELETE FROM attempt_events WHERE id IN (SELECT id FROM attempt_events "
                "WHERE attempt_id IN (%s) AND kind='tokens' LIMIT ?)" % ",".join("?" * len(done)),
                tuple(done),
                batch,
            )
            deleted += removed
            if removed < batch:
                break
        mark = [rows[-1]["created_at"], rows[-1]["id"]]
        with store.transaction() as c:
            c.execute(
                "INSERT INTO registry_metadata VALUES('tokens-sweep-mark',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(mark),),
            )
        if len(rows) < 200:
            break
    return deleted


# The `events` log keeps every action except these bookkeeping ones, which the hourly sweep drops once they are older
# than their tier's days. Not swept, because a reader needs old rows of them: `attempt.input.moved` (the privacy
# and provenance of an old run: live_privacy, read_deps, provenance_clock), `attempt.limited` (login_holds names the
# runtime of an old limited run that kept no limit_source), `judge.call` (a budget counts its day, decide_readout reads
# 31 days, and the row repeats the cost), `mcp.call` (tool-call stats up to 90 days, and the weekly SQL view),
# `github.token` and `inbound.read` (security and read audits, like `mail.read`). `credential.revealed` and
# `credential.sent_to_computer` are not swept: they are the credential's audit trail, and their repeats are written less
# often (credentials.record_use). Old rows of them stay; credential_uses keeps each last use, so nothing reads them for it.
# Seven days: writers only, or readers that look back no further than a week (bot.drain: deploy_draining reads the
# last hour, release_deploy_drains a week at most).
EVENT_NOISE_ACTIONS = (
    "sql.query",
    "wake.skipped",
    "message.delivered",
    "message.read",
    "status.set",
    "turn.start",
    "turn.finish",
    "github.checks_read",
    "rate_limit.set",
    "attempt.fallback",
    "attempt.lease-held",
    "job.suppress",
    "bot.drain",
    "operator.deployment-undrain",
    "bot.repository_read",
)
EVENT_NOISE_RETENTION_DAYS = 7
# Thirty days: `message.notice` as before, and `github.pull_request`. Its own mark, since the old one had walked past
# every github.pull_request row already on disk.
EVENT_SWEEP_ACTIONS = ("message.notice", "github.pull_request")
EVENT_RETENTION_DAYS = 30
# Rows per write transaction. A delete costs about 2 ms a row on TIDY (six indexes on events, cold pages), so 5000
# held the one writer for 6-10 s and every request queued behind it; 500 holds it for about one second.
EVENT_SWEEP_BATCH = 500
EVENT_SWEEP_MAX = 20000  # rows one hourly pass deletes at most; a larger backlog drains over later passes


def sweep_events(store, now=None, batch=EVENT_SWEEP_BATCH, most=EVENT_SWEEP_MAX):
    """Delete the bookkeeping rows past their tier's days (EVENT_NOISE_ACTIONS after EVENT_NOISE_RETENTION_DAYS,
    EVENT_SWEEP_ACTIONS after EVENT_RETENTION_DAYS); returns the count. The tiers share no action and keep separate
    marks, so each one's walk is its own. The noise tier goes first within the one `most` budget, so a large first
    backlog drains there and the 30-day tier follows."""
    at = now or H.now()
    deleted = 0
    for key, actions, days in (
        ("events-noise-mark", EVENT_NOISE_ACTIONS, EVENT_NOISE_RETENTION_DAYS),
        ("events-sweep-30-mark", EVENT_SWEEP_ACTIONS, EVENT_RETENTION_DAYS),
    ):
        deleted += _sweep_event_tier(store, key, actions, H.shift(at, days=-days), batch, most - deleted)
    return deleted


def _sweep_event_tier(store, key, actions, cutoff, batch, most):
    """One tier of sweep_events: the `actions` rows older than `cutoff`, walked forward on `events_ts` from the mark
    kept under `key`; returns the count.

    The scan walks the index from the mark, so a pass reads only the time since the last one (and not the kept rows
    before it again). The scan runs outside the write lock; each batch's delete is its own short transaction, like
    `sweep_idempotency`. Events are only ever written with the current time, so once a pass has reached the cutoff,
    nothing older than it is left behind."""
    if most <= 0:
        return 0
    marks = ",".join("?" * len(actions))
    with store.read() as c:
        saved = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    mark = json.loads(saved[0]) if saved else ""
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(
                f"SELECT rowid,created_at FROM events INDEXED BY events_ts WHERE created_at>=? AND created_at<? "
                f"AND action IN ({marks}) ORDER BY created_at LIMIT ?",
                (mark, cutoff, *actions, batch),
            ).fetchall()
        if not rows:
            mark = cutoff
            break
        with store.transaction() as c:
            removed = c.execute(
                f"DELETE FROM events WHERE rowid IN ({','.join('?' * len(rows))})", [row["rowid"] for row in rows]
            ).rowcount
        deleted += removed
        mark = rows[-1]["created_at"]
        if len(rows) < batch:
            mark = cutoff
            break
        if not removed:
            break  # the rows left the table under the scan: the next pass starts again from the mark
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(mark)),
        )
    return deleted


# Credential use rows written before credentials.record_use kept one per run, not one per USE_WINDOW. This one-time
# trim brings them to that grain: per (credential, actor, bot), in ts order, a row is kept when it is the pair's first
# or USE_WINDOW after the last kept one, which is the rule record_use applies from now on. A row record_use always
# writes (a reveal with no attempt, a computer send by a model key) is kept and counts as the pair's last write, as it
# did then. Person reveals are never touched. Rows at or after the first pass's time are not walked: the deploy's rows
# are already at the new grain. Passes resume from the credential after the last finished one (registry_metadata).
# Paced, not back to back: a batch of 500 deletes plus the WAL checkpoint it triggers held the writer gate for 5-7 s on
# TIDY's first pass (scheduler.py:437), so writers waited up to 9 s against the 10 s WRITE_WAIT. 200 rows per write, then
# a pause, lets writers in between batches.
TRIM_BATCH = 200  # deletes per write transaction
TRIM_PAUSE = 0.5  # seconds slept after each write transaction
TRIM_PAGE = 2000  # rows read per read transaction
TRIM_READ_PAUSE = 0.05  # seconds slept between read pages
TRIM_MAX = 50_000  # deletes one hourly pass makes at most


def _sleep(seconds):
    """The pauses go through here, so a test replaces this and not time.sleep for the whole process."""
    time.sleep(seconds)


TRIM_ACTIONS = ("credential.revealed", "credential.sent_to_computer")
TRIM_MARK = "credential-use-trim"


def _use_key(actor, detail_json):
    """(bot, windowed) of one row, as credentials.record_use keys it and decides whether it writes."""
    try:
        detail = json.loads(detail_json) if detail_json else {}
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    bot = str(detail.get("bot") or "") or (actor[4:] if actor.startswith("bot:") else "")
    windowed = detail.get("use") == "watcher" or bool(detail.get("attempt"))
    return bot, windowed


def _trim_stream(store, action, target, before, page):
    """One action's rows of one credential before `before`, oldest first, a page per read transaction. Keyset on ts
    (events_action_target: action, target, ts); ids already yielded at the page's last ts are skipped on the next."""
    last, seen = "", set()
    while True:
        limit = page + len(seen)
        with store.read() as c:
            rows = c.execute(
                "SELECT rowid,id,created_at,actor,detail FROM events WHERE action=? AND target=? "
                "AND created_at>=? AND created_at<? ORDER BY created_at LIMIT ?",
                (action, target, last, before, limit),
            ).fetchall()
        fresh = [row for row in rows if not (row["created_at"] == last and row["id"] in seen)]
        yield from fresh
        if len(rows) < limit or not fresh:
            return
        _sleep(TRIM_READ_PAUSE)
        last = rows[-1]["created_at"]
        seen = {row["id"] for row in rows if row["created_at"] == last}


def _trim_target(store, target, before, batch, budget, page):
    """Walk one credential's rows in ts order and delete the ones record_use would not have written. Returns
    (deleted, finished). When `budget` runs out first the walk stops, and the next pass walks the credential again:
    the rows it kept decide the same again, so nothing already passed is deleted twice."""
    from .credentials import USE_WINDOW

    last, doomed, deleted = {}, [], 0
    streams = [_trim_stream(store, action, target, before, page) for action in TRIM_ACTIONS]
    for row in heapq.merge(*streams, key=lambda row: (row["created_at"], row["id"])):
        actor = row["actor"] or ""
        if actor.startswith("human:"):
            continue
        bot, windowed = _use_key(actor, row["detail"])
        key = (actor, bot)
        prev = last.get(key)
        gap = None
        if prev is not None and windowed:
            at, was = H.parse_ts(row["created_at"]), H.parse_ts(prev)
            gap = (at - was).total_seconds() if at and was else None
        if not windowed or prev is None or gap is None or gap >= USE_WINDOW:
            last[key] = row["created_at"]
            continue
        doomed.append(row["rowid"])
        if len(doomed) >= min(batch, budget - deleted):
            deleted += _trim_flush(store, doomed)
            doomed = []
            if deleted >= budget:
                return deleted, False
    if doomed:
        deleted += _trim_flush(store, doomed)
    return deleted, True


def _trim_flush(store, rowids):
    with store.transaction() as c:
        deleted = c.execute(f"DELETE FROM events WHERE rowid IN ({','.join('?' * len(rowids))})", rowids).rowcount
    _sleep(TRIM_PAUSE)
    return deleted


def _trim_next_target(store, after):
    """The next credential after `after` (in id order) with a row of a trimmed action, or None."""
    found = []
    for action in TRIM_ACTIONS:
        with store.read() as c:
            row = c.execute(
                "SELECT target FROM events WHERE action=? AND target>? ORDER BY target LIMIT 1", (action, after)
            ).fetchone()
        if row:
            found.append(row[0])
    return min(found) if found else None


def _trim_save(store, mark):
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (TRIM_MARK, json.dumps(mark)),
        )


def sweep_credential_uses(store, now=None, batch=TRIM_BATCH, most=TRIM_MAX, page=TRIM_PAGE):
    """One pass of the credential-use trim (see TRIM_BATCH above): deletes at most `most` rows and returns the count.
    The mark holds the pass's `before` (fixed at the first pass), the last finished credential, and `done` once every
    credential is finished; a finished trim costs one read. Each pass is a few read transactions and one short write
    per `batch` rows, so writers wait about as long as they do for sweep_events."""
    with store.read() as c:
        saved = c.execute("SELECT value FROM registry_metadata WHERE key=?", (TRIM_MARK,)).fetchone()
    mark = json.loads(saved[0]) if saved else None
    if mark and mark.get("done"):
        return 0
    if mark is None:
        mark = {"before": now or H.now(), "after": ""}
        _trim_save(store, mark)
    before, after, deleted = mark["before"], mark["after"], 0
    while deleted < most:
        target = _trim_next_target(store, after)
        if target is None:
            _trim_save(store, {"before": before, "after": after, "done": True})
            break
        count, finished = _trim_target(store, target, before, batch, most - deleted, page)
        deleted += count
        if not finished:
            break
        after = target
        _trim_save(store, {"before": before, "after": after})
    return deleted


# Pages returned to the disk per write transaction: 2000 x 4 KB = 8 MB. The writer gate is held for one step at a time.
VACUUM_STEP_PAGES = 2000
# Free pages kept in the file for reuse, so the next writes take them without growing the file again.
VACUUM_FLOOR_PAGES = 1000
VACUUM_PASS_MAX = 256 * 1024 * 1024  # bytes one hourly pass returns at most; the rest waits for the next pass
_vacuum_hint_logged = False


def reclaim_free_pages(store, step=VACUUM_STEP_PAGES, floor=VACUUM_FLOOR_PAGES, most=VACUUM_PASS_MAX):
    """Return the free pages the sweeps leave behind to the disk (PRAGMA incremental_vacuum); returns the pages returned.

    Only a file with auto_vacuum=INCREMENTAL (hubdb.connect sets it on every new file) is touched, and any other file
    costs one read. Never a full VACUUM. Each step is its own write transaction, and the pass stops at `floor` free
    pages or `most` bytes, whichever comes first."""
    global _vacuum_hint_logged
    with store.read() as c:
        mode = c.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode != 2:
            if not _vacuum_hint_logged:
                _vacuum_hint_logged = True
                logging.getLogger(__name__).info(
                    "hub database has auto_vacuum=%s: a VACUUM would let the sweeps' freed pages go back to the disk",
                    mode,
                )
            return 0
        page = c.execute("PRAGMA page_size").fetchone()[0]
    budget = most // page
    returned = 0
    while returned < budget:
        # The freelist is read inside the write transaction, so a concurrent delete's pages are counted too.
        with store.transaction() as c:
            free = c.execute("PRAGMA freelist_count").fetchone()[0]
            n = min(step, free - floor, budget - returned)
            if n <= 0:
                break
            removed = len(c.execute(f"PRAGMA incremental_vacuum({int(n)})").fetchall())
        returned += removed
        if not removed:
            break
    return returned


# Parsed runner-readiness documents by their exact stored text (str or bytes): a runner's text changes only on its
# heartbeat, while every claim, page and check reads it again, and parsing a computer of many bots costs more than
# the rest of most of those reads. Bounded by count and by the texts' size (a parsed document holds about three times
# its text); least recently used goes first, and a text larger than the whole budget is parsed but not kept.
_READINESS_PARSED: "OrderedDict[str | bytes, dict]" = OrderedDict()
_READINESS_LOCK = threading.Lock()
_READINESS_MOST = 128
_READINESS_MOST_CHARS = 16 * 1024 * 1024
_readiness_chars = 0


def _readiness_copy(document):
    """The caller's own copy of a memoized document: its top level, each section (`bots`, `runtimes`, `harnesses`,
    ...) and each row of a section. Callers set, pop and delete at those levels (execution.forget_report and
    mark_rejected, views.computer_details, subscriptions.without_accounts); nothing changes a row's own lists or
    nested values in place, so those stay shared."""
    out = {}
    for key, section in document.items():
        if isinstance(section, dict):
            section = {
                name: dict(row) if isinstance(row, dict) else list(row) if isinstance(row, list) else row
                for name, row in section.items()
            }
        elif isinstance(section, list):
            section = [
                dict(row) if isinstance(row, dict) else list(row) if isinstance(row, list) else row for row in section
            ]
        out[key] = section
    return out


def _readiness_shape(value):
    value = value if isinstance(value, dict) else {}
    if value.get("schema_version") in (0, 1) and isinstance(value.get("bots"), dict):
        return value
    return {
        "schema_version": 0,
        "runtimes": {},
        "bots": {
            str(bot): {
                "ready": ready is True,
                "runtime": "",
                "model": "",
                "repository_present": ready is True,
                "repository_revision": "",
                "configuration_valid": ready is True,
                "problems": [],
            }
            for bot, ready in value.items()
            if isinstance(ready, bool)
        },
    }


def readiness_document(value):
    """Return the canonical runner-readiness shape, including legacy heartbeats. Stored text is parsed once per
    distinct text (_READINESS_PARSED); each call still gets a document it may change (_readiness_copy)."""
    global _readiness_chars
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, (str, bytes)):
        return _readiness_shape(value)
    with _READINESS_LOCK:
        document = _READINESS_PARSED.get(value)
        if document is not None:
            _READINESS_PARSED.move_to_end(value)
    if document is None:
        try:
            parsed = orjson.loads(value)
        except (TypeError, ValueError):
            parsed = {}
        document = _readiness_shape(parsed)
        with _READINESS_LOCK:
            if len(value) <= _READINESS_MOST_CHARS and value not in _READINESS_PARSED:
                _READINESS_PARSED[value] = document
                _readiness_chars += len(value)
                while _READINESS_PARSED and (
                    len(_READINESS_PARSED) > _READINESS_MOST or _readiness_chars > _READINESS_MOST_CHARS
                ):
                    _readiness_chars -= len(_READINESS_PARSED.popitem(last=False)[0])
    return _readiness_copy(document)


def bot_readiness(value, bot):
    row = readiness_document(value).get("bots", {}).get(bot)
    return row if isinstance(row, dict) else {"ready": False, "problems": ["No readiness report from this computer"]}


def repo_url(repo, github_owner=""):
    """The browsable Git address of a stored bot repository: a URL as given, `owner/name`
    under github.com, or a bare name under the environment's GitHub owner."""
    repo = str(repo or "").strip()
    if repo.startswith("http://") or repo.startswith("https://"):
        return repo
    if "/" in repo:
        return "https://github.com/" + repo
    if repo and github_owner:
        return "https://github.com/" + github_owner + "/" + repo
    return ""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def message_page(c, cid, *, before=None, since=None, limit=200):
    """Newest page, in display order; rowid breaks ties for messages in the same millisecond. A
    deleted task comment is left out: this page is also what a bot's run is handed. `cid` may be
    a list of conversations (a task's history across a merged room and its Home)."""
    cids = list(cid) if isinstance(cid, (list, tuple)) else [cid]
    where = "conversation_id IN (%s)" % ",".join("?" * len(cids))
    clauses, args = [where, "deleted_at IS NULL"], list(cids)
    if before:
        anchor = c.execute("SELECT rowid FROM messages WHERE id=? AND " + where, (before, *cids)).fetchone()
        if not anchor:
            raise Problem("cursor", "Message cursor does not belong to this conversation", 422)
        clauses.append("rowid<?")
        args.append(anchor[0])
    if since:
        clauses.append("created_at>?")
        args.append(since)
    rows = c.execute(
        "SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY rowid DESC LIMIT ?", (*args, limit + 1)
    ).fetchall()
    more = len(rows) > limit
    messages = [{**dict(r), "refs": json.loads(r["refs"] or "{}")} for r in reversed(rows[:limit])]
    return {"messages": messages, "has_more": more, "next_before": messages[0]["id"] if more and messages else None}


def task_message_page(c, task, **kwargs):
    """Messages about this task. A dedicated task thread returns the whole conversation;
    a shared chat room returns only rows tagged with this task id."""
    from . import bot_home_merge

    # A person's old chat with the bot moved into its Home (backend/bot_home_merge.py): the task's messages are in the
    # room, its recorded Home and the Home it is in now. The last one is the room the task's messages belong to.
    cids = bot_home_merge.history_ids(c, task["conversation_id"])
    page = message_page(c, cids, **kwargs)
    conv = H.conversation(c, cids[-1])
    if conv and conv.get("task_id") == task["id"]:
        return page
    page["messages"] = [m for m in page["messages"] if H.message_task_id(m) == task["id"]]
    return page


def seed_operator(slug, team, roster, owner):
    """Who runs a bot's machines on a first import: the person who claims that bot or its team
    in people.yaml, else the environment owner. A company-wide `*` claim is not a team claim."""
    wanted = {x for x in (slug, team) if x}
    claimed = [p for p in roster["people"] if wanted & set(p["primary_for"])]
    return claimed[0]["id"] if claimed else owner


class Problem(Exception):
    """A refusal the caller can act on. `extra` carries the one fact the page needs to offer the
    next move - the card a duplicate feature request already has, for instance - and is serialized
    beside the code and the detail. Nothing secret goes in it."""

    def __init__(self, code, detail, status=400, retryable=False, extra=None):
        super().__init__(detail)
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable
        self.extra = extra or {}


POOL_IDLE = 16  # read connections kept open between reads


def _pooled(base):
    """`base` for pooled reads: one a caller changed (a function, an authorizer, a progress handler, a pragma that
    lasts) is closed after its read instead of reused, so no state reaches another caller's read."""

    def taint(name):
        method = getattr(base, name)

        def changed(self, *args, **kwargs):
            self.tainted = True
            if hasattr(self, "read_cache"):
                self.read_cache.clear()
            return method(self, *args, **kwargs)

        return changed

    names = (
        "create_function",
        "create_aggregate",
        "create_window_function",
        "create_collation",
        "set_authorizer",
        "set_progress_handler",
        "set_trace_callback",
        "enable_load_extension",
        "setlimit",
        "executescript",
    )
    body = {name: taint(name) for name in names if hasattr(base, name)}

    def clear(self):
        if hasattr(self, "read_cache"):
            self.read_cache.clear()

    def execute(self, statement, *args, **kwargs):
        # Explicit transaction control starts a new snapshot even without row changes (`ROLLBACK;` too).
        if (statement.lstrip().split(None, 1) or [""])[0].rstrip(";").upper() in (
            "BEGIN",
            "COMMIT",
            "END",
            "ROLLBACK",
            "SAVEPOINT",
            "RELEASE",
            "CREATE",
            "ALTER",
            "DROP",
        ):
            clear(self)
        return base.execute(self, statement, *args, **kwargs)

    def finish(name):
        def completed(self, *args, **kwargs):
            clear(self)
            return getattr(base, name)(self, *args, **kwargs)

        return completed

    body.update(execute=execute, commit=finish("commit"), rollback=finish("rollback"))
    body["tainted"] = False
    body["pool_file"] = None
    body["tables_seen"] = {}  # {pool_file: tables seen to exist}, shared by this store's connections (hubdb._has_table)
    return type("Pooled" + base.__name__, (base,), body)


# Seconds a writer waits for the gate, then for SQLite's lock (busy_timeout, other processes). Together they bound a
# request's wait for the lock: past that it answers 503 `busy` with Retry-After (app.py storage_handler) instead of
# holding one of the 40 request threads for a minute while client retries queue behind it.
WRITE_WAIT = 10


class WriterGate:
    """FIFO admission: a scheduler opening its next transaction goes behind already waiting requests."""

    def __init__(self, timeout=WRITE_WAIT):
        self.lock = threading.Lock()
        self.queue = deque()
        self.timeout = timeout

    def acquire(self):
        ready = threading.Event()
        with self.lock:
            self.queue.append(ready)
            if len(self.queue) == 1:
                ready.set()
        try:
            if not ready.wait(self.timeout):
                raise sqlite3.OperationalError("database is locked")
        except BaseException:
            with self.lock:
                first = self.queue[0] is ready
                self.queue.remove(ready)
                if first and self.queue:
                    self.queue[0].set()
            raise

    def release(self):
        with self.lock:
            self.queue.popleft()
            if self.queue:
                self.queue[0].set()


def _close_idle(conns):
    """Closes a store's idle read connections once the store is freed (weakref.finalize in Store.__init__). An open
    sqlite connection that the garbage collector frees warns, so the store closes its own first."""
    while conns:
        try:
            conns.pop().close()
        except sqlite3.Error:
            pass


# Secrets an answer shows once: never kept with the idempotency record, so a replay answers without them.
SHOWN_ONCE = ("requester_token", "push_addresses", "push_address", "signing_secret")


class Store:
    def __init__(self, settings, recorder=None):
        self.settings = settings
        # Whether this start made the database file: it then has nothing from an older release, so initialize's cutover
        # sets up the built-ins and says nothing of moving them (backend/redesign_migration.py `_finish`).
        path = getattr(settings, "db_path", None)
        self.new_file = path is not None and (not path.exists() or not path.stat().st_size)
        # The server's file reader, set by the app once it has one (backend/media.py); None until then.
        self.blobs = None
        # The flight recorder (backend/flight.py) times every statement and write transaction when one is given.
        self.recorder = recorder
        self.factory = sqlite3.Connection
        if recorder is not None:
            from . import flight

            self.factory = flight.connection_class(recorder)
            flight.ACTIVE = recorder  # times write transactions on connections opened outside the Store
        # Reads reuse their connections (`read`): a new one parses the whole schema before its first statement,
        # which on a large install cost more than most reads themselves.
        self.pooled = _pooled(self.factory)
        self.pool, self.pool_file, self.pool_lock = [], None, threading.Lock()
        self.pool_age = 0  # retire_pool's count, part of the pool's identity
        self._pool_finalizer = weakref.finalize(self, _close_idle, self.pool)
        # In-process writers queue before BEGIN instead of competing in SQLite's busy-handler sleeps.
        # Reads still use independent WAL snapshots; other processes remain governed by busy_timeout.
        self.writer = WriterGate()
        # Requests in another app must neither drain this store's runs nor consume its pending wakeup.
        self.automation_bell = threading.Event()
        from .provenance_clock import Cache

        self.provenance_cache = Cache()
        self.roster_rows = {}  # {statement: (roster_clock count, rows)}, shared by this store's connections
        from .row_clock import Cache as RowCache

        self.row_cache = RowCache()

    def connect(self, factory=None, check_same_thread=True):
        path = self.settings.db_path
        if not path.exists() or path.stat().st_size == 0:
            # A new file takes the baseline before anything opens it: code that makes a table on first use must never
            # leave it a file with a few tables and no schema record, which the next start refuses as not Tico's.
            self.migrate()
        c = sqlite3.connect(
            str(self.settings.db_path),
            timeout=WRITE_WAIT,
            isolation_level=None,
            factory=factory or self.factory,
            check_same_thread=check_same_thread,
        )
        try:
            self._configure(c)
        except BaseException:
            # The caller never receives the connection, so nothing else would close it.
            c.close()
            raise
        return c

    def _configure(self, c):
        H.leave_wal_for_litestream(c)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        # A writer waits out a backup checkpoint or another process's pass, but not so long that requests pile up
        # (WRITE_WAIT). hubdb.connect keeps 30 s: it opens the file at start-up, for migrations.
        c.execute(f"PRAGMA busy_timeout={WRITE_WAIT * 1000}")
        # In WAL mode NORMAL syncs at checkpoints, not on every commit inside the writer gate, where a sync
        # waiting behind other disk writes on the host held every writer for seconds. The database cannot
        # corrupt; an OS crash or power loss can lose the last commits, as with hubdb.connect.
        c.execute(f"PRAGMA synchronous={H.synchronous('NORMAL')}")
        # The 2 MB default page cache re-read hot index pages on every read of a multi-GB file. 16 MB per connection
        # (POOL_IDLE kept, a few dozen at peak) fills lazily; the mmap is the OS page cache, shared by all of them.
        c.execute("PRAGMA cache_size=-16384")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=268435456")
        # The WAL file is cut back to this size after a checkpoint; a large write (a migration) otherwise keeps its peak.
        c.execute(f"PRAGMA journal_size_limit={H.WAL_SIZE_LIMIT}")

    @SRC.scoped("system:migration")
    def migrate(self):
        """The preflight and the pending migrations only (hubdb.connect runs them), for create_app before its installs."""
        from . import migrations as Migrations

        Migrations.preflight(self.settings.db_path)
        H.connect(self.settings.db_path).close()

    def initialize(self, *, seed_market=True):
        from . import redesign_migration  # the one-time move to Automations, at the end of this method

        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Refuses a database this code must not open, before anything writes to it (backend/migrations).
        from . import migrations as Migrations

        Migrations.preflight(self.settings.db_path)
        with H.connect(self.settings.db_path) as c:
            # hubdb.connect gave a new file the baseline schema and ran the pending migration files: every table, index
            # and trigger below is there. What follows are the startup steps on the rows.
            from . import start_places as _start_places

            _start_places.restore_waits(c)
            from . import updates as _updates
            from . import teammate_access as _teammate_access

            _teammate_access.ensure(c)  # people's and groups' See/Read/Write (docs/permissions.md)
            from . import rules as _rules

            _rules.ensure_schema(c)
            from . import docs as _docs

            _docs.ensure_schema(c, self.settings)
            from . import doc_sources as _doc_sources

            _doc_sources.ensure_schema(c)
            from . import performance as _performance

            _updates.purge_rejected(c)
            c.execute("BEGIN IMMEDIATE")
            try:
                # The memory hold, last sample time and a restart under way are runner_states columns; a computer that
                # sampled before its last-sample mark existed takes its newest kept sample's.
                _performance.backfill_last_sampled(c)
                from .agents import wipe_unclaimable

                wipe_unclaimable(c)
                # The person a Slack DM mapping was opened for, and the person a queued copy is for. A DM with no verified
                # inbound message yet has none: its copies fail closed. A backfill reads each DM's verified history once.
                for (channel,) in c.execute(
                    "SELECT DISTINCT channel FROM slack_threads WHERE channel LIKE 'D%' AND recipient_actor IS NULL"
                ).fetchall():
                    actors = {
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT actor FROM slack_events WHERE channel=? AND actor LIKE 'human:%' "
                            "AND state IN ('routed','recorded')",
                            (channel,),
                        )
                    }
                    if len(actors) == 1:
                        c.execute(
                            "UPDATE slack_threads SET recipient_actor=? WHERE channel=? AND recipient_actor IS NULL",
                            (actors.pop(), channel),
                        )
                # The provider choice goes in first: seeding the roster and the curator resolve
                # their runtime and model from it.
                from . import providers

                providers.seed_from_env(c, self.settings, H.now())
                from . import access

                access.seed(c, self.settings, H.now())
                access.retire_bot_lists(c, self.settings, H.now())
                access.raise_bot_limit(c, H.now())
                self.seed_goals(c)
                from .public_roadmaps import seed_secret as seed_roadmap_secret

                seed_roadmap_secret(c)
                # Notes a completed run carried are settled (hubdb.notes_settle): one pass settles the ones from before the
                # column, in one UPDATE. Unversioned and idempotent; it reads only the small unsettled-carried index.
                H.notes_settle_catch_up(c, None)
                # Setup commands cleared in Settings before a clear released the row (backend/repositories.py).
                # Idempotent: it matches nothing once those rows are released.
                from .repositories import release_cleared_overrides

                release_cleared_overrides(c)
                # The team's strategy: the first start makes the draft from the company profile (backend/strategy.py).
                from .strategy import ensure as ensure_strategy

                ensure_strategy(c)
                # The built-in rule "Asks to a person", once per team (org_rules.ensure_builtin).
                from .org_rules import ensure_builtin as ensure_builtin_rules

                ensure_builtin_rules(c)
                # Temporary task assignments are retired (#420): their actors are archived and off every computer,
                # their open tasks back with the role. Idempotent; the tables stay inert.
                from .shared_bots import retire_assignments

                retire_assignments(c)
                # Triggers made from lists in the code, so a start brings them in line with this release: the wake hold
                # (backend/wake_presets.py), the change log live events are read from (backend/events.py) and Finance's
                # org-chart triggers (backend/finance/store/tico.py watch_org).
                from .wake_presets import ensure as ensure_wake_presets

                ensure_wake_presets(c)
                from .events import ensure as ensure_changes

                ensure_changes(c)
                from .finance.store import tico as _finance_tico

                _finance_tico.watch_org(c)
                # Tico no longer records: a meeting left waiting on audio or transcription is
                # finished with what it has (backend/meetings.py). Idempotent, no migration number.
                from .meetings import retire_capture

                retire_capture(c)
                retire_health_rows(c)
                if seed_market and not self.settings.test_identities:
                    from . import market as Market

                    Market.ensure_curator(c)
                    Market.seed(c, document=Market.load_snapshot(self.settings.registry_dir))
                # The Directory's boot step: flags Market's company/self as self, and the kind of each organization Market
                # wrote without one (backend/entities/store.py boot). Idempotent.
                from .entities import store as Directory

                Directory.boot(c)
                # Teams, org groups and derived departments become groups, once (backend/groups.py). Idempotent,
                # so no migration number to collide with another branch's.
                from . import groups as Groups

                Groups.migrate(c, self.settings)
                # The Goal Manager becomes the Strategy Owner on every install, once (backend/strategy_bot.py).
                # Idempotent.
                from . import strategy_bot as StrategyBot

                StrategyBot.migrate(c)
                # Every bot gets a profile by its name, once; a bot that has one is left alone (backend/profiles.py).
                from . import profiles as Profiles

                Profiles.migrate(c)
                from . import redesign_migration as Redesign

                # Inboxes the cutover moved with the Inbox Manager's whole instructions go to the short routing
                # prompt, once (backend/redesign_migration.py repair_inbox_prompts). Idempotent by its marker. A
                # failure here is logged, never raised: the repair is tried again at the next start.
                try:
                    Redesign.repair_inbox_prompts(c)
                except Exception:  # startup goes on
                    logging.getLogger(__name__).exception("inbox prompt repair failed; it is tried at the next start")
                from . import releases as Releases

                Releases.record_start(c, H.now())
                from .credentials import FILE_MIGRATION, HUB_MIGRATION

                if not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (FILE_MIGRATION,)).fetchone():
                    pending = [r[0] for r in c.execute("SELECT slug FROM bots")]
                    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (FILE_MIGRATION, encode(pending)))
                    c.execute("INSERT OR IGNORE INTO registry_metadata VALUES(?,?)", (HUB_MIGRATION, encode([])))
                _docs.refresh_generated_docs(c)
                record = c.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()
                if record:
                    choices = H._json(record[0], {})
                    if (
                        isinstance(choices, dict)
                        and isinstance(choices.get("answers"), dict)
                        and "never_without_person" in choices["answers"]
                    ):
                        choices["answers"].pop("never_without_person")
                        c.execute("UPDATE registry_metadata SET value=? WHERE key='onboarding'", (encode(choices),))
                c.commit()
            except Exception:
                c.rollback()
                raise
            from . import provenance_clock, roster_clock

            provenance_clock.ensure(c)
            roster_clock.ensure(c)
            from . import row_clock

            row_clock.ensure(c)
            # Every start, in short batches of their own: rows written since the last pass, including any an
            # older server wrote after a rollback, get their task links (backend/message_links.py).
            message_links.backfill(c)
            # Last: the status and state constraints, derived from the lists (backend/status_values.py). Migration 0002
            # checked and rewrote the stored values; this installs the triggers and CHECKs on tables the module code
            # above created on a new file, and refreshes them when a list changes. Idempotent.
            from . import status_values as _status_values

            _status_values.enforce(c)
        c.close()
        # Once per install: routines and wake rules become automations (backend/redesign_migration.py). Each object
        # in its own write, so a restart part way goes on where it stopped.
        redesign_migration.cutover(self, new=self.new_file)

    def close(self):
        """Closes the store's idle read connections now, as the finalizer does once the store is freed. A connection
        still in use is closed when _give receives it, since the finalizer is no longer alive."""
        with self.pool_lock:
            self._pool_finalizer()

    @contextmanager
    def read(self):
        c = self._take()
        try:
            yield c
        finally:
            self._give(c)

    @contextmanager
    def read_transaction(self):
        """A consistent SQLite snapshot for multi-query authorization without taking a write lock."""
        with self.read() as c:
            c.execute("BEGIN")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def _file(self):
        try:
            stat = os.stat(self.settings.db_path)
        except OSError:
            return None
        return stat.st_dev, stat.st_ino

    def retire_pool(self):
        """Every connection opened before now is closed instead of reused: the idle ones at the next read, the rest when
        given back. A connection reads the planner statistics when it opens and keeps them (backend/planner_stats.py)."""
        with self.pool_lock:
            self.pool_age += 1

    def _take(self):
        file = self._file()
        with self.pool_lock:
            identity = (file, self.pool_age)
            if identity != self.pool_file:
                # A restored or replaced database file, or retire_pool: connections opened before are not reused.
                stale = list(self.pool)
                self.pool.clear()  # in place: the finalizer registered in __init__ holds this list
                self.pool_file = identity
                from .provenance_clock import Cache

                self.provenance_cache = Cache()
                self.roster_rows = {}
                from .row_clock import Cache as RowCache

                self.row_cache = RowCache()
            else:
                stale = []
            c = self.pool.pop() if self.pool else None
        for old in stale:
            old.close()
        if c is None:
            c = self.connect(factory=self.pooled, check_same_thread=False)
            c.pool_file = identity
        # The server's file reader as the app has it configured now (media.install_media): work that holds only the
        # connection reads a company page's logo through it, so one saved in the company's own places is found.
        c.blobs = self.blobs
        c.read_cache = {}
        c.provenance_cache = self.provenance_cache
        c.roster_rows = self.roster_rows
        c.row_cache = self.row_cache
        c.write_transaction = False
        c.after_commit = []
        c.automation_bell = self.automation_bell
        c.provenance_changes = c.total_changes
        c.provenance_start = None
        c.settings = self.settings  # the server's own settings, for work that holds only the connection
        return c

    def _give(self, c):
        c.read_cache.clear()
        # Only a connection left as it was handed out goes back: no open transaction, nothing per-caller installed.
        try:
            if c.in_transaction:
                c.rollback()
            if c.row_factory is not sqlite3.Row:
                c.row_factory = sqlite3.Row
        except sqlite3.Error:
            c.tainted = True
        with self.pool_lock:
            # Once the store is freed, its finalizer has closed the idle connections: one given back after that would
            # wait in a dead pool, open, until the garbage collector frees it (and warns).
            if (
                self._pool_finalizer.alive
                and not c.tainted
                and c.pool_file == self.pool_file
                and len(self.pool) < POOL_IDLE
            ):
                self.pool.append(c)
                return
        c.close()

    @contextmanager
    def transaction(self, durable=False, *, notify=False):
        """One write transaction. `durable` makes its commit reach the disk before the call returns, for a write
        that authorises an outside effect (finance's claim of a money transfer before the provider is called): the
        connection runs synchronous=FULL for this transaction and goes back to the pool at its default. SQLite refuses
        to change the setting inside a transaction, so it is set before BEGIN, from `checkout`.
        `notify` wakes open streams after background writes that produce changes. Request writes handle this in
        `write` or `mutate`, which also record the caller."""
        from . import events
        from .flight import begin

        logged = None
        # The gate is released only after commit/rollback, including message-link maintenance.
        with ExitStack() as stack:
            c = None

            def checkout():
                nonlocal c
                c = stack.enter_context(self.read())
                if durable:
                    c.execute("PRAGMA synchronous=FULL")
                    # Registered after the read, so it runs before the connection is given back, even when BEGIN fails.
                    stack.callback(c.execute, f"PRAGMA synchronous={H.synchronous('NORMAL')}")
                return c

            done = begin(None, self.recorder, gate=self.writer, checkout=checkout)
            try:
                c.write_transaction = True
                from . import provenance_clock

                c.provenance_start = provenance_clock.current(c)
                since = events.mark(c) if notify else None
                yield c
                message_links.refresh_dirty(c)
                H.owed_sync(c)
                if notify:
                    logged = events.claim(c, since, None)
                c.commit()
                # Work that must see the committed rows (a delivery worker's doorbell) runs once the write is durable.
                # A barrier (hubdb.durable_commit's WAL sync) runs first and its error is the request's: the write is
                # not answered as done until it is on disk.
                hooks = sorted(c.after_commit, key=lambda after: not getattr(after, "barrier", False))
                c.after_commit.clear()
                for after in hooks:
                    if getattr(after, "barrier", False):
                        after()
                        continue
                    try:
                        after()
                    except Exception:
                        logging.getLogger(__name__).exception("after-commit callback failed")
            except BaseException:
                c.rollback()
                c.after_commit.clear()
                raise
            finally:
                done()
        if logged:
            self.ring(logged)

    def seed_goals(self, c):
        """`registry/goals.yaml`, once per goal (backend/goals.py `seed`): the first draft of the
        company and team goals. After that the hub's rows are the goals, and the
        file is never read for a goal that already exists. A goal whose owner is not on the
        roster yet is skipped and tried again at the next start, so the order of seeding bots
        and goals does not matter."""
        path = self.settings.registry_dir / "goals.yaml"
        if not path.exists():
            return []
        try:
            document = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            return []
        from . import goals as G  # local: keeps goals out of the selector's fan-in

        added = G.seed(c, document, lambda owner: H.resolve_actor(c, owner))
        from . import market as Market

        Market.ensure_curator(c)
        return added

    def seed(self, entries=None, roster=None):
        """Explicit initial import only; stale local checkouts never overwrite assignments."""
        path = self.settings.registry_dir
        registry = yaml.safe_load((path / "employees.yaml").read_text())
        roster = roster or yaml.safe_load((path / "people.yaml").read_text())
        entries = entries or {e["name"]: {**registry.get("defaults", {}), **e} for e in registry["employees"]}
        people = P.load(roster, owner={"email": self.settings.owner_email})
        owner = (P.person_by_email(self.settings.owner_email, people) or {}).get("id") or people["default_user"]
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM bot_config LIMIT 1").fetchone():
                raise Problem("already_initialized", "Registry already imported; use explicit updates", 409)
            previous_states = {r["slug"]: r["state"] for r in H.bots(c)}
            # A roster entry with no runtime or model follows the company's provider choice;
            # with none made yet it stays unresolved and turns refuse to start until it is.
            from . import providers

            choice = providers.load(c, self.settings)
            entries = {slug: providers.fill(choice, entry) for slug, entry in entries.items()}
            H.sync_registry(c, entries, roster)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(people),))
            for slug, state in previous_states.items():
                c.execute("UPDATE bots SET state=? WHERE slug=?", (state, slug))
            from . import company_pause

            conn = cast(sqlite3.Connection, c)  # pyright reads transaction()'s connection as its initial None
            for (bot,) in conn.execute("SELECT id FROM bots WHERE state='paused'").fetchall():
                company_pause.pause_bot(conn, H.TICO_ACTOR, bot)  # a registry's paused bot: its own pause is a hold
            for slug, entry in entries.items():
                team = P.team_of(slug, entries, people)
                operator = seed_operator(slug, team, people, owner)
                mode = str(
                    entry.get("thread_mode")
                    or ("personal" if slug == "assistant" else "shared" if slug == "cpo" else "personal")
                )
                c.execute(
                    "INSERT INTO bot_config(bot,team,owner_id,description,reports_to,repo,"
                    "thread_mode,definition_updated_at,definition_updated_by) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        slug,
                        team,
                        operator,
                        str(entry.get("description") or ""),
                        entry.get("reports_to"),
                        str(entry.get("repo") or ("emp-" + slug)),
                        mode,
                        H.now(),
                        H.TICO_ACTOR,
                    ),
                )
                if entry.get("repo"):
                    from .repositories import note_named

                    # The registry names it: chosen, unless it is the name Tico would give the bot anyway.
                    note_named(c, entry["repo"], ("emp-" + slug, "bot-" + slug), self.settings.github_owner)
                BS.write(c, slug, json.loads(encode(entry)))
            self.seed_goals(c)
            from . import groups as Groups

            Groups.migrate(c, self.settings)

    @staticmethod
    def _principal(identity):
        return identity.actor + (":" + identity.attempt_id if identity.role == "bot" else "")

    def saved(self, c, identity, operation, key):
        """Whether this Idempotency-Key already has a saved answer here: `mutate` replays it (or refuses other content
        under the key) without running the write again, so a check made before `mutate` must not refuse it."""
        return (
            bool(key)
            and len(key) <= 200
            and c.execute(
                "SELECT 1 FROM idempotency WHERE binding IS NULL AND actor=? AND operation=? AND key=?",
                (self._principal(identity), operation, key),
            ).fetchone()
            is not None
        )

    def mutate(self, identity, operation, key, body, fn, check=None, durable=False, retry_body=None):
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        hashed = digest(encode(body))
        principal = self._principal(identity)
        refusal = logged = None
        # Only a money write asks for the durable commit; every other keyed write keeps the plain call.
        with self.transaction(durable=True) if durable else self.transaction() as c:
            # Authenticate leases again under the same write lock as the mutation.
            from .auth import validate_identity

            validate_identity(c, identity)
            if check:
                check(c)
            row = c.execute(
                "SELECT * FROM idempotency WHERE binding IS NULL AND actor=? AND operation=? AND key=?",
                (principal, operation, key),
            ).fetchone()
            if row:
                result = json.loads(row["response"])
                if row["request_hash"] != hashed:
                    legacy = retry_body(c, result) if retry_body else None
                    if legacy is None or row["request_hash"] != digest(encode(legacy)):
                        raise Problem("idempotency_conflict", "This key was used for different content", 409)
                from .auth import Auth
                from . import task_privacy as privacy

                replay_auth = Auth(self)
                replay_auth.sync_access(c)
                aid = saved = None
                if identity.role == "runner" and isinstance(result, dict):
                    saved = result.get("attempt")
                    aid = result.get("attempt_id") or (saved.get("id") if isinstance(saved, dict) else None)
                    # Several receipts contain only an acknowledgement; the route or file request still binds the turn.
                    if operation.startswith("/api/v2/attempts/"):
                        aid = operation.split("/")[4]
                    elif operation in ("/api/v2/files/uploads", "/api/v2/files/imports", "/api/v2/files/dates"):
                        aid = body.get("attempt") or aid
                replay_principal = replay_auth.runner_receipt_principal(c, identity, aid)
                if identity.role == "runner" and operation == "/api/v2/jobs/claim" and isinstance(saved, dict):
                    from . import task_context

                    task = (saved.get("make_page") or {}).get("task") or saved.get("task")
                    saved["task_context"] = task_context.for_turn(c, replay_auth, replay_principal, task)
                # Cached results keep their retry semantics, but access is current on every retry.
                task_ids = set()
                for part in operation.split("/"):
                    if H.task(c, part):
                        task_ids.add(part)

                def referenced(value):
                    if isinstance(value, dict):
                        if "requester_id" in value and "owner_id" in value and value.get("id"):
                            task_ids.add(value["id"])
                        for name, item in value.items():
                            if (
                                name in ("task", "task_id", "parent_id", "blocked_by")
                                and isinstance(item, str)
                                and H.task(c, item)
                            ):
                                task_ids.add(item)
                            referenced(item)
                    elif isinstance(value, list):
                        for item in value:
                            referenced(item)

                referenced(result)
                if operation == "/api/v2/task-bulk":
                    for rows in result.get("tasks", {}).values():
                        task_ids.update(task["id"] for task in rows)
                for task_id in task_ids:
                    replay_auth.task(c, replay_principal, task_id)
                # A replayed claim is checked as the first answer was (task_privacy.require_payload): a person's own
                # Assistant room turn as that person.
                privacy.require_payload(c, replay_principal, result, privacy.attempt_reader(c, replay_principal, aid))
                if '/meetings/' in operation or operation.endswith('/meetings'):
                    from .meeting_items import redact

                    result = redact(c, replay_principal, replay_auth, result)
                if operation.startswith('/api/v2/products'):
                    from .products import replayed as products_replayed

                    result = products_replayed(replay_auth, c, replay_principal, result)
                result = replayed_refusal(c, replay_principal, result)
                from .files import readable_page_refs

                return readable_page_refs(c, replay_auth, replay_principal, result)
            # Who made the changes this write logs (backend/events.py).
            from . import events as changes

            c.execute("SAVEPOINT domain_write")
            since = changes.mark(c)
            try:
                result = fn(c)
                logged = changes.claim(c, since, identity.actor)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                logged = None  # rolled back with the write
                refusal = refused(c, identity, exc)
                # What is missing and the commands allowed next ride with a refusal (Problem.extra). A private task's
                # are its own words, so its receipt keeps only the version; any other keeps what may be kept on
                # record (Refused.stored: a private sub-task it names is not named).
                kept = getattr(exc, "stored", None) or {"detail": refusal.detail, "extra": refusal.extra}
                private = getattr(exc, "private", False)
                # What kept less than the caller was told also keeps how to say it again (replayed_refusal).
                again = getattr(exc, "again", None) if private or getattr(exc, "stored", None) else None
                result = {
                    "_refusal": {
                        "code": refusal.code,
                        "detail": "Private task write refused" if private else kept["detail"],
                        "status": refusal.status,
                        "extra": (
                            {k: v for k, v in refusal.extra.items() if k == "version"}
                            if private
                            else dict(kept["extra"])
                        ),
                        **({"again": again} if again else {}),
                    }
                }
            if not is_poll(operation, result):
                cached = (
                    {k: v for k, v in result.items() if k not in SHOWN_ONCE} if isinstance(result, dict) else result
                )
                c.execute(
                    "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
                    (principal, operation, key, hashed, encode(cached), H.now()),
                )
        self.ring(logged)
        if refusal:
            # This key now records a rolled-back operation, not an earlier successful delivery.
            refusal.extra["idempotency_refused"] = True
            raise refusal
        return result

    def write(self, identity, fn, *, validate=True):
        """`mutate` without an idempotency record, for a route whose request is its own: the same
        identity check under the write lock, and the same all-or-nothing refusal. `validate=False` is for a write the
        server makes for itself on a bot's behalf (backend/files.py, a review page): it has no credential to check, and
        `fn` checks the records it writes."""
        refusal = logged = None
        with self.transaction() as c:
            if validate:
                from .auth import validate_identity

                validate_identity(c, identity)
            # Who made the changes this write logs (backend/events.py).
            from . import events as changes

            c.execute("SAVEPOINT domain_write")
            since = changes.mark(c)
            try:
                result = fn(c)
                logged = changes.claim(c, since, identity.actor)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                logged = None  # rolled back with the write
                refusal = refused(c, identity, exc)
        self.ring(logged)
        if refusal:
            raise refusal
        return result

    def ring(self, seq):
        """After a commit that logged changes: wake the open event streams (backend/events.py)."""
        from .events import ring

        ring(self, seq)

    def enqueue_existing(self):
        from .enqueue import external_bot_sql

        with self.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO jobs(id,message_id,bot_id,created_at) "
                "SELECT m.id,m.id,substr(m.recipient_id,5),m.created_at FROM messages m "
                "LEFT JOIN conversations cv ON cv.id=m.conversation_id "
                "WHERE m.recipient_id LIKE 'bot:%' AND m.delivered_at IS NULL AND m.deleted_at IS NULL AND NOT "
                + external_bot_sql("m.recipient_id")
            )
