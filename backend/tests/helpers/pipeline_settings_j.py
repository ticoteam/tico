"""Pipeline fixtures that more than one test module uses: a pipeline with an owner, and its settings route."""

from backend import hubdb as H
from backend.tests.helpers.api import get, headers


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
