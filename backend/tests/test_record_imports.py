"""CSV import of shared records (backend/finance/record_imports.py, backend/records/csvimport.py): a row is one Finance
action, a file imported again creates nothing (X19), a bad row does not stop the rest, a likely duplicate waits for a
person, and an imported item keeps what a person changed (M1). A teammate who is not a company Admin lacks the
records right: `api_settings` makes ben an Admin."""
import json

import pytest

from backend.finance import actions as FA
from backend.finance import record_imports as RI
from backend.finance.core import rights as R
from backend.finance.core.errors import FinanceError
from backend.finance.store import ledger as SL
from backend.records import csvimport as CI
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

LOCATIONS = "name,kind,street,city,state,postal_code\nAcme shop,store,12 Main St,Springfield,IL,62701\n" \
            "Acme depot,warehouse,40 Depot Rd,Springfield,IL,62702\n"
ITEMS = "name,sku,kind,unit,price\nAcme gasket,GSK-100,inventory,each,4.25\n"


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


def test_a_teammate_needs_the_records_right_before_a_file_imports(books):
    """Plan §3: the records right gates every import, and location, item and due date creates all need only that right,
    so a file is refused whole until it is held."""
    with books.transaction() as c:
        with pytest.raises(FinanceError) as nothing:
            RI.preview(c, "human:eve", "locations", LOCATIONS, {})
        assert nothing.value.code == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": R.RECORDS}).status == "committed"
        out = RI.preview(c, "human:eve", "locations", LOCATIONS, {})
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


def test_x10f_150_equipment_rows_flag_a_repeated_serial_and_bulk_assign_a_location(books):
    rows = ["name,kind,serial,make", *[f"Van {i},owned,SN-{i:04d},Acme" for i in range(150)],
            "Van again,owned,SN-0007,Acme"]
    with books.transaction() as c:
        shop = act(c, OWNER, "location_create", {"name": "Acme shop", "kind": "store"}).result["location_id"]
        out = RI.commit(c, OWNER, "equipment", "\n".join(rows) + "\n", {"location_id": shop}, frozenset())
        placed = c.execute("SELECT count(*) FROM equipment WHERE location_id=?", (shop,)).fetchone()[0]
    assert out["summary"] == {"created": 150, "possible_duplicate": 1} and placed == 150


def test_a_due_date_names_its_subject_and_assignee_and_a_bad_row_is_only_that_row(books):
    """Equipment is missing until its table exists, so a due date on a van is a row error, not a crash."""
    text = ("name,subject_type,subject,repeats,starts_on,assignee\n"
            "Change filter,location,Acme shop,monthly,2026-12-01,ben\n"
            "Boiler check,location,Nowhere,yearly,2026-12-01,ben\n"
            "Van check,equipment,Van 1,yearly,2026-12-01,ben\n"
            "Shop check,location,Acme shop,yearly,2026-12-01,nobody\n")
    with books.transaction() as c:
        act(c, OWNER, "location_create", {"name": "Acme shop", "kind": "store"})
        out = RI.preview(c, OWNER, "due_dates", text, {})
    assert out["summary"] == {"new": 1, "error": 3}


def test_a_site_names_its_customer_and_a_bulk_assign_fills_the_rows_that_leave_it_out(books):
    text = ("name,kind,customer\n"
            "Dental roof,customer_site,Sample Dental Group\n"
            "Dental annex,customer_site,Nobody Ltd\n"
            "Ridge court,customer_site,\n")
    with books.transaction() as c:
        act(c, OWNER, "contact_create", {"name": "Sample Dental Group", "kind": "customer"})
        dental = c.execute("SELECT id FROM fin_contacts WHERE name=?", ("Sample Dental Group",)).fetchone()[0]
        out = RI.commit(c, OWNER, "locations", text, {"customer_id": dental}, frozenset())
        owners = dict(c.execute("SELECT name, customer_id FROM locations WHERE archived_at IS NULL"))
        nobody = c.execute("SELECT id FROM fin_contacts WHERE name='Nobody Ltd'").fetchone()[0]
    assert out["summary"] == {"created": 3} and out["new_customers"] == 1
    assert owners == {"Dental roof": dental, "Dental annex": nobody, "Ridge court": dental}


def test_a_customer_the_books_lack_is_added_once_and_a_second_import_adds_nothing(books):
    text = ("name,kind,customer\n"
            "Ridge court,customer_site,Ridge Apartments\n"
            "Ridge annex,customer_site,ridge apartments\n")
    with books.transaction() as c:
        preview = RI.preview(c, OWNER, "locations", text, {})
        assert preview["summary"] == {"new": 2} and preview["new_customers"] == 1
        assert preview["lines"][0]["new_customer"] == "Ridge Apartments"
        out = RI.commit(c, OWNER, "locations", text, {}, frozenset())
        contacts = c.execute("SELECT count(*) FROM fin_contacts WHERE kind='customer' AND name='Ridge Apartments'")
        made = contacts.fetchone()[0]
        owners = {row[0] for row in c.execute("SELECT customer_id FROM locations WHERE archived_at IS NULL")}
        again = RI.commit(c, OWNER, "locations", text, {}, frozenset())
    assert out["summary"] == {"created": 2} and out["new_customers"] == 1
    assert made == 1 and len(owners) == 1 and None not in owners
    assert again["summary"] == {"already_there": 2}


def test_a_customer_name_close_to_one_already_here_is_a_likely_duplicate_linked_only_when_its_line_is_named(books):
    text = "name,kind,customer\nDental roof,customer_site,Sample Dental Grp\n"
    with books.transaction() as c:
        act(c, OWNER, "contact_create", {"name": "Sample Dental Group", "kind": "customer"})
        dental = c.execute("SELECT id FROM fin_contacts WHERE name='Sample Dental Group'").fetchone()[0]
        preview = RI.preview(c, OWNER, "locations", text, {})
        held = RI.commit(c, OWNER, "locations", text, {}, frozenset())
        assert count(c, "locations") == 0
        added = RI.commit(c, OWNER, "locations", text, {}, frozenset({2}))
        linked = c.execute("SELECT customer_id FROM locations WHERE name='Dental roof'").fetchone()[0]
        customers = [row[0] for row in c.execute("SELECT name FROM fin_contacts WHERE kind='customer' ORDER BY name")]
    line = preview["lines"][0]
    assert preview["summary"] == {"possible_duplicate": 1} and line["matches"] == {"record_id": dental}
    assert "close to Sample Dental Group" in line["reason"]
    assert held["summary"] == {"possible_duplicate": 1}
    assert added["summary"] == {"created": 1} and linked != dental
    assert customers == ["Sample Dental Group", "Sample Dental Grp"]


def test_a_teammate_without_receivables_gets_a_row_error_for_a_customer_it_would_add(books):
    text = "name,kind,customer\nRidge court,customer_site,Ridge Apartments\n"
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": R.RECORDS}).status == "committed"
        out = RI.preview(c, "human:eve", "locations", text, {})
    assert out["summary"] == {"error": 1} and out["new_customers"] == 0 and out["lines"][0]["reason"]


def test_a_new_customer_in_a_mirrored_book_names_the_provider_that_holds_it(books):
    text = "name,kind,customer\nRidge court,customer_site,Ridge Apartments\n"
    with books.transaction() as c:
        c.execute("INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) "
                  "VALUES(?,?,?,?,?,?)", ("conn-qb", "quickbooks", "books", AT, OWNER, "Acme books"))
        SL.set_book_mode(c, SL.book(c).id, "mirror", "conn-qb")
        out = RI.preview(c, OWNER, "locations", text, {})
    assert out["summary"] == {"error": 1}
    assert out["lines"][0]["reason"] == "Add this customer in QuickBooks first"


def test_an_item_row_keeps_its_description_and_taxable_flag(books):
    text = "name,kind,price,description,taxable\nDuct cleaning,service,120,Whole-house duct clean,yes\n"
    with books.transaction() as c:
        out = RI.commit(c, OWNER, "items", text, {}, frozenset())
        row = c.execute("SELECT description, taxable FROM items WHERE id=?", (out["lines"][0]["record_id"],)).fetchone()
    assert out["summary"] == {"created": 1} and tuple(row) == ("Whole-house duct clean", 1)


# HTTP case: the two routes, the rights refusal, the size refusal and the Idempotency-Key replay.

def test_the_import_routes_refuse_a_member_without_the_right_and_replay_a_retry_once(api):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
    body = {"record_type": "locations", "csv": LOCATIONS}
    assert post(api, "imports/preview", body)["summary"] == {"new": 2}
    post(api, "imports/preview", body, token="cara-test", expected=403)
    post(api, "imports/preview", {"record_type": "locations", "csv": "name\n" + "x" * 1_100_000}, expected=413)
    first = post(api, "imports/commit", body, key="import-once")
    assert first["summary"] == {"created": 2}
    assert post(api, "imports/commit", body, key="import-once")["summary"] == first["summary"]
    assert post(api, "imports/commit", body)["summary"] == {"already_there": 2}


@pytest.mark.parametrize("text", ['name,kind\n"Shop,store\nDepot,warehouse\n', 'name,kind\n"Shop"x,store\n'])
def test_a_malformed_csv_file_is_refused_not_read_as_more_rows(text):
    """A quote that never closes, or text after a closing quote, refuses the file at its line: lenient parsing would
    swallow the rows after it (the import's own check, not the parser's wording)."""
    with pytest.raises(CI.FileRefused) as e:
        CI.read(text, ("name", "kind"), ("name", "kind"))
    assert e.value.code == "invalid" and "line" in e.value.message


def test_a_cell_over_the_csv_field_limit_refuses_the_file_instead_of_failing_the_request():
    with pytest.raises(CI.FileRefused) as e:
        CI.read("name,kind\nAcme," + "x" * 131_073 + "\n", ("name", "kind"), ("name", "kind"))
    assert e.value.code == "invalid"


def _customers(c, name: str) -> int:
    return int(c.execute("SELECT count(*) FROM fin_contacts WHERE kind='customer' AND name=?", (name,)).fetchone()[0])


def test_a_customer_row_never_names_a_vendor_the_teammate_cannot_see(books):
    """A teammate with the records and receivables rights and no payables view is told nothing about a vendor that a
    customer row is close to: no id, no name (a vendor needs a payables view, tab_ap.sees_contact_kind). The owner gets
    the match, so the case is real."""
    text = "name,kind,customer\nRidge court,customer_site,Sample Privat Vendor\n"
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "Sample Private Vendor", "kind": "vendor"})
                     .result["contact_id"])
        for right in (R.RECORDS, R.AR):
            assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": right}).status == "committed"
        owner_view = RI.preview(c, OWNER, "locations", text, {})
        teammate_view = RI.preview(c, "human:eve", "locations", text, {})
    told = json.dumps(teammate_view)
    assert owner_view["summary"] == {"possible_duplicate": 1}
    assert teammate_view["summary"] == {"new": 1}
    assert vendor not in told and "Sample Private Vendor" not in told


def test_a_bot_without_the_records_right_sends_each_row_for_approval_instead_of_refusing_the_file(books):
    """A bot lacks the records right, so its rows need an approval, as its hand entry does (test_x13). The file is not
    refused whole, and a preview asks for nothing: the commit makes the approvals."""
    with books.transaction() as c:
        preview = RI.preview(c, "bot:coder", "locations", LOCATIONS, {})
        assert preview["summary"] == {"needs_approval": 2}
        assert {line["approval_id"] for line in preview["lines"]} == {""}
        done = RI.commit(c, "bot:coder", "locations", LOCATIONS, {}, frozenset())
        assert count(c, "locations") == 0
    assert done["summary"] == {"needs_approval": 2}
    assert all(line["approval_id"] for line in done["lines"])


def test_a_customer_equipment_row_adds_its_new_customer_once_and_a_preview_writes_nothing(books):
    text = "name,kind,customer\nFurnace,customer,Ridge Apartments\nBoiler,customer,ridge apartments\n"
    with books.transaction() as c:
        preview = RI.preview(c, OWNER, "equipment", text, {})
        assert preview["summary"] == {"new": 2} and preview["new_customers"] == 1
        assert _customers(c, "Ridge Apartments") == 0
        out = RI.commit(c, OWNER, "equipment", text, {}, frozenset())
        owners = {row[0] for row in c.execute("SELECT customer_id FROM equipment WHERE archived_at IS NULL")}
        made = _customers(c, "Ridge Apartments")
    assert out["summary"] == {"created": 2} and made == 1
    assert len(owners) == 1 and None not in owners


def test_a_customer_equipment_row_refused_at_its_location_adds_no_customer(books):
    """The location belongs to another customer, so the row is refused in the preview and the commit alike, and the
    customer it names is never added."""
    text = "name,kind,location,customer\nFurnace,customer,Dental roof,Ridge Apartments\n"
    with books.transaction() as c:
        RI.commit(c, OWNER, "locations", "name,kind,customer\nDental roof,customer_site,Sample Dental Group\n", {},
                  frozenset())
        preview = RI.preview(c, OWNER, "equipment", text, {})
        out = RI.commit(c, OWNER, "equipment", text, {}, frozenset())
        made = _customers(c, "Ridge Apartments")
    assert preview["summary"] == {"error": 1} and "another customer" in preview["lines"][0]["reason"]
    assert out["summary"] == {"error": 1} and made == 0
