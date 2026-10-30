"""View access: managers never see another department (seeded property test), payroll by person can't be worked out by
subtracting one read from another, Unattributed shows only company-wide, and a revoked grant is gone on the next read."""
import random

from backend.finance import reads as FR
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, spend, store  # noqa: F401

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


def _payroll(c):
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    pay = str(made.result["account_id"])
    assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
    # Engineering pays four people (ben, cara, eve, fay); Sales pays two (dan, gus).
    for mate in ("human:ben", "human:cara", "human:eve", "human:fay", "human:dan", "human:gus"):
        spend(c, "6200", 10_000 + len(mate), teammate=mate)
    spend(c, "6100", 3_000, team="web")
    spend(c, "6100", 2_000, team="sales")
    return pay


def _everything(c, viewer, pay):
    reads = {by: FR.expenses(c, viewer, "2026-10-01", "2026-10-31", by, top_n=50, at=AT)
             for by in ("department", "team", "teammate", "person", "vendor", "account")}
    reads["pnl"] = FR.pnl(c, viewer, "2026-10-01", "2026-10-31", at=AT)
    reads["line"] = FR.line_detail(c, viewer, pay, "2026-10-01", "2026-10-31", at=AT) if viewer in ("human:eve", OWNER,
                                                                                                    "human:ben") else {}
    reads["spend"] = FR.metric(c, viewer, "spend", start="2026-10-01", end="2026-10-31", at=AT)
    for key in ("web", "eng", "sales"):
        reads[f"team-{key}"] = FR.expenses(c, viewer, "2026-10-01", "2026-10-31", "account", team=key, at=AT)
    return reads


def test_payroll_by_person_is_never_derivable(books):
    with books.transaction() as c:
        pay = _payroll(c)
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "expenses"}).status == "committed"
        before = _everything(c, "human:eve", pay)
        dept = {r["key"]: r["amount"] for r in before["department"]["rows"]}
        assert dept["sales"] == 2_000                  # two paid people: their payroll never shows
        assert dept["eng"] == 3_000 + sum(10_000 + len(m) for m in ("human:ben", "human:cara", "human:eve", "human:fay"))
        assert {r["key"]: r["amount"] for r in before["teammate"]["rows"]} == {"human:eve": 10_009, "unattributed": 5_000}
        assert before["pnl"]["excluded"] and next(r for r in before["pnl"]["rows"] if r["account_id"] == pay)["private"]
        assert before["line"]["entries"] == [] and before["line"]["total"] is None
        # A raise for someone in Sales changes nothing Eve can read; one for Fay moves only
        # reads that show Engineering's total.
        spend(c, "6200", 777, teammate="human:gus")
        assert _everything(c, "human:eve", pay) == before
        spend(c, "6200", 555, teammate="human:fay")
        after = _everything(c, "human:eve", pay)
        changed = {k for k in before if before[k] != after[k]}
        # Only reads at department level or wider move, by exactly the department total Eve may see.
        assert changed == {"department", "account", "spend", "team-eng"}
        assert after["spend"]["value"] - before["spend"]["value"] == 555
        owner = _everything(c, OWNER, pay)
        assert {r["key"] for r in owner["teammate"]["rows"]} >= {"human:gus", "human:fay"}


def test_managers_get_their_department_payroll_total_only_when_it_is_whole(books):
    with books.transaction() as c:
        pay = _payroll(c)
        ben = FR.expenses(c, "human:ben", "2026-10-01", "2026-10-31", "department", at=AT)
        assert {r["key"] for r in ben["rows"]} == {"eng"} and ben["rows"][0]["amount"] > 40_000
        assert FR.expenses(c, "human:ben", "2026-10-01", "2026-10-31", "team", at=AT)["excluded"]
        cara = FR.expenses(c, "human:cara", "2026-10-01", "2026-10-31", "department", at=AT)
        assert cara["rows"][0]["amount"] == 3_000 + 10_010     # Web is not the whole department: her own pay only
        assert FR.metric(c, "human:cara", "spend", account_id=pay, at=AT)["value"] == 10_010


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
