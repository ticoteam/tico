"""Who may act for a mailbox once a Google Workspace or a person's grant covers it (plan v3 A/B, v4 O/P/Q/R, v5 T/V).

A covered mailbox has one owner: the computer hosting its message bot (Workspace) or the computer holding the
person's grant. A publisher is refused there, as is a cloud computer that owns nothing. Keys are generated here and
Google's token endpoint is replaced, so nothing leaves the test.
"""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import google_connect, google_rights
from backend import google_workspace as workspace
from backend.store import H
from backend.tests import test_google_connect as connect
from backend.tests.test_api import api, get, post, ready, runner  # noqa: F401
from backend.tests.test_mail_api import seed_org
from backend.tests.test_mail_targets import declare

BOX = "ana@acme.example"
PERSONAL = "cara@acme.example"


@pytest.fixture
def google(api):
    settings = api.app.state.store.settings
    settings.google_oauth_client_id, settings.google_oauth_client_secret = connect.CLIENT, "tico-secret"
    return api


@pytest.fixture
def workspace_key(api, monkeypatch):
    """A connected Workspace key for acme.example, sealed by the server's vault. Token mints go to a fake Google."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    creds = {"client_email": "tico-sync@tico-test.example", "client_id": "100", "private_key": key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}
    now = H.now()
    with api.app.state.store.transaction() as c:
        sealed = workspace._cipher().seal(c, "google-workspace:wsk-1", json.dumps(creds))
        c.execute("INSERT INTO google_workspaces(id,domain,project_id,client_email,client_id,key_id,key_sealed,revision,"
                  "status,checked_at,connected_at,last_error,probe_mailbox,created_by,created_at,updated_at,replaces_id,"
                  "generation,mint_failing_since) VALUES('wsk-1','acme.example','tico-test',?,'100','k1',?,1,'connected',"
                  "?,?,NULL,NULL,'ana',?,?,NULL,1,NULL)", (creds["client_email"], sealed, now, now, now, now))
    calls = []

    def google_call(url, *, data=None, token=None):
        calls.append(dict(data or {}))
        return 200, {"access_token": "ya29.workspace", "expires_in": 3599}

    monkeypatch.setattr(workspace, "google_call", google_call)
    return calls


def place(api, machine, bot="inbox"):
    """The message bot runs on this computer (its assignment)."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO assignments(bot,runner_id,generation,updated_at,updated_by) "
                  "VALUES(?,?,0,?,'ana')", (bot, machine["runner_id"], H.now()))


def unplace(api, bot="inbox"):
    """The bot has no computer: its assignment is gone."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM assignments WHERE bot=?", (bot,))


def revoke(api, machine):
    """The computer is revoked: it is no host and no holder any more."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), machine["runner_id"]))


def publish(api, token, mailbox=BOX, expected=200):
    return post(api, "connectors/mail/messages", {"mailbox": mailbox, "messages": [], "deleted": [],
                                                   "synced_at": H.now()}, token, expected=expected)


def token(api, token_, mailbox=BOX, service="gmail", expected=200):
    return post(api, "connectors/mail/workspace/token", {"mailbox": mailbox, "service": service}, token_,
                expected=expected)


def health(api, token_, failing=(), ok=None, service="mail", expected=200):
    body = {"service": service, "failing": [{"account": a, "reason": r} for a, r in failing]}
    if ok is not None:
        body["ok_accounts"] = list(ok)
    return post(api, "connectors/health", body, token_, expected=expected)


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
            c.execute("UPDATE connector_reports SET reported_at=? WHERE runner_id=? AND account=?",
                      (stamp, machine["runner_id"], account))


def begin_ana(api):
    """Ana starts Connect Google, and Google sends her back with a code."""
    _, query = connect.start(api, "ana-test")
    connect.back(api, "ana-test", state=query["state"], code="c-1")


def finish_ana(api, machine, monkeypatch, refresh_token):
    """`machine` takes the connect Ana started, exchanges its code for a grant with `refresh_token`, and answers it."""
    claimed = post(api, "connectors/mail/google/claim", {"holds_mail": True}, machine["token"])["connect"]
    assert claimed and claimed["action"] == "connect"
    monkeypatch.setattr(google_connect, "post_token", connect.FakeGoogle(
        {"access_token": "ya29.ana", "expires_in": 3599, "refresh_token": refresh_token}))
    post(api, f"connectors/mail/google/{claimed['id']}/exchange", {}, machine["token"])
    post(api, f"connectors/mail/google/{claimed['id']}/result", {"status": "connected", "can": ["read"]},
         machine["token"])


def test_a_workspace_mailbox_is_read_by_the_computer_running_its_bot(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")      # a cloud computer: not a publisher
    office = runner(api, label="Office Mac")                    # the owner's: a publisher, but not the bot's
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


def test_a_personal_grant_is_never_minted_by_the_workspace_key(api, workspace_key, google):
    seed_org(api)
    office = runner(api, label="Office Mac")
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    place(api, bot_mac)
    connect.exchanged(api, office)                              # Cara's grant, held by the office computer
    assert token(api, office["token"], PERSONAL, expected=403)["error"]["kind"] == "personal_grant"
    assert token(api, bot_mac["token"], PERSONAL, expected=403)["error"]["kind"] == "personal_grant"
    assert publish(api, office["token"], PERSONAL)                  # the holder reads it
    assert publish(api, bot_mac["token"], PERSONAL, expected=403)["error"]["kind"] == "personal_grant"


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
        c.execute("INSERT INTO automation_mail_actions(id,automation_id,run_id,mailbox,msg_id,action,args,state,"
                  "created_at,attempts,claimed_by,claimed_at) VALUES('lease1','auto_x',NULL,?,'m1','draft_reply','{}',"
                  "'claimed',?,1,?,?)", (BOX, H.now(), old["runner_id"], H.now()))
    place(api, new)                                             # the bot moves
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
    assert stored_error(api) is not None                        # the first computer still fails it
    health(api, second["token"], ok=[BOX])                       # a heartbeat changes nothing
    assert stored_error(api) is not None
    health(api, first["token"], ok=[BOX])
    assert stored_error(api) is None


def test_an_older_runner_without_ok_accounts_still_recovers_when_nobody_fails(api):
    seed_org(api)
    declare(api, BOX)
    first = runner(api, label="Office Mac")
    second = runner(api, label="Second Mac")
    health(api, first["token"], failing=[(BOX, "error")], ok=None)
    health(api, second["token"], ok=None)                       # a clean report without ok_accounts
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
    age_reports(api, bot_mac)                                    # Bot Mac has not reported for two hours
    health(api, office["token"])                                 # any computer's report recomputes the health
    assert "Bot Mac has not reported since" in stored_error(api)
    place(api, office)                                           # the bot moves and the new computer reads the account
    health(api, office["token"], ok=[BOX])
    assert stored_error(api) is None                             # the quiet computer no longer owns it


def test_a_fresh_computer_still_fails_an_account_whose_row_is_old(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    age_reports(api, bot_mac, account=BOX)                       # the account was not read lately; the computer reports
    health(api, bot_mac["token"], ok=[])
    assert "can't refresh for " + BOX in stored_error(api)
    with api.app.state.store.read() as c:
        assert BOX in google_rights.failing_accounts(c, "mail", H.now())


def test_a_holder_whose_bot_moved_away_cannot_refresh_its_grant(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    old = runner(api, operator="ben", label="Old Mac")          # not a publisher
    new = runner(api, operator="ben", label="New Mac")
    place(api, old)
    ready(api, old, [])
    begin_ana(api)
    finish_ana(api, old, monkeypatch, "1//grant")
    google_calls = connect.FakeGoogle()
    monkeypatch.setattr(google_connect, "post_token", google_calls)
    assert connect.refresh(api, old["token"], mailbox=BOX).status_code == 200
    place(api, new)                                             # the bot moves; the grant stays on the old computer
    r = connect.refresh(api, old["token"], mailbox=BOX)
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_this_grant"
    assert len(google_calls.calls) == 1                         # only the refresh before the move reached Google


def test_a_reconnect_moves_the_grant_to_the_bot_s_computer_and_the_old_holder_keeps_nothing(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    old = runner(api, operator="ben", label="Old Mac")
    new = runner(api, operator="ben", label="New Mac")
    place(api, old)
    ready(api, old, [])
    begin_ana(api)
    finish_ana(api, old, monkeypatch, "1//first")
    place(api, new)                                             # the bot moves; Ana reconnects
    ready(api, new, [])
    begin_ana(api)
    assert post(api, "connectors/mail/google/claim", {"holds_mail": True}, old["token"])["connect"] is None
    assert get(api, "runners/connectors", old["token"])["connectors"] == []
    finish_ana(api, new, monkeypatch, "1//second")                 # the new computer takes and finishes it
    monkeypatch.setattr(google_connect, "post_token", connect.FakeGoogle())
    assert connect.refresh(api, new["token"], mailbox=BOX, refresh_token="1//second").status_code == 200
    r = connect.refresh(api, old["token"], mailbox=BOX, refresh_token="1//first")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_this_grant"
    # Once the grant is the new computer's, the old one is neither holder, publisher nor bot host: the claim gate refuses it.
    refused = post(api, "connectors/mail/google/claim", {"holds_mail": True}, old["token"], expected=403)
    assert refused["error"]["code"] == "forbidden"
    assert get(api, "runners/connectors", old["token"])["connectors"] == []


def test_a_first_connect_waits_for_the_bot_s_computer_even_while_it_is_offline(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    office = runner(api, label="Office Mac")                    # the owner's: a publisher
    bot_mac = runner(api, operator="ben", label="Bot Mac")      # the bot's computer, not yet online
    place(api, bot_mac)
    begin_ana(api)
    assert post(api, "connectors/mail/google/claim", {"holds_mail": True}, office["token"])["connect"] is None
    ready(api, bot_mac, [])                                     # the bot's computer comes online and takes the connect
    finish_ana(api, bot_mac, monkeypatch, "1//host")
    assert publish(api, bot_mac["token"], expected=200)         # the bot's computer owns the grant it connected
    assert publish(api, office["token"], expected=403)["error"]["kind"] == "personal_grant"


def test_a_holder_with_no_bot_computer_refreshes_and_reconnects_its_grant(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    holder = runner(api, operator="ben", label="Bot Mac")       # not a publisher: it took the connect as the bot's host
    place(api, holder)
    ready(api, holder, [])
    begin_ana(api)
    finish_ana(api, holder, monkeypatch, "1//first")
    unplace(api)                                                # the bot has no live computer now
    monkeypatch.setattr(google_connect, "post_token", connect.FakeGoogle())
    assert connect.refresh(api, holder["token"], mailbox=BOX, refresh_token="1//first").status_code == 200
    assert publish(api, holder["token"], expected=200)          # the holder still owns the grant it holds
    begin_ana(api)
    finish_ana(api, holder, monkeypatch, "1//second")           # with no bot computer, the holder takes the reconnect
    assert connect.refresh(api, holder["token"], mailbox=BOX, refresh_token="1//second").status_code == 200
    r = connect.refresh(api, holder["token"], mailbox=BOX, refresh_token="1//first")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_this_grant"


def test_a_revoked_bot_computer_is_no_host_so_a_publisher_takes_the_first_connect(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    office = runner(api, label="Office Mac")                    # a publisher
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    place(api, bot_mac)
    ready(api, bot_mac, [])
    revoke(api, bot_mac)                                        # the bot's computer is removed: no live host
    begin_ana(api)
    finish_ana(api, office, monkeypatch, "1//office")           # the publisher takes the first connect
    assert publish(api, office["token"], expected=200)          # and the publisher owns the grant it holds


def test_a_publisher_without_mail_takes_a_first_connect_beside_a_non_publisher_grant(api, google, monkeypatch):
    seed_org(api)
    declare(api, BOX)
    holder = runner(api, operator="ben", label="Bot Mac")       # not a publisher: it hosts Ana's bot and holds her grant
    place(api, holder)
    ready(api, holder, [])
    begin_ana(api)
    finish_ana(api, holder, monkeypatch, "1//ana")
    _, query = connect.start(api, "cara-test")                  # Cara's first connect: no bot computer, no holder
    connect.back(api, "cara-test", state=query["state"], code="c-2")
    office = runner(api, label="Office Mac")                    # a publisher with no mail credentials
    claimed = post(api, "connectors/mail/google/claim", {"holds_mail": False}, office["token"])["connect"]
    assert claimed and claimed["id"] == "cara" and claimed["action"] == "connect" and claimed["mailbox"] == PERSONAL


def test_a_revoked_computers_fresh_failure_does_not_count(api, workspace_key):
    seed_org(api)
    declare(api, BOX)
    bot_mac = runner(api, operator="ben", label="Bot Mac")
    office = runner(api, label="Office Mac")
    place(api, bot_mac)
    health(api, bot_mac["token"], failing=[(BOX, "error")])
    assert stored_error(api) is not None
    revoke(api, bot_mac)                                        # its report is still fresh, but it is revoked
    health(api, office["token"])                                # any computer's report recomputes the health
    assert stored_error(api) is None
