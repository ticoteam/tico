"""`tico file publish` and `tico file import` for video and audio: streamed from disk, refused for an older server.

The large files are sparse (an MP4 or WAV header, then a truncate), so nothing big is written or committed.
"""

import pytest

from clients import bot_files as BF
from clients import hubtools
from clients.tests.helpers.wire import Wire

MB = 1024 * 1024
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


def sparse(path, head, size):
    with open(path, "wb") as stream:
        stream.write(head)
        stream.truncate(size)
    return path


def wire(features):
    """A hub that says what its features are and takes an upload."""
    return Wire({"GET config": {"features": features}, "POST files/uploads": {"file": {"id": "f1"}}})


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "reports").mkdir(parents=True)
    monkeypatch.chdir(root)  # not a git checkout, so the command's checkout is this folder
    return root


def test_a_100_mb_video_is_sent_from_the_file_not_read_whole(checkout):
    sparse(checkout / "reports" / "cut.mp4", MP4, 100 * MB)
    hub = wire({"file_media_stream": True})
    assert hubtools.files_publish_path(hub.client(), {"path": "reports/cut.mp4"}) == {"file": {"id": "f1"}}
    (sent,) = hub.writes()
    assert sent.method == "POST" and sent.path.startswith("/api/v2/files/uploads?") and "name=cut.mp4" in sent.path
    assert sent.headers["content-length"] == str(100 * MB)
    assert sent.body == {"head": MP4[:16]}  # a stream of the file: its bytes were never a bytes object


def test_an_older_server_refuses_a_large_video_before_reading_it(checkout):
    path = sparse(checkout / "reports" / "cut.mp4", MP4, 30 * MB)
    path.chmod(0)  # unreadable: a read would fail, so the refusal must come before any read
    hub = wire({})
    result = hubtools.files_publish_path(hub.client(), {"path": "reports/cut.mp4"})
    assert result["refused"] == "file" and "25 MB" in result["detail"]
    assert hub.writes() == []


def test_an_older_server_still_gets_a_small_video_as_bytes(checkout):
    sparse(checkout / "reports" / "clip.mp4", MP4, 1 * MB)
    hub = wire({})
    hubtools.files_publish_path(hub.client(), {"path": "reports/clip.mp4"})
    (sent,) = hub.writes()
    assert isinstance(sent.body, bytes) and len(sent.body) == 1 * MB


def test_a_renamed_video_is_refused_before_any_bytes_are_sent(checkout):
    sparse(checkout / "reports" / "cut.mp4", WAV, 30 * MB)
    hub = wire({"file_media_stream": True})
    result = hubtools.files_publish_path(hub.client(), {"path": "reports/cut.mp4"})
    assert result["refused"] == "file" and "first bytes" in result["detail"]
    assert hub.writes() == []


def test_other_kinds_keep_the_25_mb_cap(checkout):
    sparse(checkout / "reports" / "big.csv", b"a,b\n", 25 * MB + 1)
    hub = wire({"file_media_stream": True})
    result = hubtools.files_publish_path(hub.client(), {"path": "reports/big.csv"})
    assert result["refused"] == "file"
    assert hub.writes() == []


@pytest.mark.parametrize(
    "name, head, ok",
    [
        ("voice.m4a", MP4, True),
        ("clip.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 12, True),
        ("clip.webm", MP4, False),
        ("song.mp3", b"ID3\x04\x00", True),
        ("song.mp3", b"\xff\xfb\x90\x00", True),
        ("clip.wav", WAV, True),
        ("clip.ogg", b"OggS\x00", True),
        ("clip.aac", b"\xff\xf1\x50\x80", True),
        ("clip.aac", b"\xff\xd8\xff", False),
        ("notes.md", b"# not media", True),
        ("cut.mov", b"\x00\x00\x00\x08skip" + b"\x00" * 8, True),
        ("cut.mov", b"\x00\x00\x00\x08pnot" + b"\x00" * 8, True),
    ],
)
def test_media_must_start_with_its_container(name, head, ok):
    assert BF.media_matches(name, head) is ok


class FakeS3:
    """A boto3-shaped client whose object is 30 MB of zeros, served in chunks."""

    class Body:
        def __init__(self, size):
            self.left = size

        def read(self, n=-1):
            take = self.left if n is None or n < 0 else min(n, self.left)
            self.left -= take
            return b"\x00" * take

    def head_object(self, Bucket, Key):
        return {"ContentLength": 30 * MB, "ETag": '"abc"'}

    def get_object(self, Bucket, Key, **extra):
        return {"Body": self.Body(30 * MB)}


def test_an_s3_video_is_copied_to_a_file_and_a_bytes_import_refuses_it(tmp_path):
    name, _, path, etag = BF.fetch_s3_file("s3://acme-media/clips/cut.mp4", tmp_path, client=FakeS3())
    assert (name, etag, path.stat().st_size) == ("cut.mp4", "abc", 30 * MB)
    with pytest.raises(BF.Refused):
        BF.fetch_s3("s3://acme-media/clips/cut.mp4", client=FakeS3())
