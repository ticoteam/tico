"""Tico Cloud access (backend/cloud_access.py): HQ's open, frozen or locked, passed through Settings > Billing and kept;
frozen holds turns that would spend credit and passes routines over; locked refuses people everywhere but billing and
the download, and runs no bot; open again resumes everything with no restart. Nothing changes off cloud. HQ is a fake."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import automations, cloud_access, company_billing, routines
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_company_billing import ANSWER, KEY, URL, FakeHQ
from backend.tests.test_gateway_enroll import fake_hq
from backend.tests.test_gateway_enroll import setup as enroll_setup
from backend.tests.test_gateway_enroll import tick

BILLING_URL = "https://app.tico.dev/billing/boatyard"


def signals(access="open", reason=None, **extra):
    return {"plan": "trial", "access": access, "reason": reason, "balance_micros": 0 if access != "open" else 2_000_000,
            "trial_ends": "2026-11-12T00:00:00Z", "trial_days_left": 14, "card": None, "auto_recharge": False,
            "period_end": None, "price_micros": 20_000_000, "billing_url": BILLING_URL, **extra}


@pytest.fixture
def hq(api, monkeypatch):  # noqa: F811
    fake = FakeHQ()
    fake.answers = {"/billing": (200, ANSWER, "application/json"), "/cloud": (404, {"error": "not_cloud"}, "")}
    monkeypatch.setattr(company_billing, "OPENER", fake)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL)
    return fake


def sync(api):
    return cloud_access.sync(api.app.state.store, api.app.state.vault, api.app.state.store.settings)


def say(api, hq, access, reason=None):
    hq.answers["/cloud"] = (200, signals(access, reason), "application/json")
    return sync(api)


def test_billing_passes_cloud_through_and_keeps_it(api, hq):  # noqa: F811
    # An answer without `cloud` (self-host, EC2 hosted): nothing kept, nothing shown.
    assert get(api, "billing")["cloud"] is None
    assert api.get("/api/status", headers=headers()).json()["cloud_access"] is None
    hq.answers["/billing"] = (200, {**ANSWER, "state": "paused", "cloud": signals("frozen", "out_of_credit")},
                              "application/json")
    shown = get(api, "billing")
    assert shown["state"] == "paused" and shown["cloud"]["access"] == "frozen"
    assert shown["cloud"]["billing_url"] == BILLING_URL and shown["cloud"]["reason"] == "out_of_credit"
    # Kept at once: every page's status poll says so, with no call to /cloud.
    status = api.get("/api/status", headers=headers()).json()
    assert status["cloud_access"] == {"access": "frozen", "reason": "out_of_credit", "plan": "trial",
                                      "trial_days_left": 14, "billing_url": BILLING_URL}
    assert not [url for url, _ in hq.calls if url.endswith("/cloud")]
    # Credit added: the page's next answer opens it again.
    hq.answers["/billing"] = (200, {**ANSWER, "cloud": signals("open")}, "application/json")
    assert get(api, "billing")["cloud"]["access"] == "open"
    assert api.get("/api/status", headers=headers()).json()["cloud_access"]["access"] == "open"


def test_an_odd_cloud_answer_is_refused_and_the_last_word_kept(api, hq):  # noqa: F811
    say(api, hq, "frozen", "out_of_credit")
    for odd in ({**signals("open"), "access": "closed"}, {**signals("open"), "balance_micros": 1.5}, ["open"]):
        hq.answers["/cloud"] = (200, odd, "application/json")
        assert sync(api)["access"] == "frozen"
    hq.answers["/billing"] = (200, {**ANSWER, "cloud": {"access": "open", "reason": "gift"}}, "application/json")
    assert get(api, "billing")["state"] == "unreachable"
    hq.answers["/cloud"] = (503, {"error": "down"}, "")
    assert sync(api)["access"] == "frozen"                     # HQ down keeps the last word
    # A billing page that is not https is dropped, never linked.
    hq.answers["/cloud"] = (200, signals("open", billing_url="javascript:alert(1)"), "application/json")
    assert sync(api)["billing_url"] is None


def test_off_cloud_nothing_changes(api, hq):  # noqa: F811
    assert sync(api) is None
    with api.app.state.store.read() as c:
        assert cloud_access.state(c) is None and not cloud_access.frozen(c) and cloud_access.routines_paused(c) is None
    assert get(api, "cloud-access") == {"cloud": None}
    get(api, "tasks")
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    assert claim(api, r, "ops") is not None


def test_locked_refuses_people_except_billing_and_the_download_and_opens_again_by_itself(api, hq):  # noqa: F811
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    say(api, hq, "locked", "trial_over")
    refused = api.get("/api/v2/tasks", headers=headers())
    assert refused.status_code == 402, refused.text
    error = refused.json()["error"]
    assert error["code"] == "locked" and error["cloud"]["billing_url"] == BILLING_URL
    assert api.post("/api/v2/chat/ops", json={"text": "hi"}, headers=headers("cara-test")).status_code == 402
    for path in ("/api/me", "/api/status", "/api/v2/cloud-access", "/api/v2/billing"):
        assert api.get(path, headers=headers()).status_code == 200, path
    assert api.get("/api/v2/system/storage/export", headers=headers()).status_code != 402
    assert get(api, "cloud-access")["cloud"]["access"] == "locked"
    # No bot runs: the claim answers paused, and Health says why.
    answer = post(api, "jobs/claim", {"bot": "ops", "start_places": True}, token=r["token"])
    assert answer["attempt"] is None and answer["paused"] == cloud_access.LOCKED
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE bot='ops'").fetchone()[0] == "queued"
        assert c.execute("SELECT detail FROM events WHERE action='cloud.access'").fetchone() is not None
    # Paid: the next answer opens everything, with no restart.
    say(api, hq, "open")
    get(api, "tasks")
    assert claim(api, r, "ops") is not None
    with api.app.state.store.read() as c:
        moves = [json.loads(row[0]) for row in c.execute(
            "SELECT detail FROM events WHERE action='cloud.access' ORDER BY rowid")]
    assert [(m["from"], m["to"]) for m in moves] == [(None, "locked"), ("locked", "open")]


def test_a_restart_keeps_the_lock_until_hq_answers(api, hq):  # noqa: F811
    say(api, hq, "locked", "unpaid")
    store = api.app.state.store
    del store._cloud_access                                    # a new process: read from the database
    assert cloud_access.current(store)["access"] == "locked"
    assert api.get("/api/v2/tasks", headers=headers()).status_code == 402


def test_frozen_holds_tico_model_turns_and_lets_a_bot_on_its_own_model_run(api, hq):  # noqa: F811
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    say(api, hq, "frozen", "out_of_credit")
    get(api, "tasks")                                          # frozen keeps the app usable
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    assert claim(api, r, "ops") is not None                    # its own model spends no Tico credit


def test_frozen_holds_a_bot_on_a_tico_model_until_credit_lands(api, monkeypatch, hq):  # noqa: F811
    from backend import health
    from backend.tests.test_claim_refusals import health_admin, held
    from backend.tests.test_models_preset import choose, connected, on_tico
    connected(api, monkeypatch)
    choose(api, "", enabled=("anthropic", "openai", "tico"))
    machine = runner(api)
    assign(api, machine, "ops")
    on_tico(api, machine, True)
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("frozen", "out_of_credit")))
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    rows, found = held(api, machine)
    assert [row["reason"] for row in rows] == ["out_of_credit"] and "queue_idle" not in found
    with api.app.state.store.read() as c:
        [row] = health._stuck(c, rows, set(), health_admin(api), True)
        assert c.execute("SELECT count(*) FROM attempts WHERE bot='ops'").fetchone()[0] == 0
    assert row["why"].startswith("Out of credit") and row["fix"]["tab"] == "billing"
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
    assert claim(api, machine, "ops")["config"]["model"] == "tico-general-medium-1.0"


@pytest.mark.parametrize("access", ["frozen", "locked", "open"])
def test_routines_pass_over_while_frozen_or_locked(api, access):  # noqa: F811
    store = api.app.state.store
    with store.transaction() as c:
        routines.create(c, H.KEEPER, "ops", {"title": "Morning check", "cron": "0 7 * * *", "timezone": "UTC"},
                        key="morning")
        aid = routines.row(c, "ops:morning")["automation"]
        c.execute("UPDATE automations SET next_due=? WHERE id=?", ("2026-11-03T07:00:00.000000Z", aid))
    automations.tick(store, datetime(2026, 11, 2, 6, tzinfo=timezone.utc))      # the first tick: the built-ins
    cloud_access.apply(store, cloud_access.parse(signals(access, None if access == "open" else "out_of_credit")))
    with store.transaction() as c:
        c.execute("UPDATE automations SET next_due=? WHERE id=?", ("2026-11-02T07:00:00.000000Z", aid))
    automations.tick(store, datetime(2026, 11, 2, 8, tzinfo=timezone.utc))
    with store.read() as c:
        runs = c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0]
        skipped = [json.loads(row[0])["reason"] for row in c.execute(
            "SELECT detail FROM events WHERE action='automation.skipped' AND target=?", (aid,))]
    if access == "open":
        assert (runs, skipped) == (1, [])
    else:
        assert runs == 0 and skipped == [cloud_access.ROUTINES_PAUSED[access]]


def test_a_new_key_asks_a_cloud_cell_for_its_access_at_once(api, monkeypatch):  # noqa: F811
    # The first ask can run before the install has its key (the hourly wait was the bug): a cell's enrolled key asks now.
    asked = []
    monkeypatch.setenv("TICO_CELL", "1")
    monkeypatch.setattr(cloud_access, "sync_later", lambda store, vault, settings: asked.append(store))
    with fake_hq() as fake:
        enroll_setup(api, monkeypatch, fake)
        assert tick(api) == "enrolled"
    assert asked == [api.app.state.store]


def test_a_key_change_on_a_non_cell_install_makes_no_immediate_cloud_ask(api, monkeypatch):  # noqa: F811
    # Self-hosted and EC2-hosted installs keep their hourly ask: a new key does not ask HQ about cloud access at once.
    asked = []
    monkeypatch.delenv("TICO_CELL", raising=False)
    monkeypatch.delenv("TICO_UPDATES", raising=False)
    monkeypatch.setattr(cloud_access, "sync_later", lambda store, vault, settings: asked.append(store))
    with fake_hq() as fake:
        enroll_setup(api, monkeypatch, fake)
        assert tick(api) == "enrolled"
    assert asked == []


def test_a_cloud_cell_with_no_answer_yet_asks_every_sync_not_every_unknown(api, monkeypatch, hq):  # noqa: F811
    store = api.app.state.store
    monkeypatch.setenv("TICO_CELL", "1")
    last = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    assert cloud_access.current(store) is None
    assert not cloud_access.due(store, last + timedelta(seconds=59), last)
    assert cloud_access.due(store, last + cloud_access.SYNC, last)
    say(api, hq, "open")                                              # once HQ has answered, the same cadence holds
    assert cloud_access.due(store, last + cloud_access.SYNC, last)


def test_a_self_hosted_or_ec2_install_keeps_the_hourly_ask(api, monkeypatch):  # noqa: F811
    store = api.app.state.store
    monkeypatch.delenv("TICO_CELL", raising=False)
    monkeypatch.delenv("TICO_UPDATES", raising=False)
    last = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    assert cloud_access.current(store) is None
    assert not cloud_access.due(store, last + timedelta(seconds=60), last)
    assert not cloud_access.due(store, last + timedelta(minutes=59), last)
    assert cloud_access.due(store, last + cloud_access.UNKNOWN, last)
    assert cloud_access.due(store, last, None)                       # the first check of a start always runs


@pytest.mark.parametrize("env, cell", [({"TICO_CELL": "1"}, True), ({"TICO_UPDATES": "tico"}, True),
                                       ({"TICO_CELL": "0"}, False), ({"TICO_CELL": ""}, False), ({}, False)])
def test_a_cell_is_known_by_its_marker_or_an_older_cores_env(monkeypatch, env, cell):
    # A cell company now has an updater URL like any hosted server, so the URL cannot say "cell"; TICO_CELL=1 does.
    # TICO_UPDATES=tico is what an older core wrote, until that company's next `cell update` rewrites its .env.
    for key in ("TICO_CELL", "TICO_UPDATES"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TICO_UPDATER_URL", "http://10.208.0.1:8090")
    assert cloud_access.cell() is cell
