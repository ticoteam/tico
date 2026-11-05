"""Bot video and audio uploads stream to disk up to the server's limit; every other kind keeps 25 MB.

The media bodies are synthetic: an MP4 header followed by zeros, so nothing large is committed. See docs/files.md.
"""

import hashlib

from backend.tests.test_files import turn
from backend.tests.test_api import api, headers  # noqa: F401  (fixtures)

MB = 1024 * 1024
MP4_HEAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def video(size):
    return MP4_HEAD + b"\x00" * (size - len(MP4_HEAD))


def raw_upload(api, token, path, data):
    return api.post(path, content=data, headers={**headers(token), "Content-Type": "application/octet-stream"})


def test_a_100_mb_video_streams_in_and_is_versioned_by_its_digest(api):
    _, attempt = turn(api, "ops")
    data = video(100 * MB)
    made = raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=cut.mp4&title=Cut", data)
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    with api.app.state.store.read() as c:
        version = c.execute("SELECT digest, size FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()
    assert (version["digest"], version["size"]) == (hashlib.sha256(data).hexdigest(), len(data))


def test_video_over_the_server_limit_is_refused_while_streaming(api):
    api.app.state.store.settings.upload_max_bytes = MB
    _, attempt = turn(api, "ops")
    made = raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=long.mp4", video(2 * MB))
    assert made.status_code == 413, made.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM bot_file_versions").fetchone()[0] == 0


def test_media_must_start_with_the_container_its_name_says(api):
    _, attempt = turn(api, "ops")
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
    assert raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=clip.mp4", wav).status_code == 422
    assert raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=clip.wav", MP4_HEAD).status_code == 422
    assert raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=clip.wav", wav).status_code == 200


def test_other_kinds_keep_the_25_mb_cap(api):
    _, attempt = turn(api, "ops")
    big = b"%PDF-1.4\n" + b"0" * (25 * MB)     # over this kind's cap, under the body cap of the route
    assert raw_upload(api, attempt["token"], "/api/v2/files/uploads?name=big.pdf", big).status_code == 422


def test_s3_import_of_a_video_streams_the_same_way(api):
    _, attempt = turn(api, "ops")
    data = video(3 * MB)
    made = raw_upload(api, attempt["token"],
                      "/api/v2/files/imports?source=s3://acme-media/clips/cut.mp4&etag=v1", data)
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT digest FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0] == hashlib.sha256(data).hexdigest()
