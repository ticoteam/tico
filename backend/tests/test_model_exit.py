"""The hub's model tunnel control path with synthetic plans and a stand-in private relay."""

import json

import httpx
import pytest

from backend import browser_exit
from backend.store import Problem
from backend.tests.helpers.api import post, runner, setup_attempt
from backend.tests.helpers.claude_login import access, full
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import add, codex_file, company, machine, on, pick

PATH = "runners/me/browser-exit/tunnels"
SENTINEL = "sk-ant-oat01-synthetic-exit-credential"
A_TICKET, E_TICKET = "a" * 64, "e" * 64


class Relay:
    def __init__(self):
        self.online = False
        self.lease = "current"
        self.bindings = {}
        self.commands = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.commands.append(body)
        if body["lease"] != self.lease:
            return httpx.Response(409, json={"error": "stale permission lease"})
        if body["op"] == "exit_grant":
            if not self.online:
                return httpx.Response(409, json={"error": "model_exit_offline"})
            grant = body["grant"]
            self.bindings[grant["id"]] = {k: v for k, v in grant.items() if k not in ("id", "kind")}
            return httpx.Response(200, json={"boot": "test", "ticket": A_TICKET})
        if body["op"] == "model_exit_generation":
            return httpx.Response(200, json={"boot": "test", "closed": 0})
        assert body["op"] == "exit_redeem"
        binding = self.bindings.get(body["id"])
        if not binding:
            return httpx.Response(409, json={"error": "model_exit_stale"})
        if body["ticket"] != E_TICKET or body["runner_id"] != binding["exit"]:
            return httpx.Response(403, json={"error": "model_exit_forbidden"})
        del self.bindings[body["id"]]
        return httpx.Response(200, json={"boot": "test", "binding": binding})


def advertise(api, computer, capable=True):
    post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "capabilities": ["model_exit_v1"] if capable else []},
        token=computer["token"],
    )


@pytest.fixture
def tunnel(api, monkeypatch):
    setup(api)
    bot, _, attempt = setup_attempt(api)
    exit_runner = runner(api)
    advertise(api, bot)
    advertise(api, exit_runner)
    plan = add(api, secret=SENTINEL)
    person = plan["owner_id"].removeprefix("human:")
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE plan_exits SET runner_id=? WHERE person_id=?", (exit_runner["runner_id"], person))
        generation = c.execute("SELECT generation FROM plan_exits WHERE person_id=?", (person,)).fetchone()[0]
        c.execute(
            "INSERT INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision,delivery) VALUES(?,?,?,?,?)",
            (attempt["id"], "claude", plan["id"], plan["revision"], "model_exit"),
        )
    relay, sessions = Relay(), store.screen_sessions
    monkeypatch.setattr(sessions, "healthy", True)
    monkeypatch.setattr(sessions, "boot", "test")
    monkeypatch.setattr(sessions, "lease", relay.lease)
    with httpx.Client(base_url="http://relay.test", transport=httpx.MockTransport(relay)) as http:
        monkeypatch.setattr(sessions, "http", http)
        yield bot, exit_runner, attempt, plan, generation, relay


def grant(api, tunnel, expected=200):
    bot, _, attempt, _, generation, _ = tunnel
    return post(
        api,
        PATH,
        {"kind": "model", "attempt_id": attempt["id"], "generation": generation},
        token=bot["token"],
        expected=expected,
    )


def redeem(api, answer, token, expected=200, ticket=E_TICKET):
    return post(
        api,
        PATH + "/redeem",
        {"tunnel_id": answer["tunnel_id"], "ticket": ticket},
        token=token,
        key="redeem-" + answer["tunnel_id"],
        expected=expected,
    )


def test_model_exit_has_its_own_link_and_only_the_selected_runner_can_redeem(api, tunnel):
    bot, exit_runner, _, _, _, relay = tunnel
    state = api.app.state

    def linked(computer, kind):
        return browser_exit.authorize_link(
            state.store,
            state.auth,
            state.execution,
            {"authorization": "Bearer " + computer["token"], "exit_kind": kind},
        )

    assert linked(exit_runner, "model")["runner_id"] == exit_runner["runner_id"]
    for computer, kind in [(exit_runner, "browser"), (bot, "model")]:
        with pytest.raises(Problem):
            linked(computer, kind)
    assert grant(api, tunnel, 409)["error"]["code"] == "model_exit_offline"
    relay.online = True
    for computer in (bot, exit_runner):
        advertise(api, computer, False)
        assert grant(api, tunnel, 409)["error"]["code"] == "model_exit_stale"
        advertise(api, computer)
    answer = grant(api, tunnel)
    assert answer["ticket"] == A_TICKET
    for token, ticket in [(bot["token"], E_TICKET), (exit_runner["token"], A_TICKET), ("ana-test", E_TICKET)]:
        assert SENTINEL not in json.dumps(redeem(api, answer, token, 403, ticket))
    assert redeem(api, answer, exit_runner["token"]) == {"provider": "claude", "access_token": SENTINEL}
    assert redeem(api, answer, exit_runner["token"], 409)["error"]["code"] == "model_exit_stale"
    assert SENTINEL not in json.dumps([answer, relay.commands])
    with state.store.read() as c:
        assert not c.execute("SELECT 1 FROM idempotency WHERE response LIKE ?", (f"%{SENTINEL}%",)).fetchone()


@pytest.mark.parametrize("change", ["stopped", "generation", "revision", "relay_lease"])
def test_model_exit_rechecks_the_live_binding_before_opening_the_vault(api, tunnel, change):
    _, exit_runner, attempt, plan, generation, relay = tunnel
    relay.online = True
    answer = grant(api, tunnel)
    store = api.app.state.store
    with store.transaction() as c:
        if change == "stopped":
            c.execute("UPDATE attempts SET stop_requested_at=created_at WHERE id=?", (attempt["id"],))
        elif change == "generation":
            c.execute("UPDATE plan_exits SET generation=generation+1")
        elif change == "revision":
            c.execute("UPDATE model_subscriptions SET revision=revision+1 WHERE id=?", (plan["id"],))
        else:
            relay.lease = "replaced"
    assert redeem(api, answer, exit_runner["token"], 409)["error"]["code"] == "model_exit_stale"
    if change == "generation":
        store.screen_sessions._exit_reconcile()
        assert relay.commands[-1]["generation"] == generation + 1
    if change != "revision":
        assert grant(api, tunnel, 409 if change != "relay_lease" else 503)["error"]["code"].startswith("model_exit_")


def test_normal_claims_keep_the_claim_delivery_even_on_capable_runners(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    bot = machine(api, caps=("attempt_credentials_v1", "model_exit_v1"))
    plan = add(api)
    pick(api, "ops", "claude", sid=plan["id"])
    post(api, "chat/ops", {"text": "Check the provider."})
    attempt = post(api, "jobs/claim", {"next_run": True, "start_places": True}, token=bot["token"])["attempt"]
    assert attempt["attempt_credential"]["id"] == plan["id"]
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT delivery FROM attempt_subscriptions WHERE attempt_id=?", (attempt["id"],)).fetchone()[0]
            == "claim"
        )
    assert (
        post(
            api, PATH, {"kind": "model", "attempt_id": attempt["id"], "generation": 1}, token=bot["token"], expected=409
        )["error"]["code"]
        == "model_exit_stale"
    )


@pytest.mark.parametrize(
    ("kind", "secret", "response"),
    [
        ("claude_login", full(), {"provider": "claude", "access_token": access(1)}),
        (
            "codex_login",
            codex_file(account="saved-account", refresh="synthetic-refresh"),
            {"provider": "codex", "access_token": "a-synthetic-refresh", "account_id": "saved-account"},
        ),
    ],
)
def test_login_redemption_returns_only_the_access_token_and_codex_account(api, tunnel, kind, secret, response):
    _, exit_runner, attempt, _, _, relay = tunnel
    plan = add(api, kind, secret)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempt_subscriptions SET runtime=?,subscription_id=?,revision=? WHERE attempt_id=?",
            (response["provider"], plan["id"], plan["revision"], attempt["id"]),
        )
    relay.online = True
    assert redeem(api, grant(api, tunnel), exit_runner["token"]) == response
