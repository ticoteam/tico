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
    plugin = next(
        p for p in request.config.pluginmanager.get_plugins() if getattr(p, "__file__", None) == ROOT_CONFTEST
    )
    return plugin.unix_socket_path_under_tmp_path


@pytest.fixture
def unix_socket():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    yield s
    s.close()
