"""Reads by location, class and project: each grouping's rows add up to the total, lines with none of the tags fall in
Unattributed, the P&L by location adds up to the P&L, a teammate sees only their own lines however they group, a tag
value
cannot hold the rollup key's separator, and search finds items, locations and projects by name."""
import pytest

from backend.finance import reads as FR
from backend.finance.core.errors import DENIED, NOT_FOUND, FinanceError
from backend.finance.store import catalog as SC
from backend.records import store as RS
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

DAY = "2026-10-10"
WINDOW = ("2026-10-01", "2026-10-31")


def _records(c) -> dict[str, dict[str, str]]:
    """Two locations, two jobs and a class, made the way the planners make them."""
    out: dict[str, dict[str, str]] = {"location": {}, "job": {}}
    for name in ("Denver", "Office"):
        done = act(c, OWNER, "location_create", {"name": name})
        assert done.status == "committed", done.reason
        out["location"][name] = str(done.result["location_id"])
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
        denver, office = ids["location"]["Denver"], ids["location"]["Office"]
        job12, job2 = ids["job"]["Job 12"], ids["job"]["Job 2"]
        _post(c, 1_000, {"location": denver, "class": "cls-web", "project": job12})
        _post(c, 2_500, {"location": denver})
        _post(c, 400, {"class": "cls-web", "project": job12})
        _post(c, 7_000, {"location": office, "project": job2}, "6100")
        _post(c, 300, {})
        got = {by: FR.spend_by(c, OWNER, by, *WINDOW, top_n=50, at=AT) for by in FR.LOCATED}
        for by, out in got.items():
            assert out["total"] == 11_200, by
            assert sum(int(r["amount"]) for r in out["rows"]) == out["total"], by
        assert _amounts(got["location"]) == {"Denver": 3_500, "Office": 7_000, "Unattributed": 700}
        assert _amounts(got["class"]) == {"Web": 1_400, "Unattributed": 9_800}
        assert _amounts(got["project"]) == {"Job 12": 1_400, "Job 2": 7_000, "Unattributed": 2_800}
        assert _amounts(got["location_project"]) == {
            "Denver · Job 12": 1_000, "Denver · Unattributed": 2_500, "Unattributed · Job 12": 400,
            "Office · Job 2": 7_000, "Unattributed": 300}


def test_the_pnl_by_location_adds_up_to_the_pnl(books):
    with books.transaction() as c:
        ids = _records(c)
        denver, office = ids["location"]["Denver"], ids["location"]["Office"]
        _post(c, 1_000, {"location": denver, "project": ids["job"]["Job 12"]})
        _post(c, 2_500, {"location": denver})
        _post(c, 7_000, {"location": office}, "6100")
        _post(c, 300, {})
        sale = act(c, OWNER, "journal", {"date": DAY, "memo": "Sale", "lines": [
            {"account_id": account(c, "4000"), "amount": -9_000, "tags": {"location": denver}},
            {"account_id": account(c, "1000"), "amount": 9_000}]})
        assert sale.status == "committed", sale.reason
        by_location = FR.pnl_by(c, OWNER, "location", *WINDOW, basis="accrual", top_n=50, at=AT)
        plain = FR.pnl(c, OWNER, *WINDOW, basis="accrual", at=AT)
        assert (by_location["income"], by_location["expense"]) == (sum(plain["income"]), sum(plain["expense"]))
        assert sum(int(r["income"]) for r in by_location["rows"]) == by_location["income"]
        assert sum(int(r["expense"]) for r in by_location["rows"]) == by_location["expense"]
        assert {r["label"]: (r["income"], r["expense"]) for r in by_location["rows"]} == {
            "Denver": (9_000, 3_500), "Office": (0, 7_000), "Unattributed": (0, 300)}


def test_a_teammate_sees_only_their_own_lines_however_they_group(books):
    with books.transaction() as c:
        ids = _records(c)
        denver, office = ids["location"]["Denver"], ids["location"]["Office"]
        job12, job2 = ids["job"]["Job 12"], ids["job"]["Job 2"]
        _post(c, 1_000, {"location": denver, "project": job12, "teammate": "human:eve"})
        _post(c, 9_000, {"location": office, "project": job2, "teammate": "human:ben"})
        _post(c, 500, {"project": job12})                          # company-wide: not Eve's
        mine = FR.spend_by(c, "human:eve", "location_project", *WINDOW, top_n=50, at=AT)
        assert mine["total"] == 1_000
        assert _amounts(mine) == {"Denver · Job 12": 1_000}
        assert FR.pnl_by(c, "human:eve", "project", *WINDOW, basis="accrual", at=AT)["expense"] == 1_000
        assert FR.spend_by(c, OWNER, "project", *WINDOW, top_n=50, at=AT)["total"] == 10_500


def test_a_tag_value_cannot_hold_the_rollup_separator(books):
    with books.transaction() as c:
        refused = act(c, OWNER, "journal", {"date": DAY, "memo": "Bad tag", "lines": [
            {"account_id": account(c, "5100"), "amount": 100, "tags": {"custom": "x|location=y"}},
            {"account_id": account(c, "1000"), "amount": -100}]})
        assert refused.status != "committed"
        assert refused.code == "invalid"


def test_search_finds_items_locations_and_projects_by_name_and_pages(books):
    with books.transaction() as c:
        _records(c)
        found = FR.search(c, OWNER, kind="location", q="den", at=AT)
        assert [(r["kind"], r["label"]) for r in found["results"]] == [("location", "Denver")]
        assert [r["label"] for r in FR.search(c, OWNER, kind="project", q="job", at=AT)["results"]] == \
            ["Job 12", "Job 2"]
        assert [(r["kind"], r["label"]) for r in FR.search(c, OWNER, kind="item", q="wid", at=AT)["results"]] == \
            [("item", "Widget")]
        across = FR.search(c, OWNER, kind=None, q="den", at=AT)["results"]
        assert ("location", "Denver") in {(r["kind"], r["label"]) for r in across}
        page = FR.search(c, OWNER, kind="project", limit=1, at=AT)
        seen: list[str] = []
        while True:
            seen += [str(r["label"]) for r in page["results"]]
            if not page["next_cursor"]:
                break
            page = FR.search(c, OWNER, kind="project", limit=1, cursor=page["next_cursor"], at=AT)
        assert seen == ["Job 12", "Job 2"]


def test_record_lists_page_by_name_and_hide_archived_by_default(books):
    with books.transaction() as c:
        locations = _records(c)["location"]
        page = FR.records(c, OWNER, kind="location", limit=1, at=AT)
        assert [r["name"] for r in page["locations"]] == ["Denver"] and page["next_cursor"]
        rest = FR.records(c, OWNER, kind="location", limit=1, cursor=page["next_cursor"], at=AT)
        assert [r["name"] for r in rest["locations"]] == ["Office"] and rest["next_cursor"] is None
        RS.update(c, "locations", locations["Office"], {"archived_at": AT})
        assert [r["name"] for r in FR.records(c, OWNER, kind="location", at=AT)["locations"]] == ["Denver"]
        assert [r["name"] for r in FR.records(c, OWNER, kind="location", archived="only", at=AT)["locations"]] == \
            ["Office"]
        assert len(FR.records(c, OWNER, kind="location", archived="all", at=AT)["locations"]) == 2


def test_item_records_are_found_by_sku_and_show_their_accounts_to_the_owner(books):
    with books.transaction() as c:
        _records(c)
        made = act(c, OWNER, "item_create", {"name": "Gadget", "kind": "service", "sku": "GDG-9", "price": 2500,
                                             "income_account_id": account(c, "4000")})
        assert made.status == "committed", made.reason
        found = FR.records(c, OWNER, kind="item", q="gdg", at=AT)["items"]
        assert [r["name"] for r in found] == ["Gadget"]
        assert found[0]["price"] == 2500 and found[0]["sku"] == "GDG-9"
        assert found[0]["income_account_id"] == account(c, "4000") and found[0]["expense_account_id"] is None
        shown = FR.record(c, OWNER, kind="item", record_id=found[0]["id"], at=AT)["item"]
        assert shown["income_account_id"] == account(c, "4000")


def test_a_teammate_sees_an_items_price_and_its_accounts_only_where_they_see_the_accounts(books):
    with books.transaction() as c:
        _records(c)
        made = act(c, OWNER, "item_create", {"name": "Gadget", "kind": "service", "price": 2500,
                                             "income_account_id": account(c, "4000"),
                                             "expense_account_id": account(c, "5100")})
        assert made.status == "committed", made.reason
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "payables"}).status == "committed"
        gadget = {r["name"]: r for r in FR.records(c, "human:eve", kind="item", at=AT)["items"]}["Gadget"]
        assert gadget["kind"] == "service" and gadget["price"] == 2500      # a teammate sees the sale price
        assert not {"income_account_id", "expense_account_id"} & set(gadget)
        assert act(c, OWNER, "view_grant", {"subject": "human:eve", "what": "expenses"}).status == "committed"
        gadget = {r["name"]: r for r in FR.records(c, "human:eve", kind="item", at=AT)["items"]}["Gadget"]
        assert gadget["expense_account_id"] == account(c, "5100") and gadget["price"] == 2500
        assert "income_account_id" not in gadget


def test_x11b_a_guest_is_refused_the_records_and_their_search(books):
    with books.transaction() as c:
        locations = _records(c)["location"]
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        for call in (lambda: FR.records(c, "human:stranger", kind="location", at=AT),
                     lambda: FR.record(c, "human:stranger", kind="location", record_id=locations["Denver"], at=AT),
                     lambda: FR.search(c, "human:stranger", kind="item", q="wid", at=AT)):
            with pytest.raises(FinanceError) as refused:
                call()
            assert refused.value.code == DENIED
        across = FR.search(c, "human:stranger", kind=None, q="den", at=AT)["results"]
        assert all(r["kind"] not in ("location", "item", "project") for r in across)


def test_a_line_tagged_with_a_record_by_its_alias_is_stored_under_the_row_it_is(books):
    with books.transaction() as c:
        denver = _records(c)["location"]["Denver"]
        RS.alias(c, "locations", "loc-merged-away", denver, AT)
        _post(c, 1_000, {"location": "loc-merged-away"})
        assert [str(r[0]) for r in c.execute("SELECT value FROM fin_line_tags WHERE kind='location'")] == [denver]
        assert _amounts(FR.spend_by(c, OWNER, "location", *WINDOW, top_n=50, at=AT))["Denver"] == 1_000


def test_a_record_read_by_an_id_it_was_merged_under_resolves_to_the_row_kept(books):
    with books.transaction() as c:
        denver = _records(c)["location"]["Denver"]
        RS.alias(c, "locations", "loc-merged-away", denver, AT)
        assert FR.record(c, OWNER, kind="location", record_id="loc-merged-away", at=AT)["location"]["id"] == denver
        with pytest.raises(FinanceError) as missing:
            FR.record(c, OWNER, kind="location", record_id="loc-never-existed", at=AT)
        assert missing.value.code == NOT_FOUND


def _made(c, action: str, payload: dict, key: str) -> str:
    done = act(c, OWNER, action, payload)
    assert done.status == "committed", done.reason
    return str(done.result[key])


def test_location_lists_narrow_by_kind_page_through_it_and_carry_the_next_due_day(books):
    with books.transaction() as c:
        denver = _made(c, "location_create", {"name": "Denver", "kind": "store"}, "location_id")
        _made(c, "location_create", {"name": "Boise", "kind": "store"}, "location_id")
        _made(c, "location_create", {"name": "Office", "kind": "office"}, "location_id")
        due = act(c, OWNER, "due_date_create", {"name": "Permit", "subject_type": "location", "subject_id": denver,
                                                "repeats": "once", "starts_on": "2026-11-05", "assignee_id": OWNER})
        assert due.status == "committed", due.reason
        first = FR.records(c, OWNER, kind="location", equal={"kind": "store"}, limit=1, at=AT)
        assert [r["name"] for r in first["locations"]] == ["Boise"] and first["next_cursor"]
        rest = FR.records(c, OWNER, kind="location", equal={"kind": "store"}, limit=1, at=AT,
                          cursor=first["next_cursor"])
        assert [r["name"] for r in rest["locations"]] == ["Denver"] and rest["next_cursor"] is None
        assert rest["locations"][0]["next_due_on"] == "2026-11-05" and first["locations"][0]["next_due_on"] is None
        office = FR.records(c, OWNER, kind="location", equal={"kind": "office"}, at=AT)["locations"]
        assert [r["name"] for r in office] == ["Office"]


def test_location_lists_narrow_by_customer_and_page(books):
    with books.transaction() as c:
        acme = _made(c, "contact_create", {"name": "Acme Retail", "kind": "customer"}, "contact_id")
        bolt = _made(c, "contact_create", {"name": "Bolt & Co", "kind": "customer"}, "contact_id")
        _made(c, "location_create", {"name": "Acme Denver", "customer_id": acme}, "location_id")
        _made(c, "location_create", {"name": "Acme Boise", "customer_id": acme}, "location_id")
        _made(c, "location_create", {"name": "Bolt Yard", "customer_id": bolt}, "location_id")
        _made(c, "location_create", {"name": "Plain"}, "location_id")
        first = FR.records(c, OWNER, kind="location", equal={"customer_id": acme}, limit=1, at=AT)
        assert [r["name"] for r in first["locations"]] == ["Acme Boise"] and first["next_cursor"]
        rest = FR.records(c, OWNER, kind="location", equal={"customer_id": acme}, limit=1, cursor=first["next_cursor"],
                          at=AT)
        assert [r["name"] for r in rest["locations"]] == ["Acme Denver"] and rest["next_cursor"] is None


def test_item_lists_narrow_by_kind_and_page(books):
    with books.transaction() as c:
        for name in ("Ann Consult", "Bea Consult"):
            _made(c, "item_create", {"name": name, "kind": "service"}, "item_id")
        _made(c, "item_create", {"name": "Cog", "kind": "raw_material"}, "item_id")
        first = FR.records(c, OWNER, kind="item", equal={"kind": "service"}, limit=1, at=AT)
        assert [r["name"] for r in first["items"]] == ["Ann Consult"] and first["next_cursor"]
        rest = FR.records(c, OWNER, kind="item", equal={"kind": "service"}, limit=1, cursor=first["next_cursor"], at=AT)
        assert [r["name"] for r in rest["items"]] == ["Bea Consult"] and rest["next_cursor"] is None
        raw = FR.records(c, OWNER, kind="item", equal={"kind": "raw_material"}, at=AT)["items"]
        assert [r["name"] for r in raw] == ["Cog"]


def test_project_lists_narrow_by_status_and_page(books):
    with books.transaction() as c:
        ids = {name: _made(c, "project_create", {"name": name}, "project_id") for name in ("Job 1", "Job 2", "Job 3")}
        closed = act(c, OWNER, "project_update", {"project_id": ids["Job 2"], "status": "closed"})
        assert closed.status == "committed", closed.reason
        first = FR.records(c, OWNER, kind="project", equal={"status": "open"}, limit=1, at=AT)
        assert [r["name"] for r in first["projects"]] == ["Job 1"] and first["next_cursor"]
        rest = FR.records(c, OWNER, kind="project", equal={"status": "open"}, limit=1, at=AT,
                          cursor=first["next_cursor"])
        assert [r["name"] for r in rest["projects"]] == ["Job 3"] and rest["next_cursor"] is None
        done = FR.records(c, OWNER, kind="project", equal={"status": "closed"}, at=AT)["projects"]
        assert [r["name"] for r in done] == ["Job 2"]


def test_project_lists_narrow_by_customer_and_page(books):
    with books.transaction() as c:
        acme = _made(c, "contact_create", {"name": "Acme Retail", "kind": "customer"}, "contact_id")
        bolt = _made(c, "contact_create", {"name": "Bolt & Co", "kind": "customer"}, "contact_id")
        _made(c, "project_create", {"name": "Acme Job A", "customer_id": acme}, "project_id")
        _made(c, "project_create", {"name": "Acme Job B", "customer_id": acme}, "project_id")
        _made(c, "project_create", {"name": "Bolt Job", "customer_id": bolt}, "project_id")
        first = FR.records(c, OWNER, kind="project", equal={"customer_id": acme}, limit=1, at=AT)
        assert [r["name"] for r in first["projects"]] == ["Acme Job A"] and first["next_cursor"]
        rest = FR.records(c, OWNER, kind="project", equal={"customer_id": acme}, limit=1, cursor=first["next_cursor"],
                          at=AT)
        assert [r["name"] for r in rest["projects"]] == ["Acme Job B"] and rest["next_cursor"] is None


def test_a_teammate_without_a_finance_view_narrows_the_locations_by_customer(books):
    with books.transaction() as c:
        acme = _made(c, "contact_create", {"name": "Acme Retail", "kind": "customer"}, "contact_id")
        bolt = _made(c, "contact_create", {"name": "Bolt & Co", "kind": "customer"}, "contact_id")
        _made(c, "location_create", {"name": "Acme Denver", "customer_id": acme}, "location_id")
        _made(c, "location_create", {"name": "Acme Boise", "customer_id": acme}, "location_id")
        _made(c, "location_create", {"name": "Bolt Yard", "customer_id": bolt}, "location_id")
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        first = FR.records(c, "human:eve", kind="location", equal={"customer_id": acme}, limit=1, at=AT)
        assert [r["name"] for r in first["locations"]] == ["Acme Boise"] and first["next_cursor"]
        assert first["locations"][0]["customer_name"] == "Acme Retail"
        rest = FR.records(c, "human:eve", kind="location", equal={"customer_id": acme}, limit=1,
                          cursor=first["next_cursor"], at=AT)
        assert [r["name"] for r in rest["locations"]] == ["Acme Denver"] and rest["next_cursor"] is None


def test_a_location_detail_carries_its_next_due_day_as_its_list_does(books):
    with books.transaction() as c:
        denver = _made(c, "location_create", {"name": "Denver", "kind": "store"}, "location_id")
        due = act(c, OWNER, "due_date_create", {"name": "Permit", "subject_type": "location", "subject_id": denver,
                                                "repeats": "once", "starts_on": "2026-11-05", "assignee_id": OWNER})
        assert due.status == "committed", due.reason
        assert FR.records(c, OWNER, kind="location", at=AT)["locations"][0]["next_due_on"] == "2026-11-05"
        assert FR.record(c, OWNER, kind="location", record_id=denver, at=AT)["location"]["next_due_on"] == "2026-11-05"
