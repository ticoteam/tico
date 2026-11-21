# pyright: strict
"""The run header: where a run's card stands now and, in a person's own Assistant turn, what waits on that person.

One block of short lines, read in a read transaction of its own just after the claim is written
(backend/fleet_runner_routes.py claim), as whoever the turn acts for: the person in their own Assistant turn
(Auth.assistant_principal), else the bot. The claim's message and history were written earlier and can be stale ("still
waiting for your OK" after the OK was given); this block is read as the run starts. The runner puts it in the turn's own
part of the prompt, never in its standing part, so a resumed session keeps its cached prefix (runner-rs/src/prompt.rs).

- Card now: stage and pipeline, time in the stage, version, whose turn, what is met, what is missing, what changed since
  this bot's last run on the card, and the stage rules that apply there (stage_rules.applying).
- The person's snapshot: their Needs you count and its first TOP items (views.needs_items), and the open tasks their
  Assistant filed for them (the `task.create` events recorded `via: assistant`, as backend/assistant_changes.py lists).

At most MAX_CHARS, a "+N more" line saying how many lines were cut. Building it never holds up a turn: past
BUDGET_SECONDS, or on any error, the turn runs without it.
"""

import logging
import time
from collections.abc import Sequence
from typing import Any

from . import auth as _auth
from . import history_log as _history_log
from . import hubdb as _hubdb
from . import stage_rules
from . import store as _store
from . import task_engine as E
from . import task_requirements as R
from . import task_wakes
from . import waiting_on as W

# Untyped modules: what this one reads of them gets its type here.
H: Any = _hubdb
Identity: Any = _auth.Identity
MOVES: dict[str, str] = _history_log.MOVES
Problem: type[Exception] = _store.Problem
Row = dict[str, Any]

log = logging.getLogger(__name__)

MAX_CHARS = 2000
BUDGET_SECONDS = 0.3
TOP = 5  # the items each list shows before its "+N more"
LINE_MAX = 300
TITLE_MAX = 120
FETCH_MAX = 100  # the rows a list reads to count what it leaves out
HEAD = "Run header, read as this run starts (newer than anything earlier in this conversation):"

HANDOFFS_SQL = (
    "SELECT t.id, t.title, t.owner_id FROM events e JOIN tasks t ON t.id=e.target "
    "WHERE e.actor=? AND e.action='task.create' AND json_extract(e.detail,'$.via')='assistant' AND t.status='open' "
    "ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
)
CHANGES_SQL = (
    "SELECT actor_id, kind, new, data FROM history_events WHERE subject_type='task' AND subject_id=? AND created_at>? "
    "AND coalesce(actor_id,'')<>? ORDER BY created_at DESC, rowid DESC LIMIT ?"
)


def build(store: Any, auth: Any, attempt: Row) -> str | None:
    """The header for a claimed turn (the claim answer's `attempt`), or None: nothing to say, an error, or past budget."""
    deadline = time.monotonic() + BUDGET_SECONDS
    try:
        with store.read_transaction() as c:
            # Interrupts any one query that runs past the budget (the connection is then not reused: Store taints it).
            c.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            try:
                lines: list[str] = for_turn(c, auth, attempt)
            finally:
                c.set_progress_handler(None, 0)
    except Exception:  # noqa: BLE001 - a header never stops a turn
        log.warning("run header skipped for attempt %s", attempt.get("id"), exc_info=True)
        return None
    if time.monotonic() > deadline:
        log.warning("run header skipped for attempt %s: past %ss", attempt.get("id"), BUDGET_SECONDS)
        return None
    return capped(lines)


def for_turn(c: Any, auth: Any, attempt: Row) -> list[str]:
    """The header's lines for this attempt, read as whoever the turn acts for."""
    row = c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    if row is None:
        return []
    bot = "bot:" + str(row["bot_id"])
    acting: Any = auth.assistant_principal(c, row)
    who: Any = acting or Identity(bot, "bot", attempt_id=row["id"])
    task: Row = attempt.get("task") or {}
    task_id: str | None = task.get("id")
    lines = card(c, auth, who, bot, task_id) if task_id else []
    if acting is not None:
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
    """Where the task stands now, as `who` may read it; [] when they may not, or it is on no pipeline."""
    if not _readable(c, auth, who, task_id):
        return []
    row = E.task(c, task_id)
    if row is None or not row.get("visit"):
        return []

    def shown(other: str) -> bool:
        return _readable(c, auth, who, other)

    stage: Row = row["stage"]
    pipeline: Row = row.get("pipeline") or {}
    visit: Row = row["visit"]
    age = _age(visit.get("entered_at"))
    out: list[str] = [
        f"Card now: {_clip(row.get('title') or 'This task', TITLE_MAX)} is in {stage['name']} "
        f"({pipeline.get('name') or '?'}){f', {age} in this stage' if age else ''}; version {row.get('version')}."
    ]
    waits = W.waiting_on(c, task_id)
    if waits:
        wait = W.unnamed(waits[0], shown)
        whom: str = H.actor_name(c, wait.who) if wait.who and ":" in wait.who else ""
        out.append(f"Whose turn: {whom}: {wait.line}" if whom else f"Whose turn: {wait.line}")
    standings = [W.unnamed(s, shown) for s in R.standings(c, row)]
    out += _listed("Met:", [f"{s.name}: {s.line}" for s in standings if s.met])
    out += _listed(
        "Missing to leave this stage:",
        [
            R.sentences(f"{s.name}: {s.line}" if s.line and s.line != s.name else s.name)
            + (f" Waiting on {H.actor_name(c, s.on)}." if s.state == R.WAITING and s.on and ":" in s.on else "")
            for s in standings
            if not s.met
        ],
    )
    last = task_wakes.last_run(c, row, bot)
    if last and last.get("finished_at"):
        ended = str(last["finished_at"])[:16].replace("T", " ")
        out += _listed(f"Changes since your last run ended ({ended} UTC):", changes(c, task_id, bot, last))
    rules = stage_rules.applying(c, row.get("pipeline_id"), row.get("stage_id"))
    said: list[str] = [f"{r['name']}: {r['reason']}" if r.get("reason") else str(r["name"]) for r in rules]
    out += _listed("Rules on this stage:", said)
    return out


def changes(c: Any, task_id: str, bot: str, last: Row) -> list[str]:
    """What others changed on the task since this bot's last run there ended, newest first."""
    out: list[str] = []
    rows: list[tuple[str | None, str, str | None, str | None]] = c.execute(
        CHANGES_SQL, (task_id, last["finished_at"], bot, FETCH_MAX)
    ).fetchall()
    for actor, kind, new, data in rows:
        if kind == "move":
            moved: Row = H._json(data, {}) or {}
            how = MOVES.get(str(moved.get("move")), "moved")
            target = E.stage(c, new) if new else None
            what = f"{how}, now in {target['name']}" if target else how
        elif kind == "reassign" and new:
            what = f"gave it to {H.actor_name(c, new)}"
        else:
            what = f"changed {str(kind).replace('_', ' ')}"
        out.append(f"{H.actor_name(c, actor) if actor else 'Tico'}: {what}")
    return out


def snapshot(c: Any, auth: Any, person: Any) -> list[str]:
    """What waits on the person now and the open tasks their Assistant filed for them, as they may read them."""
    from . import views as _views

    views: Any = _views
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

    found: list[Row] = [dict(r) for r in c.execute(HANDOFFS_SQL, (person.actor, FETCH_MAX))]
    handed = [r for r in found if opens(r["id"])]
    lines: list[str] = []
    for r in handed[:TOP]:
        wait = W.headline(c, r["id"])
        said = W.unnamed(wait, opens).line if wait else ""
        owner: str = H.actor_name(c, r["owner_id"]) if r["owner_id"] else "nobody"
        lines.append(f"{_clip(r['title'] or 'A task', TITLE_MAX)} ({owner})" + (f": {said}" if said else ""))
    out += _listed("Open handoffs you filed through me:", lines, len(handed))
    return out


def _listed(head: str, items: Sequence[str], total: int | None = None) -> list[str]:
    """`head` and the first TOP items, then "+N more"; nothing when there are none."""
    total = len(items) if total is None else total
    if not total:
        return []
    out = [head, *(f"- {item}" for item in items[:TOP])]
    if total > TOP:
        out.append(f"+{total - TOP} more")
    return out


def _readable(c: Any, auth: Any, who: Any, task_id: str) -> bool:
    try:
        auth.task(c, who, task_id, tags=False)
        return True
    except Problem:
        return False


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
