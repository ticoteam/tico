"""Operations listing loads every computer's signed-out and key-cleared rows in one pass."""

from backend.tests.test_api import api, runner  # noqa: F401
from backend import model_login


def test_cleared_and_key_cleared_batch_match_per_runner(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by) "
                  "VALUES(?,?,?,?,?)", (a["runner_id"], "codex", "", "2026-01-01T00:00:00Z", "human:ana"))
        c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
                  "VALUES(?,?,?,?,?,1)", (b["runner_id"], "claude", "", "2026-01-01T00:00:00Z", "human:ana"))
    with api.app.state.store.read() as c:
        by_cleared = model_login.cleared_by_runner(c)
        by_key = model_login.key_cleared_by_runner(c)
        assert by_cleared[a["runner_id"]] == model_login.cleared_logins(c, a["runner_id"])
        assert by_cleared[b["runner_id"]] == model_login.cleared_logins(c, b["runner_id"])
        assert by_key.get(a["runner_id"], []) == model_login.key_cleared_logins(c, a["runner_id"])
        assert by_key[b["runner_id"]] == model_login.key_cleared_logins(c, b["runner_id"])
        assert by_cleared[a["runner_id"]] == {"codex": [""]}
        assert by_key[b["runner_id"]] == ["claude"]
