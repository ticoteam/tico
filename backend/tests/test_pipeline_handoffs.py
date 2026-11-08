"""A hand-off is written as activity on both cards and read back as a chip and a From (backend/pipeline_links.py): a move
names the gate that passed it or the person who moved it, a spawn names both cards, a returned hand-off counts for nothing,
and a card the reader may not open shows no stage and no link."""

from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_links as L
from backend.tests.test_pipeline_engine import decide, requirement
from backend.tests.test_pipeline_foundation import ANA, rows, step_id
from backend.tests.test_pipeline_links import two


def test_a_move_names_the_gate_that_passed_the_card(tmp_path):
    c, sales, success, _ = two(tmp_path)
    L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"], "move")
    signed = requirement(c, sales, "Won", "Signed off")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    decide(c, task, signed)
    assert E.advance(c, ANA, task)["moved"]
    [sent] = rows(c, "SELECT note, new, data FROM history_events WHERE subject_id=? AND kind='handoff'", task)
    assert sent["note"].startswith("approved by ") and sent["note"].endswith("(met) at Won")
    assert sent["new"] == success["id"]


def test_a_move_with_no_gate_names_the_person_who_moved_it(tmp_path):
    c, sales, success, _ = two(tmp_path)
    L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"], "move")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    assert E.advance(c, ANA, task)["moved"]
    [sent] = rows(c, "SELECT note FROM history_events WHERE subject_id=? AND kind='handoff'", task)
    assert sent["note"] == f"moved by {ANA}"


def test_a_spawn_writes_on_both_cards_and_the_chip_and_from_read_them_back(tmp_path):
    c, _, _, _ = two(tmp_path)  # the same boot as the move tests; its own two pipelines are not used here
    product = H.type_create(
        c, ANA, "Ideas", [{"name": "Review", "status": "doing"}, {"name": "Ship", "status": "doing"}], mover=True
    )
    success = H.type_create(
        c,
        ANA,
        "Delivery",
        [{"name": "Onboarding", "status": "doing"}, {"name": "Active", "status": "doing"}],
        mover=True,
    )
    for t in (product, success):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    link = L.create_link(c, ANA, step_id(c, product["id"], "Review"), success["id"], "spawn")
    parent = H.task_create(c, ANA, "Acme deal", "", ANA, type=product["id"], step="Review", lint=False)["id"]
    H.task_update(c, ANA, parent, step=step_id(c, product["id"], "Ship"))
    [handoff] = L.handoffs(c, link["id"])
    child = handoff["target_task_id"]

    [sent] = rows(c, "SELECT old, new FROM history_events WHERE subject_id=? AND kind='handoff'", parent)
    assert (sent["old"], sent["new"]) == (None, success["id"])
    [got] = rows(c, "SELECT old, note FROM history_events WHERE subject_id=? AND kind='handoff_from'", child)
    assert got["old"] == parent

    cards = L.card_handoffs(c, "1=1", [parent, child])
    assert cards[parent]["chip"] == {
        "handoff_id": handoff["id"],
        "to_pipeline_id": success["id"],
        "pipeline": "Delivery",
        "task_id": child,
        "stage": "Onboarding",
        "readable": True,
    }
    assert cards[child]["from"]["pipeline"] == "Ideas" and cards[child]["from"]["task_id"] == parent
    assert cards[child]["chip"] is None and cards[parent]["from"] is None

    hidden = L.card_handoffs(c, f"id <> '{child}'", [parent])
    assert hidden[parent]["chip"] == {
        "handoff_id": handoff["id"],
        "to_pipeline_id": success["id"],
        "pipeline": "Delivery",
        "task_id": None,
        "stage": None,
        "readable": False,
    }
    assert L.card_handoffs(c, f"id <> '{child}'", [child]) == {}  # a card the reader may not open is not read at all

    no_source = L.card_handoffs(c, f"id <> '{parent}'", [child])
    assert no_source[child]["from"] == {
        "handoff_id": handoff["id"],
        "pipeline": "Ideas",
        "task_id": None,
        "title": None,
        "readable": False,
    }

    c.execute("UPDATE pipeline_handoffs SET returned_at=? WHERE id=?", (H.now(), handoff["id"]))
    back = L.card_handoffs(c, "1=1", [parent, child])
    assert back[parent]["chip"] is None and back[child]["from"] is None
    assert len(rows(c, "SELECT id FROM history_events WHERE subject_id=? AND kind='handoff'", parent)) == 1


def test_a_move_link_gives_no_chip_and_no_from(tmp_path):
    c, sales, success, _ = two(tmp_path)
    L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"], "move")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    E.advance(c, ANA, task)
    assert L.card_handoffs(c, "1=1", [task]) == {task: {"chip": None, "from": None}}


def test_a_card_sent_again_shows_its_open_hand_off_and_keeps_both_activity_lines(tmp_path):
    c, _, _, _ = two(tmp_path)
    product = H.type_create(
        c, ANA, "Ideas", [{"name": "Review", "status": "doing"}, {"name": "Ship", "status": "doing"}], mover=True
    )
    success = H.type_create(
        c,
        ANA,
        "Delivery",
        [{"name": "Onboarding", "status": "doing"}, {"name": "Active", "status": "doing"}],
        mover=True,
    )
    for t in (product, success):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    link = L.create_link(c, ANA, step_id(c, product["id"], "Review"), success["id"], "spawn")
    parent = H.task_create(c, ANA, "Acme deal", "", ANA, type=product["id"], step="Review", lint=False)["id"]
    H.task_update(c, ANA, parent, step=step_id(c, product["id"], "Ship"))
    [first] = L.handoffs(c, link["id"])
    c.execute("UPDATE pipeline_handoffs SET returned_at=? WHERE id=?", (H.now(), first["id"]))
    again = H.task_create(c, ANA, "Acme deal, sent again", "", ANA, type=success["id"], step="Onboarding", lint=False)[
        "id"
    ]
    second = L.record_handoff(c, str(link["id"]), parent, again, ANA)

    cards = L.card_handoffs(c, "1=1", [parent])
    assert cards[parent]["chip"]["handoff_id"] == second and cards[parent]["chip"]["task_id"] == again
    assert len(rows(c, "SELECT id FROM history_events WHERE subject_id=? AND kind='handoff'", parent)) == 2
