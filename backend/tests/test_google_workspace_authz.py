"""Who may act for a mailbox once a Google Workspace or a person's grant covers it (plan v3 A/B, v4 O/P/Q/R, v5 T/V).

A covered mailbox has one owner: the computer hosting its message bot (Workspace) or the computer holding the
person's grant. A publisher is refused there, as is a cloud computer that owns nothing. Keys are generated here and
Google's token endpoint is replaced, so nothing leaves the test.
"""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import google_workspace as workspace
from backend.store import H
from backend.tests import test_google_connect as connect
from backend.tests.test_api import api, get, post, runner  # noqa: F401
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
