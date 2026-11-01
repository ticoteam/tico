"""A bot created without a model takes the company's Team default, else the Gateway's built-in default, and is never
refused for lacking one (backend/settings_admin.py create_bot).

Ana owns the company (the base fixture). Nothing is saved in Settings > Computers until a test saves it.
"""
import json

from backend import gateway_tiers
from backend.tests.test_api import api, headers, post  # noqa: F401  (fixture)


def create(api, slug, model=None, expected=200):
    body = {"slug": slug, "display_name": slug.title(), "description": "x", "status": "planned", "effort": "",
            "harness": None, "runner_id": None}
    if model is not None:
        body["model"] = model
    r = api.post("/api/v2/bots", json=body, headers=headers("ana-test"))
    assert r.status_code == expected, r.text
    return r


def model_of(api, slug):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
    return json.loads(row[0])["model"]


def test_with_no_team_default_a_bot_without_a_model_takes_the_gateway_default(api):
    create(api, "no-model")
    assert model_of(api, "no-model") == gateway_tiers.default_model()


def test_with_a_team_default_a_bot_without_a_model_takes_the_team_default(api):
    r = api.put("/api/v2/providers", json={"enabled": ["openai"], "model": "gpt-6.1-sol", "expected_revision": 0},
                headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    create(api, "team-model")
    assert model_of(api, "team-model") == "gpt-6.1-sol"


def test_a_named_model_that_does_not_exist_is_still_refused(api):
    create(api, "bad-model", model="no-such-model", expected=422)
