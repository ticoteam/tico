"""The exit computer for a person's saved provider plans."""

import pytest

from backend import cloud_machines, hosting_enrollment, plan_exits, runner_states
from backend.store import H, digest
from backend.tests.helpers.api import get, headers, post, put, runner
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


def test_replacing_a_manually_selected_cloud_exit_does_not_pick_its_replacement(api):
    setup(api)
    store = api.app.state.store
    key = cloud_machines.SHARED
    with store.transaction() as c:
        c.execute("INSERT INTO cloud_machines(bot,state,updated_at) VALUES(?,'stopped',?)", (key, H.now()))
        code = cloud_machines.mint(c, key, owner="ana")
    old = post(api, "runners/enroll", {"code": code, "label": "Cloud machine", "platform": "linux"})["runner_id"]
    add(api)
    put(api, f"{EXITS}/ana", {"runner_id": old})
    assert stored_exit(api) == (old, 1)

    with store.transaction() as c:
        code = cloud_machines.mint(c, key, owner="ana")
    new = post(api, "runners/enroll", {"code": code, "label": "Cloud machine", "platform": "linux"})["runner_id"]
    assert new != old
    assert stored_exit(api) == (None, 2)
    assert get(api, f"{EXITS}/ana")["runner_label"] is None


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
    first = runner(api, operator="cara", label="First Mac")["runner_id"]
    add(api, token="cara-test")
    own = runner(api, operator="cara", label="Second Mac")["runner_id"]
    shared = runner(api, label="Shared Mac")["runner_id"]
    private = runner(api, label="Private Mac")["runner_id"]
    post(api, f"computers/{private}/member-bots", {"accepts": False})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('eve','Eve','eve@acme.example')")

    path = f"{EXITS}/cara"
    changes = (("cara-test", shared), ("ben-test", own), ("cara-test", first))
    for generation, (token, computer) in enumerate(changes, start=2):
        changed = put(api, path, {"runner_id": computer}, token=token)
        assert (changed["runner_id"], changed["generation"]) == (computer, generation)
        assert put(api, path, {"runner_id": computer}, token=token) == changed

    put(api, path, {"runner_id": own}, token="eve-test", expected=403)
    put(api, path, {"runner_id": private}, token="cara-test", expected=403)
    assert stored_exit(api, "cara") == (first, 4)
    with api.app.state.store.read() as c:
        assert [
            tuple(row)
            for row in c.execute("SELECT actor,target FROM events WHERE action='subscription.plan_exit' ORDER BY rowid")
        ] == [("human:cara", "cara"), ("human:ben", "cara"), ("human:cara", "cara")]


def test_an_admin_cannot_choose_a_shared_computer_for_another_pool_owner(api):
    setup(api)
    own = runner(api, operator="cara", label="Her Mac")["runner_id"]
    add(api, token="cara-test")
    shared = runner(api, label="Shared Mac")["runner_id"]
    path = f"{EXITS}/cara"
    assert [r["id"] for r in get(api, path, token="ben-test")["computers"]] == [own]
    assert shared in [r["id"] for r in get(api, path, token="cara-test")["computers"]]
    answer = put(api, path, {"runner_id": shared}, token="ben-test", expected=403)
    assert answer["error"]["detail"] == "Choose a registered computer owned by the pool owner"
    assert stored_exit(api, "cara") == (own, 1)


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


def test_private_exit_details_are_hidden_from_other_members(api):
    setup(api)
    computer = runner(api, operator="cara", label="Private Mac")["runner_id"]
    post(api, f"computers/{computer}/member-bots", {"accepts": False})
    add(api)
    path = f"{EXITS}/ana"
    put(api, path, {"runner_id": computer})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('eve','Eve','eve@acme.example')")

    [hidden] = get(api, SUBSCRIPTIONS, token="eve-test")["plan_exits"]
    assert hidden == {
        "person_id": "ana",
        "runner_id": None,
        "runner_label": "Private computer",
        "generation": 1,
        "can_change": False,
    }
    assert get(api, path, token="eve-test") == {**hidden, "computers": [], "next_after": None}
    for token in ("ana-test", "ben-test", "cara-test"):
        visible = get(api, path, token=token)
        assert (visible["runner_id"], visible["runner_label"], visible["generation"]) == (computer, "Private Mac", 1)


def test_closing_a_shared_exit_rechooses_only_for_members_who_lose_access(api):
    setup(api)
    post(api, "access/humans", {"email": "eve@acme.example"})
    shared = runner(api, operator="cara", label="Shared Mac")["runner_id"]
    personal = runner(api, operator="eve", label="Eve's Mac")["runner_id"]
    for person in ("cara", "ben", "eve"):
        add(api, token=f"{person}-test")
        put(api, f"{EXITS}/{person}", {"runner_id": shared}, token=f"{person}-test")

    post(api, f"computers/{shared}/member-bots", {"accepts": False})
    assert stored_exit(api, "cara") == (shared, 1)
    assert stored_exit(api, "ben") == (shared, 1)
    assert stored_exit(api, "eve") == (personal, 3)


def test_departure_clears_a_plan_exit_and_a_legacy_exit_does_not_block_removal(api):
    person = post(api, "access/humans", {"email": "typo@acme.example"})["person"]
    store = api.app.state.store
    computer = runner(api, label="Their Mac")["runner_id"]
    with store.transaction() as c:
        c.execute("INSERT INTO plan_exits(person_id,runner_id,generation) VALUES(?,?,1)", (person, computer))
    post(api, f"humans/{person}", {"left": True})
    # Cleared, not deleted: a later exit after they come back gets a higher generation.
    assert stored_exit(api, person) == (None, 2)

    # Someone marked left before exits were cleared can still be removed.
    removed = api.delete(f"/api/v2/humans/{person}", headers=headers("ana-test"))
    assert removed.status_code == 200, removed.text
    assert stored_exit(api, person) is None


def test_model_subscriptions_lists_each_plan_pools_exit(api):
    setup(api)
    computer = runner(api, label="Office Mac")["runner_id"]
    add(api)
    add(api, token="cara-test")

    assert get(api, SUBSCRIPTIONS, token="cara-test")["plan_exits"] == [
        {"person_id": "ana", "runner_id": computer, "runner_label": "Office Mac", "generation": 1, "can_change": False},
        {"person_id": "cara", "runner_id": None, "runner_label": None, "generation": 0, "can_change": True},
    ]


def test_an_unknown_pool_is_not_found(api):
    setup(api)
    for person in ("nobody", "bot:ops"):
        assert api.get(f"/api/v2/{EXITS}/{person}", headers=headers("ana-test")).status_code == 404


def test_an_admin_changes_the_exit_of_someone_whose_sign_in_is_off(api):
    setup(api)
    add(api, token="cara-test")
    runner(api, operator="cara", label="First Mac")
    computer = runner(api, operator="cara", label="Office Mac")["runner_id"]
    post(api, "access/humans/cara", {"sign_in": False})
    assert put(api, f"{EXITS}/cara", {"runner_id": computer})["runner_id"] == computer


def test_departure_without_an_exit_adds_no_row(api):
    person = post(api, "access/humans", {"email": "never@acme.example"})["person"]
    post(api, f"humans/{person}", {"left": True})
    assert stored_exit(api, person) is None
