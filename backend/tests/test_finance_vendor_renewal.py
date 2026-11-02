"""Vendor seats and renewal dates (spec §12): contact_update sets them for vendors only, the vendor drill-down reads
them, and watch raises each renewal stage once per renewal date."""
from datetime import date

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import context as FC
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


def test_a_renewal_task_opens_once_per_date_and_falls_to_the_install_owner(books):  # noqa: F811
    with books.transaction() as c:
        laser = _vendor(c, "Laser Service Co")
        assert act(c, OWNER, "contact_update", {"contact_id": laser, "renewal_on": "2026-12-14",
                                                "renewal_notice_days": 60}).status == "committed"
        FA.watch_documents(c, AT)
        FA.watch_documents(c, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        tasks = [tuple(r) for r in c.execute("SELECT owner, title FROM tasks WHERE title LIKE 'Vendor renewal:%'")]
        assert tasks == [(FC.owner(c), "Vendor renewal: Laser Service Co")]


def test_a_renewal_with_no_notice_opens_its_task_thirty_days_before_the_renewal(books):  # noqa: F811
    with books.transaction() as c:
        early = _vendor(c, "Early Co")
        soon = _vendor(c, "Soon Co")
        for vendor, renews in ((early, "2026-12-14"), (soon, "2026-11-10")):
            assert act(c, OWNER, "contact_update", {"contact_id": vendor, "renewal_on": renews}).status == "committed"
        FA.watch_documents(c, "2026-10-15T13:00:00.000000Z")
    with books.read() as c:
        tasks = [str(r[0]) for r in c.execute("SELECT title FROM tasks WHERE title LIKE 'Vendor renewal:%'")]
        assert tasks == ["Vendor renewal: Soon Co"]


def test_a_renewal_whose_owner_cannot_take_it_tells_the_install_owner_once_and_opens_when_fixed(books):  # noqa: F811
    with books.transaction() as c:
        vendor = _vendor(c, "Orphan Co")
        assert act(c, OWNER, "contact_update", {"contact_id": vendor, "renewal_on": "2026-11-10"}).status == "committed"
        c.execute("UPDATE fin_contacts SET owner_id='human:gone' WHERE id=?", (vendor,))
        FA.watch_documents(c, "2026-10-15T13:00:00.000000Z")
    with books.transaction() as c:
        rows = c.execute("SELECT id FROM tasks WHERE title='Vendor renewal needs a teammate: Orphan Co'").fetchall()
        assert len(rows) == 1
        # The notice names a vendor, so the install owner reads it and an unrelated teammate does not.
        notice = H.task(c, rows[0][0])
        assert H.task_private_readable(c, FC.owner(c), notice) and not H.task_private_readable(c, "human:ben", notice)
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (rows[0][0],))
        FA.watch_documents(c, "2026-10-15T14:00:00.000000Z")
    with books.transaction() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Vendor renewal needs%'").fetchone()[0] == 1
        c.execute("UPDATE fin_contacts SET owner_id=NULL WHERE id=?", (vendor,))
        FA.watch_documents(c, "2026-10-15T15:00:00.000000Z")
    with books.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Vendor renewal: Orphan Co'").fetchone()[0] == 1


def test_a_renewal_task_goes_to_the_vendors_owner_when_it_has_one(books):  # noqa: F811
    with books.transaction() as c:
        laser = _vendor(c, "Laser Service Co")
        assert act(c, OWNER, "contact_update", {"contact_id": laser, "renewal_on": "2026-12-14",
                                                "renewal_notice_days": 60,
                                                "owner_id": "bot:coder"}).status == "committed"
        FA.watch_documents(c, AT)
    with books.read() as c:
        tasks = [tuple(r) for r in c.execute("SELECT owner, title FROM tasks WHERE title LIKE 'Vendor renewal:%'")]
        assert tasks == [("bot:coder", "Vendor renewal: Laser Service Co")]
        task = H.task(c, c.execute("SELECT id FROM tasks WHERE title LIKE 'Vendor renewal:%'").fetchone()[0])
        # gus is outside the bot's reporting line (cara, then ben), which reads a bot's private task by design.
        readers = {actor: H.task_private_readable(c, actor, task) for actor in ("bot:coder", "human:gus")}
    assert readers == {"bot:coder": True, "human:gus": False}, "a vendor's renewal is private to its owner"


def test_a_viewer_without_the_vendors_cells_or_the_payables_view_gets_no_seats_or_renewal(books):  # noqa: F811
    with books.transaction() as c:
        vid = _vendor(c)
        assert act(c, OWNER, "contact_update", {"contact_id": vid, "seats": 12,
                                                "renewal_on": "2026-11-14"}).status == "committed"
        bid = str(act(c, OWNER, "bill_create", {"vendor_id": vid, "bill_date": "2026-10-10", "lines": [
            {"account_id": account(c, "6900"), "amount": 20_000}]}).result["bill_id"])
        assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
        # Ben sees the expense line through his own spend only: the vendor's cells on it are the owner's, not his.
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:ben", "what": "expenses",
                                            "whose": "own"}).status == "committed"
        hidden = FR.line_detail(c, "human:ben", account(c, "6900"), "2026-10-01", "2026-10-31", vendor=vid)
        assert (hidden["seats"], hidden["renewal_on"]) == (None, None)
        assert FR.line_detail(c, OWNER, account(c, "6900"), "2026-10-01", "2026-10-31", vendor=vid)["seats"] == 12
        assert act(c, OWNER, "view_grant", {"subject_kind": "teammate", "subject": "human:ben", "what": "payables",
                                            "whose": "company"}).status == "committed"
        seen = FR.line_detail(c, "human:ben", account(c, "6900"), "2026-10-01", "2026-10-31", vendor=vid)
        assert (seen["seats"], seen["renewal_on"]) == (12, "2026-11-14")
