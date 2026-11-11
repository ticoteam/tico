"""A new Tico Cloud company's first go-lives, with no computer but the cloud machines HQ makes.

Setup seeds the default org; every bot goes on a cloud machine once HQ lists its sizes: the Builder on its own, the
rest on the shared one. The shared machine is made for BotOps' setup work, and BotOps then takes each seat bot live.
The Builder's own machine does not exist yet (a machine is made for work, and a bot that is not live has none), so
its go-live found no computer and answered 409 `no_computer`: the first Engineering seat a ticket needs never went
live. HQ is a fake (helpers/cloud_machines.py); the routes, BotOps' turn token, the runner's enroll, report and claim
are the real ones."""

from backend import cloud_access
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.cloud_access import signals
from backend.tests.helpers.cloud_machines import FakeHQ
from backend.tests.helpers.sample_company import booted, environment, machine, rows, signed_in  # noqa: F401
from backend.tests.test_seat_bots_go_live import _claim, _post, _report, _state


def test_botops_takes_the_builder_live_on_a_cloud_company_before_its_own_machine_exists(environment, monkeypatch):
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    cloud_access.apply(api.app.state.store, cloud_access.parse(signals("open")))
    hq, manager = FakeHQ(), api.app.state.cloud_machines
    monkeypatch.setattr(manager, "client", hq)
    manager.tick()  # HQ lists sizes: the bots go on cloud machines, and BotOps' setup work makes the shared one
    assert [call[1] for call in hq.calls if call[0] == "create"] == ["_shared"]
    seats = {r["id"]: r["slug"] for r in rows(api, "SELECT id,slug FROM bots WHERE state='planned'")}
    builder = next(bot for bot, slug in seats.items() if slug == "builder")
    assert rows(api, "SELECT placement FROM cloud_machines WHERE bot=?", (builder,)) == [{"placement": "own"}]
    computer = _post(api, "runners/enroll", {"code": hq.calls[-1][3], "label": "boat-vm", "platform": "linux"}, "")
    computer = computer.json()
    manager.tick()
    _report(api, computer)
    attempt = _claim(api, computer, "botops")
    assert attempt, "BotOps claims its setup work on the shared machine"
    for bot, slug in seats.items():
        live = act(api, attempt, "POST", f"bots/{slug}/go-live", {})
        assert live.status_code == 202, f"BotOps cannot take {slug} live: {live.status_code} {live.text[:300]}"
    _report(api, computer, present=list(seats))  # the machine has their repositories: each go-live finishes
    for bot, slug in seats.items():
        assert _state(api, bot)["state"] == "active", f"{slug} never went live: {_state(api, bot)}"
    work = _claim(api, computer, builder)
    assert work and work["task"]["title"] == "Set yourself up", f"the Builder takes no work once live: {work}"
    assert not rows(api, "SELECT 1 FROM events WHERE action='bot.needs_onboarding'")
