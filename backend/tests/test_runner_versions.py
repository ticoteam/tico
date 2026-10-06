"""Runner versions: comparison, the range a server accepts, claim gating and what Health and Computers show."""

import pytest

from backend import releases, runner_versions
from backend.tests.test_api import api, assign, post, ready, runner  # noqa: F401


@pytest.fixture(autouse=True)
def server_release(monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.3.0")
    monkeypatch.setattr(runner_versions, "MIN_RUNNER_RELEASE", "0.2.0")
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())


def report(api, r, release, kind="mac", update=None, **extra):
    body = {"version": "test", "platform": "test", "readiness": {"ops": True}, "release": release, "kind": kind, **extra}
    if update:
        body["update"] = update
    return post(api, "runners/heartbeat", body, token=r["token"])


def queue_work(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Work for the runner"})
    return r


def test_states_against_the_servers_release():
    state = runner_versions.state
    assert state("0.3.0") == "current" and state("0.4.0") == "current"          # a newer runner still works
    assert state("0.2.5") == "needs_update" and state("0.2.0") == "needs_update"
    assert state("0.1.9") == "incompatible" and state("0.1.9", "updating") == "incompatible"
    assert state("0.2.5", "updating") == "updating"
    assert state("dev") == "unknown" and state("") == "unknown"                  # not on a release: never paused
    assert state("0.3.0-rc.1") == "needs_update"                                 # a release outranks its candidates
    assert state("0.2.5", server="dev") == "unknown"
    assert not runner_versions.incompatible("dev") and not runner_versions.incompatible("")


def test_an_incompatible_runner_does_not_claim(api):
    r = queue_work(api)
    report(api, r, "0.1.0")
    reply = post(api, "jobs/claim", {"bot": None}, token=r["token"])
    assert reply["attempt"] is None and "0.2.0 or later" in reply["paused"] and "paused" in reply["paused"]
    report(api, r, "0.2.1")                                    # it updated
    assert post(api, "jobs/claim", {"bot": None}, token=r["token"])["attempt"]["bot"] == "ops"

