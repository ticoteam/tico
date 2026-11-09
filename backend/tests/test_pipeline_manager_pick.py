"""A pipeline's manager is any active bot (pipeline manager, slice 1 of 3): the pipeline's owner and admins set or clear it
through the settings route and the task-type route. An archived, paused, planned or unknown bot is a 422 and the stored
manager does not change; the bare bot id is stored, from `bot:<id>` or `<id>`; "" clears it."""

from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend.tests.helpers.pipeline_settings_j import make_pipeline, read_settings, settings
from backend.tests.helpers.tasks_board import post


def set_bot_state(api, slug, state):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state=? WHERE slug=?", (state, slug))


def test_the_owner_picks_an_active_bot_and_it_is_stored_as_the_bare_id(api):
    pipe = make_pipeline(api, "Picks")
    settings(api, pipe, {"manager_id": "bot:cpo"})
    assert read_settings(api, pipe)["manager_id"] == "cpo"
    settings(api, pipe, {"manager_id": "coo"})  # a bare id is accepted too
    assert read_settings(api, pipe)["manager_id"] == "coo"


def test_an_archived_paused_planned_or_unknown_bot_is_refused_and_the_manager_stays(api):
    pipe = make_pipeline(api, "Refusals")
    settings(api, pipe, {"manager_id": "bot:cpo"})
    set_bot_state(api, "ops", "paused")
    set_bot_state(api, "finance", "planned")
    set_bot_state(api, "inbox", "archived")
    for ref in ("bot:ops", "finance", "bot:inbox", "bot:nobody", "human:ana"):
        response = settings(api, pipe, {"manager_id": ref}, expected=422)
        assert "No active bot" in str(response), ref
        assert read_settings(api, pipe)["manager_id"] == "cpo"


def test_an_empty_manager_clears_it(api):
    pipe = make_pipeline(api, "Cleared")
    settings(api, pipe, {"manager_id": "bot:cpo"})
    settings(api, pipe, {"manager_id": ""})
    assert read_settings(api, pipe)["manager_id"] is None


def test_a_setting_left_out_leaves_the_manager_as_it_is(api):
    pipe = make_pipeline(api, "Kept")
    settings(api, pipe, {"manager_id": "bot:cpo"})
    settings(api, pipe, {"goal": "Ship it"})
    assert read_settings(api, pipe)["manager_id"] == "cpo"


def test_only_the_pipelines_owner_or_an_admin_changes_the_manager(api):
    pipe = make_pipeline(api, "Owned", owner="human:ana")
    settings(api, pipe, {"manager_id": "bot:cpo"}, token="cara-test", expected=403)
    assert read_settings(api, pipe)["manager_id"] is None
    settings(api, pipe, {"manager_id": "bot:cpo"}, token="ben-test")  # ben is an Admin in the fixture
    assert read_settings(api, pipe)["manager_id"] == "cpo"


def test_the_task_type_route_sets_and_clears_the_manager_under_the_same_check(api):
    pipe = make_pipeline(api, "Typed")
    assert post(api, f"task-types/{pipe}", {"manager": "bot:cpo"})["type"]["manager"] == "cpo"
    assert post(api, f"task-types/{pipe}", {"manager": ""})["type"]["manager"] in (None, "")


def test_the_task_type_route_refuses_an_inactive_manager_and_says_why(api):
    pipe = make_pipeline(api, "Typed refusals")
    post(api, f"task-types/{pipe}", {"manager": "bot:cpo"})
    set_bot_state(api, "cpo", "archived")
    refused = post(api, f"task-types/{pipe}", {"manager": "bot:cpo"}, expected=422)["error"]["detail"]
    assert "No active bot" in refused and "fixed by its role" not in refused
    assert get(api, f"task-types/{pipe}")["type"]["manager"] == "cpo"
