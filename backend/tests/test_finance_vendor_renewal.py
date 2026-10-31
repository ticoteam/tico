"""Vendor seats and renewal dates (spec §12): contact_update sets them for vendors only, the vendor drill-down reads
them, and watch raises each renewal stage once per renewal date."""
from datetime import date

from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.finance.core import ap as AP
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401


def _vendor(c, name="Acme Supplies") -> str:
    made = act(c, OWNER, "contact_create", {"name": name, "kind": "vendor"})
    assert made.status == "committed", made
    return str(made.result["contact_id"])


def _events(c, name) -> list[tuple[str, str]]:
    return [tuple(r) for r in c.execute("SELECT subject, period_key FROM fin_events WHERE name=? ORDER BY created_at",
                                        (name,))]


def test_renewal_stage_is_the_tightest_stage_reached_and_none_once_the_date_has_passed():
    today = date(2026, 10, 15)
    assert AP.renewal_stage("2026-12-31", today) is None
    assert AP.renewal_stage("2026-11-14", today) == "renewal_in_30"
    assert AP.renewal_stage("2026-10-22", today) == "renewal_in_7"
    assert AP.renewal_stage("2026-10-15", today) == "renewal_in_7"
    assert AP.renewal_stage("2026-10-14", today) is None
    assert AP.renewal_stage(None, today) is None


def test_seats_and_renewal_are_a_vendors_and_show_on_its_drill_down(books):  # noqa: F811
    with books.transaction() as c:
        vid = _vendor(c)
        made = act(c, OWNER, "contact_update", {"contact_id": vid, "seats": 12, "renewal_on": "2026-11-14"})
        assert made.status == "committed", made
        bid = str(act(c, OWNER, "bill_create", {"vendor_id": vid, "bill_date": "2026-10-10", "lines": [
            {"account_id": account(c, "6900"), "amount": 20_000}]}).result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        detail = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", vendor=vid)
        assert (detail["seats"], detail["renewal_on"]) == (12, "2026-11-14")
        plain = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31")
        assert (plain["seats"], plain["renewal_on"]) == (None, None)
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "seats": 0, "renewal_on": ""}).status == "committed"
        cleared = FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", vendor=vid)
        assert (cleared["seats"], cleared["renewal_on"]) == (0, None)
        customer = str(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}).result["contact_id"])
        assert act(c, OWNER, "contact_update", {"contact_id": customer, "seats": 3}).code == "invalid"
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "seats": -1}).code == "invalid"
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "renewal_on": "next week"}).code == "invalid"


def test_a_vendor_renewal_raises_one_event_per_stage_for_each_date_and_none_once_it_has_passed(books):  # noqa: F811
    with books.transaction() as c:
        vid = _vendor(c)
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "renewal_on": "2026-11-14"}).status == "committed"
        FA.watch_documents(c, AT)
        FA.watch_documents(c, "2026-10-15T13:00:00.000000Z")
        assert _events(c, "finance.vendor.renewal_due") == [(vid, "2026-11-14:renewal_in_30")]
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "renewal_on": "2026-10-20"}).status == "committed"
        FA.watch_documents(c, AT)
        # Both were written at the same instant, so compare the keys as a set, not in order.
        assert sorted(k for _, k in _events(c, "finance.vendor.renewal_due")) == [
            "2026-10-20:renewal_in_7", "2026-11-14:renewal_in_30"]
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "renewal_on": "2026-10-01"}).status == "committed"
        FA.watch_documents(c, AT)
        assert len(_events(c, "finance.vendor.renewal_due")) == 2
