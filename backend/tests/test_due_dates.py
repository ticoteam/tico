"""Due dates on records (plan §1a, §7.2; test cases H8, H9, H10, H10a, H10b, H10c, R3, R4, M3, C4, C5, S2, X3): the
calendar and completion chains, one task per teammate for a day's work, closing a task completes nothing, archived
records open nothing, the window a calendar date counts from, and who may read and work due dates. The clock is passed
in as `at`: no test reads the wall clock."""
from datetime import date, datetime, timedelta

import pytest

from backend import due_date_flow as DF
from backend import hubdb as H
from backend import sidebar as SB
from backend.finance import due_dates as FD
from backend.finance import reads as FR
from backend.finance.core.errors import NOT_FOUND, FinanceError
from backend.records import due_dates as RD
from backend.records import store as RS
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

DAY = "2026-10-15"          # AT's day in the company zone (America/Los_Angeles)
TWO_YEARS_AGO = (date(2026, 10, 15) - timedelta(weeks=104)).isoformat()   # on the weekly grid, 728 days before DAY


def moment(at: str = AT) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def later(days: int, hours: int = 0) -> str:
    return (moment() + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _equipment(c, name: str, *, archived: bool = False, warranty: str | None = None, who: str = OWNER) -> str:
    """A piece of equipment, made the way Finance makes one (equipment_create), archived when asked. A bot creates it
    straight away only with the records right, so the owner grants that first."""
    if who != OWNER:
        assert act(c, OWNER, "right_grant", {"teammate": who, "right": "finance.records"}).status == "committed"
    made = act(c, who, "equipment_create", {"name": name, "kind": "owned", "warranty_ends_on": warranty})
    assert made.status == "committed", made.reason
    equipment_id = str(made.result["equipment_id"])
    if archived:
        assert act(c, OWNER, "equipment_update", {"equipment_id": equipment_id, "active": False}).status == "committed"
    return equipment_id


def _location(c, name: str, kind: str = "store") -> str:
    return str(act(c, OWNER, "location_create", {"name": name, "kind": kind}).result["location_id"])


def _customer(c, name: str) -> str:
    assert act(c, OWNER, "contact_create", {"name": name, "kind": "customer"}).status == "committed"
    return str(c.execute("SELECT id FROM fin_contacts WHERE name=?", (name,)).fetchone()[0])


def _vendor(c, name: str) -> str:
    assert act(c, OWNER, "contact_create", {"name": name, "kind": "vendor"}).status == "committed"
    return str(c.execute("SELECT id FROM fin_contacts WHERE name=?", (name,)).fetchone()[0])


def _due(c, subject_type: str, subject_id: str, *, name: str, assignee: str, repeats: str = "once",
         starts_on: str = DAY, lead: int = 0, every: int | None = None, at: str = AT) -> str:
    payload: dict[str, object] = {"name": name, "subject_type": subject_type, "subject_id": subject_id,
                                  "repeats": repeats, "starts_on": starts_on, "lead_days": lead,
                                  "assignee_id": assignee}
    if every is not None:
        payload["every_count"] = every
    res = act(c, OWNER, "due_date_create", payload, at=at)
    assert res.status == "committed", res.reason
    return str(res.result["due_date_id"])


def _tick(store, at: str = AT) -> dict | None:
    with store.transaction() as c:
        return DF.tick(c, moment(at))


def _tasks(store, owner: str, prefix: str = "Due dates") -> list[dict]:
    with store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, title, body, status FROM tasks WHERE owner=? AND title LIKE ? ORDER BY created",
            (owner, prefix + "%"))]


def _next(store, due_date_id: str, at: str = AT) -> str | None:
    with store.read() as c:
        return FD.due_date(c, OWNER, due_date_id=due_date_id, at=at)["due_date"]["next_due_on"]  # type: ignore[index]


def _complete(store, due_date_id: str, due_on: str | None = None, at: str = AT) -> dict[str, object]:
    payload: dict[str, object] = {"due_date_id": due_date_id}
    if due_on:
        payload["due_on"] = due_on
    with store.transaction() as c:
        res = act(c, OWNER, "due_date_occurrence_complete", payload, at=at)
    assert res.status == "committed", res.reason
    return dict(res.result)


def _counted(c, due_date_id: str) -> str:
    return str(c.execute("SELECT counted_from_on FROM due_dates WHERE id=?", (due_date_id,)).fetchone()[0])


def _readable(store, actor: str, task_id: str) -> bool:
    """Whether the hub lets `actor` read the task (a private task admits its owner, requester and guest only)."""
    with store.read() as c:
        return bool(H.task_private_readable(c, actor, H.task(c, task_id)))


# ---------------------------------------------------------------------------------------------------- H8, H9, subjects
def test_h8_a_warranty_ending_in_60_days_opens_one_task_for_whoever_added_the_piece(books):
    with books.transaction() as c:
        _equipment(c, "Walk-in unit", warranty="2026-12-14", who="bot:coder")
    assert _tick(books)["tasks"] == [{"actor": "bot:coder", "task": _tasks(books, "bot:coder")[0]["id"],
                                      "occurrences": 1}]
    [task] = _tasks(books, "bot:coder")
    assert task["title"] == "Due dates: 1 due"
    assert "Warranty ends" in task["body"] and "Walk-in unit" in task["body"]
    assert _tick(books, later(1))["tasks"] == []


def test_h8_a_moved_warranty_end_moves_the_same_due_date_and_counts_from_today(books):
    with books.transaction() as c:
        unit = _equipment(c, "Kiln 2", warranty="2026-12-14", who="bot:coder")
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2027-01-13"}).status == \
            "committed"
        row = RD.get(c, f"warranty-{unit}")
        assert (row["starts_on"], row["counted_from_on"], row["assignee_id"]) == ("2027-01-13", DAY, "bot:coder")
        assert c.execute("SELECT count(*) FROM due_dates WHERE subject_id=?", (unit,)).fetchone()[0] == 1
    assert _tick(books)["tasks"] == []


def test_h8_clearing_a_warranty_end_archives_its_due_date_and_a_new_date_brings_it_back(books):
    with books.transaction() as c:
        unit = _equipment(c, "Kiln 3", warranty="2026-12-14", who="bot:coder")
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": None}).status == "committed"
        assert RD.get(c, f"warranty-{unit}")["archived_at"] == AT
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2027-01-13"}).status == \
            "committed"
        row = RD.get(c, f"warranty-{unit}")
        assert (row["starts_on"], row["archived_at"]) == ("2027-01-13", None)


def test_h8_a_renamed_warranty_due_date_is_left_alone_and_a_stopped_one_comes_back_when_the_warranty_moves(books):
    with books.transaction() as c:
        renamed = _equipment(c, "Oven 5", warranty="2026-12-14")
        assert act(c, OWNER, "due_date_update", {"due_date_id": f"warranty-{renamed}",
                                                 "name": "Service oven"}).status == "committed"
        stopped = _equipment(c, "Oven 6", warranty="2026-12-14")
        assert act(c, OWNER, "due_date_update", {"due_date_id": f"warranty-{stopped}",
                                                 "active": False}).status == "committed"
        for unit in (renamed, stopped):
            assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2027-01-13"}
                       ).status == "committed"
        assert (RD.get(c, f"warranty-{renamed}")["name"], RD.get(c, f"warranty-{renamed}")["starts_on"]) == \
            ("Service oven", "2026-12-14")
        assert RD.get(c, f"warranty-{stopped}")["archived_at"] is None
        assert RD.get(c, f"warranty-{stopped}")["starts_on"] == "2027-01-13"


def test_h8_a_warranty_end_added_later_makes_one_due_date_and_a_past_one_makes_none(books):
    with books.transaction() as c:
        unit = _equipment(c, "Pump 7")
        assert RD.get(c, f"warranty-{unit}") is None
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2026-12-14"}).status == \
            "committed"
        assert RD.get(c, f"warranty-{unit}")["starts_on"] == "2026-12-14"
        past = _equipment(c, "Pump 8", warranty="2026-09-01")
        assert RD.get(c, f"warranty-{past}") is None


def test_h8_moving_a_warranty_end_closes_its_open_task_and_the_next_pass_opens_the_new_day(books):
    with books.transaction() as c:
        unit = _equipment(c, "Kiln 4", warranty="2026-12-14", who="bot:coder")
    _tick(books)
    assert [t["status"] for t in _tasks(books, "bot:coder")] == ["open"]
    with books.transaction() as c:
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2027-01-13"}).status == \
            "committed"
    assert [t["status"] for t in _tasks(books, "bot:coder")] == ["closed"]
    with books.transaction() as c:
        late = act(c, OWNER, "due_date_occurrence_complete", {"due_date_id": f"warranty-{unit}", "due_on": DAY})
    assert late.status != "committed" and "not one of this due date's days" in late.reason
    assert _tick(books, later(30))["tasks"] == [{"actor": "bot:coder", "task": _tasks(books, "bot:coder")[1]["id"],
                                                 "occurrences": 1}]
    [_, new] = _tasks(books, "bot:coder")
    assert new["status"] in H.ACTIVE_STATUSES and "2027-01-13" in new["body"] and "Warranty ends" in new["body"]


def test_h8_clearing_a_warranty_end_closes_its_open_task_and_nothing_reopens_it(books):
    with books.transaction() as c:
        unit = _equipment(c, "Kiln 5", warranty="2026-12-14", who="bot:coder")
    _tick(books)
    with books.transaction() as c:
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": None}).status == "committed"
    assert [t["status"] for t in _tasks(books, "bot:coder")] == ["closed"]
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE due_date_id=?",
                         (f"warranty-{unit}",)).fetchone()[0] == 0
    assert _tick(books, later(30))["tasks"] == []


def test_h8_moving_a_warranty_end_off_a_shared_task_keeps_that_task_open_for_the_other_due_date(books):
    with books.transaction() as c:
        unit = _equipment(c, "Kiln 6", warranty="2026-12-14", who="bot:coder")
        service = _due(c, "equipment", unit, name="Kiln service", assignee="bot:coder")
    _tick(books)
    [shared] = _tasks(books, "bot:coder")
    assert shared["title"] == "Due dates: 2 due"
    with books.transaction() as c:
        assert act(c, OWNER, "equipment_update", {"equipment_id": unit, "warranty_ends_on": "2027-01-13"}).status == \
            "committed"
    with books.read() as c:
        assert c.execute("SELECT status FROM tasks WHERE id=?", (shared["id"],)).fetchone()[0] in H.ACTIVE_STATUSES
        assert tuple(c.execute("SELECT task_id, completed_at FROM due_date_occurrences WHERE due_date_id=?",
                               (service,)).fetchone()) == (shared["id"], None)
        assert c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE due_date_id=?",
                         (f"warranty-{unit}",)).fetchone()[0] == 0
        events = [str(tuple(r)) for r in c.execute("SELECT * FROM task_events WHERE task_id=?", (shared["id"],))]
    assert any("Warranty ends changed" in event for event in events), "the teammate is told on the task"
    with books.transaction() as c:
        assert act(c, "bot:coder", "due_date_occurrence_complete", {"due_date_id": service, "due_on": DAY}).status == \
            "committed"


def test_a_task_naming_a_vendor_is_private_to_its_assignee_and_an_ordinary_task_stays_company_visible(books):
    with books.transaction() as c:
        vendor = _vendor(c, "Laser Service Co")
        _due(c, "contact", vendor, name="Laser service contract renewal", assignee="bot:coder")
        _due(c, "location", _location(c, "Acme Visible Store"), name="Visible check", assignee="bot:coder")
    _tick(books)
    tasks = {"vendor" if "Laser" in t["body"] else "store": t for t in _tasks(books, "bot:coder")}
    assert set(tasks) == {"vendor", "store"}, "a vendor's due date does not share a task with a store's"
    assert _readable(books, "bot:coder", tasks["vendor"]["id"])
    assert not _readable(books, "human:zed", tasks["vendor"]["id"])
    assert _readable(books, "human:zed", tasks["store"]["id"])


def test_a_needs_you_notice_naming_a_vendor_is_private_to_the_install_owner(books):
    with books.transaction() as c:
        vendor = _vendor(c, "Laser Service Co")
        _due(c, "contact", vendor, name="Laser service contract renewal", assignee="bot:coder")
        c.execute("UPDATE bots SET state='archived' WHERE slug='coder'")
    _tick(books)
    [notice] = _tasks(books, "human:ana", prefix="Due dates need a teammate")
    assert _readable(books, "human:ana", notice["id"])
    assert not _readable(books, "human:zed", notice["id"])


def test_h9_van_registration_opens_30_days_before_to_the_office_manager_and_not_earlier(books):
    """H9: a registration due in 30 days opens its task for the person now; one due in 31 days waits."""
    with books.transaction() as c:
        van = _equipment(c, "Van 4")
        _due(c, "equipment", van, name="Van registration", assignee="human:ben", starts_on="2026-11-14", lead=30)
        _due(c, "equipment", van, name="Van insurance", assignee="human:ben", starts_on="2026-11-15", lead=30)
    _tick(books)
    [task] = _tasks(books, "human:ben")
    assert "Van registration" in task["body"] and "Van insurance" not in task["body"]


def test_c4_a_job_site_inspection_on_a_customer_site_location_opens_its_task(books):
    with books.transaction() as c:
        site = _location(c, "Acme Tower job site", kind="customer_site")
        _due(c, "location", site, name="Site inspection", assignee="bot:coder")
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert "Site inspection" in task["body"] and "Acme Tower job site" in task["body"]


def test_c5_an_excavator_service_by_date_opens_a_task_and_the_next_one_comes_a_year_on(books):
    with books.transaction() as c:
        excavator = _equipment(c, "Excavator 3")
        due = _due(c, "equipment", excavator, name="Excavator service", assignee="bot:coder", repeats="yearly")
    _tick(books)
    assert len(_tasks(books, "bot:coder")) == 1
    _complete(books, due)
    assert _next(books, due) == "2027-10-15"
    assert _tick(books, later(1, 3))["tasks"] == []


def test_s2_a_laser_service_contract_renewal_with_a_vendor_opens_its_task_60_days_before(books):
    with books.transaction() as c:
        vendor = _vendor(c, "Laser Service Co")
        _due(c, "contact", vendor, name="Laser service contract renewal", assignee="bot:coder", starts_on="2026-12-14",
             lead=60)
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert "Laser service contract renewal" in task["body"] and "Laser Service Co" in task["body"]


def test_r3_permit_and_license_renewals_on_a_store_open_45_days_before_and_only_when_due(books):
    with books.transaction() as c:
        store_id = _location(c, "Acme Store 1")
        _due(c, "location", store_id, name="Health permit renewal", assignee="human:ana", repeats="yearly",
             starts_on="2026-11-29", lead=45)
        _due(c, "location", store_id, name="Liquor license renewal", assignee="human:ana", repeats="yearly",
             starts_on="2026-11-30", lead=45)
    _tick(books)
    [task] = _tasks(books, "human:ana")
    assert "Health permit renewal" in task["body"] and "Liquor license renewal" not in task["body"]


def test_r4_hood_cleaning_every_90_days_names_the_service_and_keeps_its_completion_history(books):
    with books.transaction() as c:
        hood = _equipment(c, "Hood")
        due = _due(c, "equipment", hood, name="Hood cleaning by Acme Hood Services", assignee="bot:coder",
                   repeats="every_days", every=90)
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert "Hood cleaning by Acme Hood Services" in task["body"] and "at Hood" in task["body"]
    _complete(books, due, DAY)
    with books.read() as c:
        history = FD.due_date(c, OWNER, due_date_id=due)["due_date"]["history"]  # type: ignore[index]
    assert [(h["due_on"], h["completed_by_id"]) for h in history] == [(DAY, OWNER)]  # type: ignore[index]


def test_m3_a_cnc_mill_service_every_quarter_opens_a_task_for_the_quarter_it_falls_in(books):
    with books.transaction() as c:
        mill = _equipment(c, "CNC mill")
        _due(c, "equipment", mill, name="CNC quarterly service", assignee="bot:coder", repeats="quarterly",
             starts_on="2026-10-01")
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert "CNC quarterly service" in task["body"] and "due 2026-10-01" in task["body"]


# ----------------------------------------------------------------------------------------------- H10: one task a day
def test_h10_forty_visits_wake_one_task_and_closing_it_with_three_open_leaves_them_due(books):
    with books.transaction() as c:
        due_ids = [_due(c, "contact", _customer(c, f"Acme Customer {i:02d}"), name=f"Spring tune-up {i:02d}",
                        assignee="bot:coder") for i in range(40)]
    result = _tick(books)
    assert [t["occurrences"] for t in result["tasks"]] == [40]
    [task] = _tasks(books, "bot:coder")
    assert task["title"] == "Due dates: 40 due"
    for due_id in due_ids[:37]:
        assert _complete(books, due_id)["completed"] is True
    with books.transaction() as c:
        H.task_update(c, H.KEEPER, task["id"], status="done")
    with books.read() as c:
        still_open = [d for d in due_ids if RD.next_open(RD.get(c, d), RD.completed_days(c, d)) is not None]
    assert still_open == due_ids[37:], "closing the task completed nothing: the three stay due"
    tomorrow = _tick(books, later(1))
    assert [t["occurrences"] for t in tomorrow["tasks"]] == [3]
    assert [t["title"] for t in _tasks(books, "bot:coder")][-1] == "Due dates: 3 due"


def test_a_second_run_on_the_same_day_opens_nothing_new_and_an_open_task_is_not_opened_again(books):
    with books.transaction() as c:
        _due(c, "contact", _customer(c, "Acme Repeat"), name="Visit", assignee="bot:coder")
    assert _tick(books) is not None
    assert _tick(books, later(0, 3)) is None
    assert len(_tasks(books, "bot:coder")) == 1
    # The next day, the task is still open: its occurrence is not opened a second time.
    assert _tick(books, later(1))["tasks"] == []
    assert len(_tasks(books, "bot:coder")) == 1


def test_overdue_occurrences_open_a_task_whose_due_is_already_past(books):
    """Everything in the task is overdue: the task still opens, and carries a due that is already past."""
    with books.transaction() as c:
        _due(c, "location", _location(c, "Acme Past Store"), name="Late permit", assignee="bot:coder",
             starts_on="2026-10-12")
    result = _tick(books)
    assert [t["occurrences"] for t in result["tasks"]] == [1]
    [task] = _tasks(books, "bot:coder")
    assert task["title"] == "Due dates: 1 due" and "due 2026-10-12" in task["body"]
    with books.read() as c:
        task_due = c.execute("SELECT due FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
    assert task_due is not None and str(task_due)[:10] in ("2026-10-12", "2026-10-13")


# ----------------------------------------------------------------------------------------- the window a calendar counts
def test_a_weekly_date_from_two_years_ago_opens_one_occurrence_not_one_per_missed_week(books):
    with books.transaction() as c:
        _due(c, "location", _location(c, "Acme Old Weekly"), name="Weekly walk", assignee="bot:coder",
             repeats="weekly", starts_on=TWO_YEARS_AGO)
    result = _tick(books)
    assert [t["occurrences"] for t in result["tasks"]] == [1]
    [task] = _tasks(books, "bot:coder")
    assert f"due {DAY}" in task["body"]


def test_weeks_missed_after_creation_stay_open_and_count(books):
    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Missed Weekly"), name="Missed walk", assignee="bot:coder",
                   repeats="weekly", starts_on=TWO_YEARS_AGO)
    _tick(books)                          # opens the one on the day it was made
    _complete(books, due, DAY)            # that one is done
    result = _tick(books, later(14))      # two weeks on, the two missed since creation are both open
    assert [t["occurrences"] for t in result["tasks"]] == [2]


def test_counted_from_on_is_the_day_made_and_moves_only_when_the_repeat_or_the_start_changes(books):
    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Count Store"), name="Count check", assignee="bot:coder",
                   repeats="weekly", starts_on=TWO_YEARS_AGO)
        assert _counted(c, due) == DAY
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "lead_days": 3}, at=later(3)).status == "committed"
        assert _counted(c, due) == DAY
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "repeats": "monthly"},
                   at=later(3)).status == "committed"
        assert _counted(c, due) == "2026-10-18"


# --------------------------------------------------------------------------------------------- H10a, H10b, month end
def test_h10a_every_90_days_counts_from_the_last_completion_and_quarterly_falls_on_calendar_quarters(books):
    with books.transaction() as c:
        store_id = _location(c, "Acme Store 2")
        chain = _due(c, "location", store_id, name="Filter change", assignee="bot:coder", repeats="every_days",
                     every=90)
        quarterly = _due(c, "location", store_id, name="Quarterly check", assignee="bot:coder", repeats="quarterly",
                         starts_on="2026-10-01")
    _complete(books, chain, DAY, at=later(17))              # done on 1 November
    _complete(books, quarterly, "2026-10-01", at=later(17))
    assert _next(books, chain, later(17)) == "2027-01-30"   # 1 November + 90 days
    assert _next(books, quarterly, later(17)) == "2027-01-01"


def test_h10b_a_90_day_service_done_20_days_late_moves_to_90_days_after_the_day_it_was_done(books):
    with books.transaction() as c:
        plant = _location(c, "Acme Plant", kind="plant")
        service = _due(c, "location", plant, name="Service", assignee="bot:coder", repeats="every_days", every=90,
                       starts_on="2026-09-25")
    _complete(books, service, "2026-09-25", at=AT)          # 20 days late: due 25 September, done 15 October
    assert _next(books, service) == "2027-01-13"            # 15 October + 90 days


def test_month_end_a_monthly_date_from_the_31st_falls_on_the_last_day_and_a_chain_clamps_the_same_way(books):
    jan = "2026-01-31T12:00:00.000000Z"
    with books.transaction() as c:
        store_id = _location(c, "Acme Month End")
        monthly = _due(c, "location", store_id, name="Monthly close", assignee="bot:coder", repeats="monthly",
                       starts_on="2026-01-31", at=jan)
        chain = _due(c, "location", store_id, name="Monthly chain", assignee="bot:coder", repeats="every_months",
                     every=1, starts_on="2026-01-31", at=jan)
    _complete(books, monthly, "2026-01-31", at=jan)
    assert _next(books, monthly, jan) == "2026-02-28"        # 2026 is not a leap year
    _complete(books, chain, "2026-01-31", at=jan)
    assert _next(books, chain, jan) == "2026-02-28"
    _complete(books, chain, "2026-02-28", at="2026-02-28T12:00:00.000000Z")
    assert _next(books, chain, "2026-02-28T12:00:00.000000Z") == "2026-03-28"    # from the 28th, not the 31st


# --------------------------------------------------------------------------------------------- H10c, X3 and idempotency
def test_h10c_archived_equipment_with_a_pending_due_date_opens_no_more_tasks(books):
    with books.transaction() as c:
        press = _equipment(c, "Old press")
        _due(c, "equipment", press, name="Press service", assignee="bot:coder")
        c.execute("UPDATE equipment SET archived_at=? WHERE id=?", (AT, press))
    assert _tick(books)["tasks"] == []
    assert _tasks(books, "bot:coder") == []


def test_h10c_an_archived_location_or_an_archived_due_date_opens_nothing(books):
    with books.transaction() as c:
        closed = _location(c, "Acme Closed Store")
        open_due = _due(c, "location", closed, name="Permit", assignee="bot:coder")
        retired = _due(c, "location", _location(c, "Acme Retired Store"), name="Retired check", assignee="bot:coder")
        act(c, OWNER, "location_update", {"location_id": closed, "active": False})
        assert act(c, OWNER, "due_date_update", {"due_date_id": retired, "active": False}).status == "committed"
    assert _tick(books)["tasks"] == []
    assert open_due and _tasks(books, "bot:coder") == []


def test_x3_a_hidden_equipment_section_changes_nothing_about_equipment_due_dates(books):
    """X3: the sidebar hides Operations > Equipment, and the pass still opens the piece's due dates: it reads no
    sidebar setting."""
    with books.transaction() as c:
        SB.set_settings(c, OWNER, SB.SidebarSettings(hidden_sections=["operations.equipment"]))
        assert "operations.equipment" in SB.hidden_now(SB.stored(c)["hidden_sections"])
        unit = _equipment(c, "Hidden tab unit")
        _due(c, "equipment", unit, name="Hidden tab check", assignee="bot:coder")
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    assert "Hidden tab check" in task["body"]


def test_completing_twice_records_one_completion_and_moves_the_chain_once(books):
    with books.transaction() as c:
        service = _due(c, "location", _location(c, "Acme Repeat Store"), name="Service", assignee="bot:coder",
                       repeats="every_days", every=90)
    first = _complete(books, service, DAY)
    second = _complete(books, service, DAY)
    assert first["completed"] is True and second["completed"] is False
    assert second["next_due_on"] == first["next_due_on"] == "2027-01-13"


def test_reopening_the_latest_completion_restores_the_prior_next_due_on_and_only_the_latest_can_reopen(books):
    with books.transaction() as c:
        service = _due(c, "location", _location(c, "Acme Reopen Store"), name="Service", assignee="bot:coder",
                       repeats="every_days", every=90)
    _complete(books, service, DAY)
    _complete(books, service, "2027-01-13", at=later(90))
    with books.transaction() as c:
        refused = act(c, OWNER, "due_date_occurrence_reopen", {"due_date_id": service, "due_on": DAY})
    assert refused.code == "invalid" and "latest" in refused.reason
    with books.transaction() as c:
        reopened = act(c, OWNER, "due_date_occurrence_reopen", {"due_date_id": service, "due_on": "2027-01-13"},
                       at=later(90))
    assert reopened.status == "committed"
    assert _next(books, service, later(90)) == "2027-01-13"


# ----------------------------------------------------------------------------------- assignees, notices, who may do what
def test_an_archived_assignee_gets_one_needs_you_item_naming_all_its_due_dates_and_nothing_is_dropped(books):
    with books.transaction() as c:
        extinguisher = _due(c, "location", _location(c, "Acme Extinguisher Store"), name="Extinguisher check",
                            assignee="bot:coder")
        hose = _due(c, "location", _location(c, "Acme Hose Store"), name="Hose check", assignee="bot:coder")
        c.execute("UPDATE bots SET state='archived' WHERE slug='coder'")
    result = _tick(books)
    assert result["tasks"] == [] and len(result["notices"]) == 1
    [notice] = _tasks(books, "human:ana", prefix="Due dates need a teammate")
    assert notice["title"] == "Due dates need a teammate: bot:coder"
    assert "2 due date(s)" in notice["body"] and "archived" in notice["body"]
    assert "- Extinguisher check" in notice["body"] and "- Hose check" in notice["body"]
    with books.read() as c:
        assert RD.next_open(RD.get(c, extinguisher), RD.completed_days(c, extinguisher)) is not None
        assert RD.next_open(RD.get(c, hose), RD.completed_days(c, hose)) is not None
    assert _tick(books, later(0, 2)) is None      # the same day, nothing more


def test_a_missing_assignee_is_refused_when_the_due_date_is_made(books):
    with books.transaction() as c:
        res = act(c, OWNER, "due_date_create", {"name": "Check", "subject_type": "location",
                                                "subject_id": _location(c, "Acme Missing Teammate"),
                                                "repeats": "once", "starts_on": DAY, "assignee_id": "nobody"})
    assert res.code == "invalid" and "teammate" in res.reason


def test_a_teammate_without_the_records_right_cannot_make_due_dates(books):
    with books.transaction() as c:
        res = act(c, "human:eve", "due_date_create", {"name": "Check", "subject_type": "location",
                                                      "subject_id": _location(c, "Acme Eve Store"),
                                                      "repeats": "once", "starts_on": DAY,
                                                      "assignee_id": "human:ben"})
    assert res.status == "denied"


def test_the_assignee_completes_and_reopens_its_occurrences_without_the_right_and_without_approval(books):
    """A bot or a person who is the assignee works its own occurrences with no records right and no approval. Anyone else
    without the right is denied."""
    with books.transaction() as c:
        bot_due = _due(c, "location", _location(c, "Acme Bot Work"), name="Bot work", assignee="bot:coder")
        person_due = _due(c, "location", _location(c, "Acme Person Work"), name="Person work", assignee="human:eve")
    for who, due_id in (("bot:coder", bot_due), ("human:eve", person_due)):
        with books.transaction() as c:
            done = act(c, who, "due_date_occurrence_complete", {"due_date_id": due_id, "due_on": DAY})
        assert done.status == "committed" and done.approval_id is None, (who, done.reason)
        with books.transaction() as c:
            back = act(c, who, "due_date_occurrence_reopen", {"due_date_id": due_id, "due_on": DAY})
        assert back.status == "committed" and back.approval_id is None, (who, back.reason)
    with books.transaction() as c:
        assert act(c, "bot:seller", "due_date_occurrence_complete",
                   {"due_date_id": bot_due, "due_on": DAY}).status == "denied"
        assert act(c, "human:fay", "due_date_occurrence_complete",
                   {"due_date_id": person_due, "due_on": DAY}).status == "denied"


def test_a_teammate_reads_due_dates_without_a_finance_view(books):
    """Teammates read operational due dates whatever their finance view: with the member default set to none, eve (who
    leads and manages nobody) sees a location's due date, and so does the bot on the org chart."""
    with books.transaction() as c:
        assert act(c, OWNER, "view_defaults_set", {"manager": "own_expenses", "member": "none"}).status == "committed"
        _due(c, "location", _location(c, "Acme Teammate Store"), name="Store check", assignee="bot:coder")
    with books.read() as c:
        assert [r["name"] for r in FD.listing(c, "human:eve")["due_dates"]] == ["Store check"]
        assert [r["name"] for r in FD.listing(c, "bot:coder")["due_dates"]] == ["Store check"]


def test_a_guest_reads_only_the_due_dates_it_works(books):
    with books.transaction() as c:
        store_id = _location(c, "Acme Guest Store")
        mine = _due(c, "location", store_id, name="Guest work", assignee="bot:coder")
        theirs = _due(c, "location", store_id, name="Team work", assignee="bot:coder")
        c.execute("UPDATE due_dates SET assignee_id='human:zed' WHERE id=?", (mine,))
    with books.read() as c:
        assert [r["name"] for r in FD.listing(c, "human:zed")["due_dates"]] == ["Guest work"]
        assert FD.due_date(c, "human:zed", due_date_id=mine)["due_date"]["name"] == "Guest work"  # type: ignore[index]
        with pytest.raises(FinanceError) as refused:
            FD.due_date(c, "human:zed", due_date_id=theirs)
        assert refused.value.code == NOT_FOUND
        # a teammate on the org chart reads every due date, the guest's included
        assert sorted(r["name"] for r in FD.listing(c, "bot:coder")["due_dates"]) == ["Guest work", "Team work"]


def test_contact_due_dates_follow_each_contacts_kind_per_row_and_paging_counts_them(books):
    with books.transaction() as c:
        assert act(c, OWNER, "view_defaults_set", {"manager": "own_expenses", "member": "none"}).status == "committed"
        customer = _customer(c, "Acme Visible Customer")
        vendor = _vendor(c, "Acme Hidden Vendor")
        _due(c, "contact", customer, name="Customer visit", assignee="bot:coder")
        _due(c, "contact", vendor, name="Vendor audit", assignee="bot:coder")
        _due(c, "contact", vendor, name="Vendor call", assignee="human:eve")     # eve works this one
    with books.read() as c:
        assert sorted(r["name"] for r in FD.listing(c, OWNER)["due_dates"]) == [
            "Customer visit", "Vendor audit", "Vendor call"]
        # eve sees a customer's due date (every teammate reads customer names) and the vendor one she works, not the
        # vendor one the bot works: she has no vendor view.
        seen: list[str] = []
        cursor = None
        while True:
            page = FD.listing(c, "human:eve", limit=1, cursor=cursor)
            seen += [str(r["name"]) for r in page["due_dates"]]  # type: ignore[index]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert seen == ["Customer visit", "Vendor call"]


def test_a_merged_location_answers_under_its_record_and_next_due_is_read_for_it(books):
    with books.transaction() as c:
        kept = _location(c, "Acme Kept Store")
        merged = _location(c, "Acme Merged Store")
        _due(c, "location", merged, name="Merged store check", assignee="bot:coder")
        RS.alias(c, "locations", merged, kept, AT)
    with books.read() as c:
        listed = FD.listing(c, OWNER, subject_type="location", subject_id=kept)["due_dates"]  # type: ignore[index]
        assert [r["name"] for r in listed] == ["Merged store check"]  # type: ignore[index]
        assert listed[0]["subject_name"] == "Acme Kept Store"  # type: ignore[index]
        assert FD.listing(c, OWNER, subject_type="equipment")["due_dates"] == []  # type: ignore[index]
        days = RD.next_due_and_status(c, "location", [kept, merged], date.fromisoformat(DAY))
        assert {given: day.isoformat() for given, (day, _) in days.items()} == {kept: DAY, merged: DAY}
        with pytest.raises(FinanceError):
            FD.listing(c, OWNER, subject_id=kept)


def test_search_finds_due_dates_by_name_and_hides_archived_ones_unless_asked(books):
    with books.transaction() as c:
        store_id = _location(c, "Acme Search Store")
        _due(c, "location", store_id, name="Permit renewal", assignee="bot:coder")
        retired = _due(c, "location", store_id, name="Old permit", assignee="bot:coder")
        act(c, OWNER, "due_date_update", {"due_date_id": retired, "active": False})
    with books.read() as c:
        found = FR.search(c, OWNER, kind="due_date", q="permit")
        listed = FD.listing(c, OWNER)
        archived = FD.listing(c, OWNER, archived="only")
    assert [(r["kind"], r["label"]) for r in found["results"]] == [("due_date", "Permit renewal")]  # type: ignore[index]
    assert [r["name"] for r in listed["due_dates"]] == ["Permit renewal"]  # type: ignore[index]
    assert [r["name"] for r in archived["due_dates"]] == ["Old permit"]  # type: ignore[index]


# ------------------------------------------------- Codex review: early completions, hand-offs, dates from long ago
NOV1 = "2026-11-01T12:00:00.000000Z"


def test_completing_a_chain_early_moves_it_on_from_the_occurrence_not_onto_it(books):
    """Every 7 days, due Nov 8, completed Nov 1: a week on from the completion is Nov 8 itself, already done, and the chain
    would stop. It counts from the occurrence instead: Nov 15. A fixed weekly date completed early keeps the rest open."""
    with books.transaction() as c:
        chain = _due(c, "location", _location(c, "Acme Early Chain"), name="Early chain", assignee="bot:coder",
                     repeats="every_days", every=7, starts_on="2026-11-08", at=NOV1)
        fixed = _due(c, "location", _location(c, "Acme Early Fixed"), name="Early fixed", assignee="bot:coder",
                     repeats="weekly", starts_on="2026-11-08", at=NOV1)
    assert _complete(books, chain, "2026-11-08", at=NOV1)["next_due_on"] == "2026-11-15"
    assert _next(books, chain, NOV1) == "2026-11-15"
    assert _complete(books, chain, "2026-11-15", at="2026-11-15T12:00:00.000000Z")["next_due_on"] == "2026-11-22"
    _complete(books, fixed, "2026-11-08", at=NOV1)
    assert _next(books, fixed, NOV1) == "2026-11-15"


def test_an_early_monthly_chain_completion_counts_from_the_occurrence_too(books):
    oct1 = "2026-10-01T12:00:00.000000Z"
    with books.transaction() as c:
        chain = _due(c, "location", _location(c, "Acme Early Months"), name="Early months", assignee="bot:coder",
                     repeats="every_months", every=1, starts_on="2026-11-08", at=oct1)
    # done on Oct 1 for Nov 8: a month on from that is Nov 1, before the occurrence, so the month counts from Nov 8
    assert _complete(books, chain, "2026-11-08", at=oct1)["next_due_on"] == "2026-12-08"
    assert _next(books, chain, oct1) == "2026-12-08"


def test_reopening_an_early_completion_puts_the_chain_back_on_its_occurrence(books):
    with books.transaction() as c:
        chain = _due(c, "location", _location(c, "Acme Early Reopen"), name="Early reopen", assignee="bot:coder",
                     repeats="every_days", every=7, starts_on="2026-11-08", at=NOV1)
    _complete(books, chain, "2026-11-08", at=NOV1)
    assert _next(books, chain, NOV1) == "2026-11-15"
    with books.transaction() as c:
        back = act(c, OWNER, "due_date_occurrence_reopen", {"due_date_id": chain, "due_on": "2026-11-08"}, at=NOV1)
    assert back.status == "committed" and back.result["reopened"] is True
    assert _next(books, chain, NOV1) == "2026-11-08"


def test_a_new_schedule_or_a_stop_closes_the_open_task_and_a_rename_does_not(books):
    def open_tasks():
        return [t for t in _tasks(books, "human:eve") if t["status"] != "closed"]

    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Moved Store"), name="Moved check", assignee="human:eve")
    _tick(books)
    [task] = _tasks(books, "human:eve")
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "name": "Moved check 2"}).status == "committed"
    assert [t["id"] for t in open_tasks()] == [task["id"]], "a rename keeps the task"
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "starts_on": "2027-06-01"}).status == "committed"
    assert open_tasks() == [], "the old day's task closed"
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE due_date_id=? AND completed_at IS NULL",
                         (due,)).fetchone()[0] == 0
    with books.transaction() as c:
        back = act(c, OWNER, "due_date_update", {"due_date_id": due, "starts_on": DAY})
        assert back.status == "committed", back.reason
    _tick(books, later(1))
    assert len(open_tasks()) == 1, "the next pass opens the current day"
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "active": False}).status == "committed"
    assert open_tasks() == [], "a stopped due date asks for nothing"


def test_a_new_interval_keeps_the_open_task_of_a_day_still_on_the_schedule(books):
    def open_tasks():
        return [t for t in _tasks(books, "human:eve") if t["status"] != "closed"]

    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Interval Store"), name="Interval check", assignee="human:eve",
                   repeats="every_days", every=30)
    _tick(books)
    [task] = open_tasks()
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": due, "every_count": 60}).status == "committed"
    assert [t["id"] for t in open_tasks()] == [task["id"]], "the open day is still the next one, so the work stands"
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM due_date_occurrences WHERE due_date_id=? AND due_on=? AND task_id=? "
                         "AND completed_at IS NULL", (due, DAY, task["id"])).fetchone()[0] == 1
    with books.transaction() as c:
        moved = act(c, OWNER, "due_date_update", {"due_date_id": due, "next_due_on": "2027-01-15"})
        assert moved.status == "committed", moved.reason
    assert open_tasks() == [], "a day off the schedule has nothing left to complete"


def test_reassigning_a_due_date_moves_its_open_task_to_the_new_assignee(books):
    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Handoff Store"), name="Handoff check", assignee="bot:coder")
    _tick(books)
    [task] = _tasks(books, "bot:coder")
    with books.transaction() as c:
        moved = act(c, OWNER, "due_date_update", {"due_date_id": due, "assignee_id": "human:eve"})
    assert moved.status == "committed", moved.reason
    with books.read() as c:
        owner = c.execute("SELECT owner FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
        owner_events = c.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND field='owner'",
                                 (task["id"],)).fetchone()[0]
    assert owner == "human:eve" and owner_events >= 1, "the hand-off is in the task's history"
    assert _tasks(books, "bot:coder") == []
    assert _tick(books, later(1))["tasks"] == [], "the open task is eve's: nothing new for coder or for eve"
    with books.transaction() as c:
        assert act(c, "bot:coder", "due_date_occurrence_complete",
                   {"due_date_id": due, "due_on": DAY}).status == "denied", "the old assignee lost the work"
    with books.transaction() as c:
        done = act(c, "human:eve", "due_date_occurrence_complete", {"due_date_id": due, "due_on": DAY})
    assert done.status == "committed" and done.approval_id is None, done.reason


def test_reassigning_one_due_date_off_a_shared_task_closes_it_and_the_next_pass_opens_each(books):
    with books.transaction() as c:
        moving = _due(c, "location", _location(c, "Acme Moving Store"), name="Moving check", assignee="bot:coder")
        _due(c, "location", _location(c, "Acme Staying Store"), name="Staying check", assignee="bot:coder")
    _tick(books)
    [shared] = _tasks(books, "bot:coder")
    assert shared["title"] == "Due dates: 2 due"
    with books.transaction() as c:
        assert act(c, OWNER, "due_date_update", {"due_date_id": moving,
                                                 "assignee_id": "human:eve"}).status == "committed"
    with books.read() as c:
        status, note = c.execute("SELECT status, note FROM tasks WHERE id=?", (shared["id"],)).fetchone()
    assert status == "closed" and "Moving check is now assigned to human:eve." in str(note)
    result = _tick(books, later(1))
    assert sorted(t["occurrences"] for t in result["tasks"]) == [1, 1]
    [coder] = [t for t in _tasks(books, "bot:coder") if t["id"] != shared["id"]]
    [eve] = _tasks(books, "human:eve")
    assert "Staying check" in coder["body"] and "Moving check" not in coder["body"]
    assert "Moving check" in eve["body"] and "Staying check" not in eve["body"]


def test_a_weekly_date_from_1900_still_opens_and_moves_on_in_2026(books):
    """The walk over occurrences used to stop after 5,000 steps, about 1995 for a weekly date from 1900, and then looked
    finished for good. It now starts from the day the date was made, found by arithmetic."""
    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Ancient Weekly"), name="Ancient weekly", assignee="bot:coder",
                   repeats="weekly", starts_on="1900-01-01")
    assert [t["occurrences"] for t in _tick(books)["tasks"]] == [1]
    day = date.fromisoformat(_next(books, due))
    assert date(2026, 10, 15) - timedelta(days=7) < day <= date(2026, 10, 15)
    assert (day - date(1900, 1, 1)).days % 7 == 0
    _complete(books, due, day.isoformat())
    assert date.fromisoformat(_next(books, due)) == day + timedelta(days=7)


def test_a_monthly_date_from_1500_is_found_and_completed_in_2026(books):
    with books.transaction() as c:
        due = _due(c, "location", _location(c, "Acme Old Monthly"), name="Old monthly", assignee="bot:coder",
                   repeats="monthly", starts_on="1500-03-15")
    assert [t["occurrences"] for t in _tick(books)["tasks"]] == [1]
    _complete(books, due, DAY)
    assert _next(books, due) == "2026-11-15"
