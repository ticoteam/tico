from backend.tests.helpers.api import claim, expire, get, post, setup_attempt
from backend.tests.test_api import api


def test_drain_finishes_current_run_but_does_not_claim_more_work(api):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "current"}, r["token"])
    post(api, "bots/ops/control", {"action": "drain", "expected_revision": 1})
    post(api, "chat/ops", {"text": "Saved while draining"})
    post(api, f"attempts/{a['id']}/complete", {"outcome": "completed", "last_seq": 0}, r["token"])
    assert claim(api, r) is None
    post(api, "bots/ops/control", {"action": "resume", "expected_revision": 2})
    assert claim(api, r)


def test_pause_fences_current_credential_even_after_resume(api):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "current"}, r["token"])
    post(api, "bots/ops/control", {"action": "pause", "expected_revision": 1})
    get(api, "me", a["token"], expected=409)
    post(api, "bots/ops/control", {"action": "resume", "expected_revision": 2})
    get(api, "me", a["token"], expected=409)
    assert claim(api, r) is None  # Interrupted external effects still require reconciliation.
