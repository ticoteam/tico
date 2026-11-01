"""Reads by place, class and project: each grouping's rows add up to the total, lines with none of the tags fall in
Unattributed, the P&L by place adds up to the P&L, a teammate sees only their own lines however they group, a tag value
cannot hold the rollup key's separator, and search finds items, places and projects by name."""
from backend.finance import reads as FR
from backend.finance.store import catalog as SC
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

DAY = "2026-10-10"
WINDOW = ("2026-10-01", "2026-10-31")


def _records(c) -> dict[str, dict[str, str]]:
    """Two places, two jobs and a class, made the way the planners make them."""
    out: dict[str, dict[str, str]] = {"place": {}, "job": {}}
    for name in ("Denver", "Office"):
        done = act(c, OWNER, "place_create", {"name": name})
        assert done.status == "committed", done.reason
        out["place"][name] = str(done.result["place_id"])
    for name in ("Job 12", "Job 2"):
        done = act(c, OWNER, "project_create", {"name": name})
        assert done.status == "committed", done.reason
        out["job"][name] = str(done.result["project_id"])
    done = act(c, OWNER, "item_create", {"name": "Widget", "kind": "service"})
    assert done.status == "committed", done.reason
    SC.class_insert(c, dimension_id="cls-web", value="web", name="Web", active=True, at=AT, by=OWNER)
    return out


def _post(c, amount: int, tags: dict[str, str], code: str = "5100", day: str = DAY) -> None:
    done = act(c, OWNER, "journal", {"date": day, "memo": "Spend", "lines": [
        {"account_id": account(c, code), "amount": amount, "tags": tags},
        {"account_id": account(c, "1000"), "amount": -amount}]})
    assert done.status == "committed", done.reason


def _amounts(got: dict) -> dict[str, int]:
    return {str(r["label"]): int(r["amount"]) for r in got["rows"]}


def test_each_grouping_adds_up_to_the_total_with_an_unattributed_row(books):
    with books.transaction() as c:
        ids = _records(c)
        denver, office = ids["place"]["Denver"], ids["place"]["Office"]
        job12, job2 = ids["job"]["Job 12"], ids["job"]["Job 2"]
        _post(c, 1_000, {"place": denver, "class": "cls-web", "project": job12})
        _post(c, 2_500, {"place": denver})
        _post(c, 400, {"class": "cls-web", "project": job12})
        _post(c, 7_000, {"place": office, "project": job2}, "6100")
        _post(c, 300, {})
        got = {by: FR.spend_by(c, OWNER, by, *WINDOW, top_n=50, at=AT) for by in FR.PLACED}
        for by, out in got.items():
            assert out["total"] == 11_200, by
            assert sum(int(r["amount"]) for r in out["rows"]) == out["total"], by
        assert _amounts(got["place"]) == {"Denver": 3_500, "Office": 7_000, "Unattributed": 700}
        assert _amounts(got["class"]) == {"Web": 1_400, "Unattributed": 9_800}
        assert _amounts(got["project"]) == {"Job 12": 1_400, "Job 2": 7_000, "Unattributed": 2_800}
        assert _amounts(got["place_project"]) == {
            "Denver · Job 12": 1_000, "Denver · Unattributed": 2_500, "Unattributed · Job 12": 400,
            "Office · Job 2": 7_000, "Unattributed": 300}


def test_the_pnl_by_place_adds_up_to_the_pnl(books):
    with books.transaction() as c:
        ids = _records(c)
        denver, office = ids["place"]["Denver"], ids["place"]["Office"]
        _post(c, 1_000, {"place": denver, "project": ids["job"]["Job 12"]})
        _post(c, 2_500, {"place": denver})
        _post(c, 7_000, {"place": office}, "6100")
        _post(c, 300, {})
        sale = act(c, OWNER, "journal", {"date": DAY, "memo": "Sale", "lines": [
            {"account_id": account(c, "4000"), "amount": -9_000, "tags": {"place": denver}},
            {"account_id": account(c, "1000"), "amount": 9_000}]})
        assert sale.status == "committed", sale.reason
        by_place = FR.pnl_by(c, OWNER, "place", *WINDOW, basis="accrual", top_n=50, at=AT)
        plain = FR.pnl(c, OWNER, *WINDOW, basis="accrual", at=AT)
        assert (by_place["income"], by_place["expense"]) == (sum(plain["income"]), sum(plain["expense"]))
        assert sum(int(r["income"]) for r in by_place["rows"]) == by_place["income"]
        assert sum(int(r["expense"]) for r in by_place["rows"]) == by_place["expense"]
        assert {r["label"]: (r["income"], r["expense"]) for r in by_place["rows"]} == {
            "Denver": (9_000, 3_500), "Office": (0, 7_000), "Unattributed": (0, 300)}


def test_a_teammate_sees_only_their_own_lines_however_they_group(books):
    with books.transaction() as c:
        ids = _records(c)
        denver, office = ids["place"]["Denver"], ids["place"]["Office"]
        job12, job2 = ids["job"]["Job 12"], ids["job"]["Job 2"]
        _post(c, 1_000, {"place": denver, "project": job12, "teammate": "human:eve"})
        _post(c, 9_000, {"place": office, "project": job2, "teammate": "human:ben"})
        _post(c, 500, {"project": job12})                          # company-wide: not Eve's
        mine = FR.spend_by(c, "human:eve", "place_project", *WINDOW, top_n=50, at=AT)
        assert mine["total"] == 1_000
        assert _amounts(mine) == {"Denver · Job 12": 1_000}
        assert FR.pnl_by(c, "human:eve", "project", *WINDOW, basis="accrual", at=AT)["expense"] == 1_000
        assert FR.spend_by(c, OWNER, "project", *WINDOW, top_n=50, at=AT)["total"] == 10_500


def test_a_tag_value_cannot_hold_the_rollup_separator(books):
    with books.transaction() as c:
        refused = act(c, OWNER, "journal", {"date": DAY, "memo": "Bad tag", "lines": [
            {"account_id": account(c, "5100"), "amount": 100, "tags": {"custom": "x|place=y"}},
            {"account_id": account(c, "1000"), "amount": -100}]})
        assert refused.status != "committed"
        assert refused.code == "invalid"


def test_search_finds_items_places_and_projects_by_name_and_pages(books):
    with books.transaction() as c:
        _records(c)
        found = FR.search(c, OWNER, kind="place", q="den", at=AT)
        assert [(r["kind"], r["label"]) for r in found["results"]] == [("place", "Denver")]
        assert [r["label"] for r in FR.search(c, OWNER, kind="project", q="job", at=AT)["results"]] == \
            ["Job 12", "Job 2"]
        assert [(r["kind"], r["label"]) for r in FR.search(c, OWNER, kind="item", q="wid", at=AT)["results"]] == \
            [("item", "Widget")]
        across = FR.search(c, OWNER, kind=None, q="den", at=AT)["results"]
        assert ("place", "Denver") in {(r["kind"], r["label"]) for r in across}
        page = FR.search(c, OWNER, kind="project", limit=1, at=AT)
        seen: list[str] = []
        while True:
            seen += [str(r["label"]) for r in page["results"]]
            if not page["next_cursor"]:
                break
            page = FR.search(c, OWNER, kind="project", limit=1, cursor=page["next_cursor"], at=AT)
        assert seen == ["Job 12", "Job 2"]
