"""The task engine's bot commands (docs/dev/pipelines-v3-cutover.md, Bot commands): each `tico task ...` line and its
MCP tool send the same request to the plan's route, with the version the bot was given; none reads the task to find
one. A refusal says what is missing and what the bot may run next. The API is a recorder; no server runs."""

import json

import pytest

from clients import hubcli, hubtools, remotecli
from clients.tico import APIError


class Recorder:
    """The API a tool calls: each request recorded as (method, path, body), the task read answered from `task`."""

    def __init__(self, task=None):
        self.calls, self.task = [], task or {"id": "T1", "version": 3, "labels": ["bug"]}
        self.__dict__["_tico_is_botops"] = False

    def get(self, path, **query):
        self.calls.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
        if path == "me":
            return {"actor": "bot:ops", "kind": "bot"}
        if path == "needs-you":
            return {"items": [{"kind": "ask"}, {"kind": "task"}, {"kind": "ask"}, {"kind": "mention"}]}
        if path == "tasks":
            return {"tasks": [{"id": "T1", "version": 3, "stage_id": "general-todo", "turn": None}]}
        return {"task": self.task}

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body, key)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        self.calls.append((method, path, body))
        return {"task": {"id": "T1", "version": 4}}

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


# (the command line, the tool and its arguments, the request both send)
COMMANDS = [
    (
        ["task", "done", "T1", "-m", "Shipped the notes", "--version", "3"],
        ("tico_task_done", {"id": "T1", "note": "Shipped the notes", "version": 3}),
        ("POST", "tasks/T1/done", {"version": 3, "note": "Shipped the notes"}),
    ),
    (
        ["task", "move", "T1", "next", "-m", "The reviewer is away", "--version", "3"],
        ("tico_task_move", {"id": "T1", "to": "next", "why": "The reviewer is away", "version": 3}),
        ("POST", "tasks/T1/move", {"to": "next", "version": 3, "why": "The reviewer is away"}),
    ),
    (
        ["task", "move", "T1", "Build", "--version", "3"],
        ("tico_task_move", {"id": "T1", "to": "Build", "version": 3}),
        ("POST", "tasks/T1/move", {"to": "Build", "version": 3}),
    ),
    (
        ["task", "close", "T1", "-m", "Not needed", "--version", "3"],
        ("tico_task_close", {"id": "T1", "why": "Not needed", "version": 3}),
        ("POST", "tasks/T1/move", {"to": "closed", "version": 3, "why": "Not needed"}),
    ),
    (
        ["task", "reassign", "T1", "ana", "-m", "Hers to sign", "--version", "3"],
        ("tico_task_reassign", {"id": "T1", "owner": "ana", "why": "Hers to sign", "version": 3}),
        ("POST", "tasks/T1/owner", {"owner": "ana", "version": 3, "why": "Hers to sign"}),
    ),
    (
        [
            "task",
            "ask",
            "T1",
            "--to",
            "chris",
            "Which plan?",
            "--choices",
            "a,b",
            "--show",
            "page:12",
            "--version",
            "3",
        ],
        (
            "tico_task_ask",
            {
                "id": "T1",
                "to": "chris",
                "line": "Which plan?",
                "choices": ["a", "b"],
                "show": ["page:12"],
                "version": 3,
            },
        ),
        (
            "POST",
            "tasks/T1/asks",
            {"to": "chris", "line": "Which plan?", "version": 3, "choices": ["a", "b"], "show": ["page:12"]},
        ),
    ),
    (
        ["task", "ask", "T1", "--to", "chris", "Send the edition?", "--kind", "ok", "--version", "3"],
        ("tico_task_ask", {"id": "T1", "to": "chris", "line": "Send the edition?", "kind": "ok", "version": 3}),
        ("POST", "tasks/T1/asks", {"to": "chris", "line": "Send the edition?", "version": 3, "kind": "ok"}),
    ),
    (
        ["task", "ask", "T1", "--withdraw", "--requirement", "r9", "-m", "Found it myself"],
        ("tico_task_ask", {"id": "T1", "withdraw": True, "requirement": "r9", "why": "Found it myself"}),
        ("DELETE", "tasks/T1/asks/r9", {"why": "Found it myself"}),
    ),
    (
        ["task", "answer", "T1", "r9", "no", "-m", "Wrong totals", "--version", "3"],
        ("tico_task_answer", {"id": "T1", "requirement": "r9", "value": "no", "note": "Wrong totals", "version": 3}),
        ("POST", "tasks/T1/asks/r9/answer", {"value": "no", "version": 3, "note": "Wrong totals"}),
    ),
    (
        ["task", "give", "T1", "r9", "--to", "ben", "--version", "3"],
        ("tico_task_give", {"id": "T1", "requirement": "r9", "to": "ben", "version": 3}),
        ("POST", "tasks/T1/asks/r9/give", {"to": "ben", "version": 3}),
    ),
    (
        ["task", "snooze", "T1", "--until", "2026-11-14", "-m", "Vendor replies by Nov 14", "--version", "3"],
        ("tico_task_snooze", {"id": "T1", "until": "2026-11-14", "why": "Vendor replies by Nov 14", "version": 3}),
        ("POST", "tasks/T1/snooze", {"version": 3, "until": "2026-11-14", "why": "Vendor replies by Nov 14"}),
    ),
    (
        ["task", "unsnooze", "T1", "-m", "Back now"],
        ("tico_task_unsnooze", {"id": "T1", "why": "Back now"}),
        ("POST", "tasks/T1/unsnooze", {"why": "Back now"}),
    ),
    (
        ["task", "queue", "T1", "--version", "3"],
        ("tico_task_queue", {"id": "T1", "version": 3}),
        ("POST", "tasks/T1/queue", {"version": 3}),
    ),
    (
        ["task", "release", "T1", "--version", "3"],
        ("tico_task_release", {"id": "T1", "version": 3}),
        ("POST", "tasks/T1/release", {"version": 3}),
    ),
    (
        ["task", "release", "T1", "--tag", "v1.2.0", "--version", "3"],
        ("tico_task_release", {"id": "T1", "tag": "v1.2.0", "version": 3}),
        ("POST", "tasks/T1/release", {"version": 3, "tag": "v1.2.0"}),
    ),
    (
        ["task", "unlink", "T1", "L7"],
        ("tico_task_unlink", {"id": "T1", "link": "L7"}),
        ("DELETE", "tasks/T1/links/L7", {}),
    ),
    (
        ["task", "update", "T1", "--title", "Ship the notes", "--due", "2026-11-14T17:00:00Z", "--version", "3"],
        ("tico_task_update", {"id": "T1", "title": "Ship the notes", "due": "2026-11-14T17:00:00Z", "version": 3}),
        ("POST", "tasks/T1", {"version": 3, "title": "Ship the notes", "due": "2026-11-14T17:00:00Z"}),
    ),
    (
        ["task", "label", "T1", "--add", "docs,launch", "--remove", "bug", "--version", "3"],
        ("tico_task_label", {"id": "T1", "add": ["docs", "launch"], "remove": ["bug"], "version": 3}),
        ("POST", "tasks/T1", {"version": 3, "labels": ["docs", "launch"]}),
    ),
    (
        ["task", "file", "Sign the FCC filing", "--parent", "T1", "--pipeline", "general", "--owner", "ana"],
        ("tico_task_file", {"parent": "T1", "title": "Sign the FCC filing", "pipeline": "general", "owner": "ana"}),
        (
            "POST",
            "tasks",
            {
                "owner": "ana",
                "title": "Sign the FCC filing",
                "body": "Sign the FCC filing",
                "due": None,
                "goal_id": None,
                "pipeline": "general",
                "parent": "T1",
            },
        ),
    ),
]


@pytest.mark.parametrize("argv, tool, request_", COMMANDS, ids=[" ".join(c[0][:2]) for c in COMMANDS])
def test_each_command_and_its_tool_send_the_plans_request_with_the_version_given(monkeypatch, argv, tool, request_):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    cli = Recorder()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
    args = hubcli.parser().parse_args(argv)
    assert remotecli.tool_name(args.fn) == tool[0], "a command is its tool's name"
    remotecli.run(args)
    mcp = Recorder()
    hubtools.BY_NAME[tool[0]]["fn"](mcp, tool[1])
    assert hubtools.argument_error(tool[1], hubtools.BY_NAME[tool[0]]["inputSchema"]) is None
    assert cli.writes() == mcp.writes() == [request_]
    reads = [path for method, path, _ in mcp.calls if method == "GET"]
    assert "tasks/T1" not in reads or tool[0] == "tico_task_label", "no tool reads the task for a version"


@pytest.mark.parametrize(
    "name, args",
    [
        ("tico_task_reassign", {"id": "T1", "owner": "ana", "version": 3}),
        ("tico_task_done", {"id": "T1", "version": 3}),
        ("tico_task_move", {"id": "T1", "to": "next", "why": "x", "version": 3}),
        ("tico_task_close", {"id": "T1", "why": "x", "version": 3}),
        ("tico_task_answer", {"id": "T1", "requirement": "r9", "value": "ok", "version": 3}),
    ],
)
def test_the_assistant_cites_the_persons_message_on_a_change_they_asked_for(name, args):
    seen = []

    class Assistant(Recorder):
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            seen.append((method, path, delegate))
            return {"task": {"id": "T1"}}

    hubtools.BY_NAME[name]["fn"](Assistant(), {**args, "on_behalf_of": "turn"})
    assert [delegate for method, _, delegate in seen if method != "GET"] == ["turn"]
    assert "on_behalf_of" in hubtools.BY_NAME[name]["inputSchema"]["properties"]
    words = ["task", name.removeprefix("tico_task_"), *{"tico_task_reassign": ["T1", "ana"]}.get(name, ["T1"])]
    if name == "tico_task_move":
        words.append("next")
    if name == "tico_task_answer":
        words += ["r9", "ok"]
    assert hubcli.parser().parse_args([*words, "--on-behalf-of", "turn"]).on_behalf_of == "turn"


def test_a_relation_is_a_note_and_a_blocking_one_says_what_to_do_instead(monkeypatch, capsys):
    api = Recorder()
    hubtools.task_relate(api, {"id": "T1", "task": "T2", "kind": "duplicate_of"})
    assert api.writes() == [("POST", "tasks/T1/relations", {"task": "T2", "kind": "duplicate_of", "remove": False})]
    assert "enum" not in hubtools.BY_NAME["tico_task_relate"]["inputSchema"]["properties"]["kind"]
    for kind in ("blocks", "blocked_by"):
        with pytest.raises(ValueError) as refused:
            hubtools.task_relate(Recorder(), {"id": "T1", "task": "T2", "kind": kind})
        assert str(refused.value) == hubtools.BLOCKING_GONE and "depends_on" in str(refused.value)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    cli = Recorder()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
    assert remotecli.main(hubcli.parser().parse_args(["task", "relate", "T1", "T2", "--kind", "blocks"])) == 64
    assert json.loads(capsys.readouterr().out)["detail"] == hubtools.BLOCKING_GONE and cli.writes() == []


def test_depends_on_takes_three_words_and_unrelate_takes_it_off(monkeypatch):
    """`tico task relate 307 depends_on 304` (the spec's words), `--kind depends_on` and the MCP tool all write the same
    relation; `tico task unrelate` takes it off."""
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    sent = []
    for words in (
        ["relate", "T1", "depends_on", "#T2"],
        ["relate", "T1", "T2", "--kind", "depends_on"],
        ["unrelate", "T1", "depends_on", "T2"],
    ):
        cli = Recorder()
        monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
        remotecli.run(hubcli.parser().parse_args(["task", *words]))
        sent.append(cli.writes())
    write = ("POST", "tasks/T1/relations", {"task": "T2", "kind": "depends_on", "remove": False})
    assert sent == [[write], [write], [(*write[:2], {**write[2], "remove": True})]]
    api = Recorder()
    hubtools.task_relate(api, {"id": "T1", "task": "T2", "kind": "depends_on"})
    assert api.writes() == [write]
    same = Recorder()
    hubtools.task_relate(same, {"id": "T1", "task": "depends_on", "other": "T2", "kind": "depends-on"})
    assert same.writes() == [write]
    with pytest.raises(ValueError, match="Two kinds"):
        hubtools.task_relate(Recorder(), {"id": "T1", "task": "depends_on", "other": "T2", "kind": "follow_up"})


def test_give_takes_the_person_as_to_or_as_a_third_word_and_needs_one(monkeypatch, capsys):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    sent = []
    for words in (["--to", "ben"], ["ben"]):
        cli = Recorder()
        monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
        remotecli.run(hubcli.parser().parse_args(["task", "give", "T1", "r9", *words, "--version", "3"]))
        sent.append(cli.writes())
    assert sent[0] == sent[1] == [("POST", "tasks/T1/asks/r9/give", {"to": "ben", "version": 3})]
    cli = Recorder()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
    assert remotecli.main(hubcli.parser().parse_args(["task", "give", "T1", "r9", "--version", "3"])) == 64
    assert json.loads(capsys.readouterr().out)["detail"] == "Say who it goes to: --to <person>" and not cli.writes()


def test_label_reads_the_labels_but_sends_the_version_the_bot_was_shown():
    api = Recorder(task={"id": "T1", "version": 7, "labels": ["bug"]})
    hubtools.task_label(api, {"id": "T1", "add": ["docs"], "version": 3})
    assert api.writes() == [("POST", "tasks/T1", {"version": 3, "labels": ["bug", "docs"]})]


def test_label_as_taught_needs_no_version_and_guards_the_labels_with_the_version_it_read(monkeypatch):
    # policies/handoffs.md teaches `tico task label <id> --add x` with no version: a label change only adds or takes
    # off what it names, so it runs at the version it read, and a change made in between is refused (task_changed).
    api = Recorder(task={"id": "T1", "version": 7, "labels": ["bug"]})
    hubtools.task_label(api, {"id": "T1", "add": ["pricing-page"]})
    assert api.writes() == [("POST", "tasks/T1", {"version": 7, "labels": ["bug", "pricing-page"]})]
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    cli = Recorder(task={"id": "T1", "version": 7, "labels": []})
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: cli)
    assert remotecli.main(hubcli.parser().parse_args(["task", "label", "T1", "--add", "pricing-page"])) == 0
    assert cli.writes() == [("POST", "tasks/T1", {"version": 7, "labels": ["pricing-page"]})]


@pytest.mark.parametrize(
    "name, args",
    [
        ("tico_task_done", {"id": "T1", "note": "x"}),
        ("tico_task_move", {"id": "T1", "to": "next", "why": "x"}),
        ("tico_task_close", {"id": "T1", "why": "x"}),
        ("tico_task_reassign", {"id": "T1", "owner": "ana"}),
        ("tico_task_ask", {"id": "T1", "to": "chris", "line": "Which plan?"}),
        ("tico_task_answer", {"id": "T1", "requirement": "r9", "value": "ok"}),
        ("tico_task_give", {"id": "T1", "requirement": "r9", "to": "ben"}),
        ("tico_task_snooze", {"id": "T1", "why": "x"}),
        ("tico_task_update", {"id": "T1", "title": "x"}),
    ],
)
def test_without_a_version_a_change_says_to_add_the_version_and_sends_nothing(name, args):
    api = Recorder()
    with pytest.raises(ValueError, match=r"^Add --version N"):
        hubtools.BY_NAME[name]["fn"](api, args)
    assert api.calls == []
    # Over MCP it is the tool's own answer, not a schema error: the bot is told what to run.
    reply = hubtools.Protocol(Recorder(), kind="bot").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )
    assert reply["result"]["structuredContent"] == {
        "error": "usage",
        "detail": hubtools.version_needed("T1"),
        "retryable": False,
    }


def test_without_a_version_the_cli_prints_the_same_line_and_exits_with_the_usage_code(monkeypatch, capsys):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    api = Recorder()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: api)
    assert remotecli.main(hubcli.parser().parse_args(["task", "done", "T1", "-m", "x"])) == 64
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"error": "usage", "detail": hubtools.version_needed("T1"), "retryable": False}
    assert api.writes() == []


def test_withdraw_finds_the_one_ask_the_bot_may_take_back_and_names_them_when_there_are_several():
    one = {
        "id": "T1",
        "to_leave": [{"requirement_id": "r1", "may": ["done"]}, {"requirement_id": "r9", "may": ["withdraw"]}],
    }
    api = Recorder(task=one)
    hubtools.task_ask(api, {"id": "T1", "withdraw": True})
    assert api.writes() == [("DELETE", "tasks/T1/asks/r9", {})]
    two = {"id": "T1", "to_leave": [{"requirement_id": r, "may": ["withdraw"]} for r in ("r8", "r9")]}
    with pytest.raises(ValueError, match="--requirement r8, r9"):
        hubtools.task_ask(Recorder(task=two), {"id": "T1", "withdraw": True})
    with pytest.raises(ValueError, match="No ask of yours"):
        hubtools.task_ask(Recorder(task={"id": "T1", "to_leave": []}), {"id": "T1", "withdraw": True})


def test_an_ask_names_who_and_the_line_before_anything_is_sent():
    api = Recorder()
    with pytest.raises(ValueError, match="give to and line"):
        hubtools.task_ask(api, {"id": "T1", "version": 3})
    assert api.calls == []


def test_a_bots_question_to_a_person_sends_its_why_each_choice_with_what_happens_and_the_recommendation():
    """`tico task ask --why ... --choice "Label|what happens" --recommend Label` (asks-not-todos, For the engineer)."""
    argv = [
        "task",
        "ask",
        "T1",
        "Which tone?",
        "--to",
        "chris",
        "--why",
        "It goes out Friday.",
        "--choice",
        "Plain|Safe for customers",
        "--choice",
        "Playful | Stands out",
        "--recommend",
        "Plain",
        "--version",
        "3",
    ]
    args = {k: v for k, v in vars(hubcli.parser().parse_args(argv)).items() if k != "fn" and v is not None}
    api = Recorder()
    hubtools.task_ask(api, args)
    assert api.writes() == [
        (
            "POST",
            "tasks/T1/asks",
            {
                "to": "chris",
                "line": "Which tone?",
                "version": 3,
                "why": "It goes out Friday.",
                "recommend": "Plain",
                "choices": ["Plain", "Playful"],
                "details": ["Safe for customers", "Stands out"],
            },
        )
    ]
    with pytest.raises(ValueError, match="not both"):
        hubtools.task_ask(Recorder(), {**args, "choices": ["A", "B"]})


TASK = {
    "id": "t_9f3a",
    "number": 214,
    "title": "Build checkout flow",
    "status": "open",
    "version": 12,
    "pipeline": {"id": "general", "name": "General"},
    "stage": {"id": "general-todo", "name": "To do", "kind": "work", "by_hand": False},
    "owner_id": "bot:builder",
    "turn": {"state": "bot", "who": "bot:builder", "line": "stopped early, try 2 of 3"},
    "to_leave": [
        {"requirement_id": "r1", "kind": "owner_done", "line": "Owner says done", "met": True, "standing": "Done"},
        {
            "requirement_id": "r2",
            "kind": "ok",
            "line": "OK from Chris",
            "met": False,
            "standing": "Sent back: the totals are wrong",
            "who": "human:chris",
            "may": ["withdraw"],
        },
    ],
    "next": {"id": "general-review", "name": "Review"},
    "late": None,
    "filed_by": "t_parent",
    "filed": [{"id": "t_sub", "kind": "sub_task", "status": "open", "title": None}],
}


def test_show_prints_the_version_where_it_is_the_turn_what_it_takes_to_leave_next_and_who_filed_it():
    api = Recorder(task=TASK)
    shown = hubtools.task_show(api, {"id": "t_9f3a"})
    assert shown["task"] == TASK, "the task as the server sent it, with the lines beside it"
    assert shown["text"].splitlines() == [
        "#214 Build checkout flow   version 12",
        "General · To do · owner bot:builder",
        "Turn: bot:builder (stopped early, try 2 of 3)",
        "To leave To do:",
        "  met      Owner says done  id r1",
        "  not met  OK from Chris  Sent back: the totals are wrong  (waits on human:chris)  id r2  you may: withdraw",
        "Next: Review",
        "Filed by: t_parent",
        "Filed: t_sub (not yours to read) (open)",
    ]


def test_show_prints_its_lines_and_json_prints_the_task(monkeypatch, capsys):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Recorder(task=TASK))
    assert remotecli.main(hubcli.parser().parse_args(["task", "show", "t_9f3a"])) == 0
    assert capsys.readouterr().out.startswith("#214 Build checkout flow   version 12\n")
    assert remotecli.main(hubcli.parser().parse_args(["task", "show", "t_9f3a", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)["task"]["version"] == 12


REFUSAL = APIError(
    "task_not_met",
    "Not moved. Not met: Checks pass (lint, unit).",
    409,
    extra={
        "missing": ["Checks pass (lint, unit)"],
        "next": ['tico task move E-214 next -m "why" --version 12', 'tico task close E-214 -m "why" --version 12'],
        "version": 12,
    },
)


def test_a_refusal_carries_what_is_missing_and_the_commands_allowed_next_over_mcp():
    class Refusing(Recorder):
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            raise REFUSAL

    reply = hubtools.Protocol(Refusing(), kind="bot").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tico_task_move", "arguments": {"id": "E-214", "to": "next", "version": 12}},
        }
    )
    assert reply["result"]["isError"] is True
    payload = reply["result"]["structuredContent"]
    assert (payload["error"], payload["missing"], payload["version"]) == ("task_not_met", REFUSAL.extra["missing"], 12)
    assert payload["next"] == REFUSAL.extra["next"]


def test_the_cli_prints_a_refusal_as_lines_and_exits_two(monkeypatch, capsys):
    class Refusing(Recorder):
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            raise REFUSAL

    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example")
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: Refusing())
    assert remotecli.main(hubcli.parser().parse_args(["task", "move", "E-214", "next", "--version", "12"])) == 2
    assert capsys.readouterr().out.splitlines() == [
        "Not moved. Not met: Checks pass (lint, unit).",
        "Missing:",
        "  Checks pass (lint, unit)",
        "You can:",
        '  tico task move E-214 next -m "why" --version 12',
        '  tico task close E-214 -m "why" --version 12',
    ]


def test_a_stale_version_says_which_version_the_task_has_now():
    changed = {"error": "task_changed", "detail": "Changed since you looked.", "version": 13}
    assert hubtools.refusal_text(changed, "T1").splitlines() == [
        "Changed since you looked.",
        "It is version 13 now: run `tico task show T1` and decide again.",
    ]


def test_needs_you_counts_each_kind_and_lists_the_items_oldest_first():
    view = hubtools.needs_you_show(Recorder(), {"limit": 2})
    assert view == {
        "total": 4,
        "by_kind": {"ask": 2, "task": 1, "mention": 1},
        "items": [{"kind": "ask"}, {"kind": "task"}],
        "more": 2,
    }
    assert hubtools.needs_you_show(Recorder(), {"kind": "ask"})["items"] == [{"kind": "ask"}, {"kind": "ask"}]


def test_the_old_engines_tools_are_gone_and_a_call_by_one_is_an_unknown_tool():
    for name in (
        "tico_task_question",
        "tico_task_bulk",
        "tico_task_check_back",
        "tico_task_later",
        "tico_task_waive_qa",
        "tico_task_wait",
        "tico_task_types",
        "tico_approval_approve",
        "tico_pipeline_manager",
        "tico_requirements",
        "tico_task_answers",
    ):
        assert name not in hubtools.BY_NAME
        reply = hubtools.Protocol(Recorder(), kind="bot").handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        )
        assert reply["error"]["message"] == f"Unknown tool: {name}", name
    assert "bot" not in hubtools.offered_to(hubtools.BY_NAME["tico_task_unsnooze"])


def test_task_show_text_names_people_and_bots_and_a_filed_task_by_its_title():
    """`tico task show` names the owner, whose turn it is and who a requirement waits on from the answer's `actors`
    (backend/names.py), never as `human:morgan`; a wait on a filed task reads as its title, with its id last."""
    task = {
        "id": "T1",
        "title": "Import the Acme CRM",
        "version": 3,
        "owner_id": "human:morgan",
        "stage": {"name": "To do"},
        "pipeline": {"name": "General"},
        "turn": {"state": "tasks", "who": "b1a8732e-0000", "line": "Check the CRM import"},
        "to_leave": [{"requirement_id": "r1", "line": "Morgan says OK", "met": False, "who": "human:morgan"}],
    }
    actors = {"human:morgan": "Morgan Reed"}
    text = hubtools.task_text(task, actors)
    assert "owner Morgan Reed" in text and "(waits on Morgan Reed)" in text
    assert "Turn: waits on Check the CRM import (b1a8732e-0000)" in text
    assert "human:morgan" not in text
    bot_turn = {**task, "turn": {"state": "bot", "who": "bot:builder", "line": "stopped early, try 2 of 3"}}
    assert "Turn: Builder (stopped early" in hubtools.task_text(bot_turn, {"bot:builder": "Builder"})


def test_unlink_finds_the_link_by_its_url_and_says_so_when_there_is_none():
    class Links(Recorder):
        def get(self, path, **query):
            self.calls.append(("GET", path, query))
            return {"links": [{"id": "L1", "url": "https://github.com/acme/app/pull/1"}, {"id": "L2", "url": "x"}]}

    api = Links()
    hubtools.BY_NAME["tico_task_unlink"]["fn"](api, {"id": "T1", "link": "https://github.com/Acme/app/pull/1/"})
    assert api.writes() == [("DELETE", "tasks/T1/links/L1", {})]
    with pytest.raises(ValueError, match="No link to https://github.com/acme/app/pull/9"):
        hubtools.BY_NAME["tico_task_unlink"]["fn"](Links(), {"id": "T1", "link": "https://github.com/acme/app/pull/9"})


def test_task_show_says_it_waits_for_a_free_bot_and_who_each_pick_gave_it_to():
    """One of several (backend/stage_pick.py): a task nobody is free for waits with state `pick`, its `who` the stage;
    every pick is a history event of field `pick` whose note is the line."""
    task = {
        "id": "T1",
        "title": "Add negate",
        "version": 4,
        "stage": {"name": "Review 2"},
        "pipeline": {"name": "Engineering"},
        "turn": {"state": "pick", "who": "engineering-review-2", "line": "Waiting for a free Reviewer"},
        "to_leave": [],
    }
    events = [
        {"field": "move", "new": "engineering-build", "note": ""},
        {"field": "pick", "new": "bot:builder-2", "note": "Build given to Builder 2 · took turns"},
        {"field": "pick", "new": None, "note": "Review 2: Waiting for a free Reviewer"},
    ]
    text = hubtools.task_text(task, {}, events)
    assert "Turn: Waiting for a free Reviewer" in text and "engineering-review-2" not in text
    assert "Picked: Build given to Builder 2 · took turns" in text.splitlines()
    assert "Picked: Review 2: Waiting for a free Reviewer" in text.splitlines()

    class Shown(Recorder):
        def get(self, path, **query):
            return {"task": task, "events": events, "actors": {}}

    assert "Picked: Build given to Builder 2" in hubtools.BY_NAME["tico_task_show"]["fn"](Shown(), {"id": "T1"})["text"]
