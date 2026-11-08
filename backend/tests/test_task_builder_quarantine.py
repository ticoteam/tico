"""Task badges consume the shared status timing, without exposing refused private words."""
import pytest

from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401


def status(api, token="ana-test"):
    return next(row for row in get(api, "status", token)["bots"] if row["bot"] == "ops")


@pytest.mark.parametrize("since,deadline,label", [
    ("2030-10-26T12:00:00Z", "2030-10-26T13:00:00.000000Z", "13:00 UTC"),
    ("2030-10-26T23:55:00Z", "2030-10-27T00:55:00.000000Z", "2030-10-27 00:55 UTC"),
])
def test_status_supplies_safe_builder_timing_and_removes_it_on_lift(api, since, deadline, label):
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "10 repeated refusals today",
                     refusal={"rule": "identity", "detail": "Private task words"})
        c.execute("UPDATE events SET created_at=? WHERE action='quarantine' AND target='bot:ops'", (since,))
    manager = status(api)["quarantine"]
    reader = status(api, "cara-test")["quarantine"]
    assert manager["resumes_at"] == reader["resumes_at"] == deadline
    assert manager["builder_notice"] == reader["builder_notice"] == "Builder quarantined: resumes " + label
    assert "Private task words" not in reader["builder_notice"]
    assert deadline[:16].replace("T", " ") + " UTC" in reader["why"]
    with api.app.state.store.transaction() as c:
        H.status_set(c, "human:ana", "ops", state="active", reason="Checked")
    assert "quarantine" not in status(api)
    assert "quarantine" not in status(api, "cara-test")


def test_escape_builder_notice_promises_no_lift_and_discloses_no_private_context(api):
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "escape: private refusal",
                     context={"what": "task note", "found": "secrets/", "preview": "Private task words"})
    for token in ("ana-test", "cara-test"):
        q = status(api, token)["quarantine"]
        assert not q["auto"] and q["resumes_at"] is None
        assert q["builder_notice"] == "Builder quarantined: until a person clears it"


def test_status_survives_unreadable_quarantine_start_with_known_deadline(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "10 repeated refusals today")
    monkeypatch.setattr(H, "quarantine_resumes", lambda c, slug: {
        "auto": True, "since": "unreadable", "resumes_at": "2030-10-26T13:00:00Z"})
    q = status(api, "cara-test")["quarantine"]
    assert q["builder_notice"] == "Builder quarantined: resumes 13:00 UTC"
    assert "2030-10-26 13:00 UTC" in q["why"]
