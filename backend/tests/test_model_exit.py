"""The hub's model tunnel control path with synthetic plans and a stand-in private relay."""

import json
from contextlib import contextmanager

import httpx
import pytest

from backend import browser_exit, model_subscriptions, plan_exits
from backend.store import Problem
from backend.tests.helpers.api import assign, get, post, put, runner, setup_attempt
from backend.tests.helpers.claude_login import access, full, refresh
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import add, codex_file, company, machine, on, pick

PATH = "runners/me/browser-exit/tunnels"
SENTINEL = "sk-ant-oat01-synthetic-exit-credential"
A_TICKET, E_TICKET = "a" * 64, "e" * 64
SWITCH = "settings/plan-exit-routing"
CAPS = (
    "attempt_credentials_v1",
    "claude_login_v1",
    "model_connect_v1",
    "computer_logins_v1",
    plan_exits.MODEL_CAPABILITY,
    plan_exits.CLAUDE_CAPABILITY,
)


class Relay:
    def __init__(self):
        self.online = False
        self.links = []
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
        if body["op"] == "heartbeat":
            live = {b["owner"]: b["generation"] for b in self.bindings.values()}
            return httpx.Response(
                200, json={"boot": "test", "reports": [], "model_exit_live": live, "model_exit_links": self.links}
            )
        if body["op"] == "model_exit_generations":
            return httpx.Response(200, json={"boot": "test", "closed": 0})
        assert body["op"] == "exit_redeem"
        binding = self.bindings.get(body["id"])
        if not binding:
            return httpx.Response(409, json={"error": "model_exit_stale"})
        if body["ticket"] != E_TICKET or body["runner_id"] != binding["exit"]:
            return httpx.Response(403, json={"error": "model_exit_forbidden"})
        del self.bindings[body["id"]]
        return httpx.Response(200, json={"boot": "test", "binding": binding})


def advertise(api, computer, capable=True, *, claude_wiring=True, forward_only=False):
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [plan_exits.MODEL_CAPABILITY]
            if forward_only
            else ((CAPS if claude_wiring else CAPS[:-1]) if capable else []),
            "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": not forward_only}}},
        },
        token=computer["token"],
    )


@pytest.fixture
def relay(api, monkeypatch):
    relay, sessions = Relay(), api.app.state.store.screen_sessions
    monkeypatch.setattr(sessions, "healthy", True)
    monkeypatch.setattr(sessions, "boot", "test")
    monkeypatch.setattr(sessions, "lease", relay.lease)
    with httpx.Client(base_url="http://relay.test", transport=httpx.MockTransport(relay)) as http:
        monkeypatch.setattr(sessions, "http", http)
        yield relay


@pytest.fixture
def tunnel(api, relay):
    setup(api)
    bot, _, attempt = setup_attempt(api)
    exit_runner = runner(api)
    advertise(api, bot)
    advertise(api, exit_runner)
    plan = add(api, secret=SENTINEL)
    person = plan["owner_id"].removeprefix("human:")
    put(api, f"model-subscriptions/plan-exits/{person}", {"runner_id": exit_runner["runner_id"]})
    put(api, SWITCH, {"enabled": True})
    store = api.app.state.store
    with store.transaction() as c:
        generation = c.execute("SELECT generation FROM plan_exits WHERE person_id=?", (person,)).fetchone()[0]
        c.execute(
            "INSERT INTO attempt_subscriptions(attempt_id,runtime,subscription_id,revision,delivery) VALUES(?,?,?,?,?)",
            (attempt["id"], "claude", plan["id"], plan["revision"], "model_exit"),
        )
        c.execute("INSERT INTO plan_exit_bindings VALUES('attempt',?,?)", (attempt["id"], generation))
    return bot, exit_runner, attempt, plan, generation, relay


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


@pytest.mark.parametrize("change", ["stopped", "generation", "receipt", "revision", "relay_lease"])
def test_model_exit_rechecks_the_live_binding_before_opening_the_vault(api, tunnel, change):
    _, exit_runner, attempt, plan, _, relay = tunnel
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
        elif change == "receipt":
            c.execute("DELETE FROM plan_exit_bindings WHERE kind='attempt' AND ref_id=?", (attempt["id"],))
        else:
            relay.lease = "replaced"
    assert redeem(api, answer, exit_runner["token"], 409)["error"]["code"] == "model_exit_stale"
    if change != "revision":
        assert grant(api, tunnel, 409 if change != "relay_lease" else 503)["error"]["code"].startswith("model_exit_")


def test_relay_tick_without_live_model_grants_only_heartbeats(api, tunnel, monkeypatch):
    *_, relay = tunnel
    store = api.app.state.store
    read = store.read
    statements = []

    @contextmanager
    def trace_read():
        with read() as c:
            c.set_trace_callback(statements.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    monkeypatch.setattr(store, "read", trace_read)
    store.screen_sessions.tick()
    assert [c["op"] for c in relay.commands] == ["heartbeat"]
    assert not any("plan_exits" in statement.lower() for statement in statements)


def test_relay_tick_batches_only_changed_owners_with_live_model_grants(api, tunnel):
    _, _, _, plan, generation, relay = tunnel
    owner = plan["owner_id"].removeprefix("human:")
    relay.online = True
    grant(api, tunnel)
    relay.bindings["second"] = {"owner": "cara", "generation": 1}
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE plan_exits SET generation=generation+1 WHERE person_id=?", (owner,))
        c.execute("INSERT INTO plan_exits(person_id,generation) VALUES('cara',2),('ben',3)")

    store.screen_sessions.tick()
    assert [c["op"] for c in relay.commands] == ["exit_grant", "heartbeat", "model_exit_generations"]
    assert relay.commands[-1]["generations"] == {owner: generation + 1, "cara": 2}


def test_the_turn_computer_cannot_also_be_the_model_exit(api, tunnel):
    bot, _, _, plan, _, relay = tunnel
    relay.online = True
    answer = grant(api, tunnel)
    owner = plan["owner_id"].removeprefix("human:")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE plan_exits SET runner_id=? WHERE person_id=?", (bot["runner_id"], owner))
    assert grant(api, tunnel, 409)["error"]["code"] == "model_exit_stale"
    assert [c["op"] for c in relay.commands] == ["exit_grant"]
    # Recheck even if the relay had already admitted a same-computer binding.
    relay.bindings[answer["tunnel_id"]]["exit"] = bot["runner_id"]
    assert redeem(api, answer, bot["token"], 409)["error"]["code"] == "model_exit_stale"


def test_redemption_of_a_malformed_saved_login_is_stale(api, tunnel, monkeypatch):
    _, exit_runner, attempt, _, _, relay = tunnel
    plan = add(api, "claude_login", full())
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempt_subscriptions SET runtime=?,subscription_id=?,revision=? WHERE attempt_id=?",
            ("claude", plan["id"], plan["revision"], attempt["id"]),
        )
    relay.online = True
    answer = grant(api, tunnel)
    monkeypatch.setattr(model_subscriptions, "open_payer", lambda *_: "malformed")
    assert redeem(api, answer, exit_runner["token"], 409)["error"]["code"] == "model_exit_stale"


def test_normal_claims_keep_the_claim_delivery_even_on_capable_runners(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    bot = machine(api, caps=CAPS)
    exit_runner = runner(api, label="Owner exit")
    plan = add(api, secret=SENTINEL)
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": exit_runner["runner_id"]})
    assert get(api, SWITCH) == {"enabled": False}
    assert "model_exit" not in advertise(api, exit_runner)
    pick(api, "ops", "claude", sid=plan["id"])
    post(api, "chat/ops", {"text": "Check the provider."})
    attempt = post(api, "jobs/claim", {"next_run": True, "start_places": True}, token=bot["token"])["attempt"]
    assert attempt["attempt_credential"]["id"] == plan["id"]
    assert SENTINEL in json.dumps(attempt["attempt_credential"])
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


def queue_claude(api, *, kind="claude_token", secret=SENTINEL, caps=CAPS):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    bot = machine(api, caps=caps)
    exit_runner = runner(api, label="Owner exit")
    plan = add(api, kind, secret)
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": exit_runner["runner_id"]})
    pick(api, "ops", "claude", sid=plan["id"])
    post(api, "chat/ops", {"text": "Check the provider."})
    return bot, exit_runner, plan


def advertise_codex(api, computer, bot=None, *, version="0.170.0", capable=True, runtime="codex"):
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [*CAPS, *([plan_exits.CODEX_CAPABILITY] if capable else [])],
            "readiness": {
                "schema_version": 1,
                "harnesses": {"codex": {"version": version, "installed": True}},
                "runtimes": {"codex": {"installed": True}, "claude": {"installed": True}},
                "bots": {bot: {"runtime": runtime, "ready": True}} if bot else {},
            },
        },
        computer["token"],
    )


def queue_codex(api):
    setup(api)
    company(api)
    on(api, "ops", "codex", "")
    bot = machine(api)
    exit_runner = runner(api, label="Owner exit")
    plan = add(api, "codex_login", codex_file(refresh="synthetic-refresh"))
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": exit_runner["runner_id"]})
    pick(api, "ops", "codex", sid=plan["id"])
    post(api, "chat/ops", {"text": "Check the provider."})
    advertise_codex(api, bot, "ops")
    return bot, exit_runner, plan


def claim_reply(api, computer, key=None):
    return post(api, "jobs/claim", {"next_run": True, "start_places": True}, token=computer["token"], key=key)


@pytest.mark.parametrize(
    ("kind", "secret", "credential"),
    [
        ("claude_token", SENTINEL, SENTINEL),
        ("claude_login", full(), access(1)),
    ],
)
def test_claude_waits_for_the_link_and_only_the_exit_receives_the_credential(
    api, relay, kind, secret, credential, monkeypatch
):
    bot, exit_runner, plan = queue_claude(api, kind=kind, secret=secret)
    put(api, SWITCH, {"enabled": True})
    assert advertise(api, exit_runner, forward_only=True)["model_exit"] is True
    assert claim_reply(api, bot)["attempt"] is None
    wait = post(api, "jobs/claim", {"bot": "ops", "start_places": True}, bot["token"])
    assert wait == {"attempt": None, "waiting": "Waiting for Owner exit"}
    store = api.app.state.store
    rang = []
    monkeypatch.setattr(store, "ring", rang.append)
    with store.read() as c:
        before = c.execute("SELECT max(seq) FROM changes").fetchone()[0]
    relay.online, relay.links = True, [exit_runner["runner_id"]]
    store.screen_sessions.tick()
    assert len(rang) == 1 and rang[0] > before
    with store.read() as c:
        assert c.execute(
            "SELECT 1 FROM changes WHERE seq>? AND topic='runner' AND kind='work' AND subject_id=?",
            (before, bot["runner_id"]),
        ).fetchone()
    attempt = claim_reply(api, bot)["attempt"]
    [payer] = attempt["subscriptions"]
    assert payer["delivery"] == "model_exit" and payer["id"] == plan["id"]
    assert "attempt_credential_required" not in attempt and "attempt_credential" not in attempt
    a_poll = post(api, "runner-claude-logins", {"live": [{"subscription": plan["id"], "revision": 0}]}, bot["token"])
    values = get(api, "credential-runtime", attempt["token"])
    assert credential not in json.dumps([attempt, a_poll, values])
    tunnel = bot, exit_runner, attempt, plan, payer["exit_generation"], relay
    assert redeem(api, grant(api, tunnel), exit_runner["token"]) == {"provider": "claude", "access_token": credential}

    relay.online, relay.links = False, []
    store.screen_sessions.tick()
    answer = post(api, f"attempts/{attempt['id']}/started", {"thread_id": "claude-thread"}, bot["token"], expected=409)
    assert answer["error"]["code"] == "payer_wait" and answer["error"]["detail"] == "Waiting for Owner exit"
    assert claim_reply(api, bot)["attempt"] is None
    relay.online, relay.links = True, [exit_runner["runner_id"]]
    store.screen_sessions.tick()
    resumed = claim_reply(api, bot)["attempt"]
    assert resumed["job_id"] == attempt["job_id"] and resumed["id"] != attempt["id"]


def test_old_runner_skips_the_routed_plan_and_can_claim_a_key(api, relay):
    bot, _, _ = queue_claude(api, caps=CAPS[:-1])
    put(api, SWITCH, {"enabled": True})
    on(api, "cpo", "claude", "claude-sonnet-5-5")
    assign(api, bot, "cpo")
    post(
        api,
        "runners/heartbeat",
        {
            "version": "0.6.0",
            "platform": "test",
            "capabilities": CAPS[:-1],
            "readiness": {
                "schema_version": 1,
                "runtimes": {"claude": {"installed": True}},
                "bots": {"ops": {"runtime": "claude", "ready": True}, "cpo": {"runtime": "claude", "ready": True}},
            },
        },
        bot["token"],
    )
    key = add(api, "anthropic_key", "sk-ant-api03-synthetic", name="Claude key")
    pick(api, "cpo", "claude", key["id"], kind="key")
    post(api, "chat/cpo", {"text": "Check the key."})
    assert claim_reply(api, bot)["attempt"]["attempt_credential"]["id"] == key["id"]
    assert claim_reply(api, bot)["attempt"] is None


@pytest.mark.parametrize("placement", ["cloud", "hosted", "exit"])
def test_cloud_hosted_and_selected_computers_keep_direct_delivery(api, relay, placement):
    bot, _, plan = queue_claude(api, caps=CAPS[:-1])
    store = api.app.state.store
    if placement == "exit":
        put(api, "model-subscriptions/plan-exits/ana", {"runner_id": bot["runner_id"]})
    else:
        with store.transaction() as c:
            if placement == "cloud":
                c.execute("INSERT INTO cloud_machines(bot,runner_id) VALUES('ops',?)", (bot["runner_id"],))
            else:
                c.execute(
                    "INSERT INTO hosting_enrollments(request_id,code_hash,label) "
                    "SELECT 'host', code_hash, 'Hosted' FROM enrollments WHERE runner_id=?",
                    (bot["runner_id"],),
                )
    put(api, SWITCH, {"enabled": True})
    attempt = claim_reply(api, bot)["attempt"]
    assert attempt["attempt_credential_required"] is True
    assert attempt["attempt_credential"]["id"] == plan["id"] and SENTINEL in json.dumps(attempt)


@pytest.mark.parametrize("placement", ["cloud", "hosted"])
def test_running_direct_claude_turn_survives_cutovers_and_receives_the_exits_rotated_token(api, relay, placement):
    bot, exit_runner, plan = queue_claude(api, kind="claude_login", secret=full(minutes=30))
    sid, rid = plan["id"], bot["runner_id"]
    store = api.app.state.store
    with store.transaction() as c:
        if placement == "cloud":
            c.execute("INSERT INTO cloud_machines(bot,runner_id) VALUES('ops',?)", (rid,))
        else:
            c.execute(
                "INSERT INTO hosting_enrollments(request_id,code_hash,label) "
                "SELECT 'host',code_hash,'Hosted' FROM enrollments WHERE runner_id=?",
                (rid,),
            )
    attempt = claim_reply(api, bot)["attempt"]
    assert access(1) in json.dumps(attempt["attempt_credential"])
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "direct-thread"}, bot["token"])
    put(api, SWITCH, {"enabled": True})
    advertise(api, exit_runner)
    live = {"live": [{"subscription": sid, "revision": 1}]}
    assert post(api, "runner-claude-logins", live, bot["token"]) == {"tokens": [], "refreshes": [], "usage": []}
    [lease] = post(api, "runner-claude-logins", {}, exit_runner["token"])["refreshes"]
    result = {"lease": lease["lease"], "revision": 1, "state": "succeeded", "value": full(n=2)}
    post(api, f"runner-claude-logins/{sid}/refreshed", result, exit_runner["token"])
    poll = post(api, "runner-claude-logins", live, bot["token"])
    [token] = poll["tokens"]
    assert token["revision"] == 2 and access(2) in token["value"] and refresh(2) not in token["value"]
    assert poll["refreshes"] == poll["usage"] == []
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": rid})
    replacement = runner(api, label="Replacement exit")
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": replacement["runner_id"]})
    put(api, SWITCH, {"enabled": False})
    with store.read() as c:
        assert tuple(
            c.execute("SELECT state,stop_requested_at FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        ) == ("running", None)


def test_claim_does_not_return_a_direct_credential_read_across_cutover(api, relay, monkeypatch):
    bot, _, _ = queue_claude(api)
    store = api.app.state.store
    open_credential = model_subscriptions.claim_credential

    def cut_over_after_read(*args, **kwargs):
        value = open_credential(*args, **kwargs)
        if value:
            assert SENTINEL in json.dumps(value)
            with store.transaction() as c:
                plan_exits.set_enabled(c, True, "human:ana")
        return value

    monkeypatch.setattr(model_subscriptions, "claim_credential", cut_over_after_read)
    answer = claim_reply(api, bot)
    assert answer["attempt"] is None and SENTINEL not in json.dumps(answer)
    assert get(api, SWITCH) == {"enabled": True}


def test_reselecting_and_disabling_change_only_the_bound_personal_turn(api, relay):
    bot, exit_runner, _ = queue_claude(api)
    selected = "model-subscriptions/plan-exits/ana"
    put(api, selected, {"runner_id": bot["runner_id"]})
    put(api, SWITCH, {"enabled": True})
    direct = claim_reply(api, bot)["attempt"]
    assert direct["attempt_credential_required"] is True
    post(api, f"attempts/{direct['id']}/started", {"thread_id": "direct"}, bot["token"])
    put(api, selected, {"runner_id": exit_runner["runner_id"]})
    renew = post(api, f"attempts/{direct['id']}/renew", {}, bot["token"])
    assert renew["stop"] is True and renew["stop_reason"] == "The plan exit route changed"
    post(api, f"attempts/{direct['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, bot["token"])

    advertise(api, exit_runner)
    relay.online, relay.links = True, {exit_runner["runner_id"]: 1}
    api.app.state.store.screen_sessions.tick()
    routed = claim_reply(api, bot)["attempt"]
    assert routed["job_id"] == direct["job_id"] and routed["subscriptions"][0]["delivery"] == "model_exit"
    post(api, f"attempts/{routed['id']}/started", {"thread_id": "routed"}, bot["token"])
    put(api, SWITCH, {"enabled": False})
    renew = post(api, f"attempts/{routed['id']}/renew", {}, bot["token"])
    assert renew["stop"] is True and renew["stop_reason"] == "The plan exit route changed"


def test_enabling_plan_exit_routing_requires_admin_and_healthy_relay(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(api.app.state.store.screen_sessions, "healthy", False)
    get(api, SWITCH, "cara-test", expected=403)
    put(api, SWITCH, {"enabled": True}, token="cara-test", expected=403)
    refusal = put(api, SWITCH, {"enabled": True}, expected=503)
    assert refusal["error"]["code"] == "screen_relay_unavailable"
    assert "healthy screen relay" in refusal["error"]["detail"]
    assert get(api, SWITCH) == {"enabled": False}


def test_only_eligible_exit_computers_supervise_model_links(api, relay):
    setup(api)
    code = post(api, "enrollments", {"operator": "ana"})["code"]
    exit_runner = post(api, "runners/enroll", {"code": code, "label": "Owner exit", "platform": "darwin"})
    sid = add(api, "codex_login", codex_file())["owner_id"].removeprefix("human:")
    put(api, f"model-subscriptions/plan-exits/{sid}", {"runner_id": exit_runner["runner_id"]})
    put(api, SWITCH, {"enabled": True})
    assert "model_exit" not in advertise(api, exit_runner)
    assert advertise_codex(api, exit_runner)["model_exit"] is True
    add(api, "claude_login", full())
    assert "model_exit" not in advertise(api, exit_runner)
    add(api)
    assert "model_exit" not in advertise(api, exit_runner, capable=False)
    assert advertise(api, exit_runner)["model_exit"] is True
    put(api, SWITCH, {"enabled": False})
    assert "model_exit" not in advertise(api, exit_runner)


def test_a_private_exit_label_is_hidden_in_payer_waits(api, relay):
    bot, exit_runner, plan = queue_claude(api)
    post(api, f"computers/{exit_runner['runner_id']}/member-bots", {"accepts": False})
    put(api, SWITCH, {"enabled": True})
    post(api, "jobs/claim", {"bot": "ops", "start_places": True}, bot["token"])
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('eve','Eve','eve@acme.example')")

    def paid_by_wait(reader):
        health = get(api, "health", reader)
        wait = next(check for check in health["checks"] if check["id"] == "paid_by_wait")
        [item] = wait["items"]
        return item

    for reader, label in (("eve-test", "Private computer"), ("ana-test", "Owner exit")):
        item = paid_by_wait(reader)
        assert f"Waiting for {label}" in item["text"]
        assert item["fixes"] == [{"label": "Open Computers", "href": "#/settings", "tab": "devices", "click": ""}]
        if reader == "eve-test":
            assert "Owner exit" not in item["text"]
    with api.app.state.store.transaction() as c:
        model_subscriptions._status(c, plan["id"], "needs_reconnect", "Token expired")
    assert paid_by_wait("ana-test")["fixes"][0]["href"] == "#/settings/providers"


def test_cutover_fences_direct_receipts_and_connects_and_keeps_a_late_refresh_success(api, relay):
    bot, exit_runner, plan = queue_claude(api, kind="claude_login", secret=full(minutes=30))
    sid = plan["id"]
    stale_claim = claim_reply(api, bot, key="old-direct-claim")["attempt"]
    assert access(1) in json.dumps(stale_claim)
    [refresh] = post(api, "runner-claude-logins", {}, bot["token"])["refreshes"]
    connect = post(api, "model-subscriptions/connect", {"subscription": sid, "confirm": True})
    lid = connect["id"]
    assert connect["runner_id"] == bot["runner_id"]
    post(api, f"runner-logins/{lid}/report", {"state": "waiting"}, bot["token"])
    post(api, f"computers/{bot['runner_id']}/logins/{lid}/code", {"code": "abcdef#ghijkl"})
    put(api, SWITCH, {"enabled": True}, token=bot["token"], expected=403)
    put(api, SWITCH, {"enabled": True})
    advertise(api, exit_runner)
    assert claim_reply(api, bot, key="old-direct-claim")["attempt"] is None
    assert get(api, "runner-logins", bot["token"])["logins"] == []
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT stop_requested_at FROM attempts WHERE id=?", (stale_claim["id"],)).fetchone()[0]
        assert model_subscriptions.row(c, sid)["status"] == "needs_reconnect"
        assert tuple(c.execute("SELECT state,pending_code FROM model_logins WHERE id=?", (lid,)).fetchone()) == (
            "failed",
            None,
        )
    assert post(api, "runner-claude-logins", {}, exit_runner["token"])["refreshes"] == []
    result = {"lease": refresh["lease"], "revision": refresh["revision"], "state": "succeeded", "value": full(n=2)}
    path = f"runner-claude-logins/{sid}/refreshed"
    assert post(api, path, result, exit_runner["token"]) == {"stored": False}
    assert post(api, path, result, bot["token"]) == {"stored": True, "revision": 2}
    routed_connect = post(api, "model-subscriptions/connect", {"subscription": sid, "confirm": True})
    assert routed_connect["runner_id"] == exit_runner["runner_id"]
    assert put(api, SWITCH, {"enabled": False}) == {"enabled": False}
    assert get(api, "runner-logins", exit_runner["token"])["logins"] == []
    assert "model_exit" not in advertise(api, exit_runner)


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("codex", [False, True])
def test_running_model_exit_is_interrupted_on_link_loss_and_old_grants_stay_fenced(api, relay, replacement, codex):
    bot, exit_runner, plan = (queue_codex if codex else queue_claude)(api)
    put(api, SWITCH, {"enabled": True})
    (advertise_codex if codex else advertise)(api, exit_runner)
    sessions = api.app.state.store.screen_sessions
    rid = exit_runner["runner_id"]
    relay.online, relay.links = True, {rid: 1}
    sessions.tick()
    attempt = claim_reply(api, bot)["attempt"]
    generation = attempt["subscriptions"][0]["exit_generation"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "claude-thread"}, bot["token"])
    tunnel = bot, exit_runner, attempt, plan, generation, relay
    old_grant = grant(api, tunnel)

    relay.online, relay.links = replacement, {rid: 2} if replacement else {}
    sessions.tick()
    renewal = post(api, f"attempts/{attempt['id']}/renew", {}, bot["token"])
    assert renewal["stop"] is True and renewal["stop_reason"] == "The plan exit went offline"
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, bot["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"
    if not replacement:
        assert claim_reply(api, bot)["attempt"] is None

    selected = "model-subscriptions/plan-exits/ana"
    put(api, selected, {"runner_id": bot["runner_id"]})
    changed = put(api, selected, {"runner_id": exit_runner["runner_id"]})
    relay.online, relay.links = True, {rid: 3}
    sessions.tick()
    assert changed["generation"] > generation
    assert any(c.get("generations") == {"ana": changed["generation"]} for c in relay.commands)
    assert redeem(api, old_grant, exit_runner["token"], 409)["error"]["code"] == "model_exit_stale"
    resumed = claim_reply(api, bot)["attempt"]
    assert resumed["job_id"] == attempt["job_id"]
    assert resumed["subscriptions"][0]["exit_generation"] == changed["generation"]


def test_codex_claim_waits_for_a_current_exit_and_never_opens_the_login_on_a(api, relay):
    bot, exit_runner, plan = queue_codex(api)
    put(api, SWITCH, {"enabled": True})
    rid = exit_runner["runner_id"]
    relay.online, relay.links = True, {rid: 1}
    api.app.state.store.screen_sessions.tick()
    assert "model_exit" not in advertise_codex(api, exit_runner, version="0.169.0")
    assert claim_reply(api, bot)["attempt"] is None
    assert advertise_codex(api, exit_runner)["model_exit"] is True
    advertise_codex(api, bot, "ops", capable=False)
    assert claim_reply(api, bot)["attempt"] is None
    advertise_codex(api, bot, "ops")
    attempt = claim_reply(api, bot)["attempt"]
    [payer] = attempt["subscriptions"]
    assert payer["delivery"] == "model_exit" and payer["id"] == plan["id"]
    assert "attempt_credential" not in attempt and not attempt.get("attempt_credential_required")
    assert "synthetic-refresh" not in json.dumps(get(api, "credential-runtime", attempt["token"]))
    tunnel = bot, exit_runner, attempt, plan, payer["exit_generation"], relay
    ticket = grant(api, tunnel)
    assert redeem(api, ticket, bot["token"], 403)["error"]["code"] == "model_exit_forbidden"
    assert redeem(api, ticket, exit_runner["token"]) == {
        "provider": "codex",
        "access_token": "a-synthetic-refresh",
        "account_id": "acct-1",
    }


def test_codex_keys_stay_direct_and_the_claude_shell_gets_no_routed_login(api, relay):
    bot, _, _ = queue_codex(api)
    put(api, SWITCH, {"enabled": True})
    on(api, "ops", "claude", "")
    advertise_codex(api, bot, "ops", capable=False, runtime="claude")
    key = add(api, "anthropic_key", "sk-ant-api03-synthetic")
    pick(api, "ops", "claude", key["id"], kind="key")
    attempt = claim_reply(api, bot)["attempt"]
    assert [payer["kind"] for payer in attempt["subscriptions"]] == ["anthropic_key"]
    assert not attempt["attempt_credential"].get("shell")
    assign(api, bot, "cpo")
    on(api, "cpo", "codex", "")
    advertise_codex(api, bot, "cpo", capable=False)
    key = add(api, "openai_key", "sk-synthetic-openai-key")
    pick(api, "cpo", "codex", key["id"], kind="key")
    post(api, "chat/cpo", {"text": "Check the key."})
    attempt = claim_reply(api, bot)["attempt"]
    assert attempt["attempt_credential"]["id"] == key["id"]


def test_upgrading_with_routing_already_on_fences_a_direct_codex_turn(api, monkeypatch):
    bot, _, _ = queue_codex(api)
    attempt = claim_reply(api, bot)["attempt"]
    assert "synthetic-refresh" in attempt["attempt_credential"]["value"]
    monkeypatch.setattr(plan_exits, "enabled", lambda _: True)
    with api.app.state.store.transaction() as c:
        plan_exits.initialize(c)
        assert c.execute("SELECT stop_requested_at FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
