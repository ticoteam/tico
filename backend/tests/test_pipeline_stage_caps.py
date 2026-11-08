"""A stage's card cap (D68, D69): the pipeline's owner sets it per stage through the stage route, a blank cap is no cap, a
cap-0 stage is "On ice", and a cap is never read from or written by anything else. A move into a full stage is refused for
a bot and allowed for a person with a warning; a card that cannot enter waits at its stage."""

import uuid

from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend.tests.test_pipeline_members_only import make_pipeline


def _stage_cap(api, typ, name):
    steps = get(api, f"task-types/{typ}")["type"]["steps"]
    return next(s for s in steps if s["name"] == name)["card_cap"]


def test_the_owner_sets_a_stage_cap_through_the_stage_route_and_a_blank_cap_is_no_cap(api):
    typ, steps = make_pipeline(api, "Caps" + uuid.uuid4().hex[:8])
    stage = "/api/v2/pipeline-stages/" + steps["Doing"]

    assert _stage_cap(api, typ, "Doing") is None  # a new stage has no cap

    r = api.patch(stage, json={"card_cap": 3}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert _stage_cap(api, typ, "Doing") == 3

    api.patch(stage, json={"limit_minutes": 60}, headers=headers("ana-test"))  # the Warn after leaves the cap alone
    assert _stage_cap(api, typ, "Doing") == 3

    api.patch(stage, json={"card_cap": 0}, headers=headers("ana-test"))  # 0 is "On ice", not no cap
    assert _stage_cap(api, typ, "Doing") == 0

    api.patch(stage, json={"card_cap": None}, headers=headers("ana-test"))  # blank in the editor sends null
    assert _stage_cap(api, typ, "Doing") is None


def test_a_cap_is_only_for_the_pipelines_owner_and_admins_and_must_be_a_whole_number_from_zero(api):
    typ, steps = make_pipeline(api, "Caps" + uuid.uuid4().hex[:8])
    stage = "/api/v2/pipeline-stages/" + steps["Doing"]

    assert api.patch(stage, json={"card_cap": 2}, headers=headers("cara-test")).status_code == 403
    assert _stage_cap(api, typ, "Doing") is None

    assert api.patch(stage, json={"card_cap": -1}, headers=headers("ana-test")).status_code == 422
    assert api.patch(stage, json={"card_cap": 2.5}, headers=headers("ana-test")).status_code == 422
    assert _stage_cap(api, typ, "Doing") is None
