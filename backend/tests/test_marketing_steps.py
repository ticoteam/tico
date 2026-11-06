"""The Marketing task type's step names (backend/marketing_tasks.py): the migration that renames a default-named step
on an install from before the rename and carries its rules and "What wakes" flows along, the In development step that
ensure adds in its board place, and the outcome closes the owner bot may make. Examples use Acme, a home-cleaning
company."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from backend import automations as A
from backend import hubdb as H
from backend import marketing_tasks as MT
from backend import rules as R
from backend.tests.test_api import api, post  # noqa: F401

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261106010106_marketing_step_names.py"
_spec = importlib.util.spec_from_file_location("marketing_step_names", MIGRATION)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

STAMP = "2026-11-05T10:00:00.000000Z"
# The Marketing type as an install from before the rename has it: the default names, and no In development step yet.
BEFORE = (("idea-open", "Idea", "open"), ("idea-doing", "Testing", "doing"), ("idea-scaling", "Scaling", "doing"),
          ("idea-paused", "Paused", "waiting"), ("idea-review", "Review", "review"), ("idea-ready", "Ready", "ready"),
          ("idea-done", "Done", "done"), ("idea-closed", "Closed", "closed"), ("idea-won", "Won", "closed"),
          ("idea-lost", "Lost", "closed"), ("idea-inconclusive", "Inconclusive", "closed"),
          ("idea-dropped", "Dropped", "closed"))
AFTER = {"idea-open": "On deck", "idea-doing": "Live", "idea-scaling": "Working", "idea-paused": "Paused",
         "idea-review": "Review", "idea-ready": "Ready", "idea-done": "Done", "idea-closed": "Closed",
         "idea-won": "Succeeded", "idea-lost": "Failed", "idea-inconclusive": "Inconclusive", "idea-dropped": "Dropped"}


def install(public_name=True):
    """An in-memory database holding the Marketing type's tables as an install from before the rename has them."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE task_types (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, "
              "created_at TEXT, updated_at TEXT)")
    c.execute("CREATE TABLE task_steps (id TEXT PRIMARY KEY, type_id TEXT NOT NULL REFERENCES task_types(id), "
              "name TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(type_id, name))")
    if public_name:
        c.execute("ALTER TABLE task_steps ADD COLUMN public_name TEXT NOT NULL DEFAULT ''")
    c.execute("INSERT INTO task_types (id, name) VALUES (?, ?)", (MT.TYPE_ID, "Marketing"))
    for position, (ident, name, status) in enumerate(BEFORE):
        c.execute("INSERT INTO task_steps (id, type_id, name, position, status) VALUES (?,?,?,?,?)",
                  (ident, MT.TYPE_ID, name, position, status))
    if public_name:
        c.execute("UPDATE task_steps SET public_name=name")       # as roadmaps.py sets it on a new install
    c.executescript(R.SCHEMA)
    for statement in A.SCHEMA:
        c.execute(statement)
    return c


def names(c):
    return dict(c.execute("SELECT id, name FROM task_steps WHERE type_id=?", (MT.TYPE_ID,)).fetchall())


def add_rule(c, ident, condition, scope_id=MT.TYPE_ID):
    c.execute("INSERT INTO rules (id, scope_kind, scope_id, event, condition, then_outcome, created_by, created_at, "
              "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (ident, "type", scope_id, "task_changed", json.dumps(condition), "notify", "person:ana", STAMP, STAMP))


def condition(c, ident):
    return json.loads(c.execute("SELECT condition FROM rules WHERE id=?", (ident,)).fetchone()[0])


def wake_flow(step_value, rule_step, scope_id=MT.TYPE_ID):
    """A What wakes flow: wake the bot when a task is at one step, and hand the rule it carries another step."""
    return {"wakes": {"scope_kind": "type", "scope_id": scope_id},
            "steps": [{"id": "s1", "rules": [{"when": [{"field": "task.step", "op": "is", "value": step_value}],
                                              "then": [{"do": "wake_bot",
                                                        "args": {"rule": {"when": {"step": rule_step}}}}]}]}]}


def add_automation(c, ident, flow, deleted_at=None):
    c.execute("INSERT INTO automations (id, name, owner_id, trigger_type, enabled, flow, version, created_at, "
              "created_by_id, updated_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (ident, ident, "person:ana", "manual", 1, json.dumps(flow), 1, STAMP, "person:ana", STAMP, deleted_at))


def flow(c, ident):
    return json.loads(c.execute("SELECT flow FROM automations WHERE id=?", (ident,)).fetchone()[0])


def test_a_default_name_is_renamed_and_a_team_name_is_kept():
    c = install()
    c.execute("UPDATE task_steps SET name='Backlog', public_name='Backlog' WHERE id='idea-open'")
    c.execute("UPDATE task_steps SET public_name='Did not work' WHERE id='idea-lost'")
    migration.up(c)
    assert names(c) == {**AFTER, "idea-open": "Backlog"}
    # The public name follows the name only where it was the same as it: a team's own public name stays.
    assert c.execute("SELECT public_name FROM task_steps WHERE id='idea-won'").fetchone()[0] == "Succeeded"
    assert c.execute("SELECT public_name FROM task_steps WHERE id='idea-lost'").fetchone()[0] == "Did not work"


def test_a_name_another_step_already_holds_is_not_taken():
    c = install()
    c.execute("INSERT INTO task_steps (id, type_id, name, position, status, public_name) VALUES "
              "('idea-team', ?, 'live', 20, 'doing', 'live')", (MT.TYPE_ID,))
    add_rule(c, "rule_testing", {"step": "Testing"})
    migration.up(c)
    # "live" holds the name Testing would become, so idea-doing keeps Testing and the rule that names it is untouched.
    assert names(c)["idea-doing"] == "Testing" and names(c)["idea-scaling"] == "Working"
    assert condition(c, "rule_testing") == {"step": "Testing"}


def test_a_rule_and_a_what_wakes_flow_follow_the_step_they_name():
    c = install()
    add_rule(c, "rule_one", {"step": "Testing"})
    add_rule(c, "rule_list", {"step": ["Idea", "Scaling", "Ready"]})
    add_rule(c, "rule_other_board", {"step": "Testing"}, scope_id="general")
    add_rule(c, "rule_no_step", {"owner": "ops"})
    add_automation(c, "auto_wakes_marketing", wake_flow(["Testing"], "Scaling"))
    add_automation(c, "auto_wakes_general", wake_flow(["Testing"], "Scaling", scope_id="general"))
    add_automation(c, "auto_wakes_deleted", wake_flow(["Testing"], "Scaling"), deleted_at=STAMP)
    add_automation(c, "auto_custom", wake_flow(["Testing"], "Scaling"))
    migration.up(c)
    assert condition(c, "rule_one") == {"step": "Live"}
    assert condition(c, "rule_list") == {"step": ["On deck", "Working", "Ready"]}
    assert condition(c, "rule_other_board") == {"step": "Testing"}
    assert condition(c, "rule_no_step") == {"owner": "ops"}
    assert flow(c, "auto_wakes_marketing") == wake_flow(["Live"], "Working")
    assert flow(c, "auto_wakes_general") == wake_flow(["Testing"], "Scaling", scope_id="general")
    assert flow(c, "auto_wakes_deleted") == wake_flow(["Testing"], "Scaling")
    assert flow(c, "auto_custom") == wake_flow(["Testing"], "Scaling")


def test_running_it_again_changes_nothing():
    c = install()
    add_rule(c, "rule_one", {"step": "Testing"})
    add_automation(c, "auto_wakes_marketing", wake_flow(["Testing"], "Scaling"))

    def snapshot():
        return [c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("task_steps", "rules", "automations")]

    migration.up(c)
    once = snapshot()
    migration.up(c)
    assert snapshot() == once


def test_a_new_file_has_nothing_to_rename_and_an_install_without_public_name_still_renames():
    fresh = sqlite3.connect(":memory:")
    migration.up(fresh)                       # no task_steps yet: ensure seeds the new names at the first start
    assert fresh.execute("SELECT name FROM sqlite_master").fetchall() == []
    old = install(public_name=False)
    migration.up(old)
    assert names(old) == dict(AFTER)


def test_the_in_development_step_is_added_once_in_board_order(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("DELETE FROM task_steps WHERE id=?", (MT.step_id("building"),))
        assert MT.ensure(c) == MT.TYPE_ID
        MT.ensure(c)
    with store.read() as c:
        rows = c.execute("SELECT id, name FROM task_steps WHERE type_id=? ORDER BY position", (MT.TYPE_ID,)).fetchall()
    ids = [MT.step_id(key) for key, _, _ in MT.STEPS]
    board = [(row[0], row[1]) for row in rows if row[0] in ids]
    assert [sid for sid, _ in board] == ids
    assert [name for sid, name in board if sid == "idea-building"] == ["In development"]
    assert [name for _, name in board][:4] == ["On deck", "In development", "Live", "Working"]


def test_only_the_owner_bot_closes_a_marketing_idea_on_an_outcome(api):
    tid = post(api, "tasks", {"title": "Flyer test", "body": "Try it.", "owner": "ops", "type": "idea",
                              "labels": ["channel:referrals"]})["id"]
    with api.app.state.store.read() as c:
        cur = c.execute("SELECT * FROM tasks WHERE id=?", (tid,))
        row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        owner = row["owner_id"]
        assert owner.startswith("bot:")
        outcome = {step: H._marketing_outcome(c, owner, row, step) for step in (
            "Succeeded", "Failed", "Inconclusive", "Dropped", "idea-won", "idea-lost",
            "Live", "Working", "In development", "On deck", "Paused")}
        assert {step for step, closed in outcome.items() if closed} == {
            "Succeeded", "Failed", "Inconclusive", "Dropped", "idea-won", "idea-lost"}
        # Another bot, a person, and the owner's own board under another type are not an outcome.
        assert H._marketing_outcome(c, "bot:someone-else", row, "Succeeded") is False
        assert H._marketing_outcome(c, "human:ana", row, "Succeeded") is False
        assert H._marketing_outcome(c, owner, row, "Succeeded", type=H.GENERAL_TYPE) is False
