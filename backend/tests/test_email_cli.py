"""The email CLI and MCP tools read through the real API, with the caller's identity."""

import json
import shlex

import pytest

from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_automation_cli import Client
from backend.tests.test_gmail_live import BOX, NEXT, gmail_http  # noqa: F401
from backend.tests.test_google_workspace_authz import workspace_key  # noqa: F401
from backend.tests.test_mail_live import SHARED, assistant_token, live, no_cached_tokens  # noqa: F401
from backend.tests.test_mcp import call
from clients import hubcli, hubtools, remotecli


@pytest.fixture
def email_cli(api, live, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setattr(remotecli, "Client", lambda url, token, **kwargs: Client(api, token))

    def run(token, *words):
        monkeypatch.setenv("HUB_TOKEN", token)
        return hubcli.main(["email", *words])

    return run


@pytest.mark.parametrize("caller", ["person", "assistant"])
def test_email_commands_reach_the_api_as_the_person_or_their_assistant(email_cli, live, capsys, assistant_token, caller):
    token = "ana-test" if caller == "person" else assistant_token
    assert email_cli(token, "mailboxes", "--json") == 0
    boxes = json.loads(capsys.readouterr().out)["mailboxes"]
    assert {BOX, SHARED} <= {box["address"] for box in boxes}
    assert email_cli(token, "inbox", SHARED, "--q", "from:sender OR is:unread", "--limit", "1", "--json") == 0
    page = json.loads(capsys.readouterr().out)
    assert page["next_cursor"] == NEXT and "body" not in page["messages"][0]
    assert live["calls"][0][1]["q"] == ['(from:sender OR is:unread) label:"INBOX"']
    assert live["calls"][0][1]["maxResults"] == ["1"]
    assert email_cli(token, "search", BOX, "newer_than:7d -label:spam", "--cursor", NEXT, "--json") == 0
    assert json.loads(capsys.readouterr().out)["messages"][0]["msg_id"] == "m1"
    assert live["calls"][-2][1]["q"] == ["(newer_than:7d -label:spam)"]
    assert email_cli(token, "thread", SHARED, "t1", "--json") == 0
    assert len(json.loads(capsys.readouterr().out)["messages"]) == 2
    assert email_cli(token, "message", SHARED, "m1", "--json") == 0
    assert json.loads(capsys.readouterr().out)["body"] == "Live contract body."


def test_email_markdown_includes_ids_bodies_and_paging(email_cli, capsys, assistant_token):
    assert email_cli(assistant_token, "inbox", SHARED) == 0
    text = capsys.readouterr().out
    assert "## A contract" in text and "Message: m2 · Thread: t1" in text
    assert "Next cursor: " + NEXT in text
    assert email_cli(assistant_token, "message", SHARED, "m1") == 0
    text = capsys.readouterr().out
    assert "Live contract body." in text and "contract.pdf" in text


def test_email_cli_returns_refusal_for_a_member_without_mailbox_access(email_cli, live, capsys):
    assert email_cli("cara-test", "inbox", SHARED, "--json") == 2
    assert "not visible" in json.loads(capsys.readouterr().out)["detail"]
    assert live["calls"] == []


def test_email_mcp_reads_share_the_same_rights_and_are_offered_to_the_assistant(api, live, assistant_token):
    names = {"hub_email_" + action for action in ("mailboxes", "inbox", "search", "thread", "message")}
    assert names <= {tool["name"] for tool in hubtools.listing(kind="assistant")}
    assert not names & {tool["name"] for tool in hubtools.listing(kind="bot")}
    for name, args in (("mailboxes", {}), ("inbox", {"mailbox": SHARED}),
                       ("search", {"mailbox": BOX, "q": "from:sender"}),
                       ("thread", {"mailbox": SHARED, "id": "t1"}),
                       ("message", {"mailbox": SHARED, "id": "m1"})):
        error, result = call(api, "hub_email_" + name, args, token=assistant_token)
        assert not error, result
    error, result = call(api, "hub_email_message", {"mailbox": SHARED, "id": "m1"}, token="cara-test")
    assert error and result["error"] == "forbidden"


def test_archive_cli_reports_count_and_a_runnable_undo(email_cli, live, capsys, assistant_token):
    assert email_cli(assistant_token, "archive", BOX, "m1", "m2") == 0
    text = capsys.readouterr().out.strip()
    assert text == f"Archived 2 messages in {BOX}. Undo: hub email unarchive {BOX} m1 m2"
    assert email_cli(assistant_token, *shlex.split(text.split("Undo: hub email ")[1])) == 0
    assert capsys.readouterr().out.startswith(f"Unarchived 2 messages in {BOX}.")
    assert live["writes"][-1][1] == {"ids": ["m1", "m2"], "addLabelIds": ["INBOX"], "removeLabelIds": []}


def test_organize_cli_query_limits_json_and_mark_read_commands(email_cli, live, capsys):
    assert email_cli("ana-test", "archive", SHARED, "--query", "in:inbox category:promotions",
                     "--limit", "1", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 1 and result["undo"] == {"action": "unarchive", "ids": ["m2"]}
    assert live["calls"][0][1]["q"] == ["in:inbox category:promotions"]
    for action, inverse, add, remove in (("mark-read", "mark-unread", [], ["UNREAD"]),
                                         ("mark-unread", "mark-read", ["UNREAD"], [])):
        assert email_cli("ana-test", action, BOX, "m1") == 0
        assert f"Undo: hub email {inverse} {BOX} m1" in capsys.readouterr().out
        assert live["writes"][-1][1] == {"ids": ["m1"], "addLabelIds": add, "removeLabelIds": remove}


def test_label_cli_undo_quotes_label_names_and_is_runnable(email_cli, live, capsys):
    assert email_cli("ana-test", "label", BOX, "Review later", "m1") == 0
    text = capsys.readouterr().out
    assert "Labeled 1 messages" in text and "'Review later'" in text
    assert email_cli("ana-test", *shlex.split(text.split("Undo: hub email ")[1])) == 0
    assert "Unlabeled 1 messages" in capsys.readouterr().out
    assert live["writes"][-1][1]["removeLabelIds"]


def test_organize_cli_refuses_missing_or_conflicting_selections(email_cli, live, capsys):
    assert email_cli("ana-test", "archive", BOX, "--json") == 2
    assert "either" in json.loads(capsys.readouterr().out)["detail"]
    assert email_cli("ana-test", "archive", BOX, "m1", "--query", "in:inbox", "--json") == 2
    assert "either" in json.loads(capsys.readouterr().out)["detail"]
    assert not live["calls"]


def test_email_organize_mcp_tools_are_available_and_use_the_callers_rights(api, live, assistant_token):
    actions = ("archive", "unarchive", "mark_read", "mark_unread", "label", "unlabel")
    names = {"hub_email_" + action for action in actions}
    for kind in ("owner", "member", "assistant", "bot", "botops", "agent"):
        assert names <= {tool["name"] for tool in hubtools.listing(kind=kind)}
    for action in actions:
        args = {"mailbox": BOX, "ids": ["m1"]}
        if action in ("label", "unlabel"):
            args["label"] = "Review later"
        error, result = call(api, "hub_email_" + action, args, token=assistant_token)
        assert not error, result
        assert result["count"] == 1 and result["ids"] == ["m1"]
    before = len(live["writes"])
    error, result = call(api, "hub_email_archive", {"mailbox": SHARED, "ids": ["m1"]}, token="cara-test")
    assert error and result["error"] == "forbidden" and len(live["writes"]) == before


def test_long_undo_output_points_to_json_with_all_ids():
    ids = [str(i).zfill(16) for i in range(500)]
    result = {"mailbox": BOX, "action": "archive", "count": 500, "ids": ids,
              "undo": {"action": "unarchive", "ids": ids}}
    assert "--json" in remotecli.email_text(result, "archive")
    assert len(remotecli.email_text(result, "archive")) < 200
