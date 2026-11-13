"""`tico` is the teamwork CLI and `hub` its old name: every check that reads a command line out of a bot's output or
text reads both the same way, so a line written with `tico` is never missed (the runner's own checks are tested in
runner-rs/src/hosts/mod.rs)."""

import pytest

from backend import execution, turns, update_feeds
from backend.hubdb_text import lint_plain


@pytest.mark.parametrize('program', ['tico', 'hub'])
@pytest.mark.parametrize(
    'summary, label',
    [
        ('{p} release now', 'publish release'),
        ('{p} support send', 'send support report'),
        ('{p} webhook send', 'send webhook'),
        ('{p} approval consume', 'consume approval'),
        ('{p} email send', 'send email'),
    ],
)
def test_an_outside_action_reads_the_same_under_either_name(program, summary, label):
    summary = summary.format(p=program)
    assert [name for name, pattern in execution.OUTSIDE_ACTIONS if pattern.search(summary)] == [label]
    assert label in execution.OUTSIDE_LABELS


@pytest.mark.parametrize('program', ['tico', 'hub'])
@pytest.mark.parametrize(
    'text', ['Run {p} task done 12 when it is in.', 'I will {p} message send ops.', 'Say yes and I {p} whoami.']
)
def test_the_ask_lint_refuses_a_command_line_under_either_name(program, text):
    problems = lint_plain(text.format(p=program))
    assert any(p.startswith('has a tool or field name: "' + program) for p in problems), problems


def test_the_ask_lint_still_lets_the_product_name_through():
    assert lint_plain('Tico tasks are on the Tasks page. Approve the new price?') == []


def test_a_release_that_changes_the_cli_is_flagged_for_bots_under_either_name():
    for body in ('`tico task show` now prints the stage.', 'The tico CLI reads --json.', '`hub task list` sorts.'):
        assert update_feeds._TOOLING.search(body), body
    assert not update_feeds._TOOLING.search('`tico-computer update` waits for turns.')
