"""Who may act for a mailbox once a Google Workspace or a person's grant covers it (plan v3 A/B, v4 O/P/Q/R, v5 T/V).

A covered mailbox has one owner: the computer hosting its message bot (Workspace) or the computer holding the
person's grant. A publisher is refused there, as is a cloud computer that owns nothing. Keys are generated here and
Google's token endpoint is replaced, so nothing leaves the test.
"""

import json

import pytest

from backend import google_connect, google_rights
from backend import google_workspace as workspace
from backend.store import H
from backend.tests.helpers import google_connect as connect
from backend.tests.helpers.api import get, post, ready, runner  # noqa: F401
from backend.tests.helpers.mail_api import seed_org
from backend.tests.helpers.mail_targets import declare
from backend.tests.helpers.google_workspace_authz import (
    BOX,
    PERSONAL,
    health,
    place,
    publish,
    revoke,
    token,
    workspace_key,
)


@pytest.fixture
def google(api):
    settings = api.app.state.store.settings
    settings.google_oauth_client_id, settings.google_oauth_client_secret = connect.CLIENT, "tico-secret"
    return api


def unplace(api, bot="inbox"):
    """The bot has no computer: its assignment is gone."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM assignments WHERE bot=?", (bot,))


def stored_error(api, service="connector:mail"):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT last_error FROM service_health WHERE service=?", (service,)).fetchone()
    return row["last_error"] if row else "no row"


def age_reports(api, machine, minutes=120, account=None):
    """This computer's reports, as if they were `minutes` old: its heartbeat, or only the row of one account."""
    stamp = H.shift(H.now(), minutes=-minutes)
    with api.app.state.store.transaction() as c:
        if account is None:
            c.execute("UPDATE connector_reports SET reported_at=? WHERE runner_id=?", (stamp, machine["runner_id"]))
        else:
            c.execute(
                "UPDATE connector_reports SET reported_at=? WHERE runner_id=? AND account=?",
                (stamp, machine["runner_id"], account),
            )


def connect_ana(api, monkeypatch, refresh_token="1//ana"):
    """Ana connects her own mailbox (her message bot's, BOX) with Connect Google; later refreshes answer `ya29.ana`."""
    connect.signed_in(api, "ana-test", email=BOX, refresh_token=refresh_token)
    google = connect.FakeGoogle(
        {"access_token": "ya29.ana", "expires_in": 3599, "scope": connect.oauth.G + "gmail.readonly"}
    )
    monkeypatch.setattr(google_connect, "post_token", google)
    return google


def test_a_workspace_mailbox_is_read_by_the_computer_running_its_bot(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")  # a cloud computer: not a publisher
    office = runner(api, label="Office Mac")  # the owner's: a publisher, but not the bot's
    place(api, bot_mac)
    publish(api, bot_mac["token"])
    assert publish(api, office["token"], expected=403)["error"]["kind"] == "workspace_owned"
    assert [b["address"] for b in get(api, "connectors/mail/targets", bot_mac["token"])["mailboxes"]] == [BOX]
    assert get(api, "connectors/mail/targets", office["token"])["mailboxes"] == []


def test_the_token_route_answers_only_the_bot_s_computer(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    answer = token(api, bot_mac["token"])
    assert answer["access_token"] == "ya29.workspace" and answer["scopes"]
    assert workspace_key[-1]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert token(api, office["token"], expected=403)["error"]["kind"] == "workspace_owned"
    assert token(api, bot_mac["token"], "ben@acme.example", expected=403)["error"]["kind"] == "not_this_mailbox"
    assert token(api, bot_mac["token"], "lena@other.example", expected=403)["error"]["kind"] == "not_connected"


def test_a_personal_grant_wins_over_the_workspace_key_and_only_its_owner_gets_its_token(
    api, workspace_key, google, monkeypatch
):
    seed_org(api)
    declare(api, BOX)
    office = runner(api, label="Office Mac")
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    place(api, bot_mac)
    refreshes = connect_ana(api, monkeypatch)
    minted = len(workspace_key)
    answer = token(api, bot_mac["token"])
    assert answer["access_token"] == "ya29.ana" and answer["scopes"] == [connect.oauth.G + "gmail.readonly"]
    assert refreshes.calls[-1]["refresh_token"] == "1//ana" and len(workspace_key) == minted  # never the key
    assert "1//ana" not in json.dumps(answer)
    assert token(api, office["token"], expected=403)["error"]["kind"] == "workspace_owned"
    assert publish(api, bot_mac["token"])
    assert publish(api, office["token"], expected=403)["error"]["kind"] == "personal_grant"


def test_a_failing_key_keeps_each_mailbox_with_its_owner(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE google_workspaces SET status='failing', last_error='Google refused the key' WHERE id='wsk-1'")
    publish(api, bot_mac["token"])
    assert publish(api, office["token"], expected=403)["error"]["kind"] == "workspace_owned"


def test_a_moved_bot_hands_over_its_mail_and_the_old_computer_finishes_its_lease(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    old = runner(api, operator="ben", label="Old Mac")
    new = runner(api, operator="ben", label="New Mac")
    place(api, old)
    publish(api, old["token"])
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,"
            "created_at,attempts,claimed_by,claimed_at) VALUES('lease1','auto_x',NULL,?,'m1','draft_reply','{}',"
            "'claimed',?,1,?,?)",
            (BOX, H.now(), old["runner_id"], H.now()),
        )
    place(api, new)  # the bot moves
    publish(api, new["token"])
    assert publish(api, old["token"], expected=403)["error"]["kind"] == "workspace_owned"
    post(api, "automations/mail-actions/lease1/result", {"ok": True, "detail": "sent"}, old["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id='lease1'").fetchone()[0] == "done"
    post(api, "automations/mail-actions/claim", {"mailbox": BOX}, old["token"], expected=403)


def test_health_is_kept_per_computer_and_account(api):
    seed_org(api)
    declare(api, BOX)
    first = runner(api, label="Office Mac")
    second = runner(api, label="Second Mac")
    health(api, first["token"], failing=[(BOX, "error")])
    assert "can't refresh for " + BOX in stored_error(api)
    health(api, second["token"], ok=[BOX])
    assert stored_error(api) is not None  # the first computer still fails it
    health(api, second["token"], ok=[BOX])  # a heartbeat changes nothing
    assert stored_error(api) is not None
    health(api, first["token"], ok=[BOX])
    assert stored_error(api) is None


def test_an_older_runner_without_ok_accounts_still_recovers_when_nobody_fails(api):
    seed_org(api)
    declare(api, BOX)
    first = runner(api, label="Office Mac")
    second = runner(api, label="Second Mac")
    health(api, first["token"], failing=[(BOX, "error")], ok=None)
    health(api, second["token"], ok=None)  # a clean report without ok_accounts
    assert stored_error(api) is not None
    health(api, first["token"], ok=None)
    assert stored_error(api) is None


def test_a_computer_reports_only_the_accounts_it_may_act_for(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    assert health(api, office["token"], failing=[(BOX, "error")], expected=403)["error"]["kind"] == "workspace_owned"
    assert health(api, office["token"], ok=[BOX], expected=403)["error"]["kind"] == "workspace_owned"


def test_a_computer_gone_quiet_still_fails_the_accounts_it_owns(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    age_reports(api, bot_mac)  # Bot Mac has not reported for two hours
    health(api, office["token"])  # any computer's report recomputes the health
    assert "Bot Mac has not reported since" in stored_error(api)
    place(api, office)  # the bot moves and the new computer reads the account
    health(api, office["token"], ok=[BOX])
    assert stored_error(api) is None  # the quiet computer no longer owns it


def test_a_fresh_computer_still_fails_an_account_whose_row_is_old(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    age_reports(api, bot_mac, account=BOX)  # the account was not read lately; the computer reports
    health(api, bot_mac["token"], ok=[])
    assert "can't refresh for " + BOX in stored_error(api)
    with api.app.state.store.read() as c:
        assert BOX in google_rights.failing_accounts(c, "mail", H.now())


def test_a_personal_mailbox_moves_with_its_bot_and_needs_no_reconnect(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    old = runner(api, operator="ben", label="Old Mac")
    new = runner(api, operator="ben", label="New Mac")
    place(api, old)
    connect_ana(api, monkeypatch)
    assert get(api, "runners/connectors", old["token"])["connectors"] == ["mail", "calendar"]
    assert token(api, old["token"])["access_token"] == "ya29.ana"
    place(api, new)  # the bot moves: its new computer reads the mailbox, with the same grant on the server
    assert token(api, new["token"])["access_token"] == "ya29.ana"
    assert token(api, old["token"], expected=403)["error"]["kind"] == "not_this_mailbox"
    assert get(api, "runners/connectors", old["token"])["connectors"] == []


def test_a_personal_mailbox_without_a_bot_computer_goes_to_its_person_s_own_online_computer(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    mine = runner(api, label="Ana's Mac")  # Ana's own computer
    others = runner(api, operator="ben", label="Ben's Mac")
    ready(api, mine, [])
    ready(api, others, [])
    connect_ana(api, monkeypatch)
    assert publish(api, mine["token"])
    assert publish(api, others["token"], expected=403)["error"]["kind"] == "personal_grant"


def test_a_revoked_computers_fresh_failure_does_not_count(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    assert stored_error(api) is not None
    revoke(api, bot_mac)  # its report is still fresh, but it is revoked
    health(api, office["token"])  # any computer's report recomputes the health
    assert stored_error(api) is None
