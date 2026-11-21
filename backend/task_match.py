# pyright: strict
"""The Tasks page's search and filters, answered on the server (perf I16).

A search or a filter on a pipeline or the archive used to read every page of the view into the browser and match
there (ui/app/task-row.ts `taskFilterMatch`). `GET /api/v2/tasks` now takes the same search and filters with the same
meaning, and answers one bounded keyset page of matches (`after`, hubdb.KEYSET_SQL):

- `search`: every word (a leading # dropped) in the title, the task number or a label, case-insensitively; the first
  50 words, a bound the page's search box never reaches in practice. Text is
  lowered in Python, as the browser lowers it (SQLite's lower() knows only ASCII), over the view's narrow columns.
- `owners`, `stages`, `requesters`, `labels`: one or several values each, comma-separated; any of them.
- `turn`: one or several people or bots whose turn it is, the list row's headline wait (`turn.who`, any approver of a
  page several people approve: waiting_on.heads), as Waiting on
  matches on the page. Worked out with the list's own rule (task_reads.brief), for the open tasks the other filters
  leave.
- `due`: overdue, week (due within 7 days, past due included) or any (has a date). `late`: an open task late in its
  stage (`late` on a list row). Pushed past is the existing `pushed_past=true`.

Each filter either adds a WHERE fragment over the task's own columns, under the reader's visibility, or keeps a set of
ids; a task the reader may not read is never a candidate, so it is in no page and no `next_after`.
"""

import json
import re
import sqlite3
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from .store import Problem

DUE = ("overdue", "week", "any")
MAX_VALUES = 200


def _values(raw: str | None, name: str) -> list[str] | None:
    if raw is None:
        return None
    out = [v.strip() for v in str(raw).split(",") if v.strip()]
    if len(out) > MAX_VALUES:
        raise Problem(name, f"{name} takes at most {MAX_VALUES} values", 422)
    return out


def words(search: str | None) -> list[str]:
    """The search's words as the page splits and lowers them: a leading # dropped from each."""
    return [w.lower().removeprefix("#") for w in re.split(r"\s+", str(search or "").lower()) if w]


def text_hit(found: Sequence[str], title: str | None, number: int | None, labels: Sequence[str]) -> bool:
    """Whether every word is in the title, the number or a label (task-row.ts `taskFilterMatch`)."""
    # Two spaces: the page's haystack is `${title} ${body} ${number}`, and list rows carry no body.
    hay = f"{title or ''}  {'' if number is None else number} {' '.join(labels)}".lower()
    return all(w in hay for w in found)


@dataclass
class Match:
    sql: list[tuple[str, list[Any]]] = field(default_factory=lambda: [])  # (fragment, args) over the task's columns
    words: list[str] = field(default_factory=lambda: [])
    turn: list[str] | None = None
    late: bool = False  # the Late filter: `kept` drops a bot's task whose turn is a person's (late_rule.late_of)

    def __bool__(self) -> bool:
        return bool(self.sql or self.words or self.turn is not None)


def parse(
    now: str,
    search: str = "",
    owners: str | None = None,
    stages: str | None = None,
    requesters: str | None = None,
    labels: str | None = None,
    turn: str | None = None,
    due: str | None = None,
    late: bool | None = None,
    hidden: Collection[str] = (),
) -> Match:
    """The page's filters as a Match; empty (falsy) when none is set. `hidden` are the people this reader may not
    see (teammate_access.Known): the page shows their ids as blank, so a filter on one matches nothing there."""
    m = Match(words=words(search)[:50])
    for name, raw, column in (("owners", owners, "owner_id"), ("requesters", requesters, "requester_id")):
        values = _values(raw, name)
        if values is not None:
            values = [v for v in values if v not in hidden]
            m.sql.append((f"{column} IN (SELECT value FROM json_each(?))", [json.dumps(values)]))
    values = _values(stages, "stages")
    if values is not None:
        m.sql.append(("stage_id IN (SELECT value FROM json_each(?))", [json.dumps(values)]))
    values = _values(labels, "labels")
    if values is not None:
        m.sql.append(
            (
                "EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id WHERE task_tags.task_id=tasks.id "
                "AND tags.key IN (SELECT value FROM json_each(?)))",
                [json.dumps(values)],
            )
        )
    if turn is not None:
        m.turn = [v for v in _values(turn, "turn") or [] if v not in hidden]
        m.sql.append(("status='open'", []))
    if due is not None:
        if due not in DUE:
            raise Problem("due", "due is overdue, week or any", 422)
        # A date the browser can read: julianday reads the ISO forms tasks keep, with Z or an offset, as one instant.
        m.sql.append(("julianday(due_at) IS NOT NULL", []))
        if due == "overdue":
            m.sql.append(("julianday(due_at) < julianday(?)", [now]))
        elif due == "week":
            m.sql.append(("julianday(due_at) <= julianday(?) + 7", [now]))
    if late:
        # late_rule.is_late, in SQL: a person's own task by its due date, any other by its stage's expected time.
        m.sql.append(
            (
                "status='open' AND EXISTS (SELECT 1 FROM visits v JOIN stages s ON s.id=v.stage_id "
                "WHERE v.task_id=tasks.id AND v.left_at IS NULL AND v.late_told_at IS NOT NULL AND ("
                "(COALESCE(tasks.owner_id, '') LIKE 'human:%' AND julianday(tasks.due_at) IS NOT NULL "
                "AND julianday(?) >= julianday(tasks.due_at) + (CASE WHEN length(tasks.due_at)=10 THEN 1 ELSE 0 END)) "
                "OR (COALESCE(tasks.owner_id, '') NOT LIKE 'human:%' AND COALESCE(s.expected_minutes, 0) > 0 "
                "AND julianday(?) - julianday(v.entered_at) >= COALESCE(s.expected_minutes, 0) / 1440.0 "
                # A person answered after the mark: the bot's clock started again (never_stuck.clock_from).
                "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.visit_id=v.id AND a.by_id LIKE 'human:%' "
                "AND a.created_at > v.late_told_at) "
                "AND NOT EXISTS (SELECT 1 FROM question_marks q WHERE q.task_id=tasks.id "
                "AND q.cleared_at IS NOT NULL AND q.cleared_at > v.late_told_at))))",
                [now, now],
            )
        )
        m.late = True
    return m


def kept(c: sqlite3.Connection, m: Match, candidates: Sequence[Sequence[Any]], visible_sql: str) -> list[str]:
    """Of `candidates` ([(id, title, number)], the rows the SQL filters leave), the ids the search, Late and Waiting on
    keep."""
    from . import task_reads

    reads: Any = task_reads  # an untyped module
    ids: list[str] = [str(row[0]) for row in candidates]
    if m.words:
        labels: dict[str, list[str]] = {tid: [] for tid in ids}
        for part in _chunks(ids):
            for tid, key in c.execute(
                "SELECT task_tags.task_id, tags.key FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                "WHERE task_tags.task_id IN (SELECT value FROM json_each(?))",
                (json.dumps(part),),
            ):
                labels[str(tid)].append(str(key))
        ids = [str(tid) for tid, title, number in candidates if text_hit(m.words, title, number, labels[str(tid)])]
    if m.late:
        # A person's turn is never Late, which only the task's waits say: the list row's own rule decides.
        late: list[str] = []
        for part in _chunks(ids):
            found = c.execute(
                "SELECT id, owner_id, due_at FROM tasks WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(part),),
            ).fetchall()
            mine = [{"id": tid, "owner_id": owner, "due_at": due} for tid, owner, due in found]
            kept_ids = {
                row["id"] for row in cast(list[dict[str, Any]], reads.brief(c, mine, visible_sql)) if row.get("late")
            }
            late += [tid for tid in part if tid in kept_ids]
        ids = late
    if m.turn is not None:
        wanted: set[str] = set(m.turn)
        out: list[str] = []
        from . import waiting_on as W
        from .store import H

        for part in _chunks(ids):
            # Each person a page several people approve is asked of has its turn alike (waiting_on.heads); a sub-task
            # or blocker the reader may not open is said without its id, as on the board (waiting_on.unnamed).
            waits = cast(dict[str, list[Any]], H.task_waits(c, part))
            found = {tid: W.heads(waits.get(tid) or []) for tid in part}
            named = sorted({w.about for ws in found.values() for w in ws if w.about})
            shown = {
                str(r[0])
                for r in c.execute(
                    f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
                    (json.dumps(named),),
                )
            }
            out += [tid for tid in part if any(W.unnamed(w, shown.__contains__).who in wanted for w in found[tid])]
        ids = out
    return ids


def _chunks(ids: list[str], size: int = 500) -> Iterator[list[str]]:
    for start in range(0, len(ids), size):
        yield ids[start : start + size]
