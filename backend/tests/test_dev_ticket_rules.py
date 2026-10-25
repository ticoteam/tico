"""A Dev tickets type ships editable hand-off rules: step entries route news to the bot that must act, and only it."""
import json

from backend import release_flow, rules as R
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def dev_type(c):
    return H.type_create(c, H.KEEPER, "Dev tickets", [
        {"name": "In Progress", "status": "doing"}, {"name": "In QA", "status": "review"},
        {"name": "QA Fixes", "status": "doing"}, {"name": "Pending Release", "status": "review"},
        {"name": "Done", "status": "done"}], bots="work")


def seeded(c, type_id):
    return [(r["note"], json.loads(r["when_json"]), r["then_outcome"]) for r in c.execute(
        "SELECT * FROM rules WHERE scope_kind='type' AND scope_id=? AND event='step_entered' ORDER BY position", (type_id,))]


def test_a_dev_tickets_type_is_seeded_once_and_a_deleted_row_stays_deleted(api):
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        rows = seeded(c, typ["id"])
        assert [(w, o) for _, w, o in rows] == [(w, o) for _, w, o in R.DEV_ROWS]
        c.execute("DELETE FROM rules WHERE scope_id=? AND json_extract(when_json,'$.step')='In QA'", (typ["id"],))
        R.seed_dev(c)
        H.type_update(c, H.KEEPER, typ["id"], name="Dev tickets")
        assert len(seeded(c, typ["id"])) == 2, "a row a person removed is not put back"
        other = H.type_create(c, H.KEEPER, "Tasks for the garden", [{"name": "Todo", "status": "open"}])
        assert seeded(c, other["id"]) == []
        # A server from before the rows: the type exists with nothing recorded, and startup gives it the rows.
        c.execute("DELETE FROM rules WHERE scope_id=?", (typ["id"],))
        c.execute("DELETE FROM events WHERE action=? AND target=?", (R.SEEDED, typ["id"]))
        R.seed_dev(c)
        assert len(seeded(c, typ["id"])) == 3


def test_each_step_entry_reaches_only_the_bot_that_must_act(api):
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        ticket = H.task_create(c, "bot:coo", "Improve messages", "Build.", "bot:cpo", type=typ["id"],
                               step="In Progress", lint=False)
        H.task_create(c, "bot:cpo", "QA: messages", "Test.", "bot:finance", parent_id=ticket["id"], lint=False)
        release_flow.save(c, H.KEEPER, {"bot": "ops"})

        def moved(mover, step):
            c.execute("DELETE FROM jobs")
            c.execute("DELETE FROM messages WHERE json_extract(refs_json,'$.news')='step_entered'")
            H.task_update(c, mover, ticket["id"], step=step)
            return ({r["bot"] for r in c.execute("SELECT bot FROM jobs WHERE state='queued'")},
                    {r["to_actor"] for r in c.execute(
                        "SELECT to_actor FROM messages WHERE json_extract(refs_json,'$.news')='step_entered'")})

        # QA alone is woken for In QA: neither the Builder who moved it nor the requester hears of it.
        assert moved("bot:cpo", "In QA") == ({"finance"}, {"bot:finance"})
        # The Builder is woken when QA sends it back.
        assert moved("bot:finance", "QA Fixes") == ({"cpo"}, {"bot:cpo"})
        # The Release Manager gets a note, no wake.
        assert moved("bot:finance", "Pending Release") == (set(), {"bot:ops"})
        # Steps no rule names write nothing.
        assert moved("bot:cpo", "In Progress") == (set(), set())


def test_a_type_without_step_rules_writes_no_message_on_a_step_move(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Garden", [{"name": "Todo", "status": "open"}, {"name": "Doing", "status": "doing"}])
        task = H.task_create(c, "bot:coo", "Water", "Water it.", "bot:cpo", type=typ["id"], step="Todo", lint=False)
        H.task_update(c, "bot:cpo", task["id"], step="Doing")
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs_json,'$.news')='step_entered'").fetchone()
