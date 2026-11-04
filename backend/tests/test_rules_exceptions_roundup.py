"""A saved rule on the retired "exceptions" event becomes a task_roundup rule (backend/migrations/0078_rules_exceptions_roundup.py),
so validation and loading never meet an event that no longer exists. Every other rule keeps its event."""
import importlib
import json
import sqlite3

from backend import automations, rules

MIGRATION = importlib.import_module("backend.migrations.0078_rules_exceptions_roundup")
FLOWS = importlib.import_module("backend.migrations.0079_exceptions_flows_roundup")
NOW = "2026-11-01T00:00:00.000000Z"


def saved(conn, rule_id, event):
    conn.execute("INSERT INTO rules(id,scope_kind,event,then_outcome,created_by,created_at,updated_at) "
                 "VALUES(?,'team',?,'wake_bot','human:ana',?,?)", (rule_id, event, NOW, NOW))


def converted(name, event):
    """An automation the redesign cutover made from a wake rule on `event`: the rule is the step's `when` and its args."""
    rule = {"id": "old", "scope_kind": "team", "event": event, "note": "Ignore the Acme ones."}
    step = {"id": "r1", "type": "filter", "enabled": True, "rules": [
        {"when": [{"field": "event", "op": "is", "value": event}], "match": "all", "stop": True,
         "then": [{"do": "wake_bot", "args": {"outcome": "wake_bot", "params": {}, "rule": rule}}]}]}
    return json.dumps({"name": name, "owner": "human:ana", "enabled": True, "steps": [step],
                       "trigger": {"type": "event", "name": "tico.wake"}})


def test_a_saved_exceptions_rule_moves_to_the_roundup_and_every_other_rule_keeps_its_event():
    conn = sqlite3.connect(":memory:")
    conn.executescript(rules.SCHEMA)
    saved(conn, "old", "exceptions")
    saved(conn, "kept", "assignment")
    saved(conn, "roundup", "task_roundup")
    MIGRATION.up(conn)
    events = dict(conn.execute("SELECT id, event FROM rules").fetchall())
    assert events == {"old": "task_roundup", "kept": "assignment", "roundup": "task_roundup"}
    assert all(event in rules.EVENTS for event in events.values()), "every saved rule names an event that exists"


def test_the_migration_does_nothing_on_a_database_without_rules():
    MIGRATION.up(sqlite3.connect(":memory:"))


def test_a_converted_exceptions_rule_moves_in_its_flow_and_saved_version_and_running_it_again_changes_nothing():
    conn = sqlite3.connect(":memory:")
    for statement in automations.SCHEMA:
        conn.execute(statement)
    flow = converted("Exceptions note", "exceptions")
    conn.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
                 "VALUES('a1','Exceptions note','human:ana','event',1,?,1,?,'human:ana',?)", (flow, NOW, NOW))
    # A bystander: its name says "Exceptions", its rule is on another event, so its flow is not rewritten.
    untouched = converted("Exceptions digest", "assignment")
    conn.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
                 "VALUES('a2','Exceptions digest','human:ana','event',1,?,1,?,'human:ana',?)", (untouched, NOW, NOW))
    conn.execute("INSERT INTO automation_versions(automation_id,n,actor,created_at,flow) VALUES('a1',1,'human:ana',?,?)",
                 (NOW, flow))

    def flows():
        return (dict(conn.execute("SELECT id, flow FROM automations").fetchall()),
                conn.execute("SELECT flow FROM automation_versions").fetchone()[0])

    FLOWS.up(conn)
    now, version = flows()
    rule = json.loads(now["a1"])["steps"][0]["rules"][0]
    assert rule["when"] == [{"field": "event", "op": "is", "value": "task_roundup"}]
    saved_rule = rule["then"][0]["args"]["rule"]
    assert saved_rule["event"] == "task_roundup" and saved_rule["note"] == "Ignore the Acme ones.", "the saved note stays"
    assert json.loads(now["a1"])["name"] == "Exceptions note", "a name is not an event"
    assert json.loads(version) == json.loads(now["a1"]), "a restored version brings no retired event back"
    assert now["a2"] == untouched
    FLOWS.up(conn)
    assert flows() == (now, version), "a second run changes nothing"


def test_a_call_webhook_payload_that_names_the_retired_event_is_not_renamed():
    """An action's own data is not an event: a webhook that posts {"event": "exceptions"} keeps it, in a flow that
    also holds a converted rule (which moves) and in a flow that holds only the webhook (which is left as it was)."""
    conn = sqlite3.connect(":memory:")
    for statement in automations.SCHEMA:
        conn.execute(statement)
    webhook = {"id": "r2", "type": "action", "enabled": True, "do": "call_webhook",
               "args": {"url": "https://hooks.acme.example/tico", "data": {"event": "exceptions"}}}
    mixed = json.loads(converted("Exceptions note", "exceptions"))
    mixed["steps"].append(webhook)
    only_webhook = json.dumps({"name": "Post exceptions", "owner": "human:ana", "enabled": True,
                               "trigger": {"type": "event", "name": "tico.wake"}, "steps": [webhook]})
    for aid, flow in (("a1", json.dumps(mixed)), ("a2", only_webhook)):
        conn.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,updated_at) "
                     "VALUES(?,?,'human:ana','event',1,?,1,?,'human:ana',?)", (aid, aid, flow, NOW, NOW))

    FLOWS.up(conn)
    now = dict(conn.execute("SELECT id, flow FROM automations").fetchall())
    moved = json.loads(now["a1"])
    assert moved["steps"][0]["rules"][0]["when"] == [{"field": "event", "op": "is", "value": "task_roundup"}]
    assert moved["steps"][0]["rules"][0]["then"][0]["args"]["rule"]["event"] == "task_roundup"
    assert moved["steps"][1] == webhook, "the webhook's data keeps its event"
    assert now["a2"] == only_webhook, "an automation with no converted rule is left exactly as it was"
