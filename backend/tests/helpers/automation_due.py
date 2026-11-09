"""Helpers that other tests share, moved out of test_automation_due.py so they import no test file."""

from backend import automations
from backend.tests.helpers.api import get


def runs_of(api, aid):
    return sorted(run["trigger_ref"] for run in get(api, f"automations/{aid}/runs")["runs"])


def tick(api, at):
    automations.tick(api.app.state.store, at)


def drain(api):
    api.app.state.store.automations.drain()
