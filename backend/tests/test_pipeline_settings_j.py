"""Pipeline settings and factories (Track J): the owner and admins set a pipeline's goal, judge, visibility, members and
factory; a private pipeline leaves its factory and only its members see it; admins make a factory, its owner or an admin
edits it, and retiring one takes its pipelines out of it.

The fixture's people are ana (owner), ben (an Admin through bot_admins) and cara (a plain member). Only they are known."""

import uuid

from backend import hubdb as H
from backend.tests.helpers.api import get, headers  # noqa: F401  (api is a fixture)
from backend.tests.test_api import api  # noqa: F401  (api is a fixture)


def make_pipeline(api, name, owner="human:ana"):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(
            c, H.KEEPER, name, [{"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]
        )
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (owner, typ["id"]))
    return typ["id"]


def send(api, method, path, body, token="ana-test", expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def settings(api, pipeline, body, token="ana-test", expected=200):
    return send(api, "PATCH", f"pipelines/{pipeline}/settings", body, token, expected)


def read_settings(api, pipeline, token="ana-test"):
    return get(api, f"pipelines/{pipeline}/settings", token)


def seen_on_overview(api, token):
    """The ids of the pipelines this reader sees on the Overview."""
    return {p["id"] for p in get(api, "pipelines/overview?period=7d", token)["pipelines"]}


def test_the_owner_sets_goal_and_judge_and_a_plain_member_changes_nothing(api):
    pipeline = make_pipeline(api, "Launches")

    out = settings(api, pipeline, {"goal": "  Ship launches on time  ", "judge_model": "jev"})
    assert out["goal"] == "Ship launches on time" and out["judge_model"] == "jev"
    read = read_settings(api, pipeline)
    assert read["goal"] == out["goal"] and read["judge_models"] == ["haiku-5.5", "jev", "sonnet"]
    assert read["default_judge_model"] == "haiku-5.5"

    # A field left out stays as it is.
    settings(api, pipeline, {"goal": "Keep going"})
    assert read_settings(api, pipeline)["judge_model"] == "jev"

    # An empty goal clears it, and a null judge model takes the default back.
    out = settings(api, pipeline, {"goal": "", "judge_model": None})
    assert out["goal"] is None and out["judge_model"] is None

    settings(api, pipeline, {"goal": "Nope"}, token="cara-test", expected=403)
    assert read_settings(api, pipeline)["goal"] is None


def test_a_judge_model_must_be_one_the_judge_offers_and_a_long_goal_is_refused(api):
    pipeline = make_pipeline(api, "Judged")
    settings(api, pipeline, {"judge_model": "gpt-9"}, expected=422)
    settings(api, pipeline, {"goal": "x" * 201}, expected=422)
    assert read_settings(api, pipeline)["judge_model"] is None


def test_a_private_pipeline_leaves_its_factory_and_refuses_one(api):
    factory = send(api, "POST", "factories", {"name": "Growth"})["id"]
    pipeline = make_pipeline(api, "Legal")
    settings(api, pipeline, {"factory_id": factory})
    assert read_settings(api, pipeline)["factory_id"] == factory

    out = settings(api, pipeline, {"visibility": "members"})
    assert out["visibility"] == "members" and out["factory_id"] is None  # D61: going private clears the factory

    settings(api, pipeline, {"factory_id": factory}, expected=422)
    settings(api, pipeline, {"factory_id": "fac-missing"}, expected=422)
    settings(api, pipeline, {"factory_id": factory, "visibility": "company"})  # public again, so it may join
    assert read_settings(api, pipeline)["factory_id"] == factory


def test_members_replace_the_list_and_only_a_member_sees_a_private_pipeline(api):
    pipeline = make_pipeline(api, "Counsel")
    settings(api, pipeline, {"visibility": "members", "members": ["person:cara", "bot:counsel-bot"]})
    assert read_settings(api, pipeline)["members"] == ["bot:counsel-bot", "human:cara"]
    settings(api, pipeline, {"members": ["cara"]}, expected=422)  # a bare name is not an actor reference
    assert read_settings(api, pipeline)["members"] == ["bot:counsel-bot", "human:cara"]

    # The owner sees it; cara sees it while she is a member.
    assert pipeline in seen_on_overview(api, "ana-test")
    assert pipeline in seen_on_overview(api, "cara-test")
    assert read_settings(api, pipeline, token="cara-test")["visibility"] == "members"

    # Replacing the list without her takes her out: she no longer sees it, and its settings are a 404 to her.
    settings(api, pipeline, {"members": ["bot:counsel-bot"]})
    assert pipeline not in seen_on_overview(api, "cara-test")
    get(api, f"pipelines/{pipeline}/settings", token="cara-test", expected=404)

    settings(api, pipeline, {"members": []})
    assert read_settings(api, pipeline)["members"] == []


def test_factories_are_made_by_admins_and_edited_by_their_owner(api):
    name = "Growth " + uuid.uuid4().hex[:6]
    send(api, "POST", "factories", {"name": name}, token="cara-test", expected=403)
    made = send(api, "POST", "factories", {"name": name, "goal": "Find customers", "owner_id": "human:cara"})
    assert made["name"] == name and made["owner_id"] == "human:cara" and made["retired_at"] is None
    send(api, "POST", "factories", {"name": name.upper()}, expected=409)  # names are unique among live factories
    send(api, "POST", "factories", {"name": "Bots", "manager_id": "human:cara"}, expected=422)

    edited = send(
        api, "PATCH", f"factories/{made['id']}", {"goal": "", "manager_id": "bot:growth-factory"}, token="cara-test"
    )
    assert (
        edited["goal"] is None and edited["manager_id"] == "bot:growth-factory" and edited["owner_id"] == "human:cara"
    )
    send(api, "PATCH", f"factories/{made['id']}", {"name": ""}, token="cara-test", expected=422)
    assert made["id"] in {f["id"] for f in get(api, "factories")["factories"]}

    # A factory with no owner is changed by Admins alone: cara may not, ben (an Admin) may.
    unowned = send(api, "POST", "factories", {"name": "Unowned " + uuid.uuid4().hex[:6]})["id"]
    send(api, "PATCH", f"factories/{unowned}", {"goal": "Mine now"}, token="cara-test", expected=403)
    assert send(api, "PATCH", f"factories/{unowned}", {"goal": "Ben's now"}, token="ben-test")["goal"] == "Ben's now"


def test_retiring_a_factory_takes_its_pipelines_out_of_it(api):
    factory = send(api, "POST", "factories", {"name": "Success " + uuid.uuid4().hex[:6]})["id"]
    first, second = make_pipeline(api, "Onboarding"), make_pipeline(api, "Renewals")
    settings(api, first, {"factory_id": factory})
    settings(api, second, {"factory_id": factory})

    send(api, "POST", f"factories/{factory}/retire", {}, token="cara-test", expected=403)
    out = send(api, "POST", f"factories/{factory}/retire", {})
    assert out["retired_at"] is not None and out["detached"] == 2
    assert factory not in {f["id"] for f in get(api, "factories")["factories"]}
    assert read_settings(api, first)["factory_id"] is None
    assert read_settings(api, second)["factory_id"] is None
    settings(api, first, {"factory_id": factory}, expected=422)  # a retired factory is not live
    send(api, "PATCH", f"factories/{factory}", {"goal": "Too late"}, expected=404)
