"""Settings > Google > What Tico may do (backend/google_workspace.py `permissions`): the company's own say over what
Tico does in Google beyond reading. Organize mail and Send mail need gmail.modify, Change calendar needs calendar
events; reading is always on. Connect Google with the company's own app asks only for what is ticked, a refresh and a
Workspace mint are narrowed to it on the next token, and Send mail off stops every bot's sends whatever its own switch
says. Google is never asked (`post_token` and `google_call` replaced); keys are throwaway and the domain acme.example."""

import json

import jwt
import pytest

from backend import google_connect
from backend import google_workspace as W
from backend.auth import Identity
from backend.tests.test_api import api, get, headers, runner  # noqa: F401
from backend.tests.test_google_connect import FakeGoogle, app, exchanged, own_app, refresh, start  # noqa: F401
from backend.tests.test_google_workspace import (  # noqa: F401  (fixtures)
    CALENDAR, GMAIL, MAILBOX, check, connected, covered, fresh_state, google, google_view, keys, private, rows)
from backend.store import Problem
from connectors.mail import oauth

PERMISSIONS = "/api/v2/google/permissions"
ALL_ON = {"organize_mail": True, "send_mail": True, "change_calendar": True}
G = oauth.G


def allow(api, token="ana-test", **changes):
    return api.put(PERMISSIONS, json={**ALL_ON, **changes}, headers=headers(token))


def test_everything_is_on_until_an_admin_acting_as_themselves_turns_it_off(api):
    assert get(api, "google", "cara-test")["permissions"] == ALL_ON
    assert allow(api, "cara-test", send_mail=False).status_code == 403              # not an admin
    settings = api.app.state.store.settings
    settings.test_identities["ana-assistant"] = Identity("human:ana", "human", "ana@acme.example", via="assistant")
    assert allow(api, "ana-assistant", send_mail=False).status_code == 403          # nobody acting for the admin
    assert api.put(PERMISSIONS, json={"send_mail": False}, headers=headers()).status_code == 422   # all three named
    r = allow(api, organize_mail=False, send_mail=False)
    assert r.status_code == 200, r.text
    off = {"organize_mail": False, "send_mail": False, "change_calendar": True}
    assert r.json()["permissions"] == off
    assert get(api, "google", "cara-test")["permissions"] == off
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='google.permissions.set'").fetchone()[0] == 1


def test_a_stored_legacy_attachment_switch_does_not_restrict_sending(app):
    machine = runner(app)
    with app.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value", (W.PERMISSIONS_KEY, json.dumps({**ALL_ON, "send_attachments": False})))
    assert get(app, "google", "cara-test")["permissions"] == ALL_ON
    assert get(app, "connectors/mail/targets", machine["token"])["company"] == ALL_ON
    changed = app.post("/api/v2/bots/inbox/mail-settings", json={"outbound_send": True}, headers=headers())
    assert changed.status_code == 200, changed.text
    own = get(app, "bots/inbox/mail-settings", "inbox-test")
    assert own["company_send"] is True and own["outbound_send"] is True
    assert "company_attachments" not in own


def test_scopes_follow_what_tico_may_do():
    send_only = {"organize_mail": False, "send_mail": True, "change_calendar": False}
    nothing = dict.fromkeys(ALL_ON, False)
    assert google_connect.grantable("settings") == google_connect.READ_SCOPES + google_connect.ACT_SCOPES
    assert google_connect.grantable("settings", send_only) == google_connect.READ_SCOPES + [G + "gmail.modify"]
    assert google_connect.grantable("settings", {**nothing, "change_calendar": True}) == (
        google_connect.READ_SCOPES + [G + "calendar.events"])
    assert google_connect.grantable("settings", nothing) == google_connect.READ_SCOPES
    assert google_connect.grantable("env", ALL_ON) == google_connect.READ_SCOPES       # the shared app stays read only
    held = json.dumps(google_connect.READ_SCOPES + google_connect.ACT_SCOPES)
    assert google_connect.refresh_scopes("gmail", held, nothing) == {G + "gmail.readonly"}
    assert google_connect.refresh_scopes("gmail", held, send_only) == {G + "gmail.readonly", G + "gmail.modify"}
    assert G + "calendar.events" not in google_connect.refresh_scopes("calendar", held, send_only)
    assert W.workspace_scopes("gmail", nothing) == [G + "gmail.readonly"]
    assert W.workspace_scopes("calendar", nothing) == [G + "calendar.readonly"]
    assert W.workspace_scopes("gmail", send_only) == [GMAIL] and W.workspace_scopes("calendar") == [CALENDAR]
    # The admin authorizes the read-only scopes too: delegation takes only scopes named in the Admin console.
    assert W.scope_urls() == sorted([GMAIL, CALENDAR, G + "gmail.readonly", G + "calendar.readonly"])


def test_the_company_s_own_app_asks_only_for_what_is_ticked(own_app):
    assert allow(own_app, organize_mail=False, send_mail=False).status_code == 200
    _, query = start(own_app)
    assert query["scope"].split() == google_connect.READ_SCOPES + [G + "calendar.events"]
    assert get(own_app, "mail/google", "cara-test")["scopes"] == query["scope"].split()


def test_a_box_turned_off_narrows_the_next_refresh_and_turned_on_offers_a_reconnect(own_app, monkeypatch):
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(google_connect.READ_SCOPES + [G + "gmail.modify"]))
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": True, "calendar": False}
    assert seen["message"] == google_connect.UPGRADE["calendar"][0]
    # Change calendar off: the missing calendar write is not offered, since Tico would not use it.
    assert allow(own_app, change_calendar=False).status_code == 200
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["message"] is None and seen["acts"] == {"mail": True, "calendar": False}
    # Organize and Send off too: the grant keeps gmail.modify, the token leaves it out, and the card says so.
    assert allow(own_app, organize_mail=False, send_mail=False, change_calendar=False).status_code == 200
    assert get(own_app, "mail/google", "cara-test")["acts"] == {"mail": False, "calendar": False}
    google = FakeGoogle({"access_token": "ya29.read", "expires_in": 3599, "scope": G + "gmail.readonly"})
    monkeypatch.setattr(google_connect, "post_token", google)
    r = refresh(own_app, machine["token"], scopes=[G + "gmail.modify", G + "gmail.readonly"])
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == [G + "gmail.readonly"] and google.calls[-1]["scope"] == G + "gmail.readonly"
    count = len(google.calls)
    assert refresh(own_app, machine["token"], scopes=[G + "gmail.modify"]).status_code == 422   # nothing left to ask
    assert len(google.calls) == count
    # Back on: the grant still holds gmail.modify, so the next refresh carries it with no new sign-in.
    assert allow(own_app, change_calendar=False).status_code == 200
    google.answer = {"access_token": "ya29.mail", "expires_in": 3599, "scope": G + "gmail.modify " + G + "gmail.readonly"}
    r = refresh(own_app, machine["token"], scopes=[G + "gmail.modify", G + "gmail.readonly"])
    assert r.json()["scopes"] == [G + "gmail.modify", G + "gmail.readonly"]
    # Change calendar on again, on a grant without calendar events: the reconnect hint comes back.
    assert allow(own_app).status_code == 200
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["message"] == google_connect.UPGRADE["calendar"][0]
    assert seen["hint"] == google_connect.UPGRADE["calendar"][1]


def test_one_missing_write_with_the_other_unused_names_only_what_a_reconnect_adds(own_app):
    machine = runner(own_app)
    exchanged(own_app, machine, scope=" ".join(google_connect.READ_SCOPES + [G + "calendar.events"]))
    assert allow(own_app, change_calendar=False).status_code == 200
    seen = get(own_app, "mail/google", "cara-test")
    assert seen["acts"] == {"mail": False, "calendar": False}
    assert seen["message"] == google_connect.UPGRADE["mail_only"][0] == "This connection only reads your mail."


def test_a_workspace_token_is_minted_with_only_what_is_ticked(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    _, calls = google
    connected(api, private)

    def minted(service):
        with api.app.state.store.read() as c:
            answer = W.mint(c, MAILBOX, service)
        last = [data for url, data, _ in calls if url == W.TOKEN_URI][-1]
        sent = jwt.decode(last["assertion"], options={"verify_signature": False})
        return answer["scopes"], sent["scope"]

    W.forget_tokens()                                   # the check's probes minted both; start from Google again
    assert minted("gmail") == ([GMAIL], GMAIL) and minted("calendar") == ([CALENDAR], CALENDAR)
    assert allow(api, organize_mail=False, send_mail=False, change_calendar=False).status_code == 200
    # The cached full tokens are not handed out: the scopes are part of the cache key.
    assert minted("gmail") == ([G + "gmail.readonly"], G + "gmail.readonly")
    assert minted("calendar") == ([G + "calendar.readonly"], G + "calendar.readonly")
    assert allow(api, organize_mail=False).status_code == 200                       # Send alone needs gmail.modify
    assert minted("gmail")[0] == [GMAIL]


def test_send_mail_off_stops_every_bot_whatever_its_own_switch(app):
    r = app.post("/api/v2/bots/inbox/mail-settings", json={"outbound_send": True}, headers=headers())
    assert r.status_code == 200, r.text
    own = get(app, "bots/inbox/mail-settings", "inbox-test")
    assert own["outbound_send"] is True and own["company_send"] is True
    assert own["company_organize"] is True and own["company_calendar"] is True
    assert allow(app, organize_mail=False, change_calendar=False).status_code == 200
    own = get(app, "bots/inbox/mail-settings", "inbox-test")
    # The connector gates labels, drafts and calendar writes on these, whichever credential it holds.
    assert (own["company_organize"], own["company_send"], own["company_calendar"]) == (False, True, False)
    assert own["outbound_send"] is True
    assert allow(app, send_mail=False).status_code == 200
    own = get(app, "bots/inbox/mail-settings", "inbox-test")
    assert own["outbound_send"] is False and own["company_send"] is False   # the connector refuses before any approval
    person = get(app, "bots/inbox/mail-settings", "ana-test")
    assert person["outbound_send"] is True and person["company_send"] is False   # the bot's own switch is kept
    assert allow(app).status_code == 200
    assert get(app, "bots/inbox/mail-settings", "inbox-test")["outbound_send"] is True


def test_the_assistant_never_sets_what_tico_may_do():
    from backend import assistant
    assert any(method in (None, "PUT") and pattern.fullmatch(PERMISSIONS) for method, pattern in assistant.NEVER)


def test_a_key_authorized_before_the_read_only_scopes_keeps_minting_what_google_accepts(api, private, covered, google):
    covered((MAILBOX, "inbox-cara"))
    state, _ = google
    state["authorized"] = [GMAIL, CALENDAR]             # step 2 done before Tico listed the read-only scopes
    connected(api, private)                             # Check: the pair is refused, the write scope alone accepted
    (row,) = rows(api)
    assert json.loads(row["authorized_scopes"]) == W.LEGACY_SCOPES
    assert google_view(api)["workspace"]["narrowable"] is False
    assert allow(api, organize_mail=False, send_mail=False, change_calendar=False).status_code == 200
    W.forget_tokens()
    with api.app.state.store.read() as c:
        # Google refuses a read-only scope this key was never authorized for ...
        with pytest.raises(Problem) as refused:
            W.mint(c, MAILBOX, "calendar", scopes=W.READ_ONLY_SCOPES["calendar"])
        assert refused.value.extra["kind"] == "unauthorized_client"
        # ... so the key keeps minting its write scopes, and the mail connector holds What Tico may do.
        assert W.mint(c, MAILBOX, "gmail")["scopes"] == [GMAIL]
        assert W.mint(c, MAILBOX, "calendar")["scopes"] == [CALENDAR]
    with api.app.state.store.transaction() as c:        # a row from before migration 0081: no list at all
        c.execute("UPDATE google_workspaces SET authorized_scopes=NULL")
    W.forget_tokens()
    with api.app.state.store.read() as c:
        assert W.mint(c, MAILBOX, "calendar")["scopes"] == [CALENDAR]
    assert google_view(api)["workspace"]["narrowable"] is False
    state["authorized"] = W.scope_urls()                # the admin redoes step 2, then Check
    assert check(api, {"mailbox": MAILBOX})["ok"] is True
    assert google_view(api)["workspace"]["narrowable"] is True
    W.forget_tokens()
    with api.app.state.store.read() as c:
        assert W.mint(c, MAILBOX, "gmail")["scopes"] == [G + "gmail.readonly"]
        assert W.mint(c, MAILBOX, "calendar")["scopes"] == [G + "calendar.readonly"]


def test_a_key_stored_by_upload_lists_every_scope_tico_shows(api, private, covered, google):
    from backend.tests.test_google_workspace import key_json, new_code, upload
    covered((MAILBOX, "inbox-cara"))
    upload(api, new_code(api), key_json(private))
    (row,) = rows(api)
    assert row["status"] == "pending" and json.loads(row["authorized_scopes"]) == W.scope_urls()
