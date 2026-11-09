"""scripts/pytest_warning_ignores.py: the ignore list only shrinks, and the warnings gate stays in pytest.ini."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest_warning_ignores as ratchet  # noqa: E402

BASE = "[pytest]\nfilterwarnings =\n    error\n    # Library: reason\n    ignore:old:DeprecationWarning:a\n"


def test_the_same_list_passes():
    assert ratchet.problems(BASE, BASE) == []


def test_a_new_ignore_line_fails_against_a_base_with_the_gate():
    grown = BASE + "    # Library: reason\n    ignore:new:DeprecationWarning:b\n"
    found = ratchet.problems(grown, BASE)
    assert len(found) == 1 and "2 `ignore:` lines, 1 on the base" in found[0]


def test_a_base_without_the_gate_is_the_introducing_pull_request_and_passes():
    assert ratchet.problems(BASE, "[pytest]\naddopts = -q\n") == []


def test_dropping_the_gate_fails():
    assert ratchet.problems("[pytest]\nfilterwarnings =\n    ignore:old:DeprecationWarning:a\n", BASE)


def test_no_warnings_plugin_never_comes_back():
    found = ratchet.problems("[pytest]\naddopts = -p no:warnings\nfilterwarnings =\n    error\n")
    assert any("no:warnings" in line for line in found)


def test_comment_lines_inside_the_value_are_not_entries():
    values = ratchet.filter_values(
        "[pytest]\nfilterwarnings =\n    error\n    # why\n    ignore:x:DeprecationWarning:a\nmarkers =\n    slow: x\n"
    )
    assert values == ["error", "ignore:x:DeprecationWarning:a"]
