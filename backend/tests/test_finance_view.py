"""View access: managers never see another department (seeded property test), payroll shows only as whole closed runs
so no day, range or own line isolates a bonus, grants lapse with their grantor's authority, writes check every tag,
and moving an account needs company-wide view. Plus the P&L's revenue sign."""
import random

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import (AT, OWNER, account, act, books, finance_template, set_lead,  # noqa: F401
                                                 spend, store)

TEAMS = {"human:ben": "eng", "human:cara": "web", "human:eve": "web", "human:fay": "web", "human:dan": "sales",
         "human:gus": "sales", "bot:coder": "web", "bot:seller": "sales"}
SEES = {"human:ben": {"eng", "web"}, "human:cara": {"web"}, "human:dan": {"sales"}}


def test_managers_see_only_their_teams_and_members_only_their_own(books):
    rng = random.Random(11)
    with books.transaction() as c:
        posted: list[tuple[str, str, int]] = []
        for _ in range(150):
            mate = rng.choice([""] + sorted(TEAMS))
            team = TEAMS.get(mate) or rng.choice(["", "eng", "web", "sales"])
            amount = rng.randint(1, 9_000)
            spend(c, rng.choice(["5100", "5200", "6100", "6900"]), amount, team=team, teammate=mate)
            posted.append((team, mate, amount))
        for viewer, teams in SEES.items():
            got = FR.expenses(c, viewer, "2026-10-01", "2026-10-31", "team", top_n=50, at=AT)
            assert {r["key"] for r in got["rows"]} <= teams | {"other"}
            assert got["total"] == sum(a for t, m, a in posted if t in teams or m == viewer)
            assert FR.expenses(c, viewer, "2026-10-01", "2026-10-31", "team", team="sales" if viewer != "human:dan"
                               else "web", at=AT)["total"] == 0
        mine = FR.expenses(c, "human:eve", "2026-10-01", "2026-10-31", "teammate", at=AT)
        assert {r["key"] for r in mine["rows"]} <= {"human:eve"}
        assert mine["total"] == sum(a for t, m, a in posted if m == "human:eve")
        everyone = FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "team", top_n=2, at=AT)
        assert everyone["total"] == sum(a for _, _, a in posted)
        assert any(r["key"] == "unattributed" for r in FR.expenses(c, OWNER, "2026-10-01", "2026-10-31", "team",
                                                                    top_n=10, at=AT)["rows"])
        assert everyone["rows"][-1]["label"].startswith("Other · ")
        assert FR.pnl(c, "human:cara", "2026-10-01", "2026-10-31", at=AT)["income"] == [0]
        revenue = [r for r in FR.pnl(c, "human:cara", "2026-10-01", "2026-10-31", at=AT)["rows"] if r["type"] == "income"]
        assert revenue == []


PAY = {"human:ben": 11_000, "human:cara": 12_000, "human:eve": 13_000, "human:fay": 14_000, "human:dan": 15_000,
       "human:gus": 16_000}
ENG = ("human:ben", "human:cara", "human:eve", "human:fay")


def _close(c, *months):
    for month in months:
        assert act(c, OWNER, "close_period", {"month": month}).status == "committed"


def _payroll(c):
    """A private Payroll line. Q3 has two regular runs (Engineering pays four people, Sales two) and one off-cycle
    bonus for Fay on Sep 15; Jul-Sep are closed."""
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    pay = str(made.result["account_id"])
    assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
    for day in ("2026-08-31", "2026-09-30"):
        for mate, amount in PAY.items():
            spend(c, "6200", amount, day=day, teammate=mate)
    spend(c, "6200", 50_000, day="2026-09-15", teammate="human:fay")
    spend(c, "6100", 3_000, day="2026-09-10", team="web")
    spend(c, "6100", 2_000, day="2026-09-10", team="sales")
    _close(c, "2026-07", "2026-08", "2026-09")
    return pay


DAYS = ("2026-09-14", "2026-09-15", "2026-09-16", "2026-09-30")
RANGES = (("2026-09-01", "2026-09-15"), ("2026-09-10", "2026-09-30"), ("2026-09-15", "2026-09-30"),
          ("2026-09-01", "2026-09-30"), ("2026-07-01", "2026-09-30"))


def _everything(c, viewer, pay):
    """Every read the viewer can make over single days and overlapping ranges, daily P&L buckets included."""
    out = {}
    for a, z in [(d, d) for d in DAYS] + list(RANGES):
        for by in ("department", "team", "teammate", "account", "vendor"):
            out[(a, z, by)] = FR.expenses(c, viewer, a, z, by, top_n=50, at=AT)
        out[(a, z, "spend")] = FR.metric(c, viewer, "spend", start=a, end=z, at=AT)
        out[(a, z, "eng")] = FR.expenses(c, viewer, a, z, "account", team="eng", at=AT)
        out[(a, z, "line")] = FR.line_detail(c, viewer, pay, a, z, at=AT)
    out["pnl-day"] = FR.pnl(c, viewer, "2026-09-01", "2026-09-30", period="day", at=AT)
    out["pnl-month"] = FR.pnl(c, viewer, "2026-07-01", "2026-09-30", at=AT)
    return out


def _dept(c, viewer, a, z):
    return {r["key"]: r["amount"] for r in FR.expenses(c, viewer, a, z, "department", at=AT)["rows"]}


def test_payroll_shows_only_as_whole_closed_runs(books):
    with books.transaction() as c:
        pay = _payroll(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "expenses"}).status == "committed"
        run = sum(PAY[m] for m in ENG)
        assert _dept(c, "human:eve", "2026-09-30", "2026-09-30") == {"eng": run}
        # The bonus run paid one person: no single day, range or bucket shows it.
        assert _dept(c, "human:eve", "2026-09-15", "2026-09-15") == {}
        assert _dept(c, "human:eve", "2026-09-10", "2026-09-30") == {"eng": 3_000 + run, "sales": 2_000}
        before = _everything(c, "human:eve", pay)
        # A bigger bonus for Fay, or a raise in two-person Sales, changes nothing Eve can read.
        assert act(c, OWNER, "reopen_period", {"month": "2026-09"}).status == "committed"
        spend(c, "6200", 7_777, day="2026-09-15", teammate="human:fay")
        spend(c, "6200", 999, day="2026-09-30", teammate="human:gus")
        _close(c, "2026-09")
        assert _everything(c, "human:eve", pay) == before
        # A run in an open period never shows: Eve gets her own pay only.
        spend(c, "6200", 9_000, day="2026-10-10", teammate="human:ben")
        for mate in ENG[1:]:
            spend(c, "6200", 9_000, day="2026-10-10", teammate=mate)
        assert _dept(c, "human:eve", "2026-10-01", "2026-10-31") == {"eng": 9_000}


def test_own_lines_never_sit_beside_the_department_cell(books):
    with books.transaction() as c:
        _payroll(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:fay", "what": "expenses"}).status == "committed"
        run = sum(PAY[m] for m in ENG)
        dept = FR.expenses(c, "human:fay", "2026-09-01", "2026-09-30", "department", at=AT)
        assert {r["key"]: r["amount"] for r in dept["rows"]} == {"eng": 3_000 + run, "sales": 2_000}
        assert dept["excluded"] and dept["total"] == 5_000 + run       # her own bonus is not added in
        mine = FR.expenses(c, "human:fay", "2026-09-01", "2026-09-30", "teammate", at=AT)
        assert {r["key"]: r["amount"] for r in mine["rows"]} == {"human:fay": PAY["human:fay"] + 50_000,
                                                                 "unattributed": 5_000}


def test_managers_get_their_department_cell_only_when_they_cover_it(books):
    with books.transaction() as c:
        _payroll(c)
        run = sum(PAY[m] for m in ENG)
        assert _dept(c, "human:ben", "2026-09-01", "2026-09-30") == {"eng": 3_000 + run}
        # Cara leads Web, not all of Engineering: her own pay and Web's other spend only.
        assert _dept(c, "human:cara", "2026-09-01", "2026-09-30") == {"eng": 3_000 + PAY["human:cara"]}
        assert FR.expenses(c, "human:ben", "2026-09-01", "2026-09-30", "team", at=AT)["excluded"]


def test_grants_lapse_when_the_grantor_loses_authority(books):
    with books.transaction() as c:
        spend(c, "6100", 4_000, team="web")
        made = act(c, "human:cara", "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                                   "whose_team": "web"})
        assert made.status == "committed"
        assert FR.expenses(c, "human:eve", by="team", at=AT)["total"] == 4_000
        set_lead(c, "web", "")                      # Cara no longer leads Web
        assert FR.expenses(c, "human:eve", by="team", at=AT)["total"] == 0
        before = FR.view_version(c)
        act(c, OWNER, "account_create", {"code": "6400", "name": "Ads", "type": "expense"})   # any write notices
        status = c.execute("SELECT status FROM fin_view_grants WHERE id=?", (made.result["grant_id"],)).fetchone()[0]
        assert status == "suspended" and FR.view_version(c) == before + 1


def test_writes_check_every_tag(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:cara", "right": "finance.book"})
        act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "bs_lines", "account_ids": [account(c, "1000")]})

        def entry(**tags):
            return {"date": "2026-10-03", "lines": [{"account_id": account(c, "6100"), "amount": 100, "tags": tags},
                                                    {"account_id": account(c, "1000"), "amount": -100}]}
        assert act(c, "human:cara", "journal", entry(team="web", teammate="human:gus")).code == "denied"
        assert act(c, "human:cara", "journal", entry(team="sales", teammate="human:cara")).code == "denied"
        assert act(c, "human:cara", "journal", entry()).code == "denied"           # unattributed: company-wide only
        ok = act(c, "human:cara", "journal", entry(team="web", teammate="human:eve"))
        assert ok.status == "committed"
        line = next(i for i in ok.result["line_ids"] if c.execute(
            "SELECT account_id FROM fin_lines WHERE id=?", (i,)).fetchone()[0] == account(c, "6100"))
        moved = act(c, "human:cara", "categorize", {"lines": [{"line_id": line, "tags": {"teammate": "human:gus"}}]})
        assert moved.code == "denied"


def test_moving_an_account_needs_company_wide_view_and_keeps_budgets_whole(books):
    with books.transaction() as c:
        act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.book"})
        tools = str(act(c, OWNER, "account_create", {"code": "6110", "name": "Dev tools", "type": "expense",
                                                     "parent_id": account(c, "6100")}).result["account_id"])
        assert act(c, "human:ben", "account_update", {"account_id": tools, "parent_id": account(c, "6900")}).code \
            == "denied"
        soft = act(c, OWNER, "budget_create", {"name": "Software", "account_id": account(c, "6100"), "amount": 9_000})
        act(c, OWNER, "budget_create", {"name": "Dev tools", "account_id": tools, "parent_id": soft.result["budget_id"]})
        broken = act(c, OWNER, "account_update", {"account_id": tools, "parent_id": account(c, "6900")})
        assert broken.code == "invalid" and "breaks budget" in broken.reason
        assert act(c, OWNER, "account_update", {"account_id": tools, "name": "Developer tools"}).status == "committed"


def test_revenue_shows_positive_and_net_is_income_less_expense(books):
    with books.transaction() as c:
        FA.post_entry(c, {"date": "2026-10-05", "source": "import", "source_id": "sale-1", "lines": [
            {"account_id": account(c, "4000"), "amount": -10_000}, {"account_id": account(c, "1000"), "amount": 10_000}]},
            at=AT)
        spend(c, "6100", 2_000)
        out = FR.pnl(c, OWNER, "2026-10-01", "2026-10-31", at=AT)
        assert (out["income"], out["expense"], out["net"]) == ([10_000], [2_000], [8_000])


def test_a_revoked_grant_is_gone_on_the_next_read(books):
    with books.transaction() as c:
        spend(c, "6100", 4_000, team="sales")
        grant = act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                             "whose_team": "sales"})
        before = FR.view_version(c)
        assert FR.expenses(c, "human:eve", by="team", at=AT)["total"] == 4_000
        assert act(c, OWNER, "view_revoke", {"grant_id": grant.result["grant_id"]}).status == "committed"
        assert FR.view_version(c) == before + 1
        assert FR.expenses(c, "human:eve", by="team", at=AT)["total"] == 0


def test_who_may_grant(books):
    with books.transaction() as c:
        assert act(c, "human:cara", "view_grant", {"subject": "human:eve", "what": "expenses", "whose": "team",
                                                   "whose_team": "web"}).status == "committed"
        assert act(c, "human:cara", "view_grant", {"subject": "human:gus", "what": "expenses", "whose": "team",
                                                   "whose_team": "web"}).code == "denied"
        assert act(c, "human:cara", "view_grant", {"subject": "human:eve", "what": "pnl"}).code == "denied"
        assert act(c, "human:eve", "view_grant", {"subject": "human:eve", "what": "everything"}).code == "denied"
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "balance_sheet",
                                            "whose": "team", "whose_team": "web"}).code == "invalid"
