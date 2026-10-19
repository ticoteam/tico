"""A hosted install on the built-in OIDC sign-in, the way a Tico account issuer is trusted: only the generic settings
(TICO_OIDC_ISSUER / _CLIENT_ID / _CLIENT_SECRET, and TICO_OIDC_LABEL for the button), the owner email as owner, invited
teammates as members, and the owner sign-in link beside it as break-glass for when the issuer is down."""

import dataclasses
import secrets

import pytest
from fastapi.testclient import TestClient

from backend import identity_proxy, oidc, owner_link
from backend.app import create_app
from backend.config import Settings
from backend.store import digest
from backend.tests.test_api import api, post  # noqa: F401  (the api fixture)
from backend.tests.test_oidc import CLIENT, SECRET, provider, signin  # noqa: F401  (the fixtures)

URL = "https://acme.hosted.example"
OWNER = "ana@acme.example"
LABEL = "Tico account"
HTML = {"accept": "text/html"}


@pytest.fixture
def hosted(signin):  # noqa: F811
    """The api fixture's server on an https address of a hosted install, signing in with the fake issuer, with the
    owner's break-glass link from the installer and the button labelled "Tico account"."""
    token = secrets.token_urlsafe(32)
    signin.settings.owner_email = OWNER
    signin.start(public_url=URL, hosted="invited", owner_link_hash=digest(token), oidc_label=LABEL)
    identity_proxy.check(signin.settings)
    signin.browser = TestClient(signin.api.app, base_url="https://testserver")
    signin.token = token
    return signin


def browser_login(h, email, client=None):
    client = client or h.browser
    began = client.get("/auth/login", follow_redirects=False)
    assert began.status_code == 302, began.text
    code, state = h.fake.approve(began.headers["location"], email=email)
    return client.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)


def who(client):
    return client.get("/api/v2/me")


def test_a_signed_out_browser_gets_one_sign_in_with_button_that_goes_to_the_issuer(hosted):
    page = hosted.browser.get("/tasks", headers=HTML, follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"] == oidc.SIGN_IN_PATH + "?next=%2Ftasks"
    shown = hosted.browser.get(page.headers["location"])
    assert shown.status_code == 200 and "Sign in with Tico account</a>" in shown.text
    assert 'href="/auth/login?next=%2Ftasks"' in shown.text and shown.headers["cache-control"] == "no-store"
    # The UI's fetch layer follows the same page.
    assert who(hosted.browser).json()["error"]["sign_in"] == oidc.SIGN_IN_PATH
    # The button starts the ordinary OIDC round trip at the issuer.
    began = hosted.browser.get("/auth/login", params={"next": "/tasks"}, follow_redirects=False)
    assert began.status_code == 302 and began.headers["location"].startswith(hosted.fake.issuer + "/authorize?")


def test_the_owner_email_signs_in_as_owner_and_an_invited_teammate_as_a_member(hosted):
    done = browser_login(hosted, OWNER)
    assert done.status_code == 302
    me = who(hosted.browser).json()
    assert me["actor"] == "human:ana" and me["email"] == OWNER and me["role"] == "owner"
    teammate = TestClient(hosted.api.app, base_url="https://testserver")
    assert browser_login(hosted, "ben@acme.example", teammate).status_code == 302
    me = who(teammate).json()
    assert me["actor"] == "human:ben" and me["role"] != "owner"


def test_an_account_nobody_invited_is_refused_and_not_added(hosted):
    with hosted.auth.store.read() as c:
        before = c.execute("SELECT count(*) FROM humans").fetchone()[0]
    refused = browser_login(hosted, "zoe@elsewhere.example")
    assert refused.status_code == 403 and "not on" in refused.text and "zoe@elsewhere.example" in refused.text
    with hosted.auth.store.read() as c:
        assert c.execute("SELECT count(*) FROM humans").fetchone()[0] == before
        assert c.execute("SELECT 1 FROM humans WHERE lower(email)='zoe@elsewhere.example'").fetchone() is None
    assert hosted.sessions() == [] and who(hosted.browser).status_code == 401


def test_with_the_issuer_down_from_startup_sessions_keep_working_and_the_owner_link_signs_the_owner_in(hosted):
    # Signed in while the issuer was up.
    assert browser_login(hosted, OWNER).status_code == 302
    owner_cookie = hosted.browser.cookies.get("__Host-tico_session")
    teammate = TestClient(hosted.api.app, base_url="https://testserver")
    assert browser_login(hosted, "ben@acme.example", teammate).status_code == 302
    member_cookie = teammate.cookies.get("__Host-tico_session")
    # The issuer stops, and the server restarts on the same database with the issuer still unreachable.
    hosted.fake.server.shutdown()
    hosted.fake.server.server_close()
    with hosted.auth.store.read() as c:
        assert c.execute("SELECT count(*) FROM owner_links").fetchone()[0] == 0    # no link placed yet
    with TestClient(create_app(dataclasses.replace(hosted.settings)), base_url="https://testserver") as app:
        app.app.state.auth.warm()           # what startup runs: the installer's link goes in although the issuer is down
        for name, value, actor in (("owner", owner_cookie, "human:ana"), ("member", member_cookie, "human:ben")):
            kept = app.get("/api/v2/me", headers={"cookie": "__Host-tico_session=" + value})
            assert kept.status_code == 200 and kept.json()["actor"] == actor, name
        # A new OIDC sign-in can't start, and says so.
        down = app.get("/auth/login", follow_redirects=False)
        assert down.status_code == 502 and "Sign-in is unavailable" in down.text
        # The owner's break-glass link still signs the owner in.
        page = app.get(owner_link.LINK_PATH)
        assert page.status_code == 200 and "disabled>Sign in</button>" in page.text
        pressed = app.post(owner_link.LINK_PATH, data={"token": hosted.token}, follow_redirects=False)
        assert pressed.status_code == 303 and pressed.headers["set-cookie"].startswith("__Host-tico_session=")
        me = app.get("/api/v2/me").json()
        assert me["actor"] == "human:ana" and me["role"] == "owner"
        # Single use, as on an owner-link server.
        app.cookies.clear()
        assert app.post(owner_link.LINK_PATH, data={"token": hosted.token}, follow_redirects=False).status_code == 410


def test_without_a_label_every_bring_your_own_oidc_install_goes_straight_to_the_provider(signin):  # noqa: F811
    page = signin.api.get("/tasks", headers=HTML, follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"] == oidc.LOGIN_PATH + "?next=%2Ftasks"
    assert who(signin.api).json()["error"]["sign_in"] == oidc.LOGIN_PATH
    assert signin.api.get(oidc.SIGN_IN_PATH, follow_redirects=False).headers["location"] == oidc.LOGIN_PATH
    # And no owner link: it is for hosted installs the installer gave one.
    assert signin.api.get(owner_link.LINK_PATH).status_code == 404


def test_the_owner_link_is_off_on_a_hosted_oidc_install_without_one(signin):  # noqa: F811
    signin.settings.owner_email = OWNER
    signin.start(public_url=URL, hosted="invited", owner_link_hash="")
    assert not owner_link.break_glass(signin.settings)
    browser = TestClient(signin.api.app, base_url="https://testserver")
    assert browser.get(owner_link.LINK_PATH).status_code == 404
    assert browser.post(owner_link.LINK_PATH, data={"token": secrets.token_urlsafe(32)}).status_code == 404


def base(tmp_path, **kw):
    return dict(db_path=tmp_path / "hub.db", registry_dir=tmp_path, auth_proxy="oidc", oidc_issuer="https://id.example",
                oidc_client_id=CLIENT, oidc_client_secret=SECRET, public_url=URL, **kw)


@pytest.mark.parametrize("changes, message", [
    ({"oidc_label": "x" * 41}, "TICO_OIDC_LABEL"),
    ({"oidc_label": "Tico <b>"}, "TICO_OIDC_LABEL"),
    ({"hosted": "invited", "owner_email": OWNER, "owner_link_hash": "abc"}, "64-character sha256"),
    ({"hosted": "invited", "owner_link_hash": digest("t" * 40), "public_url": "https://203.0.113.7"}, "not an IP address"),
])
def test_startup_refuses_a_bad_label_or_a_bad_break_glass_link(tmp_path, changes, message):
    Settings(**base(tmp_path, oidc_label=LABEL, hosted="invited", owner_email=OWNER, owner_link_hash=digest("t" * 40)))
    with pytest.raises(RuntimeError, match=message):
        Settings(**{**base(tmp_path), **changes})


def test_the_replace_command_takes_a_hosted_oidc_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TICO_AUTH_PROXY", "oidc")
    monkeypatch.delenv("TICO_HOSTED", raising=False)
    assert owner_link.main(["replace", str(tmp_path / "hub.db"), digest("t" * 40)]) == 2
    assert "does not use the owner sign-in link" in capsys.readouterr().err
    monkeypatch.setenv("TICO_HOSTED", "invited")
    monkeypatch.setenv("TICO_REGISTRY_DIR", str(tmp_path))
    assert owner_link.main(["replace", str(tmp_path / "hub.db"), digest("t" * 40)]) == 0
