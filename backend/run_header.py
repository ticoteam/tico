# pyright: strict
"""The run header: where a run's card stands now and, in a person's own Assistant turn, what waits on that person.

One block of short lines, read in a read transaction of its own just after the claim is written
(backend/fleet_runner_routes.py claim), as whoever the turn acts for: the person in their own Assistant turn
(Auth.assistant_principal), else the bot. The claim's message and history were written earlier and can be stale ("still
waiting for your OK" after the OK was given); this block is read as the run starts. The runner puts it in the turn's own
part of the prompt, at its tail, never in its standing part, so a resumed session keeps its cached prefix
(runner-rs/src/prompt.rs).

- Card now, as the task's own read shows it to that reader (task_calls.task_view, then the page checks the task read
  makes): stage and pipeline, time in the stage, version, whose turn, each requirement met (with the page versions its
  approval was given on, Standing.approved) or missing, what others changed since this bot's last run on the card (the
  task history's own filters, task_reads.readable_history), the stage rules that apply there and the org rules the bot
  follows. A Make page turn's card is the task it makes the page for.
- The person's snapshot: their Needs you count and its first TOP items (views.needs_shown), and the open tasks their
  Assistant filed for them that they may still read (the `task.create` events recorded `via: assistant`).

At most MAX_CHARS, a "+N more" line saying how many lines were cut. Building it never holds up a turn: it runs on a
worker thread, and the claim is answered without it past BUDGET_SECONDS or on any error.
"""

import logging
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as Late
from typing import Any, cast

from . import auth as _auth
from . import files as _files
from . import history_log as _history_log
from . import hubdb as _hubdb
from . import org_rules as _org_rules
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
org_rules: Any = _org_rules
task_reads: Any = _task_reads
views: Any = _views
Row = dict[str, Any]

log = logging.getLogger(__name__)

MAX_CHARS = 2000
BUDGET_SECONDS = 0.3
TOP = 5  # the Needs you items shown; every other list is cut by MAX_CHARS alone
TITLE_MAX = 120
REFS = ("page:", "file:")  # the refs an approval names a version by
HEAD = "Run header, read as this run starts (newer than anything earlier in this conversation):"
# A build that stalls (a lock, a slow connect) keeps its worker. A claim takes a slot or goes without a header, so the
# pool never queues: at most WORKERS builds at once, and none waits for a worker.
WORKERS = 4
_WORKERS = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="run-header")
_SLOTS = threading.BoundedSemaphore(WORKERS)

# The open tasks the person's Assistant filed for them that they may read: `%s` is their task rule (Auth.task_sql).
HANDOFFS_SQL = (
    "SELECT id, title, owner_id FROM tasks WHERE status='open' AND id IN (SELECT target FROM events WHERE actor=? "
    "AND action='task.create' AND json_extract(detail,'$.via')='assistant') AND (%s) ORDER BY created_at DESC, id DESC"
)


def build(store: Any, auth: Any, attempt: Row, runner_id: str | None = None) -> str | None:
    """The header for a claimed turn (the claim answer's `attempt`, claimed by `runner_id`), or None: nothing to say,
    the lease no longer live there (a replayed claim), an error, or not ready within BUDGET_SECONDS (the build may go
    on in its worker; its answer is dropped)."""
    deadline = time.monotonic() + BUDGET_SECONDS
    if not _SLOTS.acquire(blocking=False):
        log.warning("run header skipped for attempt %s: every worker is busy", attempt.get("id"))
        return None
    try:
        future = _WORKERS.submit(_slotted, store, auth, attempt, runner_id, deadline)
    except Exception:  # noqa: BLE001 - a header never stops a turn
        _SLOTS.release()
        log.warning("run header skipped for attempt %s", attempt.get("id"), exc_info=True)
        return None
    try:
        header = future.result(timeout=max(0.0, deadline - time.monotonic()))
        return header if time.monotonic() <= deadline else None
    except Late:
        log.warning("run header skipped for attempt %s: not ready in %ss", attempt.get("id"), BUDGET_SECONDS)
    except Exception:  # noqa: BLE001 - a header never stops a turn
        log.warning("run header skipped for attempt %s", attempt.get("id"), exc_info=True)
    return None


def _slotted(store: Any, auth: Any, attempt: Row, runner_id: str | None, deadline: float) -> str | None:
    try:
        return _build(store, auth, attempt, runner_id, deadline)
    finally:
        _SLOTS.release()


# The turn is still this computer's to start: leased to it, not yet started, under its assignment's generation.
LIVE_SQL = (
    "SELECT 1 FROM attempts a JOIN assignments s ON s.bot=a.bot_id AND s.runner_id=a.runner_id "
    "AND s.generation=a.generation WHERE a.id=? AND a.runner_id=? AND a.state='leased' AND a.lease_until>?"
)


def _build(store: Any, auth: Any, attempt: Row, runner_id: str | None, deadline: float) -> str | None:
    if time.monotonic() > deadline:  # its claim was answered already
        return None
    with store.read_transaction() as c:
        # A query still running at the deadline is interrupted (the connection is then not reused: Store taints it).
        c.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        try:
            if not c.execute(LIVE_SQL, (attempt["id"], runner_id, H.now())).fetchone():
                return None
            lines, unseen = for_turn(c, auth, attempt)
        finally:
            c.set_progress_handler(None, 0)
    return capped(lines, unseen)


def for_turn(c: Any, auth: Any, attempt: Row) -> tuple[list[str], int]:
    """The header's lines for this attempt, read as whoever the turn acts for, and how many entries were not even read
    because MAX_CHARS could not show them."""
    row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    if row is None:
        return [], 0
    bot = "bot:" + str(row["bot_id"])
    acting: Any = auth.assistant_principal(c, row)
    who: Any = acting or Identity(bot, "bot", attempt_id=row["id"])
    page: Row = attempt.get("make_page") or {}
    making: Row = page.get("task") or {}
    task: Row = making or attempt.get("task") or {}
    task_id = str(task.get("id") or "")
    lines = card(c, auth, who, bot, task_id, making=bool(making)) if task_id else []
    if acting is None:
        return lines, 0
    # Their own Assistant turn, a Make page one included (Auth.assistant_principal).
    told, unseen = snapshot(c, auth, acting, MAX_CHARS - len(HEAD) - sum(len(line) + 1 for line in lines))
    return lines + told, unseen


def capped(lines: Sequence[str], unseen: int = 0) -> str | None:
    """`lines` under HEAD within MAX_CHARS, each whole or not at all, then a last line counting the entries left out
    (`unseen` more were never read). A heading (a line ending in ":") goes with its bullets ("- " lines): it is kept
    only with at least one of them and is no entry itself. None when there is nothing to say."""
    if not lines:
        return None
    groups: list[list[str]] = []
    for line in (" ".join(line.split()) for line in lines):
        if line.startswith("- ") and groups:
            groups[-1].append(line)
        else:
            groups.append([line])
    out, size, left = [HEAD], len(HEAD), unseen
    room = MAX_CHARS - len(f"\n+{len(lines) + unseen} more")
    for first, *bullets in groups:
        heading = first.endswith(":") and bool(bullets)
        if not heading and size + 1 + len(first) > room:
            left += 1 + len(bullets)
            continue
        kept: list[str] = []
        cost = 0 if heading else len(first) + 1
        for bullet in bullets:
            more = len(bullet) + 1 + (len(first) + 1 if heading and not kept else 0)  # a heading with its first bullet
            if size + cost + more > room:
                left += 1
                continue
            cost += more
            kept.append(bullet)
        if heading and not kept:
            continue
        out += [first, *kept]
        size += cost
    return "\n".join([*out, f"+{left} more"] if left else out)


def card(c: Any, auth: Any, who: Any, bot: str, task_id: str, *, making: bool = False) -> list[str]:
    """Where the task stands now, as its own read shows it to `who`; [] when they may not read it. A task that is done,
    closed or on no pipeline says what it can: its title, status, stage and version."""
    if not _readable(c, auth, who, task_id):
        return []
    plain: Row = H.task(c, task_id) or {}
    view = _view(c, auth, who, task_id)
    row = E.task(c, task_id)
    title = _clip(plain.get("title") or "This task", TITLE_MAX)
    if not view.get("stage_since"):
        stage: Row = view.get("stage") or {}
        where = f" in {stage['name']}" if stage.get("name") else ""
        out = [f"Card now: {title} is {plain.get('status') or 'open'}{where}; version {plain.get('version')}."]
        return out + _changes_since(c, auth, who, bot, plain, making)
    assert row is not None
    approved = {str(s.requirement["id"]): s.approved for s in R.standings(c, row) if s.met}
    pipeline: Row = view["pipeline"]
    age = _age(view.get("stage_since"))
    out: list[str] = [
        f"Card now: {title} is in {view['stage'].get('name')} "
        f"({pipeline.get('name') or '?'}){f', {age} in this stage' if age else ''}; version {view.get('version')}."
    ]
    turn: Row | None = view.get("turn")
    if turn:
        whom = _name(c, turn.get("who"))
        line = _turn_line(view, turn)
        out.append(f"Whose turn: {whom}: {line}" if whom else f"Whose turn: {line}")
    met: list[str] = []
    missing: list[str] = []
    for entry in view["to_leave"]:
        if _redacted(entry):  # the page checks took its words: neither met nor missing says it
            continue
        said = f"{entry['line']}: {entry['standing']}"
        if entry.get("met"):
            rid = str(entry["requirement_id"])
            # A bound OK's pages are its parties' alone, as its line is (task_asks.hidden_from).
            private = task_asks.hidden_from(c, who.task_actor or who.actor, rid)
            said_at: Row = entry.get("answer") or {}
            at = str(said_at.get("at") or H.now())
            # A live requirement's from its evaluator; an OK a newer ask replaced, from the pages it showed; a stage's
            # OK of nothing, the earlier stage's page it showed (kept only when it passed its page check).
            given = list(approved.get(rid, ())) if rid in approved else [str(v) for v in entry.get("shows") or ()]
            earlier: Row = entry.get("earlier") or {}
            if earlier.get("ref"):
                given.append(str(earlier["ref"]))
            versions = [] if private else [_pinned(c, v, at) for v in dict.fromkeys(given) if v.startswith(REFS)]
            versions = [v for v in versions if _opens(c, auth, who, v)]
            met.append(said + (f" (approved {', '.join(versions)})" if versions else ""))
        else:
            whom = _name(c, entry.get("who"))
            missing.append(R.sentences(said, f"Waiting on {whom}" if whom else ""))
    out += _listed("Met:", met)
    out += _listed("Missing to leave this stage:", missing)
    out += _changes_since(c, auth, who, bot, row, making)
    rules = [
        f"{r['name']}: {r['reason']}" if r.get("reason") else str(r["name"])
        for r in stage_rules.applying(c, row.get("pipeline_id"), row.get("stage_id"))
    ]
    # The org rules it follows, as its Rules tab lists them (org_rules.bot_rules): every teammate reads them.
    rules += [str(r["text"]) for r in org_rules.applies_to(c, bot[len("bot:") :], org_rules._active(c))]
    out += _listed("Rules on this stage:", rules)
    return out


def _view(c: Any, auth: Any, who: Any, task_id: str) -> Row:
    """The engine's part of the task as its own read gives it to `who` (task_list_routes task_view): each OK's page from
    an earlier stage only when they may open it, and every entry whose words come from a page they may not open
    replaced by that page's label. {} for a task on no pipeline."""
    view: Row | None = H.task_view(c, task_id, who.task_actor or who.actor, auth.task_sql(c, who))
    if not view:
        return {}
    for entry in view["to_leave"]:
        files.readable_earlier(c, auth, who, entry)
    ids = [entry.get("requirement_id") for entry in view["to_leave"]]
    shown: Row = files.readable_page_refs(c, auth, who, view) or {}
    # The page filter replaces an entry it takes whole by a placeholder without its requirement: keep which one it was,
    # here only, so whose turn it is says no more than the entry does (`_turn_line`).
    shown["to_leave"] = [
        {**entry, "requirement_id": rid} if _redacted(entry) else entry
        for entry, rid in zip(shown.get("to_leave") or [], ids, strict=True)
    ]
    return shown


def _redacted(entry: Row) -> bool:
    """An entry the page checks took (whole, or its words)."""
    return bool(entry.get("removed")) or any(entry.get(k) in files.REMOVED_LABELS for k in ("line", "standing"))


def _turn_line(view: Row, turn: Row) -> str:
    """Whose turn it is in words, said only as far as the requirement it waits on may be: its page label when the
    reader may not open the page its words come from, the private line for a bound OK they are no party of."""
    entries: list[Row] = view.get("to_leave") or []
    wanted = turn.get("requirement_id")
    empty: Row = {}
    entry = next((e for e in entries if e.get("requirement_id") == wanted), empty)
    if wanted and _redacted(entry):
        said = [entry.get(k) for k in ("label", "line", "standing") if entry.get(k) in files.REMOVED_LABELS]
        return str(said[0] if said else files.REMOVED_LABELS[0])
    if entry.get("line") == task_asks.PRIVATE_LINE:
        return task_asks.PRIVATE_LINE
    return str(turn.get("line") or "")


# The bot's newest finished Make page turn for this task, as the same person: it read the task as they do.
MADE_PAGE_SQL = (
    "SELECT max(a.finished_at) FROM attempts a JOIN messages m ON m.id=a.message_id WHERE a.bot_id=? "
    "AND a.acting_person=? AND a.state='completed' AND json_extract(m.refs,'$.make_page.task')=?"
)


def _changes_since(c: Any, auth: Any, who: Any, bot: str, task: Row, making: bool) -> list[str]:
    """What others changed since this bot's last run on the task ended: its last run there, or for a Make page turn its
    last finished Make page turn for the same task and person, whichever ended later."""
    last = task_wakes.last_run(c, task, bot) or {}
    ended = str(last.get("finished_at") or "")
    if making:
        made = c.execute(MADE_PAGE_SQL, (bot[len("bot:") :], who.actor, task["id"])).fetchone()
        ended = max(ended, str(made[0] or "") if made else "")
    if not ended:
        return []
    said = changes(c, auth, who, str(task["id"]), bot, {"finished_at": ended})
    return _listed(f"Changes since your last run ended ({ended[:16].replace('T', ' ')} UTC):", said)


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
        # A row the page checks redacted says nothing it may tell: it is neither shown nor counted.
        if event.get("actor") == bot or event.get("removed") or any(v in files.REMOVED_LABELS for v in event.values()):
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


# How many open tasks the person's Assistant filed for them that they may read (HANDOFFS_SQL's rows).
HANDOFFS_COUNT_SQL = "SELECT count(*) FROM (" + HANDOFFS_SQL + ")"


def snapshot(c: Any, auth: Any, person: Any, room: int = MAX_CHARS) -> tuple[list[str], int]:
    """What waits on the person now (their Needs you count and its first TOP items, as the list they are served shows
    them) and the open tasks their Assistant filed for them, as they may read them; and how many of those tasks were
    not read because `room` characters could not show them."""
    items: list[Row] = sorted(views.needs_shown(c, auth, person), key=views.needs_key)
    out = [f"Needs you ({H.actor_name(c, person.actor)}): {len(items)}"]
    for item in items[:TOP]:
        task: Row = item.get("task") or {}
        title = task.get("title")
        out.append(f"- {item['line']}" + (f" ({_clip(title, TITLE_MAX)})" if title else ""))
    rule: str = auth.task_sql(c, person)
    total = int(c.execute(HANDOFFS_COUNT_SQL % rule, (person.actor,)).fetchone()[0])
    room -= sum(len(line) + 1 for line in out)
    lines: list[str] = []
    for r in c.execute(HANDOFFS_SQL % rule, (person.actor,)):
        if room <= 0:
            break
        view = _view(c, auth, person, r["id"])
        turn: Row = view.get("turn") or {}
        owner: str = H.actor_name(c, r["owner_id"]) if r["owner_id"] else "nobody"
        said = _turn_line(view, turn) if turn else ""
        lines.append(f"{_clip(r['title'] or 'A task', TITLE_MAX)} ({owner})" + (f": {said}" if said else ""))
        room -= len(lines[-1]) + 3
    out += _listed("Open handoffs you filed through me:", lines)
    return out, total - len(lines)


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
        "SELECT max(version) FROM bot_file_versions WHERE file_id=? AND created_at<=?", (ref.partition(":")[2], at)
    ).fetchone()
    return f"{ref}@{found[0]}" if found and found[0] else ref


def _opens(c: Any, auth: Any, who: Any, ref: str) -> bool:
    """Whether `who` may read that version of the page or file, through the file's own read check."""
    return bool(files.page_refs_readable(c, auth, who, ["page:" + ref.partition(":")[2]]))


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
