# pyright: strict
"""A page that comes back: its rounds, and what a bot says with each new version (spec "Decision view: a page that
comes back", #263).

- At attach (backend/task_pages.py), a bot may say what it changed (`changed`, up to CHANGED_MAX one-line bullets) and
  what it did with each point of the person's last send back (`answered`, up to ANSWERED_MAX entries, each
  "<point>: done|not done, <why>"), beside its short `note`. They are kept on the `page_attached` event's data. Tico
  never splits a person's note into points: the bot does.
- A note or line that repeats QUOTE_WORDS or more words in a row from the person's last send back is refused
  (`quoted_back`): say what was done with each point instead. No model call.
- `rounds` is the page's history on one task and requirement, oldest first: each version attached, when and by whom,
  what the bot said with it, Jev's verdict on it, and each person's answer to it (OK'd or sent back, with the note).
  The decision view draws its right side and its review history from it.

Every function takes the caller's open connection and never commits.
"""

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

CHANGED_MAX = 5
CHANGED_CHARS = 120
ANSWERED_MAX = 12
ANSWERED_CHARS = 300
NOTE_CHARS = 500
QUOTE_WORDS = 12
ANSWERED_FORM = '--answered takes "<point>: done|not done, <why>"'
QUOTED = "Don't quote their note back; say what you did with each point (--answered)"
# The point is everything before the last ": done," or ": not done,"; the why is what follows.
_ANSWERED = re.compile(r"^(?P<point>.+):\s*(?P<state>not done|done)\s*,\s*(?P<why>\S.*)$", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[\w']+")


def parse_answered(entry: str) -> Row | None:
    """One `--answered` entry as {point, done, why}, or None when it is not "<point>: done|not done, <why>"."""
    found = _ANSWERED.match(" ".join(str(entry or "").split()))
    if not found or not found.group("point").strip():
        return None
    return {
        "point": found.group("point").strip(),
        "done": found.group("state").lower() == "done",
        "why": found.group("why").strip(),
    }


def said(note: str | None, changed: list[str] | None, answered: list[str] | None) -> tuple[Row, str | None]:
    """What a bot says with a new version, checked: ({note, changed, answered}, None), or ({}, the sentence it is
    refused with). Each part is optional; an older client sends none."""
    text = " ".join(str(note or "").split())
    if len(text) > NOTE_CHARS:
        return {}, f"A note on a page is at most {NOTE_CHARS} characters"
    lines = [" ".join(str(line).split()) for line in changed or []]
    lines = [line for line in lines if line]
    if len(lines) > CHANGED_MAX:
        return {}, f"--changed takes at most {CHANGED_MAX} lines; keep the {CHANGED_MAX} that matter most"
    if any("\n" in str(line) for line in changed or []) or any(len(line) > CHANGED_CHARS for line in lines):
        return {}, f"Each --changed line is one line of at most {CHANGED_CHARS} characters"
    entries = [str(entry) for entry in answered or [] if str(entry).strip()]
    if len(entries) > ANSWERED_MAX:
        return {}, f"--answered takes at most {ANSWERED_MAX} points"
    points: list[Row] = []
    for entry in entries:
        point = parse_answered(entry)
        if point is None or len(entry) > ANSWERED_CHARS:
            return {}, f"{ANSWERED_FORM}, at most {ANSWERED_CHARS} characters each. Not: {entry[:80]}"
        points.append(point)
    return {"note": text, "changed": lines, "answered": points}, None


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text or "")]


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + QUOTE_WORDS]) for i in range(len(words) - QUOTE_WORDS + 1)}


def quoted_back(sent_back: str | None, parts: list[str]) -> bool:
    """Whether any part (a note, a changed line, an answered entry) repeats QUOTE_WORDS or more words in a row from
    the person's send-back note. Case and punctuation are ignored."""
    theirs = _shingles(_words(sent_back or ""))
    return bool(theirs) and any(_shingles(_words(part)) & theirs for part in parts)


def parts_of(saying: Row) -> list[str]:
    """Each piece of text a bot says with a version, for `quoted_back`."""
    answered: list[Row] = saying.get("answered") or []
    return [
        str(saying.get("note") or ""),
        *[str(line) for line in saying.get("changed") or []],
        *[f"{p['point']}: {p['why']}" for p in answered],
    ]


def last_sent_back(conn: "sqlite3.Connection", task_id: str, requirement_id: str) -> str | None:
    """The newest send-back note on this page requirement of the task, any visit, not undone."""
    found = conn.execute(
        "SELECT a.notes FROM answers a JOIN visits v ON v.id = a.visit_id WHERE v.task_id=? AND a.requirement_id=? "
        "AND a.answer='no' AND a.undone_at IS NULL ORDER BY a.created_at DESC, a.id DESC LIMIT 1",
        (task_id, requirement_id),
    ).fetchone()
    return str(found[0]) if found and found[0] else None


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def _jev(verdict: Row | None) -> Row:
    """Jev's verdict as the decision view draws it: no p-value, only the verdict and its reason."""
    if not verdict or verdict.get("verdict") is None:
        return {"verdict": None, "reason": ""}
    said_ = str(verdict["verdict"])
    reason = " ".join(str(verdict.get("last_error") if said_ == "failed" else verdict.get("reasons") or "").split())
    if said_ == "failed":
        reason = f"Jev couldn't check this: {reason}" if reason else "Jev couldn't check this"
    return {"verdict": said_, "reason": reason}


def rounds(conn: "sqlite3.Connection", task_id: str, requirement_id: str) -> list[Row]:
    """Each version of the page attached to this requirement of the task, oldest first: {file_id, version, at, by,
    replaced, note, changed, answered, jev: {verdict, reason}, answers: [{by, answer, note, at}]}. `answers` are the
    people's and bots' OKs (`ok`) and send backs (`no`) given while that version counted, not undone."""
    from . import page_verdicts as V

    pages = _rows(
        conn,
        "SELECT p.* FROM requirement_pages p JOIN visits v ON v.id = p.visit_id WHERE v.task_id=? "
        "AND p.requirement_id=? ORDER BY p.created_at, p.id",
        (task_id, requirement_id),
    )
    if not pages:
        return []
    events = _rows(
        conn,
        "SELECT data FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='page_attached' "
        "ORDER BY created_at, id",
        (task_id,),
    )
    saying: dict[tuple[str, int], Row] = {}
    for event in events:
        try:
            data: Row = json.loads(event["data"] or "{}")
        except ValueError:
            continue
        if data.get("requirement") == requirement_id and data.get("file_id"):
            saying[(str(data["file_id"]), int(data.get("version") or 0))] = data
    answers = _rows(
        conn,
        "SELECT a.by_id, a.answer, a.notes, a.page_version, a.created_at, a.visit_id FROM answers a "
        "JOIN visits v ON v.id = a.visit_id WHERE v.task_id=? AND a.requirement_id=? AND a.undone_at IS NULL "
        "AND a.answer IN ('ok', 'no') ORDER BY a.created_at, a.id",
        (task_id, requirement_id),
    )
    # The same version attached again (a new commit to judge it against) is one round: from its first attach, with
    # its newest verdict.
    runs: list[tuple[str, Row]] = []
    for page in pages:
        if runs and (runs[-1][1]["file_id"], runs[-1][1]["version"]) == (page["file_id"], page["version"]):
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((str(page["created_at"]), page))
    out: list[Row] = []
    for index, (start, page) in enumerate(runs):
        end = runs[index + 1][0] if index + 1 < len(runs) else None
        version = int(page["version"])
        given = [
            {"by": a["by_id"], "answer": a["answer"], "note": a["notes"] or "", "at": a["created_at"]}
            for a in answers
            if a["page_version"] == version
            and str(a["created_at"]) >= start
            and (end is None or str(a["created_at"]) < end)
        ]
        data = saying.get((str(page["file_id"]), version)) or {}
        verdict = V.get(conn, page["file_id"], version, page.get("rubric_hash") or "")
        out.append(
            {
                "file_id": page["file_id"],
                "version": version,
                "at": page["created_at"],
                "by": page["attached_by_id"],
                "replaced": bool(page.get("replaced_at")),
                "note": str(data.get("note") or ""),
                "changed": list(data.get("changed") or []),
                "answered": list(data.get("answered") or []),
                "jev": _jev(verdict),
                "answers": given,
            }
        )
    return out
