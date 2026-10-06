"""The BotOps evals without a model (evals/botops/): the scoring, the scenarios' predicates, and the tool-level part of each
scenario replayed as a script through the real `hub` tools against the real server.

The live runner (evals/botops/run.py) needs a real model and never runs here. This layer proves the scenarios are
achievable with the tools BotOps has, that their `done` checks hold when the job is done and not before, and that the
scoring counts what it says (the words of the conversation this work came from are the fixture).
"""
import sys
from pathlib import Path

import pytest

from backend.mcp import ApiProblem
from clients import hubtools
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import vault
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)

EVALS = Path(__file__).resolve().parents[2] / "evals" / "botops"
sys.path.insert(0, str(EVALS))
import run as live  # noqa: E402
import score  # noqa: E402

TOKENS = {"owner": "ana-test", "member": "cara-test"}
# Two representative scripted scenarios: the broadest owner flow and the member permission boundary.
SCRIPTED = [s for s in live.scenarios() if s.get("script") and s["id"] in ("build-jira-bot", "member-owner-only")]


class Server:
    """The test client as the scorer's server."""

    def __init__(self, api):
        self.api = api

    def request(self, method, path, body=None, token=None):
        r = self.api.request(method, "/api/v2/" + path.lstrip("/"), json=body, headers=headers(token))
        return r.status_code, (r.json() if r.content else {})


class ToolApi:
    """`api` for hubtools, as a BotOps turn: what the tools call, answered by the test client."""

    def __init__(self, api, token):
        self.client, self.token = api, token

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        head = {**headers(self.token), **({"X-Tico-On-Behalf-Of": "turn"} if delegate else {})}
        r = self.client.request(method, "/api/v2/" + path.lstrip("/"), json=body if method != "GET" else None,
                                params={k: v for k, v in (query or {}).items() if v is not None}, headers=head)
        payload = r.json() if r.content else {}
        if r.status_code >= 400:
            error = payload.get("error", {})
            raise ApiProblem(error.get("code", "http_error"), error.get("detail", ""), r.status_code)
        return payload

    def get(self, path, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body if body is not None else {}, key)

    def patch(self, path, body=None, key=None):
        return self.call("PATCH", path, body if body is not None else {}, key)


def seed(api, rows):
    server = Server(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM assignments WHERE bot IN ('coo','ops','cpo','product-design','finance','inbox','doc-updater')")
    live.seed(server, TOKENS["owner"], rows)


def replay(api, botops_machine, scenario):
    """One scenario: seed, the person asks, BotOps does what the script says with the real tools, the scorer reads the result."""
    vault(api)
    computer = runner(api, label="Team Mac")
    ready(api, computer, [])
    seed(api, scenario.get("seed"))
    server, owner = Server(api), TOKENS["owner"]
    before = score.before_state(server, owner)
    # Before BotOps does anything the job is not done: the checks are not vacuous.
    assert not score.score(scenario, server, owner, [("person", scenario["prompt"])], before, 0)["done"], scenario["id"]
    person = TOKENS[scenario.get("as", "owner")]
    attempt = turn(api, botops_machine, person=person, text=scenario["prompt"])
    captured, steps, models = {}, 0, None
    rules = {r["when"]: r for r in scenario.get("person", [])}
    for step in scenario["script"]:
        if step.get("person") == "fill_card":
            with api.app.state.store.read() as c:
                card = c.execute("SELECT id FROM credential_requests WHERE status='pending' ORDER BY created DESC").fetchone()
            post(api, f"credential-requests/{card['id']}/save", {"value": rules["credential_card"]["value"]}, owner)
            steps += 1
        elif step.get("next_turn"):
            post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, botops_machine["token"])
            post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, botops_machine["token"])
            attempt = claim(api, botops_machine, "botops")             # woken by the person's own saved message
        else:
            args = dict(step.get("args") or {})
            if args.get("model") == "$other_model":
                args["model"] = next(m["id"] for m in captured["models"]["models"] if m["id"] != captured["models"]["current"])
            if step["tool"] == "hub_bot_go_live":
                # Local repository construction is outside the remote tool script; report its result.
                post(api, f"bots/{args['bot']}/place", {"computer": computer["runner_id"]}, person)
                post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"bots": {
                    args["bot"]: {"ready": True, "repository_present": True}}}}, computer["token"])
            fn = hubtools.BY_NAME[step["tool"]]["fn"]
            try:
                result = fn(ToolApi(api, attempt["token"]), args)
                assert not step.get("expect_refused"), "the server should have refused: " + step["tool"]
            except ApiProblem:
                assert step.get("expect_refused"), "refused: " + step["tool"]
                result = None
            if step.get("capture"):
                captured[step["capture"]] = result
    transcript = [("person", scenario["prompt"]), ("bot", scenario["reply"])]
    return score.score(scenario, server, owner, transcript, before, steps), server, owner, before


@pytest.mark.parametrize("scenario", SCRIPTED, ids=lambda s: s["id"])
def test_every_scenario_is_done_by_its_script(api, botops, scenario):
    result, *_ = replay(api, botops, scenario)
    assert result["passed"], result


def test_the_scoring_counts_the_words_of_the_conversation_this_came_from():
    said = ("I checked the available Hub commands: they expose no runner assignment control. The bot is still planned. "
            "Please set JIRA_BASIC_AUTH in Settings > Bots yourself.")
    assert set(w.lower() for w in score.jargon_words(said)) >= {"runner", "assignment", "planned", "jira_basic_auth"}
    assert score.sent_elsewhere([said]) == 1
    assert score.jargon_words("Hub shows the task is closed.") == ["Hub"]
    assert score.jargon_words("Tico shows the task is closed.") == []
    assert score.jargon_words("It is on your Mac and turned on. Ask it which issues to solve first.") == []
    assert score.jargon_words("Fixed in 3fa9c1d, and the defaced page is gone.") == ["3fa9c1d"]      # a hash, not a word
    twice = [("person", "Tell me issues to solve"),
             ("bot", "Jira Manager is live. Ask it which issues to solve first."),
             ("bot", "Jira Manager is live! Ask it which issues to solve first"),
             ("person", "Thanks"), ("bot", "Jira Manager is live. Ask it which issues to solve first.")]
    assert score.duplicates(twice) == 1                      # the repeat in one turn; a later turn is a fresh one
    assert score.duplicates([("bot", "Done."), ("bot", "Something else entirely, with new facts in it.")]) == 0
