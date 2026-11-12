"""A bot's read-only view of the app (backend/app_view.py): off until an owner or admin turns it on for that bot; the
bot opens it from inside a run and reads as itself; every write is refused and changes nothing; it ends when the setting
goes off, when it expires and when the run ends; each turn and each view is in the audit log."""

import pytest

from backend import app_view
from backend.store import H, Problem
from backend.tests.helpers.api import expire, get, headers, post, setup_attempt


def bearer(token):
    return {"Authorization": "Bearer " + token}


def turned_on(api, bot="ops", token="ana-test"):
    return post(api, f"bots/{bot}/app-view", {"on": True}, token=token)


def opened(api, attempt):
    return post(api, "app-view", {}, token=attempt["token"])


def test_off_by_default_and_only_an_owner_or_admin_turns_it_on(api):
    _, _, attempt = setup_attempt(api)
    assert get(api, "bots/ops/app-view")["on"] is False
    refused = api.post("/api/v2/app-view", json={}, headers=headers(attempt["token"]))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "app_view_off"
    # A member, and the bot itself, may not turn it on.
    assert api.post("/api/v2/bots/ops/app-view", json={"on": True}, headers=headers("cara-test")).status_code == 403
    assert (
        api.post("/api/v2/bots/ops/app-view", json={"on": True}, headers=headers(attempt["token"])).status_code == 403
    )
    assert get(api, "bots/ops/app-view")["on"] is False
    # An admin may.
    assert turned_on(api, token="ben-test")["turned_on_by"] == "human:ben"
    assert get(api, "bots/ops/app-view")["on"] is True


def test_the_view_reads_as_the_bot_and_sees_only_what_it_already_reads(api):
    _, _, attempt = setup_attempt(api)
    turned_on(api)
    view = opened(api, attempt)
    assert view["token"].startswith("tico_av_") and view["read_only"] is True
    me = api.get("/api/v2/me", headers=bearer(view["token"]))
    assert me.status_code == 200 and me.json()["actor"].startswith("bot:")
    page = api.get("/", headers=bearer(view["token"]))
    assert page.status_code == 200 and "<html" in page.text
    # A private task of a person's is as unreadable through the view as with the bot's own token.
    private = post(api, "tasks", {"title": "Ana's own", "body": "Mine.", "owner": "human:ana", "private": True})
    own = api.get(f"/api/v2/tasks/{private['id']}", headers=bearer(attempt["token"])).status_code
    seen = api.get(f"/api/v2/tasks/{private['id']}", headers=bearer(view["token"])).status_code
    assert own == seen and seen in (403, 404)


def test_every_write_is_refused_and_changes_nothing(api):
    _, _, attempt = setup_attempt(api)
    turned_on(api)
    token = opened(api, attempt)["token"]
    task = post(api, "tasks", {"title": "Leave me alone", "body": "Keep.", "owner": "human:ana"})
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    for method, path, body in (
        ("POST", "/api/v2/tasks", {"title": "Written from a view"}),
        ("PATCH", f"/api/v2/tasks/{task['id']}", {"title": "Renamed from a view"}),
        ("POST", f"/api/v2/tasks/{task['id']}/comments", {"body": "Said from a view"}),
        ("DELETE", f"/api/v2/tasks/{task['id']}", None),
        ("POST", "/api/v2/updates/read", {}),
        ("POST", "/api/v2/app-view", {}),
    ):
        r = api.request(method, path, json=body, headers=headers(token))
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "read_only_view"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before
        assert c.execute("SELECT title FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == "Leave me alone"


def test_reads_that_show_a_token_or_credential_are_refused(api):
    _, _, attempt = setup_attempt(api)
    turned_on(api)
    token = opened(api, attempt)["token"]
    for path in (
        "/api/v2/credential-runtime",
        "/api/v2/me/tokens",
        "/api/v2/bots/ops/repository-read-token",
        f"/api/v2/attempts/{attempt['id']}/capture",
    ):
        r = api.get(path, headers=bearer(token))
        assert r.status_code == 403 and r.json()["error"]["code"] == "read_only_view", (path, r.text)
    # The gate reads the address as sent: an encoded or dotted segment is not read past.
    for path in ("/api/v2/tasks/%2e%2e/credential-runtime", "/api/v2/tasks/../me/tokens", "/ui/%2e%2e/scim/v2/Users"):
        with pytest.raises(Problem):
            app_view.gate(path, "GET")
    app_view.gate("/api/v2/tasks", "GET")
    app_view.gate("/", "GET")


def test_the_view_ends_when_the_setting_goes_off_when_it_expires_and_when_the_run_ends(api):
    _, _, attempt = setup_attempt(api)
    turned_on(api)
    token = opened(api, attempt)["token"]
    post(api, "bots/ops/app-view", {"on": False})
    assert api.get("/api/v2/me", headers=bearer(token)).status_code == 401
    # Turned on again, the old view stays ended; a new one works until its time.
    turned_on(api)
    assert api.get("/api/v2/me", headers=bearer(token)).status_code == 401
    view = opened(api, attempt)
    assert api.get("/api/v2/me", headers=bearer(view["token"])).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE app_views SET expires_at=? WHERE id=?", (H.shift(H.now(), seconds=-1), view["id"]))
    assert api.get("/api/v2/me", headers=bearer(view["token"])).status_code == 401
    # The run that opened it ends: so does the view.
    view = opened(api, attempt)
    expire(api, attempt["id"])
    assert api.get("/api/v2/me", headers=bearer(view["token"])).status_code in (401, 409)


def test_each_turn_and_each_view_is_in_the_audit_log(api):
    _, _, attempt = setup_attempt(api)
    turned_on(api)
    view = opened(api, attempt)
    post(api, "bots/ops/app-view", {"on": False})
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, "ops")["id"]
        rows = c.execute(
            "SELECT actor, before, after FROM audit_log WHERE object=? ORDER BY created_at, rowid", ("app_view:" + bot,)
        ).fetchall()
    assert [r["actor"] for r in rows] == ["human:ana", "bot:" + bot, "human:ana"]
    assert view["id"] in rows[1]["after"] and view["token"] not in "".join(str(tuple(r)) for r in rows)


def test_the_page_starts_for_a_view_and_offers_it_nothing_that_writes(api):
    _, _, attempt = setup_attempt(api)
    # Without a view, the page's own start-up reads stay a person's.
    assert api.get("/api/me", headers=bearer(attempt["token"])).status_code == 403
    turned_on(api)
    token = opened(api, attempt)["token"]
    me = api.get("/api/me", headers=bearer(token))
    assert me.status_code == 200, me.text
    me = me.json()
    assert me["read_only_view"] is True and me["name"] and me["role"] == "viewer"
    assert not any(me[key] for key in ("bot_admin", "can_create_bots", "can_see_sql", "credential_access", "mover"))
    for path in ("/api/employees", "/api/humans", "/api/status", "/api/v2/needs-you"):
        assert api.get(path, headers=bearer(token)).status_code == 200, path
