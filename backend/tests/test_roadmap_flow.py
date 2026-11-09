"""The legacy roadmap sync (roadmap_flow.sync): a Product card follows its linked Engineering tickets whether the hub
still has the seed's stage names (Approved, Building, Shipped) or pipeline_setup renamed them (Review, In development,
Done). Setup keeps the stage ids, so the sync finds each stage by its id or by either name."""

import json

from backend import hubdb as H
from backend import onboarding, pipeline_review, pipeline_setup, roadmap_flow
from backend.tests.test_pipeline_foundation import ANA, boot, hub, step_id

T = "2026-11-07T09:00:00.000000Z"


def seed(tmp_path):
    boot(tmp_path / "hub.sqlite")
    return hub(tmp_path / "hub.sqlite")


def setup_ran(tmp_path):
    c = seed(tmp_path)
    pipeline_setup.apply_factory_setup(c, ANA)
    c.commit()
    return c


def retire_requirements(c, pipeline_id):
    """The setup's requirements are the off switch of the legacy sync (has_requirements). A pipeline without them is
    the one the sync still drives, so these tests retire them to exercise the name mapping on a setup database."""
    c.execute(
        "UPDATE stage_requirements SET retired_at = ? WHERE pipeline_id = ? AND retired_at IS NULL", (T, pipeline_id)
    )
    c.commit()


def card_in(c, stage_name, stage_id=None):
    product = roadmap_flow.role_type(c, roadmap_flow.PRODUCT)
    step = stage_id or step_id(c, product, stage_name)
    row = H.task_create(c, ANA, "Dark mode", "", ANA, type=product, step=step, lint=False)
    c.commit()
    return row["id"]


def card_past_spec(c):
    """A Product card in Review while its requirements are live: made at Ideas, moved to Specifying, its Spec page met by the
    owner, then moved on, as a real card is."""
    product = roadmap_flow.role_type(c, roadmap_flow.PRODUCT)
    specifying = step_id(c, product, "Specifying")
    row = H.task_create(c, ANA, "Dark mode", "", ANA, type=product, lint=False)
    c.commit()
    H.task_update(c, ANA, row["id"], step=specifying, mover=True)
    c.commit()
    spec_page = c.execute(
        "SELECT id FROM stage_requirements WHERE pipeline_stage_id = ? AND name = 'Spec page' AND retired_at IS NULL",
        (specifying,),
    ).fetchone()[0]
    pipeline_review.mark_met(c, ANA, row["id"], spec_page, "Spec page written in the test")
    c.commit()
    H.task_update(c, ANA, row["id"], step=step_id(c, product, "Review"), mover=True)
    c.commit()
    return row["id"]


def ticket(c, card, eng_stage):
    eng = roadmap_flow.role_type(c, roadmap_flow.ENGINEERING)
    row = H.task_create(
        c, ANA, "Build it", "", ANA, type=eng, step=step_id(c, eng, eng_stage), parent_id=card, lint=False
    )
    c.commit()
    return row["id"]


def move(c, task_id, eng_stage):
    eng = H.task(c, task_id)["type_id"]
    H.task_update(c, ANA, task_id, step=step_id(c, eng, eng_stage), mover=True)
    c.commit()


def stage_of(c, task_id):
    return c.execute(
        "SELECT s.name FROM tasks k JOIN task_steps s ON s.id = k.step_id WHERE k.id = ?", (task_id,)
    ).fetchone()[0]


def test_a_seed_card_in_approved_moves_to_building_when_its_ticket_starts(tmp_path):
    c = seed(tmp_path)
    card = card_in(c, "Approved")
    task = ticket(c, card, "Todo")
    move(c, task, "Doing")
    assert stage_of(c, card) == "Building"


def test_after_setup_a_card_in_review_moves_to_in_development_when_its_ticket_starts(tmp_path):
    c = setup_ran(tmp_path)
    product = roadmap_flow.role_type(c, roadmap_flow.PRODUCT)
    retire_requirements(c, product)
    card = card_in(c, "Review")
    task = ticket(c, card, "Todo")
    move(c, task, "Doing")
    assert stage_of(c, card) == "In development"


def test_after_setup_a_card_follows_its_shipped_ticket_to_done(tmp_path):
    c = setup_ran(tmp_path)
    product = roadmap_flow.role_type(c, roadmap_flow.PRODUCT)
    retire_requirements(c, product)
    card = card_in(c, "In development")
    task = ticket(c, card, "Todo")
    move(c, task, "Done")
    assert stage_of(c, card) == "Done"


def test_after_setup_with_requirements_live_the_legacy_sync_moves_nothing(tmp_path):
    c = setup_ran(tmp_path)
    card = card_past_spec(c)
    task = ticket(c, card, "Todo")
    move(c, task, "Doing")
    assert stage_of(c, card) == "Review"


def test_after_setup_a_card_with_no_linked_tickets_does_not_move(tmp_path):
    c = setup_ran(tmp_path)
    retire_requirements(c, roadmap_flow.role_type(c, roadmap_flow.PRODUCT))
    card = card_in(c, "Review")
    roadmap_flow.changed(c, card)
    c.commit()
    assert stage_of(c, card) == "Review"


def test_a_new_hub_starts_with_its_product_and_engineering_pipelines_and_no_manager(tmp_path):
    """A new hub's onboarding is not complete, so its role types start with no legacy manager and no manager_since: the
    company's own setup binds the pipeline managers (pipeline_setup). A second start adds nothing."""
    c = H.connect(str(tmp_path / "fresh.db"))
    for role in (roadmap_flow.PRODUCT, roadmap_flow.ENGINEERING):
        found = roadmap_flow.role_type(c, role)
        assert found is not None
        assert tuple(c.execute("SELECT manager, manager_since FROM task_types WHERE id=?", (found,)).fetchone()) == (
            None,
            None,
        )
    c.close()
    again = H.connect(str(tmp_path / "fresh.db"))  # a second start adds nothing
    assert again.execute("SELECT COUNT(*) FROM task_types WHERE role IS NOT NULL AND role<>''").fetchone()[0] == 2


def _drop_role_type(c, role):
    c.execute("DELETE FROM task_steps WHERE type_id IN (SELECT id FROM task_types WHERE role=?)", (role,))
    c.execute("DELETE FROM task_types WHERE role=?", (role,))


def test_a_missing_role_type_gets_its_legacy_manager_only_once_onboarding_is_complete(tmp_path):
    c = H.connect(str(tmp_path / "hub.db"))
    _drop_role_type(c, roadmap_flow.PRODUCT)
    roadmap_flow.ensure(c)
    manager = "SELECT manager FROM task_types WHERE role=?"
    assert c.execute(manager, (roadmap_flow.PRODUCT,)).fetchone()[0] is None  # onboarding not complete: no legacy bot
    _drop_role_type(c, roadmap_flow.PRODUCT)
    c.execute(
        "INSERT OR REPLACE INTO registry_metadata(key, value) VALUES(?, ?)",
        (onboarding.KEY, json.dumps({"completed": T})),
    )
    roadmap_flow.ensure(c)
    assert (
        c.execute(manager, (roadmap_flow.PRODUCT,)).fetchone()[0] == roadmap_flow.LEGACY_MANAGERS[roadmap_flow.PRODUCT]
    )
    c.close()


def test_a_type_with_no_manager_stays_without_one_when_the_roles_are_ensured(tmp_path):
    """Criterion 2: the legacy slug is written only into a type this creates. An existing role type with no manager
    stays with none, so the manager column never holds a dangling 'none' or a legacy name it did not have."""
    c = H.connect(str(tmp_path / "hub.db"))
    engineering = roadmap_flow.role_type(c, roadmap_flow.ENGINEERING)
    c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (engineering,))
    roadmap_flow.ensure(c)
    assert tuple(c.execute("SELECT manager, role FROM task_types WHERE id=?", (engineering,)).fetchone()) == (
        None,
        roadmap_flow.ENGINEERING,
    )
    c.close()


def test_a_manager_change_on_engineering_keeps_the_role_holder_found(tmp_path):
    """Criterion 3 (A5): routing finds the role holder through task_types.role, so a changed manager neither moves
    the role nor adds a second holder, and ensure never overwrites the manager a type has."""
    c = H.connect(str(tmp_path / "hub.db"))
    engineering = roadmap_flow.role_type(c, roadmap_flow.ENGINEERING)
    c.execute("UPDATE task_types SET manager='bot:someone-else' WHERE id=?", (engineering,))
    roadmap_flow.ensure(c)
    assert roadmap_flow.role_type(c, roadmap_flow.ENGINEERING) == engineering
    assert c.execute("SELECT manager FROM task_types WHERE id=?", (engineering,)).fetchone()[0] == 'bot:someone-else'
    c.close()


def test_a_hub_that_deleted_its_product_pipeline_does_not_get_it_back_on_restart(tmp_path):
    """r4 follow-up: the first start was guessed from "no Product pipeline and no managed pipeline", so an existing hub
    that had deleted Product and cleared its managers got both role pipelines back on every start. The migration's own
    note is the evidence now, cleared once the hub has seeded."""
    c = H.connect(str(tmp_path / "hub.db"))
    assert c.execute("SELECT COUNT(*) FROM first_start_seeds").fetchone()[0] == 0
    product = roadmap_flow.role_type(c, roadmap_flow.PRODUCT)
    c.execute("UPDATE task_types SET manager=NULL")
    c.execute("DELETE FROM task_steps WHERE type_id=?", (product,))
    c.execute("DELETE FROM task_types WHERE id=?", (product,))
    c.close()
    again = H.connect(str(tmp_path / "hub.db"))
    assert roadmap_flow.role_type(again, roadmap_flow.PRODUCT) is None
    again.close()
