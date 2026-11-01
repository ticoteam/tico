"""Visuals always on 1/3 (Dev 313): without a separate visual address, pictures, video and audio still show inline,
served from the app's own address as media only; pages and SVG are cards that open the file viewer, and are never
served from the app's own address. No visual response ever sets a cookie. Spec tests 1-4 and 7 (Product roadmap
"Visuals always on"); the UI half is ui/tests/visual-fallback.cjs."""
import base64
from dataclasses import replace

import pytest

from backend import visuals as V
from backend.tests.test_api import api, headers, post  # noqa: F401  (fixtures)
from backend.tests.test_files import publish, turn
from backend.tests.test_visuals import PNG, VISUALS, bot_reply, media_settled, task_with_file, visual_on_task, wav

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
CLIP = bytes(range(256)) * 8


@pytest.fixture
def fallback(api, monkeypatch):
    """A company with no visual address: the app's own (production-like) address only."""
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "visual_origin", "")
    monkeypatch.setattr(settings, "public_url", "https://tico.example")
    return settings


def media_reply(api):
    """A bot's chat reply with a PNG, a video and an audio clip, as the reader reads it back."""
    _, attempt = turn(api)
    refs = []
    for name, data in (("chart.png", PNG), ("clip.mp4", CLIP), ("note.wav", wav())):
        made = publish(api, attempt, name=name, text=None, content_base64=base64.b64encode(data).decode())
        assert made.status_code == 200, made.text
        refs.append({"ref": made.json()["file"]["id"] + "@1", "title": name})
    sent = bot_reply(api, attempt, attachments=refs)
    assert sent.status_code == 200, sent.text
    return api.get("/api/v2/messages/" + sent.json()["id"], headers=headers("ben-test")).json()


def page_and_svg_comment(api):
    tid, page, _ = task_with_file(api, private=False, name="funnel.html", text="<p>funnel</p>")
    svg = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "logo.svg", "text": SVG}, headers=headers("ben-test"))
    assert svg.status_code == 200, svg.text
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Both", "attachments": [
        {"ref": f"{page}@1", "title": "Funnel"}, {"ref": svg.json()["file_id"] + "@1", "title": "Logo"}]},
        headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    return tid, r.json()["comment"]


def comment_items(api, tid, comment):
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("ben-test")).json()["comments"]
    return next(m for m in listed if m["id"] == comment["id"])["refs"]["attachments"]


def give_preview(api, fid, version, data=PNG, mime="image/png"):
    """A still preview stored for this file version (thumb_blob_id), as file_metadata leaves it."""
    media_settled(api)
    digest = api.app.state.blobs.put(data, mime)
    with api.app.state.store.transaction() as c:
        blob = c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES "
                         "(lower(hex(randomblob(16))),'human:ben',?,?,?,?,'2026-10-21') RETURNING id",
                         (digest, len(data), "preview", mime)).fetchone()[0]
        c.execute("UPDATE bot_file_versions SET thumb_blob_id=? WHERE file_id=? AND version=?", (blob, fid, version))


def test_without_a_visual_address_pictures_video_and_audio_show_inline_from_the_apps_own_address(api, fallback):
    """Spec 1 and 3: each gets an address on the app's own /visuals route, served with its own media type, nosniff,
    a content policy that runs no script, inline, and no cookie."""
    items = media_reply(api)["refs"]["attachments"]
    assert [(a["kind"], a["src"][:9]) for a in items] == [("image", "/visuals/"), ("video", "/visuals/"),
                                                          ("audio", "/visuals/")]
    for a, mime in zip(items, ("image/png", "video/mp4", "audio/wav")):
        assert "open" not in a
        r = api.get(a["src"])
        assert r.status_code == 200, a
        assert r.headers["content-type"].split(";")[0] == mime
        assert r.headers["x-content-type-options"] == "nosniff"
        rule = r.headers["content-security-policy"]
        assert "default-src 'none'" in rule and "sandbox" in rule and "script-src" not in rule
        assert "allow-scripts" not in rule
        assert r.headers["content-disposition"] == "inline"
        assert "set-cookie" not in r.headers
    part = api.get(items[1]["src"], headers={"Range": "bytes=0-1"})
    assert part.status_code == 206 and part.content == CLIP[:2]           # video still seeks


def test_without_a_visual_address_a_page_and_an_svg_are_open_cards_never_served_from_the_app(api, fallback):
    """Spec 2 and 3: no `src` and `open` true (a card with Open, which shows the file viewer); the route refuses a
    page or SVG on the app's own address even with a token signed for it, and the file viewer's address stays."""
    tid, comment = page_and_svg_comment(api)
    for a in comment_items(api, tid, comment):
        assert a["kind"] in ("page", "svg") and a["src"] is None and a["open"] is True, a
        assert a["title"] in ("Funnel", "Logo") and "preview_url" not in a
        assert a["url"] == f"/api/v2/files/{a['file_id']}?v={a['version']}"
        token = V.mint(api.app.state.visual_key, "human:ben", a["file_id"], a["version"])
        for method in (api.get, api.head):
            r = method("/visuals/" + token)
            assert r.status_code == 404 and not r.content and "set-cookie" not in r.headers
            assert r.headers["content-disposition"] == "attachment"


def test_a_page_with_a_still_preview_shows_it_and_the_preview_is_only_ever_a_picture(api, fallback):
    """Spec 2: "the still preview when one exists". The preview is served as a picture; one that is not a raster
    picture (an SVG or a page stored as the preview) is refused."""
    tid, comment = page_and_svg_comment(api)
    page = comment_items(api, tid, comment)[0]

    def preview(data, mime):
        give_preview(api, page["file_id"], page["version"], data, mime)
        [a, _] = comment_items(api, tid, comment)
        return a

    a = preview(PNG, "image/png")
    assert a["src"] is None and a["preview_url"].startswith("/visuals/")
    r = api.get(a["preview_url"])
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == "inline" and "set-cookie" not in r.headers
    for data, mime in ((SVG.encode(), "image/svg+xml"), (b"<p>x</p>", "text/html")):
        a = preview(data, mime)
        assert api.get(a["preview_url"]).status_code == 404


def test_a_bot_reading_its_chat_gets_page_and_svg_open_cards_without_a_preview_address_the_route_refuses(api, fallback):
    """The visual route rebuilds a bot reader without its run (Dev 319), so it cannot see a chat's files: a bot
    reading its own chat reply gets the Open card (Open uses the file viewer, not the route) and no preview address,
    even when the file has a preview. The person in the chat keeps a working preview, and so does a bot on a task."""
    _, attempt = turn(api)
    refs = []
    for name, text in (("funnel.html", "<p>funnel</p>"), ("logo.svg", SVG)):
        made = publish(api, attempt, name=name, text=text)
        assert made.status_code == 200, made.text
        give_preview(api, made.json()["file"]["id"], 1)
        refs.append({"ref": made.json()["file"]["id"] + "@1", "title": name})
    sent = bot_reply(api, attempt, attachments=refs)
    assert sent.status_code == 200, sent.text
    message = sent.json()
    for read in (message, api.get("/api/v2/messages/" + message["id"], headers=headers(attempt["token"])).json()):
        items = read["refs"]["attachments"]
        assert [a["kind"] for a in items] == ["page", "svg"]
        for a in items:
            assert a["open"] is True and not a["removed"] and a["src"] is None, a
            assert a.get("preview_url") is None, a
    for a in api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json()["refs"]["attachments"]:
        r = api.get(a["preview_url"])                         # the person's preview is unchanged
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png", a

    tid, fid, comment = visual_on_task(api, private=False)
    give_preview(api, fid, 1)
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers(attempt["token"]))
    assert listed.status_code == 200, listed.text
    [item] = next(m for m in listed.json()["comments"] if m["id"] == comment["id"])["refs"]["attachments"]
    assert item["file_id"] == fid and item["open"] is True and item["src"] is None
    assert api.get(item["preview_url"]).status_code == 200                  # a bot's task preview still opens


def test_with_a_visual_address_all_five_kinds_show_inline_there(api, monkeypatch):
    """Spec 4: a working visual address keeps Visual replies as they are: every kind has a `src` on that address."""
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", VISUALS)
    items = media_reply(api)["refs"]["attachments"]
    tid, comment = page_and_svg_comment(api)
    items += comment_items(api, tid, comment)
    assert [a["kind"] for a in items] == ["image", "video", "audio", "page", "svg"]
    for a in items:
        assert a["src"].startswith(VISUALS + "/visuals/") and "open" not in a, a
        r = api.get(a["src"])
        assert r.status_code == 200 and r.headers["content-disposition"] == "inline" and "set-cookie" not in r.headers


def test_with_a_visual_address_the_apps_own_address_still_serves_media_only(api, monkeypatch):
    """The route answers on every host the server does (a front door passes the app's host through to it), so a page
    or SVG link opened on the app's own address is refused there in full mode too; media still plays there."""
    monkeypatch.setattr(api.app.state.store.settings, "visual_origin", VISUALS)
    media = media_reply(api)["refs"]["attachments"]
    tid, comment = page_and_svg_comment(api)
    for a in comment_items(api, tid, comment):
        path = a["src"].removeprefix(VISUALS)
        for method in ("GET", "HEAD"):
            on = api.request(method, a["src"])                                          # on the visual address
            assert on.status_code == 200 and on.headers["content-disposition"] == "inline", (a["kind"], method)
            for host in ("testserver", "tico.example", "visuals.localhost", "visuals.localhost:9002", "bad host:x"):
                r = api.request(method, "http://" + host.split(" ")[0] + path, headers={"Host": host})
                assert r.status_code == 404 and not r.content, (a["kind"], method, host)
        give_preview(api, a["file_id"], a["version"])                    # its raster preview plays on the app host
        token = V.mint(api.app.state.visual_key, "human:ben", a["file_id"], a["version"], "preview")
        r = api.get("/visuals/" + token, headers={"Host": "tico.example"})
        assert r.status_code == 200 and r.content == PNG and r.headers["content-disposition"] == "inline"
    video = media[1]
    media_settled(api)
    with api.app.state.store.transaction() as c:                         # a video with its poster ready
        c.execute("UPDATE bot_file_versions SET poster_blob_id=(SELECT thumb_blob_id FROM bot_file_versions WHERE "
                  "thumb_blob_id IS NOT NULL LIMIT 1) WHERE file_id=?", (video["file_id"],))
    poster = V.mint(api.app.state.visual_key, "human:ben", video["file_id"], video["version"], "poster")
    for a, path in [(a, a["src"].removeprefix(VISUALS)) for a in media] + [(video, "/visuals/" + poster)]:
        for method in ("GET", "HEAD"):
            r = api.request(method, path, headers={"Host": "tico.example"})
            assert r.status_code == 200 and r.headers["content-disposition"] == "inline", (a["kind"], method, path)


def test_no_visual_response_ever_sets_a_cookie(api, monkeypatch):
    """No cookie on a ticofiles.com host until the Public Suffix listing is live: every response on the visual route,
    in either mode, found or not, GET or HEAD, with the app's own cookie sent along, carries no Set-Cookie."""
    settings = api.app.state.store.settings
    message, (tid, comment), seen = media_reply(api), page_and_svg_comment(api), 0
    for origin in (VISUALS, ""):
        monkeypatch.setattr(settings, "visual_origin", origin)
        items = api.get("/api/v2/messages/" + message["id"], headers=headers("ben-test")).json()["refs"]["attachments"]
        srcs = [a["src"] for a in items]
        for a in comment_items(api, tid, comment):
            srcs.append(a["src"] or "/visuals/" + V.mint(api.app.state.visual_key, "human:ben", a["file_id"], 1))
        srcs += ["/visuals/nonsense", "/visuals/" + V.mint(api.app.state.visual_key, "human:ben", "nofile", 1)]
        for src in srcs:
            for method in ("GET", "HEAD"):
                for extra in ({}, {"Range": "bytes=0-1"}, {"Cookie": "tico_session=synthetic"}):
                    r = api.request(method, src, headers=extra)
                    seen += 1
                    assert "set-cookie" not in r.headers, (method, src, extra, r.status_code)
    assert seen == 2 * 7 * 2 * 3


def test_only_pictures_video_and_audio_are_ever_inline_in_a_response():
    """Spec 3: anything else the route could name is an attachment."""
    for mime in ("image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "audio/wav", "audio/mpeg"):
        assert V.disposition(mime) == "inline", mime
    for mime in ("image/svg+xml", "text/html", "application/pdf", "application/octet-stream", "", "text/plain"):
        assert V.disposition(mime) == "attachment", mime


def test_there_is_no_switch_the_setup_alone_decides(monkeypatch, tmp_path):
    """Spec 7 (server half): the old opt-in that served pages from the app's own address is not read any more, and a
    loopback install without a visual address is the fallback like any other."""
    from backend.config import Settings
    monkeypatch.setenv("TICO_VISUAL_SAME_ORIGIN", "1")
    monkeypatch.delenv("TICO_VISUAL_ORIGIN", raising=False)
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    settings = Settings.from_env()
    assert not hasattr(settings, "visual_same_origin")
    assert settings.loopback and V.mode(settings) == "media" and settings.environment()["visuals"] == "media"
    monkeypatch.setenv("TICO_VISUAL_ORIGIN", "https://files.acme.example")
    full = Settings.from_env()
    assert V.mode(full) == "full" and full.environment()["visuals"] == "full"


def test_a_visual_address_on_the_apps_own_hostname_is_refused_whatever_the_scheme_or_port(monkeypatch, tmp_path):
    """A visual address on the app's own hostname is refused whatever its scheme or port: cookies do not separate by
    port, and a request to the app's https address must never be taken for one to a visual http address on the same
    host (or the reverse). Spelling the app's own address differently (case, default port, a path) is refused too. It
    stops the server, in check_origin and in Settings; a separate hostname is kept as given."""
    from backend.config import Settings
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_PUBLIC_URL", raising=False)
    monkeypatch.delenv("TICO_VISUAL_ORIGIN", raising=False)
    local = Settings.from_env()
    for app, same in (("https://tico.example", "https://tico.example:443"), ("https://tico.example", "https://TICO.example"),
                      ("https://tico.example:443", "HTTPS://Tico.Example/"), ("http://tico.example", "http://tico.example:80"),
                      ("https://Tico.example/app", "https://tico.EXAMPLE:443/visuals"),
                      ("https://acme.example", "http://acme.example"), ("http://acme.example", "https://acme.example"),
                      ("https://acme.example", "http://acme.example:443"), ("https://tico.example", "https://tico.example:8443"),
                      ("https://tico.example:8443", "https://tico.example"), ("http://tico.example:8080", "https://Tico.example:9443/")):
        class S:
            public_url, visual_origin = app, same
        with pytest.raises(ValueError, match="must differ"):
            V.check_origin(S)
        monkeypatch.setenv("TICO_PUBLIC_URL", app)
        monkeypatch.setenv("TICO_VISUAL_ORIGIN", same)
        with pytest.raises(ValueError, match="must differ"):
            Settings.from_env()
    for app, apart in (("https://tico.example", "https://files.tico.example"), ("https://tico.example:8080", "https://visuals.tico.example:8080"),
                       ("https://acme.example", "http://visuals.acme-files.example")):
        class S:
            public_url, visual_origin = app, apart
        assert V.check_origin(S) == apart
        assert replace(local, public_url=app, visual_origin=apart).visual_origin == apart
