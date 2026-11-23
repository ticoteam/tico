"""The `tico calendar`, `bot repos`, `agent pair`, `event-types` and `listening sources` commands built from specs
(clients/commands/calls.py, bots.py, automations.py, listening.py): each sends what the hand parser it replaced sent
(clients/hubcli.py and clients/remotecli.py before these specs)."""

from clients.commands import bots
from clients.tests.helpers.cli_fixtures import ANSWERS, run_line


def test_calendar_add_takes_who_as_comma_separated_attendees_and_sends_all_day_only_when_given(monkeypatch):
    line = ["calendar", "add", "--title", "T", "--start", "s", "--end", "e", "--who", "human:ana, human:ben"]
    result = run_line([*line, "--no-meet"], monkeypatch)
    assert result["http"][-1] == [
        "POST",
        "calendar/events",
        {"title": "T", "start": "s", "end": "e", "attendees": ["human:ana", "human:ben"], "add_meet": False},
    ]
    plain = run_line(["calendar", "add", "--title", "T", "--start", "s", "--end", "e", "--who", " , "], monkeypatch)
    assert plain["http"][-1] == ["POST", "calendar/events", {"title": "T", "start": "s", "end": "e", "add_meet": True}]
    assert run_line(["calendar", "add", "--title", "T"], monkeypatch)["exit"] != 0


def test_calendar_free_and_search_drop_spaces_and_an_empty_who(monkeypatch):
    free = run_line(["calendar", "free", "--who", "a, b"], monkeypatch)
    assert free["http"][-1] == ["GET", "calendar/free?who=a%2Cb&minutes=30", None]
    search = run_line(["calendar", "search", "plan", "--who", " "], monkeypatch)
    assert search["tools"] == [["tico_calendar_search", {"operation_id": None, "q": "plan"}]]


def test_calendar_list_and_status_keep_their_requests(monkeypatch):
    assert run_line(["calendar", "list"], monkeypatch)["tools"][0][0] == "tico_calendar_list"
    events = run_line(["calendar", "list", "--who", "a , b"], monkeypatch)
    assert events["http"][-1] == ["GET", "calendar/events?who=a%2Cb", None]
    status = run_line(["calendar", "status", "a1"], monkeypatch)
    assert status["http"][-1] == ["GET", "calendar/actions/a1", None]


def test_bot_repos_reads_without_a_mode_and_sets_one_without_an_operation_id(monkeypatch):
    monkeypatch.setenv("HUB_OPERATION_ID", "op-1")
    assert run_line(["bot", "repos", "b"], monkeypatch)["http"][-1] == ["GET", "bots/b/repositories", None]
    own = run_line(["bot", "repos", "b", "--own"], monkeypatch)
    assert own["http"][-1] == ["PUT", "bots/b/repositories", {"mode": "own"}]
    chosen = run_line(["bot", "repos", "b", "--chosen", "a/b:write+actions", "c/d:read"], monkeypatch)
    assert chosen["http"][-1][2] == {
        "mode": "chosen",
        "chosen": [
            {"full_name": "a/b", "access": "write", "run_workflows": True},
            {"full_name": "c/d", "access": "read"},
        ],
    }
    none = run_line(["bot", "repos", "b", "--chosen"], monkeypatch)
    assert none["http"][-1][2] == {"mode": "chosen", "chosen": []}
    bad = run_line(["bot", "repos", "b", "--chosen", "a/b:write+deploy"], monkeypatch)
    assert bad["exit"] == 2 and '"error": "validation"' in bad["stdout"]
    both = run_line(["bot", "repos", "b", "--all", "--own"], monkeypatch)
    assert both["exit"] != 0 and both["http"] == [] and "not allowed with" in both["stderr"]


def test_bot_repos_add_and_remove_keep_the_other_chosen_repositories(monkeypatch):
    current = {
        "mode": "chosen",
        "chosen": [
            {"full_name": "a/b", "access": "write", "run_workflows": True},
            {"full_name": "c/d", "access": "read"},
            {"full_name": "e/f", "access": "read"},
        ],
    }
    monkeypatch.setitem(ANSWERS, "GET bots/b/repositories", current)
    added = run_line(["bot", "repos", "b", "--add", "g/h:write+actions", "--add", "C/D:write"], monkeypatch)
    assert added["http"][-2:] == [
        ["GET", "bots/b/repositories", None],
        [
            "PUT",
            "bots/b/repositories",
            {
                "mode": "chosen",
                "chosen": [  # a kept grant's workflow settings stay as they are: left out, the hub keeps them
                    {"full_name": "a/b", "access": "write"},
                    {"full_name": "e/f", "access": "read"},
                    {"full_name": "g/h", "access": "write", "run_workflows": True},
                    {"full_name": "C/D", "access": "write"},
                ],
            },
        ],
    ]
    removed = run_line(["bot", "repos", "b", "--remove", "e/f", "--add", "g/h:read"], monkeypatch)
    assert [r["full_name"] for r in removed["http"][-1][2]["chosen"]] == ["a/b", "c/d", "g/h"]

    # From its own repository it starts from none; from All ticked repos, from what that covers at its access.
    monkeypatch.setitem(ANSWERS, "GET bots/b/repositories", {"mode": "own", "chosen": [current["chosen"][0]]})
    own = run_line(["bot", "repos", "b", "--add", "g/h"], monkeypatch)
    assert own["http"][-1][2] == {"mode": "chosen", "chosen": [{"full_name": "g/h", "access": "write"}]}
    monkeypatch.setitem(
        ANSWERS, "GET bots/b/repositories", {"mode": "all", "all_access": "read", "all_ticked": ["a/b", "c/d"]}
    )
    every = run_line(["bot", "repos", "b", "--remove", "c/d"], monkeypatch)
    assert every["http"][-1][2] == {"mode": "chosen", "chosen": [{"full_name": "a/b", "access": "read"}]}
    assert "moves from All ticked repos" in every["stderr"]

    both = run_line(["bot", "repos", "b", "--own", "--add", "g/h"], monkeypatch)
    assert both["exit"] == 2 and '"error": "validation"' in both["stdout"] and both["http"] == []
    bad = run_line(["bot", "repos", "b", "--add", "g/h:write+deploy"], monkeypatch)
    assert bad["exit"] == 2 and bad["http"] == []


def test_bot_repos_says_what_a_change_took_away(monkeypatch, capsys):
    taken = {
        "stopped": [{"full_name": "a/b", "access": "write"}],
        "workflows_stopped": ["a/c"],
        "workflow_edits_stopped": ["a/d"],
        "creation_stopped": True,
        "product_creation_stopped": True,
    }
    monkeypatch.setattr(bots, "bot_repos_set", lambda api, fields: taken)
    assert bots._repos_run(None, {"bot": "b", "mode": "own"}) is taken
    assert capsys.readouterr().err == (
        "b no longer has: a/b (write), a/c (run workflows), a/d (edit workflow files), repository creation, "
        "product repository creation\n"
    )


def test_agent_pair_event_types_and_listening_sources_send_as_before(monkeypatch):
    approve = run_line(["agent", "pair", "approve", "K7QM-4F2P", "--bot", "hermes-1"], monkeypatch)
    assert approve["tools"] == [
        ["tico_agent_pair_approve", {"bot": "hermes-1", "code": "K7QM-4F2P", "operation_id": None}]
    ]
    assert run_line(["agent", "pair", "approve", "K7QM-4F2P"], monkeypatch)["exit"] != 0
    types = run_line(["event-types", "--group", "task"], monkeypatch)
    assert types["http"][-1] == ["GET", "event-types?group=task", None]
    sources = run_line(["listening", "sources", "--automation", "a1"], monkeypatch)
    assert sources["http"][-1] == ["GET", "listening/sources?due=1&automation=a1", None]
