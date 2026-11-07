"""Segments in Finance (spec: Money and Access): a budget and a customer carry a company segment, a line may be tagged
with one, the P&L splits by segment into a column each plus Unallocated and Total, and each teammate sees the columns
their access gives. The owner and anyone with the whole P&L see every column; a segment's lead sees its own column; a
dedicated teammate who leads nothing sees the segment's budgets but no P&L; anyone else sees nothing. Who is dedicated
to a segment comes from segment_members, which Track A owns; these tests create that table and fill it directly."""

import importlib.util
import itertools
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend import hubdb as H
from backend import market as MK
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance import segmentation as SG
from backend.finance.core.errors import FinanceError
from backend.finance.store import ledger as SL
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT,
    OWNER,
    account,
    act,
    books,
    finance_template,
    store,
)

S1, S2, S3 = "segment/small-businesses", "segment/creators", "segment/agencies"
OCTOBER = ("2026-10-01", "2026-10-31")
DAY = "2026-10-10"
ALL_COLUMNS = [S1, S2, S3, SG.UNALLOCATED, SG.TOTAL]
_seq = itertools.count()


def _ok(r):
    assert r.status == "committed", r
    return r.result


def _refused(c, who: str, kind: str, payload: dict[str, object]) -> str:
    """Why an action is refused, whether it raised or came back refused."""
    try:
        r = act(c, who, kind, payload)
    except FinanceError as e:
        return e.message
    assert r.status != "committed", r
    return r.reason


def _market(c, sid: str, name: str, rank: int | None) -> None:
    """A company segment: a `segment` entity the company operates in (backend/segments.py ours_ids)."""
    now = H.now()
    props: dict[str, object] = {"color": "#2f6fed"}
    if rank is not None:
        props["rank"] = rank
    c.execute(
        "INSERT INTO market_entities(id, type, name, properties, created_at, created_by, updated_at) "
        "VALUES(?, 'segment', ?, ?, ?, 'test', ?)",
        (sid, name, json.dumps(props), now, now),
    )
    c.execute(
        "INSERT INTO market_edges(id, src, rel, dst, created_at, created_by, updated_at) "
        "VALUES(?, ?, 'operates_in', ?, ?, 'test', ?)",
        (f"edge-{sid}", MK.OWN_COMPANY_ID, sid, now, now),
    )


def _segments(c) -> None:
    _market(c, S1, "Small businesses", 1)
    _market(c, S2, "Creators", 2)
    _market(c, S3, "Agencies", None)  # no rank: after the ranked segments


def _dedicated(c, rows: list[tuple[str, str]]) -> None:
    """Who is tagged with which segment (actors), in segment_members, the table Track A owns."""
    c.execute(
        "CREATE TABLE IF NOT EXISTS segment_members (member TEXT NOT NULL, segment_id TEXT NOT NULL, "
        "PRIMARY KEY (member, segment_id))"
    )
    c.executemany(
        "INSERT INTO segment_members(member, segment_id) VALUES(?, ?)",
        [(SG.member_ref(c, who), sid) for who, sid in rows],
    )


def _accounts(c) -> None:
    for code, name in (("6510", "Hosting"), ("6530", "Shared tools")):
        _ok(act(c, OWNER, "account_create", {"code": code, "name": name, "type": "expense"}))


def _post(c, lines: list[dict[str, object]]) -> None:
    FA.post_entry(c, {"date": DAY, "source": "usage", "source_id": f"seg-{next(_seq)}", "lines": lines}, at=AT)


def _sale(c, customer: str, amount: int) -> None:
    """Revenue on a customer's account, paid into the bank."""
    _post(
        c,
        [
            {"account_id": account(c, "4000"), "amount": -amount, "contact_id": customer},
            {"account_id": account(c, "1000"), "amount": amount},
        ],
    )


def _cost(c, code: str, amount: int, *, team: str = "", segment: str = "") -> None:
    """A cost paid from the bank, tagged with a team and a segment when given."""
    tags = {k: v for k, v in (("team", team), ("segment", segment)) if v}
    _post(
        c,
        [
            {"account_id": account(c, code), "amount": amount, "tags": tags},
            {"account_id": account(c, "1000"), "amount": -amount},
        ],
    )


def _customer(c, name: str) -> str:
    return str(_ok(act(c, OWNER, "contact_create", {"name": name, "kind": "customer"}))["contact_id"])


def _month(c) -> dict[str, str]:
    """October in Finance: three segments, the people and bot on them, two customers (Northwind's segment set by a
    person, Contoso's unset), three budgets and five costs. Returns the budget ids and the customers."""
    _segments(c)
    _dedicated(c, [("human:cara", S1), ("human:eve", S1), ("bot:coder", S2)])
    _accounts(c)
    northwind, contoso = _customer(c, "Northwind"), _customer(c, "Contoso")
    _ok(act(c, OWNER, "contact_segment", {"contact_id": northwind, "segment_id": S1}))
    creators = str(
        _ok(
            act(
                c,
                OWNER,
                "budget_create",
                {
                    "name": "Software creators",
                    "account_id": account(c, "6100"),
                    "amount": 100_000,
                    "at_limit": "decline",
                    "segment": S2,
                },
            )
        )["budget_id"]
    )
    web = str(
        _ok(
            act(
                c,
                OWNER,
                "budget_create",
                {
                    "name": "Software web",
                    "account_id": account(c, "6100"),
                    "team": "web",
                    "parent_id": creators,
                    "amount": 50_000,
                    "at_limit": "decline",
                },
            )
        )["budget_id"]
    )
    hosting = str(
        _ok(
            act(
                c,
                OWNER,
                "budget_create",
                {
                    "name": "Hosting small",
                    "account_id": account(c, "6510"),
                    "amount": 100_000,
                    "at_limit": "decline",
                    "segment": S1,
                },
            )
        )["budget_id"]
    )
    _sale(c, northwind, 10_000)
    _sale(c, contoso, 4_000)
    _cost(c, "6100", 3_000)  # the creators budget: S2
    _cost(c, "6100", 2_000, team="web")  # the web budget, no segment of its own: S2 from its parent
    _cost(c, "6100", 700, segment=S1)  # an explicit tag beats the creators budget: S1
    _cost(c, "6510", 1_000)  # the hosting budget: S1
    _cost(c, "6530", 500)  # no budget and no tag: Unallocated
    return {"creators": creators, "web": web, "hosting": hosting, "northwind": northwind, "contoso": contoso}


def _budgets(c, who: str = OWNER, segment: str | None = None) -> dict[str, dict[str, Any]]:
    return {str(r["name"]): r for r in FR.budgets(c, who, segment=segment)["budgets"]}


def _contact(c, cid: str) -> dict[str, Any]:
    return next(r for r in FR.contacts(c, OWNER, limit=200)["contacts"] if r["id"] == cid)


def _columns(out: dict[str, Any]) -> list[str]:
    return [str(x["key"]) for x in out["columns"]]


def _values(out: dict[str, Any], key: str) -> dict[str, int]:
    return next(dict(r["values"]) for r in out["rows"] if r["key"] == key)


def test_a_budget_takes_a_company_segment_and_refuses_any_other(books):  # noqa: F811
    with books.transaction() as c:
        _segments(c)
        assert "not one of the company's segments" in _refused(
            c,
            OWNER,
            "budget_create",
            {
                "name": "Stray",
                "account_id": account(c, "6100"),
                "amount": 1_000,
                "at_limit": "decline",
                "segment": "segment/nobody",
            },
        )
        small = str(
            _ok(
                act(
                    c,
                    OWNER,
                    "budget_create",
                    {
                        "name": "Small",
                        "account_id": account(c, "6100"),
                        "amount": 1_000,
                        "at_limit": "decline",
                        "segment": S1,
                    },
                )
            )["budget_id"]
        )
        assert _budgets(c)["Small"]["segment"] == S1
        _ok(act(c, OWNER, "budget_update", {"budget_id": small, "segment": S2}))
        assert _budgets(c)["Small"]["segment"] == S2
        assert "not one of the company's segments" in _refused(
            c, OWNER, "budget_update", {"budget_id": small, "segment": "segment/nobody"}
        )
        assert _budgets(c)["Small"]["segment"] == S2  # the refusal changed nothing


def test_a_child_with_no_segment_reports_under_its_parent_and_the_filter_finds_it(books):  # noqa: F811
    with books.transaction() as c:
        ids = _month(c)
        under_creators = _budgets(c, segment=S2)
        assert {"Software creators", "Software web"} <= set(under_creators)
        assert under_creators["Software web"]["segment"] is None
        assert under_creators["Software web"]["segment_effective"] == S2
        assert under_creators["Software creators"]["segment"] == S2
        assert "Hosting small" not in under_creators
        hosting = _budgets(c, segment=S1)
        assert "Hosting small" in hosting and "Software web" not in hosting
        unallocated = _budgets(c, segment="unallocated")
        assert not {"Hosting small", "Software creators", "Software web"} & set(unallocated)
        assert _budgets(c)["Software web"]["id"] == ids["web"]


def test_a_customers_segment_is_a_guess_until_a_person_sets_it(books):  # noqa: F811
    with books.transaction() as c:
        _segments(c)
        _ok(act(c, OWNER, "right_grant", {"teammate": "bot:seller", "right": "finance.ar"}))
        fab = _customer(c, "Fabrikam")
        _ok(act(c, "bot:seller", "contact_segment", {"contact_id": fab, "segment_id": S1}))
        assert (_contact(c, fab)["segment_id"], _contact(c, fab)["segment_sure"]) == (S1, False)
        _ok(act(c, "bot:seller", "contact_segment", {"contact_id": fab, "segment_id": S2}))  # a guess replaces a guess
        assert (_contact(c, fab)["segment_id"], _contact(c, fab)["segment_sure"]) == (S2, False)
        _ok(act(c, OWNER, "contact_segment", {"contact_id": fab, "segment_id": S1}))
        assert (_contact(c, fab)["segment_id"], _contact(c, fab)["segment_sure"]) == (S1, True)
        assert "a person set this customer's segment" in _refused(
            c, "bot:seller", "contact_segment", {"contact_id": fab, "segment_id": S2}
        )
        _ok(act(c, "bot:seller", "contact_segment", {"contact_id": fab, "segment_id": S1}))  # the same choice again
        assert (_contact(c, fab)["segment_id"], _contact(c, fab)["segment_sure"]) == (S1, True)
        vendor = str(_ok(act(c, OWNER, "contact_create", {"name": "Zed Supplies", "kind": "vendor"}))["contact_id"])
        assert "a segment is set on a customer" in _refused(
            c, OWNER, "contact_segment", {"contact_id": vendor, "segment_id": S1}
        )
        _ok(act(c, OWNER, "contact_segment", {"contact_id": fab, "segment_id": ""}))
        assert _contact(c, fab)["segment_id"] is None


def test_the_review_list_holds_customers_with_no_segment_or_a_guess_and_pages(books):  # noqa: F811
    with books.transaction() as c:
        _segments(c)
        _ok(act(c, OWNER, "right_grant", {"teammate": "bot:seller", "right": "finance.ar"}))
        alpha = _customer(c, "Alpha Co")  # unset
        bravo = _customer(c, "Bravo Co")  # a bot's guess
        charlie = _customer(c, "Charlie Co")  # a person's choice
        _ok(act(c, "bot:seller", "contact_segment", {"contact_id": bravo, "segment_id": S1}))
        _ok(act(c, OWNER, "contact_segment", {"contact_id": charlie, "segment_id": S2}))
        _ok(act(c, OWNER, "contact_create", {"name": "Zed Supplies", "kind": "vendor"}))
        assert [r["id"] for r in FR.contacts(c, OWNER, review=True)["contacts"]] == [alpha, bravo]
        first = FR.contacts(c, OWNER, review=True, limit=1)
        assert [r["name"] for r in first["contacts"]] == ["Alpha Co"] and first["next_cursor"]
        second = FR.contacts(c, OWNER, review=True, limit=1, cursor=first["next_cursor"])
        assert [r["name"] for r in second["contacts"]] == ["Bravo Co"] and second["next_cursor"] is None
        assert charlie not in [r["id"] for r in FR.contacts(c, OWNER, review=True, limit=200)["contacts"]]


def test_a_posted_line_takes_a_segment_tag_through_categorize_and_moves_its_column(books):  # noqa: F811
    with books.transaction() as c:
        _segments(c)
        _accounts(c)
        _cost(c, "6530", 500)
        line = c.execute("SELECT id FROM fin_lines WHERE account_id=? LIMIT 1", (account(c, "6530"),)).fetchone()[0]
        assert "segment" in _refused(
            c, OWNER, "categorize", {"lines": [{"line_id": line, "tags": {"segment": "segment/nobody"}}]}
        )
        assert _values(FR.pnl_by(c, OWNER, "segment", *OCTOBER, basis="accrual"), "expense")[SG.UNALLOCATED] == 500
        _ok(act(c, OWNER, "categorize", {"lines": [{"line_id": line, "tags": {"segment": S2}}]}))
        assert SL.line_tags(c, [line])[line]["segment"] == S2
        expense = _values(FR.pnl_by(c, OWNER, "segment", *OCTOBER, basis="accrual"), "expense")
        assert expense == {S1: 0, S2: 500, S3: 0, SG.UNALLOCATED: 0, SG.TOTAL: 500}


def test_the_segment_pnl_splits_each_line_once_and_its_total_is_the_pnl(books):  # noqa: F811
    with books.transaction() as c:
        _month(c)
        out = FR.pnl_by(c, OWNER, "segment", *OCTOBER, basis="accrual")
        assert _columns(out) == ALL_COLUMNS
        assert _values(out, "income") == {S1: 10_000, S2: 0, S3: 0, SG.UNALLOCATED: 4_000, SG.TOTAL: 14_000}
        assert _values(out, "expense") == {S1: 1_700, S2: 5_000, S3: 0, SG.UNALLOCATED: 500, SG.TOTAL: 7_200}
        assert _values(out, "contribution") == {S1: 8_300, S2: -5_000, S3: 0, SG.UNALLOCATED: 3_500, SG.TOTAL: 6_800}
        whole = FR.pnl(c, OWNER, *OCTOBER, "month", "accrual")
        assert (whole["income"], whole["expense"], whole["net"]) == ([14_000], [7_200], [6_800])
        via_report = FR.report(c, OWNER, "pnl_by", start=OCTOBER[0], end=OCTOBER[1], by="segment", basis="accrual")
        assert via_report["kind"] == "pnl_by" and _values(via_report, "contribution") == _values(out, "contribution")


def test_the_segment_filter_keeps_one_segments_lines(books):  # noqa: F811
    with books.transaction() as c:
        _month(c)
        only = FR.pnl(c, OWNER, *OCTOBER, "month", "accrual", segment=S2)
        assert (only["segment"], only["income"], only["expense"]) == (S2, [0], [5_000])
        unallocated = FR.pnl(c, OWNER, *OCTOBER, "month", "accrual", segment="unallocated")
        assert (unallocated["segment"], unallocated["income"], unallocated["expense"]) == (
            SG.UNALLOCATED,
            [4_000],
            [500],
        )
        with pytest.raises(FinanceError, match="not one of the company's segments"):
            FR.pnl(c, OWNER, *OCTOBER, "month", "accrual", segment="segment/nobody")


def test_the_owner_and_a_full_pnl_viewer_see_every_column(books):  # noqa: F811
    with books.transaction() as c:
        _month(c)
        _ok(act(c, OWNER, "view_grant", {"subject": "human:ben", "what": "pnl", "whose": "company"}))
        for who in (OWNER, "human:ben"):
            assert _columns(FR.pnl_by(c, who, "segment", *OCTOBER, basis="accrual")) == ALL_COLUMNS, who


def test_a_segment_lead_sees_only_their_own_column(books):  # noqa: F811
    with books.transaction() as c:
        _month(c)
        out = FR.pnl_by(
            c, "human:cara", "segment", *OCTOBER, basis="accrual"
        )  # cara leads S1: ben, her manager, does not
        assert _columns(out) == [S1]
        assert _values(out, "income") == {S1: 10_000}
        assert _values(out, "expense") == {S1: 1_700}
        coder = FR.pnl_by(c, "bot:coder", "segment", *OCTOBER, basis="accrual")  # the bot leads S2 by ref
        assert _columns(coder) == [S2]
        assert _values(coder, "contribution") == {S2: -5_000}


def test_a_dedicated_teammate_who_leads_nothing_reads_the_budget_but_no_pnl(books):  # noqa: F811
    with books.transaction() as c:
        ids = _month(c)
        # eve is tagged with S1 under cara, who is tagged too: eve is dedicated, not a lead
        with pytest.raises(FinanceError, match="segment P&L"):
            FR.pnl_by(c, "human:eve", "segment", *OCTOBER, basis="accrual")
        with pytest.raises(FinanceError, match="segment P&L"):
            FR.pnl_by(c, "human:fay", "segment", *OCTOBER, basis="accrual")  # fay is an outsider
        eve_budgets = _budgets(c, "human:eve")
        assert "Hosting small" in eve_budgets and "Software creators" not in eve_budgets
        assert "Hosting small" not in _budgets(c, "human:fay")
        assert _refused(c, "human:eve", "budget_update", {"budget_id": ids["hosting"], "amount": 1})


def test_a_company_can_turn_the_segment_lead_view_off(books):  # noqa: F811
    with books.transaction() as c:
        _month(c)
        _ok(act(c, OWNER, "view_defaults_set", {"segment_lead": "none"}))
        with pytest.raises(FinanceError, match="segment P&L"):
            FR.pnl_by(c, "human:cara", "segment", *OCTOBER, basis="accrual")


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations" / "20261107010204_finance_segments.py"
    spec = importlib.util.spec_from_file_location("finance_segments_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A database as it stood before this migration: fin_line_tags without the segment kind, with its rows and its index.
LEGACY = """
CREATE TABLE fin_lines (id TEXT PRIMARY KEY);
CREATE TABLE fin_line_tags (
  line_id TEXT NOT NULL REFERENCES fin_lines(id),
  kind TEXT NOT NULL CHECK (kind IN ('team','teammate','budget','project','custom','location','class')),
  value TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, PRIMARY KEY (line_id, kind));
CREATE INDEX fin_line_tags_value ON fin_line_tags(kind, value);
CREATE TABLE fin_budgets (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE fin_contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE fin_view_versions (
  id INTEGER PRIMARY KEY, version INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO fin_view_versions VALUES (1, 4, 'grant', '2026-10-01T00:00:00Z');
INSERT INTO fin_lines VALUES ('l1'), ('l2');
INSERT INTO fin_line_tags VALUES ('l1', 'team', 'web', '2026-10-01', 'ana'), ('l2', 'location', 'hq', '2026-10-01', 'ana');
INSERT INTO fin_budgets VALUES ('b1', 'Software');
INSERT INTO fin_contacts VALUES ('k1', 'Northwind');
"""


def test_the_migration_widens_line_tags_keeping_rows_and_index_and_adds_the_segment_columns():
    migration = _migration()
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY)
    migration.up(conn)
    rows = conn.execute("SELECT line_id, kind, value FROM fin_line_tags ORDER BY line_id").fetchall()
    assert rows == [("l1", "team", "web"), ("l2", "location", "hq")]
    indexes = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fin_line_tags'")
    }
    assert "fin_line_tags_value" in indexes
    conn.execute("INSERT INTO fin_lines VALUES ('l3')")
    conn.execute("INSERT INTO fin_line_tags VALUES ('l3', 'segment', 'segment/creators', '2026-10-02', 'ana')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO fin_line_tags VALUES ('l3', 'bogus', 'x', '2026-10-02', 'ana')")
    assert "segment_id" in {r[1] for r in conn.execute("PRAGMA table_info(fin_budgets)")}
    contact_columns = {r[1] for r in conn.execute("PRAGMA table_info(fin_contacts)")}
    assert {"segment_id", "segment_sure"} <= contact_columns
    assert conn.execute("SELECT segment_sure FROM fin_contacts").fetchone() == (0,)
    assert conn.execute("SELECT version, reason FROM fin_view_versions").fetchone() == (5, "defaults")
    migration.up(conn)  # a second run changes nothing
    assert conn.execute("SELECT count(*) FROM fin_line_tags").fetchone() == (3,)
