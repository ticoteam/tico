"""The company-wide pause: one Hub record, who may pause and resume, and a refusal that changes nothing.

Synthetic fixture team only (backend/tests/test_api.py): ana is the owner, ben an admin, cara a member.
"""

import pytest

from backend import company_pause
from backend.auth import Identity
from backend.store import Problem
from backend.store import H
from backend.tests.helpers.api import get, post  # noqa: F401
from backend.tests.test_api import api  # noqa: F401


def _record_rows(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM registry_metadata WHERE key='company_pause'").fetchone()[0]


def _bot_state(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state FROM bots WHERE id=?", (bot,)).fetchone()[0]


def _set_bot_state(api, bot, state):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state=? WHERE id=?", (state, bot))


def _status_state(api, bot, token="ana-test"):
    """The bot's status row as the pages read it (GET /api/v2/status?bot=)."""
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, bot, state="idle")
    return get(api, "status?bot=" + bot, token)["status"]["state"]


def test_the_owner_pauses_once_and_resume_lifts_the_one_record(api):
    before = get(api, "company/pause")
    assert before["paused"] is False and before["can_control"] is True
    assert _record_rows(api) == 0

    paused = post(api, "company/pause", {})
    assert paused["changed"] is True
    assert paused["state"]["paused"] is True
    assert paused["state"]["paused_by"]["name"] and paused["state"]["paused_by"]["at"].endswith("Z")
    assert _record_rows(api) == 1

    # The status row does not claim a pause that nothing enforces yet: it keeps its own state.
    assert _status_state(api, "ops") == "idle"

    # An admin pausing again changes nothing: the first pause stays on record, and there is still one record.
    again = post(api, "company/pause", {}, token="ben-test")
    assert again["changed"] is False
    assert again["state"]["paused_by"] == paused["state"]["paused_by"]
    assert _record_rows(api) == 1

    resumed = post(api, "company/resume", {})
    assert resumed["changed"] is True and resumed["state"]["paused"] is False
    assert _record_rows(api) == 0

    # Resuming when nothing is paused changes nothing and says so.
    nothing = post(api, "company/resume", {})
    assert nothing["changed"] is False and nothing["state"]["paused"] is False


def test_a_member_is_refused_with_the_reason_and_nothing_changes(api):
    refused = post(api, "company/pause", {}, token="cara-test", expected=403)
    assert "owner or an admin" in str(refused)
    assert _record_rows(api) == 0
    assert get(api, "company/pause", "cara-test")["can_control"] is False

    post(api, "company/pause", {})
    refused = post(api, "company/resume", {}, token="cara-test", expected=403)
    assert "owner or an admin" in str(refused)
    assert get(api, "company/pause")["paused"] is True
    assert _record_rows(api) == 1

    post(api, "company/resume", {})


def test_a_bot_paused_on_its_own_stays_paused_and_the_record_counts_it(api):
    _set_bot_state(api, "ops", "paused")
    try:
        post(api, "company/pause", {})
        assert get(api, "company/pause")["bots_paused_on_own"] == 1

        post(api, "company/resume", {})
        assert _bot_state(api, "ops") == "paused"
        assert get(api, "company/pause")["bots_paused_on_own"] == 1
    finally:
        _set_bot_state(api, "ops", "active")


def test_a_chat_message_that_says_stop_creates_no_record_and_stops_no_bot(api):
    post(api, "chat/cpo", {"text": "stop bot work"})
    assert _record_rows(api) == 0
    assert get(api, "company/pause")["paused"] is False
    assert _bot_state(api, "cpo") != "paused"


def test_the_pause_and_resume_actions_are_audited_once_each(api):
    post(api, "company/pause", {})
    post(api, "company/pause", {})
    post(api, "company/resume", {})
    post(api, "company/resume", {})
    with api.app.state.store.read() as c:
        actions = [
            row[0]
            for row in c.execute(
                "SELECT action FROM events WHERE action IN ('company.pause','company.resume') ORDER BY created_at, rowid"
            )
        ]
    assert actions == ["company.pause", "company.resume"]


@pytest.mark.parametrize(
    "who",
    [
        Identity("bot:ops", "bot"),
        Identity("service:key-1", "service"),
        Identity("human:tico-support", "human", "ana@acme.example", support="app"),
        Identity("human:tico-support", "human", "ana@acme.example", support="full"),
        Identity("human:ana", "owner", "ana@acme.example", support="app"),
    ],
)
def test_bots_service_keys_and_support_sessions_are_refused_and_change_nothing(api, who):
    auth = api.app.state.auth
    assert company_pause.may_control(auth, who) is False
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as refused:
            company_pause.pause(c, auth, who)
    assert refused.value.status == 403 and "owner or an admin" in refused.value.detail
    assert _record_rows(api) == 0
    post(api, "company/pause", {})
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem):
            company_pause.resume(c, auth, who)
    assert _record_rows(api) == 1
    post(api, "company/resume", {})


def test_a_running_bot_row_is_not_changed_by_the_pause(api):
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "ops", state="running")
    post(api, "company/pause", {})
    try:
        assert get(api, "status?bot=ops")["status"]["state"] == "running"
    finally:
        post(api, "company/resume", {})
