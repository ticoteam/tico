"""Equipment (backend/records): vans are owned pieces with no location of their own (H2), a bot's photo draft asks the
owner while a person's create goes straight through (H7), a vendor's terms set a bill's default due date (V1), a
renewal stage counts to the cancel-by day (P3), the database refuses a kind outside its CHECK (X16), and a photo is
read where its equipment is read (X10e)."""
import sqlite3

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core.errors import NOT_FOUND
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

PHOTO = "lib-file-data-plate"


def _vendor(c, name="Acme Supplies", kind="vendor") -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": kind})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _count(c, table: str) -> int:
    return int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_three_vans_are_owned_equipment_and_none_makes_a_location(books):  # noqa: F811
    """H2: vans carry make, model and VIN as serial, sit at the shop (a location made once), and no location is
    created for a van itself."""
    with books.transaction() as c:
        shop = str(act(c, OWNER, "location_create", {"name": "Acme Shop", "kind": "warehouse"}).result["location_id"])
        before = _count(c, "locations")
        vans = []
        for n, vin in enumerate(("1FTBW2CM5NKA00001", "1FTBW2CM5NKA00002", "1FTBW2CM5NKA00003"), start=1):
            made = act(c, OWNER, "equipment_create", {"name": f"Van {n}", "kind": "owned", "location_id": shop,
                                                       "make": "Ford", "model": "Transit 250", "serial": vin})
            assert made.status == "committed", made
            vans.append(str(made.result["equipment_id"]))
        assert _count(c, "locations") == before
        rows = c.execute("SELECT kind, location_id, customer_id, serial FROM equipment ORDER BY name").fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [("owned", shop, None)] * 3
        assert FR.records(c, OWNER, kind="equipment")["equipment"][0]["serial"] == "1FTBW2CM5NKA00001"
        # Owned equipment names no customer; customer equipment names one, and it is a customer's.
        assert act(c, OWNER, "equipment_create", {"name": "Van 4", "kind": "owned",
                                                   "customer_id": _vendor(c, "Acme Retail", "customer")}).code == "invalid"
        assert act(c, OWNER, "equipment_create", {"name": "Unit", "kind": "customer"}).code == "invalid"
        assert act(c, OWNER, "equipment_create", {"name": "Unit", "kind": "customer",
                                                   "customer_id": _vendor(c, "Acme Co")}).code == NOT_FOUND
        assert act(c, OWNER, "equipment_create", {"name": "Van 5", "kind": "truck"}).code == "invalid"


def test_archive_and_filters_find_equipment_by_customer_kind_location_and_warranty(books):  # noqa: F811
    with books.transaction() as c:
        shop = str(act(c, OWNER, "location_create", {"name": "Acme Shop"}).result["location_id"])
        smith = _vendor(c, "Smith Homes", "customer")
        van = str(act(c, OWNER, "equipment_create", {"name": "Van 1", "kind": "owned",
                                                      "location_id": shop}).result["equipment_id"])
        furnace = str(act(c, OWNER, "equipment_create", {"name": "Furnace", "kind": "customer", "customer_id": smith,
                                                          "warranty_ends_on": "2026-12-01"}).result["equipment_id"])
        act(c, OWNER, "equipment_create", {"name": "Boiler", "kind": "customer", "customer_id": smith,
                                           "warranty_ends_on": "2027-06-01"})

        def names(**filters) -> list[str]:
            """The labels of the active equipment a search keeps (archived rows show only when asked for)."""
            return sorted(r["label"] for r in FR.search(c, OWNER, kind="equipment",
                                                        filters={"active": 1, **filters})["results"])
        assert names(customer_id=smith) == ["Boiler", "Furnace"]
        assert names(kind="owned") == ["Van 1"]
        assert names(location_id=shop) == ["Van 1"]
        assert names(warranty_ends_by="2026-12-31") == ["Furnace"]
        assert names(kind="customer", customer_id=smith, warranty_ends_by="2027-12-31") == ["Boiler", "Furnace"]
        assert act(c, OWNER, "equipment_update", {"equipment_id": furnace, "active": False}).status == "committed"
        assert names(customer_id=smith) == ["Boiler"]
        assert [r["name"] for r in FR.records(c, OWNER, kind="equipment", archived="only")["equipment"]] == ["Furnace"]
        assert act(c, OWNER, "equipment_update", {"equipment_id": van, "location_id": None}).status == "committed"
        assert names(location_id=shop) == []


def test_a_bots_photo_draft_asks_the_owner_with_the_photo_and_a_persons_create_goes_straight_through(books):  # noqa: F811
    """H7 (gate side): a bot with the records right still asks, the approval carries the photo, and the owner's
    approval commits the record. A bot's create without a photo, and a person's create with one, need no approval."""
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.records"}).status == "committed"
        smith = _vendor(c, "Smith Homes", "customer")
        draft = act(c, "bot:coder", "equipment_create", {"name": "Furnace", "kind": "customer", "customer_id": smith,
                                                          "make": "Acme", "model": "H-90", "serial": "SN-1",
                                                          "photo_file_id": PHOTO})
        assert (draft.status, draft.approver) == ("needs_approval", "human:ana")
        row = H.approval(c, draft.approval_id)
        assert row["payload"]["payload"]["photo_file_id"] == PHOTO
        assert _count(c, "equipment") == 0
        H.approval_decide(c, "human:ana", draft.approval_id, "approved")
        done = FA.approval_decided(c, draft.approval_id, AT)
        assert done.status == "committed", done
        assert tuple(c.execute("SELECT photo_file_id, serial FROM equipment").fetchone()) == (PHOTO, "SN-1")
        assert act(c, "bot:coder", "equipment_create", {"name": "Hand truck", "kind": "owned"}).status == "committed"
        assert act(c, OWNER, "equipment_create", {"name": "Plate", "kind": "owned",
                                                  "photo_file_id": PHOTO}).status == "committed"
        # Without the records right a teammate is refused, and a grant lets them create.
        assert act(c, "human:dan", "equipment_create", {"name": "Ladder", "kind": "owned"}).status == "denied"
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        assert act(c, "human:dan", "equipment_create", {"name": "Ladder", "kind": "owned"}).status == "committed"


def test_a_new_bill_is_due_on_the_vendors_terms_unless_its_due_date_is_typed(books):  # noqa: F811
    """V1: net 30 gives a due date of bill date plus 30 days; a due date typed on the bill wins; a vendor with no terms
    gives none."""
    with books.transaction() as c:
        vid = _vendor(c)
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "payment_terms_days": 30}).status == "committed"
        lines = [{"account_id": account(c, "6900"), "amount": 20_000}]
        plain = str(act(c, OWNER, "bill_create", {"vendor_id": vid, "bill_date": "2026-10-10",
                                                  "lines": lines}).result["bill_id"])
        typed = str(act(c, OWNER, "bill_create", {"vendor_id": vid, "bill_date": "2026-10-10", "due_date": "2026-10-25",
                                                  "lines": lines}).result["bill_id"])
        assert tuple(c.execute("SELECT due_date, terms_days FROM fin_bills WHERE id=?", (plain,)).fetchone()) == \
            ("2026-11-09", 30)
        assert c.execute("SELECT due_date FROM fin_bills WHERE id=?", (typed,)).fetchone()[0] == "2026-10-25"
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "payment_terms_days": None}).status == "committed"
        bare = str(act(c, OWNER, "bill_create", {"vendor_id": vid, "bill_date": "2026-10-10",
                                                 "lines": lines}).result["bill_id"])
        assert c.execute("SELECT due_date FROM fin_bills WHERE id=?", (bare,)).fetchone()[0] is None
        customer = _vendor(c, "Acme Retail", "customer")
        assert act(c, OWNER, "contact_update", {"contact_id": customer, "payment_terms_days": 30}).code == "invalid"
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "payment_terms_days": 366}).code == "invalid"


def test_the_renewal_stage_counts_to_the_cancel_by_day_and_raises_each_stage_once(books):  # noqa: F811
    """P3: an annual commitment with a 30-day notice raises its first stage on the day the notice window opens (a month
    before the cancel-by day), not on the renewal date, and reuses the vendor's renewal event with no second alert."""
    with books.transaction() as c:
        vid = _vendor(c, "Acme Cloud")
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "renewal_on": "2026-12-31",
                                                "renewal_notice_days": 30}).status == "committed"

        def events() -> list[tuple[str, str]]:
            return [(r[0], r[1]) for r in c.execute("SELECT subject, period_key FROM fin_events WHERE name="
                                                    "'finance.vendor.renewal_due' ORDER BY period_key")]
        FA.watch_documents(c, "2026-10-31T12:00:00.000000Z")
        assert events() == []
        FA.watch_documents(c, "2026-11-01T12:00:00.000000Z")
        assert events() == [(vid, "2026-12-31:renewal_in_30")]
        notice = c.execute("SELECT payload_json FROM fin_events WHERE name='finance.vendor.renewal_due'").fetchone()[0]
        assert '"notice_by": "2026-12-01"' in notice
        FA.watch_documents(c, "2026-11-20T12:00:00.000000Z")
        assert len(events()) == 1
        FA.watch_documents(c, "2026-11-25T12:00:00.000000Z")
        assert events() == [(vid, "2026-12-31:renewal_in_30"), (vid, "2026-12-31:renewal_in_7")]
        FA.watch_documents(c, "2026-12-02T12:00:00.000000Z")
        assert len(events()) == 2


def test_the_database_refuses_an_equipment_kind_outside_its_check(books):  # noqa: F811
    """X16: a row whose kind is not owned or customer is refused by the table itself, whatever the planner allowed."""
    with books.transaction() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO equipment(id, name, kind, created_at, created_by_id) VALUES('x', 'Truck', 'truck', ?, ?)",
                      (AT, OWNER))
        assert _count(c, "equipment") == 0


def test_a_photo_is_read_with_its_equipment_and_a_missing_one_is_not_found(books):  # noqa: F811
    """X10e: the photo rides on the equipment record, so the teammates who read the record read the photo, and an id that
    names no equipment is not found."""
    with books.transaction() as c:
        made = act(c, OWNER, "equipment_create", {"name": "Plate", "kind": "owned", "photo_file_id": PHOTO})
        equipment_id = str(made.result["equipment_id"])
        assert FR.record(c, OWNER, kind="equipment", record_id=equipment_id)["equipment"]["photo_file_id"] == PHOTO
        assert act(c, OWNER, "equipment_update", {"equipment_id": equipment_id, "photo_file_id": ""}).status == "committed"
        assert FR.record(c, OWNER, kind="equipment", record_id=equipment_id)["equipment"]["photo_file_id"] is None
        with pytest.raises(Exception) as missing:
            FR.record(c, OWNER, kind="equipment", record_id="no-such-equipment")
        assert getattr(missing.value, "code", "") == NOT_FOUND
