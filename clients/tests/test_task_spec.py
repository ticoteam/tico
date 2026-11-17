"""The `tico task` commands that clients/commands/tasks.py runs itself, not through the tool's function: each sends
what the hand parser it replaced sent (clients/hubcli.py and clients/remotecli.py before the task specs)."""

import pytest

from clients.tests.helpers.cli_fixtures import run_line


def test_show_several_is_one_read(monkeypatch):
    result = run_line(["task", "show", "T1", "T2"], monkeypatch)
    assert result["tools"] == [["tico_task_show_many", {"ids": ["T1", "T2"]}]]


def test_show_one_reads_the_task(monkeypatch):
    result = run_line(["task", "show", "T1"], monkeypatch)
    assert ["GET", "tasks/T1"] in [call[:2] for call in result["http"]]


def test_show_json_is_local_and_still_reads_the_task(monkeypatch):
    result = run_line(["task", "show", "T1", "--json"], monkeypatch)
    assert result["exit"] == 0
    assert ["GET", "tasks/T1"] in [call[:2] for call in result["http"]]


def test_label_splits_comma_lists(monkeypatch):
    result = run_line(["task", "label", "T1", "--add", "a, b", "--add", "c", "--remove", "d"], monkeypatch)
    name, args = result["tools"][0]
    assert name == "tico_task_label"
    assert args["add"] == ["a", "b", "c"]
    assert args["remove"] == ["d"]


def test_link_sends_the_session_kind_and_the_wake_switch(monkeypatch):
    result = run_line(["task", "link", "T1", "https://example.com/pr/1", "--session", "--no-wake-once"], monkeypatch)
    name, args = result["tools"][0]
    assert name == "tico_task_link"
    assert args["kind"] == "session"
    assert args["wake_once"] is False


def test_link_leaves_out_what_was_not_given(monkeypatch):
    result = run_line(["task", "link", "T1", "https://example.com/pr/1"], monkeypatch)
    args = result["tools"][0][1]
    assert "kind" not in args
    assert "wake_once" not in args


def test_link_sends_on_behalf_of(monkeypatch):
    result = run_line(["task", "link", "T1", "https://example.com/pr/1", "--on-behalf-of", "M1"], monkeypatch)
    assert result["tools"][0][1]["on_behalf_of"] == "M1"


def test_unfollow_is_follow_with_unfollow_true(monkeypatch):
    result = run_line(["task", "unfollow", "T1"], monkeypatch)
    assert result["tools"][0][0] == "tico_task_follow"
    assert result["tools"][0][1]["unfollow"] is True


def test_the_request_operation_id_is_sent(monkeypatch):
    from clients.commands import tasks

    keys = []

    class Api:
        def post(self, path, body=None, key=None, **_):
            keys.append(key)
            return {"ok": True}

    monkeypatch.setenv("HUB_OPERATION_ID", "op-7")
    tasks._call(Api(), "tico_task_run", {"id": "T1"})
    assert keys == ["op-7"]


@pytest.mark.parametrize(
    "argv, tool",
    [
        (["task", "delete", "T1"], "tico_task_delete"),
        (["task", "deleted"], "tico_task_deleted"),
        (["task", "restore", "T1"], "tico_task_restore"),
        (["task", "run", "T1"], "tico_task_run"),
        (["task", "comment-delete", "T1", "C1"], "tico_task_comment_delete"),
        (["task", "comment-edit", "T1", "C1", "fixed", "--ok", "OK1"], "tico_task_comment_edit"),
        (["task", "done", "T1", "-m", "shipped"], "tico_task_done"),
        (["task", "snooze", "T1", "-m", "later"], "tico_task_snooze"),
        (["task", "file", "Write it", "--parent", "T1"], "tico_task_file"),
        (["task", "ask", "T1", "what next?"], "tico_task_ask"),
        (["task", "thumbs", "T1", "R1", "up"], "tico_task_thumbs"),
    ],
)
def test_each_command_calls_its_tool(monkeypatch, argv, tool):
    assert run_line(argv, monkeypatch)["tools"][0][0] == tool
