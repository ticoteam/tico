"""Paging flags the routes take and the tools lacked, and `tico api --file` (docs/cli.md, Paging)."""

import json

import pytest

from clients import hubcli, hubtools, remotecli


@pytest.fixture
def calls(monkeypatch):
    seen = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False

        def get(self, path, /, **query):
            if path == "me":
                return {"actor": "human:ana", "kind": "human"}
            seen.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
            if path == "tasks":
                return {"tasks": [{"id": "t1"}], "next_offset": 1}
            return {}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            seen.append((method, path, body if body is not None else {k: v for k, v in (query or {}).items()}))
            return {"ok": True}

    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    return seen


def run(words):
    return remotecli.run(hubcli.parser().parse_args(words))


def exit_of(words):
    return remotecli.main(hubcli.parser().parse_args(words))


def test_list_commands_send_their_paging_flags(calls):
    table = [
        (["task", "list", "--limit", "2", "--offset", "4"], ("GET", "tasks", {"limit": 2, "offset": 4})),
        (["doc", "list", "--cursor", "c2", "--limit", "5"], ("GET", "docs", {"cursor": "c2", "limit": 5})),
        (
            ["update", "list", "--before", "2026-11-10T00:00:00Z"],
            ("GET", "updates", {"before": "2026-11-10T00:00:00Z"}),
        ),
        (["card", "list", "--limit", "3", "--cursor", "a|b"], ("GET", "cards", {"limit": 3, "cursor": "a|b"})),
        (["goal", "strategy-bot-log", "--limit", "7"], ("GET", "strategy-bot/log", {"limit": 7})),
        # The search route reads a page's next_cursor as `before`: `cursor=` was ignored, so page 2 was page 1.
        (["thread", "search", "refund", "--cursor", "k9"], ("GET", "threads/search", {"q": "refund", "before": "k9"})),
    ]
    for words, expected in table:
        calls.clear()
        run(words)
        assert calls == [expected], words


def test_a_paged_task_list_keeps_next_offset_and_an_unpaged_one_stays_a_list(calls):
    assert hubtools.task_list(hubtools_api(calls), {"limit": 1}) == {"tasks": [{"id": "t1"}], "next_offset": 1}
    assert hubtools.task_list(hubtools_api(calls), {}) == [{"id": "t1"}]
    assert {"limit", "offset"} <= set(hubtools.BY_NAME["tico_task_list"]["inputSchema"]["properties"])


def hubtools_api(calls):
    return remotecli.Client("https://tico.acme.example", "t")


def test_api_reads_its_body_from_a_file(calls, tmp_path, capsys):
    body = tmp_path / "body.json"
    body.write_text(json.dumps({"title": "Launch", "notes": "x" * 5000}))
    assert exit_of(["api", "POST", "/api/v2/threads", "--file", str(body)]) == 0
    assert calls == [("POST", "threads", {"title": "Launch", "notes": "x" * 5000})]


def test_api_refuses_two_bodies_and_a_missing_file(calls, tmp_path, capsys):
    assert exit_of(["api", "POST", "threads", "{}", "--file", str(tmp_path / "b.json")]) == 2
    assert "one way" in capsys.readouterr().out
    assert exit_of(["api", "POST", "threads", "--file", str(tmp_path / "missing.json")]) == 2
    assert "Cannot read" in capsys.readouterr().out
    assert calls == []


def test_task_list_all_asks_for_the_snoozed_tasks_too(calls):
    """`--all` is every task you may see: a snoozed task is open, only set aside, and was left out."""
    run(["task", "list", "--all"])
    assert calls[0] == ("GET", "tasks", {"snoozed": "all"})
    calls.clear()
    run(["task", "list", "--all", "--snoozed", "only"])
    assert calls[0] == ("GET", "tasks", {"snoozed": "only"})
