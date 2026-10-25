"""Tico file hosting 1b, the app half (Dev 458): a page or SVG held in Tico's bucket shows inline from the company's
ticofiles.com address, from a cleaned copy stored beside it with the policy slots the edge reads (Dev 457, docs/files.md),
through a five-minute signed link handed out only after the reader is checked. Fake S3, HQ and edge: no network."""
import base64
import hashlib
import hmac
import time
import urllib.parse
import urllib.request

from backend import file_metadata, tico_storage
from backend import visuals as V
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_storage_delivery import task
from backend.tests.test_tico_storage import ADDRESS, SIGNING, Owner, on, tico  # noqa: F401
from backend.tests.test_visuals import task_with_file

PAGE = '<p>funnel</p><img src="https://tracker.example/p.gif"><script>steal()</script>'
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def page_and_svg(api, tid=None, private=False):
    """A page and an SVG on a task, posted on a comment: (task id, {name: file id}, the comment)."""
    if tid is None:
        tid, page, _ = task_with_file(api, private=private, name="funnel.html", text=PAGE)
    else:
        made = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "funnel.html", "text": PAGE}, headers=headers("ben-test"))
        assert made.status_code == 200, made.text
        page = made.json()["file_id"]
    svg = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "logo.svg", "text": SVG}, headers=headers("ben-test"))
    assert svg.status_code == 200, svg.text
    svg = svg.json()["file_id"]
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Both", "attachments": [
        {"ref": f"{page}@1", "title": "Funnel"}, {"ref": f"{svg}@1", "title": "Logo"}]}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    return tid, {"page": page, "svg": svg}, r.json()["comment"]


def items(api, tid, comment, who="ben-test"):
    listed = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers(who))
    assert listed.status_code == 200, listed.text
    return {a["kind"]: a for a in next(m for m in listed.json()["comments"] if m["id"] == comment["id"])["refs"]["attachments"]}


def visuals_in(bucket):
    return {k: v for k, v in bucket.objects.items() if k.startswith("c/acme/visuals/")}


def check_link(location):
    """The signed link's path, checked as the edge does (Dev 457 R6): host, path and expiry under HMAC."""
    link = urllib.parse.urlsplit(location)
    query = dict(urllib.parse.parse_qsl(link.query))
    assert (link.scheme, link.netloc) == ("https", ADDRESS) and link.path.startswith("/visuals/"), location
    name = link.path.rsplit("/", 1)[1]
    assert link.path == f"/visuals/{name[:2]}/{name}" and len(name) == 64
    assert 0 < int(query["e"]) - time.time() <= tico_storage.LINK_SECONDS and query["k"] == "k1"
    mac = hmac.new(SIGNING.encode(), f"{ADDRESS}\n{link.path}\n{query['e']}".encode(), hashlib.sha256).digest()
    assert query["s"] == base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    assert "tico-files-us" not in location and "amazonaws" not in location
    return link.path


def follow(api, src, who_ok=True):
    sent = api.get(src, follow_redirects=False)
    assert sent.status_code == 302, (sent.status_code, sent.text)
    assert sent.headers["cache-control"] == "private, no-store" and sent.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in sent.headers
    return sent.headers["location"], check_link(sent.headers["location"])


def test_a_page_and_an_svg_uploaded_to_tico_storage_show_inline_from_the_companys_address(api, tico):
    """Spec 1b: stored there at upload with a cleaned copy beside it, typed, inline and with the policy slots; the
    reader gets a `src` on the app's own route, which sends them to a signed link to the copy. Making and checking
    the copy reads nothing back through the edge, so nothing is counted as delivered for it."""
    made, hq, sync = tico
    sync(on())
    settings = api.app.state.store.settings
    assert settings.visual_origin == "", "a self-hosted company with no visual address of its own"
    tid, files, comment = page_and_svg(api)
    bucket = made["AKIATICOHOSTING0001"]
    copies = visuals_in(bucket)
    # Each page and SVG stored there: the page as uploaded, the version posting it made (references removed) and the SVG.
    assert len(copies) == 3, sorted(bucket.objects)
    delivered = hq.edge.delivered.get(ADDRESS, 0)

    class Unread:                                        # a read there is through the company's address, as delivery
        directory = api.app.state.blobs.directory

        def open_range(self, *args, **kwargs):
            raise AssertionError("the media worker read an SVG it derives nothing from")
    with api.app.state.store.read() as c:
        svg = dict(c.execute("SELECT *, NULL AS poster_blob_id FROM blobs WHERE content_type='image/svg+xml'").fetchone())
    file_metadata.Metadata(api.app.state.store, Unread()).process(svg)

    shown = items(api, tid, comment)
    for kind in ("page", "svg"):
        a = shown[kind]
        assert a["src"] and a["src"].startswith("/visuals/") and "open" not in a, a
        location, path = follow(api, a["src"])
        key = "c/acme" + path
        assert key in copies, (key, sorted(copies))
        body = copies[key]
        assert b"tracker.example" not in body and b"steal()" not in body, "the cleaned bytes, not the upload"
        kind_type, how = bucket.meta[key]
        meta = bucket.metadata[key]
        assert how == "inline" and meta["tico-visual-policy-version"] == "1"
        assert meta["sha256"] == hashlib.sha256(body).hexdigest()
        assert meta["tico-visual-ancestors"].split() == [x for x in V.ancestors(settings)
                                                         if tico_storage.ANCESTOR.match(x)][:8]
        if kind == "page":
            assert kind_type == "text/html; charset=utf-8"
            assert meta["tico-visual-script"] == "'" + V.REPORTER_HASH + "'"
            assert body.endswith(b"<script>" + V.REPORTER.encode() + b"</script>"), "the height reporter is appended"
        else:
            assert kind_type == "image/svg+xml" and "tico-visual-script" not in meta
            assert body == V.clean(SVG.encode(), "svg")[0]
        # The edge serves the copy on that link.
        served = hq.edge(urllib.request.Request(location), 60).read()
        assert served == body
        delivered += len(served)                         # this test's own read on the link, counted as delivery

    assert hq.edge.delivered.get(ADDRESS, 0) == delivered, \
        "made at upload and checked by S3's own SHA-256: nothing read back through the edge for it"
    # The app's own route never serves the page itself: a HEAD is sent on too, and nothing on it streams the bytes.
    assert api.head(shown["page"]["src"], follow_redirects=False).status_code == 302


def test_a_person_without_access_gets_no_link_and_access_is_checked_on_every_request(api, tico):
    made, hq, sync = tico
    sync(on())
    tid, files, comment = page_and_svg(api, private=True)
    for kind, fid in files.items():
        forged = V.mint(V.key_for(api.app, api.app.state.store.settings), "human:cara", fid, 1)
        refused = api.get("/visuals/" + forged, follow_redirects=False)
        assert refused.status_code == 404 and "location" not in refused.headers, kind
        assert ADDRESS not in refused.text
    refused = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("cara-test"))
    assert refused.status_code in (403, 404) and ADDRESS not in refused.text
    shown = items(api, tid, comment)
    follow(api, shown["page"]["src"])
    # Ben loses the task (moved to someone else, private): his address stops working on the next request.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET archived=1 WHERE id=?", (files["page"],))
    gone = api.get(shown["page"]["src"], follow_redirects=False)
    assert gone.status_code == 404 and "location" not in gone.headers


def test_an_old_page_in_tico_storage_gets_its_copy_on_first_view(api, tico):
    """A page uploaded there before 1b has no copy: the first view makes it (reading the page once through the
    company's address, as the view itself would) and later views use it."""
    made, hq, sync = tico
    sync(on())
    tid, files, comment = page_and_svg(api)
    bucket = made["AKIATICOHOSTING0001"]
    for key in list(visuals_in(bucket)):                 # as if stored before this change
        del bucket.objects[key]
    api.app.state.blobs._edge_seen.clear()
    shown = items(api, tid, comment)
    assert shown["page"]["src"] and shown["svg"]["src"], "still inline: the copy can be made now"
    before = hq.edge.delivered.get(ADDRESS, 0)
    follow(api, shown["page"]["src"])
    follow(api, shown["svg"]["src"])
    assert len(visuals_in(bucket)) == 2
    first = hq.edge.delivered[ADDRESS] - before
    assert first == shown["page"]["size"] + shown["svg"]["size"], "each posted version read once, to clean it"
    follow(api, shown["page"]["src"])
    assert hq.edge.delivered[ADDRESS] - before == first, "later views use the copy"


def test_move_existing_files_stores_the_copy_and_until_then_a_page_on_disk_stays_a_card(api, tico):
    made, hq, sync = tico
    tid, files, comment = page_and_svg(api)              # on the server's disk, before file hosting
    sync(on())
    shown = items(api, tid, comment)
    for kind in ("page", "svg"):
        assert shown[kind]["src"] is None and shown[kind]["open"] is True, "not in Tico's bucket yet: a card"
        token = V.mint(V.key_for(api.app, api.app.state.store.settings), "human:ben", shown[kind]["file_id"],
                       shown[kind]["version"])
        assert api.get("/visuals/" + token, follow_redirects=False).status_code == 404
    size = api.get("/api/v2/system/storage/move", headers=headers()).json()["estimate"]["bytes"]
    delivered = dict(hq.edge.delivered)
    tico_storage.start_move(api.app.state.store, api.app.state.blobs, Owner(), size, wait=True)
    assert hq.edge.delivered == delivered, "the copies are made from the bytes moved, nothing read back"
    bucket = made["AKIATICOHOSTING0001"]
    assert len(visuals_in(bucket)) == 3, "the page as uploaded, the version posted and the SVG"
    shown = items(api, tid, comment)
    for kind in ("page", "svg"):
        follow(api, shown[kind]["src"])
    assert hq.edge.delivered == delivered


def test_a_copy_that_does_not_check_out_is_never_linked(api, tico):
    """The company holds write keys for its folder: an object already at the copy's path that is not the copy (other
    bytes, type or policy slots) is not linked, and the app's own address still doesn't serve the page."""
    made, hq, sync = tico
    sync(on())
    tid, files, comment = page_and_svg(api)
    bucket = made["AKIATICOHOSTING0001"]
    shown = items(api, tid, comment)
    _, path = follow(api, shown["page"]["src"])
    key = "c/acme" + path
    copy = bucket.objects[key]
    bucket.objects[key] = b"<script>evil()</script>"
    api.app.state.blobs._edge_seen.clear()
    refused = api.get(shown["page"]["src"], follow_redirects=False)
    assert refused.status_code == 404 and "location" not in refused.headers
    assert b"evil" not in refused.content
    bucket.objects[key] = copy
    for meta in ({"tico-visual-ancestors": "*"}, {"tico-visual-script": "'unsafe-inline'"},
                 {"tico-visual-sandbox": "allow-same-origin"}):
        saved = dict(bucket.metadata[key])
        bucket.metadata[key].update(meta)
        api.app.state.blobs._edge_seen.clear()
        assert api.get(shown["page"]["src"], follow_redirects=False).status_code == 404, meta
        bucket.metadata[key] = saved
    api.app.state.blobs._edge_seen.clear()
    follow(api, shown["page"]["src"])


def test_when_credits_run_out_pages_with_a_copy_stay_inline_and_one_without_is_a_card(api, tico):
    """No copy is made while Tico's bucket takes no new files; one made before keeps showing."""
    made, hq, sync = tico
    sync(on())
    tid, files, comment = page_and_svg(api)
    bucket = made["AKIATICOHOSTING0001"]
    svg_path = follow(api, items(api, tid, comment)["svg"]["src"])[1]
    page_path = follow(api, items(api, tid, comment)["page"]["src"])[1]
    del bucket.objects["c/acme" + page_path]
    api.app.state.blobs._edge_seen.clear()
    sync(on("credits_out"))
    shown = items(api, tid, comment)
    assert shown["page"]["src"] is None and shown["page"]["open"] is True
    assert follow(api, shown["svg"]["src"])[1] == svg_path
    puts = len(bucket.calls)
    token = V.mint(V.key_for(api.app, api.app.state.store.settings), "human:ben", files["page"],
                   shown["page"]["version"])
    assert api.get("/visuals/" + token, follow_redirects=False).status_code == 404
    assert len(bucket.calls) == puts, "nothing written to a place that takes no new files"


def test_pictures_still_redirect_and_other_files_are_unchanged(api, tico):
    """1a is unchanged: the copy is made for pages and SVG only, never for other files."""
    made, hq, sync = tico
    sync(on())
    tid = task(api)
    made_file = api.post(f"/api/v2/tasks/{tid}/files", files={"file": ("notes.txt", b"plain words")},
                         headers=headers("ana-test"))
    assert made_file.status_code == 200
    assert not visuals_in(made["AKIATICOHOSTING0001"])
    assert tico_storage.visual_kind("text/plain", 10) is None and tico_storage.visual_kind("image/png", 10) is None
    assert tico_storage.visual_kind("text/html", V.LIMITS["page"] + 1) is None

