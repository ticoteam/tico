"""SQL for attribution: each posted line's attribution records (`fin_attributions`) and the rules that set them
(`fin_attribution_rules`). Tico-side only: nothing here enqueues a provider write.

A line's attribution is a chain of immutable rows. The newest row not yet superseded is what is in force; a change
inserts a row and stamps the old one superseded (superseded_by points at the new one). A row whose splits are empty
is a clear: it removes an override, so a matching rule applies again, else the line counts as its tags. No row at all
means the same. Undo restores the row a change superseded, as a new row of its own.

Precedence for a line: an in-force person or bot row with splits > a matching rule > the line's tags. A rule's effect
is a row too (source 'rule', rule_id), written when it applies, so a rule's removal can find and undo it.

Vendor, account and money-account matches are judged here (match_rule), on the facts of the line: its vendor keys
(store/ap.vendor_keys), its P&L account, and the entry's money account (the entry's first line on a non-P&L account,
its bank or card or payables side).
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ... import hubdb as _hubdb
from ..core.attribution import Split

# Tico's own modules are untyped: Finance reaches them through this file, typed at each call.
_H: Any = _hubdb

SOURCES = ("rule", "person", "bot")
MATCH_KINDS = ("vendor", "account", "money_account")
# The order a line's matching rules are judged in: the first kind with an enabled rule that fits the line wins.
PRECEDENCE = ("vendor", "money_account", "account")
HISTORY_MAX = 20


def new_id() -> str:
    return str(_H.new_id())


@dataclass(frozen=True)
class Claim:
    """What a line's attribution is set to by a rule or a person: its splits, its source and the rule that set it."""

    splits: tuple[Split, ...]
    source: str
    rule_id: str = ""


@dataclass(frozen=True)
class Facts:
    """The facts a rule matches a line on."""

    account_id: str
    vendor_keys: tuple[str, ...]
    money_account_id: str


def dump_splits(splits: Sequence[Split]) -> str:
    return json.dumps(
        [{"teammate": s.teammate, "team": s.team, "share_bps": s.share_bps} for s in splits], sort_keys=True
    )


def parse_splits(text: str | None) -> tuple[Split, ...]:
    if not text:
        return ()
    raw: Any = json.loads(text)
    out: list[Split] = []
    for item in raw:
        out.append(Split(str(item.get("teammate") or ""), str(item.get("team") or ""), int(item["share_bps"])))
    return tuple(out)


# ------------------------------------------------------------------------------------------------ in force and history
def in_force(c: sqlite3.Connection, line_id: str) -> Any:
    """The line's newest attribution row not superseded, or None (the line counts as its tags)."""
    return c.execute(
        "SELECT * FROM fin_attributions WHERE line_id=? AND superseded_at IS NULL LIMIT 1", (line_id,)
    ).fetchone()


def in_force_splits(c: sqlite3.Connection, line_id: str) -> tuple[Split, ...]:
    row = in_force(c, line_id)
    return parse_splits(str(row["splits"])) if row is not None else ()


def in_force_rows(c: sqlite3.Connection, line_ids: Sequence[str]) -> dict[str, Any]:
    """{line id: its in-force row} for the lines that have one."""
    out: dict[str, Any] = {}
    ids = list(dict.fromkeys(line_ids))
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
            f"SELECT * FROM fin_attributions WHERE superseded_at IS NULL AND line_id IN ({marks})", chunk
        ):
            out[str(row["line_id"])] = row
    return out


def claim_of_line(c: sqlite3.Connection, line_id: str) -> Claim | None:
    """The line's in-force attribution as a claim, when it has splits (what a reversal must carry)."""
    row = in_force(c, line_id)
    if row is None:
        return None
    splits = parse_splits(str(row["splits"]))
    if not splits:
        return None
    return Claim(splits, str(row["source"]), str(row["rule_id"] or ""))


def previous(c: sqlite3.Connection, line_id: str) -> Any:
    """The row the line's in-force row superseded, or None when nothing came before it."""
    row = in_force(c, line_id)
    if row is None:
        return None
    return c.execute("SELECT * FROM fin_attributions WHERE superseded_by=? LIMIT 1", (str(row["id"]),)).fetchone()


def history(c: sqlite3.Connection, line_id: str) -> list[Any]:
    """The line's rows, newest first, following the chain of superseded_by from the row in force."""
    out: list[Any] = []
    row = in_force(c, line_id)
    while row is not None and len(out) < HISTORY_MAX:
        out.append(row)
        row = c.execute("SELECT * FROM fin_attributions WHERE superseded_by=? LIMIT 1", (str(row["id"]),)).fetchone()
    return out


def write(
    c: sqlite3.Connection,
    *,
    book_id: str,
    line_id: str,
    splits: Sequence[Split],
    source: str,
    rule_id: str,
    by: str,
    at: str,
) -> str:
    """Put a new attribution in force on a line: the old row is stamped superseded first (one row in force at a time,
    enforced by a partial unique index), then the new row goes in. The new row's id."""
    if source not in SOURCES:
        raise ValueError(f"unknown attribution source {source}")
    new = new_id()
    old = in_force(c, line_id)
    if old is not None:
        c.execute(
            "UPDATE fin_attributions SET superseded_at=?, superseded_by=? WHERE id=? AND superseded_at IS NULL",
            (at, new, str(old["id"])),
        )
    c.execute(
        "INSERT INTO fin_attributions(id, book_id, line_id, splits, source, rule_id, set_by, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (new, book_id, line_id, dump_splits(splits), source, rule_id or None, by, at),
    )
    return new


def in_force_source(c: sqlite3.Connection, line_id: str) -> tuple[str, str, tuple[Split, ...]]:
    """(source, rule id, splits) in force on a line; ("", "", ()) when none is."""
    row = in_force(c, line_id)
    if row is None:
        return ("", "", ())
    return (str(row["source"]), str(row["rule_id"] or ""), parse_splits(str(row["splits"])))


def lines_with_rule(c: sqlite3.Connection, rule_id: str) -> list[str]:
    """The lines whose in-force attribution a rule put there (a rule's row, with splits)."""
    return [
        str(r[0])
        for r in c.execute(
            "SELECT line_id FROM fin_attributions WHERE rule_id=? AND source='rule' AND superseded_at IS NULL "
            "AND splits<>'[]' ORDER BY line_id",
            (rule_id,),
        ).fetchall()
    ]


# ------------------------------------------------------------------------------------------------ rules
def rules(c: sqlite3.Connection, book_id: str, *, live_only: bool = True) -> list[Any]:
    """The book's rules, first created first (the order a tie between two rules goes by)."""
    sql = "SELECT * FROM fin_attribution_rules WHERE book_id=?"
    if live_only:
        sql += " AND deleted_at IS NULL"
    return c.execute(sql + " ORDER BY created_at, id", (book_id,)).fetchall()


def rule(c: sqlite3.Connection, rule_id: str) -> Any:
    return c.execute("SELECT * FROM fin_attribution_rules WHERE id=?", (rule_id,)).fetchone()


def rule_splits(row: Any) -> tuple[Split, ...]:
    return parse_splits(str(row["splits"]))


def rule_taking(c: sqlite3.Connection, book_id: str, match_kind: str, match_id: str) -> Any:
    """The live rule already on this match, if any (one rule per match)."""
    return c.execute(
        "SELECT * FROM fin_attribution_rules WHERE book_id=? AND match_kind=? AND match_id=? AND deleted_at IS NULL",
        (book_id, match_kind, match_id),
    ).fetchone()


def put_rule(
    c: sqlite3.Connection,
    *,
    rule_id: str,
    book_id: str,
    name: str,
    match_kind: str,
    match_id: str,
    splits: Sequence[Split],
    enabled: bool,
    apply_from: str | None,
    by: str,
    at: str,
    exists: bool,
) -> None:
    if exists:
        c.execute(
            "UPDATE fin_attribution_rules SET name=?, match_kind=?, match_id=?, splits=?, enabled=?, "
            "apply_from=?, updated_at=? WHERE id=?",
            (name, match_kind, match_id, dump_splits(splits), int(enabled), apply_from, at, rule_id),
        )
        return
    c.execute(
        "INSERT INTO fin_attribution_rules(id, book_id, name, match_kind, match_id, splits, enabled, apply_from, "
        "created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (rule_id, book_id, name, match_kind, match_id, dump_splits(splits), int(enabled), apply_from, by, at, at),
    )


def delete_rule(c: sqlite3.Connection, rule_id: str, at: str) -> None:
    c.execute("UPDATE fin_attribution_rules SET deleted_at=?, updated_at=? WHERE id=?", (at, at, rule_id))


def match_rule(rule_rows: Iterable[Any], facts: Facts) -> Any:
    """The enabled rule a line takes, by precedence: a vendor rule, then a money-account rule, then an account rule;
    within one kind, the first created."""
    live = [r for r in rule_rows if int(r["enabled"])]
    for kind in PRECEDENCE:
        for r in live:
            if str(r["match_kind"]) != kind:
                continue
            if kind == "vendor" and str(r["match_id"]) in facts.vendor_keys:
                return r
            if kind == "money_account" and str(r["match_id"]) == facts.money_account_id:
                return r
            if kind == "account" and str(r["match_id"]) == facts.account_id:
                return r
    return None


def claim_for(rule_rows: Iterable[Any], facts: Facts) -> Claim | None:
    hit = match_rule(rule_rows, facts)
    if hit is None:
        return None
    return Claim(rule_splits(hit), "rule", str(hit["id"]))


def facts_of_line(c: sqlite3.Connection, book_id: str, line_id: str) -> Facts | None:
    """The facts a line matches rules on, read from the books (None when the line is not in the book)."""
    row = c.execute("SELECT * FROM fin_lines WHERE id=? AND book_id=?", (line_id, book_id)).fetchone()
    if row is None:
        return None
    keys = vendor_keys_of(c, book_id, str(row["contact_id"] or ""))
    return Facts(str(row["account_id"]), keys, money_account_of(c, str(row["entry_id"])))


def vendor_keys_of(c: sqlite3.Connection, book_id: str, contact_key: str) -> tuple[str, ...]:
    from .ap import vendor_keys  # lazy: store/ap imports the ledger's helpers

    if not contact_key:
        return ()
    return tuple(vendor_keys(c, book_id, contact_key))


def money_account_of(c: sqlite3.Connection, entry_id: str) -> str:
    """The entry's money account: its first line (in line order) on a balance sheet or payables account, else ""."""
    for r in c.execute(
        "SELECT l.account_id, a.type FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id "
        "WHERE l.entry_id=? ORDER BY l.line_no",
        (entry_id,),
    ).fetchall():
        if str(r[1]) not in ("income", "expense"):
            return str(r[0])
    return ""


def candidate_lines(c: sqlite3.Connection, book_id: str, rule_row: Any, since: str) -> list[str]:
    """The P&L lines a rule could match from `since` on (a superset: the match is judged per line by facts_of_line)."""
    kind, match = str(rule_row["match_kind"]), str(rule_row["match_id"])
    pnl = "a.type IN ('income','expense')"
    if kind == "account":
        sql = (
            f"SELECT l.id FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.book_id=? AND "
            f"l.entry_date>=? AND l.account_id=? AND {pnl}"
        )
        args: list[object] = [book_id, since, match]
    elif kind == "vendor":
        keys = vendor_keys_of(c, book_id, match)
        if not keys:
            return []
        marks = ",".join("?" * len(keys))
        sql = (
            f"SELECT l.id FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.book_id=? AND "
            f"l.entry_date>=? AND l.contact_id IN ({marks}) AND {pnl}"
        )
        args = [book_id, since, *keys]
    else:
        sql = (
            "SELECT l.id FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.book_id=? AND "
            f"l.entry_date>=? AND {pnl} AND l.entry_id IN (SELECT entry_id FROM fin_lines WHERE account_id=?)"
        )
        args = [book_id, since, match]
    return [str(r[0]) for r in c.execute(sql, args).fetchall()]


def lines_owned_by_people(c: sqlite3.Connection, line_ids: Sequence[str]) -> set[str]:
    """The lines among these with a person's or bot's override in force (a rule never replaces one)."""
    rows = in_force_rows(c, line_ids)
    return {lid for lid, r in rows.items() if str(r["source"]) in ("person", "bot") and parse_splits(str(r["splits"]))}


def rule_label_targets(c: sqlite3.Connection, book_id: str, rule_rows: Sequence[Any]) -> Mapping[str, str]:
    """{match id: its label}: a vendor's name, an account's code and name."""
    out: dict[str, str] = {}
    for r in rule_rows:
        mid = str(r["match_id"])
        if str(r["match_kind"]) == "vendor":
            row = c.execute("SELECT name FROM fin_contacts WHERE id=?", (mid,)).fetchone()
            out[mid] = str(row[0]) if row else mid
        else:
            row = c.execute("SELECT code, name FROM fin_accounts WHERE id=?", (mid,)).fetchone()
            out[mid] = f"{row[0]} {row[1]}" if row else mid
    return out
