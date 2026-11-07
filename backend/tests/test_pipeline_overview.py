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


def link(api, a, stage, b, mode="move"):
    ident = "pl-" + uuid.uuid4().hex[:8]
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_links(id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (ident, a, stage, b, mode, ago()),
        )
    return ident


def visit(api, task, pipeline, stage, entered, direction="forward"):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_by, entered_at) "
            "VALUES (?,?,?,?,?,NULL,?)",
            (H.new_id(), task, pipeline, stage, direction, entered),
        )


def handoff(api, link_id, target, created, returned=None, source=None):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_handoffs(id, pipeline_link_id, source_task_id, target_task_id, created_at, returned_at) "
            "VALUES (?,?,?,?,?,?)",
            ("ph-" + uuid.uuid4().hex[:8], link_id, source or target, target, created, returned),
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
        H._set_task_tags(c, "human:ana", old["id"], ["ads"])  # the tag a card is labelled by, through the tag writer
        c.execute("UPDATE tasks SET updated_at=?, status='doing' WHERE id=?", (ago(hours=3), old["id"]))
        c.execute(
            "UPDATE tasks SET status='done', done_at=?, created_at=? WHERE id=?",
            (ago(hours=1), ago(days=3), done["id"]),
        )
    move(api, done["id"], step=sales_steps["Done"])  # a real move into the output stage: one done card
    # The late clock is the open stage visit: the old card has been in Doing for three hours.
    visit(api, old["id"], marketing, marketing_steps["Doing"], ago(hours=3), "initial")
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
    assert week["edges"] == [{"from": marketing, "to": sales, "mode": "move", "count": 1, "median_age_minutes": 2880}]
    month = overview(api, "30d")["edges"][0]
    assert month["count"] == 2 and month["median_age_minutes"] == 1440  # 2 days and 0 (a handoff before the card)
    inputs = {i["label"]: i["count"] for i in week["inputs"] if i["to"] == marketing}
    assert inputs == {"Ads": 1, "Team ideas": 2}  # no source on a card: its first tag, else who made it
    assert week["loops"] == []
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


def test_a_spawn_is_not_an_input_but_the_card_that_made_it_still_is(api):
    growth = make_factory(api, "Gro")
    marketing, m_steps = make_type(api, "Mkt", factory=growth)
    sales, s_steps = make_type(api, "Sls", factory=growth)
    spawn = link(api, marketing, m_steps["Doing"], sales, mode="spawn")
    source = card(api, marketing, m_steps["Doing"])  # made by a person: an input
    made = card(api, sales, s_steps["Doing"])  # the card the spawn makes
    handoff(api, spawn, made["id"], ago(hours=2))
    inputs = {i["label"]: i["count"] for i in overview(api)["inputs"] if i["to"] == marketing}
    assert inputs == {"Team ideas": 1}  # the spawned card is excluded; its source is not
    assert overview(api)["edges"][0]["mode"] == "spawn"
    assert source["id"] != made["id"]


def test_inputs_cap_labels_at_five_then_other_and_count_every_card(api):
    growth = make_factory(api, "Gro")
    marketing, steps = make_type(api, "Mkt", factory=growth)
    for n in range(7):
        made = card(api, marketing, steps["Doing"])
        with api.app.state.store.transaction() as c:
            H._set_task_tags(c, "human:ana", made["id"], [f"tag-{n}"])
    rows = [i for i in overview(api)["inputs"] if i["to"] == marketing]
    labels = {i["label"]: i["count"] for i in rows}
    assert len(labels) == 6 and labels["Other"] == 2 and sum(labels.values()) == 7


def test_a_reader_is_not_counted_a_review_on_a_card_they_may_not_open(api):
    growth = make_factory(api, "Gro")
    steps = [
        {"name": "Doing", "status": "doing"},
        {"name": "Review", "status": "review"},
        {"name": "Done", "status": "done"},
    ]
    typ, s = make_type(api, "Rvw", steps=steps, factory=growth)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, reviewed_by, reviewer_id, "
            "created_at, updated_at) VALUES ('rq1',?,?,'Sign off','approval','person','human:ben',?,?)",
            (typ, s["Review"], ago(), ago()),
        )
    card(api, typ, s["Review"])
    card(api, typ, s["Review"], private=True)  # Ana's: Ben is the reviewer but may not open it
    assert {p["name"]: p["for_you"] for p in overview(api, token="ben-test")["pipelines"]}["Rvw"] == 1


def test_a_loop_counts_its_laps_and_shows_a_median_from_three(api):
    growth = make_factory(api, "Gro")
    prod, p_steps = make_type(api, "Prod", factory=growth)
    eng, e_steps = make_type(api, "Eng", factory=growth)
    forward = link(api, prod, p_steps["Doing"], eng)
    back = link(api, eng, e_steps["Doing"], prod)
    rider = card(api, prod, p_steps["Doing"])

    def lap(minutes_ago, span):
        handoff(api, forward, rider["id"], ago(minutes=minutes_ago))
        handoff(api, back, rider["id"], ago(minutes=minutes_ago - span))

    lap(600, 60)
    lap(500, 60)
    (two,) = overview(api)["loops"]
    assert two["pipelines"] == [prod, eng] and two["laps"] == 2 and two["median_lap_minutes"] is None
    lap(400, 60)
    (three,) = overview(api)["loops"]
    assert three["laps"] == 3 and three["median_lap_minutes"] == 60


def test_the_map_counts_each_window_the_same_as_direct_sql(api):
    growth = make_factory(api, "Gro")
    marketing, m_steps = make_type(api, "Mkt", factory=growth)
    sales, _ = make_type(api, "Sls", factory=growth)
    lead = link(api, marketing, m_steps["Doing"], sales)
    cards = [card(api, marketing, m_steps["Doing"]) for _ in range(3)]
    for c_, hours in zip(cards, (12, 3 * 24, 10 * 24), strict=True):
        handoff(api, lead, c_["id"], ago(hours=hours))
    for period, days in (("1d", 1), ("7d", 7), ("30d", 30)):
        with api.app.state.store.transaction() as c:
            direct = c.execute(
                "SELECT COUNT(*) FROM pipeline_handoffs WHERE created_at>=? AND returned_at IS NULL", (ago(days=days),)
            ).fetchone()[0]
        (edge,) = overview(api, period)["edges"]
        assert edge["count"] == direct, period


def test_a_factory_with_no_links_renders_cleanly(api):
    # Leads and customers live in the CRM, so a factory may have pipelines with no links between them at all.
    growth = make_factory(api, "Gro")
    marketing, steps = make_type(api, "Mkt", factory=growth)
    sales, _ = make_type(api, "Sls", factory=growth)
    card(api, marketing, steps["Doing"])

    week = overview(api)
    assert week["edges"] == []
    assert week["loops"] == []
    assert {p["id"] for p in week["pipelines"]} >= {marketing, sales}
    assert [f["name"] for f in week["factories"]] == ["Gro"]
    assert week["factories"][0]["done"] == 0


def move(api, task, token="ana-test", **fields):
    """A real ticket update (POST /api/v2/tasks/{id}) with the version the ticket has now."""
    version = get(api, "tasks/" + task, token)["task"]["version"]
    return post(api, "tasks/" + task, {"version": version, **fields}, token)


def test_a_ticket_moved_into_a_members_only_pipeline_stops_counting_for_a_non_member(api):
    growth = make_factory(api, "Gro")
    marketing, m_steps = make_type(api, "Mkt", factory=growth)
    sales, _ = make_type(api, "Sls", factory=growth)
    legal, _ = make_type(api, "Lgl", pipeline_visibility="members", pipeline_owner_id="human:ana")
    lead = link(api, marketing, m_steps["Doing"], sales)
    made = card(api, marketing, m_steps["Doing"])  # company-wide: Ben may read it
    handoff(api, lead, made["id"], ago(hours=1))

    def counted(token):
        week = overview(api, token=token)
        inputs = sum(i["count"] for i in week["inputs"] if i["to"] == marketing)
        edges = sum(e["count"] for e in week["edges"] if e["from"] == marketing and e["to"] == sales)
        return inputs, edges

    assert counted("ben-test") == (1, 1)
    move(api, made["id"], type=legal)  # now members-only: Ben may not open it
    assert counted("ben-test") == (0, 0)
    assert counted("ana-test") == (
        0,
        1,
    )  # Ana may open it: its handoff counts; its card now starts in Legal, not Marketing


def test_a_spawn_counts_only_when_the_reader_may_open_both_its_tickets(api):
    growth = make_factory(api, "Gro")
    marketing, m_steps = make_type(api, "Mkt", factory=growth)
    sales, s_steps = make_type(api, "Sls", factory=growth)
    spawn = link(api, marketing, m_steps["Doing"], sales, mode="spawn")
    parent = card(api, marketing, m_steps["Doing"], private=True)  # Ana's: Ben may not open the parent
    child = card(api, sales, s_steps["Doing"])  # Ben may read the subtask
    handoff(api, spawn, child["id"], ago(hours=1), source=parent["id"])

    assert [e["count"] for e in overview(api)["edges"]] == [1]
    assert overview(api, token="ben-test")["edges"] == []


def test_factory_done_counts_real_moves_into_the_output_stage_without_requirements(api):
    # A pipeline with no stage requirements records no stage visits; its done count still comes from the moves.
    growth = make_factory(api, "Gro")
    sales, steps = make_type(api, "Sls", factory=growth)
    first = card(api, sales, steps["Doing"])
    second = card(api, sales, steps["Doing"])
    card(api, sales, steps["Doing"])  # still open
    move(api, first["id"], step=steps["Done"])
    move(api, second["id"], step=steps["Done"])
    move(api, second["id"], step=steps["Doing"])  # back out
    move(api, second["id"], step=steps["Done"])  # and in again: one entry per card per period
    assert overview(api)["factories"][0]["done"] == 2
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM stage_visits").fetchone()[0] == 0


def test_the_migration_picks_the_output_stage_by_flow_order_not_creation_order(api):
    # Engineering was made first, but Product feeds it: the factory's output is Engineering's done stage.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "migrations" / "20261107210000_factory_output_stage.py"
    spec = importlib.util.spec_from_file_location("migration_factory_output_stage", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    growth = make_factory(api, "Gro")
    eng, e_steps = make_type(api, "Eng", factory=growth)
    prod, p_steps = make_type(api, "Prod", factory=growth)
    link(api, prod, p_steps["Doing"], eng)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE factories SET output_stage_id=NULL WHERE id=?", (growth,))
        migration.up(c)
        stored = c.execute("SELECT output_stage_id FROM factories WHERE id=?", (growth,)).fetchone()[0]
    assert stored == e_steps["Done"]
