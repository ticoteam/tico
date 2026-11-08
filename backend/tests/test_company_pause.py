"""The company-wide pause: one Hub record, who may pause and resume, and a refusal that changes nothing.

Synthetic fixture team only (backend/tests/test_api.py): ana is the owner, ben an admin, cara a member.
"""

from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def _record_rows(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM registry_metadata WHERE key='company_pause'").fetchone()[0]


def _bot_state(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state FROM bots WHERE id=?", (bot,)).fetchone()[0]


def _status_line(api, bot, token="ana-test"):
    """The bot's status as the pages read it (GET /api/v2/status?bot=)."""
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, bot, state="idle")
    return get(api, "status?bot=" + bot, token)["status"]


def test_the_owner_pauses_every_bot_once_and_resume_lifts_the_one_record(api):
    before = get(api, "company/pause")
    assert before["paused"] is False and before["can_control"] is True
    assert _record_rows(api) == 0

    paused = post(api, "company/pause", {})
    assert paused["changed"] is True
    assert paused["state"]["paused"] is True
    assert paused["state"]["paused_by"]["name"] and paused["state"]["paused_by"]["at"].endswith("Z")
    assert _record_rows(api) == 1

    # Every bot on the team reads paused in Hub, with the words why.
    line = _status_line(api, "ops")
    assert line["state"] == "paused" and "paused by" in line["focus"] and "Resume" in line["focus"]

    # An admin pausing again changes nothing: the first pause stays on record, and there is still one record.
    again = post(api, "company/pause", {}, token="ben-test")
    assert again["changed"] is False
    assert again["state"]["paused_by"] == paused["state"]["paused_by"]
    assert _record_rows(api) == 1

    resumed = post(api, "company/resume", {})
    assert resumed["changed"] is True and resumed["state"]["paused"] is False
    assert _record_rows(api) == 0
    assert _status_line(api, "ops")["state"] != "paused"

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
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")

    post(api, "company/pause", {})
    assert get(api, "company/pause")["bots_paused_on_own"] == 1

    post(api, "company/resume", {})
    assert _bot_state(api, "ops") == "paused"
    assert get(api, "company/pause")["bots_paused_on_own"] == 1

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE id='ops'")


def test_an_earlier_chat_message_that_says_stop_creates_no_record_and_stops_no_bot(api):
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
