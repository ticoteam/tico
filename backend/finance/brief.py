"""What changed: the Finance briefing a reader is handed (plan v2, step 4b).

A few short lines about the money a reader may see: the budgets that are theirs, their team's, the over-pace teammates
on their team, and, where they see the company, cash, burn, ad spend, Watch findings and unattributed spend. Every
line is built from a read that already cuts to the reader (`reads.budgets`, `reads.metric`, `reads.spend_by`,
`reads.watch_findings`), so nothing here shows a number the reader's own Finance page would not.

Shape. `compute` reads the lines now: at most MAX_LINES, each cut to LINE_CHARS, none when the reader has nothing there,
and none for a reader with no Finance view. `brief` keeps one row per reader in `fin_briefs` (see
migrations/20261107074031_finance_brief.sql): a fingerprint of the lines. Same fingerprint keeps the stored `as_of`;
when the lines change, `as_of` moves to now and `changed_since` takes the `as_of` the previous lines had. So the people
page and the bots' turn start see the same brief, and a brief is re-issued only when its numbers move.

Who gets it. `for_turn` is the turn-start hand-off (backend/execution.py): the lines a bot or a person's Assistant turn
reads, or None. The runner renders them as standing lines (runner-rs/src/prompt.rs), which a resumed thread receives
again only when they change. `hub_finance_brief` and GET /api/v2/finance/brief return the same `brief`.

Later steps hook in at `_later_lines`: campaign and receipt lines (steps 5 and 7) add nothing yet.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any, cast

from .. import hubdb as H
from . import reads as R
from .core.errors import FinanceError

MAX_LINES = 6  # a briefing is a few lines, not a report
LINE_CHARS = 100  # one short line each; the page and the prompt never carry a paragraph
Row = dict[str, Any]


def _int(value: object) -> int:
    """A figure the reads hand back as an int or None (a line with nothing to say counts as 0)."""
    return int(cast(int, value or 0))


def _money(minor: int) -> str:
    """Whole dollars, with a k past a thousand: $412k, $3,000, $140."""
    dollars = abs(int(minor)) / 100
    if dollars >= 100_000:
        return f"${dollars / 1000:,.0f}k"
    return f"${dollars:,.0f}"


def _short(text: str) -> str:
    return text if len(text) <= LINE_CHARS else text[: LINE_CHARS - 1].rstrip() + "…"


def _who(actor: str) -> str:
    """The name a teammate's id reads as in a line: the slug or person id after the actor's prefix."""
    return str(actor or "").split(":", 1)[-1] or "someone"


def _budget_line(row: Row, label: str) -> str:
    st = row["state"]
    amount, actual, pace = int(st["amount"] or 0), st["actual"], st["pace_end"]
    text = f"{label}: {row['name']} · {_money(amount)}"
    if actual is not None:
        pct = st.get("pct")
        text += f". Spent {_money(int(actual))}" + (f" ({float(pct):.0f}%)" if pct not in (None, "") else "")
    if pace is not None:
        text += f". Pace {_money(int(pace))}" + (", over budget." if int(pace) > amount else ".")
    return _short(text)


def _over_pace(row: Row) -> bool:
    st = row["state"]
    return st["pace_end"] is not None and int(st["pace_end"]) > int(st["amount"] or 0)


def _budget_lines(c: sqlite3.Connection, reader: str) -> list[str]:
    """The reader's own budgets, then their team's, then over-pace teammates (a manager sees them), then the ad total."""
    try:
        rows = cast(list[Row], R.budgets(c, reader, limit=500)["budgets"])
    except FinanceError:
        return []
    # Theirs: a budget on their own name, or a company budget with no team or teammate (what the owner reads as theirs).
    own = [r for r in rows if r["teammate"] == reader or (not r["teammate"] and not r["team"])]
    team = [r for r in rows if r["team"] and not r["teammate"]]
    others = [r for r in rows if r["teammate"] and r["teammate"] != reader and _over_pace(r)]
    ads = [r for r in rows if r.get("platform") and r["state"]["amount"]]
    lines = [_budget_line(r, "Budget") for r in own[:2]]
    lines += [_budget_line(r, "Team") for r in team[:1]]
    lines += [
        _short(
            f"Over pace: {_who(r['teammate'])} ({r['name']}: {_money(int(r['state']['actual'] or 0))} "
            f"vs {_money(int(r['state']['amount']))})"
        )
        for r in others
        if r["state"]["actual"] is not None
    ]
    if len(ads) >= 2:
        amount = sum(int(r["state"]["amount"] or 0) for r in ads)
        actual = sum(int(r["state"]["actual"] or 0) for r in ads if r["state"]["actual"] is not None)
        pace = sum(int(r["state"]["pace_end"] or 0) for r in ads if r["state"]["pace_end"] is not None)
        lines.append(_short(f"Ad spend {_money(actual)} this month, pace {_money(pace)} vs {_money(amount)} budget."))
    # Budgets take at most three lines, so the company's lines (cash, burn, unattributed) still fit under MAX_LINES.
    return lines[:3]


def _company_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """Cash, burn, Watch, unattributed spend and the bank sync time. Each is left out when the reader's view does not
    cover it: cash and burn need the balance sheet (the owner's unless granted), Unattributed the expense lines."""
    out: list[str] = []
    cash: Row | None = None
    try:
        cash = R.metric(c, reader, "cash", at=at)
        out.append(f"Cash {_money(_int(cash['value']))}.")
        burn = R.metric(c, reader, "burn", at=at)
        out.append(f"Burn {_money(_int(burn['value']))} this month.")
    except FinanceError:
        cash = None
    if cash is not None:
        try:
            findings = R.watch_findings(c, reader, at)
        except FinanceError:
            findings = []
        if findings:
            out.append(f"Watch: {len(findings)} finding{'' if len(findings) == 1 else 's'}.")
    try:
        today = date.fromisoformat(at[:10])
        spent = R.spend_by(c, reader, "team", today.replace(day=1).isoformat(), today.isoformat(), top_n=500, at=at)
        loose = next((_int(r["amount"]) for r in cast(list[Row], spent["rows"]) if r["key"] == R.NONE), 0)
        if loose:
            out.append(f"Unattributed: {_money(loose)} this month.")
    except FinanceError:
        pass
    synced = str((cash or {}).get("data_as_of") or "")  # the bank data's time (reads.metric data_as_of)
    if synced:
        out.append(f"Bank data synced {synced[11:16]} UTC" + (", stale." if cash and cash.get("stale") else "."))
    return out


def _later_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """Campaign and receipt lines (plan steps 5 and 7) arrive here; until then, none."""
    return []


def compute(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """The lines this reader may see, read now: [] when they have no Finance view at all."""
    lines = _budget_lines(c, reader)
    lines += _company_lines(c, reader, at)
    lines += _later_lines(c, reader, at)
    return [_short(line) for line in lines][:MAX_LINES]


def fingerprint(lines: list[str]) -> str:
    return hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()


def brief(c: sqlite3.Connection, reader: str, at: str | None = None) -> Row:
    """The reader's brief: {lines, as_of, changed_since, version}. Writes `fin_briefs` (one row per reader) inside the
    caller's transaction, so call it in a write transaction."""
    now = at or H.now()
    lines = compute(c, reader, now)
    digest = fingerprint(lines)
    version = R.view_version(c)
    row = c.execute("SELECT * FROM fin_briefs WHERE reader=?", (reader,)).fetchone()
    if row is not None and row["fingerprint"] == digest:
        as_of, changed_since = row["as_of"], row["changed_since"]
    else:
        as_of = now
        changed_since = row["as_of"] if row is not None else None
        c.execute(
            "INSERT INTO fin_briefs(reader, fingerprint, lines, as_of, changed_since, view_version, created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(reader) DO UPDATE SET fingerprint=excluded.fingerprint, "
            "lines=excluded.lines, as_of=excluded.as_of, changed_since=excluded.changed_since, "
            "view_version=excluded.view_version",
            (reader, digest, json.dumps(lines, ensure_ascii=False), as_of, changed_since, version, now),
        )
    return {"lines": lines, "as_of": as_of, "changed_since": changed_since, "version": version}


def for_turn(c: sqlite3.Connection, reader: str) -> Row | None:
    """The turn-start hand-off (backend/execution.py): the brief's lines for this reader, or None when there are none."""
    got = brief(c, reader)
    if not got["lines"]:
        return None
    return {"lines": got["lines"], "as_of": got["as_of"], "changed_since": got["changed_since"]}
