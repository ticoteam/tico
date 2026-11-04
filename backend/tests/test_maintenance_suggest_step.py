"""Suggest's privacy boundary, off-transaction HTTP, fresh conditions and Update receipts. No real network."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from backend import bot_settings as BS, botops_act, gateway_connection, suggestions, updates
from backend.maintenance_steps import suggest as step
from backend.maintenance_steps import update as update_step
from backend.store import H, Problem
from backend.tests.test_api import api, get, put  # noqa: F401
from backend.tests.test_maintenance_learn_step import complete, queue


def item(n=1, **changes):
    return {"id": f"sg_{n:032x}", "fingerprint": f"sha256:{n:064x}", "owner_role": "botops",
            "title": "Set up a manager", "body": "Give open work a manager.",
            "condition": {"all": [{"field": "bots_total", "op": "gt", "value": 0}]},
            "cooldown_days": 7, **changes}


def context(store, **changes):
    return {"store": store, "run_id": H.new_id(), "settings": {"key": "suggest", "enabled": 1},
            "now": H.now(), **changes}


def run_step(store, **changes):
    with store.read() as c:
        c.execute("PRAGMA query_only=ON")
        try:
            return step.run(c, context(store, **changes))
        finally:
            c.execute("PRAGMA query_only=OFF")


@pytest.fixture
def install(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setattr(store.settings, "gateway_key", "install-test-key")
    monkeypatch.setattr(store.settings, "gateway_url", "https://gateway.acme.example")
    with store.transaction() as c:
        BS.update(c, "ops", {"template": "botops"})
        suggestions.save_privacy(c, store.settings, "human:ana", {"share_stats_for_suggestions": True})
    return store


@pytest.fixture
def http(install, monkeypatch):
    calls, state = [], {"items": [item()], "status": 200, "reply": None}
    original = install.transaction
    writing = []

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as c:
            writing.append(c)
            try:
                yield c
            finally:
                writing.pop()

    def handle(request):
        assert not writing, "HTTP must run outside every write transaction"
        assert request.headers["authorization"] == "Bearer install-test-key"
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if state["status"] != 200:
            return httpx.Response(state["status"], text="secret remote error: do not persist")
        if request.url.path.endswith("/outcomes"):
            return httpx.Response(204)
        return httpx.Response(200, json=state["reply"] if state["reply"] is not None
                              else {"suggestions": state["items"], "version": 1})

    monkeypatch.setattr(install, "transaction", transaction)
    monkeypatch.setattr(suggestions, "TRANSPORT", httpx.MockTransport(handle))
    return calls, state


def receipt(store, ident=None):
    with store.read() as c:
        row = c.execute("SELECT * FROM suggestions WHERE id=?", (ident or item()["id"],)).fetchone()
        return dict(row) if row else None


def test_consent_off_makes_no_http_call(install, http):
    with install.transaction() as c:
        suggestions.save_privacy(c, install.settings, "human:ana", {"share_stats_for_suggestions": False})
    result = run_step(install)
    assert result["status"] == "skipped" and result["reason"] == "Privacy consent is off"
    assert http[0] == [] and receipt(install) is None


def test_open_source_cannot_enable_or_call_even_with_a_stored_opt_in(install, http, monkeypatch):
    monkeypatch.setattr(install.settings, "hosted", "")
    monkeypatch.setattr(install.settings, "gateway_key", "")
    monkeypatch.delenv("TICO_GATEWAY_KEY", raising=False)
    with install.transaction() as c:
        assert suggestions.privacy(c, install.settings) == {**suggestions.DEFAULTS, "available": False}
        with pytest.raises(Problem, match="Needs Tico Gateway"):
            suggestions.save_privacy(c, install.settings, "human:ana", {"share_stats_for_suggestions": True})
    result = run_step(install)
    assert result["status"] == "skipped" and result["reason"] == "Needs Tico Gateway"
    assert http[0] == []


def test_new_install_defaults_are_off_and_reporting_on(api):
    store = api.app.state.store
    with store.read() as c:
        assert suggestions.privacy(c, store.settings)["share_stats_for_suggestions"] is False
        assert suggestions.privacy(c, store.settings)["report_suggestion_outcomes"] is True


def test_privacy_api_enforces_owner_and_open_source_gate(api):
    value = get(api, "system/suggestions")
    assert value["share_stats_for_suggestions"] is False
    put(api, "system/suggestions", {"share_stats_for_suggestions": True}, expected=422)
    get(api, "system/suggestions", token="ben-test", expected=403)
    put(api, "system/suggestions", {"report_suggestion_outcomes": False}, token="ben-test", expected=403)
    assert put(api, "system/suggestions", {"report_suggestion_outcomes": False})["report_suggestion_outcomes"] is False
    assert botops_act.classify("PUT", "system/suggestions", {"share_stats_for_suggestions": True}) is None
    assert botops_act.classify("PUT", "system/suggestions", {"report_suggestion_outcomes": False}) == "do"


@pytest.mark.parametrize("mode", ["demo", "rehearsal"])
def test_demo_and_rehearsal_send_nothing(install, http, monkeypatch, mode):
    monkeypatch.setattr(install.settings, mode, True)
    assert run_step(install)["status"] == "skipped" and http[0] == []


def test_step_last_run_prevents_fetch_without_any_local_receipts(install, http):
    assert run_step(install, settings={"last_run_at": H.now()})["reason"] == "Already checked today"
    assert http[0] == []


def test_install_owned_suggestions_are_hidden_from_sql(install, http, api):
    run_step(install)
    from backend.tests.test_sql import query
    query(api, "SELECT * FROM suggestions", expected=422)


def test_fetch_is_once_daily_and_snapshot_contains_only_dictionary_counts(install, http):
    result = run_step(install)
    assert (result["status"], result["found"], result["woke"], result["cost_usd"]) == ("ran", 1, 0, 0)
    assert run_step(install)["reason"] == "Already checked today"
    assert len(http[0]) == 1
    path, facts = http[0][0]
    assert path == "/v1/suggestions" and facts["snapshot_version"] == 1
    assert set(facts) == {"snapshot_version", "bots_total", "bots_botops", "bots_goal_manager",
                          "bots_engineering_lead", "bots_finance_bot", "tasks_open", "automations_count"}
    assert set(facts) <= suggestions.FIELDS
    assert all(type(v) is int and v >= 0 for v in facts.values())
    assert facts["bots_botops"] == 1
    assert receipt(install)["owner_bot_id"] is None


@pytest.mark.parametrize("base,path", [
    ("https://gateway.acme.example/v1/gateway", "/v1/suggestions"),
    ("https://gateway.acme.example/v1/gateway/decisions", "/v1/suggestions"),
    ("https://gateway.acme.example/proxy/v1/gateway", "/proxy/v1/suggestions"),
])
def test_gateway_model_prefix_is_removed_for_suggest(install, http, monkeypatch, base, path):
    monkeypatch.setattr(install.settings, "gateway_url", base)
    assert run_step(install)["status"] == "ran"
    assert http[0][0][0] == path


def test_fresh_recheck_drops_a_stale_condition_without_delivery(install, http):
    with install.read() as c:
        tasks = suggestions.snapshot(c, install.settings)["tasks_open"]
    http[1]["items"] = [item(condition={"all": [{"field": "tasks_open", "op": "eq", "value": tasks}]})]
    run_step(install)
    with install.transaction() as c:
        H.task_create(c, "human:ana", "New private work", "Never leaves the install", "bot:ops", lint=False)
        assert suggestions.pending(c, "ops") == []
        assert update_step.plan_request(c, "ops", updates.today()) == "skip"
    row = receipt(install)
    assert row["status"] == row["outcome"] == "condition_false"
    assert row["delivered_at"] is None and row["update_batch_id"] is None


def test_fingerprint_cooldown_and_reported_receipt_allow_later_refetch(install, http):
    run_step(install)
    with install.transaction() as c:
        updates.send_request(c, queue(c), delivery=True)
        assert suggestions.store_new(c, [item(2, fingerprint=item()["fingerprint"])]) == 0
        assert c.execute("SELECT count(*) FROM suggestions").fetchone()[0] == 1
        future = H.shift(H.now(), days=8)
        suggestions.expire(c, future)
        assert c.execute("SELECT outcome FROM suggestions").fetchone()[0] == "expired"
        c.execute("UPDATE suggestions SET outcome_reported_at=?", (future,))
        assert suggestions.store_new(c, [item()], future) == 1
        row = c.execute("SELECT * FROM suggestions").fetchone()
        assert row["status"] == "new" and row["update_batch_id"] is None and row["outcome"] is None


def test_missing_role_waits_then_template_holder_gets_delivery_only_turn(install, http):
    http[1]["items"] = [item(owner_role="goal_manager")]
    run_step(install)
    with install.transaction() as c:
        assert suggestions.pending(c, "ops") == []
        assert receipt(install)["status"] == "new"
        BS.update(c, "ops", {"template": "goal-manager"})
        assert update_step.plan_request(c, "ops", updates.today()) == "deliver"
        row = queue(c)
        message = updates.send_request(c, row, delivery=True)
        assert "Suggestions:" in message["body"] and item()["id"] in message["body"]
        assert row["id"] in message["body"] and "do not post" in message["body"]
        assert suggestions.pending(c, "ops") == []
        assert len(suggestions.pending(c, "ops", row["id"])) == 1
        complete(c, c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone(), "Done")
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert updates.settle_sent(c, sent) is False
        assert c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()[0] == "delivered_without_update"
    assert receipt(install)["outcome"] == "delivered"


def test_post_feedback_is_bound_to_receiving_bot_and_request(install, http):
    http[1]["items"] = [item(), item(2)]
    run_step(install)
    with install.transaction() as c:
        row = queue(c)
        updates.send_request(c, row)
        updates.post(c, "ops", "- Finished the work", suggestions_seen=[item()["id"]], delivery_batch_id="other")
        assert c.execute("SELECT count(*) FROM suggestions WHERE outcome IS NOT NULL").fetchone()[0] == 0
        suggestions.feedback(c, "finance", row["id"], [item()["id"]], settled=True)
        assert c.execute("SELECT count(*) FROM suggestions WHERE outcome IS NOT NULL").fetchone()[0] == 0
        updates.post(c, "ops", "- Finished the work", suggestions_seen=[item()["id"]], delivery_batch_id=row["id"])
        assert c.execute("SELECT outcome FROM suggestions WHERE id=?", (item()["id"],)).fetchone()[0] == "acted"
        assert c.execute("SELECT outcome FROM suggestions WHERE id=?", (item(2)["id"],)).fetchone()[0] == "delivered"


def test_completed_normal_turn_without_a_post_is_still_delivery(install, http):
    run_step(install)
    with install.transaction() as c:
        row = queue(c)
        updates.send_request(c, row)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        complete(c, sent, "No update posted")
        updates.settle_sent(c, sent)
    assert receipt(install)["outcome"] == "delivered"


def test_redo_does_not_consume_suggestions_and_failed_send_leaves_them_pending(install, http, monkeypatch):
    run_step(install)
    with install.transaction() as c:
        row = dict(queue(c))
        row["redo"] = 1
        assert item()["id"] not in updates.send_request(c, row)["body"]
        assert c.execute("SELECT status FROM suggestions").fetchone()[0] == "new"
    def refuse(*args, **kwargs):
        raise RuntimeError("request refused")
    monkeypatch.setattr(updates.H, "say", refuse)
    with pytest.raises(RuntimeError, match="request refused"):
        with install.transaction() as c:
            updates.send_request(c, queue(c), delivery=True)
    assert receipt(install)["update_batch_id"] is None


def test_outcomes_off_sends_no_outcomes_and_does_not_stamp(install, http):
    with install.transaction() as c:
        suggestions.store_new(c, [item()])
        c.execute("UPDATE suggestions SET outcome='dismissed',status='dismissed'")
        suggestions.save_privacy(c, install.settings, "human:ana", {"report_suggestion_outcomes": False})
    assert run_step(install)["status"] == "ran"
    assert [path for path, _ in http[0]] == ["/v1/suggestions"]
    assert receipt(install)["outcome_reported_at"] is None


def test_outcomes_payload_is_only_id_and_outcome_and_bounded_to_100(install, http):
    with install.transaction() as c:
        suggestions.store_new(c, [item(n) for n in range(1, 103)])
        c.execute("UPDATE suggestions SET status='dismissed',outcome='dismissed'")
    http[1]["items"] = []
    result = run_step(install)
    path, payload = http[0][-1]
    assert path == "/v1/suggestions/outcomes" and len(payload) == 100
    assert all(set(row) == {"suggestion_id", "outcome"} and row["outcome"] == "dismissed" for row in payload)
    assert result["details"]["reported"] == 100
    with install.read() as c:
        assert c.execute("SELECT count(*) FROM suggestions WHERE outcome_reported_at IS NOT NULL").fetchone()[0] == 100


def test_reporting_error_does_not_acknowledge_and_can_be_retried(install, http):
    with install.transaction() as c:
        suggestions.store_new(c, [item()])
        c.execute("UPDATE suggestions SET status='dismissed',outcome='dismissed'")
    http[1]["status"] = 503
    with pytest.raises(suggestions.GatewayError, match="503"):
        suggestions.report(install)
    assert receipt(install)["outcome_reported_at"] is None
    http[1]["status"] = 200
    assert suggestions.report(install) == 1
    assert receipt(install)["outcome_reported_at"]


@pytest.mark.parametrize("failure", ["http", "malformed", "timeout"])
def test_hq_error_fails_without_any_rows_or_fetch_stamp(install, http, monkeypatch, failure):
    if failure == "http":
        http[1]["status"] = 500
    elif failure == "malformed":
        http[1]["reply"] = {"suggestions": [item(), item(2, cooldown_days=-1)], "version": 1}
    else:
        def timeout(request):
            raise httpx.ReadTimeout("secret key must not reach the reason")
        monkeypatch.setattr(suggestions, "TRANSPORT", httpx.MockTransport(timeout))
    result = run_step(install)
    assert result["status"] == "failed" and result["cost_usd"] == 0
    assert "secret" not in result["reason"]
    with install.read() as c:
        assert c.execute("SELECT count(*) FROM suggestions").fetchone()[0] == 0
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (suggestions.LAST_FETCH,)).fetchone() is None


def test_saved_gateway_target_is_used_without_an_environment_key(install, http, monkeypatch):
    monkeypatch.setattr(install.settings, "gateway_key", "")
    monkeypatch.delenv("TICO_GATEWAY_KEY", raising=False)
    monkeypatch.setattr(install.settings, "hosted", "invited")
    calls = []
    def saved(c, vault, settings):
        assert not c.in_transaction
        calls.append(vault)
        return "install-test-key", "https://gateway.acme.example", "key-fingerprint"
    monkeypatch.setattr(gateway_connection, "target", saved)
    assert run_step(install)["status"] == "ran" and len(calls) == 1


def test_person_money_cap_uses_spend_alert_company_owner_path(install, http):
    http[1]["items"] = [item(owner_role="person_money_cap")]
    assert run_step(install)["details"]["delivered"] == 1
    row = receipt(install)
    assert row["outcome"] == "delivered" and row["owner_bot_id"] is None
    with install.read() as c:
        notice = c.execute("SELECT recipient_id,kind FROM messages WHERE body=?",
                           (item()["title"] + "\n\n" + item()["body"],)).fetchone()
        assert tuple(notice) == ("human:ana", "notice")


def test_condition_semantics_missing_operands_and_no_coercion():
    condition = lambda **test: {"all": [test]}
    for op in ("eq", "ne", "gt", "gte", "lt", "lte"):
        assert not suggestions.matches(condition(field="tasks_open", op=op, value=0), {})
    assert not suggestions.matches(condition(field="tasks_open", op="ne", other_field="tasks_stuck"), {"tasks_open": 1})
    assert suggestions.matches(condition(field="tasks_open", op="gt", other_field="bots_total"),
                               {"tasks_open": 2, "bots_total": 1})
    assert suggestions.matches(condition(field="plan_tier", op="ne", value="trial"), {"plan_tier": "starter"})
    with pytest.raises(ValueError):
        suggestions.matches(condition(field="tasks_open", op="eq", value=True), {"tasks_open": 1})
    with pytest.raises(ValueError):
        suggestions.matches(condition(field="tasks_open", op="gt", value="1"), {"tasks_open": 2})
    assert not suggestions.matches(condition(field="tasks_open", op="gt", value=0), {"tasks_open": float("nan")})


def test_condition_validation_matches_hq_dictionary_types_and_bounds():
    invalid = [
        {"all": []},
        {"all": [{"field": "tasks_open", "op": "eq", "other_field": "plan_tier"}]},
        {"all": [{"field": "plan_tier", "op": "gt", "value": "starter"}]},
        {"all": [{"field": "tasks_open", "op": "eq", "value": 1_000_000_001}]},
    ]
    for condition in invalid:
        with pytest.raises(ValueError):
            suggestions.matches(condition, {"tasks_open": 1, "plan_tier": "starter"})
    condition = {"all": [{"field": "spend_month_usd", "op": "gt", "other_field": "spend_month_cap_usd"},
                         {"field": "source_github_connected", "op": "eq", "value": True},
                         {"field": "plan_tier", "op": "ne", "value": "trial"}]}
    assert suggestions.matches(condition, {"spend_month_usd": 51, "spend_month_cap_usd": 50,
                                          "source_github_connected": True, "plan_tier": "starter"})


def test_migration_is_idempotent_and_preserves_receipts():
    path = Path(__file__).resolve().parents[1] / "migrations" / "0086_suggestions.py"
    spec = importlib.util.spec_from_file_location("suggestions_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sqlite3.connect(":memory:") as c:
        migration.up(c)
        c.execute("INSERT INTO suggestions(id,fingerprint,owner_role,title,body,condition,cooldown_days,created_at,updated_at) "
                  "VALUES('id','hash','botops','Title','Body','{}',7,'then','then')")
        migration.up(c)
        assert c.execute("SELECT status FROM suggestions").fetchone()[0] == "new"
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE suggestions SET status='unknown'")
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
