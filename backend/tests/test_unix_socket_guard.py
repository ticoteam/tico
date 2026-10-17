"""The root conftest refuses a Unix socket under pytest's tmp_path (v0.3.50: the hosted gate's long TMPDIR made such a
path longer than AF_UNIX allows, and only the release gate saw it). These tests fail if that guard is removed."""
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile

import pytest

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="no Unix sockets")
ROOT_CONFTEST = str(Path(__file__).resolve().parents[2] / "conftest.py")


@pytest.fixture
def path_under_tmp_path(request):
    plugin = next(p for p in request.config.pluginmanager.get_plugins() if getattr(p, "__file__", None) == ROOT_CONFTEST)
    return plugin.unix_socket_path_under_tmp_path


@pytest.fixture
def unix_socket():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    yield s
    s.close()


@pytest.mark.parametrize("form", [str, Path, os.fsencode], ids=["str", "path", "bytes"])
def test_a_socket_under_tmp_path_is_refused(tmp_path, unix_socket, form):
    with pytest.raises(OSError, match="Unix socket under pytest's tmp_path"):
        unix_socket.bind(form(tmp_path / "s"))


def test_a_relative_socket_in_tmp_path_is_refused(tmp_path, unix_socket, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OSError, match="Unix socket under pytest's tmp_path"):
        unix_socket.bind("s")


def test_a_short_tmp_socket_and_tcp_are_allowed(unix_socket):
    directory = tempfile.mkdtemp(dir="/tmp")
    try:
        unix_socket.bind(os.path.join(directory, "s"))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
    finally:
        shutil.rmtree(directory)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="abstract socket names are Linux only")
def test_an_abstract_socket_name_is_allowed(unix_socket):
    unix_socket.bind(f"\0tico-guard-{os.getpid()}".encode())


def test_an_explicit_basetemp_matches_whole_directories(path_under_tmp_path):
    assert path_under_tmp_path("/tmp/check/popen-gw0/s", "/tmp/check") == os.path.realpath("/tmp/check/popen-gw0/s")
    assert path_under_tmp_path("/tmp/check-short/s", "/tmp/check") is None
    assert path_under_tmp_path("/tmp/pytest-of-ci/pytest-3/t0/s") is not None
    assert path_under_tmp_path(b"", None) is None and path_under_tmp_path(("127.0.0.1", 0), None) is None
