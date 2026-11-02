"""Live blocks in pages (docs/pages.md "Live blocks"): a page's <tico-*> tags are filled in for the one viewer the visual
address names, from the same visibility as Needs you and the task page. Nothing a viewer may not see reaches the page."""
import pytest

from backend import visuals as V
from backend.tests.test_api import api, headers, post  # noqa: F401  (fixtures)
from backend.tests.test_files import turn

VISUALS = "http://visuals.localhost:9001"
ON_VISUALS = {"host": "visuals.localhost:9001"}


def wait_on(api, tid, who):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='waiting', waiting_on=? WHERE id=?", (who, tid))


def page(api, attempt, task, name, data):
    made = api.post("/api/v2/pages", params={"name": name, "task": task}, content=data,
                    headers={**headers(attempt["token"]), "Content-Type": "text/html"})
    assert made.status_code == 200, made.text
    return made.json()["page"]["file_id"]


def view(api, fid, who, version=1, follow=True):
    token = V.mint(api.app.state.visual_key, "human:" + who, fid, version)
    return api.get("/visuals/" + token, headers=ON_VISUALS, follow_redirects=follow)


def test_a_live_page_shows_each_viewer_only_what_they_may_see_and_escapes_titles(api, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", VISUALS)
    _, attempt = turn(api)
    mine = post(api, "tasks", {"owner": "ops", "title": "<script>alert(1)</script> Renew", "body": "x",
                               "private": True}, "ben-test")
    theirs = post(api, "tasks", {"owner": "ops", "title": "Cara private ask", "body": "x", "private": True},
                  "cara-test")
    wait_on(api, mine["id"], "human:ben")
    wait_on(api, theirs["id"], "human:cara")
    shared = post(api, "tasks", {"owner": "ops", "title": "Weekly review", "body": "x", "private": False}, "ben-test")
    html_page = (f'<html><body><h1>Asks</h1><tico-tasks waiting-on="viewer" limit="5"></tico-tasks>'
                 f'<tico-task id="{theirs["id"]}"></tico-task><tico-files task="{theirs["id"]}"></tico-files>'
                 f'<tico-files task="{shared["id"]}"></tico-files></body></html>').encode()
    fid = page(api, attempt, shared["id"], "asks.html", html_page)
    # Publishing keeps the live tags and drops nothing else about them.
    stored = api.get("/api/v2/files/" + fid, headers=headers("ben-test")).content
    assert b'<tico-tasks waiting-on="viewer" limit="5"></tico-tasks>' in stored

    ben = view(api, fid, "ben")
    assert ben.status_code == 200, ben.text
    assert b"&lt;script&gt;alert(1)&lt;/script&gt; Renew" in ben.content
    assert b"<script>alert(1)" not in ben.content
    assert b"Cara private ask" not in ben.content and theirs["id"].encode() not in ben.content

    cara = view(api, fid, "cara")
    assert cara.status_code == 200, cara.text
    assert b"Cara private ask" in cara.content and b"Renew" not in cara.content
    assert b"Weekly review" not in cara.content and b"Nothing waiting" not in cara.content
    assert f"#/page/{fid}".encode() in cara.content                 # a page in a file list opens its page view


def test_a_page_with_live_blocks_is_never_the_hosted_copy(api, monkeypatch):
    from types import SimpleNamespace
    from backend import tico_storage
    # No shared copy is ever written for a live page, so the hosted 302 never finds one.
    monkeypatch.setattr(tico_storage, "_writable", lambda *args: pytest.fail("a live page is never copied"))
    live_bytes = b'<html><body><tico-tasks waiting-on="viewer"></tico-tasks></body></html>'
    assert tico_storage.make_visual(SimpleNamespace(edge={"location": "x"}), "d" * 64, "page", live_bytes, ()) is False
    # Without a copy it is served from here, filled in for the reader.
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", VISUALS)
    monkeypatch.setattr(tico_storage, "visual_link", lambda *args, **kwargs: None)
    _, attempt = turn(api)
    shared = post(api, "tasks", {"owner": "ops", "title": "Weekly review", "body": "x", "private": False}, "ben-test")
    live = page(api, attempt, shared["id"], "live.html", live_bytes)
    shown = view(api, live, "ben")
    assert shown.status_code == 200 and b"Nothing waiting on you" in shown.content


def test_publishing_keeps_live_tags_with_their_own_attributes_only():
    cleaned, unembedded = V.clean(b'<p><tico-tasks waiting-on="viewer" limit="3" bogus="1" onclick="x()">'
                                  b'</tico-tasks><tico-task id="t1" src="https://cdn.example/x.png"/></p>',
                                  "page", base=False)
    assert b'<tico-tasks waiting-on="viewer" limit="3"></tico-tasks>' in cleaned
    assert b'<tico-task id="t1"/>' in cleaned
    assert b"bogus" not in cleaned and b"onclick" not in cleaned and unembedded == []


def test_a_page_fills_at_most_the_cap_and_the_tags_past_it_render_empty(monkeypatch):
    from types import SimpleNamespace
    from backend import live_blocks as LB
    filled = []
    monkeypatch.setattr(LB, "_task", lambda c, auth, who, attrs, base: filled.append(attrs["id"]) or f"<b>{attrs['id']}</b>")
    auth = SimpleNamespace(settings=SimpleNamespace(public_url=""))
    text = "".join(f'<tico-task id="t{i}"></tico-task>' for i in range(LB.LIVE_TAG_CAP + 25))
    out = LB.fill(text, None, None, auth, None)
    assert filled == [f"t{i}" for i in range(LB.LIVE_TAG_CAP)]
    assert out.startswith(LB.STYLE) and out.count(LB.STYLE) == 1
    assert "<b>t49</b>" in out and "t50" not in out and "<tico-task" not in out
