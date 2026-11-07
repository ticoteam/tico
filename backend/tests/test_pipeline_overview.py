"""The Overview flow map read (backend/pipeline_overview.py): its numbers equal direct counts over the same rows, an
edge carries the count and the median time cards spent in the source pipeline, a members-only pipeline and a private
card are hidden from a reader who may not open them, and the payload is capped."""

import uuid
from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend.tests.test_api import api, get, post  # noqa: F401  (api is a fixture)


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def make_type(api, name, steps=None, factory=None, **columns):
    with api.app.state.store.transaction() as c:
        for col, decl in (("goal", "TEXT"), ("pipeline_visibility", "TEXT NOT NULL DEFAULT 'company'")):
            H.add_column(c, "task_types", col, decl)
        typ = H.type_create(
            c, H.KEEPER, name, steps or [{"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]
        )
        for col, value in {"factory_id": factory, **columns}.items():
            c.execute(f"UPDATE task_types SET {col}=? WHERE id=?", (value, typ["id"]))
    return typ["id"], {s["name"]: s["id"] for s in typ["steps"]}


def make_factory(api, name, position=0):
    ident = "fac-" + uuid.uuid4().hex[:8]
    with api.app.state.store.transaction() as c:
        H.add_column(c, "factories", "goal", "TEXT")
        c.execute(
            "INSERT INTO factories(id, name, position, created_at, updated_at) VALUES (?,?,?,?,?)",
            (ident, name, position, ago(), ago()),
        )
    return ident


def card(api, typ, step, private=False, token="ana-test"):
    ask = uuid.uuid4().hex[:8]
    body = {
        "owner": "ops",
        "title": f"Card {ask}",
        "body": f"Card {ask}.",
        "type": typ,
        "step": step,
        "private": private,
    }
    return post(api, "tasks", body, token)


def link(api, a, stage, b):
    ident = "pl-" + uuid.uuid4().hex[:8]
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_links(id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, created_at) "
            "VALUES (?,?,?,?,'move',?)",
            (ident, a, stage, b, ago()),
        )
    return ident


def handoff(api, link_id, target, created, returned=None):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_handoffs(id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at) "
            "VALUES (?,?,?,?,?,?)",
            ("ph-" + uuid.uuid4().hex[:8], link_id, target, target, created, returned),
        )


def overview(api, period="7d", token="ana-test", expected=200):
    return get(api, "pipelines/overview?period=" + period, token, expected)


def test_the_numbers_equal_direct_counts_and_follow_link_order(api):
    growth = make_factory(api, "Gro")
    sales, sales_steps = make_type(api, "Sls", factory=growth)  # made first, yet it comes second: Marketing feeds it
    marketing, marketing_steps = make_type(api, "Mkt", factory=growth)
    lone, lone_steps = make_type(api, "Help desk")
    lead = link(api, marketing, marketing_steps["Doing"], sales)
    # Three cards, one of them tagged Ads and one old enough to be past its stage's Warn after.
    old = card(api, marketing, marketing_steps["Doing"])
    card(api, marketing, marketing_steps["Doing"])
    card(api, marketing, marketing_steps["Doing"])
    done = card(api, sales, sales_steps["Doing"])
    card(api, lone, lone_steps["Doing"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (marketing_steps["Doing"],))
        c.execute("UPDATE tasks SET updated_at=?, labels='[\"ads\"]' WHERE id=?", (ago(hours=3), old["id"]))
        c.execute(
            "UPDATE tasks SET status='done', done_at=?, step_id=?, created_at=? WHERE id=?",
            (ago(hours=1), sales_steps["Done"], ago(days=3), done["id"]),
        )
    handoff(api, lead, done["id"], ago(days=1))
    handoff(api, lead, done["id"], ago(days=20))  # outside 7 days

    week = overview(api)
    by = {p["id"]: p for p in week["pipelines"]}
    assert [p["id"] for p in week["pipelines"][:2]] == [marketing, sales]  # factory pipelines first, in link order
    assert by[lone] in week["pipelines"][2:]
    assert by[marketing]["cards"] == 3 and by[marketing]["overdue"] == 1
    assert by[sales]["cards"] == 0  # its one card is done
    assert by[lone]["factory_id"] is None
    assert [f["name"] for f in week["factories"]] == ["Gro"]
    assert week["factories"][0]["done"] == 1 and week["factories"][0]["overdue"] == 1
    assert week["edges"] == [{"from": marketing, "to": sales, "count": 1, "median_age_minutes": 2880}]
    month = overview(api, "30d")["edges"][0]
    assert month["count"] == 2 and month["median_age_minutes"] == 1440  # 2 days and 0 (a handoff before the card)
    inputs = {i["label"]: i["count"] for i in week["inputs"] if i["to"] == marketing}
    assert inputs == {"Ads": 1, "Team ideas": 2}  # no source on a card: its first tag, else who made it
    assert overview(api, "bad", expected=422)


def test_a_members_only_pipeline_and_a_private_card_are_hidden_from_a_reader_who_may_not_open_them(api):
    growth = make_factory(api, "Gro")
    marketing, steps = make_type(api, "Mkt", factory=growth)
    legal, legal_steps = make_type(api, "Lgl", pipeline_visibility="members", pipeline_owner_id="human:ana")
    card(api, marketing, steps["Doing"])
    card(api, marketing, steps["Doing"], private=True)  # Ana's: Ben may not open it
    card(api, legal, legal_steps["Doing"])

    mine = {p["name"]: p for p in overview(api)["pipelines"]}
    theirs = {p["name"]: p for p in overview(api, token="ben-test")["pipelines"]}
    assert {"Mkt", "Lgl"} <= set(mine) and mine["Mkt"]["cards"] == 2
    assert "Mkt" in theirs and "Lgl" not in theirs and theirs["Mkt"]["cards"] == 1

    with api.app.state.store.transaction() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_members (pipeline_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, PRIMARY KEY (pipeline_id, actor_id))"
        )
        c.execute(
            "INSERT INTO pipeline_members(pipeline_id, actor_id, created_at) VALUES (?,?,?)",
            (legal, "human:ben", ago()),
        )
    assert "Lgl" in {p["name"] for p in overview(api, token="ben-test")["pipelines"]}


def test_the_payload_is_capped_at_fifty_pipelines(api):
    for n in range(55):
        make_type(api, f"Pl {n:02d}")
    assert len(overview(api)["pipelines"]) == 50


def test_rights_say_who_may_edit_a_pipeline(api):
    pid, _ = make_type(api, "Rgt", pipeline_owner_id="human:ana")
    assert get(api, f"pipelines/{pid}/rights", "ana-test") == {"may_edit_rules": True}
    assert isinstance(get(api, f"pipelines/{pid}/rights", "ben-test")["may_edit_rules"], bool)
    assert get(api, "pipelines/nope/rights", "ana-test") == {"may_edit_rules": False}
