"""The flaky-test ledger (scripts/flaky-tests.json): what the check refuses, and which test ids get the one retry.

The ledger is checked in seconds on every pull request, so these run on fake trees under tmp_path, and the seeded
ledger is checked against the repository itself.
"""

import datetime as dt
import json

import pytest

from scripts import flaky_ledger as ledger

DAY = dt.date(2026, 11, 6)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "backend/tests").mkdir(parents=True)
    (tmp_path / "backend/tests/test_timing.py").write_text(
        "def test_plain():\n    pass\n\nasync def test_async_one():\n    pass\n"
    )
    (tmp_path / "ui/tests").mkdir(parents=True)
    (tmp_path / "ui/tests/google-tab.cjs").write_text("// script\n")
    return tmp_path


def entry(**changes):
    base = {
        "id": "backend/tests/test_timing.py::test_plain",
        "reason": "Timing budget under load.",
        "owner": "BotOps",
        "added": "2026-11-06",
        "expires": "2026-11-20",
    }
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def test_a_clean_entry_has_no_problems(tree):
    assert ledger.problems([entry()], root=tree, on=DAY) == []


def test_a_browser_script_and_an_async_test_are_accepted(tree):
    entries = [
        entry(id="ui/tests/google-tab.cjs"),
        entry(id="backend/tests/test_timing.py::test_async_one"),
    ]
    assert ledger.problems(entries, root=tree, on=DAY) == []


@pytest.mark.parametrize("field", ["owner", "reason", "added", "expires"])
def test_a_missing_field_is_refused(tree, field):
    found = ledger.problems([entry(**{field: None})], root=tree, on=DAY)
    assert f"backend/tests/test_timing.py::test_plain: missing {field}" in found


def test_a_blank_owner_is_refused(tree):
    assert "missing owner" in ledger.problems([entry(owner="  ")], root=tree, on=DAY)[0]


def test_an_exit_date_more_than_fourteen_days_out_is_refused(tree):
    found = ledger.problems([entry(expires="2026-11-21")], root=tree, on=DAY)
    assert any("more than 14 days after added" in line for line in found)


def test_exactly_fourteen_days_is_allowed(tree):
    assert ledger.problems([entry(expires="2026-11-20")], root=tree, on=DAY) == []


def test_an_exit_date_before_the_date_added_is_refused(tree):
    found = ledger.problems([entry(expires="2026-11-01")], root=tree, on=DAY)
    assert any("is before added" in line for line in found)


@pytest.mark.parametrize("value", ["2026/11/20", "20-11-2026", "Nov 20", "2026-13-01"])
def test_a_date_that_is_not_a_real_iso_date_is_refused(tree, value):
    found = ledger.problems([entry(expires=value)], root=tree, on=DAY)
    assert any("expires must be a YYYY-MM-DD date" in line for line in found)


def test_an_expired_entry_is_refused_on_its_day_after(tree):
    assert ledger.problems([entry()], root=tree, on=dt.date(2026, 11, 20)) == []
    found = ledger.problems([entry()], root=tree, on=dt.date(2026, 11, 21))
    assert found == ["backend/tests/test_timing.py::test_plain: expired 2026-11-20; fix the test and remove the entry"]


def test_a_duplicate_id_is_refused(tree):
    found = ledger.problems([entry(), entry()], root=tree, on=DAY)
    assert found == ["backend/tests/test_timing.py::test_plain: listed twice"]


def test_a_test_that_no_longer_exists_is_refused(tree):
    found = ledger.problems([entry(id="backend/tests/test_timing.py::test_renamed")], root=tree, on=DAY)
    assert found == [
        "backend/tests/test_timing.py::test_renamed: no test function test_renamed in backend/tests/test_timing.py"
    ]


def test_a_missing_test_file_and_script_are_refused(tree):
    found = ledger.problems(
        [entry(id="backend/tests/test_gone.py::test_x"), entry(id="ui/tests/gone.cjs")], root=tree, on=DAY
    )
    assert found == [
        "backend/tests/test_gone.py::test_x: no such test file backend/tests/test_gone.py",
        "ui/tests/gone.cjs: no such browser script ui/tests/gone.cjs",
    ]


def test_an_id_of_neither_form_is_refused(tree):
    found = ledger.problems([entry(id="test_plain")], root=tree, on=DAY)
    assert found == ["test_plain: id must be path/to/test_file.py::test_name or ui/tests/name.cjs"]


def test_a_non_object_entry_is_refused(tree):
    assert ledger.problems(["oops"], root=tree, on=DAY) == ["entry 1: not an object"]


def test_active_keeps_only_entries_whose_exit_date_has_not_passed():
    entries = [
        entry(id="a.cjs", expires="2026-11-20"),
        entry(id="b.cjs", expires="2026-11-19"),
        entry(id="c.cjs", expires="soon"),
    ]
    assert [e["id"] for e in ledger.active(entries, on=dt.date(2026, 11, 20))] == ["a.cjs"]


def test_matches_takes_the_test_and_its_parametrized_cases_but_not_a_longer_name():
    listed = "scripts/tests/test_image_pins.py::test_every_pin"
    assert ledger.matches(listed, listed)
    assert ledger.matches(listed + "[backend/requirements.txt]", listed)
    assert not ledger.matches("scripts/tests/test_image_pins.py::test_every_pin_has_more", listed)


def test_load_refuses_a_file_without_a_tests_list(tmp_path):
    path = tmp_path / "flaky-tests.json"
    path.write_text(json.dumps([{"id": "x"}]))
    with pytest.raises(ValueError, match="'tests' list"):
        ledger.load(path)


def test_the_seeded_ledger_is_valid_on_the_day_it_was_written():
    entries = ledger.load()
    assert entries, "the seed lists the open flakes"
    assert ledger.problems(entries, on=DAY) == []
    assert all(entry["owner"] for entry in entries)
    assert all(
        dt.date.fromisoformat(e["expires"]) - dt.date.fromisoformat(e["added"]) <= dt.timedelta(days=ledger.MAX_DAYS)
        for e in entries
    )


def test_the_check_script_exits_nonzero_on_a_bad_ledger(tmp_path, monkeypatch, capsys):
    path = tmp_path / "flaky-tests.json"
    path.write_text(json.dumps({"tests": [entry(owner=None)]}))
    monkeypatch.setattr(ledger, "LEDGER", path)
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    assert ledger.main() == 1
    assert "missing owner" in capsys.readouterr().err
