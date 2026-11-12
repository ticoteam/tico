"""The Tasks page's search and filters, answered on the server (perf I16).

A search or a filter on a pipeline or the archive used to read every page of the view into the browser and match
there (ui/app/task-row.ts `taskFilterMatch`). `GET /api/v2/tasks` now takes the same search and filters with the same
meaning, and answers one bounded keyset page of matches (`after`, hubdb.KEYSET_SQL):

- `search`: every word (a leading # dropped) in the title, the task number or a label, case-insensitively. Text is
  lowered in Python, as the browser lowers it (SQLite's lower() knows only ASCII), over the view's narrow columns.
- `owners`, `stages`, `requesters`, `labels`: one or several values each, comma-separated; any of them.
- `turn`: one or several people or bots whose turn it is, the list row's headline wait (`turn.who`), as Waiting on
  matches on the page. Worked out with the list's own rule (task_reads.brief), for the open tasks the other filters
  leave.
- `due`: overdue, week (due within 7 days, past due included) or any (has a date). `late`: an open task late in its
  stage (`late` on a list row). Pushed past is the existing `pushed_past=true`.

Each filter either adds a WHERE fragment over the task's own columns, under the reader's visibility, or keeps a set of
ids; a task the reader may not read is never a candidate, so it is in no page and no `next_after`.
"""

import json
import re
from dataclasses import dataclass, field

from .store import Problem

DUE = ("overdue", "week", "any")
MAX_VALUES = 200


def _values(raw, name):
    if raw is None:
        return None
    out = [v.strip() for v in str(raw).split(",") if v.strip()]
    if len(out) > MAX_VALUES:
        raise Problem(name, f"{name} takes at most {MAX_VALUES} values", 422)
    return out


def words(search):
    """The search's words as the page splits and lowers them: a leading # dropped from each."""
    return [w.lower().removeprefix("#") for w in re.split(r"\s+", str(search or "").lower()) if w]


def text_hit(found, title, number, labels):
    """Whether every word is in the title, the number or a label (task-row.ts `taskFilterMatch`)."""
    hay = f"{title or ''}  {'' if number is None else number} {' '.join(labels)}".lower()
    return all(w in hay for w in found)


@dataclass
class Match:
    sql: list = field(default_factory=list)  # (fragment, args) over the task's own columns
    words: list = field(default_factory=list)
    turn: list | None = None

    def __bool__(self):
        return bool(self.sql or self.words or self.turn is not None)


def parse(
    c,
    now,
    search="",
    owners=None,
    stages=None,
    requesters=None,
    labels=None,
    turn=None,
    due=None,
    late=None,
    hidden=frozenset(),
):
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
        m.sql.append(
            (
                "status='open' AND EXISTS (SELECT 1 FROM visits v WHERE v.task_id=tasks.id AND v.left_at IS NULL "
                "AND v.late_told_at IS NOT NULL)",
                [],
            )
        )
    return m


def kept(c, m, candidates, visible_sql):
    """Of `candidates` ([(id, title, number)], the rows the SQL filters leave), the ids the search and Waiting on keep."""
    from . import task_reads

    ids = [row[0] for row in candidates]
    if m.words:
        labels = {tid: [] for tid in ids}
        for part in _chunks(ids):
            for tid, key in c.execute(
                "SELECT task_tags.task_id, tags.key FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                "WHERE task_tags.task_id IN (SELECT value FROM json_each(?))",
                (json.dumps(part),),
            ):
                labels[tid].append(key)
        ids = [tid for tid, title, number in candidates if text_hit(m.words, title, number, labels[tid])]
    if m.turn is not None:
        wanted, out = set(m.turn), []
        for part in _chunks(ids):
            rows = task_reads.brief(c, [{"id": tid} for tid in part], visible_sql)
            out += [row["id"] for row in rows if row["turn"] and row["turn"].get("who") in wanted]
        ids = out
    return ids


def _chunks(ids, size=500):
    for start in range(0, len(ids), size):
        yield ids[start : start + size]
