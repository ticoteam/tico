"""Due dates on records (plan §1a, §7.2; test cases H8, H9, H10, H10a, H10b, H10c, R3, R4, M3, C4, C5, S2, X3): the
calendar and completion chains, one task per teammate for a day's work, closing a task completes nothing, archived
records open nothing, and who may ask. The clock is passed in as `at`: no test reads the wall clock."""
import json
from datetime import datetime, timedelta

from backend import due_date_flow as DF
from backend import hubdb as H
from backend.finance import due_dates as FD
from backend.finance import reads as FR
from backend.finance.core.errors import FinanceError
from backend.records import due_dates as RD
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

DAY = "2026-10-15"          # AT's day in the company zone (America/Los_Angeles)


def moment(at: str = AT) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def later(days: int, hours: int = 0) -> str:
    return (moment() + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _equipment(c, name: str, *, archived: bool = False) -> str:
    """A piece of equipment. The equipment table is ops-equipment's; this makes the minimal one the pass reads when it is
    not on the branch yet, and uses the same column names when it is."""
    c.execute("CREATE TABLE IF NOT EXISTS equipment(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
              "kind TEXT NOT NULL DEFAULT 'owned', archived_at TEXT, created_at TEXT, created_by_id TEXT)")
    equipment_id = RD.new_id()
    c.execute("INSERT INTO equipment(id, name, kind, archived_at, created_at, created_by_id) VALUES(?,?,?,?,?,?)",
              (equipment_id, name, "owned", AT if archived else None, AT, OWNER))
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


# ---------------------------------------------------------------------------------------------------- H8, H9, subjects
def test_h8_a_warranty_ending_in_60_days_opens_one_task_for_the_bot_to_offer_the_agreement(books):
    with books.transaction() as c:
        unit = _equipment(c, "Walk-in unit")
        _due(c, "equipment", unit, name="Offer maintenance agreement", assignee="bot:coder", starts_on="2026-12-14",
             lead=60)
    assert _tick(books)["tasks"] == [{"actor": "bot:coder", "task": _tasks(books, "bot:coder")[0]["id"],
                                      "occurrences": 1}]
    [task] = _tasks(books, "bot:coder")
    assert task["title"] == "Due dates: 1 due" and "Offer maintenance agreement" in task["body"]


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
    with books.transaction() as c:
        store_id = _location(c, "Acme Month End")
        monthly = _due(c, "location", store_id, name="Monthly close", assignee="bot:coder", repeats="monthly",
                       starts_on="2026-01-31")
        chain = _due(c, "location", store_id, name="Monthly chain", assignee="bot:coder", repeats="every_months",
                     every=1, starts_on="2026-01-31")
    _complete(books, monthly, "2026-01-31", at="2026-01-31T12:00:00.000000Z")
    assert _next(books, monthly, "2026-01-31T12:00:00.000000Z") == "2026-02-28"   # 2026 is not a leap year
    _complete(books, chain, "2026-01-31", at="2026-01-31T12:00:00.000000Z")
    assert _next(books, chain, "2026-01-31T12:00:00.000000Z") == "2026-02-28"
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
    """X3: the company's hidden sections are a sidebar setting; the pass reads none of them."""
    with books.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata(key, value_json) VALUES('hidden_sections', ?)",
                  (json.dumps(["operations", "operations.equipment"]),))
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


# ------------------------------------------------------------------------------------- assignees and who may ask
def test_an_archived_assignee_becomes_one_needs_you_item_for_the_owner_and_nothing_is_dropped(books):
    with books.transaction() as c:
        extinguisher = _due(c, "location", _location(c, "Acme Extinguisher Store"), name="Extinguisher check",
                            assignee="bot:coder")
        c.execute("UPDATE bots SET state='archived' WHERE slug='coder'")
    result = _tick(books)
    assert result["tasks"] == [] and len(result["notices"]) == 1
    [notice] = _tasks(books, "human:ana", prefix="Due date has no teammate")
    assert "Extinguisher check" in notice["body"] and "archived" in notice["body"]
    with books.read() as c:
        assert RD.next_open(RD.get(c, extinguisher), RD.completed_days(c, extinguisher)) is not None
    assert _tick(books, later(0, 2)) is None      # the same day, nothing more


def test_a_missing_assignee_is_refused_when_the_due_date_is_made(books):
    with books.transaction() as c:
        res = act(c, OWNER, "due_date_create", {"name": "Check", "subject_type": "location",
                                                "subject_id": _location(c, "Acme Missing Teammate"),
                                                "repeats": "once", "starts_on": DAY, "assignee_id": "nobody"})
    assert res.code == "invalid" and "teammate" in res.reason


def test_a_teammate_without_the_records_right_cannot_make_or_complete_due_dates(books):
    with books.transaction() as c:
        res = act(c, "human:eve", "due_date_create", {"name": "Check", "subject_type": "location",
                                                      "subject_id": _location(c, "Acme Eve Store"),
                                                      "repeats": "once", "starts_on": DAY,
                                                      "assignee_id": "human:ben"})
    assert res.status == "denied"


def test_a_teammate_with_no_finance_view_is_refused_by_the_reads(books):
    """Members default to own expenses; with the member default set to none, eve (who leads and manages nobody) has no
    view at all, so the reads refuse rather than list due dates."""
    with books.transaction() as c:
        assert act(c, OWNER, "view_defaults_set", {"manager": "own_expenses", "member": "none"}).status == "committed"
    with books.read() as c:
        try:
            FD.listing(c, "human:eve")
        except FinanceError as exc:
            assert exc.code == "denied"
        else:
            raise AssertionError("the listing answered a teammate with no finance view")


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
