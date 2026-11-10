# pyright: strict
"""Jev's verdicts on pages: one per page version and rubric (docs/pipelines-v3.md "The nine requirement kinds", 3).

A page attached to a page requirement (backend/task_pages.py) is owed a verdict. The Jev step on the Heartbeat (`run`)
judges what is owed through `pipeline_judges.judge_once`, with no write transaction held during the model call, and
records each answer here. The page requirement reads the verdict (`task_requirements._page`); it never asks Jev.

- The key is (file id, version, `rubric_hash`). `rubric_hash` covers the rubric and everything else Jev is shown beside
  the page (the commit a review is for, the release a Validate page is checked against), so a change to any of them is
  a new verdict, and the same page judged the same way is judged once.
- `verdict` is NULL while owed, then `met`, `not_met` or `unsure` (pipeline_judges' bands), or `failed` when every one
  of TRIES calls failed: a person decides ("Jev couldn't check this").
- A failed call is not a verdict. It counts a try and sets `next_try_at` RETRY_S later.

Every function takes the caller's open write transaction and never commits, except `run`.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

VERDICTS = ("met", "not_met", "unsure", "failed")
TRIES = 3  # failed calls before a person decides
RETRY_S = (60, 300, 900)  # wait after the first, second and third failed call


def _h() -> Any:
    from . import hubdb

    return hubdb


def rubric_hash(rubric: str, context: Row | None = None) -> str:
    """The key for what a page is judged against: the rubric and the context Jev is shown beside the page (JSON-able,
    key order does not matter)."""
    text = json.dumps({"rubric": rubric.strip(), "context": context or {}}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _one(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> Row | None:
    cursor = conn.execute(sql, args)
    found = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], found, strict=True)) if found else None


def get(conn: "sqlite3.Connection", file_id: str, version: int, key: str) -> Row | None:
    """The verdict row for a page version and rubric hash, or None when none is owed yet."""
    return _one(
        conn,
        "SELECT * FROM page_verdicts WHERE file_id=? AND version=? AND rubric_hash=?",
        (file_id, int(version), key),
    )


def owe(conn: "sqlite3.Connection", file_id: str, version: int, key: str) -> Row:
    """The verdict row for a page version and rubric hash, made owed (due now) when there is none. Attaching a page calls
    it, so the Jev step finds the page without reading every requirement."""
    found = get(conn, file_id, version, key)
    if found:
        return found
    H = _h()
    now = H.now()
    conn.execute(
        "INSERT INTO page_verdicts (id, file_id, version, rubric_hash, next_try_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), file_id, int(version), key, now, now, now),
    )
    made = get(conn, file_id, version, key)
    assert made is not None
    return made


def record(conn: "sqlite3.Connection", verdict_id: str, result: Row, *, failed: bool = False) -> Row:
    """Record one Jev call on an owed verdict. `result` is `pipeline_judges.judge_once`'s. With `failed` (the call
    itself failed: no model, an error, over the Decide cap) a try is counted and the verdict stays owed until TRIES,
    then it is `failed`. Otherwise its verdict, p, reason, model and cost are kept. A verdict already given is left as
    it is."""
    H = _h()
    now = H.now()
    row = _one(conn, "SELECT * FROM page_verdicts WHERE id=?", (verdict_id,))
    if row is None:
        raise KeyError(verdict_id)
    if row["verdict"] is not None:
        return row
    cost = float(row["cost_usd"] or 0) + float(result.get("cost_usd") or 0)
    if failed:
        tries = int(row["tries"]) + 1
        gave_up = tries >= TRIES
        conn.execute(
            "UPDATE page_verdicts SET tries=?, last_error=?, cost_usd=?, verdict=?, next_try_at=?, updated_at=? "
            "WHERE id=?",
            (
                tries,
                str(result.get("reason") or "The call failed"),
                cost,
                "failed" if gave_up else None,
                None if gave_up else H.shift(now, seconds=RETRY_S[min(tries, len(RETRY_S)) - 1]),
                now,
                verdict_id,
            ),
        )
    else:
        verdict = str(result.get("verdict") or "unsure")
        if verdict not in VERDICTS[:3]:
            verdict = "unsure"
        conn.execute(
            "UPDATE page_verdicts SET verdict=?, p=?, reasons=?, model=?, cost_usd=?, tries=tries+1, next_try_at=NULL, "
            "updated_at=? WHERE id=?",
            (verdict, result.get("p"), str(result.get("reason") or ""), result.get("model"), cost, now, verdict_id),
        )
    out = _one(conn, "SELECT * FROM page_verdicts WHERE id=?", (verdict_id,))
    assert out is not None
    return out


def owed(conn: "sqlite3.Connection", now: str, limit: int = 20) -> list[Row]:
    """The verdicts still owed whose next try is due, oldest first."""
    cursor = conn.execute(
        "SELECT * FROM page_verdicts WHERE verdict IS NULL AND (next_try_at IS NULL OR next_try_at <= ?) "
        "ORDER BY created_at, id LIMIT ?",
        (now, int(limit)),
    )
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def run(store: Any, now: str | None = None) -> int:
    """The Jev step on the Heartbeat: read what is `owed` in a short read, judge each with no transaction held
    (pipeline_judges.judge_once, Decide's cap checked), then `record` each in its own short write and nudge its tasks
    (task_engine.changed / advance). Returns how many verdicts it gave. Track B."""
    raise NotImplementedError("page_verdicts.run: track B")
