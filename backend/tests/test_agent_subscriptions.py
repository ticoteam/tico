from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest

from backend import agent_subscriptions as paid_by
from backend import model_subscriptions as MS
from backend import claude_login, providers
from backend import subscription_refresh as refresh
from backend.auth import Identity
from backend.models import Claim
from backend.gateway_connection import MODEL_PREFIX
from backend.store import H, Problem, encode
from backend.tests.helpers.api import assign, claim, get, headers, post, put, runner
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import TOKEN, add, codex_file, company, machine, on

MIGRATION = import_module("backend.migrations.20261122220441_agent_subscriptions")


def bot_id(c, slug):
    return H.resolve_bot(c, slug)["id"]


def secret(c, cid, env, actor):
    c.execute(
        "INSERT INTO credentials(id,name,kind,env,ciphertext,created_at,updated_at,updated_by,created_by) "
        "VALUES(?,?,'api_key',?,X'FF',?,?,?,?)",
        (cid, cid, env, H.now(), H.now(), actor, actor),
    )


def test_migration_copies_pools_exact_choices_and_effective_grants_for_placed_agents(api):
    setup(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    box = machine(api, signed_in=True)
    assign(api, box, "cpo")
    assign(api, box, "doc-updater")
    report(api, box, ["ops"])
    post(api, "chat/ops", {"text": "Work already leased at cutover."})
    live = claim(api, box, "ops")
    signed_out = machine(api, bot="finance", label="Signed out")
    plan = add(api)["id"]
    old_key = add(api, "openai_key", "sk-acme-fake-test-key-123456")["id"]
    exact = add(api, "codex_login", codex_file(), name="Exact plan")["id"]
    orphan = add(api, "cursor_key", "csr-acme-fake-test-key-123456")["id"]
    lost = add(api, name="Former member's plan")["id"]
    local = add(api, "meta_key", "meta-acme-fake-test-key-123456")["id"]
    with api.app.state.store.transaction() as c:
        c.execute("DROP TABLE agent_subscriptions")
        c.execute("DROP INDEX model_subscriptions_pool")
        bot, coworker, out, ownerless, unplaced = (
            bot_id(c, slug) for slug in ("ops", "cpo", "finance", "doc-updater", "product-design")
        )
        c.execute("UPDATE bot_config SET owner_id='' WHERE bot=?", (ownerless,))
        # A kept vault key follows the computer, with a per-agent grant taking precedence.
        secret(c, "box-key", "OPENAI_API_KEY", "human:ana")
        c.execute("UPDATE model_subscriptions SET credential_id=?,owner_id='' WHERE id=?", ("box-key", old_key))
        c.execute("UPDATE model_subscriptions SET added_by='bot:ops',owner_id='' WHERE id=?", (orphan,))
        MS._set(c, box["runner_id"], "claude", "", "human:ana", "human:ana")
        MS._set(c, box["runner_id"], "codex", old_key, "human:ana")
        MS._set(c, signed_out["runner_id"], "claude", plan, "human:ana")
        MS._set(c, signed_out["runner_id"], "codex", exact, "human:ana")
        MS._set(c, box["runner_id"], "cursor", orphan, "human:ana")
        MS._set(c, box["runner_id"], "musecode", local, "human:ana")
        c.execute(
            "UPDATE credentials SET source=? WHERE id=?",
            ("computer:" + box["runner_id"], MS.row(c, local)["credential_id"]),
        )
        c.execute("UPDATE model_subscriptions SET owner_id='human:gone' WHERE id=?", (lost,))
        MS._set(c, signed_out["runner_id"], "claude", lost, "human:ana")
        # This local logout must not clear a different saved plan.
        MS._set(c, box["runner_id"], "claude", plan, "human:ana", "human:ana")
        c.execute("UPDATE model_subscriptions SET owner_id='' WHERE id=?", (plan,))
        c.execute(
            "INSERT INTO model_login_cleared(runner_id,runtime,cleared_at,cleared_by) VALUES(?,'claude',?,'human:ana')",
            (signed_out["runner_id"], H.now()),
        )
        secret(c, "agent-key", "OPENAI_API_KEY", "human:ben")
        secret(c, "revoked-key", "OPENAI_API_KEY", "human:ana")
        secret(c, "a-shadowed-key", "OPENAI_API_KEY", "human:ana")
        secret(c, "oauth-plan", "CLAUDE_CODE_OAUTH_TOKEN", "human:ben")
        secret(c, "lost-oauth", "CLAUDE_CODE_OAUTH_TOKEN", "human:gone")
        for name, subject, revoked in (
            ("agent-key", bot, None),
            ("revoked-key", bot, H.now()),
            ("a-shadowed-key", bot, None),
            ("agent-key", unplaced, None),
            ("oauth-plan", unplaced, None),
            ("lost-oauth", out, None),
            ("agent-key", ownerless, None),
        ):
            c.execute(
                "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at,revoked_at) "
                "VALUES(?,?,?,'human:ana',?,?)",
                (H.new_id(), name, "bot:" + subject, H.now(), revoked),
            )
        MIGRATION.up(c)
        chosen = paid_by.picks(c, bot)
        assert (chosen["claude"]["kind"], chosen["claude"]["person"], chosen["claude"]["subscription_id"]) == (
            "pool",
            "human:ana",
            "",
        )
        grant = MS.row(c, chosen["codex"]["subscription_id"])
        assert chosen["codex"]["kind"] == "key" and chosen["codex"]["person"] == "human:ben"
        assert grant["credential_id"] == "agent-key"
        other = paid_by.picks(c, coworker)
        assert other["codex"]["kind"] == "key" and other["codex"]["subscription_id"] == old_key
        assert MS.row(c, old_key)["owner_id"] == "human:ana"
        assert MS.row(c, orphan)["owner_id"] == ""
        assert other["cursor"]["subscription_id"] == orphan
        selected = paid_by.picks(c, out)
        assert "claude" not in selected and MS.row(c, lost)
        assert MS.row(c, plan)["owner_id"] == "human:ana"
        assert "musecode" not in chosen
        assert c.execute(
            "SELECT 1 FROM events WHERE target=? AND action='agent_subscription.owner_left'", (out,)
        ).fetchone()
        assert selected["codex"]["kind"] == "plan" and selected["codex"]["subscription_id"] == exact
        assert not c.execute(
            "SELECT 1 FROM credential_grants WHERE subject LIKE 'bot:%' AND revoked_at IS NULL"
        ).fetchone()
        assert not paid_by.picks(c, ownerless)
        explicit = paid_by.picks(c, unplaced)
        assert set(explicit) == {"claude", "codex"}
        assert explicit["claude"]["kind"] == "plan"
        assert MS.row(c, explicit["claude"]["subscription_id"])["credential_id"] == "oauth-plan"
        assert c.execute("SELECT 1 FROM model_subscriptions WHERE credential_id='a-shadowed-key'").fetchone()
        assert (
            c.execute("SELECT stop_requested_at FROM attempts WHERE id=?", (live["id"],)).fetchone()[0] == H.TICO_ACTOR
        )


def test_saving_check_rights_only_for_changed_tools(api):
    setup(api)
    plan = add(api)["id"]
    store, auth = api.app.state.store, api.app.state.auth
    owner = Identity("human:ana", "owner")
    admin = Identity("human:ben", "human", email="ben@acme.example")
    member = Identity("human:cara", "human", email="cara@acme.example")
    with store.transaction() as c:
        bot = bot_id(c, "ops")
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot=?", (bot,))
        claude = paid_by.Pick(kind="plan", subscription_id=plan)
        # Admins can choose another person's provider. The member may retain it while changing their own tool.
        original = paid_by.save(c, auth, admin, bot, {"claude": claude})["claude"]
        own = paid_by.Pick(kind="pool", person="cara")
        saved = paid_by.save(c, auth, member, bot, {"claude": claude, "codex": own})
        assert saved["claude"] == original and saved["codex"]["person"] == "human:cara"
        elsewhere = paid_by.Pick(kind="pool", person="ana")
        with pytest.raises(Problem) as refused:
            paid_by.save(c, auth, member, bot, {"claude": elsewhere})
        assert refused.value.status == 403
        with pytest.raises(Problem) as refused:
            paid_by.save(c, auth, Identity("bot:" + bot, "bot"), bot, {"claude": None})
        assert refused.value.status == 403
        assert paid_by.save(c, auth, owner, bot, {"claude": elsewhere})["claude"]["person"] == "human:ana"
        with pytest.raises(Problem) as no_plans:
            paid_by.save(c, auth, member, bot, {"cursor": own})
        assert no_plans.value.code == "wrong_runtime"


def reading(c, sid, used, reset):
    key = refresh.plan_key(sid)
    now = H.now()
    c.execute(
        "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
        (key, encode({"reading": {"used_percent": used, "resets_at": reset.isoformat(), "reported_at": now}})),
    )


def test_pool_uses_next_reset_with_headroom_and_waits_without_falling_back(api):
    setup(api)
    first = add(api, name="First")["id"]
    second = add(api, name="Second")["id"]
    later = add(api, name="Later")["id"]
    add(api, "anthropic_key", "sk-ant-synthetic-test-key-0000")
    add(api, "codex_login", codex_file(), name="Another provider")
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        bot = bot_id(c, "ops")
        owner = Identity("human:ana", "owner")
        pool = paid_by.Pick(kind="pool", person="ana")
        soon = datetime.now(timezone.utc) + timedelta(hours=2)
        reading(c, first, 99.5, soon)
        reading(c, second, 99.49, soon + timedelta(hours=1))
        reading(c, later, 10, soon + timedelta(days=1))
        paid_by.save(c, auth, owner, bot, {"claude": pool})
        assert paid_by.resolve(c, bot, "claude", "sonnet").subscription["id"] == second
        reading(c, first, 90, soon)
        assert paid_by.resolve(c, bot, "claude", "sonnet").subscription["id"] == first
        # A shorter provider window is still the next reset.
        key = refresh.plan_key(later)
        c.execute(
            "UPDATE registry_metadata SET value=json_set(value,'$.turn_five_hour',json(?)) WHERE key=?",
            (
                encode(
                    {"used_percent": 20, "resets_at": (soon - timedelta(hours=1)).isoformat(), "reported_at": H.now()}
                ),
                key,
            ),
        )
        assert paid_by.resolve(c, bot, "claude", "sonnet").subscription["id"] == later
        for sid, hours in ((first, 0), (second, 1), (later, 2)):
            reading(c, sid, 99.5, soon + timedelta(hours=hours))
        wait = paid_by.resolve(c, bot, "claude", "sonnet")
        assert wait.subscription is None and wait.until == soon and "next reset" in wait.reason
        assert paid_by.resolve(c, bot, "claude", MODEL_PREFIX + "medium").subscription is None
        paid_by.save(c, auth, owner, bot, {"claude": paid_by.Pick(kind="plan", subscription_id=first)})
        assert paid_by.resolve(c, bot, "claude", "sonnet").subscription["id"] == first
        assert paid_by.resolve(c, bot, "claude", providers.recommended("openai")["id"]).subscription is None
        assert paid_by.resolve(c, bot, "codex", "") is None


def test_removing_last_plan_confirms_and_clears_exact_and_pool_picks(api):
    setup(api)
    plan = add(api)["id"]
    store, auth = api.app.state.store, api.app.state.auth
    owner = Identity("human:ana", "owner")
    with store.transaction() as c:
        bot, other = (bot_id(c, slug) for slug in ("ops", "cpo"))
        paid_by.save(c, auth, owner, bot, {"claude": paid_by.Pick(kind="plan", subscription_id=plan)})
        paid_by.save(c, auth, owner, other, {"claude": paid_by.Pick(kind="pool", person="ana")})
        with pytest.raises(Problem) as refused:
            MS.remove(c, store.settings, owner, plan)
        assert refused.value.status == 409
        assert {a["bot"] for a in refused.value.extra["agents"]} == {bot, other}
        MS.remove(c, store.settings, owner, plan, confirm=True)
        assert paid_by.resolve(c, bot, "claude", "sonnet") is None
        assert paid_by.resolve(c, other, "claude", "sonnet") is None


def test_new_agent_defaults_to_owners_pool_and_leaving_removes_its_providers(api):
    setup(api)
    company(api)
    plan = add(api, token="ben-test")["id"]
    key = add(api, "openai_key", "sk-acme-fake-test-key-123456", token="ben-test")["id"]
    surviving = add(api, name="Someone else's plan")["id"]
    post(api, "bots", {"slug": "new-agent", "display_name": "New agent", "operator": "ben"})
    post(api, "bots", {"slug": "no-plans", "display_name": "No plans", "operator": "cara"})
    box = machine(api)
    store = api.app.state.store
    with store.transaction() as c:
        bot = bot_id(c, "new-agent")
        defaults = paid_by.picks(c, bot)
        assert set(defaults) == {"claude"}
        assert paid_by.picks(c, bot_id(c, "no-plans")) == {}
        assert {(p["kind"], p["person"]) for p in defaults.values()} == {("pool", "human:ben")}
        coworker = bot_id(c, "ops")
        secret(c, "departing-key", "OPENAI_API_KEY", "human:ben")
        c.execute("UPDATE model_subscriptions SET credential_id='departing-key' WHERE id=?", (key,))
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) "
            "VALUES(?, 'departing-key', ?, 'human:ben', ?)",
            (H.new_id(), "bot:" + coworker, H.now()),
        )
        paid_by.save(
            c,
            api.app.state.auth,
            Identity("human:ana", "owner"),
            coworker,
            {"codex": paid_by.Pick(kind="key", subscription_id=key)},
        )
        c.execute(
            "INSERT INTO model_logins(id,runner_id,runtime,state,created_at,updated_at,expires_at,requested_by,kind) "
            "VALUES('ongoing',?,'claude','finishing',?,?,?,'human:ana','connect')",
            (box["runner_id"], H.now(), H.now(), H.shift(H.now(), minutes=10)),
        )
        c.execute(
            "INSERT INTO subscription_connects(login_id,kind,subscription_id) VALUES('ongoing','claude_token',?)",
            (plan,),
        )
    post(api, "humans/ben", {"left": True})
    with store.read() as c:
        assert MS.row(c, plan) is None and MS.row(c, key) is None
        assert MS.row(c, surviving) is not None
        assert not c.execute("SELECT 1 FROM credentials WHERE id='departing-key'").fetchone()
        assert paid_by.picks(c, bot) == paid_by.picks(c, coworker) == {}
        assert c.execute("SELECT state FROM model_logins WHERE id='ongoing'").fetchone()[0] == "canceled"
        assert not c.execute("SELECT 1 FROM subscription_connects WHERE login_id='ongoing'").fetchone()
        assert {
            row[0] for row in c.execute("SELECT target FROM events WHERE action='agent_subscription.owner_left'")
        } == {
            bot,
            coworker,
        }


def test_removing_an_exact_choice_restarts_only_that_agents_turn_and_lists_the_loss_for_admins(api):
    setup(api)
    company(api)
    for bot in ("ops", "cpo"):
        on(api, bot, "claude", "claude-sonnet-5-5")
    box = machine(api, signed_in=True)
    assign(api, box, "cpo")
    report(api, box, ["ops", "cpo"])
    sid = add(api, token="cara-test")["id"]
    store = api.app.state.store
    with store.transaction() as c:
        bot = bot_id(c, "ops")
        paid_by.save(
            c,
            api.app.state.auth,
            Identity("human:ana", "owner"),
            bot,
            {"claude": paid_by.Pick(kind="plan", subscription_id=sid)},
        )
    post(api, "chat/ops", {"text": "Summarize the account."})
    post(api, "chat/cpo", {"text": "List the open questions."})
    picked = claim(api, box, "ops")
    unaffected = claim(api, box, "cpo")
    assert picked and unaffected
    path = f"/api/v2/model-subscriptions/{sid}"
    response = api.delete(path, headers=headers("cara-test"))
    assert response.status_code == 409 and response.json()["error"]["running"] == 1
    response = api.delete(path, headers=headers("cara-test"), params={"confirm": True})
    assert response.status_code == 200 and response.json()["stopped"] == 1
    assert post(api, f"attempts/{picked['id']}/renew", {}, box["token"])["stop"]
    assert not post(api, f"attempts/{unaffected['id']}/renew", {}, box["token"]).get("stop")

    def lost(token):
        return [check for check in get(api, "health", token)["checks"] if check["id"] == "paid_by_unset"]

    assert [item["bot"] for item in lost("ben-test")[0]["items"]] == [bot]
    assert not lost("cara-test")
    with store.transaction() as c:
        owner = Identity("human:ana", "owner")
        paid_by.save(c, api.app.state.auth, owner, bot, {"claude": paid_by.Pick(kind="pool", person="ana")})
        paid_by.save(c, api.app.state.auth, owner, bot, {"claude": None})
    assert not lost("ben-test")


def report(api, computer, bots, *, modern=True, codex=False):
    caps = ["model_subscriptions_v1", "task_lanes_v2"]
    if modern:
        caps.append(paid_by.CAPABILITY)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test" if modern else "0.6.0",
            "platform": "test",
            "capabilities": caps,
            "readiness": {
                "schema_version": 1,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "ready"},
                    **({"codex": {"installed": True, "authenticated": "ready"}} if codex else {}),
                },
                "bots": {bot: {"runtime": "claude", "ready": True} for bot in bots},
            },
        },
        token=computer["token"],
    )


def choose(api, bot, pick, token="ana-test", expected=200):
    return put(api, f"bots/{bot}/subscriptions", {"subscriptions": {"claude": pick}}, token, expected)


def test_claim_binds_each_agents_credential_and_keeps_it_out_of_receipts(api):
    setup(api)
    company(api)
    first = add(api, secret="sk-ant-oat01-first-acme-provider-secret")["id"]
    second = add(api, secret="sk-ant-oat01-second-acme-provider-secret")["id"]
    box = machine(api)
    assign(api, box, "cpo")
    for bot, sid in (("ops", first), ("cpo", second)):
        on(api, bot, "claude", "claude-sonnet-5-5")
        choose(api, bot, {"kind": "plan", "subscription_id": sid})
        post(api, f"chat/{bot}", {"text": "Check the provider."})
    report(api, box, ["ops", "cpo"])
    assert [r for r in get(api, "computer-subscriptions")["assignments"] if r["runtime"] == "claude"] == [
        {
            "runner_id": box["runner_id"],
            "runtime": "claude",
            "mixed": True,
            "subscription_id": "",
            "person": "",
            "set": None,
        }
    ]
    key = "paid-by-test-claim"
    first_turn = claim(api, box, "ops", key=key)
    second_turn = claim(api, box, "cpo")
    assert first_turn["attempt_credential"]["id"] == first
    assert second_turn["attempt_credential"]["id"] == second
    assert first_turn["attempt_credential"]["value"] != second_turn["attempt_credential"]["value"]
    assert first_turn["attempt_credential_required"] is True
    assert first_turn["config"]["fallback"] == {"off": True} and not first_turn["gateway"]
    assert not get(api, "credential-runtime", first_turn["token"]).get("subscriptions")
    choose(api, "ops", {"kind": "plan", "subscription_id": second})
    assert claim(api, box, "ops", key=key)["attempt_credential"]["id"] == first
    assert not post(api, f"attempts/{first_turn['id']}/renew", {}, box["token"]).get("stop")
    with api.app.state.store.read() as c:
        receipt = c.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
        assert receipt and "acme-provider-secret" not in str(dict(receipt))
    refused = put(api, "computer-subscriptions", {}, expected=410)
    assert "/api/v2/bots/{bot}/subscriptions" in refused["error"]["detail"]
    assert choose(api, "ops", None, token=first_turn["token"], expected=403)


@pytest.mark.parametrize("mismatch", [False, True])
def test_old_runner_admits_only_turns_that_match_its_current_payer(api, mismatch):
    from backend.readiness import can_run
    from backend.start_places import summaries

    setup(api)
    company(api)
    first = add(api)["id"]
    second = add(api, secret="sk-ant-oat01-acme-provider-other-secret")["id"] if mismatch else first
    box = machine(api, signed_in=True)
    assign(api, box, "cpo")
    report(api, box, ["ops", "cpo"], modern=False)
    with api.app.state.store.transaction() as c:
        MS._set(c, box["runner_id"], "claude", first, "human:ana")
        model = providers.load(c)["model"]
    choose(api, "ops", {"kind": "plan", "subscription_id": first})
    choose(api, "cpo", {"kind": "plan", "subscription_id": second})
    for bot in ("ops", "cpo"):
        on(api, bot, "claude", model)
        post(api, f"chat/{bot}", {"text": "Check the provider."})
    with api.app.state.store.read() as c:
        assert can_run(c, bot_id(c, "ops"), "claude")["can_run"] is True
        readiness = can_run(c, bot_id(c, "cpo"), "claude")
        if mismatch:
            assert readiness["problem"] == "Update Test Mac"
            counts = summaries(c, [box["runner_id"]], api.app.state.store.settings)
            [wait] = counts[box["runner_id"]]["waits"]
            assert wait["reason"] == "a runner update" and wait["count"] == 1
        else:
            assert readiness["can_run"] is True

    cpo = claim(api, box, "cpo")
    if mismatch:
        assert cpo is None
    else:
        assert cpo and cpo["subscriptions"][0]["id"] == first
    turn = claim(api, box, "ops")
    assert turn and turn["credential_vault"] and "attempt_credential" not in turn
    assert "attempt_credential_required" not in turn
    assert turn["config"]["fallback"] == {"off": True} and not turn["gateway"]
    assert turn["subscriptions"][0]["source"] == "computer"
    [sent] = get(api, "credential-runtime", turn["token"])["subscriptions"]
    assert sent["id"] == first and sent["value"] == TOKEN
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "legacy-payer"}, box["token"])

    if mismatch:
        report(api, box, ["ops", "cpo"])
        assert claim(api, box, "cpo")["attempt_credential"]["id"] == second


def test_pool_wait_is_visible_in_health_and_rechecks_without_a_gateway_fallback(api):
    from backend.tests.helpers.check_models import finish

    setup(api)
    company(api)
    first = add(api)["id"]
    on(api, "ops", "claude", "claude-sonnet-5-5")
    box = machine(api, signed_in=True)
    report(api, box, ["ops"])
    choose(api, "ops", {"kind": "pool", "person": "ana"})
    with api.app.state.store.transaction() as c:
        soon = datetime.now(timezone.utc) + timedelta(hours=2)
        reading(c, first, 99.5, soon)
    post(api, "chat/ops", {"text": "Check the pool."})
    assert claim(api, box, "ops") is None
    health = get(api, "health")
    wait = next(check for check in health["checks"] if check["id"] == "paid_by_wait")
    assert any("next reset" in item["text"] and "admin" in item["text"] for item in wait["items"])
    detail = get(api, "bots/ops/subscriptions?last_turn=true")
    assert detail["can_edit"]
    [blocked] = detail["last_turn"]["waits"]
    assert "next reset" in blocked["reason"] and "admin" in blocked["fixer"]
    assert datetime.fromisoformat(blocked["until"]) == soon
    assert get(api, "bots/ops/subscriptions?last_turn=true", "ben-test")["last_turn"] is None
    with api.app.state.store.transaction() as c:
        reading(c, first, 99.49, soon)
    turn = claim(api, box, "ops")
    assert turn["attempt_credential"]["id"] == first
    finish(api, box, turn, {"runtime": "claude", "model": "claude-sonnet-5-5", "billing": "subscription"})
    put(api, "bots/ops/subscriptions", {"subscriptions": {"claude": None}})
    detail = get(api, "bots/ops/subscriptions?last_turn=true")
    label = next(s["name"] for s in get(api, "model-subscriptions")["subscriptions"] if s["id"] == first)
    assert detail["last_turn"]["payers"] == [label]
    assert not detail["last_turn"]["waits"]
    assert detail["last_turn"]["at"] and not detail["subscriptions"]
    assert get(api, "bots/ops/subscriptions?last_turn=true", "ben-test")["last_turn"] is None


def test_paid_by_catalog_and_agent_report_the_same_assignment_rights_as_writes(api):
    setup(api)
    own = add(api, token="cara-test")["id"]
    other = add(api)["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
    assert get(api, "bots/ops/subscriptions", "cara-test")["can_edit"]
    catalog = get(api, "model-subscriptions", "cara-test")
    assert {s["id"]: s["can_assign"] for s in catalog["subscriptions"]} == {own: True, other: False}
    assert all(s["can_assign"] for s in get(api, "model-subscriptions", "ben-test")["subscriptions"])


def test_legacy_pool_keeps_its_usable_binding_and_rotates_with_scoped_reads(api):
    from backend import bot_settings, login_holds
    from backend.execution import fallback_choice, limit_lifted

    setup(api)
    company(api)
    current, soon, later = [add(api, name=name)["id"] for name in ("Current", "Soon", "Later")]
    box = machine(api, signed_in=True)
    report(api, box, ["ops"], modern=False, codex=True)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    choose(api, "ops", {"kind": "pool", "person": "ana"})
    job = post(api, "chat/ops", {"text": "Check the legacy pool."})["id"]
    store, execution = api.app.state.store, api.app.state.execution
    who = Identity("runner:" + box["runner_id"], "runner", runner_id=box["runner_id"])
    with store.transaction() as c:
        MS._set(c, box["runner_id"], "claude", current, "human:ana", "human:ana")
        now = datetime.now(timezone.utc)
        for hours, sid in enumerate((soon, later, current), 1):
            reading(c, sid, 40, now + timedelta(hours=hours))
        runner_row = execution.runner(c, who)
        config = {"runtime": "claude", "model": "claude-sonnet-5-5"}
        assert paid_by.resolve(c, "ops", **config).subscription["id"] == soon
        assert paid_by.for_runner(c, runner_row, "ops", config).subscription["id"] == current
        c.execute("UPDATE model_subscriptions SET status='needs_reconnect' WHERE id=?", (current,))
        traces = []
        c.set_trace_callback(traces.append)
        try:
            assert execution.candidate(c, who, Claim(), runner_row)["id"] == job
        finally:
            c.set_trace_callback(None)
        assert sum("AS payer_usage FROM model_subscriptions" in query for query in traces) == 1
        assert not any("AS granted_at FROM computer_subscriptions" in query for query in traces)
    turn = claim(api, box, "ops")
    assert turn["subscriptions"][0]["id"] == soon
    assert get(api, "credential-runtime", turn["token"])["subscriptions"][0]["id"] == soon
    with store.transaction() as c:
        bot_settings.update(c, "ops", {"fallback": {"harness": "codex", "model": "gpt-5.5"}})
        now = H.now()
        for sid in (soon, later):
            login_holds.record(c, "", login_holds.subscription_login(sid), "claude", now, reset=H.shift(now, hours=2))
        config = bot_settings.read(c, "ops")
        assert fallback_choice(c, store.settings, box["runner_id"], "ops", config) == ("", "codex")
        assert limit_lifted(c, store.settings, "ops", turn["id"], now) is None


def test_pool_uses_next_readable_plan_and_health_reports_only_an_exhausted_pool(api):
    setup(api)
    company(api)
    first = add(api, name="First")["id"]
    next_plan = add(api, name="Next")["id"]
    box = machine(api)
    report(api, box, ["ops"])
    on(api, "ops", "claude", "claude-sonnet-5-5")
    choose(api, "ops", {"kind": "pool", "person": "ana"})
    post(api, "chat/ops", {"text": "Find a readable credential."})
    store = api.app.state.store
    with store.transaction() as c:
        now = datetime.now(timezone.utc)
        first_credential, next_credential = (MS.row(c, sid)["credential_id"] for sid in (first, next_plan))
        for hours, sid in enumerate((first, next_plan), 1):
            reading(c, sid, 40, now + timedelta(hours=hours))
        saved = c.execute("SELECT ciphertext FROM credentials WHERE id=?", (next_credential,)).fetchone()[0]
        c.execute("UPDATE credentials SET ciphertext=X'FF' WHERE id IN (?,?)", (first_credential, next_credential))
    assert claim(api, box, "ops") is None
    wait = next(check for check in get(api, "health")["checks"] if check["id"] == "paid_by_wait")
    assert any(MS.PAYER_UNAVAILABLE in item["text"] for item in wait["items"])
    with store.transaction() as c:
        c.execute("UPDATE credentials SET ciphertext=? WHERE id=?", (saved, next_credential))
    assert not any(check["id"] == "paid_by_wait" for check in get(api, "health")["checks"])
    assert claim(api, box, "ops")["attempt_credential"]["id"] == next_plan


@pytest.mark.parametrize("modern", [False, True])
def test_unset_tool_does_not_fall_back_to_a_paid_tool(api, modern):
    from backend import bot_settings

    setup(api)
    company(api, enabled=["anthropic", "openai"])
    key = add(api, "openai_key", "sk-acme-fake-test-key-123456")["id"]
    box = machine(api, signed_in=True)
    report(api, box, ["ops"], modern=modern, codex=True)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    put(api, "bots/ops/subscriptions", {"subscriptions": {"codex": {"kind": "key", "subscription_id": key}}})
    with api.app.state.store.transaction() as c:
        bot_settings.update(c, "ops", {"fallback": {"harness": "codex", "model": "gpt-5.5"}})
    post(api, "chat/ops", {"text": "Run the unset Claude tool."})
    config = claim(api, box, "ops")["config"]
    assert config["runtime"] == "claude"
    assert config["blocked_fallback_runtimes"] == ["codex"]
    assert config.get("fallback", {}).get("harness") != "codex"
    if not modern:
        assert config["fallback"] == {"off": True}


def test_saved_fallback_session_waits_when_its_tool_gets_a_paid_by(api, monkeypatch):
    from backend.tests.helpers.login_holds import at, events
    from backend.tests.helpers.model_subscriptions import pick

    setup(api)
    company(api, enabled=["anthropic", "openai"])
    key = add(api, "openai_key", "sk-acme-fake-test-key-123456")["id"]
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    box = machine(api, signed_in=True, caps=(paid_by.CAPABILITY, "source_continue_v1", "capacity_continue_v1"))
    on(api, "ops", "claude", "claude-sonnet-5-5")
    post(api, "chat/ops", {"text": "Finish the saved work."})
    first = claim(api, box, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "primary-thread"}, box["token"])
    saved = {
        "thread_id": "fallback-thread",
        "runtime": "codex",
        "harness": "codex",
        "model": "gpt-5.5",
        "reasoning_effort": "",
        "profile": "",
        "epoch": None,
    }
    seq = events(api, box, first, [("tool", "Read"), ("error", "Usage limit; resets at 2026-10-19T07:00:00Z")])
    post(
        api,
        f"attempts/{first['id']}/complete",
        {"outcome": "failed", "limited": True, "fallback": "codex", "source_continuation": saved, "last_seq": seq},
        box["token"],
    )
    pick(api, "ops", "codex", key, kind="key")
    at(monkeypatch, "2026-10-19T07:01:00.000000Z", api)
    assert claim(api, box, "ops") is None
    wait = next(check for check in get(api, "health")["checks"] if check["id"] == "paid_by_wait")
    assert [item["bot"] for item in wait["items"]] == ["ops"]

    pick(api, "ops", "codex")
    again = claim(api, box, "ops")
    assert again["job_id"] == first["job_id"] and again["source_continuation"] == saved


def test_moving_clears_only_the_other_owners_payers(api):
    setup(api)
    owner_plan = add(api)["id"]
    member_key = add(api, "openai_key", "sk-acme-fake-test-key-654321", token="cara-test")["id"]
    box = machine(api)
    other = runner(api, label="Other Mac")
    with api.app.state.store.transaction() as c:
        bot = bot_id(c, "ops")
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot=?", (bot,))
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (other["runner_id"],))
    choose(api, "ops", {"kind": "plan", "subscription_id": owner_plan})
    put(
        api,
        "bots/ops/subscriptions",
        {
            "subscriptions": {
                "codex": {"kind": "key", "subscription_id": member_key},
            }
        },
        "cara-test",
    )
    move = assign(api, other, "ops", generation=1, operator="cara-test")
    assert move["paid_by_cleared"] == ["claude"]
    with api.app.state.store.read() as c:
        assert set(paid_by.picks(c, bot)) == {"codex"}
    assign(api, box, "ops", generation=2)
    choose(api, "ops", {"kind": "plan", "subscription_id": owner_plan})
    assert "paid_by_cleared" not in assign(api, other, "ops", generation=3)


@pytest.mark.parametrize(("automatic", "runner_owner"), [(False, "cara"), (True, "cara"), (True, "ana")])
def test_first_placement_by_comanager_only_keeps_default_pool_on_owners_computer(api, automatic, runner_owner):
    from backend import placement

    setup(api)
    add(api)
    box = runner(api, operator=runner_owner)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "UPDATE bot_config SET owner_id='ana',created_by='human:cara',bot_owners=? WHERE bot='ops'",
            (encode(["cara"]),),
        )
        paid_by.seed_owner_pool(c, "ops", "ana", "human:ana", H.now())
        if automatic:
            c.execute("UPDATE bots SET state='active' WHERE id='ops'")
            placed = placement.auto_place(c, api.app.state.execution, "ops", Identity("human:cara", "human"))
            assert placed["runner_id"] == box["runner_id"]
    if not automatic:
        assert assign(api, box, "ops", operator="cara-test")["paid_by_cleared"] == ["claude"]
    with store.read() as c:
        assert bool(paid_by.picks(c, "ops")) == (runner_owner == "ana")


@pytest.mark.parametrize("runner_owner", ["cara", "ana", "ben"])
def test_placement_sweep_keeps_explicit_picks_except_a_foreign_pool_on_a_third_party_computer(api, runner_owner):
    from backend import placement
    from backend.tests.helpers.model_subscriptions import pick

    setup(api)
    add(api)
    key = add(api, "openai_key", "sk-acme-fake-test-key-654321", token="cara-test")["id"]
    box = runner(api, operator=runner_owner)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara',created_by='human:cara' WHERE bot='ops'")
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (box["runner_id"],))
    pick(api, "ops", "claude", person="ana")
    pick(api, "ops", "codex", key, kind="key", token="cara-test")
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE id='ops'")
        assert dict(placement.sweep(c, api.app.state.execution))["ops"]["runner_id"] == box["runner_id"]
        choices = paid_by.picks(c, "ops")
        assert choices["codex"]["subscription_id"] == key
        assert (choices.get("claude") or {}).get("person") == (None if runner_owner == "ben" else "human:ana")


def test_member_restore_checks_a_foreign_plan_before_placing_the_agent(api):
    from backend import bot_settings
    from backend.tests.helpers.shared_bots import revision

    setup(api)
    plan = add(api)["id"]
    box = machine(api, signed_in=True)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (box["runner_id"],))
        bot_settings.update(c, "ops", {"materialize": True})
    choose(api, "ops", {"kind": "plan", "subscription_id": plan})
    post(api, "bots/ops/archive", {"expected_revision": revision(api, "ops")})
    post(api, "bots/ops/restore", {}, "cara-test")
    with store.read() as c:
        assert paid_by.picks(c, "ops") == {}
        assert c.execute("SELECT runner_id FROM assignments WHERE bot='ops'").fetchone()[0] == box["runner_id"]
    lost = next(row for row in get(api, "health")["checks"] if row["id"] == "paid_by_unset")
    assert [item["bot"] for item in lost["items"]] == ["ops"]


def test_a_comanager_can_place_an_agent_on_its_seeded_pool(api):
    setup(api)
    plan = add(api)["id"]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET bot_owners=? WHERE bot='ops'", (encode(["cara"]),))
        paid_by.seed_owner_pool(c, "ops", "ana", "human:ana", H.now())
    box = runner(api)
    assign(api, box, "ops", operator="cara-test")
    with store.read() as c:
        assert paid_by.resolve(c, "ops", "claude", "").subscription["id"] == plan


@pytest.mark.parametrize("lost", ["cutover", "provider", "computer_binding"])
def test_lost_lease_payer_returns_the_job_without_a_failed_start(api, lost):
    from backend.execution import failed_starts

    setup(api)
    company(api)
    sid = add(api)["id"]
    on(api, "ops", "claude", "claude-sonnet-5-5")
    box = machine(api, signed_in=True)
    store = api.app.state.store
    report(api, box, ["ops"], modern=lost != "computer_binding")
    if lost == "computer_binding":
        with store.transaction() as c:
            MS._set(c, box["runner_id"], "claude", sid, "human:ana")
    choose(api, "ops", {"kind": "plan", "subscription_id": sid})
    post(api, "chat/ops", {"text": "Check the provider."})
    turn = claim(api, box, "ops", key="payer-lost")
    assert turn
    with store.transaction() as c:
        if lost == "provider":
            c.execute("UPDATE model_subscriptions SET status='needs_reconnect' WHERE id=?", (sid,))
        elif lost == "computer_binding":
            c.execute("DELETE FROM computer_subscriptions WHERE runner_id=?", (box["runner_id"],))
        else:
            c.execute("UPDATE attempt_subscriptions SET delivery='agent' WHERE attempt_id=?", (turn["id"],))
    if lost == "provider":
        assert claim(api, box, "ops", key="payer-lost") is None
    else:
        report(api, box, ["ops"], modern=False)
        response = api.get("/api/v2/credential-runtime", headers=headers(turn["token"]))
        assert response.status_code == 409 and response.json()["error"] == "payer_wait"
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"
        assert failed_starts(c, turn["job_id"]) == 0
    assert claim(api, box, "ops") is None
    choose(api, "ops", None)
    retry = claim(api, box, "ops")
    assert retry["job_id"] == turn["job_id"] and not retry.get("subscriptions")


def test_pool_prefers_measured_plan_and_uses_the_latest_reading(api):
    setup(api)
    newer = add(api, name="Measured")["id"]
    unknown = add(api, name="Unmeasured")["id"]
    choose(api, "ops", {"kind": "pool", "person": "ana"})
    end = datetime.now(timezone.utc) + timedelta(days=3)
    with api.app.state.store.transaction() as c:
        bot = bot_id(c, "ops")
        reading(c, newer, 99.5, end)
        assert paid_by.resolve(c, bot, "claude", "").subscription["id"] == unknown
        key = refresh.plan_key(newer)
        meta = H._json(c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()[0], {})
        meta["turn_weekly"] = {**meta["reading"], "reported_at": H.shift(H.now(), seconds=1), "used_percent": 55}
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (encode(meta), key))
        assert paid_by.resolve(c, bot, "claude", "").subscription["id"] == newer


def test_claim_batches_payer_and_refresh_park_rows_across_queued_agents(api):
    from backend.tests.test_claude_login import full

    setup(api)
    company(api)
    plan = add(api, "claude_login", full())["id"]
    box = machine(api)
    bots = ["ops", "cpo", "finance"]
    jobs = {}
    for slug in bots:
        on(api, slug, "claude", "claude-sonnet-5-5")
        if slug != "ops":
            assign(api, box, slug)
        choose(api, slug, {"kind": "pool", "person": "ana"})
        jobs[slug] = post(api, f"chat/{slug}", {"text": "Check with the payer."})["id"]
    report(api, box, bots)
    store, execution = api.app.state.store, api.app.state.execution
    who = Identity("runner:" + box["runner_id"], "runner", runner_id=box["runner_id"])
    with store.transaction() as c:
        claude_login._put_lease(c, plan, lease_id="refresh")
        for slug in bots[:-1]:
            c.execute(
                "INSERT INTO claude_login_parks VALUES(?,'prior-attempt',?,1,?,?)",
                (jobs[slug], plan, H.shift(H.now(), minutes=5), H.now()),
            )
        traces = []
        c.set_trace_callback(traces.append)
        try:
            runner_row = execution.runner(c, who)
            assert execution.candidate(c, who, Claim(), runner_row)["id"] == jobs["finance"]
            assert execution.candidate(c, who, Claim(), runner_row, job_id=jobs["ops"]) is None
        finally:
            c.set_trace_callback(None)
        park_reads = [q for q in traces if q.startswith("SELECT p.job_id,p.subscription_id")]
        assert len(park_reads) == 2
        assert "j.bot_id IN" in park_reads[0] and "j.id=" in park_reads[1]
        assert all("attempts CROSS JOIN" not in query for query in traces)


def test_unreadable_payer_does_not_block_the_next_job(api):
    setup(api)
    company(api)
    broken = add(api)["id"]
    ready = add(api, secret="sk-ant-oat01-working-acme-provider-secret")["id"]
    box = machine(api)
    assign(api, box, "cpo")
    for bot, sid in (("ops", broken), ("cpo", ready)):
        on(api, bot, "claude", "claude-sonnet-5-5")
        choose(api, bot, {"kind": "plan", "subscription_id": sid})
        post(api, f"chat/{bot}", {"text": "Check the provider."})
    report(api, box, ["ops", "cpo"])
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE credentials SET ciphertext=X'FF' WHERE id=?", (MS.row(c, broken)["credential_id"],))
        c.execute("UPDATE jobs SET redirect=1 WHERE bot_id='ops' AND state='queued'")
    assert claim(api, box)["attempt_credential"]["id"] == ready
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE bot_id='ops'").fetchone()[0] == "queued"
        assert not c.execute("SELECT 1 FROM attempts WHERE bot_id='ops'").fetchone()


def test_check_job_uses_its_payer_even_when_the_default_waits(api):
    from backend.tests.helpers.check_models import finish, parent_turn

    setup(api)
    box, parent, original = parent_turn(api)
    company(api)
    report(api, box, ["ops"], codex=True)
    key = add(api, "openai_key", "sk-acme-fake-test-key-123456")["id"]
    put(
        api,
        "bots/ops/subscriptions",
        {
            "subscriptions": {
                "claude": {"kind": "pool", "person": "ana"},
                "codex": {"kind": "key", "subscription_id": key},
            }
        },
    )
    sub = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Review the import",
            "body": "Review it.",
            "parent": parent["id"],
            "check_model": "codex",
            "check_role": "review",
        },
        original["token"],
    )
    finish(api, box, original)
    post(api, "chat/ops", {"text": "This Claude turn must wait."})
    turn = claim(api, box, "ops")
    assert turn["task"]["id"] == sub["id"]
    assert turn["config"]["runtime"] == "codex" and turn["config"]["fallback"] == {"off": True}
    assert turn["attempt_credential"]["id"] == key


@pytest.mark.parametrize("gateway", [True, False])
def test_an_unset_tool_uses_its_own_readiness(api, gateway, monkeypatch):
    from backend import gateway_connection
    from backend.tests.helpers.check_models import finish, parent_turn

    setup(api)
    box, parent, original = parent_turn(api)
    company(api, enabled=["anthropic", "openai", "tico"])
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    choose(api, "ops", {"kind": "pool", "person": "ana"})
    report(api, box, ["ops"], codex=True)
    task = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Check",
            "body": "Review it.",
            "parent": parent["id"],
            "check_model": "codex",
            "check_role": "review",
        },
        original["token"],
    )
    finish(api, box, original)
    if gateway:
        with api.app.state.store.transaction() as c:
            c.execute(
                "UPDATE task_check_models SET runtime='pi',model='tico-medium-1.0',harness='pi' WHERE task_id=?",
                (task["id"],),
            )
            c.execute("UPDATE registry_metadata SET value=json_set(value,'$.preset','gateway') WHERE key='providers'")
    runtime = "pi" if gateway else "codex"
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [paid_by.CAPABILITY, "task_lanes_v2"],
            "readiness": {
                "schema_version": 1,
                "isolated": True,
                "runtimes": {"claude": {"installed": True}, runtime: {"installed": True, "authenticated": "missing"}},
                "bots": {
                    "ops": {
                        "runtime": "claude",
                        "ready": not gateway,
                        "repository_present": True,
                        "configuration_valid": True,
                    }
                },
            },
        },
        token=box["token"],
    )
    turn = claim(api, box, "ops")
    if gateway:
        assert turn["config"]["runtime"] == "pi" and turn["task"]["id"] == task["id"]
    else:
        assert turn is None


def test_health_fills_its_limit_with_readable_payer_losses(api, monkeypatch):
    from backend import health

    store = api.app.state.store
    monkeypatch.setattr(health, "DIAGNOSTIC_LIMIT", 1)
    auth = api.app.state.auth
    monkeypatch.setattr(auth, "bot_accesses", lambda c, who, bots: {b: {"read": b == "ops"} for b in bots})
    with store.transaction() as c:
        for bot in ("ops", "inbox"):
            H.event(c, H.TICO_ACTOR, "agent_subscription.provider_removed", bot, {"runtime": "claude"})
        result = health._lost_payers(c, Identity("human:ana", "owner"), auth)
        assert [item["bot"] for item in result["items"]] == ["ops"]


@pytest.mark.parametrize("kind, broken", [("codex_login", False), ("codex_login", True), ("openai_key", False)])
def test_only_a_readable_codex_plan_reaches_its_agents_shell(api, kind, broken):
    setup(api)
    company(api)
    legacy = add(api, "codex_login", codex_file(), name="Old computer binding")["id"]
    value = codex_file() if kind == "codex_login" else "sk-acme-fake-test-key-123456"
    shell = add(api, kind, value)["id"]
    main = add(api)["id"]
    box = machine(api, signed_in=True)
    assign(api, box, "cpo")
    for bot in ("ops", "cpo"):
        on(api, bot, "claude", "claude-sonnet-5-5")
        choose(api, bot, {"kind": "plan", "subscription_id": main})
        post(api, f"chat/{bot}", {"text": "Check the shell."})
    report(api, box, ["ops", "cpo"], codex=True)
    with api.app.state.store.transaction() as c:
        MS._set(c, box["runner_id"], "codex", legacy, "human:ana")
        if broken:
            c.execute("UPDATE credentials SET ciphertext=X'FF' WHERE id=?", (MS.row(c, shell)["credential_id"],))
    put(
        api,
        "bots/ops/subscriptions",
        {
            "subscriptions": {
                "codex": {"kind": "plan" if kind == "codex_login" else "key", "subscription_id": shell},
            }
        },
    )
    ops = claim(api, box, "ops")
    cpo = claim(api, box, "cpo")
    assert ops["attempt_credential"]["id"] == main
    if kind == "codex_login" and not broken:
        delivered = ops["attempt_credential"]["shell"]
        assert (delivered["id"], delivered["runtime"]) == (shell, "codex")
    else:
        assert "shell" not in ops["attempt_credential"]
    assert "shell" not in cpo["attempt_credential"]
    assert not get(api, "credential-runtime", ops["token"]).get("subscriptions")
    assert not get(api, "credential-runtime", cpo["token"]).get("subscriptions")


def test_revoking_a_converted_parent_fences_a_lease_even_after_the_pick_changes(api):
    setup(api)
    company(api)
    first = add(api)["id"]
    on(api, "ops", "claude", "claude-sonnet-5-5")
    box = machine(api, signed_in=True)
    report(api, box, ["ops"])
    choose(api, "ops", {"kind": "plan", "subscription_id": first})
    store = api.app.state.store
    with store.transaction() as c:
        sid = MS.row(c, first)["credential_id"]
        bot = bot_id(c, "ops")
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) "
            "VALUES('old-parent',?,'human:ana','human:ana',?)",
            (sid, H.now()),
        )
        c.execute("UPDATE agent_subscriptions SET parent_grant='old-parent' WHERE bot=?", (bot,))
    post(api, "chat/ops", {"text": "Check the provider."})
    turn = claim(api, box, "ops", key="parent-grant-attempt")
    assert turn["attempt_credential"]["id"] == first
    choose(api, "ops", None)
    with store.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE id='old-parent'", (H.now(),))
        paid_by.revoke_parent(c, "human:ana", "old-parent")
    assert claim(api, box, "ops", key="parent-grant-attempt") is None
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (turn["job_id"],)).fetchone()[0] == "queued"
        assert not paid_by.picks(c, bot)
