"""Page apps (docs/pages.md#apps): a version opts in to reading Tico, is served sealed with Tico's bridge and a key for
its viewer, and every call reads the viewer's rows cut to what the page's writer may also read. Page data is the
page's writers' to keep, and goes with the page."""

import re

import pytest

from backend import page_apps as A, visuals as V
from backend.tests.helpers.api import headers, post  # noqa: F401  (fixtures)
from backend.tests.helpers.files import publish, turn
from backend.tests.helpers.pages import HTML, page, read
from backend.tests.helpers.visuals import VISUALS

KEY = re.compile(rb'<script data-tico-app="([^"]+)">')


@pytest.fixture(autouse=True)
def full(request, monkeypatch):
    if "api" in request.fixturenames:
        monkeypatch.setattr(request.getfixturevalue("api").app.state.store.settings, "visual_origin", VISUALS)


def app_page(api, who="ben-test", reads="tasks,store"):
    _, attempt = turn(api, who=who)
    made = page(api, attempt, app=f"reads={reads}")
    assert made.status_code == 200, made.text
    return attempt, made.json()["page"]


def served(api, fid, who="ben-test"):
    shown = read(api, fid, who).json()["page"]
    return shown, api.get(shown["src"])


def call(api, fid, version, key, op, who="ben-test", **args):
    return api.post(
        f"/api/v2/pages/{fid}/app",
        json={"version": version, "nonce": key, "op": op, "args": args},
        headers=headers(who),
    )


def test_an_app_is_its_own_version_served_sealed_with_a_key_only_while_current(api):
    _, attempt = turn(api)
    plain = page(api, attempt).json()["page"]
    fid = plain["file_id"]
    # The same bytes with access declared are a new version; the same again is not; dropping it is another.
    again = page(api, attempt, app="reads=tasks,store")
    assert again.status_code == 200, again.text
    assert again.json()["page"]["version"] == 2
    assert page(api, attempt, app="reads=store,tasks").json()["page"]["version"] == 2
    assert page(api, attempt, app="reads=calendar").status_code == 422
    shown, r = served(api, fid)
    assert shown["app"] == {"reads": ["store", "tasks"]}
    rule = r.headers["content-security-policy"]
    assert "allow-same-origin" not in rule and rule.startswith("sandbox allow-scripts allow-forms")
    assert r.content.index(b"data-tico-app") < r.content.index(b"<title>")  # first in <head>, before the page's own
    # A plain page keeps its storage; an app is never the shared hosted copy.
    assert page(api, attempt).json()["page"]["version"] == 3
    shown, r = served(api, fid)
    assert "app" not in shown and b"data-tico-app" not in r.content
    assert "allow-same-origin" in r.headers["content-security-policy"]
    # An older app version still opens sealed, without a key.
    settings = api.app.state.store.settings
    key = V.key_for(api.app, settings)
    viewer = V.read(key, shown["src"].rsplit("/", 1)[1], principals=True)[0]
    old = api.get(VISUALS + "/visuals/" + V.mint(key, viewer, fid, 2))
    assert old.status_code == 200 and b"data-tico-app" not in old.content
    assert "allow-same-origin" not in old.headers["content-security-policy"]
    # An app's live tags are never filled for its viewer: it reads only through the bridge, cut to its writer's reach.
    tagged = HTML.replace(b"</body>", b'<tico-tasks waiting-on="viewer"></tico-tasks></body>')
    page(api, attempt, data=tagged, app="reads=tasks")
    _, r = served(api, fid)
    assert b"<tico-tasks" in r.content and b"tico-live" not in r.content


def test_calls_read_the_viewers_rows_cut_to_the_writers_and_stop_with_the_version(api, monkeypatch):
    attempt, made = app_page(api)
    fid, version = made["file_id"], made["version"]
    _, r = served(api, fid)
    key = KEY.search(r.content).group(1).decode()
    shared = post(api, "tasks", {"owner": "ops", "title": "Acme renewal", "body": "x"}, "ben-test")
    mine = post(api, "tasks", {"owner": "ben", "title": "Ben salary review", "body": "x", "private": True}, "ben-test")
    assert api.get(f"/api/v2/tasks/{mine['id']}", headers=headers("ben-test")).status_code == 200

    me = call(api, fid, version, key, "me")
    assert me.status_code == 200, me.text
    assert me.json()["result"]["actor"].startswith("human:")
    rows = call(api, fid, version, key, "query", kind="tasks", limit=200).json()["result"]["rows"]
    ids = {t["id"] for t in rows}
    # ben reads both; the page's writer (the ops agent) cannot read ben's private task, so the app never sees it
    assert shared["id"] in ids and mine["id"] not in ids
    assert "body" not in rows[0]  # the listed fields (and their names), never a task's words

    # Undeclared reads, another viewer's key, a stranger, a bad key: refused.
    assert call(api, fid, version, key, "query", kind="files").json()["error"]["code"] == "not_declared"
    assert call(api, fid, version, key, "me", who="ana-test").status_code in (403, 404)
    assert call(api, fid, version, key, "me", who="cara-test").status_code == 404
    assert call(api, fid, version, key[:-2] + "xx", "me").status_code == 403
    assert call(api, fid, version, key, "drop").status_code == 422

    # The writer gone: nothing is read for them.
    with monkeypatch.context() as m:
        m.setattr(A.visuals, "token_reader", lambda *a: None)
        assert call(api, fid, version, key, "me").json()["error"]["code"] == "writer_gone"

    # A new version: the old frame's calls are refused.
    page(api, attempt, data=HTML.replace(b"42", b"43"), app="reads=tasks,store")
    assert call(api, fid, version, key, "me").json()["error"]["code"] == "version_replaced"
    # Archived: the app is stopped.
    _, r = served(api, fid)
    key = KEY.search(r.content).group(1).decode()
    assert call(api, fid, version + 1, key, "me").status_code == 200
    archived = api.patch(f"/api/v2/pages/{fid}", json={"archived": True}, headers=headers(attempt["token"]))
    assert archived.status_code == 200, archived.text
    assert call(api, fid, version + 1, key, "me").json()["error"]["code"] == "app_stopped"


def test_filters_open_and_contacts_hold_to_both_readers(api, monkeypatch):
    _, made = app_page(api, reads="tasks,files,pages,contacts")
    fid, version = made["file_id"], made["version"]
    _, r = served(api, fid)
    key = KEY.search(r.content).group(1).decode()
    shared = post(api, "tasks", {"owner": "ops", "title": "Acme renewal", "body": "x"}, "ben-test")
    mine = post(api, "tasks", {"owner": "ben", "title": "Ben salary review", "body": "x", "private": True}, "ben-test")

    def result(op, **args):
        answer = call(api, fid, version, key, op, **args)
        assert answer.status_code == 200, answer.text
        return answer.json()["result"]

    # A list on a task the writer cannot read says nothing of what links to it.
    for kind in ("pages", "files"):
        assert result("query", kind=kind, task=mine["id"]) == {"rows": [], "next": None}
        assert "rows" in result("query", kind=kind, task=shared["id"])
    # open answers only for a declared kind both may read.
    assert result("open", ref="task:" + shared["id"]) == {"href": "/task/" + shared["id"]}
    assert call(api, fid, version, key, "open", ref="task:" + mine["id"]).status_code == 404
    assert call(api, fid, version, key, "open", ref="file:nope").status_code == 404
    # This chat's page is out of its agent's own reach (an agent as itself), so it is not the app's to open either.
    assert call(api, fid, version, key, "open", ref="page:" + fid).status_code == 404
    assert call(api, fid, version, key, "open", ref="page:nope").status_code == 404
    assert call(api, fid, version, key, "open", ref="nowhere").status_code == 422
    assert result("query", kind="contacts")["rows"] == []
    assert call(api, fid, version, key, "query", kind="contacts", type="nope").status_code == 404
    assert call(api, fid, version, key, "query", kind="contacts", cursor="bad").status_code == 422
    # A contact's cursor carries its name, so it goes on whole, however long the name.
    asked = {}
    from backend.entities import store as ES

    real_find = ES.find
    monkeypatch.setattr(ES, "find", lambda *a, **k: asked.update(k) or real_find(*a, **k))
    long = "x" * 300
    call(api, fid, version, key, "query", kind="contacts", cursor=long)
    assert asked["after"] == long
    assert call(api, fid, version, key, "query", kind="kpis").json()["error"]["code"] == "not_declared"


def test_open_takes_a_page_as_a_page_and_never_an_archived_one(api, monkeypatch):
    attempt, made = app_page(api, reads="pages,files")
    fid = made["file_id"]
    notes = publish(api, attempt, name="notes.md", text="# Acme notes")
    assert notes.status_code == 200, notes.text
    other = notes.json()["file"]["id"]
    apps = api.app.state.page_apps
    monkeypatch.setattr(apps.files, "visible", lambda *a: True)  # both readers see both: only kind and state decide

    def href(ref):
        with api.app.state.store.read() as c:
            try:
                return apps.href(c, None, None, ["pages", "files"], ref)
            except A.Problem as e:
                return e.status

    assert href("page:" + fid) == "/library/files?file=" + fid
    assert href("file:" + other) == "/library/files?file=" + other
    assert href("page:" + other) == 404  # a file that is no page is not opened as one
    archived = api.patch(f"/api/v2/pages/{fid}", json={"archived": True}, headers=headers(attempt["token"]))
    assert archived.status_code == 200, archived.text
    assert href("page:" + fid) == 404


def test_rate_holds_twenty_a_second_per_frame():
    rate = A.Rate()
    assert all(rate.take("k", now=10.0 + i / 100) for i in range(20))
    assert not rate.take("k", now=10.5) and rate.take("other", now=10.5)
    assert rate.take("k", now=11.01)


def test_page_data_is_its_writers_to_keep_the_app_reads_it_and_it_goes_with_the_page(api, monkeypatch):
    attempt, made = app_page(api, who="ana-test")
    fid, version = made["file_id"], made["version"]
    token = attempt["token"]
    url = f"/api/v2/pages/{fid}/data"

    put = api.put(url + "/spend/2026-11-24", json={"data": {"meta": 120}}, headers=headers(token))
    assert put.status_code == 200, put.text
    assert put.json()["row"]["data"] == {"meta": 120} and put.json()["row"]["updated_by"].startswith("bot:")
    api.put(url + "/spend/2026-11-25", json={"data": {"meta": 90}}, headers=headers(token))
    listed = api.get(url, params={"collection": "spend", "limit": 1}, headers=headers(token)).json()
    assert [r["id"] for r in listed["rows"]] == ["2026-11-24"] and listed["next"] == "2026-11-24"
    nxt = api.get(url, params={"collection": "spend", "after": listed["next"]}, headers=headers(token)).json()
    assert [r["id"] for r in nxt["rows"]] == ["2026-11-25"] and nxt["next"] is None
    assert (
        api.put(url + "/spend/x", json={"data": "a" * (A.DATA_ROW_MAX + 1)}, headers=headers(token)).status_code == 413
    )
    assert api.put(url + "/Bad Name/x", json={"data": 1}, headers=headers(token)).status_code == 422
    assert api.get(url + "/spend/2026-11-24", headers=headers("cara-test")).status_code == 404

    # the app reads it through store
    _, r = served(api, fid, who="ana-test")
    key = KEY.search(r.content).group(1).decode()
    got = call(api, fid, version, key, "store.get", who="ana-test", collection="spend", id="2026-11-25").json()
    assert got["result"]["row"]["data"] == {"meta": 90}
    assert (
        len(call(api, fid, version, key, "store.list", who="ana-test", collection="spend").json()["result"]["rows"])
        == 2
    )

    with monkeypatch.context() as m:
        m.setattr(A.page_links, "may_write", lambda c, auth, who, row: not who.actor.startswith("bot:"))
        gone = call(api, fid, version, key, "store.list", who="ana-test", collection="spend")
        assert gone.json()["error"]["code"] == "writer_gone"

    removed = api.delete(url + "/spend/2026-11-25", headers=headers(token))
    assert removed.status_code == 200 and removed.json() == {"removed": True}

    # Hard delete takes the rows with the page.
    api.app.state.blobs._fresh.clear()
    assert api.delete("/api/v2/files/" + fid, headers=headers("ana-test")).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM page_app_rows WHERE page_id=?", (fid,)).fetchone()[0] == 0


def test_a_page_keeps_at_most_its_rows(api, monkeypatch):
    attempt, made = app_page(api)
    url = f"/api/v2/pages/{made['file_id']}/data/notes/"
    monkeypatch.setattr(A, "DATA_ROWS_MAX", 2)
    token = attempt["token"]
    assert [api.put(url + n, json={"data": n}, headers=headers(token)).status_code for n in ("a", "b", "c")] == [
        200,
        200,
        413,
    ]
    again = api.put(url + "a", json={"data": "again"}, headers=headers(token))
    assert again.status_code == 200, again.text  # rewriting a row is no new one


def test_bridge_goes_after_head_and_never_before_the_doctype():
    script = b'<script data-tico-app="k">'
    assert A.with_bridge(b"<!doctype html><html><head><title>x</title>", "k").startswith(
        b"<!doctype html><html><head>" + script
    )
    assert A.with_bridge(b"<!DOCTYPE html><p>hi", "k").startswith(b"<!DOCTYPE html>" + script)
    assert A.with_bridge(b"<p>hi", "k").startswith(script)
    # Tag-like text in a script or comment is never taken for the page's <head>.
    tricky = b"<!doctype html><html><script>var a='<head>'</script>"
    assert A.with_bridge(tricky, "k") == b"<!doctype html><html>" + A.with_bridge(b"", "k") + tricky[21:]
    commented = b"<!-- <head> --><html><head><title>x</title>"
    assert A.with_bridge(commented, "k").startswith(b"<!-- <head> --><html><head>" + script)
    quoted = b"<html data-x=\"a>b\"><head data-y='c>d'><title>x</title>"
    assert A.with_bridge(quoted, "k").startswith(b"<html data-x=\"a>b\"><head data-y='c>d'>" + script)
    assert A.parse("reads=tasks; reads=files") == '{"reads":["files","tasks"]}' and A.parse("  ") is None
