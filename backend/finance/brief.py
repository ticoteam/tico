"""What changed: the Finance briefing a reader is handed (plan v2, step 4b).

A few short lines about the money a reader may see: the budgets that are theirs, their team's, the over-pace teammates
on their team, and, where they see the company, cash, burn, Watch findings and unattributed spend. Every line is built
from a read that already cuts to the reader (`reads.budgets`, `reads.metric`, `reads.watch_findings`, and the expense
rollup rows the reader's view covers), so nothing here shows a number the reader's own Finance page would not.

Shape. `compute` reads the lines now: at most MAX_LINES, each cut to LINE_CHARS, none when the reader has nothing there,
and none for a reader with no Finance view at all. `brief` keeps one row per reader in `fin_briefs` (see
migrations/20261107074031_finance_brief.sql): a fingerprint of the lines. Same fingerprint keeps the stored `as_of`;
when the lines change, `as_of` moves to now and `changed_since` takes the `as_of` the previous lines had. So the people
page and the bots' turn start see the same brief, and a brief is re-issued only when its numbers move.

Who gets it. `for_turn` is the turn-start hand-off (backend/execution.py): the lines a bot, or a person's Assistant
turn, is handed, or None. It hands lines only when they differ from the fingerprint handed at this reader's last turn
start (`delivered_fingerprint`); when they do not, it reads and compares and writes nothing. The runner renders the lines
as standing lines (runner-rs/src/prompt.rs). `tico_finance_brief` and GET /api/v2/finance/brief return `brief` on demand
and never move the delivered fingerprint.

Later steps hook in at `_later_lines`: subscription lines (step 6, subscriptions.py) and the receipt line (step 7,
receipts.brief_lines) are in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import date
from typing import Any, cast

from .. import hubdb as H
from . import receipts as RCP
from . import reads as R
from . import subscriptions as SUB
from .context import Ctx, is_set_up, load
from .core import dates
from .core.errors import FinanceError
from .core.view import View
from .store import access as SA
from .store import ledger as SL

MAX_LINES = 6  # a briefing is a few lines, not a report
LINE_CHARS = 100  # one short line each; the page and the prompt never carry a paragraph
Row = dict[str, Any]
log = logging.getLogger(__name__)
PACE = re.compile(r"[Pp]ace \$[\d,.]+k?")  # the pace figure, which moves with the clock (see fingerprint)


def _int(value: object) -> int:
    """A figure the reads hand back as an int or None (a line with nothing to say counts as 0)."""
    return int(cast(int, value or 0))


def _money(minor: int) -> str:
    """Whole dollars, with a k past a hundred thousand: $412k, $3,000, $140."""
    dollars = abs(int(minor)) / 100
    if dollars >= 100_000:
        return f"${dollars / 1000:,.0f}k"
    return f"${dollars:,.0f}"


def _short(text: str) -> str:
    return text if len(text) <= LINE_CHARS else text[: LINE_CHARS - 1].rstrip() + "…"


def _who(actor: str) -> str:
    """The name a teammate's id reads as in a line: the slug or person id after the actor's prefix."""
    return str(actor or "").split(":", 1)[-1] or "someone"


def _lines_word(n: int) -> str:
    return f"{n} line" if n == 1 else f"{n} lines"


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
    """A budget whose pace at its rate so far ends past its amount (a cut or unpaced figure, None, is never over)."""
    st = row["state"]
    return st["pace_end"] is not None and int(st["pace_end"]) > int(st["amount"] or 0)


def _budget_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """The reader's own budgets, then their team's, then over-pace teammates (a manager sees them), then the ad total. A
    budget with no amount and no spend (a placeholder nobody set) says nothing."""
    try:
        rows = cast(list[Row], R.budgets(c, reader, limit=500, at=at)["budgets"])
    except FinanceError:
        return []
    # Theirs: a budget on their own name, or a company budget with no team or teammate (what the owner reads as theirs).
    rows = [r for r in rows if int(r["state"]["amount"] or 0) or int(r["state"]["actual"] or 0)]
    own = [r for r in rows if r["teammate"] == reader or (not r["teammate"] and not r["team"])]
    team = [r for r in rows if r["team"] and not r["teammate"]]
    others = [r for r in rows if r["teammate"] and r["teammate"] != reader and _over_pace(r)]
    ads = [r for r in rows if r.get("platform") and r["state"]["amount"]]
    lines = [_budget_line(r, "Budget") for r in own[:2]]
    lines += [_budget_line(r, "Team") for r in team[:1]]
    lines += [
        _short(
            f"Over pace: {_who(r['teammate'])} ({r['name']}: pace {_money(int(r['state']['pace_end']))} "
            f"vs {_money(int(r['state']['amount']))})"
        )
        for r in others
    ]
    if len(ads) >= 2:
        amount = sum(int(r["state"]["amount"] or 0) for r in ads)
        actual = sum(int(r["state"]["actual"] or 0) for r in ads if r["state"]["actual"] is not None)
        pace = sum(int(r["state"]["pace_end"] or 0) for r in ads if r["state"]["pace_end"] is not None)
        lines.append(_short(f"Ad spend {_money(actual)} this month, pace {_money(pace)} vs {_money(amount)} budget."))
    # Budgets take at most three lines, so the company's lines (cash, burn, unattributed) still fit under MAX_LINES.
    return lines[:3]


def _loose(
    c: sqlite3.Connection, ctx: Ctx, view: View, private: dict[str, Any], at: str
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Expense lines this month with no teammate, as (amount, lines) for those with no team either (company-wide) and
    for those on a team (a manager's). Read from the rollup rows the reader's view covers; private accounts never show
    (view.suppress: "Unattributed private lines never show")."""
    a, z = dates.month_bounds(date.fromisoformat(at[:10]))
    ids = [x.id for x in ctx.accounts.values() if x.type == "expense" and x.id not in private]
    company, team = [0, 0], [0, 0]  # [amount, lines]
    for r in SL.rollup_rows(c, ctx.book.id, a.isoformat(), z.isoformat(), ids):
        if str(r["teammate"] or ""):
            continue
        account = ctx.accounts.get(r["account_id"])
        tags = {"team": str(r["team"] or ""), "teammate": ""}
        if account is None or not view.sees(account, tags):
            continue
        bucket = team if tags["team"] else company
        bucket[0] += int(r["amount"] or 0)
        bucket[1] += int(r["line_count"] or 0)
    return (company[0], company[1]), (team[0], team[1])


def _loose_lines(c: sqlite3.Connection, ctx: Ctx, view: View, private: dict[str, Any], at: str) -> list[str]:
    """Unattributed spend: the company's as "Unattributed" (a reader who sees all the expenses), a team's as "in your
    team" (a manager). Each says its amount and its line count."""
    company, team = _loose(c, ctx, view, private, at)
    if view.company_expenses():
        amount, n = company
        return [f"Unattributed: {_money(amount)} ({_lines_word(n)})."] if n else []
    amount, n = team
    return [f"Unattributed in your team: {_money(amount)} ({_lines_word(n)})."] if n else []


def _company_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """Cash, burn, Watch findings and the bank sync time. Each is left out when the reader's view does not cover it:
    cash and burn need the balance sheet (the owner's unless granted)."""
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
    synced = str((cash or {}).get("data_as_of") or "")  # the bank data's time (reads.metric data_as_of)
    if synced:
        out.append(f"Bank data synced {synced[11:16]} UTC" + (", stale." if cash and cash.get("stale") else "."))
    return out


def _later_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """Subscription lines (subscriptions.py), then the receipt line (receipts.py)."""
    return SUB.brief_lines(c, reader, at) + RCP.brief_lines(c, reader, at)


def compute(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """The lines this reader may see, read now: [] when Finance is not set up or they have no Finance view at all.
    Reads only."""
    if not is_set_up(c):
        return []
    ctx = load(c, at)
    view = ctx.view(reader)
    if not view.sees_anything():
        return []
    lines = _budget_lines(c, reader, at)
    lines += _loose_lines(c, ctx, view, SA.private_lines(c), at)
    lines += _company_lines(c, reader, at)
    lines += _later_lines(c, reader, at)
    return [_short(line) for line in lines][:MAX_LINES]


def fingerprint(lines: list[str]) -> str:
    """The brief's numbers, as its lines state them. The pace figure is left out: a budget's pace moves with the clock
    every hour the month runs, so a brief is re-issued when a spend, an amount, a team's total or whether a budget is
    over pace changes, and the stored lines keep the pace of that time (their as_of)."""
    normal = [PACE.sub("pace", line) for line in lines]
    return hashlib.sha256(json.dumps(normal, ensure_ascii=False).encode("utf-8")).hexdigest()


def _row(c: sqlite3.Connection, reader: str) -> Row | None:
    got = c.execute("SELECT * FROM fin_briefs WHERE reader=?", (reader,)).fetchone()
    return None if got is None else dict(got)


def _save(
    c: sqlite3.Connection, reader: str, lines: list[str], digest: str, now: str, row: Row | None, *, deliver: bool
) -> Row:
    """Store the brief when its numbers moved (`as_of` moves, `changed_since` keeps the old one). With `deliver`, the
    fingerprint handed at turn start is recorded as well. Writes nothing when nothing moved."""
    version = R.view_version(c)
    if row is not None and row["fingerprint"] == digest:
        # The numbers did not move: the stored lines (with the pace of the time they were issued) stand.
        lines = json.loads(row["lines"])
        as_of, changed_since = row["as_of"], row["changed_since"]
        if deliver and row["delivered_fingerprint"] != digest:
            c.execute("UPDATE fin_briefs SET delivered_fingerprint=? WHERE reader=?", (digest, reader))
    else:
        as_of = now
        changed_since = row["as_of"] if row is not None else None
        delivered = digest if deliver else (row["delivered_fingerprint"] if row is not None else "")
        c.execute(
            "INSERT INTO fin_briefs(reader, fingerprint, lines, as_of, changed_since, view_version, created_at, "
            "delivered_fingerprint) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(reader) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, lines=excluded.lines, as_of=excluded.as_of, "
            "changed_since=excluded.changed_since, view_version=excluded.view_version, "
            "delivered_fingerprint=excluded.delivered_fingerprint",
            (reader, digest, json.dumps(lines, ensure_ascii=False), as_of, changed_since, version, now, delivered),
        )
    return {"lines": lines, "as_of": as_of, "changed_since": changed_since, "version": version}


def brief(c: sqlite3.Connection, reader: str, at: str | None = None) -> Row:
    """The reader's brief: {lines, as_of, changed_since, version}, for the people page and tico_finance_brief. It keeps
    one `fin_briefs` row per reader and writes only when the lines moved (call it in a write transaction)."""
    now = at or H.now()
    lines = compute(c, reader, now)
    return _save(c, reader, lines, fingerprint(lines), now, _row(c, reader), deliver=False)


def for_turn(c: sqlite3.Connection, reader: str, at: str | None = None) -> Row | None:
    """The turn-start hand-off (backend/execution.py): the lines this reader is handed now, or None. Lines are handed
    only when they differ from the fingerprint handed at this reader's last turn start. When they do not, this reads
    and compares and writes nothing. It runs inside every bot's turn claim, so a failure here hands no lines (and is
    logged) instead of failing the turn; its one write sits in a savepoint that a failure rolls back."""
    try:
        c.execute("SAVEPOINT finance_brief")
        try:
            got = _for_turn(c, reader, at)
        except BaseException:
            c.execute("ROLLBACK TO finance_brief")
            raise
        finally:
            c.execute("RELEASE finance_brief")
    except Exception:
        log.exception("finance brief for %s failed; the turn goes on without it", reader)
        return None
    return got


def _for_turn(c: sqlite3.Connection, reader: str, at: str | None) -> Row | None:
    now = at or H.now()
    lines = compute(c, reader, now)
    digest = fingerprint(lines)
    row = _row(c, reader)
    if row is not None and row["fingerprint"] == digest and row["delivered_fingerprint"] == digest:
        return None
    got = _save(c, reader, lines, digest, now, row, deliver=True)
    if not got["lines"]:
        return None
    return {"lines": got["lines"], "as_of": got["as_of"], "changed_since": got["changed_since"]}
