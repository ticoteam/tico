"""The Engineering stage map (spec 795, items 1 and 6): setup moves each card on the thirteen Dev steps to the stage the
map names and keeps its status, re-points the routing that named those steps, names a step it does not know and then
moves nothing, and a second run changes nothing. Runs on a disposable hub with one card on each old step."""

from backend import hubdb as H
from backend import pipeline_setup as S
from backend import roadmap_flow
from backend import step_flow as F
from backend.tests.test_pipeline_foundation import ANA, boot, hub, rows, step_id

OLD = [
    ("On Deck", "open"),
    ("In Progress", "doing"),
    ("Blocked", "waiting"),
    ("Review 1", "review"),
    ("Review 1 Fixes", "doing"),
    ("Review 2", "review"),
    ("Review 2 Fixes", "doing"),
    ("In QA", "review"),
    ("QA Fixes", "doing"),
    ("Pending Release", "review"),
    ("Post Release QA", "review"),
    ("Done", "done"),
    ("Closed", "closed"),
]
EXPECT = {
    "On Deck": "Build",
    "In Progress": "Build",
    "Blocked": "Build",
    "Review 1": "Review 1",
    "Review 1 Fixes": "Build",
    "Review 2": "Review 2",
    "Review 2 Fixes": "Build",
    "In QA": "Review 2",
    "QA Fixes": "Build",
    "Pending Release": "Release",
    "Post Release QA": "Watch 24h",
    "Done": "Closed",
    "Closed": "Closed",
}
STAGES = ["Build", "Review 1", "Review 2", "Merge queue", "Release", "Watch 24h", "Closed"]


def old_board(tmp_path):
    """A hub whose Engineering type has the thirteen steps the Dev board had, one open card on each."""
    boot(tmp_path / "hub.sqlite")
    c = hub(tmp_path / "hub.sqlite")
    eng = roadmap_flow.role_type(c, roadmap_flow.ENGINEERING)
    H.type_update(c, ANA, eng, steps=[{"name": n, "status": s} for n, s in OLD], mover=True)
    cards = {}
    for name, _status in OLD:
        cards[name] = H.task_create(c, ANA, f"Ticket on {name}", "", ANA, type=eng, step=name, lint=False)["id"]
    c.commit()
    return c, eng, cards


def stage_names(c, eng):
    return [r["name"] for r in rows(c, "SELECT name FROM task_steps WHERE type_id=? ORDER BY position", eng)]


def where(c, task):
    return rows(
        c,
        "SELECT s.name, k.status FROM tasks k JOIN task_steps s ON s.id = k.step_id WHERE k.id = ?",
        task,
    )[0]


def test_each_card_moves_to_the_stage_the_map_names_and_keeps_its_status(tmp_path):
    c, eng, cards = old_board(tmp_path)
    before = {name: where(c, task)["status"] for name, task in cards.items()}
    out = S.apply_factory_setup(c, ANA)
    assert out["unresolved"] == [line for line in out["unresolved"] if "stage map" not in line]
    for name, task in cards.items():
        got = where(c, task)
        assert got == {"name": EXPECT[name], "status": before[name]}, name


def test_the_thirteen_steps_become_the_seven_stages_in_order(tmp_path):
    c, eng, _cards = old_board(tmp_path)
    S.apply_factory_setup(c, ANA)
    assert stage_names(c, eng) == STAGES


def test_merge_queue_starts_empty_and_a_card_with_no_step_lands_there(tmp_path):
    c, eng, _cards = old_board(tmp_path)
    S.apply_factory_setup(c, ANA)
    assert rows(
        c, "SELECT COUNT(*) AS n FROM tasks k JOIN task_steps s ON s.id=k.step_id WHERE s.name='Merge queue'"
    ) == [{"n": 0}]


def test_a_second_run_changes_nothing_and_says_so(tmp_path):
    c, eng, _cards = old_board(tmp_path)
    S.apply_factory_setup(c, ANA)
    snapshot = (
        rows(c, "SELECT id, name, status, flow, position FROM task_steps WHERE type_id=? ORDER BY id", eng),
        rows(c, "SELECT id, step_id, status FROM tasks WHERE type_id=? ORDER BY id", eng),
    )
    events = rows(c, "SELECT COUNT(*) AS n FROM history_events")[0]["n"]
    again = S.apply_factory_setup(c, ANA)
    assert (
        rows(c, "SELECT id, name, status, flow, position FROM task_steps WHERE type_id=? ORDER BY id", eng),
        rows(c, "SELECT id, step_id, status FROM tasks WHERE type_id=? ORDER BY id", eng),
    ) == snapshot
    assert rows(c, "SELECT COUNT(*) AS n FROM history_events")[0]["n"] == events
    assert "Engineering stages already match the stage map: nothing changed" in again["notes"]


def test_a_step_the_map_does_not_name_blocks_the_whole_map_and_moves_nothing(tmp_path):
    c, eng, cards = old_board(tmp_path)
    c.execute(
        "INSERT INTO task_steps (id, type_id, name, position, status) VALUES ('odd', ?, 'Hotfix', 13, 'doing')", (eng,)
    )
    c.commit()
    before = {name: where(c, task)["name"] for name, task in cards.items()}
    out = S.apply_factory_setup(c, ANA)
    assert any("Hotfix is not in the stage map" in line for line in out["unresolved"])
    assert {name: where(c, task)["name"] for name, task in cards.items()} == before
    assert "Build" not in stage_names(c, eng)


def test_routing_that_named_a_merged_step_names_the_stage_it_became(tmp_path):
    c, eng, _cards = old_board(tmp_path)
    by = {r["name"]: r for r in rows(c, "SELECT id, name, flow FROM task_steps WHERE type_id=?", eng)}
    for name, step in (("Review 1", "Review 1 Fixes"), ("Review 2", "Review 2 Fixes")):
        c.execute(
            "UPDATE task_steps SET flow=? WHERE id=?",
            ('{"on_fail": "%s", "on_pass": "%s"}' % (by[step]["id"], by[step]["id"]), by[name]["id"]),
        )
    c.commit()
    S.apply_factory_setup(c, ANA)
    now = {r["name"]: F.of(r) for r in rows(c, "SELECT name, flow FROM task_steps WHERE type_id=?", eng)}
    build = next(
        r["id"] for r in rows(c, "SELECT id, name FROM task_steps WHERE type_id=?", eng) if r["name"] == "Build"
    )
    assert now["Review 1"]["on_fail"] == build and now["Review 1"]["on_pass"] == build
    assert now["Review 2"]["on_fail"] == build
    assert now["Review 2"]["on_pass"] == F.MERGE
    assert sorted(F.skip_tags(now["Review 2"])) == ["review-1-only", "skip-qa"]
    assert now["Release"]["on_merge"] is True and now["Release"]["can_hold"] is False
    assert now["Watch 24h"]["check"] == "release_qa"


def test_setup_renames_a_role_held_pipelines_stages_and_keeps_its_cards(tmp_path):
    """Setup leaves a stage list alone on a pipeline that holds a role, except for the Engineering stage map: that rename
    applies to the role holder, and its cards keep their place."""
    c, eng, cards = old_board(tmp_path)
    assert rows(c, "SELECT role FROM task_types WHERE id=?", eng)[0]["role"] == roadmap_flow.ENGINEERING
    S.apply_factory_setup(c, ANA, replace=True)
    assert stage_names(c, eng) == STAGES
    assert where(c, cards["Pending Release"])["name"] == "Release"
    assert where(c, cards["Closed"])["name"] == "Closed"
