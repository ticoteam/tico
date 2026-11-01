"""A Dev tickets type ships editable hand-off rules: step entries route news to the bot that must act, and only it.
Its rows move into the type's "What wakes" automation in the write that seeds them (redesign_migration.adopt), and
the wake engine reads them from there (wake_rows); a person edits them as that automation's steps."""
import json

from backend import profiles as P, redesign_migration, release_flow, rules as R
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_profiles import add_bot, put


def dev_type(c):
    return H.type_create(c, H.KEEPER, "Dev tickets", [
        {"name": "In Progress", "status": "doing"}, {"name": "In QA", "status": "review"},
        {"name": "QA Fixes", "status": "doing"}, {"name": "Pending Release", "status": "review"},
        {"name": "Done", "status": "done"}], bots="work")


def rows_of(c, type_id):
    """A type's step-entry rows as the wake engine reads them: kept in the table, or in its What wakes automation."""
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM rules WHERE scope_kind='type' AND scope_id=? AND event='step_entered' AND enabled=1", (type_id,))]
    rows += [r for r in redesign_migration.wake_rows(c, "step_entered")
             if (r["scope_kind"], r["scope_id"], r["event"]) == ("type", type_id, "step_entered")]
    return sorted(rows, key=lambda r: r["position"])


def seeded(c, type_id):
    return [(r["note"], json.loads(r["condition"]), r["then_outcome"]) for r in rows_of(c, type_id)]


def wakes(c, type_id):
    """The type's one What wakes automation: (id, flow)."""
    (row, flow), = redesign_migration._wakes_of(c, "type", type_id)
    return row["id"], flow


def names_step(step, name):
    return any(cond.get("value") == name for rule in step["rules"] for cond in rule["when"])


def edit_steps(api, aid, steps):
    """A person saves the What wakes automation with these steps."""
    r = api.patch(f"/api/v2/automations/{aid}", json={"steps": steps}, headers=headers())
    assert r.status_code == 200, r.text


def test_a_dev_tickets_type_is_seeded_once_and_a_deleted_row_stays_deleted(api):
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        rows = seeded(c, typ["id"])
        assert [(w, o) for _, w, o in rows] == [(w, o) for _, w, o in R.DEV_ROWS]
        # Moved in the same write: nothing is left in the table, everything is in the type's What wakes automation.
        assert not c.execute("SELECT 1 FROM rules WHERE scope_id=?", (typ["id"],)).fetchone()
        aid, flow = wakes(c, typ["id"])
        assert len(flow["steps"]) == 3
    # A person deletes the In QA row: its step in the automation.
    edit_steps(api, aid, [s for s in flow["steps"] if not names_step(s, "In QA")])
    with api.app.state.store.transaction() as c:
        R.seed_dev(c)
        H.type_update(c, H.KEEPER, typ["id"], name="Dev tickets")
        assert len(seeded(c, typ["id"])) == 2, "a row a person removed is not put back"
        assert wakes(c, typ["id"])[0] == aid
        other = H.type_create(c, H.KEEPER, "Tasks for the garden", [{"name": "Todo", "status": "open"}])
        assert seeded(c, other["id"]) == []
        # A server from before the rows: the type exists with nothing recorded, and startup gives it the rows.
        c.execute("UPDATE automations SET deleted_at=?, enabled=0 WHERE id=?", (H.now(), aid))
        c.execute("DELETE FROM events WHERE action=? AND target=?", (R.SEEDED, typ["id"]))
        R.seed_dev(c)
        assert len(seeded(c, typ["id"])) == 3


def test_renaming_a_step_moves_its_rows_in_the_what_wakes_automation_to_the_new_name(api):
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        steps = [{"id": s["id"], "name": "Acceptance" if s["name"] == "In QA" else s["name"], "status": s["status"]}
                 for s in H.type_get(c, typ["id"])["steps"]]
        H.type_update(c, H.KEEPER, typ["id"], steps=steps)
        named = [w.get("step") for _, w, _ in seeded(c, typ["id"])]
        assert "Acceptance" in named and "In QA" not in named
        assert any(names_step(s, "Acceptance") for s in wakes(c, typ["id"])[1]["steps"])


def test_each_step_entry_reaches_only_the_bot_that_must_act(api):
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        ticket = H.task_create(c, "bot:coo", "Improve messages", "Build.", "bot:cpo", type=typ["id"],
                               step="In Progress", lint=False)
        H.task_create(c, "bot:cpo", "QA: messages", "Test.", "bot:finance", parent_id=ticket["id"], lint=False)
        release_flow.save(c, H.KEEPER, {"bot_id": "ops"})

        def moved(mover, step):
            c.execute("DELETE FROM jobs")
            c.execute("DELETE FROM messages WHERE json_extract(refs,'$.news')='step_entered'")
            H.task_update(c, mover, ticket["id"], step=step)
            return ({r["bot_id"] for r in c.execute("SELECT bot_id FROM jobs WHERE state='queued'")},
                    {r["recipient_id"] for r in c.execute(
                        "SELECT recipient_id FROM messages WHERE json_extract(refs,'$.news')='step_entered'")})

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
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.news')='step_entered'").fetchone()


def step_news(c):
    return [(r["recipient_id"], json.loads(r["refs"]).get("rule")) for r in c.execute(
        "SELECT recipient_id,refs FROM messages WHERE json_extract(refs,'$.news')='step_entered' ORDER BY rowid")]


def leads(c):
    """A Release Manager and a requester that are Leads: their profile's catch-all holds any other news for a digest."""
    for slug in ("release-manager", "boss"):
        add_bot(c, slug)
        put(c, slug, P.LEAD)
    release_flow.save(c, H.KEEPER, {"bot": "release-manager"})


def lead_moved(c, mover, task, step):
    c.execute("DELETE FROM jobs")
    c.execute("DELETE FROM messages WHERE json_extract(refs,'$.news')='step_entered'")
    H.task_update(c, mover, task["id"], step=step)
    return ({r["bot_id"] for r in c.execute(
        "SELECT j.bot_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE json_extract(m.refs,'$.news')='step_entered'")}, step_news(c))


def test_a_step_move_reaches_a_lead_only_through_a_row_that_names_it(api):
    with api.app.state.store.transaction() as c:
        leads(c)
        typ = dev_type(c)
        idea = H.task_create(c, "bot:boss", "Recruit an advisor", "x", "bot:cpo", type="Product roadmap",
                             step="Suggested", lint=False)
        ticket = H.task_create(c, "bot:boss", "Improve messages", "Build.", "bot:cpo", type=typ["id"],
                               step="In Progress", lint=False)
        # No row names the Release Manager or the Lead who asked for it: neither hears of it, not even in a digest.
        assert lead_moved(c, "bot:cpo", idea, "Exploring") == (set(), [])
        assert lead_moved(c, H.KEEPER, idea, "Suggested") == (set(), [])
        # The Release Manager's row: one note, no job.
        pending = next(r["id"] for r in rows_of(c, typ["id"]) if json.loads(r["condition"])["step"] == "Pending Release")
        assert lead_moved(c, "bot:cpo", ticket, "Pending Release") == (set(), [("bot:release-manager", pending)])
        aid, flow = wakes(c, typ["id"])
    # A Lead requester a row names hears it as the row says: here a wake.
    fixes = next(s for s in flow["steps"] if names_step(s, "QA Fixes"))
    for cond in fixes["rules"][0]["when"]:
        if cond["field"] == "task.role":
            cond["value"] = "requester"
    edit_steps(api, aid, flow["steps"])
    with api.app.state.store.transaction() as c:
        jobs, said = lead_moved(c, "bot:cpo", ticket, "QA Fixes")
        assert jobs == {"boss"} and [to for to, _ in said] == ["bot:boss"]
    # A row that names the step with `ignore` (archive, in the automation) writes nothing at all.
    fixes["rules"][0]["then"][0]["do"] = "archive"
    edit_steps(api, aid, flow["steps"])
    with api.app.state.store.transaction() as c:
        assert lead_moved(c, "bot:cpo", ticket, "QA Fixes") == (set(), [])
        # A step no row names: nothing for anyone.
        assert lead_moved(c, "bot:cpo", ticket, "In Progress") == (set(), [])


def test_the_release_managers_held_step_notices_are_dropped_once_and_nothing_else(api):
    with api.app.state.store.transaction() as c:
        leads(c)
        task = H.task_create(c, "bot:boss", "Recruit an advisor", "x", "bot:cpo", lint=False)
        other = H.task_create(c, "bot:boss", "Book the offsite", "x", "bot:cpo", lint=False)
        c.execute("DELETE FROM jobs")
        # What the catch-all held before: step entries batched for the digest, and other held news.
        batched = {"news": "step_entered", "wake_batch": "rule"}
        own_row = {"news": "step_entered", "wake_batch": "rule"}
        for bot, refs in (("release-manager", batched), ("boss", batched),
                          ("release-manager", {"news": "task_done", "wake_batch": "completion"})):
            H._wake(c, task, "bot:" + bot, f"{task['title']} entered Exploring.", refs)
        H._wake(c, other, "bot:release-manager", f"{other['title']} entered Exploring.", own_row)
        # As a team's own step_entered row with outcome batch stamps it (rules.stamp).
        c.execute("UPDATE messages SET refs=json_set(refs,'$.rule','r-team-step-batch') WHERE body LIKE ?",
                  (other["title"] + "%",))
        held = {r["id"]: (r["bot_id"], r["state"]) for r in c.execute("SELECT id,bot_id,state FROM jobs WHERE held=1")}
        assert sorted(b for b, _ in held.values()) == ["boss", "release-manager", "release-manager", "release-manager"]
        c.execute("DELETE FROM events WHERE action=?", (R.STEP_NOISE_DROPPED,))   # this database's startup ran it
        assert R.drop_step_noise(c) == 1
        after = {r["id"]: (r["bot_id"], r["state"], json.loads(r["refs"])["news"]) for r in c.execute(
            "SELECT j.id,j.bot_id,j.state,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id")}
        assert sorted(after.values()) == [("boss", "queued", "step_entered"),
                                          ("release-manager", "canceled", "step_entered"),
                                          ("release-manager", "queued", "step_entered"),
                                          ("release-manager", "queued", "task_done")]
        # Once per install: a later start leaves a step notice a person's row batched since.
        H._wake(c, task, "bot:release-manager", f"{task['title']} entered Doing.", batched)
        assert R.drop_step_noise(c) == 0
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='release-manager' AND state='queued'").fetchone()[0] == 3
