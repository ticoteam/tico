"""A release candidate's Recorder is offered to installed Recorders only when it is newer."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "semver_newer", Path(__file__).resolve().parents[1] / "ci" / "semver_newer.py"
)
semver_newer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(semver_newer)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0.6.0-rc.20", "0.5.215", True),
        ("0.6.0-rc.20", "0.6.0-rc.9", True),
        ("0.6.0-rc.9", "0.6.0-rc.20", False),
        ("0.6.0-rc.20", "0.6.0-rc.20", False),
        ("0.6.0", "0.6.0-rc.20", True),
        ("0.6.0-rc.21", "0.6.0", False),
        ("0.5.217", "0.6.0-rc.20", False),
        ("0.6.0-rc.1", "", True),
        ("0.6.0-rc.1", "garbage", True),
        ("garbage", "0.5.1", False),
    ],
)
def test_newer_follows_semver(a, b, expected):
    assert semver_newer.newer(a, b) is expected
