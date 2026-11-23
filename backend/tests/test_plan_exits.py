"""The exit computer for a person's saved provider plans."""

import pytest

from backend import hosting_enrollment, plan_exits, runner_states
from backend.store import H, digest
from backend.tests.helpers.api import get, post, put, runner
from backend.tests.helpers.claude_login import full
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import TOKEN, add, codex_file


SUBSCRIPTIONS = "model-subscriptions"
EXITS = f"{SUBSCRIPTIONS}/plan-exits"
API_KEY = "sk-ant-api-" + "c" * 24


def stored_exit(api, person="ana"):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT runner_id,generation FROM plan_exits WHERE person_id=?", (person,)).fetchone()
    return tuple(row) if row else None


@pytest.mark.parametrize("kind", ["claude_login", "claude_token", "codex_login"])
def test_a_personal_plan_uses_the_owners_most_recently_seen_computer(api, kind):
    setup(api)
    recent = runner(api, operator="cara", label="Recently seen")
    new = runner(api, operator="cara", label="Recently enrolled")
    other = runner(api, label="Another owner's computer")
    now = H.now()
    with api.app.state.store.transaction() as c:
        runner_states.save(c, recent["runner_id"], last_seen_at=H.shift(now, minutes=-1))
        runner_states.save(c, new["runner_id"], last_seen_at=H.shift(now, minutes=-2))
        runner_states.save(c, other["runner_id"], last_seen_at=now)

    add(api, "anthropic_key", API_KEY, name="Work key", token="cara-test")
    assert stored_exit(api, "cara") is None
    secret = {"claude_login": full(), "claude_token": TOKEN, "codex_login": codex_file()}[kind]
    post(api, SUBSCRIPTIONS, {"kind": kind, "secret": secret, "owner_id": "human:cara"})
    assert stored_exit(api, "cara") == (recent["runner_id"], 1)
    assert stored_exit(api) is None


def test_cloud_and_hosted_computers_are_excluded_from_the_default(api):
    setup(api)
    state = api.app.state
    personal = runner(api, label="Personal Mac")
    cloud = runner(api, label="Cloud machine")
    code = "acme-test-hosting-enrollment-code"
    hosting_enrollment.enrollment(
        state.store, state.auth, "pv-0000000000000001", digest(code), "Hosted computer", "ana@acme.example"
    )
    hosted = post(api, "runners/enroll", {"code": code, "label": "Hosted computer", "platform": "linux"})
    now = H.now()
    with state.store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot,runner_id,state,updated_at) VALUES('cpo',?,'stopped',?)",
            (cloud["runner_id"], now),
        )
        runner_states.save(c, personal["runner_id"], last_seen_at=H.shift(now, minutes=-1))
        for computer in (cloud, hosted):
            runner_states.save(c, computer["runner_id"], last_seen_at=now)

    add(api)
    assert stored_exit(api) == (personal["runner_id"], 1)


def test_revoking_the_exit_moves_it_to_the_next_computer_then_clears_it(api):
    setup(api)
    primary = runner(api, label="Primary")["runner_id"]
    backup = runner(api, label="Backup")["runner_id"]
    with api.app.state.store.transaction() as c:
        runner_states.save(c, primary, last_seen_at=H.now())
    add(api)
    assert stored_exit(api) == (primary, 1)

    post(api, f"computers/{primary}/revoke", {})
    assert stored_exit(api) == (backup, 2)
    post(api, f"computers/{backup}/revoke", {})
    assert stored_exit(api) == (None, 3)


def test_enrolling_a_first_personal_computer_fills_the_exit(api):
    setup(api)
    add(api)
    assert stored_exit(api) is None

    first = runner(api, label="First Mac")["runner_id"]
    assert stored_exit(api) == (first, 1)


def test_only_the_owner_and_admin_can_change_the_exit_to_an_eligible_computer(api):
    setup(api)
    runner(api, operator="cara", label="First Mac")
    add(api, token="cara-test")
    own = runner(api, operator="cara", label="Second Mac")["runner_id"]
    shared = runner(api, label="Shared Mac")["runner_id"]
    private = runner(api, label="Private Mac")["runner_id"]
    post(api, f"computers/{private}/member-bots", {"accepts": False})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('eve','Eve','eve@acme.example')")

    path = f"{EXITS}/cara"
    for generation, (token, computer) in enumerate((("cara-test", own), ("ben-test", shared)), start=2):
        changed = put(api, path, {"runner_id": computer}, token=token)
        assert (changed["runner_id"], changed["generation"]) == (computer, generation)
        assert put(api, path, {"runner_id": computer}, token=token) == changed

    put(api, path, {"runner_id": own}, token="eve-test", expected=403)
    put(api, path, {"runner_id": private}, token="ben-test", expected=403)
    assert stored_exit(api, "cara") == (shared, 3)
    with api.app.state.store.read() as c:
        assert [
            tuple(row)
            for row in c.execute("SELECT actor,target FROM events WHERE action='subscription.plan_exit' ORDER BY rowid")
        ] == [("human:cara", "cara"), ("human:ben", "cara")]


def test_exit_computers_are_paged_and_visible_only_to_people_who_can_change_it(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(plan_exits, "PAGE_SIZE", 1)
    computers = sorted(
        ({"id": runner(api, label=label)["runner_id"], "label": label} for label in ("First Mac", "Second Mac")),
        key=lambda computer: computer["id"],
    )
    add(api)
    path = f"{EXITS}/ana"
    first = get(api, path)
    assert first["can_change"] is True
    assert first["computers"] == computers[:1]
    assert first["next_after"] == computers[0]["id"]

    second = get(api, f"{path}?after={first['next_after']}")
    assert second["computers"] == computers[1:]
    assert second["next_after"] is None

    readonly = get(api, path, token="cara-test")
    assert readonly["runner_id"] == first["runner_id"]
    assert readonly["can_change"] is False
    assert readonly["computers"] == [] and readonly["next_after"] is None


def test_health_warns_only_for_a_personal_plan_without_an_exit(api):
    setup(api)

    def warnings():
        return [check for check in get(api, "health")["checks"] if check["id"] == "plan_exit_missing"]

    add(api, "anthropic_key", API_KEY)
    assert warnings() == []
    add(api, token="cara-test")
    [check] = warnings()
    assert (check["label"], check["status"]) == ("No exit computer", "warn")
    assert [item["name"] for item in check["items"]] == ["Cara"]
    # The issue list sorts every check's items by name, so this one must carry the shared item shape.
    assert [i["name"] for i in get(api, "health/issues")["issues"] if i["kind"] == "plan_exit_missing"] == ["Cara"]

    runner(api, operator="cara")
    assert warnings() == []


def test_model_subscriptions_lists_each_plan_pools_exit(api):
    setup(api)
    computer = runner(api, label="Office Mac")["runner_id"]
    add(api)
    add(api, token="cara-test")

    assert get(api, SUBSCRIPTIONS, token="cara-test")["plan_exits"] == [
        {"person_id": "ana", "runner_id": computer, "runner_label": "Office Mac", "generation": 1, "can_change": False},
        {"person_id": "cara", "runner_id": None, "runner_label": None, "generation": 0, "can_change": True},
    ]
