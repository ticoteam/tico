"""POST /api/v2/pipelines/{id} makes a pipeline only under an id of its own shape: the 0.5 paths that now fall into it
(/overview, /setup) answer 404 and make nothing, with a name or without."""

import pytest

from backend.tests.helpers.sample_company import booted, environment, rows, signed_in  # noqa: F401

RECORD = {"name": "Orders", "prefix": "OR", "stages": [{"name": "Take", "owner": "assignee", "requirements": []}]}


@pytest.mark.parametrize("path", ["overview", "setup"])
@pytest.mark.parametrize("body", [{}, RECORD], ids=["no-name", "named"])
def test_an_old_path_is_not_a_new_pipeline(environment, path, body):
    api = environment()
    before = rows(api, "SELECT id FROM pipelines ORDER BY id")
    r = api.post("/api/v2/pipelines/" + path, json=body, headers=signed_in())
    assert r.status_code == 404, r.text
    assert rows(api, "SELECT id FROM pipelines ORDER BY id") == before


def test_a_new_id_of_its_own_shape_still_makes_a_pipeline(environment):
    api = environment()
    r = api.post("/api/v2/pipelines/orders", json=RECORD, headers=signed_in())
    assert r.status_code == 200, r.text
    assert api.post("/api/v2/pipelines/Not An Id", json=RECORD, headers=signed_in()).status_code == 404
