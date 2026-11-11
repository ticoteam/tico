"""`tico pipeline|stage|company ...`, `tico task request|approve|send-back` and `tico task wait` (clients/hubtools_pipelines.py,
docs/terminal.md): each command line runs the tool of its name, both doors send the same requests, and the waiter prints
one JSON line per change and exits by what happened. The API is a stub; no server, no network."""

import json

import pytest

from clients import hubcli, hubcli_pipelines, hubtools, hubtools_pipelines as P, remotecli
from clients.tico import APIError

PRODUCT_OWNER, BUILDER = "bot:01a-po", "bot:01a-b"
PIPELINES = {
    "pipelines": [
        {
            "id": "general",
            "name": "General",
            "manager_id": "bot:task-manager",
            "settings_version": 1,
            "stages": [
                {
                    "id": "general-todo",
                    "name": "To do",
                    "owner": "assignee",
                    "requirements": [{"id": "r0", "kind": "owner_done"}],
                }
            ],
            "done": {"id": "general-done", "name": "Done"},
        },
        {
            "id": "product",
            "name": "Product",
            "manager_id": "bot:01a-pm",
            "settings_version": 2,
            "stages": [
                {"id": "product-ideas", "name": "Ideas", "owner": PRODUCT_OWNER, "by_hand": True, "requirements": []},
                {
                    "id": "product-spec",
                    "name": "Spec",
                    "owner": PRODUCT_OWNER,
                    "owner_name": "Product Owner",
                    "task_limit": 2,
                    "requirements": [
                        {
                            "id": "r1",
                            "kind": "page",
                            "who_oks": "people_only",
                            "person_id": "human:ana",
                            "line": "Spec page",
                        }
                    ],
                },
            ],
            "done": {"id": "product-done", "name": "Done"},
        },
        {
            "id": "engineering",
            "name": "Engineering",
            "manager_id": "bot:01a-em",
            "stages": [
                {
                    "id": "engineering-build",
                    "name": "Build",
                    "owner": BUILDER,
                    "requirements": [{"id": "r2", "kind": "item", "source": "pr", "condition": "open"}],
                },
                {"id": "engineering-release", "name": "Release", "owner": "bot:release-owner", "requirements": []},
            ],
            "done": {"id": "engineering-done", "name": "Done"},
        },
        {"id": "old", "name": "Old", "retired_at": "2026-11-01T00:00:00Z", "stages": []},
    ],
    "actors": {
        PRODUCT_OWNER: "Product Owner",
        BUILDER: "Builder",
        "bot:01a-pm": "Product Pipeline Manager",
        "human:ana": "Ana",
    },
}
BOTS = [
    {
        "id": "01a-po",
        "slug": "product-owner",
        "display_name": "Product Owner",
        "state": "active",
        "online": True,
        "status": {"open_tasks": 2},
    },
    {
        "id": "release-owner",
        "slug": "release-owner",
        "display_name": "Release Owner",
        "state": "active",
        "online": False,
    },
]


def ask(may=("ok", "no"), rid="r1", who="human:ana", met=False):
    return {
        "requirement_id": rid,
        "kind": "page",
        "line": "Spec page",
        "met": met,
        "who": who,
        "may": list(may),
        "page": {"file_id": "file-9", "version": 4, "ref": "page:file-9@4"},
    }


class Api:
    """The API a tool calls: requests recorded, reads answered from the fixtures, `tasks` from a script per poll."""

    def __init__(self, task=None, script=None):
        self.calls = []
        self.task = task or {"id": "T1", "version": 7, "status": "open", "to_leave": [ask()]}
        self.script = list(script or [])  # each poll's tasks, in order; the last repeats

    def get(self, path, **query):
        self.calls.append(("GET", path, {k: v for k, v in query.items() if v is not None}))
        if path == "pipelines":
            return json.loads(json.dumps(PIPELINES))
        if path == "bots":
            return BOTS
        if path == "config":
            return {"company_name": "Acme", "public_url": "https://acme.example", "release": "v0.6.0-rc.3"}
        if path == "me":
            return {"actor": "human:ana", "actor_name": "Ana", "role": "owner", "kind": "owner"}
        if path.endswith("/log"):
            return {"log": [{"line": "moved"}], "learned": []}
        if path == "tasks" and not self.script:
            return {"tasks": []}
        if path == "tasks":
            return {"tasks": [dict(t, stage_id=t["stage"]["id"]) for t in self.now().values()]}
        if path.startswith("tasks/"):
            if self.script:
                return {"task": self.now()[path.split("/")[1]], "actors": {"human:ana": "Ana"}}
            return {"task": self.task}
        raise AssertionError(path)

    def now(self):
        return self.script[0]

    def post(self, path, body=None, key=None):
        self.calls.append(("POST", path, body))
        return {"task": {"id": "T1", "version": 8}}

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://acme.example")
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    monkeypatch.delenv("HUB_BOT", raising=False)
    monkeypatch.delenv("HUB_EMPLOYEE", raising=False)

    def run(argv, api=None):
        api = api or Api()
        monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: api)
        return hubcli.main(argv), api

    return run


# (the command line, its tool's arguments)
LINES = [
    (["pipeline", "list"], {}),
    (["pipeline", "list", "--retired"], {"retired": True}),
    (["pipeline", "show", "PRODUCT"], {"pipeline": "PRODUCT"}),
    (["pipeline", "log", "Engineering"], {"pipeline": "Engineering"}),
    (["stage", "show", "product-spec"], {"stage": "product-spec"}),
    (["company", "show"], {}),
    (
        ["task", "request", "product", "Add negate", "--body", "Why"],
        {"pipeline": "product", "title": "Add negate", "body": "Why"},
    ),
    (["task", "approve", "T1", "-m", "Looks right"], {"id": "T1", "note": "Looks right"}),
    (["task", "send-back", "T1", "-m", "Cover -0"], {"id": "T1", "note": "Cover -0"}),
]


@pytest.mark.parametrize("argv, fields", LINES, ids=[" ".join(a) for a, _ in LINES])
def test_each_command_runs_its_tool_and_both_doors_send_the_same_requests(cli, capsys, argv, fields):
    args = hubcli.parser().parse_args(argv)
    name = remotecli.tool_name(args.fn)
    assert name in hubtools.BY_NAME and args.fn in hubcli_pipelines.COMMANDS
    assert hubtools.argument_error(fields, hubtools.BY_NAME[name]["inputSchema"]) is None
    code, api = cli(argv)
    printed = json.loads(capsys.readouterr().out)
    mcp = Api()
    assert hubtools.BY_NAME[name]["fn"](mcp, fields) == printed and code == 0
    assert api.calls == mcp.calls


def test_the_tools_join_the_table_for_people_and_a_bots_list_stays_short():
    names = set(P.AUDIENCE)
    people = {t["name"] for t in hubtools.listing(kind="owner")}
    bots = {t["name"] for t in hubtools.listing(kind="bot")}
    assert names <= people and not names & bots
    assert "tico_task_wait" in hubtools.SHELL_ONLY and "tico_task_wait" not in hubtools.BY_NAME
    assert len({t["name"] for t in hubtools.TOOLS}) == len(hubtools.TOOLS)


def test_over_mcp_a_pipeline_read_is_one_call_and_a_missing_one_names_the_ones_there_are():
    protocol = hubtools.Protocol(Api(), api_error=APIError, kind="owner")
    call = lambda name, args: protocol.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    )["result"]
    shown = call("tico_pipeline_show", {"pipeline": "product"})["structuredContent"]["pipeline"]
    assert [s["id"] for s in shown["stages"]] == ["product-ideas", "product-spec"]
    spec = shown["stages"][1]
    assert spec["held_by_name"] == "Product Owner" and spec["to_leave"][0]["line"] == "Spec page (a person's OK)"
    assert spec["to_leave"][0]["person"] == "Ana" and shown["stages"][0]["by_hand"] is True
    missing = call("tico_pipeline_show", {"pipeline": "nope"})
    assert missing["isError"] and "general, product, engineering" in missing["structuredContent"]["detail"]
    assert "old" not in missing["structuredContent"]["detail"]


def test_a_stage_shows_its_pipeline_and_its_neighbours_and_a_stage_for_whoever_holds_the_task():
    shown = P.stage_show(Api(), {"stage": "product-spec"})
    assert shown["pipeline"] == {"id": "product", "name": "Product"}
    assert shown["before"]["id"] == "product-ideas" and shown["after"]["id"] == "product-done"
    general = P.stage_show(Api(), {"stage": "general-todo"})["stage"]
    assert general["held_by_name"] == P.WHOEVER and general["to_leave"][0]["line"] == "its owner says the work is done"
    with pytest.raises(APIError, match="tico pipeline show"):
        P.stage_show(Api(), {"stage": "nope"})


def test_the_company_in_one_read_names_what_each_bot_holds_and_leaves_retired_pipelines_out():
    shown = P.company_show(Api(), {})
    assert shown["company"] == {"name": "Acme", "url": "https://acme.example", "release": "v0.6.0-rc.3"}
    assert shown["me"]["role"] == "owner" and "old" not in [p["id"] for p in shown["pipelines"]]
    po = next(b for b in shown["bots"] if b["slug"] == "product-owner")
    assert po["holds"] == ["product-ideas", "product-spec"] and po["open_tasks"] == 2
    release = next(b for b in shown["bots"] if b["slug"] == "release-owner")
    assert release["holds"] == ["engineering-release"]  # a bot named by slug in a stage is found too
    assert "old" in [p["id"] for p in P.company_show(Api(), {"retired": True})["pipelines"]]


def test_a_request_goes_to_the_first_stages_holder_and_a_stage_for_whoever_needs_an_owner(cli, capsys):
    api = Api()
    P.task_request(api, {"pipeline": "Product", "title": "Add negate"})
    assert api.writes() == [
        (
            "POST",
            "tasks",
            {
                "owner": PRODUCT_OWNER,
                "title": "Add negate",
                "body": "Add negate",
                "due": None,
                "goal_id": None,
                "pipeline": "product",
            },
        )
    ]
    api = Api()
    P.task_request(api, {"pipeline": "product", "title": "x", "owner": "builder", "private": True})
    assert api.writes()[0][2]["owner"] == "builder" and api.writes()[0][2]["private"] is True
    code, api = cli(["task", "request", "general", "Tidy the docs"])
    out = json.loads(capsys.readouterr().out)
    assert code == 64 and out["error"] == "usage" and "--owner" in out["detail"] and not api.writes()


def test_approve_answers_the_one_ok_that_waits_on_you_for_the_page_version_shown(cli, capsys):
    code, api = cli(["task", "approve", "T1"])
    assert code == 0 and api.writes() == [
        ("POST", "tasks/T1/asks/r1/answer", {"value": "ok", "version": 7, "page_version": "file-9@4"})
    ]
    assert json.loads(capsys.readouterr().out)["answered"]["page"] == "page:file-9@4"
    two = Api(task={"id": "T1", "version": 7, "to_leave": [ask(), ask(rid="r5")]})
    code, api = cli(["task", "approve", "T1", "--version", "6"], two)
    assert code == 64 and "--requirement" in json.loads(capsys.readouterr().out)["detail"] and not api.writes()
    code, api = cli(["task", "approve", "T1", "--requirement", "r5", "--version", "6"], two)
    assert api.writes()[0][1:] == ("tasks/T1/asks/r5/answer", {"value": "ok", "version": 6, "page_version": "file-9@4"})
    capsys.readouterr()
    none = Api(task={"id": "T1", "version": 7, "to_leave": [ask(may=()), ask(rid="r6", met=True)]})
    code, api = cli(["task", "approve", "T1"], none)
    detail = json.loads(capsys.readouterr().out)["detail"]
    assert code == 64 and detail.startswith("Nothing on this task waits for your ok: r1 Spec page") and not api.writes()


def test_send_back_is_a_no_with_the_note_and_needs_one(cli, capsys):
    code, api = cli(["task", "send-back", "T1", "-m", "Cover negative zero"])
    assert api.writes()[0][2] == {
        "value": "no",
        "version": 7,
        "note": "Cover negative zero",
        "page_version": "file-9@4",
    }
    with pytest.raises(ValueError, match="Say what to change"):
        P.task_send_back(Api(), {"id": "T1", "note": "  "})
    assert hubcli.main(["task", "send-back", "T1"]) == 64  # argparse: -m is required


def test_task_list_narrows_to_a_pipeline_a_stage_and_whose_turn_it_is(cli, capsys):
    code, api = cli(["task", "list", "--pipeline", "engineering", "--stage", "engineering-build", "--waiting-on", "me"])
    query = next(q for method, path, q in api.calls if path == "tasks")
    assert (
        code == 0
        and query.items()
        >= {"pipeline": "engineering", "stage": "engineering-build", "waiting_on": "human:ana"}.items()
    )


def test_pipeline_list_is_new_and_the_old_pipeline_lines_still_say_what_to_use():
    assert hubcli.removed_notice(["pipeline", "list"]) is None
    assert hubcli.removed_notice(["pipeline", "show", "product"]) is None
    assert "on its page" in hubcli.removed_notice(["pipeline", "--type", "Ops"])
    assert hubcli.removed_notice(["task", "wait", "T1"]) is None
    assert "tico task file" in hubcli.removed_notice(["task", "wait", "t1", "--on", "task:t2"])


# ----------------------------------------------------------------------------- tico task wait
def task(tid, stage, status="open", version=1, turn="bot", who="bot:x", filed=(), to_leave=()):
    return {
        "id": tid,
        "number": 3,
        "title": "Add negate",
        "pipeline": {"id": stage.split("-")[0]},
        "stage": {"id": stage, "name": stage},
        "status": status,
        "version": version,
        "turn": {"state": turn, "who": who},
        "filed": [{"id": f} for f in filed],
        "to_leave": list(to_leave),
    }


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def watch(api, args, **more):
    lines, clock = [], Clock()

    def sleep(s):
        clock.t += s
        if len(api.script) > 1:
            api.script.pop(0)

    code = P.task_wait(
        api,
        {"timeout": "1m", "every": "10s", **args},
        out=lambda line: lines.append(json.loads(line)),
        sleep=sleep,
        clock=clock,
        **more,
    )
    return code, lines


def test_wait_prints_a_line_per_change_follows_filed_tasks_and_exits_zero_at_the_stage():
    api = Api(
        script=[
            {"T1": task("T1", "product-spec")},
            {"T1": task("T1", "product-spec")},  # nothing moved: nothing printed
            {
                "T1": task("T1", "product-in-development", version=2, turn="tasks", who="E1", filed=["E1"]),
                "E1": task("E1", "engineering-build"),
            },
            {
                "T1": task("T1", "product-in-development", version=2, turn="tasks", who="E1", filed=["E1"]),
                "E1": task("E1", "engineering-release", version=5),
            },
        ]
    )
    code, lines = watch(api, {"id": "T1", "tree": True, "stage": ["engineering-release"]})
    assert code == P.EXIT_MET
    assert [(l["event"], l.get("task"), l.get("stage")) for l in lines] == [
        ("start", "T1", "product-spec"),
        ("change", "T1", "product-in-development"),
        ("filed", "E1", "engineering-build"),
        ("change", "E1", "engineering-release"),
        ("met", "E1", "engineering-release"),
    ]
    assert lines[2]["filed_by"] == "T1" and lines[-1]["reason"] == "stage engineering-release"
    whole = [path for method, path, _ in api.calls if path.startswith("tasks/")]
    assert whole == ["tasks/T1", "tasks/T1", "tasks/E1", "tasks/E1"], "a task is read whole only when it changed"


def test_a_task_that_went_past_the_stage_has_reached_it_and_a_stage_must_exist():
    api = Api(
        script=[
            {
                "T1": task("T1", "product-in-development", filed=["E1"]),
                "E1": task("E1", "engineering-done", status="done"),
            }
        ]
    )
    code, lines = watch(api, {"id": "T1", "tree": True, "stage": ["engineering-release"]})
    assert code == 0 and lines[-1]["reason"] == "stage engineering-release (now engineering-done)"
    with pytest.raises(ValueError, match="No stage engineering-merged"):
        watch(Api(script=api.script), {"id": "T1", "stage": ["engineering-merged"]})


def test_wait_times_out_with_its_own_exit_and_the_task_where_it_stands():
    api = Api(script=[{"T1": task("T1", "product-spec")}])
    code, lines = watch(api, {"id": "T1", "status": ["done"]})
    assert code == P.EXIT_TIMEOUT == 124 and lines[-1]["event"] == "timeout" and lines[-1]["stage"] == "product-spec"
    assert len([c for c in api.calls if c[1] == "tasks"]) == 6  # every 10 s for a minute


def test_wait_with_no_condition_ends_with_the_task_and_with_one_it_never_meets_exits_five():
    script = lambda: [{"T1": task("T1", "general-todo")}, {"T1": task("T1", "general-done", status="done", version=2)}]
    code, lines = watch(Api(script=script()), {"id": "T1"})
    assert code == 0 and lines[-1] == {**lines[-1], "event": "met", "reason": "status done"}
    code, lines = watch(Api(script=script()), {"id": "T1", "stage": ["engineering-release"]})
    assert code == P.EXIT_ENDED == 6 and lines[-1]["event"] == "ended"


def test_wait_for_you_meets_when_an_ok_names_you_and_says_what_you_may_do():
    api = Api(
        script=[
            {"T1": task("T1", "product-ideas")},
            {"T1": task("T1", "product-spec", version=2, turn="person", who="human:ana", to_leave=[ask()])},
        ]
    )
    code, lines = watch(api, {"id": "T1", "needs_me": True})
    assert code == 0 and lines[-1]["reason"] == "needs you" and lines[-1]["who"] == "Ana"
    assert lines[-1]["yours"] == [{"requirement_id": "r1", "line": "Spec page", "may": ["ok", "no"]}]


def test_wait_rides_out_a_passing_error_and_stops_on_a_refusal():
    class Flaky(Api):
        def __init__(self, error):
            super().__init__(script=[{"T1": task("T1", "general-todo")}])
            self.error, self.reads = error, 0

        def get(self, path, **query):
            if path == "tasks":
                self.reads += 1
                if self.reads == 1:
                    raise self.error
            return super().get(path, **query)

    code, lines = watch(
        Flaky(APIError("unavailable", "try again", 503, retryable=True)), {"id": "T1", "status": ["done"]}
    )
    assert code == 124 and lines[1]["event"] == "error" and lines[1]["retryable"] is True
    code, lines = watch(Flaky(APIError("not_found", "No such task", 404)), {"id": "T1", "status": ["done"]})
    assert code == 5 and lines[-1] == {**lines[-1], "event": "error", "error": "not_found"}  # 404: not found


def test_wait_refuses_a_turn_name_it_does_not_know(cli):
    with pytest.raises(ValueError, match="--turn takes"):
        P.task_wait(Api(), {"id": "T1", "turn": ["waiting"]})


def test_a_bots_turn_never_waits_and_is_told_how_a_task_waits(cli, monkeypatch, capsys):
    monkeypatch.setenv("HUB_BOT", "builder")
    code, api = cli(["task", "wait", "T1", "--stage", "engineering-release"])
    assert code == 1 and api.calls == []
    assert "tico task file --parent" in capsys.readouterr().err


def test_the_cli_runs_the_waiter_with_the_lines_flags(cli, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(P, "task_wait", lambda api, fields: seen.update(fields) or 124)
    monkeypatch.setattr("clients.tico.Client", lambda *a, **kw: Api())
    code, _ = cli(
        ["task", "wait", "T1", "--stage", "a,b", "--turn", "person", "--needs-me", "--tree", "--timeout", "2h"]
    )
    assert code == 124
    assert seen == {
        "id": "T1",
        "stage": ["a,b"],
        "status": None,
        "turn": ["person"],
        "needs_me": True,
        "tree": True,
        "timeout": "2h",
        "every": "15s",
    }


@pytest.mark.parametrize("text, s", [("90", 90), ("90s", 90), ("15m", 900), ("2h", 7200), ("1d", 86400), ("", 5)])
def test_a_time_is_seconds_minutes_hours_or_days(text, s):
    assert P.seconds(text, 5) == s
