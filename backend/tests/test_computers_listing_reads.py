"""The Computers list reads fleet-wide rows once, not once per computer."""

from backend.tests.test_api import api, get, runner  # noqa: F401
from backend.tests.test_task_privacy_reads import traced

FLEET_WIDE = ("FROM runner_versions", "computer-repositories:")


def counted(api, token):
    page, statements = traced(api, lambda: get(api, "computers", token=token))
    return page, {name: sum(name in s for s in statements) for name in FLEET_WIDE}


def test_computers_reads_fleet_wide_rows_once_however_many_computers(api):
    for label in ("A Mac", "B Mac"):
        runner(api, "ana", label)
    few = counted(api, "ana-test")
    for label in ("C Mac", "D Mac", "E Mac", "F Mac"):
        runner(api, "ana", label)
    many = counted(api, "ana-test")
    assert len(many[0]["computers"]) == 6
    assert many[1] == few[1], (few[1], many[1])
