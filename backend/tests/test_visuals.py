"""Visual replies 1/4 (Dev 245): a visual attached to a chat reply or a task comment, its clean-up, its signed
cookie-free address and who may open it. Spec tests 1-4, 18, 19 and 22 (Product roadmap "Visual replies")."""
import base64
import hashlib
import pathlib
import re
import time
from dataclasses import replace

import pytest

from backend import visuals as V
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from backend.tests.test_files import publish, turn
from backend.tests.test_events import live  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DOT = "data:image/png;base64," + base64.b64encode(PNG).decode()
VISUALS = "http://visuals.localhost:9001"


@pytest.fixture(autouse=True)
def full(request, monkeypatch):
    """Visual replies show in full on a separate visual address; the fallback tests (test_visuals_fallback.py) and
    the ones here that need none take it away again."""
    if "api" in request.fixturenames:
        monkeypatch.setattr(request.getfixturevalue("api").app.state.store.settings, "visual_origin", VISUALS)


def png(api, attempt, name="chart.png", data=PNG):
    made = publish(api, attempt, name=name, text=None, content_base64=base64.b64encode(data).decode())
    assert made.status_code == 200, made.text
    return made.json()["file"]["id"], 1


def bot_reply(api, attempt, to="human:ben", **body):
    conversation = attempt["conversation_id"] if "conversation_id" in attempt else attempt["message"]["conversation_id"]
    return api.post("/api/v2/messages", json={"to": to, "text": "Here is the chart", "conversation_id": conversation,
                                              **body}, headers=headers(attempt["token"]))


def test_a_bots_chat_reply_carries_a_titled_versioned_visual_and_reading_returns_it(api):
    """Spec 1: stored as a versioned file on that conversation with its title; reading the message returns it."""
    _, attempt = turn(api)
    fid, _ = png(api, attempt)
    sent = bot_reply(api, attempt, attachments=[{"ref": f"{fid}@1", "title": "Weekly signups"}])
    assert sent.status_code == 200, sent.text
    message = sent.json()
    for read in (api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json(),
                 next(m for m in api.get(f"/api/v2/conversations/{message['conversation_id']}/messages",
                                         headers=headers("ben-test")).json()["messages"] if m["id"] == message["id"])):
        [item] = read["refs"]["attachments"]
        assert (item["file_id"], item["version"], item["title"], item["kind"]) == (fid, 1, "Weekly signups", "image")
        assert (item["name"], item["content_type"], item["size"]) == ("chart.png", "image/png", len(PNG))
        assert item["url"] == f"/api/v2/files/{fid}?v=1" and item["current_version"] == 1 and not item["removed"]
        assert item["src"].startswith(VISUALS + "/visuals/")
    with api.app.state.store.read() as c:
        row = c.execute("SELECT scope FROM bot_files WHERE id=?", (fid,)).fetchone()
    assert row["scope"] == "conversation:" + message["conversation_id"]


def test_a_chat_reply_cannot_attach_a_file_from_another_conversation(api):
    _, attempt = turn(api)
    fid, _ = png(api, attempt)
    _, other = turn(api, bot="cpo", who="cara-test")
    sent = bot_reply(api, other, to="human:cara", attachments=[f"{fid}@1"])
    assert sent.status_code == 404, sent.text


def task_with_file(api, *, private, name="page.html", text="<p>hi</p>"):
    task_with_file.n = getattr(task_with_file, "n", 0) + 1
    task = post(api, "tasks", {"owner": "ops", "title": f"Chart {task_with_file.n}", "body": "x", "private": private},
                "ben-test")
    tid = task["id"]
    made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": name, "text": text}, headers=headers("ben-test"))
    assert made.status_code == 200, made.text
    return tid, made.json()["file_id"], made.json()["version"]


def test_a_task_comment_page_gets_its_local_images_embedded_and_reports_nothing_unembedded(api):
    """Spec 2: a page whose two images are embedded data: URIs stays as it is and reports zero unembedded."""
    page = f'<html><body><img src="{DOT}"><div style="background:url({DOT})"></div></body></html>'
    tid, fid, number = task_with_file(api, private=False, text=page)
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "See the page",
                 "attachments": [{"ref": f"{fid}@{number}", "title": "Funnel"}]}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert r.json()["visuals"] == [{"ref": f"{fid}@{number}", "kind": "page", "unembedded": []}]
    [item] = r.json()["comment"]["refs"]["attachments"]
    assert item["title"] == "Funnel" and item["kind"] == "page" and item["version"] == number


def test_a_remote_reference_is_named_and_the_stored_page_loads_nothing(api):
    """Spec 3: the attach result names each remote reference; the stored file has none that loads."""
    page = ('<html><head><link rel="stylesheet" href="https://cdn.example/a.css"><style>@import "https://f.example/x.css";'
            'body{background:url(https://img.example/bg.png)}</style></head><body><img src="https://img.example/a.png">'
            '<img srcset="//img.example/b.png 2x"><script src="https://js.example/x.js"></script>'
            '<a href="https://example.com/more">more</a></body></html>')
    tid, fid, number = task_with_file(api, private=False, text=page)
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Page", "attachments": [f"{fid}@{number}"]},
                 headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    [report] = r.json()["visuals"]
    assert report["unembedded"] == ["https://cdn.example/a.css", "https://f.example/x.css",
                                    "https://img.example/bg.png", "https://img.example/a.png", "//img.example/b.png"]
    [item] = r.json()["comment"]["refs"]["attachments"]
    assert item["version"] == number + 1                    # the cleaned copy is the version posted
    stored = api.get(f"/api/v2/files/{fid}/versions/{item['version']}", headers=headers("ben-test"))
    assert stored.status_code == 200
    assert V.loading_references(stored.content) == [] and b"<script" not in stored.content
    assert b'href="https://example.com/more"' in stored.content       # a link is a click, not a load


def test_over_the_limit_or_another_type_is_a_plain_file_never_a_visual(api, monkeypatch):
    """Spec 4: too big, or a type that is not a visual, is a file chip; a refused name is a plain refusal."""
    monkeypatch.setitem(V.LIMITS, "page", 10)
    tid, fid, number = task_with_file(api, private=False, text="<p>" + "x" * 50 + "</p>")
    tid2, fid2, number2 = task_with_file(api, private=False, name="notes.csv", text="a,b")
    for t, f, n in ((tid, fid, number), (tid2, fid2, number2)):
        r = api.post(f"/api/v2/tasks/{t}/comments", json={"text": "x", "attachments": [f"{f}@{n}"]},
                     headers=headers("ben-test"))
        assert r.status_code == 200, r.text
        [item] = r.json()["comment"]["refs"]["attachments"]
        assert item["kind"] == "file" and "src" not in item
    # Bytes that are not the picture their name says: a chip.
    assert V.kind_of("image/png", 10, b"GIF89a....") == "file" and V.kind_of("application/zip", 1) == "file"


def test_six_attachments_come_back_all_in_order(api):
    tid = post(api, "tasks", {"owner": "ops", "title": "Six", "body": "x", "private": False}, "ben-test")["id"]
    refs = []
    for i in range(6):
        made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": f"p{i}.html", "text": f"<p>{i}</p>"},
                        headers=headers("ben-test")).json()
        refs.append({"ref": f"{made['file_id']}@{made['version']}", "title": f"Page {i}"})
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "x", "attachments": refs}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    wanted = [(a["ref"], a["title"], "page") for a in refs]
    comment = next(m for m in api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("ben-test")).json()["comments"]
                   if m["id"] == r.json()["comment"]["id"])
    for read in (r.json()["comment"], comment):
        items = read["refs"]["attachments"]
        assert [(a["ref"], a["title"], a["kind"]) for a in items] == wanted
        assert all(a["src"].startswith(VISUALS + "/visuals/") for a in items)


# ---------------------------------------------------------------------------------------------- access (18, 19)
def visual_on_task(api, private):
    tid, fid, number = task_with_file(api, private=private, name="chart.html", text="<p>chart</p>")
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "chart", "attachments": [f"{fid}@{number}"]},
                 headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    return tid, fid, r.json()["comment"]


def test_a_private_tasks_visual_is_refused_to_a_reader_without_access(api):
    """Spec 18: the message, the file and the direct link are each refused."""
    tid, fid, comment = visual_on_task(api, private=True)
    [item] = comment["refs"]["attachments"]
    assert api.get(item["src"]).status_code == 200                      # the reader's own address works
    for path in (f"/api/v2/messages/{comment['id']}", f"/api/v2/tasks/{tid}/comments",
                 f"/api/v2/files/{fid}", f"/api/v2/files/{fid}/versions/{item['version']}"):
        assert api.get(path, headers=headers("cara-test")).status_code in (403, 404), path
    # A token minted for a reader without access opens nothing.
    forged = V.mint(api.app.state.visual_key, "human:cara", fid, item["version"])
    assert api.get("/visuals/" + forged).status_code == 404
    # A tampered token opens nothing.
    assert api.get(item["src"][:-3] + "abc").status_code == 404


def test_a_non_private_tasks_visual_is_open_to_its_readers(api):
    tid, fid, comment = visual_on_task(api, private=False)
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("cara-test"))
    assert listed.status_code == 200
    [item] = next(m for m in listed.json()["comments"] if m["id"] == comment["id"])["refs"]["attachments"]
    assert api.get(item["src"]).status_code == 200
    page = api.get(f"/api/v2/tasks/{tid}", headers=headers("cara-test")).json()     # the task page reads them too
    [item] = next(m for m in page["comments"] if m["id"] == comment["id"])["refs"]["attachments"]
    assert api.get(item["src"]).status_code == 200


def test_a_reader_who_loses_access_loses_the_address_on_the_next_request(api):
    tid, fid, comment = visual_on_task(api, private=False)
    src = comment["refs"]["attachments"][0]["src"]
    assert api.get(src).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET archived=1 WHERE id=?", (fid,))
    assert api.get(src).status_code == 404
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("ben-test")).json()["comments"]
    [item] = next(m for m in listed if m["id"] == comment["id"])["refs"]["attachments"]
    assert item["removed"] and "src" not in item


def test_a_reader_taken_off_a_private_task_is_refused_the_next_range_with_their_old_address(api):
    """The claim Review 2 and Security lean on: the address is checked against the reader's access on every request,
    each byte range included, so losing access ends it at once, not when the token expires."""
    tid = post(api, "tasks", {"owner": "ops", "title": "Cara's chart", "body": "x", "private": True}, "cara-test")["id"]
    made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "chart.png", "content_base64": base64.b64encode(PNG).decode()},
                    headers=headers("cara-test"))
    assert made.status_code == 200, made.text
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "chart", "attachments": [
        f"{made.json()['file_id']}@{made.json()['version']}"]}, headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    src = r.json()["comment"]["refs"]["attachments"][0]["src"]
    assert api.get(src, headers={"Range": "bytes=0-1"}).status_code == 206
    with api.app.state.store.transaction() as c:                # the task is handed to Ben: Cara no longer reads it
        c.execute("UPDATE tasks SET requester_id='human:ben' WHERE id=?", (tid,))
    assert api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("cara-test")).status_code in (403, 404)
    for request in ({"Range": "bytes=0-1"}, {"Range": "bytes=4-9"}, {}):
        assert api.get(src, headers=request).status_code == 404


def test_a_chat_visual_is_refused_to_everyone_outside_the_chat(api):
    _, attempt = turn(api)
    fid, _ = png(api, attempt)
    message = bot_reply(api, attempt, attachments=[f"{fid}@1"]).json()
    for other in ("cara-test",):
        assert api.get("/api/v2/messages/" + message["id"], headers=headers(other)).status_code in (403, 404)
        assert api.get(f"/api/v2/files/{fid}", headers=headers(other)).status_code == 404
        assert api.get("/visuals/" + V.mint(api.app.state.visual_key, "human:cara", fid, 1)).status_code == 404


def test_the_public_roadmap_never_shows_a_private_tasks_visual(api):
    """Spec 19: nothing of a private task's visual (file id, title, address) reaches a public roadmap response."""
    tid, fid, comment = visual_on_task(api, private=True)
    assert comment["refs"]["attachments"][0]["src"].startswith(VISUALS + "/visuals/")     # a reader does see it
    from backend import public_roadmaps
    from backend.store import H
    with api.app.state.store.transaction() as c:
        # Even marked public and on a task made public, the comment's visual does not travel.
        c.execute("UPDATE messages SET public=1 WHERE id=?", (comment["id"],))
        c.execute("UPDATE tasks SET visibility='public', public_title='Chart' WHERE id=?", (tid,))
        row = H.task(c, tid)
        shown = repr(public_roadmaps.comments(c, row)) + repr(public_roadmaps.output(c, row))
    assert comment["id"] in shown
    assert fid not in shown and "/visuals/" not in shown and "chart.html" not in shown


def snapshot_item(api, cid, mid, who):
    got = api.get(f"/api/v2/conversations/{cid}/snapshot", headers=headers(who))
    if got.status_code != 200:
        return got.status_code
    [item] = next(m for m in got.json()["messages"] if m["id"] == mid)["refs"]["attachments"]
    return item


def test_the_snapshot_and_the_live_stream_give_each_reader_the_visual_and_nobody_else(api, live):
    """The conversation snapshot (what the app opens a chat or task with) and the live stream (a new comment arriving)
    carry the same fresh address as the message routes, for a private task's comment and a chat reply alike."""
    _, attempt = turn(api)
    png_id, _ = png(api, attempt)
    reply = bot_reply(api, attempt, attachments=[{"ref": f"{png_id}@1", "title": "Signups"}])
    assert reply.status_code == 200, reply.text
    reply = reply.json()
    tid, fid, comment = visual_on_task(api, private=True)
    with api.app.state.store.read() as c:
        room = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    for cid, mid, file_id in ((room, comment["id"], fid),
                              (reply["conversation_id"], reply["id"], png_id)):
        item = snapshot_item(api, cid, mid, "ben-test")
        assert (item["file_id"], item["current_version"], item["removed"]) == (file_id, 1, False)
        assert api.get(item["src"]).status_code == 200
        assert snapshot_item(api, cid, mid, "cara-test") in (403, 404)       # not a reader: no snapshot at all

    stream = live("ben-test", f"?topics=messages&conversation={room}")
    assert stream.status == 200
    stream.until(lambda e: e["event"] in ("keepalive", "cursor", "messages"))
    made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "next.html", "text": "<p>next</p>"},
                    headers=headers("ben-test")).json()
    sent = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "next", "attachments": [
        {"ref": f"{made['file_id']}@{made['version']}", "title": "Next"}]}, headers=headers("ben-test")).json()
    event = stream.until(lambda e: e["event"] == "messages"
                         and (e["data"].get("message") or {}).get("id") == sent["comment"]["id"])
    [item] = event["data"]["message"]["refs"]["attachments"]
    assert item["title"] == "Next" and item["removed"] is False and api.get(item["src"]).status_code == 200


def test_dropping_an_embed_or_frame_keeps_the_rest_of_the_page():
    """embed and frame have no end tag: dropping one must not drop what follows, alone or inside a dropped object."""
    for page in ('<p>before</p><embed src="old.pdf"><p>after</p>',
                 '<p>before</p><object data="x.swf"><embed src="y.swf"><p>fallback</p></object><p>after</p>',
                 '<p>before</p><frame src="f.html"><p>after</p>'):
        cleaned, unembedded = V.clean(page.encode(), "page", base=False)
        assert b"<p>before</p>" in cleaned and b"<p>after</p>" in cleaned, cleaned
        assert b"embed" not in cleaned and b"frame" not in cleaned and b"object" not in cleaned
        assert b"fallback" not in cleaned                    # a dropped container's own content still goes


def test_unquoted_src_and_both_srcset_forms_are_embedded_and_survive_the_server(tmp_path):
    """Spec 2 for ordinary HTML: every local image a browser would load ends up a data: URI the server keeps."""
    from clients import visual_embed
    (tmp_path / "a.png").write_bytes(PNG)
    other = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
    (tmp_path / "b.png").write_bytes(other)
    (tmp_path / "page.html").write_text('<img src=a.png><img src=\'b.png\'><img srcset="a.png 1x,b.png 2x">'
                                        '<img srcset="b.png 1x, a.png 2x">')
    data, embedded, unembedded = visual_embed.embed(tmp_path, "page.html")
    assert unembedded == [] and b"a.png" not in data and b"b.png" not in data
    cleaned, removed = V.clean(data, "page", base=False)
    assert removed == []
    for image in (PNG, other):
        assert cleaned.count(base64.b64encode(image).decode().encode()) == 3


def test_an_older_task_attachment_stays_a_plain_file(api):
    """A task attachment from before versioned files (task_assets) is still attached, as the plain file it was:
    a picture, a clean page and a page cleaning would change all post, none becomes a visual or is marked removed."""
    from backend.auth import Identity
    from backend.blobs import register
    tid = post(api, "tasks", {"owner": "ops", "title": "Old files", "body": "x", "private": False}, "ben-test")["id"]
    refs = []
    for name, data, mime in (("old.png", PNG, "image/png"), ("clean.html", b"<p>report</p>", "text/html"),
                             ("remote.html", b'<img src="remote.png"><p>report</p>', "text/html")):
        digest = api.app.state.blobs.put(data, mime)
        with api.app.state.store.transaction() as c:
            old = register(c, Identity("human:ben", "admin"), digest, len(data), name, mime)
            c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, old["id"]))
        refs.append(f"{old['id']}@1")
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "old ones", "attachments": refs},
                 headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("ben-test")).json()["comments"]
    for read in (r.json()["comment"], next(m for m in listed if m["id"] == r.json()["comment"]["id"])):
        items = read["refs"]["attachments"]
        assert [a["ref"] for a in items] == refs and {a["kind"] for a in items} == {"file"}
        assert not any("src" in a or a.get("removed") for a in items)
    for ref in refs:                                             # the bytes are as they were: nothing was rewritten
        assert api.get("/api/v2/files/" + ref.split("@")[0], headers=headers("ben-test")).status_code == 200


# ---------------------------------------------------------------------------------------------- serving (22)
def test_the_visual_route_is_apart_from_the_sign_in_with_a_policy_that_blocks_scripts_and_loads(api):
    """Spec 22: the response carries a policy with no network loads and only the height reporter's script hash;
    it never sets a cookie, and the app's own sign-in does not open it."""
    tid, fid, comment = visual_on_task(api, private=False)
    src = comment["refs"]["attachments"][0]["src"]
    r = api.get(src)
    rule = r.headers["content-security-policy"]
    assert "default-src 'none'" in rule and "connect-src 'none'" in rule and "form-action 'none'" in rule
    assert re.search(r"script-src 'sha256-[A-Za-z0-9+/=]+';", rule) and "unsafe-inline' data:; connect" in rule
    assert "'unsafe-inline'" not in rule.split("script-src", 1)[1].split(";", 1)[0]
    assert "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox" in rule
    assert "allow-same-origin" not in rule and "allow-forms" not in rule and "allow-top-navigation" not in rule
    assert "set-cookie" not in r.headers and r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert b'<base target="_blank">' in r.content and V.REPORTER.encode() in r.content
    # The hash is the served script's own, taken from backend/visual-reporter.js, never typed in by hand.
    script = (pathlib.Path(V.__file__).with_name("visual-reporter.js")).read_text().strip()
    assert V.REPORTER == script and f"script-src '{V.REPORTER_HASH}';" in rule
    assert V.REPORTER_HASH == "sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"<script>{script}</script>".encode() in r.content
    # The signed-in app has no way in without the token, and the token needs no cookie.
    assert api.get("/visuals/", headers=headers("ben-test")).status_code == 404
    assert api.get("/visuals/nonsense", headers=headers("ben-test")).status_code == 404


def test_video_on_the_visual_route_serves_byte_ranges(api):
    """3/4 test 13 needs seeking: bytes=0-1, a mid-file range, and a range past the end."""
    _, attempt = turn(api)
    data = bytes(range(256)) * 8
    made = publish(api, attempt, name="clip.mp4", text=None, content_base64=base64.b64encode(data).decode())
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    message = bot_reply(api, attempt, attachments=[{"ref": f"{fid}@1", "title": "Clip"}]).json()
    read = api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json()
    [item] = read["refs"]["attachments"]
    assert item["kind"] == "video"
    first = api.get(item["src"], headers={"Range": "bytes=0-1"})
    assert first.status_code == 206 and first.headers["content-range"] == f"bytes 0-1/{len(data)}"
    assert first.content == data[:2]
    middle = api.get(item["src"], headers={"Range": "bytes=1000-1099"})
    assert middle.status_code == 206 and middle.content == data[1000:1100]
    assert api.get(item["src"], headers={"Range": f"bytes={len(data) + 5}-"}).status_code == 416
    assert "sandbox" in middle.headers["content-security-policy"] and "set-cookie" not in middle.headers


def test_tokens_are_signed_expire_and_stay_stable_within_half_their_life():
    key = b"k" * 32
    token = V.mint(key, "human:ben", "f", 3, now=0)
    assert V.read(key, token, now=0) == ("human:ben", "f", 3, "")
    assert V.read(key, token, now=V.LIFE) is None
    assert V.read(b"x" * 32, token, now=0) is None
    assert V.mint(key, "human:ben", "f", 3, now=V.LIFE // 2 - 1) == token
    assert V.mint(key, "human:ben", "f", 3, now=V.LIFE // 2) != token


def test_a_visual_origin_equal_to_the_app_origin_is_refused():
    class S:
        public_url, visual_origin = "https://tico.example", "https://tico.example/"
    try:
        V.check_origin(S)
    except ValueError:
        pass
    else:
        raise AssertionError("the app's own origin was accepted as the visual origin")


def test_actual_config_projects_visual_origin_only_after_api_auth_without_private_settings(api, monkeypatch):
    settings = api.app.state.store.settings
    for path in ("/api/me", "/api/v2/config"):
        assert api.get(path).status_code == 401
    monkeypatch.setattr(settings, "visual_origin", "")
    config = api.get("/api/v2/config", headers=headers()).json()
    assert (config["visual_origin"], config["visuals"]) == ("", "media")
    monkeypatch.setattr(settings, "visual_origin", replace(settings, visual_origin="https://visuals.acme-files.example/").visual_origin)
    monkeypatch.setattr(settings, "oidc_client_secret", "synthetic-private-oidc-secret")
    monkeypatch.setattr(settings, "gateway_key", "synthetic-private-gateway-key")
    monkeypatch.setattr(settings, "screen_relay_key", "synthetic-private-screen-key")
    for path in ("/api/me", "/api/v2/config"):
        response = api.get(path, headers=headers("ben-test"))
        assert response.status_code == 200
        config = response.json()["config"] if path == "/api/me" else response.json()
        assert (config["visual_origin"], config["visuals"]) == ("https://visuals.acme-files.example", "full")
        assert "synthetic-private-" not in response.text
        assert not {"oidc_client_secret", "gateway_key", "screen_relay_key", "visual_key"} & config.keys()


def test_visual_projection_hides_legacy_credentials_and_bad_hosts_without_changing_runtime(api, monkeypatch):
    settings = api.app.state.store.settings
    for configured in ("https://synthetic-user:synthetic-visual-password@visuals.acme-files.example/",
                       "https://@visuals.acme-files.example/", "https://bad_host.acme.example/",
                       "https://visuals.acme-files.example:"):
        legacy = replace(settings, visual_origin=configured).visual_origin
        monkeypatch.setattr(settings, "visual_origin", legacy)
        response = api.get("/api/v2/config", headers=headers())
        assert response.status_code == 200 and response.json()["visual_origin"] == ""
        assert "synthetic-visual-password" not in response.text
        assert settings.visual_origin == legacy and V.mode(settings) == "full"
    local = replace(settings, visual_origin="http://localhost:9001/").visual_origin
    monkeypatch.setattr(settings, "visual_origin", local)
    assert api.get("/api/v2/config", headers=headers()).json()["visual_origin"] == "http://localhost:9001"
    assert settings.visual_origin == local and V.mode(settings) == "full"


def test_a_persons_chat_upload_is_a_titled_versioned_visual_too(api):
    """Choice 2: a picture a person sends is a visual like a bot's; older clients keep the blob id and url."""
    r = api.post("/api/v2/uploads/chat/ops", data={"text": "My sketch", "titles": '["Sketch"]'},
                 files={"files": ("sketch.png", PNG, "image/png")}, headers=headers("ben-test", "k-visual-1"))
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    [now] = message["refs"]["attachments"]                                    # the upload's own response
    assert now["src"].startswith(VISUALS + "/visuals/") and (now["current_version"], now["removed"]) == (1, False)
    read = api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json()
    [item] = read["refs"]["attachments"]
    assert item["url"] == "/api/v2/files/" + item["id"]                     # unchanged for today's chips
    assert (item["title"], item["kind"], item["version"]) == ("Sketch", "image", 1)
    assert item["file_id"] != item["id"] and api.get(item["src"]).status_code == 200
    with api.app.state.store.read() as c:
        scope = c.execute("SELECT scope FROM bot_files WHERE id=?", (item["file_id"],)).fetchone()["scope"]
    assert scope == "conversation:" + message["conversation_id"]
    for other in ("cara", "ana"):                   # the company owner included: a personal chat is not theirs
        assert api.get("/api/v2/files/" + item["file_id"], headers=headers(other + "-test")).status_code == 404
        token = V.mint(api.app.state.visual_key, "human:" + other, item["file_id"], 1)
        assert api.get("/visuals/" + token).status_code == 404


def test_a_file_from_one_chat_cannot_be_posted_into_another_by_someone_who_can_read_both(api):
    """The conversation check stands alone: Ben can open his ops chat's file, and still can't post it to cpo."""
    r = api.post("/api/v2/uploads/chat/ops", data={"text": "Sketch"},
                 files={"files": ("sketch.png", PNG, "image/png")}, headers=headers("ben-test", "k-visual-2"))
    fid = r.json()["message"]["refs"]["attachments"][0]["file_id"]
    assert api.get("/api/v2/files/" + fid, headers=headers("ben-test")).status_code == 200
    moved = api.post("/api/v2/chat/cpo", json={"text": "Look", "attachments": [f"{fid}@1"]},
                     headers=headers("ben-test", "k-visual-3"))
    assert moved.status_code == 404, moved.text


def wav():
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(b"\x00\x01" * 800)
    return out.getvalue()


def test_a_wav_on_a_task_is_an_audio_player_whatever_python_calls_its_type(api, monkeypatch):
    """QA d1c1857a: Python 3.15 guesses .wav as audio/vnd.wave, which was a file chip. JSON and multipart uploads
    store it as audio/wav, the comment shows an audio card, and the visual route serves it by range."""
    import mimetypes
    guess = mimetypes.guess_type      # the machine's own MIME tables vary; pin the guess QA saw on the shipped build
    monkeypatch.setattr(mimetypes, "guess_type", lambda name, *a, **k: ("audio/vnd.wave", None)
                        if str(name).lower().endswith(".wav") else guess(name, *a, **k))
    data = wav()
    task = post(api, "tasks", {"owner": "ops", "title": "Voice note", "body": "x", "private": True}, "ben-test")
    tid = task["id"]
    by_json = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "private.wav",
                       "content_base64": base64.b64encode(data).decode()}, headers=headers("ben-test"))
    by_form = api.post(f"/api/v2/tasks/{tid}/files", data={"name": "second.wav"},
                       files={"file": ("second.wav", data, "application/octet-stream")}, headers=headers("ben-test"))
    for made in (by_json, by_form):
        assert made.status_code == 200, made.text
        r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Listen",
                     "attachments": [f"{made.json()['file_id']}@{made.json()['version']}"]}, headers=headers("ben-test"))
        assert r.status_code == 200, r.text
        [item] = r.json()["comment"]["refs"]["attachments"]
        assert (item["kind"], item["content_type"]) == ("audio", "audio/wav"), item
        part = api.get(item["src"], headers={"Range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"RIFF" and part.headers["content-type"] == "audio/wav"


def test_a_wav_already_stored_or_sent_as_audio_vnd_wave_still_plays_inline(api):
    """Versions stored before the fix, and a browser that sends audio/vnd.wave, keep the audio card."""
    assert V.kind_of("audio/vnd.wave", 1000) == "audio" and V.stored_type("audio/vnd.wave") == "audio/wav"
    r = api.post("/api/v2/uploads/chat/ops", data={"text": "My voice note"},
                 files={"files": ("note.wav", wav(), "audio/vnd.wave")}, headers=headers("ben-test", "k-visual-wav"))
    assert r.status_code == 200, r.text
    [item] = r.json()["message"]["refs"]["attachments"]
    assert item["kind"] == "audio" and item["src"].startswith(VISUALS + "/visuals/")


def media_settled(api):
    """Wait until the media worker (backend/file_metadata.py) has nothing pending, deferred retries included: its
    save rewrites a version's poster and preview, so one landing after a test sets them would clear them."""
    from backend.file_metadata import PENDING
    for _ in range(200):
        with api.app.state.store.read() as c:
            if not c.execute(PENDING, (float("inf"),)).fetchone():
                return
        time.sleep(0.05)
    raise AssertionError("the media worker never settled")


def test_a_bot_is_never_handed_a_visual_address_the_visual_route_refuses(api):
    """Dev 319: the visual route rebuilds a bot reader without its run, so it cannot see a chat's files. A bot reading
    its chat reply gets the file chip (no address) rather than a link that always 404s; the person keeps theirs, and
    a bot's address for a task file still opens."""
    _, attempt = turn(api)
    fid, _ = png(api, attempt)
    made = publish(api, attempt, name="clip.mp4", text=None, content_base64=base64.b64encode(bytes(range(256))).decode())
    assert made.status_code == 200, made.text
    clip = made.json()["file"]["id"]
    media_settled(api)
    with api.app.state.store.transaction() as c:              # a video with its poster ready, as file_metadata leaves it
        c.execute("UPDATE bot_file_versions SET poster_blob_id=(SELECT blob_id FROM bot_file_versions WHERE file_id=?) "
                  "WHERE file_id=?", (fid, clip))
    sent = bot_reply(api, attempt, attachments=[f"{fid}@1", f"{clip}@1"])
    assert sent.status_code == 200, sent.text
    message = sent.json()
    for read in (message, api.get("/api/v2/messages/" + message["id"], headers=headers(attempt["token"])).json()):
        picture, video = read["refs"]["attachments"]
        assert (picture["file_id"], video["file_id"], video["kind"]) == (fid, clip, "video")
        for item in (picture, video):
            assert not item["removed"] and item["current_version"] == 1
            assert item.get("src") is None and item.get("poster_url") is None, item
    picture, video = api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json()["refs"]["attachments"]
    for address in (picture["src"], video["src"], video["poster_url"]):  # the person's addresses are unchanged
        assert api.get(address).status_code in (200, 206), address

    tid, task_fid, comment = visual_on_task(api, private=False)
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers(attempt["token"]))
    assert listed.status_code == 200, listed.text
    [item] = next(m for m in listed.json()["comments"] if m["id"] == comment["id"])["refs"]["attachments"]
    assert item["file_id"] == task_fid and api.get(item["src"]).status_code == 200


def test_a_reader_acting_for_someone_else_gets_a_working_address(api):
    """A bot acting for its requester, or a person through BotOps, reads as two actors (task_privacy.actor). Their
    visual address names one reader: the tuple once crashed the task page with a 500 and could not be signed."""
    from backend.auth import Identity
    from backend.store import H
    tid, fid, comment = visual_on_task(api, private=False)
    settings = api.app.state.store.settings
    for who in (Identity("bot:ops", "bot", task_actor="human:ben"), Identity("human:ben", "human", task_actor="bot:ops")):
        with api.app.state.store.read() as c:
            [message] = V.for_reader(api.app, settings, c, who, [H.message(c, comment["id"])])
        [item] = message["refs"]["attachments"]
        assert not item["removed"] and item["src"], who
        assert api.get(item["src"]).status_code == 200, who


def test_first_requests_at_once_share_one_whole_secret(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    monkeypatch.delenv("TICO_VISUAL_SECRET", raising=False)
    settings, start = SimpleNamespace(db_path=tmp_path / "hub.db"), threading.Barrier(16)

    def first():
        start.wait()
        return V.secret(settings)
    with ThreadPoolExecutor(16) as pool:
        keys = set(pool.map(lambda _: first(), range(16)))
    assert len(keys) == 1 and len(keys.pop()) == 64
    assert [p.name for p in tmp_path.iterdir()] == [V.SECRET_FILE]
