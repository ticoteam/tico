"""The hosted owner's sign-in link (TICO_AUTH_PROXY=owner-link): single use, a week unused at most, replaced by our
team's command, only on an https address of a hosted install, and never shown or logged beyond the link itself."""

import logging
import secrets

import pytest
from fastapi.testclient import TestClient

from backend import identity_proxy, owner_link
from backend.auth import LOGOUT_PATH
from backend.config import Settings
from backend.store import H, digest
from backend.tests.test_api import api  # noqa: F401  (the api fixture)

URL = "https://acme.hosted.example"
OWNER = "ana@acme.example"
ASK = "Ask Tico for a new sign-in link."


def new_token():
    return secrets.token_urlsafe(32)


@pytest.fixture
def hosted(api):  # noqa: F811
    """The api fixture's server, switched to the owner link on an https address of a hosted install."""
    auth = api.app.state.auth
    settings = auth.settings
    settings.auth_proxy, settings.public_url, settings.hosted, settings.owner_email = "owner-link", URL, "own", OWNER
    identity_proxy.check(settings)
    auth.proxy = identity_proxy.build(settings, auth.store)
    # The session cookie is Secure, so the browser has to be on https to send it back.
    browser = TestClient(api.app, base_url="https://testserver")

    def place(token, **kw):
        with auth.store.transaction() as c:
            return owner_link.place(c, digest(token), OWNER, **kw)

    def press(token, client=None, **headers):
        return (client or browser).post(owner_link.LINK_PATH, data={"token": token}, headers=headers,
                                        follow_redirects=False)

    def me(client=None):
        return (client or browser).get("/api/v2/me")

    class Hosted:
        pass
    h = Hosted()
    h.api, h.auth, h.settings, h.browser, h.place, h.press, h.me = api, auth, settings, browser, place, press, me
    return h


def test_the_link_shows_one_button_and_pressing_it_signs_the_owner_in_with_a_secure_session(hosted):
    token = new_token()
    hosted.place(token)
    # The link is LINK_PATH#token: the browser asks for the page alone, and its script fills the form from the fragment.
    page = hosted.browser.get(owner_link.LINK_PATH)
    assert page.status_code == 200 and 'method="post"' in page.text and "disabled>Sign in</button>" in page.text
    assert f'<script src="{owner_link.SCRIPT_PATH}"></script>' in page.text and OWNER in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "same-origin"
    script = hosted.browser.get(owner_link.SCRIPT_PATH)
    assert script.status_code == 200 and script.headers["content-type"].startswith("text/javascript")
    assert "location.hash" in script.text and "history.replaceState" in script.text
    # A secret in the query, where logs in front of the server would keep it, is not taken.
    assert hosted.browser.get(owner_link.LINK_PATH, params={"token": token}).status_code == 400
    # Opening the page spends nothing: a mail scanner that fetches the link leaves it working.
    signed = hosted.press(token)
    assert signed.status_code == 303 and signed.headers["location"] == "/"
    cookie = signed.headers["set-cookie"]
    assert cookie.startswith("__Host-tico_session=") and "Secure" in cookie and "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower() and "Path=/" in cookie and token not in cookie
    who = hosted.me()
    assert who.status_code == 200 and who.json()["email"] == OWNER
    # The table keeps hashes only.
    with hosted.auth.store.read() as c:
        stored = " ".join(" ".join(str(v) for v in tuple(r)) for r in c.execute("SELECT * FROM owner_links"))
        sessions = " ".join(str(r[0]) for r in c.execute("SELECT id_hash FROM oidc_sessions"))
    assert token not in stored and digest(token) in stored
    assert cookie.split(";")[0].split("=", 1)[1] not in sessions


def test_a_link_works_once_and_a_second_press_gets_the_ask_for_a_new_link_page(hosted):
    token = new_token()
    hosted.place(token)
    assert hosted.press(token).status_code == 303
    other = TestClient(hosted.api.app, base_url="https://testserver")
    again = hosted.press(token, client=other)
    assert again.status_code == 410 and ASK in again.text and "set-cookie" not in again.headers
    assert token not in again.text
    assert hosted.me(other).status_code == 401


def test_a_link_unused_for_a_week_is_refused(hosted):
    token = new_token()
    hosted.place(token)
    with hosted.auth.store.read() as c:
        expires = c.execute("SELECT expires_at FROM owner_links").fetchone()[0]
    created = H.now()
    assert H.shift(created, days=6, hours=23) < expires <= H.shift(created, days=7)
    with hosted.auth.store.transaction() as c:
        c.execute("UPDATE owner_links SET expires_at=?", (H.shift(H.now(), seconds=-1),))
    late = hosted.press(token)
    assert late.status_code == 410 and ASK in late.text


def test_a_new_link_kills_the_old_unused_one_and_sign_out_ends_the_owners_sessions(hosted):
    first, second, third = new_token(), new_token(), new_token()
    hosted.place(first)
    hosted.place(second)
    hosted.place(second)                                           # a retried command keeps the link it placed
    assert hosted.press(first).status_code == 410                 # replaced before it was used
    assert hosted.press(second).status_code == 303 and hosted.me().status_code == 200
    hosted.place(third)                                            # without --sign-out the session stays
    assert hosted.me().status_code == 200
    hosted.place(new_token(), sign_out=True)
    assert hosted.me().status_code == 401
    assert hosted.press(third).status_code == 410


def test_the_replace_command_puts_a_new_link_in_place(hosted, monkeypatch, capsys):
    old, new = new_token(), new_token()
    hosted.place(old)
    assert hosted.press(old).status_code == 303
    db = str(hosted.settings.db_path)
    monkeypatch.setenv("TICO_REGISTRY_DIR", str(hosted.settings.registry_dir))
    monkeypatch.setenv("TICO_AUTH_PROXY", "owner-link")
    assert owner_link.main(["replace", db, digest(new), "--sign-out"]) == 0
    said = capsys.readouterr()
    assert "signed out everywhere" in said.out and new not in said.out + said.err
    assert hosted.me().status_code == 401 and hosted.press(new).status_code == 303
    assert owner_link.main(["replace", db, "not-a-hash"]) == 2
    monkeypatch.setenv("TICO_AUTH_PROXY", "oidc")
    assert owner_link.main(["replace", db, digest(new_token())]) == 2


def test_the_first_start_places_the_installers_link_once(hosted):
    token, later = new_token(), new_token()
    hosted.settings.owner_link_hash = digest(token)
    hosted.auth.proxy.warm()
    hosted.settings.owner_link_hash = digest(later)
    hosted.auth.proxy.warm()                                       # a restart keeps the link it has
    assert hosted.press(later).status_code == 410
    assert hosted.press(token).status_code == 303


def test_wrong_links_are_rate_limited_per_address(hosted):
    token = new_token()
    hosted.place(token)
    for _ in range(owner_link.FAILURES_PER_WINDOW):
        assert hosted.press(new_token()).status_code == 410
    blocked = hosted.press(token)
    assert blocked.status_code == 429 and "set-cookie" not in blocked.headers
    hosted.auth.proxy.failures = owner_link.Failures()             # the window passed
    assert hosted.press(token).status_code == 303


def test_failures_forget_an_address_after_the_window():
    now = [0.0]
    failures = owner_link.Failures(limit=2, window=60, clock=lambda: now[0])
    failures.note("1.2.3.4"), failures.note("1.2.3.4")
    assert failures.blocked("1.2.3.4") and not failures.blocked("5.6.7.8")
    now[0] = 61
    assert not failures.blocked("1.2.3.4")


def test_a_press_from_another_site_is_refused(hosted):
    token = new_token()
    hosted.place(token)
    assert hosted.press(token, origin="https://evil.example").status_code == 403
    assert hosted.press(token, origin=URL).status_code == 303


def test_the_token_is_never_logged_by_the_server(hosted, caplog):
    token = new_token()
    hosted.place(token)
    with caplog.at_level(logging.DEBUG):
        hosted.browser.get(owner_link.LINK_PATH)
        hosted.press(token)
        hosted.press(token)
        hosted.press(token + "x")
    # httpx is the test's own browser; everything else is the server.
    server = [r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]
    assert server is not None and not any(token in line for line in server)


def test_a_browser_without_a_session_is_sent_to_the_link_page_and_the_api_names_it(hosted):
    page = hosted.browser.get("/tasks", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"] == owner_link.LINK_PATH
    landing = hosted.browser.get(owner_link.LINK_PATH)
    assert landing.status_code == 200 and "Open the sign-in link Tico sent you" in landing.text
    assert "disabled>Sign in" in landing.text                    # no fragment, nothing to press
    refused = hosted.me()
    assert refused.status_code == 401 and refused.json()["error"]["sign_in"] == owner_link.LINK_PATH
    signed_out = hosted.browser.get(owner_link.LINK_PATH, params={"signed_out": "1"})
    assert ASK in signed_out.text


def test_the_apps_next_is_taken_and_the_owner_comes_back_there_but_a_query_secret_is_still_refused(hosted):
    # ui/app/session.js sends a lapsed or first-401 browser to sign_in + "?next=" + where it was.
    here = "/?a=1#/tasks"
    landing = hosted.browser.get(owner_link.LINK_PATH, params={"next": here})
    assert landing.status_code == 200 and "Open the sign-in link Tico sent you" in landing.text
    assert '<input type="hidden" name="next" value="/?a=1#/tasks">' in landing.text
    token = new_token()
    for query in ({"next": here, "token": token}, {"token": token}, {"next": here, "t": token},
                  {"signed_out": "1", "token": token}):
        refused = hosted.browser.get(owner_link.LINK_PATH, params=query)
        assert refused.status_code == 400 and token not in refused.text
    hosted.place(token)
    signed = hosted.browser.post(owner_link.LINK_PATH, data={"token": token, "next": here}, follow_redirects=False)
    assert signed.status_code == 303 and signed.headers["location"] == here


@pytest.mark.parametrize("next_", ["//evil.example/x", "https://evil.example/", "/\\evil.example",
                                   "/ok\r\nSet-Cookie: x=1", "/auth/owner-link"])
def test_a_next_that_leaves_the_site_or_the_path_goes_home_instead(hosted, next_):
    page = hosted.browser.get(owner_link.LINK_PATH, params={"next": next_})
    assert page.status_code == 200 and 'name="next"' not in page.text
    token = new_token()
    hosted.place(token)
    signed = hosted.browser.post(owner_link.LINK_PATH, data={"token": token, "next": next_}, follow_redirects=False)
    assert signed.status_code == 303 and signed.headers["location"] == "/"


def test_sign_out_ends_the_session_and_lands_on_the_link_page(hosted):
    token = new_token()
    hosted.place(token)
    hosted.press(token)
    out = hosted.browser.get(LOGOUT_PATH, follow_redirects=False)
    assert out.status_code == 302 and out.headers["location"] == owner_link.LINK_PATH + "?signed_out=1"
    assert hosted.me().status_code == 401


def test_the_link_page_is_not_there_on_any_other_server(api):  # noqa: F811
    assert api.get(owner_link.LINK_PATH).status_code == 404 and api.get(owner_link.SCRIPT_PATH).status_code == 404
    assert api.post(owner_link.LINK_PATH, data={"token": new_token()}).status_code == 404


@pytest.mark.parametrize("change, wanted", [
    ({"public_url": "http://acme.hosted.example"}, "needs an https address"),
    ({"public_url": "https://127.0.0.1:8765"}, "needs an https address"),
    ({"public_url": "https://203.0.113.7"}, "not an IP address"),
    ({"public_url": "https://203.0.113.7:8443"}, "not an IP address"),
    ({"public_url": "https://[2001:db8::7]"}, "not an IP address"),
    ({"public_url": "https://[2001:db8::7]:8443"}, "not an IP address"),
    ({"public_url": "https://[::ffff:203.0.113.7]"}, "not an IP address"),
    ({"hosted": ""}, "set TICO_HOSTED to own or invited"),
    ({"owner_link_hash": "abc"}, "64-character sha256"),
    ({"owner_email": ""}, "needs TICO_OWNER_EMAIL"),
])
def test_the_server_refuses_to_start_with_the_owner_link_anywhere_but_a_hosted_https_address(tmp_path, change, wanted):
    good = {"db_path": tmp_path / "hub.db", "auth_proxy": "owner-link", "public_url": URL, "hosted": "invited",
            "owner_email": OWNER, "owner_link_hash": digest(new_token())}
    Settings(**good)
    with pytest.raises(RuntimeError, match=wanted):
        Settings(**{**good, **change})


def test_an_unknown_hosted_mark_stops_the_server(tmp_path):
    with pytest.raises(ValueError, match="TICO_HOSTED is own or invited"):
        Settings(db_path=tmp_path / "hub.db", hosted="team")
    assert Settings(db_path=tmp_path / "hub.db").hosted == ""
