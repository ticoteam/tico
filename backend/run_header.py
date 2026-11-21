# pyright: strict
"""The run header: where a run's card stands now and, in a person's own Assistant turn, what waits on that person.

One block of short lines, read in a read transaction of its own just after the claim is written
(backend/fleet_runner_routes.py claim), as whoever the turn acts for: the person in their own Assistant turn
(Auth.assistant_principal), else the bot. The claim's message and history were written earlier and can be stale ("still
waiting for your OK" after the OK was given); this block is read as the run starts. The runner puts it in the turn's own
part of the prompt, never in its standing part, so a resumed session keeps its cached prefix (runner-rs/src/prompt.rs).

- Card now, as the task's own read shows it to that reader (task_calls.task_view, then the page checks the task read
  makes): stage and pipeline, time in the stage, version, whose turn, each requirement met (with the page versions its
  approval was given on, Standing.approved) or missing, what others changed since this bot's last run on the card (the
  task history's own filters, task_reads.readable_history), and the stage rules that apply there. A Make page turn's
  card is the task it makes the page for.
- The person's snapshot: their Needs you count and its first TOP items (views.needs_items), and the open tasks their
  Assistant filed for them that they may still read (the `task.create` events recorded `via: assistant`).

At most MAX_CHARS, a "+N more" line saying how many lines were cut. Building it never holds up a turn: it runs on a
worker thread, and the claim is answered without it past BUDGET_SECONDS or on any error.
"""

import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as Late
from typing import Any, cast

from . import auth as _auth
from . import files as _files
from . import history_log as _history_log
from . import hubdb as _hubdb
from . import stage_rules
from . import store as _store
from . import task_asks
from . import task_engine as E
from . import task_reads as _task_reads
from . import task_requirements as R
from . import task_wakes
from . import views as _views

# Untyped modules: what this one reads of them gets its type here.
H: Any = _hubdb
Identity: Any = _auth.Identity
MOVES: dict[str, str] = _history_log.MOVES
Problem: type[Exception] = _store.Problem
files: Any = _files
task_reads: Any = _task_reads
views: Any = _views
Row = dict[str, Any]

log = logging.getLogger(__name__)

MAX_CHARS = 2000
BUDGET_SECONDS = 0.3
TOP = 5  # the Needs you items shown before its "+N more"; every other list is cut by MAX_CHARS alone
LINE_MAX = 300
TITLE_MAX = 120
HEAD = "Run header, read as this run starts (newer than anything earlier in this conversation):"
# A build that stalls (a lock, a slow connect) keeps its worker; a few at once is the most it holds.
_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="run-header")

HANDOFFS_SQL = (
    "SELECT t.id, t.title, t.owner_id FROM events e JOIN tasks t ON t.id=e.target "
    "WHERE e.actor=? AND e.action='task.create' AND json_extract(e.detail,'$.via')='assistant' AND t.status='open' "
    "ORDER BY e.created_at DESC, e.id DESC"
)


def build(store: Any, auth: Any, attempt: Row) -> str | None:
    """The header for a claimed turn (the claim answer's `attempt`), or None: nothing to say, an error, or not ready
    within BUDGET_SECONDS (the build may go on in its worker; its answer is dropped)."""
    deadline = time.monotonic() + BUDGET_SECONDS
    try:
        return _WORKERS.submit(_build, store, auth, attempt, deadline).result(timeout=BUDGET_SECONDS)
    except Late:
        log.warning("run header skipped for attempt %s: not ready in %ss", attempt.get("id"), BUDGET_SECONDS)
    except Exception:  # noqa: BLE001 - a header never stops a turn
        log.warning("run header skipped for attempt %s", attempt.get("id"), exc_info=True)
    return None


def _build(store: Any, auth: Any, attempt: Row, deadline: float) -> str | None:
    with store.read_transaction() as c:
        # A query still running at the deadline is interrupted (the connection is then not reused: Store taints it).
        c.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        try:
            lines: list[str] = for_turn(c, auth, attempt)
        finally:
            c.set_progress_handler(None, 0)
    return capped(lines)


def for_turn(c: Any, auth: Any, attempt: Row) -> list[str]:
    """The header's lines for this attempt, read as whoever the turn acts for."""
    row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    if row is None:
        return []
    bot = "bot:" + str(row["bot_id"])
    acting: Any = auth.assistant_principal(c, row)
    who: Any = acting or Identity(bot, "bot", attempt_id=row["id"])
    page: Row = attempt.get("make_page") or {}
    making: Row = page.get("task") or {}
    task: Row = making or attempt.get("task") or {}
    task_id = str(task.get("id") or "")
    lines = card(c, auth, who, bot, task_id) if task_id else []
    if acting is not None and not making:
        lines += snapshot(c, auth, acting)
    return lines


def capped(lines: Sequence[str]) -> str | None:
    """`lines` under HEAD, cut to MAX_CHARS with a last line saying how many were left out; None when empty."""
    if not lines:
        return None
    lines = [_clip(line, LINE_MAX) for line in lines]
    out, size = [HEAD], len(HEAD)
    room = MAX_CHARS - len(f"\n+{len(lines)} more")
    for at, line in enumerate(lines):
        if size + 1 + len(line) > room:
            out.append(f"+{len(lines) - at} more")
            break
        out.append(line)
        size += 1 + len(line)
    return "\n".join(out)


def card(c: Any, auth: Any, who: Any, bot: str, task_id: str) -> list[str]:
    """Where the task stands now, as its own read shows it to `who`; [] when they may not read it, or it is on no
    pipeline or not open."""
    if not _readable(c, auth, who, task_id):
        return []
    rule: str = auth.task_sql(c, who)
    view: Row | None = H.task_view(c, task_id, who.task_actor or who.actor, rule)
    if not view or not view.get("stage_since"):
        return []
    for entry in view["to_leave"]:
        files.readable_earlier(c, auth, who, entry)
    view = files.readable_page_refs(c, auth, who, view)
    assert view is not None
    row = E.task(c, task_id)
    assert row is not None
    approved = {str(s.requirement["id"]): s.approved for s in R.standings(c, row) if s.met}
    stage: Row = view["stage"]
    pipeline: Row = view["pipeline"]
    age = _age(view.get("stage_since"))
    out: list[str] = [
        f"Card now: {_clip(row.get('title') or 'This task', TITLE_MAX)} is in {stage.get('name')} "
        f"({pipeline.get('name') or '?'}){f', {age} in this stage' if age else ''}; version {view.get('version')}."
    ]
    turn: Row | None = view.get("turn")
    if turn:
        whom = _name(c, turn.get("who"))
        out.append(f"Whose turn: {whom}: {turn.get('line')}" if whom else f"Whose turn: {turn.get('line')}")
    met: list[str] = []
    missing: list[str] = []
    for entry in view["to_leave"]:
        if entry.get("removed"):
            missing.append(str(entry.get("label")))
            continue
        said = f"{entry['line']}: {entry['standing']}"
        if entry.get("met"):
            rid = str(entry["requirement_id"])
            # A bound OK's pages are its parties' alone, as its line is (task_asks.hidden_from).
            private = task_asks.hidden_from(c, who.task_actor or who.actor, rid)
            said_at: Row = entry.get("answer") or {}
            at = str(said_at.get("at") or H.now())
            # A live requirement's from its evaluator; an OK a newer ask replaced, from the pages it showed.
            given = approved.get(rid, ()) if rid in approved else [str(v) for v in entry.get("shows") or ()]
            versions = [] if private else [_pinned(c, v, at) for v in given if v.startswith("page:")]
            versions = [v for v in versions if _opens(c, auth, who, v)]
            met.append(said + (f" (approved {', '.join(versions)})" if versions else ""))
        else:
            whom = _name(c, entry.get("who"))
            missing.append(R.sentences(said, f"Waiting on {whom}" if whom else ""))
    out += _listed("Met:", met)
    out += _listed("Missing to leave this stage:", missing)
    last = task_wakes.last_run(c, row, bot)
    if last and last.get("finished_at"):
        ended = str(last["finished_at"])[:16].replace("T", " ")
        out += _listed(f"Changes since your last run ended ({ended} UTC):", changes(c, auth, who, task_id, bot, last))
    rules = stage_rules.applying(c, row.get("pipeline_id"), row.get("stage_id"))
    out += _listed(
        "Rules on this stage:", [f"{r['name']}: {r['reason']}" if r.get("reason") else str(r["name"]) for r in rules]
    )
    return out


def changes(c: Any, auth: Any, who: Any, task_id: str, bot: str, last: Row) -> list[str]:
    """What others changed on the task since this bot's last run there ended, newest first, as the task's history
    shows it to `who`."""
    history: list[Row] = [
        dict(e)
        for e in c.execute(
            f"SELECT {H.TASK_EVENT_COLUMNS}, data FROM history_events WHERE subject_type='task' AND subject_id=? "
            "AND created_at>? ORDER BY created_at, rowid",
            (task_id, last["finished_at"]),
        )
    ]
    history = task_reads.readable_history(c, who, history, auth.task_sql(c, who))
    shown: list[Any] = files.readable_page_refs(c, auth, who, history)
    out: list[str] = []
    for item in reversed(shown):
        if not isinstance(item, dict):
            continue
        event: Row = cast(Row, item)
        if event.get("actor") == bot:
            continue
        kind, new = str(event.get("field")), event.get("new")
        if kind == "move":
            moved: Row = H._json(event.get("data"), {}) or {}
            how = MOVES.get(str(moved.get("move")), "moved")
            target = E.stage(c, new) if new else None
            what = f"{how}, now in {target['name']}" if target else how
        elif kind == "reassign" and new:
            what = f"gave it to {H.actor_name(c, new)}"
        else:
            what = f"changed {kind.replace('_', ' ')}"
        out.append(f"{_name(c, event.get('actor')) or 'Tico'}: {what}")
    return out


def snapshot(c: Any, auth: Any, person: Any) -> list[str]:
    """What waits on the person now and the open tasks their Assistant filed for them, as they may read them."""
    items: list[Row] = views.needs_items(c, auth, person, count_only=True)
    shown = items[:TOP]
    ids = sorted({str(i["task_id"]) for i in shown if i.get("task_id")})
    titles: dict[str, str] = (
        {r[0]: r[1] for r in c.execute(f"SELECT id, title FROM tasks WHERE id IN ({','.join('?' * len(ids))})", ids)}
        if ids
        else {}
    )
    out = [f"Needs you ({H.actor_name(c, person.actor)}): {len(items)}"]
    for item in shown:
        title = titles.get(str(item.get("task_id")))
        out.append(f"- {item['line']}" + (f" ({_clip(title, TITLE_MAX)})" if title else ""))
    if len(items) > TOP:
        out.append(f"+{len(items) - TOP} more")

    def opens(other: str) -> bool:
        return _readable(c, auth, person, other)

    lines: list[str] = []
    for r in c.execute(HANDOFFS_SQL, (person.actor,)).fetchall():
        if not opens(r["id"]):
            continue
        view: Row = H.task_view(c, r["id"], person.task_actor or person.actor, auth.task_sql(c, person)) or {}
        turn: Row = view.get("turn") or {}
        owner: str = H.actor_name(c, r["owner_id"]) if r["owner_id"] else "nobody"
        said = str(turn.get("line") or "")
        lines.append(f"{_clip(r['title'] or 'A task', TITLE_MAX)} ({owner})" + (f": {said}" if said else ""))
    out += _listed("Open handoffs you filed through me:", lines)
    return out


def _listed(head: str, items: Sequence[str]) -> list[str]:
    """`head` and its items, one a line; nothing when there are none. MAX_CHARS cuts the header as a whole."""
    return [head, *(f"- {item}" for item in items)] if items else []


def _readable(c: Any, auth: Any, who: Any, task_id: str) -> bool:
    try:
        auth.task(c, who, task_id, tags=False)
        return True
    except Problem:
        return False


def _pinned(c: Any, ref: str, at: str) -> str:
    """A page ref with its version: an OK that shows a page by id alone approved the version it had when OK'd (`at`)."""
    if "@" in ref:
        return ref
    found = c.execute(
        "SELECT max(version) FROM bot_file_versions WHERE file_id=? AND created_at<=?", (ref[len("page:") :], at)
    ).fetchone()
    return f"{ref}@{found[0]}" if found and found[0] else ref


def _opens(c: Any, auth: Any, who: Any, ref: str) -> bool:
    return bool(files.page_refs_readable(c, auth, who, [ref]))


def _name(c: Any, actor: object) -> str:
    """A person's or bot's name for an actor; '' for anything else (a task id, a source, nobody)."""
    return str(H.actor_name(c, actor)) if isinstance(actor, str) and actor.startswith(("human:", "bot:")) else ""


def _age(since: str | None) -> str:
    began: Any = H.parse_ts(since)
    now: Any = H.parse_ts(H.now())
    if not began or not now:
        return ""
    minutes = max(0, int((now - began).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} days"


def _clip(text: object, n: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"
