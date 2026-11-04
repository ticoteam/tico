"""runner_versions.load reuses one fleet map per read snapshot."""

from backend.tests.test_api import api, post, runner  # noqa: F401
from backend import runner_versions


def test_load_reuses_the_map_within_one_read_snapshot(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    for r, release in ((a, "0.3.0"), (b, "0.2.5")):
        post(api, "runners/heartbeat",
             {"version": "test", "platform": "test", "readiness": {"ops": True},
              "release": release, "kind": "mac"}, token=r["token"])
    with api.app.state.store.read_transaction() as c:
        first = runner_versions.load(c)
        assert first is runner_versions.load(c)
        assert first[a["runner_id"]]["release"] == "0.3.0"
        assert first[b["runner_id"]]["release"] == "0.2.5"
