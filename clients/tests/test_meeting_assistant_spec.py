"""The `tico meeting` and `tico assistant` commands built from specs (clients/commands/calls.py MEETING,
clients/commands/assistant.py ASSISTANT): each sends what the hand parser it replaced sent (clients/hubcli.py and
clients/remotecli.py before these specs)."""

import pytest

from clients.tests.helpers.cli_fixtures import run_line


def test_meeting_writes_send_no_operation_id_even_when_one_is_set():
    from clients.commands import calls

    assert calls._no_key({"id": "m1", "operation_id": "op-7"}) == {"id": "m1"}


def test_meeting_search_sends_the_hand_parsers_defaults(monkeypatch):
    result = run_line(["meeting", "search"], monkeypatch)
    assert result["tools"] == [["tico_meeting_search", {"limit": 20, "offset": 0, "person": "", "q": ""}]]
    assert result["http"][0][1] == "meetings/search?q=&person=&limit=20&offset=0"


def test_meeting_read_sends_notes_only_when_asked(monkeypatch):
    assert run_line(["meeting", "read", "m1"], monkeypatch)["tools"][0][1] == {"id": "m1", "limit": 20000, "offset": 0}


def test_meeting_link_takes_task_ids_and_no_kind(monkeypatch):
    result = run_line(["meeting", "link", "m1", "T1", "T2"], monkeypatch)
    assert result["tools"] == [["tico_meeting_link", {"id": "m1", "task_ids": ["T1", "T2"]}]]
    assert run_line(["meeting", "link", "m1", "T1", "--kind", "sent"], monkeypatch)["exit"] != 0


def test_meeting_approve_needs_an_id_or_all(monkeypatch):
    assert run_line(["meeting", "approve"], monkeypatch)["exit"] != 0
    result = run_line(["meeting", "approve", "m1"], monkeypatch)
    assert result["tools"] == [["tico_meeting_approve", {"all": False, "id": "m1"}]]
    assert result["http"][-1] == ["POST", "meetings/m1/review", {"action": "approve"}]


def test_meeting_import_stays_hand_built_and_reads_the_file(monkeypatch, tmp_path):
    transcript = tmp_path / "standup.txt"
    transcript.write_text("Ana: hello\n", encoding="utf-8")
    result = run_line(["meeting", "import", str(transcript)], monkeypatch)
    assert result["exit"] == 0
    method, path, body = result["http"][-1]
    assert (method, path) == ("POST", "meetings/import")
    assert body["transcript"] == "Ana: hello\n" and body["title"] == "standup"
    assert body["source"] == "upload" and body["format"] == "auto"


def test_assistant_propose_sends_every_card_field(monkeypatch):
    result = run_line(["assistant", "propose", "--summary", "S", "--path", "/api/v2/x"], monkeypatch)
    name, sent = result["tools"][0]
    assert name == "tico_assistant_propose"
    assert sent == {
        "body": {},
        "for_person": None,
        "method": "POST",
        "operation_id": None,
        "path": "/api/v2/x",
        "summary": "S",
    }


def test_assistant_propose_parses_body_and_names_the_person(monkeypatch):
    argv = ["assistant", "propose", "--summary", "S", "--path", "/p", "--body", '{"a": 1}', "--for", "human:ana"]
    result = run_line(argv, monkeypatch)
    assert result["http"][-1] == [
        "POST",
        "assistant/actions",
        {"summary": "S", "method": "POST", "path": "/p", "body": {"a": 1}, "for": "human:ana"},
    ]


def test_assistant_propose_refuses_a_body_that_is_not_json(monkeypatch):
    result = run_line(["assistant", "propose", "--summary", "S", "--path", "/p", "--body", "nope"], monkeypatch)
    assert result["exit"] == 2
    assert '"detail": "--body must be JSON"' in result["stdout"]
    assert result["http"] == []


@pytest.mark.parametrize(
    "argv, tool, sent",
    [
        (["assistant", "learn", "x"], "tico_assistant_learn", {"layer": "personal", "text": "x"}),
        (["assistant", "learn", "x", "--company"], "tico_assistant_learn", {"layer": "company", "text": "x"}),
        (["assistant", "withdraw", "a1"], "tico_assistant_withdraw", {"id": "a1", "reason": ""}),
        (
            ["assistant", "morning", "--for", "human:ana", "--body", "Hi"],
            "tico_assistant_morning",
            {"body": "Hi", "for_person": "human:ana"},
        ),
        (["assistant", "change", "list", "--days", "7"], "tico_assistant_change_list", {"days": 7}),
        (["assistant", "proposals", "--proposer", "botops"], "tico_assistant_proposals", {"proposer": "botops"}),
    ],
)
def test_each_assistant_command_calls_its_tool(monkeypatch, argv, tool, sent):
    name, args = run_line(argv, monkeypatch)["tools"][0]
    assert name == tool
    assert args == {**sent, "operation_id": None}
