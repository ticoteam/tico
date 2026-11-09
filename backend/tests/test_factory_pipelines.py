"""The factory sim's world with more than one pipeline (evals/factory/world.py `pipelines`), and the API paths the
pipeline_handoff and pipeline_marketing cases drive, on a world built for them. No scenario is run here: each test makes
the calls of a tick or two, one at a time."""

import random
from types import SimpleNamespace

from evals.factory.board import dev_board
from evals.factory.cases import pipeline_handoff as handoff
from evals.factory.cases import pipeline_marketing as marketing
from evals.factory.sim import TEAM, Scenario
from evals.factory.world import World


def names(world):
    return [r["name"] for r in world.sql("SELECT name FROM task_types ORDER BY name")]


def test_a_world_builds_the_pipelines_a_scenario_asks_for_and_none_by_default():
    with World(dev_board(), TEAM) as plain:
        assert plain.pipelines == {}
        before = names(plain)
    with World(dev_board(), TEAM, pipelines=(handoff.PRODUCT, marketing.SALES)) as world:
        assert sorted(world.pipelines) == ["Product", "Sales"]
        assert sorted(names(world)) == sorted([*before, "Product", "Sales"])
        stages = [s["name"] for s in world.pipelines["Product"]["steps"]]
        assert stages == [s["name"] for s in handoff.PRODUCT["steps"]]
        review = world.stage_id("Product", "Review")
        assert (
            world.sql("SELECT type_id FROM task_steps WHERE id=?", (review,))[0]["type_id"]
            == (world.pipelines["Product"]["id"])
        )


def test_a_scenario_asks_for_no_pipeline_unless_it_names_one():
    assert Scenario("x", "y", setup=None).pipelines == ()
    assert handoff.SCENARIOS[0].pipelines == (handoff.PRODUCT,)


def ship(world, task):
    """The Engineering ticket ships: Done, as the Release Manager moves it."""
    world.update(task, token="release-test", step="Done")


def test_an_approved_spec_spawns_one_subtask_and_the_card_waits_until_it_ships():
    with World(dev_board(), TEAM, pipelines=(handoff.PRODUCT,)) as world:
        run = SimpleNamespace()
        handoff.setup(run, world, None)
        handoff.during(run, world, None, 0)
        seen = run.handoff
        assert seen["approved"] == set(run.cards)
        assert seen["again"] and all(code == "already_met" for _, _, code in seen["again"]), seen["again"]
        # Sent back to Review and moved on again: the live handoff files nothing more (checked per card below).
        assert [(step, status) for step, status, _ in seen["bounce"]] == [("Review", 200), ("In development", 200)]
        for card in run.cards:
            assert handoff.stage_of(world, card) == "In development"
            (child,) = handoff.subtasks(world, card)
            assert len(handoff.handoffs(world, card)) == 1
            row = world.task(child["id"])
            assert world.step_of(row) == dev_board().first().name
            assert row["owner_id"].startswith("bot:builder-")  # the first stage's pool, as a new Dev ticket is owned
        card = next(c for c in run.cards if c != seen["bounced"])
        (child,) = handoff.subtasks(world, card)
        ship(world, child["id"])
        assert handoff.stage_of(world, card) == "Validate"
        handoff.during(run, world, None, 1)
        assert handoff.stage_of(world, card) == "Done"
        assert not seen["early"] and not seen["late"]
        assert not world.server_errors


def marketing_world():
    return World(marketing.marketing_board(), TEAM, pipelines=(marketing.SALES,))


def fake_run():
    """What the case's setup reads of a run (tickets() and the team), with no builders to swap."""
    team = SimpleNamespace(builders=[])
    return SimpleNamespace(rng=random.Random(0), scenario=SimpleNamespace(major=0.0), t=team), team


def lead_at(world, step, title):
    """A lead ana files, given to builder-claude as the board's assign does (Run.assign)."""
    made = world.post(
        "tasks", {"owner": "ana", "title": title, "body": "A lead.", "type": world.dev["id"], "step": step}
    )
    lead = made.get("task", made)["id"]
    world.update(lead, owner="bot:builder-claude")
    return lead


def test_a_lead_record_is_judged_corrected_and_the_brief_approved_through_the_api():
    """The marketing case's paths, one call at a time: the builder publishes a record with no email and attaches it in
    a claimed turn, the judge pass (inline, the fake engine) says not met, the corrected record is met, and ana's Approve
    of the brief moves the lead on."""
    with marketing_world() as world:
        run, team = fake_run()
        marketing.setup(run, world, team)
        stage = next(s for s in world.dev["steps"] if s["name"] == "Qualifying")["id"]
        rules = world.get(f"pipeline-stages/{stage}/requirements")["requirements"]
        assert {r["name"] for r in rules} == {marketing.CONTACT, marketing.BRIEF}
        limit = world.sql("SELECT limit_minutes FROM task_steps WHERE id=?", (stage,))[0]["limit_minutes"]
        assert limit == marketing.WARN_AFTER_MINUTES
        lead = lead_at(world, "Qualifying", "Lead A")
        runner = world.runner["token"]
        _, reply = world.request("post", "jobs/claim", {"bot": "builder-claude", "start_places": True}, token=runner)
        attempt = reply["attempt"]  # the turn the lead's assignment woke: its token is the bot's for the calls below
        for step, body in (("start-place", {}), ("started", {"thread_id": "sim"})):
            status, answer = world.request("post", f"attempts/{attempt['id']}/{step}", body, token=runner)
            assert status == 200, answer
        bot = marketing.LeadBuilder(world, "builder-claude", "builder", random.Random(0), style={"no_email": 1.0})
        bot._token, bot.marketing, bot.records, bot.sent = attempt["token"], run.marketing, {}, []
        contact = run.marketing[marketing.CONTACT]

        assert bot.send_record(world.task(lead)) == "attached the lead record"
        world.scheduler_pass()
        assert marketing.latest_verdict(world, lead, contact)[0] == "failed"
        assert bot.send_record(world.task(lead)) == "attached the corrected lead record"
        world.scheduler_pass()
        assert marketing.contact_met(world, lead, contact)
        assert bot.send_record(world.task(lead)) == "waiting on the brief"

        marketing.during(run, world, None, 0)
        assert [status for _, _, status in run.approvals] == [200]
        assert world.step_of(world.task(lead)) == "Peer Check"
        assert not world.server_errors


def test_a_lead_handed_over_files_one_sales_opportunity():
    """The Release Manager hands a qualified lead over: entering Handed over files one Sales opportunity under it, and
    the lead's finish leaves it open."""
    with marketing_world() as world:
        run, team = fake_run()
        marketing.setup(run, world, team)
        lead = lead_at(world, "Qualified", "Lead B")
        world.update(lead, token="release-test", step="Handed over")
        sales = world.pipelines["Sales"]["id"]
        found = world.sql(
            "SELECT t.status FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
            "WHERE r.to_task=? AND t.type_id=?",
            (lead, sales),
        )
        assert found == [{"status": "open"}]
        run.w, run.final = world, world.tickets()
        assert marketing.handed_to_sales(run)[0]
        assert not world.server_errors
