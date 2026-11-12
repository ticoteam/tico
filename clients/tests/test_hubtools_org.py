"""The company-management tools in clients/commands/org.py, through the one tool table and the `tico` CLI: each command's
argv becomes the requests its routes take, against a stubbed API (no network). The routes are the server's own tests'."""

import pytest

from clients import hubcli, hubtools, remotecli
from clients.commands import org as hubtools_org
from clients.tico import APIError


class FakeAPI:
    """Stands in for clients.tico.Client: answers each (method, path) with a canned body, a function of the body, or an
    APIError, and keeps every request (method, path, query, body, key, delegate)."""

    def __init__(self, answers, kind="owner"):
        self.answers = {("GET", "me"): {"kind": kind}, **answers}
        self.calls = []

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        query = {k: v for k, v in (query or {}).items() if v is not None}
        self.calls.append((method, path, query, body, key, delegate))
        answer = self.answers.get((method, path), {})
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer

    def get(self, path, /, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body, key)

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def tool(name, api, **args):
    return hubtools.BY_NAME[name]["fn"](api, args)


BOTS = [
    {
        "slug": "builder",
        "id": "builder",
        "display_name": "Builder",
        "state": "active",
        "stage": "active",
        "onboarding_state": "onboarded",
        "reports_to": "bot:pm",
        "team": "eng",
        "template": "builder",
        "model": "m1",
        "harness": "claude",
        "effort": "high",
        "assignment": {"label": "mac-1"},
        "online": True,
        "status": {"state": "working"},
    },
    {
        "slug": "pm",
        "id": "0b6c",
        "display_name": "Product Owner",
        "state": "planned",
        "stage": "onboarding",
        "onboarding_state": "needs_setup",
        "reports_to": "u1",
        "assignment": None,
    },
    {
        "slug": "qa",
        "id": "77aa",
        "display_name": "QA Owner",
        "state": "quarantined",
        "stage": "active",
        "onboarding_state": "onboarded",
        "reports_to": "bot:0b6c",
        "assignment": {"label": "mac-1"},
    },
    {
        "slug": "ads",
        "id": "ads",
        "display_name": "Ads",
        "state": "active",
        "stage": "onboarding",
        "onboarding_state": "needs_setup",
        "reports_to": "u1",
        "assignment": {"label": "mac-2"},
    },
    {
        "slug": "idle",
        "id": "idle",
        "display_name": "Idle",
        "state": "paused",
        "stage": "active",
        "onboarding_state": "onboarded",
        "reports_to": "u1",
        "assignment": {"label": "mac-2"},
    },
]
ORG = {
    "people": [
        {"id": "u1", "name": "Ana Owner", "email": "ana@acme.example", "title": "CEO"},
        {"id": "u2", "name": "Ben Ortiz", "email": "ben@acme.example", "reports_to": "u1"},
    ],
    "bots": [
        {
            "slug": b["slug"],
            "display_name": b["display_name"],
            "status": b["state"],
            "stage": b["stage"],
            "onboarding_state": b["onboarding_state"],
            "reports_to": b["reports_to"],
        }
        for b in BOTS
    ],
    "org_groups": [{"id": "eng", "name": "Engineering"}],
    "seats": [{"id": "s1", "title": "Designer", "reports_to": "bot:0b6c"}],
}
ACCESS = {
    "people": [
        {"id": "u1", "name": "Ana Owner", "email": "ana@acme.example", "role": "owner", "can_sign_in": True},
        {"id": "u2", "name": "Ben Ortiz", "email": "ben@acme.example", "role": "member", "can_sign_in": True},
        {
            "id": "u3",
            "name": "Gone Person",
            "email": "gone@acme.example",
            "role": "member",
            "left": True,
            "removable": True,
        },
    ],
    "invites": {"u2": "2026-11-10T00:00:00Z"},
}
PIPELINES = {
    "pipelines": [
        {
            "id": "p1",
            "name": "Product",
            "manager_id": "bot:0b6c",
            "stages": [
                {"name": "Build", "owner": "bot:builder"},
                {"name": "Test", "owner": "bot:77aa"},
                {"name": "Ship", "owner": "assignee"},
            ],
        },
        {"id": "p0", "name": "Old", "retired_at": "2026-11-01", "stages": []},
    ]
}


def company(more=None):
    return {
        ("GET", "bots"): BOTS,
        ("GET", "org"): ORG,
        ("GET", "access"): ACCESS,
        ("GET", "pipelines"): PIPELINES,
        **(more or {}),
    }


# ----------------------------------------------------------------------------- bots
def test_bot_list_says_why_each_stuck_bot_takes_no_work_and_the_fix():
    out = tool("tico_bot_list", FakeAPI(company()))
    rows = {r["slug"]: r for r in out["bots"]}
    assert "next" not in rows["builder"]
    assert rows["builder"]["computer"] == "mac-1" and rows["builder"]["live"] == "working"
    assert rows["pm"]["next"]["fix"] == "tico bot go-live pm"
    assert rows["qa"]["next"]["fix"] == "tico bot quarantine-clear qa"
    assert rows["ads"]["next"]["fix"] == "tico bot setup-done ads"
    assert rows["idle"]["next"]["fix"] == "tico bot resume idle"
    assert rows["qa"]["reports_to"] == "bot:pm"  # a stored bot id comes back as its slug
    assert rows["pm"]["reports_to"] == "human:u1"
    assert out["counts"]["needs_action"] == 4 and out["counts"]["state"]["planned"] == 1


def test_bot_list_filters():
    api = FakeAPI(company())
    assert [r["slug"] for r in tool("tico_bot_list", api, needs_action=True, stage="onboarding")["bots"]] == [
        "pm",
        "ads",
    ]
    assert [r["slug"] for r in tool("tico_bot_list", api, state="quarantined")["bots"]] == ["qa"]
    tool("tico_bot_list", api, archived=True)
    assert api.calls[-1][:3] == ("GET", "bots", {"include_archived": "1"})


def test_go_live_all_takes_each_planned_bot_live_and_finishes_setup():
    after = [dict(b, state="active") if b["slug"] == "pm" else b for b in BOTS]
    reads = iter([BOTS, after])
    api = FakeAPI(
        {
            ("GET", "bots"): lambda _body: next(reads),
            ("POST", "bots/pm/onboarded"): APIError("forbidden", "Only a person above it", 403),
        }
    )
    out = tool("tico_bot_go_live_all", api, setup_done=True, operation_id="op")
    assert out == {
        "went_live": ["pm"],
        "waiting_for_repository": [],
        "setup_done": ["ads"],
        "failed": [{"bot": "pm", "step": "setup-done", "error": "forbidden", "detail": "Only a person above it"}],
    }
    assert ("POST", "bots/pm/go-live", {}, {"computer": "", "setup": False}, "op:live:pm", False) in api.calls


def test_go_live_all_keeps_going_past_a_refusal_and_reports_waiting():
    two = BOTS + [dict(BOTS[1], slug="ops", id="ops")]
    api = FakeAPI(
        {
            ("GET", "bots"): two,
            ("POST", "bots/pm/go-live"): APIError("quarantined", "It is quarantined", 409),
            ("POST", "bots/ops/go-live"): {"state": "waiting_for_repository"},
        }
    )
    out = tool("tico_bot_go_live_all", api, computer="mac-1")
    assert out["failed"][0]["bot"] == "pm" and out["waiting_for_repository"] == ["ops"] and out["setup_done"] == []
    assert api.writes()[-1][3] == {"computer": "mac-1", "setup": True}  # the setup chat starts unless setup_done


def test_go_live_all_dry_run_changes_nothing():
    api = FakeAPI(company())
    out = tool("tico_bot_go_live_all", api, dry_run=True, setup_done=True)
    assert out == {"dry_run": True, "go_live": ["pm"], "setup_done": ["ads", "pm"]}
    assert api.writes() == []


def test_go_live_all_as_botops_uses_the_requesters_rights():
    api = FakeAPI(company(), kind="botops")
    tool("tico_bot_go_live_all", api)
    assert {c[5] for c in api.calls if c[1] != "me"} == {True}


@pytest.mark.parametrize("kind, body", [("owner", {"note": "ok"}), ("botops", {"note": "ok", "on_behalf_of": "turn"})])
def test_quarantine_clear(kind, body):
    api = FakeAPI({}, kind=kind)
    tool("tico_bot_quarantine_clear", api, bot="qa", note="ok", operation_id="k")
    assert api.writes() == [("POST", "bots/qa/quarantine/clear", {}, body, "k", False)]


# ----------------------------------------------------------------------------- the org in one call
def test_org_show_names_roles_managers_and_stage_holders():
    out = tool("tico_org_show", FakeAPI(company()))
    people = {p["id"]: p for p in out["people"]}
    bots = {b["id"]: b for b in out["bots"]}
    assert people["human:u1"]["role"] == "owner" and people["human:u2"]["reports_to"] == "human:u1"
    assert bots["bot:pm"]["manages_pipelines"] == ["Product"]
    assert bots["bot:builder"]["holds_stages"] == ["Product: Build"]
    assert bots["bot:qa"]["holds_stages"] == ["Product: Test"] and bots["bot:qa"]["reports_to"] == "bot:pm"
    assert out["pipelines"] == [
        {
            "id": "p1",
            "name": "Product",
            "manager": "bot:pm",
            "stages": [
                {"stage": "Build", "holder": "bot:builder"},
                {"stage": "Test", "holder": "bot:qa"},
                {"stage": "Ship", "holder": "assignee"},
            ],
        }
    ]
    assert sorted(out["reports"]["human:u1"]) == ["bot:ads", "bot:idle", "bot:pm", "human:u2"]
    assert out["seats"][0]["reports_to"] == "bot:pm"
    chart = out["chart"]
    assert chart[0].startswith("Ana Owner (human:u1, owner)")
    assert any(line.startswith("  Product Owner (bot:pm, planned, onboarding) · manages Product") for line in chart)
    assert any(line.startswith("    Builder (bot:builder") for line in chart)


def test_org_show_for_a_member_leaves_roles_out():
    api = FakeAPI(company({("GET", "access"): APIError("forbidden", "Owners and admins", 403)}))
    out = tool("tico_org_show", api)
    assert "role" not in out["people"][0] and out["roles_hidden"]


# ----------------------------------------------------------------------------- people
def test_human_show_by_email():
    out = tool("tico_human_show", FakeAPI(company()), human="ana@acme.example")
    assert out["id"] == "human:u1" and out["role"] == "owner" and out["sign_in"] is True
    assert sorted(out["reports"]) == ["bot:ads", "bot:idle", "bot:pm", "human:u2"]


def test_human_invite_by_name():
    api = FakeAPI(company())
    tool("tico_human_invite", api, human="Ben Ortiz")
    assert api.writes() == [("POST", "access/humans/u2/invite", {}, {}, None, False)]


def test_human_remove_needs_the_exact_name_or_email():
    api = FakeAPI(company())
    with pytest.raises(APIError) as refused:
        tool("tico_human_remove", api, human="u2", confirm="Ben")
    assert refused.value.code == "confirmation_required" and api.writes() == []
    assert tool("tico_human_remove", api, human="human:u2", confirm="ben@acme.example")["left"] is True
    assert api.writes() == [("POST", "humans/u2", {}, {"left": True}, None, False)]


def test_human_remove_delete_reaches_someone_already_left():
    api = FakeAPI(company())
    out = tool("tico_human_remove", api, human="gone@acme.example", confirm="Gone Person", delete=True)
    assert out["deleted"] is True
    assert [c[:2] for c in api.writes()] == [("POST", "humans/u3"), ("DELETE", "humans/u3")]


def test_an_unknown_person_is_not_found():
    with pytest.raises(APIError) as missing:
        tool("tico_human_show", FakeAPI(company()), human="nobody")
    assert missing.value.code == "not_found"


# ----------------------------------------------------------------------------- reach
REACH = {
    "effective": {"bot:77aa": {"mode": "nearby"}, "*": {"mode": "open"}},
    "settings": [{"party": "bot:77aa", "mode": "nearby"}],
    "lines": [{"id": "l1", "sender": "bot:77aa", "receiver": "human:u1"}, {"id": "l2", "sender": "*", "receiver": "*"}],
    "may_edit": ["bot:77aa"],
}


def test_reach_show_one_bot_by_slug():
    out = tool("tico_reach_show", FakeAPI(company({("GET", "reach"): REACH})), party="qa")
    assert out["party"] == "bot:77aa" and out["effective"] == {"mode": "nearby"} and out["may_change"]
    assert [l["id"] for l in out["lines"]] == ["l1"]


def test_reach_set_sends_only_what_changes_and_inherit_clears():
    api = FakeAPI({})
    tool("tico_reach_set", api, party="bot:qa", mode="closed", tasks_per_day="inherit", operation_id="r")
    assert api.writes() == [("PUT", "reach/settings/bot:qa", {}, {"mode": "closed", "tasks_per_day": None}, "r", False)]
    with pytest.raises(ValueError):
        tool("tico_reach_set", api, party="*")


# ----------------------------------------------------------------------------- the commands
@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    fake = FakeAPI(company({("GET", "reach"): REACH}))
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: fake)
    return fake


def run(argv):
    argv, _ = hubcli.rename_argv(argv)
    return remotecli.run(hubcli.parser().parse_args(argv))


@pytest.mark.parametrize(
    "argv, request_",
    [
        (["bot", "list", "--needs-action"], ("GET", "bots", {})),
        (["bots", "list", "--archived"], ("GET", "bots", {"include_archived": "1"})),
        (["bot", "quarantine-clear", "qa", "--note", "fine"], ("POST", "bots/qa/quarantine/clear", {"note": "fine"})),
        (["bot", "go-live-all", "--dry-run"], ("GET", "bots", {})),
        (["org", "show"], ("GET", "pipelines", {})),
        (["human", "show", "u2"], ("GET", "org", {})),
        (["human", "invite", "u2"], ("POST", "access/humans/u2/invite", {})),
        (["human", "remove", "u2", "--confirm", "Ben Ortiz"], ("POST", "humans/u2", {"left": True})),
        (
            ["usage", "show", "--group", "model", "--from", "2026-11-01"],
            ("GET", "usage", {"group": "model", "from": "2026-11-01"}),
        ),
        (["usage", "limits"], ("GET", "usage/limits", {})),
        (["billing", "show", "--month", "2026-11"], ("GET", "billing", {"month": "2026-11"})),
        (["reach", "show"], ("GET", "reach", {})),
        (["reach", "set", "qa", "--chats-per-day", "5"], ("PUT", "reach/settings/bot:qa", {"chats_per_day": 5})),
    ],
)
def test_each_command_runs_its_tool(cli, argv, request_):
    run(argv)
    method, path, fields = request_
    made = [c for c in cli.calls if c[:2] == (method, path)]
    assert made, cli.calls
    assert (made[-1][2] if method == "GET" else made[-1][3]) == fields


def test_org_subcommands_are_not_renamed_to_team_show():
    assert hubcli.rename_argv(["org"])[0] == ["team", "show"]


def test_human_remove_without_confirm_is_a_usage_error():
    with pytest.raises(SystemExit):
        hubcli.parser().parse_args(["human", "remove", "u2"])


def test_new_tools_are_offered_to_people_and_botops_not_the_assistant_or_bots():
    for name in (t["name"] for t in hubtools_org.TOOLS):
        kinds = hubtools.offered_to(hubtools.BY_NAME[name])
        assert "assistant" not in kinds and "bot" not in kinds, name
    assert "botops" not in hubtools.offered_to(hubtools.BY_NAME["tico_human_remove"])
