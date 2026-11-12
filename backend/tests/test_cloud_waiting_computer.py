"""A Tico Cloud company in its first minutes, before the shared cloud machine's computer enrolls.

Its bots have no computer yet, and none is theirs to add: each goes on the cloud machine when it enrolls. So a bot
waiting for it says it waits for that machine (named) to start, links to Settings > Computers, and asks nobody; a
person's go-live is not refused but finishes once the machine is up. Only a machine that failed, or a company out of
credit, is a person's to act on. HQ is a fake (helpers/cloud_machines.py); the routes are the real ones."""

import uuid

from backend import cloud_access, task_wakes, waiting_on
from backend.tests.helpers.cloud_access import signals
from backend.tests.helpers.cloud_machines import FakeHQ
from backend.tests.helpers.sample_company import booted, environment, rows, signed_in  # noqa: F401


def _cloud_company(environment, monkeypatch):
    """Setup finished on Tico Cloud: HQ lists its sizes and makes the shared machine, which has not enrolled."""
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
    hq, manager = FakeHQ(), api.app.state.cloud_machines
    monkeypatch.setattr(manager, "client", hq)
    manager.tick()
    assert [call[1] for call in hq.calls if call[0] == "create"] == ["_shared"]
    assert not rows(api, "SELECT 1 FROM assignments WHERE bot='botops'")
    return api, hq, manager


def _waits(api, bot="bot:botops"):
    """BotOps' cause, its line, its page, and every Needs you row that names it."""
    with api.app.state.store.read() as c:
        cause = task_wakes.cannot_run(c, bot)
        line = task_wakes.cause_line(c, bot, cause) if cause else None
        needs = [row for found in waiting_on.needs_by_person(c).values() for row in found if bot in row.get("bots", [])]
    return cause, line, task_wakes.cause_href(cause), needs


def _issue(api):
    """BotOps' line on Settings > Computers."""
    found = api.get("/api/v2/operations", headers=signed_in()).json()["issues"]
    return next((issue for issue in found if issue.get("bot") == "botops" and issue["kind"] == "machine"), None)


def test_a_bot_waiting_for_its_cloud_machine_names_it_links_to_computers_and_asks_nobody(environment, monkeypatch):
    api, _, _ = _cloud_company(environment, monkeypatch)
    cause, line, href, needs = _waits(api)
    assert cause == task_wakes.CLOUD_STARTING
    assert line == "BotOps is waiting for Shared cloud machine to start"
    assert href == "#/settings/computers"
    assert not needs, "a machine on its way is nobody's to fix"
    issue = _issue(api)
    assert issue and not issue["needs_person"] and "registered computer" not in str(issue)
    computers = api.get("/api/v2/operations", headers=signed_in()).json()
    assert [(r["label"], r["status"]) for r in computers["cloud_coming"]] == [("Shared cloud machine", "starting")]
    assert api.get("/api/v2/bots/botops", headers=signed_in()).json()["cant_run"]["fix"] == "open_computers"


def test_a_failed_cloud_machine_or_no_credit_is_the_persons_with_where_to_fix_it(environment, monkeypatch):
    api, _, _ = _cloud_company(environment, monkeypatch)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='no_enroll' WHERE bot='_shared'")
    cause, line, href, needs = _waits(api)
    assert cause == task_wakes.CLOUD_FAILED and href == "#/settings/computers"
    assert line == "BotOps can't run: Shared cloud machine didn't start"
    assert [row["href"] for row in needs] == ["#/settings/computers"]
    assert _issue(api)["needs_person"]
    coming = api.get("/api/v2/operations", headers=signed_in()).json()["cloud_coming"]
    assert [(r["status"], r["replace"]) for r in coming] == [("failed", True)]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='' WHERE bot='_shared'")
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("frozen", "out_of_credit")))
    cause, _, href, needs = _waits(api)
    assert cause == task_wakes.OUT_OF_CREDIT and href == "#/settings/billing"
    assert [row["href"] for row in needs] == ["#/settings/billing"]


def test_a_self_hosted_bot_with_no_computer_keeps_add_a_computer(environment):
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE id='botops'")
    cause, line, href, _ = _waits(api)
    assert cause == task_wakes.NO_COMPUTER and href == "#/settings/computers"
    assert cause and line == "BotOps has no computer"
    assert "registered computer" in _issue(api)["action"]
    assert "cloud_coming" in api.get("/api/v2/operations", headers=signed_in()).json()
    seat = rows(api, "SELECT slug FROM bots WHERE state='planned' LIMIT 1")[0]["slug"]
    live = api.post(f"/api/v2/bots/{seat}/go-live", json={}, headers=signed_in())
    assert live.status_code == 409 and live.json()["error"]["code"] == "no_computer", live.text


def test_going_live_before_the_cloud_machine_enrolls_finishes_once_it_is_up(environment, monkeypatch):
    api, hq, manager = _cloud_company(environment, monkeypatch)
    seat = rows(api, "SELECT id,slug FROM bots WHERE state='planned' AND slug<>'builder' ORDER BY slug LIMIT 1")[0]
    live = api.post(f"/api/v2/bots/{seat['slug']}/go-live", json={}, headers=signed_in())
    assert live.status_code == 202, live.text
    answer = live.json()
    assert answer["computer"] == "Shared cloud machine" and "to start" in answer["message"]
    assert rows(api, "SELECT state FROM bots WHERE id=?", (seat["id"],)) == [{"state": "planned"}]
    # The machine enrolls: the bot is put on it, and its report of the repository finishes the go-live.
    computer = api.post(
        "/api/v2/runners/enroll",
        json={"code": hq.calls[-1][3], "label": "boat-vm", "platform": "linux"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    manager.tick()
    assert rows(api, "SELECT runner_id FROM assignments WHERE bot=?", (seat["id"],)) == [
        {"runner_id": computer["runner_id"]}
    ]
    ready = {"ready": True, "runtime": "codex", "repository_present": True, "configuration_valid": True, "problems": []}
    body = {
        "version": "test",
        "platform": "test",
        "capacity": 4,
        "readiness": {"schema_version": 1, "bots": {seat["id"]: ready, "botops": ready}},
    }
    beat = api.post(
        "/api/v2/runners/heartbeat",
        json=body,
        headers={"Authorization": "Bearer " + computer["token"], "Idempotency-Key": str(uuid.uuid4())},
    )
    assert beat.status_code == 200, beat.text
    assert rows(api, "SELECT state FROM bots WHERE id=?", (seat["id"],)) == [{"state": "active"}]


def test_going_live_on_a_failed_cloud_machine_says_so(environment, monkeypatch):
    api, _, _ = _cloud_company(environment, monkeypatch)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE cloud_machines SET problem='no_enroll' WHERE bot='_shared'")
    seat = rows(api, "SELECT slug FROM bots WHERE state='planned' AND slug<>'builder' ORDER BY slug LIMIT 1")[0]
    live = api.post(f"/api/v2/bots/{seat['slug']}/go-live", json={}, headers=signed_in())
    assert live.status_code == 409 and live.json()["error"]["code"] == "cloud_computer_failed", live.text
    assert "Settings > Computers" in live.json()["error"]["detail"]
