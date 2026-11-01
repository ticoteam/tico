"""CSV import of shared records (backend/finance/record_imports.py, backend/records/csvimport.py): a row is one Finance
action, a file imported again creates nothing (X19), a bad row does not stop the rest, a likely duplicate waits for a
person, and an imported item keeps what a person changed (M1). Equipment and due dates land on sibling branches; their
cases skip until those kinds are on this install."""
import pytest

from backend.finance import actions as FA
from backend.finance import record_imports as RI
from backend.finance.core import kinds as K
from backend.finance.core import rights as R
from backend.finance.core.errors import FinanceError
from backend.records import csvimport as CI
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

LOCATIONS = "name,kind,street,city,state,postal_code\nAcme shop,store,12 Main St,Springfield,IL,62701\n" \
            "Acme depot,warehouse,40 Depot Rd,Springfield,IL,62702\n"
ITEMS = "name,sku,kind,unit,price\nAcme gasket,GSK-100,inventory,each,4.25\n"
needs_equipment = pytest.mark.skipif("equipment_create" not in K.LIVE,
                                     reason="equipment_create lands with the equipment branch")
needs_due_dates = pytest.mark.skipif("due_date_create" not in K.LIVE,
                                     reason="due_date_create lands with the due dates branch")


def sites(n: int = 40) -> str:
    rows = ["name,kind,street,city,state,postal_code"]
    rows += [f"Site {i:02d},customer_site,{100 + i} Oak Ave,Springfield,IL,62701" for i in range(n)]
    return "\n".join(rows) + "\n"


def count(c, table: str) -> int:
    return int(c.execute(f"SELECT count(*) FROM {table} WHERE archived_at IS NULL").fetchone()[0])


# Unit cases: the cell rules every record type shares. No database.

@pytest.mark.parametrize("text, minor", [("89", 8900), ("89.5", 8950), ("$89.50", 8950), ("1,200", 120000),
                                         ("0.05", 5)])
def test_money_is_minor_units(text, minor):
    assert CI.money(text, "price") == minor


@pytest.mark.parametrize("text", ["89.505", "-3", "1e3", "", "$"])
def test_a_fraction_of_a_cent_is_refused_not_rounded(text):
    with pytest.raises(CI.RowError):
        CI.money(text, "price")


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=x", "\r=x"])
def test_a_cell_a_spreadsheet_would_run_is_kept_as_text(text):
    assert CI.safe_cell(text).startswith("'")


def test_plain_text_is_not_changed_by_the_formula_guard():
    assert CI.safe_cell("12 Main St") == "12 Main St"


def test_a_file_over_the_byte_cap_or_the_row_cap_is_refused_whole():
    with pytest.raises(CI.FileRefused) as big:
        CI.read("name\n" + "x" * CI.MAX_BYTES, ("name",), ("name",))
    assert big.value.code == "too_large"
    many = "name\n" + "".join(f"Site {i}\n" for i in range(CI.MAX_ROWS + 1))
    with pytest.raises(CI.FileRefused) as rows:
        CI.read(many, ("name",), ("name",))
    assert rows.value.code == "too_large"


def test_a_file_without_a_required_column_is_refused_before_any_row():
    with pytest.raises(CI.FileRefused) as e:
        CI.read("kind\nstore\n", ("name", "kind"), ("name",))
    assert e.value.code == "invalid"


def test_the_same_record_has_the_same_key_whatever_the_order_of_its_fields():
    same = CI.content_key("items", {"name": "Gasket", "sku": "G1"})
    assert same == CI.content_key("items", {"sku": "G1", "name": "Gasket"})
    assert same != CI.content_key("items", {"name": "Gasket", "sku": "G2"})


# Books cases: rows become Finance actions, through the same path as hand entry.

def test_x19_the_same_file_again_creates_nothing_and_reports_already_there(books):
    with books.transaction() as c:
        before = count(c, "locations")
        first = RI.commit(c, OWNER, "locations", LOCATIONS, {}, frozenset())
        created = count(c, "locations")
    assert first["summary"] == {"created": 2} and created == before + 2
    with books.transaction() as c:
        again = RI.commit(c, OWNER, "locations", LOCATIONS, {}, frozenset())
        assert count(c, "locations") == created
    assert again["summary"] == {"already_there": 2}
    assert [line["record_id"] for line in again["lines"]] == [line["record_id"] for line in first["lines"]]


def test_x10f_a_preview_flags_each_bad_row_and_the_likely_duplicate_and_writes_nothing(books):
    text = sites() + "Site bad,garage,1 Elm St,Springfield,IL,62701\n,store,2 Elm St,Springfield,IL,62701\n" \
                     "Site 05 again,customer_site,105 OAK AVE.,Springfield,il,62701\n"
    with books.transaction() as c:
        before = count(c, "locations")
        out = RI.preview(c, OWNER, "locations", text, {})
        assert count(c, "locations") == before
    assert out["summary"] == {"new": 40, "error": 2, "possible_duplicate": 1}
    by_line = {line["line"]: line for line in out["lines"]}
    assert by_line[42]["status"] == "error" and by_line[43]["status"] == "error"
    # The duplicate names the earlier line (Site 05 is line 7) because nothing is stored yet.
    assert by_line[44]["status"] == "possible_duplicate" and by_line[44]["matches"] == {"line": "7"}


def test_x10f_a_likely_duplicate_imports_only_when_its_line_is_named(books):
    text = sites(3) + "Site 01 again,customer_site,101 OAK AVE.,Springfield,IL,62701\n"
    with books.transaction() as c:
        out = RI.commit(c, OWNER, "locations", text, {}, frozenset())
        assert out["summary"] == {"created": 3, "possible_duplicate": 1}
        again = RI.commit(c, OWNER, "locations", text, {}, frozenset({5}))
    assert again["summary"] == {"already_there": 3, "created": 1}


def test_x10f_bulk_assign_sets_the_parent_on_every_new_row(books):
    with books.transaction() as c:
        yard = act(c, OWNER, "location_create", {"name": "Springfield yard", "kind": "warehouse"}).result["location_id"]
        out = RI.commit(c, OWNER, "locations", sites(40), {"location_id": yard}, frozenset())
        under = c.execute("SELECT count(*) FROM locations WHERE parent_id=?", (yard,)).fetchone()[0]
    assert out["summary"] == {"created": 40} and under == 40


def test_m1_a_person_marks_an_imported_item_raw_material_and_a_rerun_keeps_it(books):
    with books.transaction() as c:
        first = RI.commit(c, OWNER, "items", ITEMS, {}, frozenset())
        item = first["lines"][0]["record_id"]
        act(c, OWNER, "item_update", {"item_id": item, "kind": "raw_material"})
        again = RI.commit(c, OWNER, "items", ITEMS, {}, frozenset())
        kind = c.execute("SELECT kind FROM items WHERE id=?", (item,)).fetchone()[0]
    assert again["summary"] == {"already_there": 1} and kind == "raw_material"


def test_m1_a_changed_row_for_an_existing_sku_waits_and_never_overwrites_the_item(books):
    with books.transaction() as c:
        item = RI.commit(c, OWNER, "items", ITEMS, {}, frozenset())["lines"][0]["record_id"]
        changed = "name,sku,kind,unit,price\nAcme gasket,GSK-100,inventory,each,9.99\n"
        out = RI.commit(c, OWNER, "items", changed, {}, frozenset())
        price = c.execute("SELECT price FROM items WHERE id=?", (item,)).fetchone()[0]
    assert out["summary"] == {"possible_duplicate": 1} and price == 425


def test_a_teammate_needs_the_records_right_and_the_kinds_right_before_a_file_imports(books):
    """Plan §3: the records right gates every import; the action's own right (finance.book for a location) still
    applies on top, so a file is refused whole until both are held."""
    with books.transaction() as c:
        with pytest.raises(FinanceError) as nothing:
            RI.preview(c, "human:ben", "locations", LOCATIONS, {})
        assert nothing.value.code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": K.NEEDS["location_create"]}).status \
            == "committed"
        with pytest.raises(FinanceError) as no_records:
            RI.preview(c, "human:ben", "locations", LOCATIONS, {})
        assert no_records.value.code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": R.RECORDS}).status == "committed"
        out = RI.preview(c, "human:ben", "locations", LOCATIONS, {})
    assert out["summary"] == {"new": 2}


def test_a_bad_row_does_not_stop_the_good_rows_and_the_fixed_file_finishes_the_rest(books):
    first = "name,kind\nAcme shop,store\nAcme depot,garage\nAcme yard,warehouse\n"
    fixed = "name,kind\nAcme shop,store\nAcme depot,warehouse\nAcme yard,warehouse\n"
    with books.transaction() as c:
        out = RI.commit(c, OWNER, "locations", first, {}, frozenset())
        assert out["summary"] == {"created": 2, "error": 1}
        done = RI.commit(c, OWNER, "locations", fixed, {}, frozenset())
    assert done["summary"] == {"already_there": 2, "created": 1}


def test_the_report_keeps_a_name_a_spreadsheet_would_run_as_text(books):
    with books.transaction() as c:
        out = RI.preview(c, OWNER, "locations", 'name,kind\n=HYPERLINK("x"),store\n', {}, report=True)
    assert "'=HYPERLINK" in out["report_csv"]


@needs_equipment
def test_x10f_150_equipment_rows_flag_a_repeated_serial_and_bulk_assign_a_location(books):
    rows = ["name,kind,serial,make", *[f"Van {i},owned,SN-{i:04d},Acme" for i in range(150)],
            "Van again,owned,SN-0007,Acme"]
    with books.transaction() as c:
        shop = act(c, OWNER, "location_create", {"name": "Acme shop", "kind": "store"}).result["location_id"]
        out = RI.commit(c, OWNER, "equipment", "\n".join(rows) + "\n", {"location_id": shop}, frozenset())
        placed = c.execute("SELECT count(*) FROM equipment WHERE location_id=?", (shop,)).fetchone()[0]
    assert out["summary"] == {"created": 150, "possible_duplicate": 1} and placed == 150


@needs_due_dates
def test_a_due_date_names_its_subject_and_an_unknown_subject_is_an_error(books):
    text = ("name,subject_type,subject,repeats,starts_on\n"
            "Change filter,location,Acme shop,monthly,2026-12-01\n"
            "Boiler check,location,Nowhere,yearly,2026-12-01\n")
    with books.transaction() as c:
        act(c, OWNER, "location_create", {"name": "Acme shop", "kind": "store"})
        out = RI.preview(c, OWNER, "due_dates", text, {})
    assert out["summary"] == {"new": 1, "error": 1}


# HTTP case: the two routes, the rights refusal, the size refusal and the Idempotency-Key replay.

def test_the_import_routes_refuse_a_member_without_the_right_and_replay_a_retry_once(api):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    body = {"record_type": "locations", "csv": LOCATIONS}
    assert post(api, "imports/preview", body)["summary"] == {"new": 2}
    post(api, "imports/preview", body, token="ben-test", expected=403)
    post(api, "imports/preview", {"record_type": "locations", "csv": "name\n" + "x" * 1_100_000}, expected=413)
    first = post(api, "imports/commit", body, key="import-once")
    assert first["summary"] == {"created": 2}
    assert post(api, "imports/commit", body, key="import-once")["summary"] == first["summary"]
    assert post(api, "imports/commit", body)["summary"] == {"already_there": 2}
