"""A chat's pictures, videos and PDFs get the previews task files get: a thread upload is queued for the preview worker,
and the chat draws a picture's /thumb, which checks its reader as the file itself does. A source that keeps failing is
given up after a few tries, the media tools run under backend/limited.py's limits, and a message's malformed or refused
file never breaks or leaks through the live stream's decoration (visuals.for_reader). Fictional people only: ana and
ben share a thread, cara is in none of it."""

import io
import json
import resource
import sys

import pytest
from PIL import Image

from backend import file_metadata, limited, visuals
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.thread_api import new_thread


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


def as_reader(api, actor, messages):
    app = api.app
    with app.state.store.read() as c:
        who = app.state.auth.identity_for_actor(c, actor)
        return visuals.for_reader(app, app.state.store.settings, c, who, messages)


def test_a_thread_picture_is_queued_and_its_thumbnail_opens_only_for_its_readers(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.png", png(), "image/png"), ("notes.txt", b"words", "text/plain")])
    picture, _ = message["refs"]["attachments"]
    with api.app.state.store.read() as c:
        queued = {r[0]: r[1] for r in c.execute("SELECT blob_id,media_state FROM blob_media")}
    assert queued == {picture["id"]: "pending"}  # only a type the worker reads is queued
    thumb = picture["url"] + "/thumb"
    assert api.get(thumb, headers=headers("ben-test")).status_code == 404  # not made yet: the chat draws the file
    assert drive.batch() is True
    made = api.get(thumb, headers=headers("ben-test"))
    assert made.status_code == 200 and made.headers["content-type"] == "image/jpeg"
    assert api.get(thumb, headers=headers("cara-test")).status_code in (403, 404)  # not in the thread


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


def test_a_reader_the_download_refuses_gets_nothing_of_a_versions_bytes(api, monkeypatch):
    """A chat picture kept as a file version: one who may see the file but whose download of that version is refused
    (version_refusal: a scan, a fenced blob) gets no size, address or poster from the live stream's decoration."""
    from backend import files

    drive = worker(api)
    chat = api.post(
        "/api/v2/uploads/chat/ops",
        data={"text": "Review this"},
        files={"files": ("plan.png", png(), "image/png")},
        headers=headers("ben-test"),
    )
    assert chat.status_code == 200, chat.text
    mid = chat.json()["message"]["id"]
    drive.batch()
    with api.app.state.store.read() as c:
        stored = H.message(c, mid)
    (seen,) = as_reader(api, "human:ben", [dict(stored)])[0]["refs"]["attachments"]
    assert seen["width"] == 40 and seen["src"]
    # The chat draws <url>/thumb, by the blob's address or the version's (`?v=`): both reach the version's thumbnail.
    by_blob = seen["url"] + "/thumb"
    by_version = f"/api/v2/files/{seen['file_id']}/thumb?v={seen['version']}"
    for thumb in (by_blob, by_version):
        assert api.get(thumb, headers=headers("ben-test")).status_code == 200, thumb
    monkeypatch.setattr(files, "version_refusal", lambda *args: "scan")
    (refused,) = as_reader(api, "human:ben", [dict(stored)])[0]["refs"]["attachments"]
    assert not set(refused) & {"width", "height", "duration_ms", "src", "poster_url", "preview_url"}


def test_malformed_attachment_refs_never_break_the_live_streams_decoration(api):
    thread = new_thread(api, members=("human:ben",))
    bad = [
        {"file_id": "file-x", "version": "two", "kind": "image"},
        {"file_id": "file-x", "version": 2**70, "kind": "image"},
        {"file_id": "file-x", "version": -1, "kind": "image"},
        {"file_id": "file-x", "version": True, "kind": "image"},
        {"file_id": "file-x", "version": 1.5, "kind": "image"},
        {"file_id": {"nested": 1}, "kind": "image"},
        "loose",
    ]
    messages = [
        {"id": "m1", "conversation_id": thread["id"], "refs": {"attachments": bad}},
        {"id": "m2", "conversation_id": thread["id"], "refs": {"attachments": "not a list"}},
        {"id": "m3", "conversation_id": thread["id"], "refs": "{}"},
    ]
    first, second, third = as_reader(api, "human:ben", messages)
    assert [a.get("removed") for a in first["refs"]["attachments"][:6]] == [True] * 6
    assert "attachments" not in second["refs"] and third["refs"] == "{}"


def test_the_wrapper_refuses_a_bad_call_and_sets_every_limit_before_it_becomes_the_tool(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(limited.resource, "setrlimit", lambda kind, value: seen.append(kind))
    monkeypatch.setattr(limited.os, "chdir", lambda path: seen.append(("cwd", path)))
    monkeypatch.setattr(limited.os, "execve", lambda program, args, env: seen.append(("exec", program, args, env)))
    assert limited.main([str(tmp_path), "relative/tool"]) == 2 and not seen
    limited.main([str(tmp_path), "/bin/true", "-v"])
    assert seen[0] == ("cwd", str(tmp_path)) and seen[-1] == ("exec", "/bin/true", ["/bin/true", "-v"], limited.ENV)
    assert {resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_CORE} <= {
        k for k in seen if not isinstance(k, tuple)
    }


def test_a_version_is_a_whole_number_or_its_digits():
    assert visuals.version_of({"file_id": "file-x", "version": "2"}) == ("file-x", 2)
    assert visuals.version_of({"file_id": "file-x"}) == ("file-x", 0)
