"""The public contract names an attempt a run (backend/runs.py); the runner's wire keeps the storage names."""
import json

from backend import runs
from backend.status_values import ATTEMPT_STATES


def test_every_attempt_state_has_its_own_run_state():
    assert set(runs.STATE) == set(ATTEMPT_STATES)
    assert runs.state("expired") != runs.state("interrupted")
    assert runs.state(None) is None
    assert set(runs.LIVE) <= set(runs.STATES)


def test_public_json_renames_keys_not_values():
    body = json.dumps({"attempt_id": "a1", "rows": [{"attempt_id": "a2"}],
                       "refs": json.dumps({"attempt_id": "a3"}), "note": "attempt_id"}).encode()
    out = json.loads(runs.public_json(body, "/api/v2/jobs"))
    assert out["run_id"] == "a1" and out["rows"] == [{"run_id": "a2"}] and "attempt_id" not in out
    # A JSON string inside a value is escaped, so its key is left as it was stored.
    assert json.loads(out["refs"]) == {"attempt_id": "a3"} and out["note"] == "attempt_id"
    assert runs.public_json('{"attempt_id": "a1"}') == '{"run_id": "a1"}'


def test_runner_wire_keeps_storage_names():
    body = b'{"attempt_id": "a1"}'
    for path in ("/api/v2/jobs/claim", "/api/v2/attempts/a1/complete", "/api/v2/attempts/a1/inputs/m1/ack",
                 "/api/v2/runners/heartbeat", "/api/v2/runners/assignments", "/api/v2/runner-auth/refresh"):
        assert runs.public_json(body, path) is body, path
    for path in ("/api/v2/jobs", "/api/v2/runs", "/api/v2/runners", "/api/v2/bots/ops/stop"):
        assert runs.public_json(body, path) == b'{"run_id": "a1"}', path
