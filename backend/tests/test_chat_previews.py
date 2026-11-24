"""A chat's pictures, videos and PDFs get the previews task files get: a thread upload is queued for the preview worker,
and once its thumbnail is made the timeline, the upload's answer and the live stream (visuals.for_reader) carry its size
and thumbnail address, only for a reader who may open the file. A source that keeps failing is given up after a few
tries, and the media tools run under backend/limited.py's limits. Fictional people only: ana and ben share a thread,
cara is in none of it."""

import io
import json
import resource
import sys

import pytest
from PIL import Image

from backend import file_metadata, limited, visuals
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.thread_api import new_thread, say


def png(width=40, height=30):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "teal").save(out, format="PNG")
    return out.getvalue()


def worker(api):
    """The preview worker, driven by the test: the server's own loop is stopped first, so it never takes a row."""
    running = api.app.state.file_metadata
    running.stop.set()
    running.wake.set()
    return file_metadata.Metadata(api.app.state.store, api.app.state.blobs)


def upload(api, thread_id, files, token="ana-test"):
    sent = api.post(
        f"/api/v2/uploads/threads/{thread_id}/messages",
        data={"text": "The mock-ups"},
        files=[("files", f) for f in files],
        headers=headers(token),
    )
    assert sent.status_code == 200, sent.text
    return sent.json()["message"]


def timeline_attachments(api, thread_id, message_id, token):
    got = api.get(f"/api/v2/threads/{thread_id}/timeline", headers=headers(token))
    if got.status_code != 200:
        return got.status_code, None
    (row,) = [m for m in got.json()["messages"] if m.get("id") == message_id]
    return 200, row["refs"]["attachments"]


def test_a_thread_picture_gets_its_thumbnail_for_readers_who_may_open_it(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    say(api, thread["id"], "Mock-ups below")  # a message with no refs reads beside them
    message = upload(api, thread["id"], [("plan.png", png(), "image/png"), ("notes.txt", b"words", "text/plain")])
    picture, notes = message["refs"]["attachments"]
    with api.app.state.store.read() as c:
        queued = {r[0]: r[1] for r in c.execute("SELECT blob_id,media_state FROM blob_media")}
    assert queued == {picture["id"]: "pending"}  # only a type the worker reads is queued
    assert "thumb_url" not in picture  # nothing made yet: the picture itself stands in

    assert drive.batch() is True
    status, (shown, text) = timeline_attachments(api, thread["id"], message["id"], "ben-test")
    assert status == 200
    assert shown["thumb_url"] == f"/api/v2/files/{picture['id']}/thumb"
    assert (shown["width"], shown["height"]) == (40, 30)
    assert shown["url"] == picture["url"]  # the viewer still opens the whole file
    assert "thumb_url" not in text and "width" not in text
    thumb = api.get(shown["thumb_url"], headers=headers("ben-test"))
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

    # Cara is not in the thread: no timeline, no thumbnail, and the live stream's decoration gives her nothing either.
    assert timeline_attachments(api, thread["id"], message["id"], "cara-test")[0] == 404
    assert api.get(shown["thumb_url"], headers=headers("cara-test")).status_code in (403, 404)
    app = api.app
    with app.state.store.read() as c:
        cara = app.state.auth.identity_for_actor(c, "human:cara")
        ben = app.state.auth.identity_for_actor(c, "human:ben")
        (as_cara,) = visuals.for_reader(app, app.state.store.settings, c, cara, [H.message(c, message["id"])])
        (as_ben,) = visuals.for_reader(app, app.state.store.settings, c, ben, [H.message(c, message["id"])])
    assert not any("thumb_url" in a or "width" in a for a in as_cara["refs"]["attachments"])
    assert as_ben["refs"]["attachments"][0]["thumb_url"] == shown["thumb_url"]


def test_a_reader_the_download_refuses_gets_nothing_of_a_files_bytes(api, monkeypatch):
    """A chat picture kept as a file version: one who may see the file but whose download of that version is refused
    (version_refusal: a scan, a fenced blob) gets no size, address, poster or thumbnail in the timeline."""
    from backend import files

    drive = worker(api)
    chat = api.post(
        "/api/v2/uploads/chat/ops",
        data={"text": "Review this"},
        files={"files": ("plan.png", png(), "image/png")},
        headers=headers("ben-test"),
    )
    assert chat.status_code == 200, chat.text
    room, message = chat.json()["conversation"]["id"], chat.json()["message"]["id"]
    drive.batch()
    _, (seen,) = timeline_attachments(api, room, message, "ben-test")
    assert seen["width"] == 40 and seen["src"] and seen["thumb_url"].endswith("/thumb?v=1")
    monkeypatch.setattr(files, "version_refusal", lambda *args: "scan")
    _, (refused,) = timeline_attachments(api, room, message, "ben-test")
    assert not set(refused) & {"width", "height", "src", "thumb_url", "poster_url", "preview_url"}


def test_a_malformed_attachment_ref_never_breaks_the_timeline(api):
    thread = new_thread(api, members=("human:ben",))
    sent = say(api, thread["id"], "Odd refs")
    bad = [{"file_id": "file-x", "version": "two"}, {"file_id": {"nested": 1}}, {"id": 5}, "loose", {"name": "no id"}]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps({"attachments": bad}), sent["id"]))
    status, shown = timeline_attachments(api, thread["id"], sent["id"], "ben-test")
    assert status == 200 and len(shown) == len(bad)
    assert shown[0] == {"file_id": "file-x", "version": "two", "removed": True}


def test_the_rules_owner_sees_a_plain_upload_as_its_id_alone(api):
    from backend.auth import Identity
    from backend.counsel import SLUG

    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.png", png(), "image/png")])
    app = api.app
    with app.state.store.read() as c:
        (seen,) = visuals.for_reader(
            app, app.state.store.settings, c, Identity("bot:" + SLUG, "bot"), [H.message(c, message["id"])]
        )
    assert seen["refs"]["attachments"] == [{"id": message["refs"]["attachments"][0]["id"]}]


def test_a_removed_message_takes_its_pictures_previews_with_it(api, monkeypatch):
    """The parent goes first: released after it, the thumbnail is no longer named by any row, so nothing is left behind.
    The thumbnail's id sorts before its parent's here, the order that once kept it for good."""
    from backend import file_library

    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.png", png(), "image/png")])
    monkeypatch.setattr(H, "new_id", lambda: "0-thumbnail")
    drive.batch()
    monkeypatch.undo()
    with api.app.state.store.transaction() as c:
        (thumb,) = c.execute("SELECT thumb_blob_id FROM blob_media").fetchone()
        assert thumb
        assert file_library.release_message_files(c, "human:ana", message["id"]) == 1
        assert c.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM blob_media").fetchone()[0] == 0


def test_a_source_that_keeps_failing_is_given_up_after_its_tries(api, monkeypatch):
    drive = worker(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
            "VALUES('stuck','human:ana','0'||hex(randomblob(31)),10,'stuck.png','image/png',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO blob_media(blob_id) VALUES('stuck')")

    def broken(blob):
        raise OSError("storage unreachable")

    monkeypatch.setattr(drive, "process", broken)
    for attempt in range(1, file_metadata.TRIES + 1):
        assert drive.batch() is True
        with api.app.state.store.transaction() as c:
            retry = c.execute("SELECT attempts FROM blob_media_retries WHERE blob_id='stuck'").fetchone()
            state = c.execute("SELECT media_state FROM blob_media WHERE blob_id='stuck'").fetchone()[0]
            if attempt < file_metadata.TRIES:
                assert (retry[0], state) == (attempt, "pending")
                c.execute("UPDATE blob_media_retries SET retry_at=0 WHERE blob_id='stuck'")  # due again now
    assert retry is None and state == "none"
    assert drive.batch() is False  # never read again


def test_a_media_tool_runs_inside_its_limits(tmp_path):
    probe = (
        "import json,os,resource;"
        "print(json.dumps({'cpu':resource.getrlimit(resource.RLIMIT_CPU)[0],"
        "'fsize':resource.getrlimit(resource.RLIMIT_FSIZE)[0],'mem':resource.getrlimit(resource.RLIMIT_AS)[0],"
        "'cwd':os.getcwd(),'env':sorted(os.environ)}))"
    )
    seen = json.loads(file_metadata.run([sys.executable, "-I", "-c", probe], tmp_path))
    assert seen["cpu"] == limited.CPU_SECONDS and seen["fsize"] == limited.FILE_BYTES
    assert seen["mem"] == (resource.RLIM_INFINITY if sys.platform == "darwin" else limited.MEMORY)
    assert seen["cwd"] == str(tmp_path.resolve())
    assert set(seen["env"]) <= set(limited.ENV) | {"__CF_USER_TEXT_ENCODING"}  # macOS adds its own
    # A tool that writes past its file limit fails the pass instead of filling the disk.
    with pytest.raises(file_metadata.DecodeError):
        file_metadata.run([sys.executable, "-I", "-c", "open('big','wb').write(b'x' * (11 << 20))"], tmp_path)
    assert (tmp_path / "big").stat().st_size <= limited.FILE_BYTES
