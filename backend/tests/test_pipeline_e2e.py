"""Pipeline engine, end to end: one fixture company built by the Tico preset, one short test per thing the engine promises.

The company is `pipeline_setup.apply_factory_setup(c, ana, "tico_default")` on a booted hub. A booted hub already has the
Product roadmap, Engineering and Marketing pipelines; setup adopts them with their own stages and adds Sales, Success and
the private Legal. So the stage names below are the ones a real install has, and `test_the_company_is_laid_out...` checks
them, so a renamed seed fails there and not in every scenario.

Rows the preset does not make (requirements, attachments, decisions, move links, PR links) are written by the real helper
where one exists (`pipeline_links.create_link`) and by a plain insert elsewhere, as the plan says backend tracks test: a
scenario depends on the engine and not on another track's write path.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend import (
    factory_manager,
    hubdb as H,
    pipeline_engine,
    pipeline_judges,
    pipeline_links,
    pipeline_manager,
    pipeline_overview,
    pipeline_review,
    pipeline_setup,
)
from backend.config import Settings
from backend.store import Store

ANA, BEN, CAL = "human:ana", "human:ben", "human:cal"  # ana owns the pipelines; ben is her backup; cal is a bystander
PM, BUILDER = "bot:pipeline-manager", "bot:builder"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"

# The booted stage names (the preset's own names are overridden by the seed that a booted hub already has).
STAGES = {
    "Product": ["Suggested", "Exploring", "Spec", "Approved", "Building", "Ready to ship", "Shipped", "Declined"],
    "Engineering": ["Todo", "Doing", "In review", "Ready to ship", "Done"],
    "Marketing": ["On deck", "In development", "Live", "Working", "Paused", "Review", "Ready", "Done", "Closed"],
    "Sales": ["Lead", "Talking", "Proposal", "Closing", "Won", "Lost"],
    "Success": ["Onboarding", "Active", "Referral asked", "Referred", "Closed"],
    "Legal": ["Intake", "Review", "Counsel check", "Done", "Closed"],
}
FACTORY_OF = {
    "Product": "Product development",
    "Engineering": "Product development",
    "Marketing": "Growth",
    "Sales": "Growth",
    "Success": "Success",
    "Legal": None,
}


def stamp(minutes_ago=0, base=NOW):
    return (base - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


class Company:
    """The fixture company and the row helpers every scenario shares."""

    def __init__(self, store, c):
        self.store, self.c = store, c
        built = pipeline_setup.apply_factory_setup(c, ANA, "tico_default")
        c.commit()
        self.type = built["pipelines"]  # preset name -> type id
        self.factory = built["factories"]  # preset factory name -> id

    def stage(self, pipeline, name):
        found = self.c.execute(
            "SELECT id FROM task_steps WHERE type_id=? AND name=? COLLATE NOCASE", (self.type[pipeline], name)
        ).fetchone()
        assert found is not None, f"{pipeline} has no stage {name!r}"
        return found[0]

    def ticket(self, pipeline, stage, title="A ticket"):
        row = H.task_create(self.c, ANA, title, "", BUILDER, type=self.type[pipeline], step=stage, lint=False)
        self.c.commit()
        return row["id"]

    def move(self, task_id, stage, actor=BUILDER, **kw):
        row = H.task_update(self.c, actor, task_id, step=stage, mover=True, **kw)
        self.c.commit()
        return row

    def advance(self, task_id, actor=BUILDER):
        result = pipeline_engine.advance(self.c, actor, task_id)
        self.c.commit()
        return result

    def approve(self, task_id, requirement_id, actor=ANA):
        result = pipeline_review.approve(self.c, actor, task_id, requirement_id)
        self.c.commit()
        return result

    def mark_met(self, actor, task_id, requirement_id, note):
        result = pipeline_review.mark_met(self.c, actor, task_id, requirement_id, note)
        self.c.commit()
        return result

    def where(self, task_id):
        """(pipeline name, stage name) of a ticket now."""
        row = self.c.execute(
            "SELECT t.name, s.name FROM tasks k JOIN task_types t ON t.id=k.type_id JOIN task_steps s ON s.id=k.step_id "
            "WHERE k.id=?",
            (task_id,),
        ).fetchone()
        return tuple(row)

    def require(self, pipeline, stage, name, kind="attachment", reviewed_by="nobody", **extra):
        """A live requirement on a stage; returns its id. `reviewer_id` defaults to Ana for a person's review and to the
        builder bot for a bot-only review (the manager only asks a reviewer it has)."""
        rid = f"req-{pipeline.lower()}-{name.lower().replace(' ', '-')}"
        needs = extra.pop("needs", {"formats": ["image"], "newer_than_change": True} if kind == "attachment" else {})
        if kind == "approval" or "person" in reviewed_by:
            default_reviewer = ANA
        elif reviewed_by == "bot":
            default_reviewer = BUILDER
        else:
            default_reviewer = None
        columns = {
            "id": rid,
            "pipeline_id": self.type[pipeline],
            "pipeline_stage_id": self.stage(pipeline, stage),
            "name": name,
            "kind": kind,
            "needs": json.dumps(needs),
            "reviewed_by": "person" if kind == "approval" else reviewed_by,
            "reviewer_id": extra.pop("reviewer_id", default_reviewer),
            "version": 1,
            "created_by": ANA,
            "created_at": stamp(),
            "updated_at": stamp(),
            **extra,
        }
        self.insert("stage_requirements", **columns)
        self.insert(
            "stage_requirement_versions",
            stage_requirement_id=rid,
            version=1,
            definition=json.dumps({"name": name, "kind": kind, "needs": needs}),
            changed_by=ANA,
            created_at=stamp(),
        )
        self.c.commit()
        return rid

    def link(self, pipeline, stage, to, mode="move"):
        """A pipeline link, made by the real helper."""
        pipeline_links.create_link(self.c, ANA, self.stage(pipeline, stage), self.type[to], mode)
        self.c.commit()

    def push(self, task_id):
        """New work on the ticket (a push to its pull request): the work version moves on."""
        pipeline_engine.work_changed(self.c, task_id)
        self.c.commit()

    def attach(self, task_id, req_id, by=BUILDER, digest="sha-1"):
        self.insert(
            "requirement_attachments",
            id=f"att-{task_id[-6:]}-{req_id[-8:]}-{digest}",
            task_id=task_id,
            stage_requirement_id=req_id,
            source="link",
            url="https://example.test/shot.png",
            digest=digest,
            work_version=pipeline_engine.work_version(self.c, task_id),
            attached_by=by,
            created_at=stamp(),
        )
        self.c.commit()

    def decide(self, task_id, req_id, verdict="met", judge="person", actor=ANA, reason=None):
        row = self.c.execute(
            "SELECT pipeline_stage_id, version FROM stage_requirements WHERE id=?", (req_id,)
        ).fetchone()
        self.insert(
            "stage_decisions",
            id=f"dec-{task_id[-6:]}-{req_id[-8:]}-{self.count('stage_decisions')}",
            task_id=task_id,
            pipeline_stage_id=row[0],
            stage_requirement_id=req_id,
            requirement_version=row[1],
            work_version=pipeline_engine.work_version(self.c, task_id),
            judge=judge,
            judge_actor=actor,
            verdict=verdict,
            reason=reason,
            created_at=stamp(),
        )
        self.c.commit()

    def count(self, table, where="1=1", *args):
        return self.c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]

    def insert(self, table, **values):
        self.c.execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def dots(self, task_id):
        """{requirement name: dot} as the board shows them. A dot's state is met, waiting, out_of_date, failed or none."""
        row = H.task(self.c, task_id)
        return {d["name"]: d for d in pipeline_engine.dots_many(self.c, [row]).get(task_id, [])}

    def age_visit(self, task_id, minutes, base=NOW):
        """The ticket has been in its stage `minutes` before `base`. Two clocks read this: the Limit's (step_age: the
        newest step or status event, else the creation) and the overview's (the open stage visit). Both start then."""
        pipeline_engine.ensure_visit(self.c, task_id)
        when = stamp(minutes, base)
        self.c.execute("UPDATE stage_visits SET entered_at=? WHERE task_id=? AND left_at IS NULL", (when, task_id))
        self.c.execute("UPDATE tasks SET created_at=? WHERE id=?", (when, task_id))
        self.c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind IN ('step','status')",
            (when, task_id),
        )
        self.c.commit()

    def warn_after(self, pipeline, stage, minutes):
        self.c.execute("UPDATE task_steps SET limit_minutes=? WHERE id=?", (minutes, self.stage(pipeline, stage)))
        self.c.commit()

    def refused(self, task_id, stage):
        """True when moving the ticket to `stage` is refused for a requirement."""
        try:
            self.move(task_id, stage)
        except H.Refused as exc:
            self.c.rollback()
            return exc.rule == "requirement"
        return False


@pytest.fixture
def co(tmp_path):
    path = tmp_path / "hub.sqlite"
    store = Store(Settings(db_path=path, registry_dir=FIXTURES / "registry"))
    store.initialize(seed_market=False)
    c = H.connect(path)
    H.sync_registry(
        c,
        {"pipeline-manager": {}, "builder": {}, "botops": {"name": "botops", "status": "active"}},
        {
            "people": [
                {"id": "ana", "email": "ana@acme.example"},
                {"id": "ben", "email": "ben@acme.example"},
                {"id": "cal", "email": "cal@acme.example"},
            ]
        },
    )
    yield Company(store, c)
    c.close()


# ----------------------------------------------------------------------------- the company
def test_a_pipeline_with_no_requirements_moves_freely(co):
    """The off switch: no stage has a requirement, so a ticket walks its pipeline and no card carries a dot."""
    task = co.ticket("Engineering", "Todo")
    co.move(task, "Doing", actor=ANA)
    co.move(task, "In review", actor=ANA)
    co.move(task, "Ready to ship", actor=ANA)  # a person sets ready to ship; a bot may not (the merge sets it)
    assert co.where(task) == ("Engineering", "Ready to ship")
    assert pipeline_engine.dots_many(co.c, [H.task(co.c, task)]) == {}


def test_the_company_is_laid_out_as_the_plan_describes(co):
    for pipeline, stages in STAGES.items():
        for stage in stages:
            co.stage(pipeline, stage)  # raises when a stage the scenarios use is missing
    rows = co.c.execute(
        "SELECT t.name, f.name, t.pipeline_visibility FROM task_types t LEFT JOIN factories f ON f.id=t.factory_id "
        "WHERE t.name IN (" + ", ".join("?" for _ in STAGES) + ")",
        tuple(STAGES),
    ).fetchall()
    assert {name: (factory, visibility) for name, factory, visibility in rows} == {
        "Product": ("Product development", "company"),
        "Engineering": ("Product development", "company"),
        "Marketing": ("Growth", "company"),
        "Sales": ("Growth", "company"),
        "Success": ("Success", "company"),
        "Legal": (None, "members"),
    }


# ----------------------------------------------------------------------------- handoffs
def test_an_approved_spec_is_stored_as_a_spawn_and_does_not_create_engineering_in_v1(co):
    """Plan section 2: the Product Approved -> Engineering link is stored as a spawn, and v1 does not run it. Approving
    the spec moves the card on inside Product; no Engineering ticket appears."""
    spec_ok = co.require("Product", "Approved", "Spec approved", kind="approval")
    task = co.ticket("Product", "Approved", "Dark mode")
    assert co.count("pipeline_links", "from_pipeline_stage_id=? AND mode='spawn'", co.stage("Product", "Approved")) == 1
    engineering_before = co.count("tasks", "type_id=?", co.type["Engineering"])
    co.approve(task, spec_ok)
    assert co.where(task)[0] == "Product"
    assert co.count("tasks", "type_id=?", co.type["Engineering"]) == engineering_before


def test_a_shipped_ticket_goes_back_to_product_through_its_move_link(co):
    """Engineering's Ready to ship moves a ticket back into Product's first stage through a move link. The evidence on
    Doing (a stage the ticket passed) must be met first, and the handoff is recorded (the flow map counts it)."""
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    co.link("Engineering", "Ready to ship", "Product")
    task = co.ticket("Engineering", "Doing", "Dark mode")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    co.move(task, "In review", actor=ANA)
    co.move(task, "Ready to ship", actor=ANA)
    result = co.advance(task, actor=ANA)
    assert result["moved"] is True
    assert co.where(task) == ("Product", "Suggested")
    assert co.count("pipeline_handoffs", "source_task_id=? AND target_task_id=?", task, task) == 1
    assert co.count("stage_visits", "task_id=? AND direction='handoff'", task) == 1


def test_a_marketing_lead_is_handed_to_sales_when_approved(co):
    """Approval happens on an open stage (Review); a done stage is finished and takes no decision."""
    co.link("Marketing", "Review", "Sales")
    lead_ok = co.require("Marketing", "Review", "Lead has company and contact", kind="approval")
    task = co.ticket("Marketing", "Review", "Acme Lighting")
    co.approve(task, lead_ok)
    assert co.where(task) == ("Sales", "Lead")
    assert co.count("pipeline_handoffs", "source_task_id=? AND target_task_id=?", task, task) == 1


# ----------------------------------------------------------------------------- evidence
def test_missing_evidence_blocks_the_move(co):
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Doing")
    assert co.refused(task, "In review")
    assert co.dots(task)["Screenshots"]["state"] == "none"
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    assert not co.refused(task, "In review")


def test_evidence_goes_out_of_date_after_a_push(co):
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Doing")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    assert co.dots(task)["Screenshots"]["state"] == "met"
    co.push(task)
    assert co.dots(task)["Screenshots"]["state"] == "out_of_date"
    assert co.refused(task, "In review")


# ----------------------------------------------------------------------------- judges
def test_an_unsure_judge_goes_to_a_person_and_never_passes(co):
    shots = co.require(
        "Engineering",
        "Doing",
        "Screenshots",
        reviewed_by="bot_then_person",
        reviewer_id=ANA,
        rubric="The screenshot shows the feature working",
    )
    task = co.ticket("Engineering", "Doing")
    co.attach(task, shots)

    def unsure(model, state, questions, label):
        return {"p": 0.5, "reason": "cannot tell", "provider": "fake", "model": model, "cost_usd": 0.0, "latency_ms": 1}

    pipeline_judges.ENGINE = unsure
    try:
        pipeline_judges.tick(co.store, NOW)
    finally:
        pipeline_judges.ENGINE = None
    assert co.count("stage_decisions", "task_id=? AND verdict='unsure'", task) == 1
    assert co.count("stage_decisions", "task_id=? AND verdict='met'", task) == 0
    assert co.dots(task)["Screenshots"]["state"] == "waiting"
    assert task in [item["task_id"] for item in pipeline_review.queue_for(co.c, ANA)]


def test_a_person_who_is_away_hands_the_review_to_the_backup(co):
    """The reviewer sits on it past Warn after: the manager asks the backup, who then sees it in the queue."""
    shots = co.require(
        "Engineering", "In review", "Screenshots", reviewed_by="person", reviewer_id=ANA, backup_reviewer_id=BEN
    )
    co.warn_after("Engineering", "In review", 60)
    task = co.ticket("Engineering", "In review")
    co.attach(task, shots)
    co.age_visit(task, 120)
    assert task not in [i["task_id"] for i in pipeline_review.queue_for(co.c, BEN)]
    pipeline_manager.tick(co.store, NOW)
    assert co.count("pipeline_manager_actions", "task_id=? AND action='ask_backup'", task) == 1
    assert task in [i["task_id"] for i in pipeline_review.queue_for(co.c, BEN)]


# ----------------------------------------------------------------------------- the managers
def test_late_card_is_asked_once_and_the_factory_manager_sees_the_bottleneck(co):
    """Past Warn after the Pipeline Manager asks once per visit (two passes, one ask); the Factory Manager reads the stage
    with the most Late cards across the factory as its bottleneck."""
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    co.warn_after("Engineering", "Doing", 60)
    tasks = [co.ticket("Engineering", "Doing", f"Late {n}") for n in range(3)]
    for task in tasks:
        co.age_visit(task, 180)
    pipeline_manager.tick(co.store, NOW)
    pipeline_manager.tick(co.store, NOW + timedelta(minutes=10))
    for task in tasks:
        assert (
            co.count("pipeline_manager_actions", "task_id=? AND stage_requirement_id=? AND action='ask'", task, shots)
            == 1
        )
    late = [item["task_id"] for item in pipeline_manager.rail(co.c, co.type["Engineering"], NOW)["late"]]
    assert sorted(late) == sorted(tasks)
    bottleneck = factory_manager.factory_rail(co.c, co.factory["Product development"], NOW)["bottleneck"]
    assert bottleneck["stage_id"] == co.stage("Engineering", "Doing")


def test_a_pipeline_manager_that_stops_opens_one_botops_task(co):
    co.insert(
        "pipeline_manager_runs",
        id="run-old",
        pipeline_id=co.type["Engineering"],
        started_at=stamp(40),
        finished_at=stamp(40),
        status="ok",
    )
    co.c.commit()
    pipeline_manager.watchdog(co.store, NOW)
    pipeline_manager.watchdog(co.store, NOW + timedelta(minutes=5))
    opened = co.c.execute(
        "SELECT COUNT(*) FROM tasks WHERE title=? AND status NOT IN ('done','closed')",
        ("Pipeline manager stopped: Engineering",),
    ).fetchone()[0]
    assert opened == 1
    assert co.count("pipeline_manager_actions", "action='open_botops' AND pipeline_id=?", co.type["Engineering"]) == 1


def test_a_bot_that_never_answers_makes_the_card_late_not_silent(co):
    co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    co.warn_after("Engineering", "Doing", 45)
    task = co.ticket("Engineering", "Doing", "Stuck runner")
    co.age_visit(task, 300)
    pipeline_manager.tick(co.store, NOW)
    rail = pipeline_manager.rail(co.c, co.type["Engineering"], NOW)
    assert task in [item["task_id"] for item in rail["late"]]
    assert rail["last_run"] is not None and rail["last_run"]["status"] == "ok", "the pass is logged when nobody answers"


# ----------------------------------------------------------------------------- merge queue
def test_a_pr_ejected_from_the_merge_queue_sends_the_card_back_with_the_reason(co):
    from backend import github

    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")  # the pipeline has requirements
    task = co.ticket("Engineering", "Doing", "Merge me")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    co.move(task, "In review", actor=ANA)
    link_id = "lnk-pr-1"
    detail = {
        "head_sha": "abc123",
        "merge_queue": {"state": "dequeued", "reason": "checks failed", "head": "abc123", "verify": True},
    }
    co.insert(
        "task_links",
        id=link_id,
        task_id=task,
        kind="pr",
        url="https://github.com/acme/app/pull/7",
        state="open",
        added_by=BUILDER,
        created_at=stamp(),
        detail=json.dumps(detail),
    )
    co.c.commit()
    github._settle_removal(co.c, link_id, {"state": "open", "merged": False, "head": {"sha": "abc123"}})
    co.c.commit()
    assert co.where(task) == ("Engineering", "Doing")
    notes = [
        row[0]
        for row in co.c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='queue_ejected'",
            (task,),
        )
    ]
    assert notes == ["checks failed"]


# ----------------------------------------------------------------------------- Mark met
def test_the_owner_can_mark_met_and_a_bot_cannot(co):
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Doing")
    with pytest.raises(H.Refused):
        pipeline_review.mark_met(co.c, PM, task, shots, "looks fine")
    co.c.rollback()
    with pytest.raises(H.Refused):
        pipeline_review.mark_met(co.c, ANA, task, shots, "")  # a note is required
    co.c.rollback()
    co.mark_met(ANA, task, shots, "Shots live in the design doc")
    assert co.count("stage_decisions", "task_id=? AND verdict='override' AND judge_actor=?", task, ANA) == 1
    assert co.dots(task)["Screenshots"]["state"] == "met"
    co.push(task)
    assert co.dots(task)["Screenshots"]["state"] == "out_of_date"  # new work makes a met out of date


# ----------------------------------------------------------------------------- privacy
def test_a_private_legal_pipeline_is_hidden_from_a_non_member(co):
    """The preset makes Legal private (pipeline_visibility 'members'): the owner sees it, a non-member does not."""
    legal = co.require("Legal", "Review", "Counsel signed off", kind="approval")
    task = co.ticket("Legal", "Review", "NDA")
    assert task in [i["task_id"] for i in pipeline_review.queue_for(co.c, ANA)]
    for hidden_from in (CAL, BEN):
        seen = pipeline_overview.read(co.c, hidden_from, "7d")
        assert co.type["Legal"] not in [p["id"] for p in seen["pipelines"]]
        assert legal not in [i.get("requirement_id") for i in pipeline_review.queue_for(co.c, hidden_from)]
    mine = pipeline_overview.read(co.c, ANA, "7d")
    assert co.type["Legal"] in [p["id"] for p in mine["pipelines"]]


# ----------------------------------------------------------------------------- the flow map
def test_flow_map_numbers_equal_the_database(co):
    """Handoff counts per period, the cards in each pipeline, overdue and done are what the tables hold."""
    co.link("Marketing", "Review", "Sales")
    lead_ok = co.require("Marketing", "Review", "Lead has company and contact", kind="approval")
    co.warn_after("Engineering", "Doing", 60)
    for n in range(3):  # three leads handed from Marketing to Sales inside the week
        co.approve(co.ticket("Marketing", "Review", f"Lead {n}"), lead_ok)
    old = co.ticket("Engineering", "Doing", "Old")
    co.age_visit(old, 500, base=datetime.now(UTC))  # the period is anchored at the real clock, so age from it

    seen = pipeline_overview.read(co.c, ANA, "7d")
    edge = next(e for e in seen["edges"] if e["from"] == co.type["Marketing"] and e["to"] == co.type["Sales"])
    truth = co.count(
        "stage_visits",
        "pipeline_id=? AND direction='handoff' AND entered_at>=?",
        co.type["Sales"],
        stamp(7 * 24 * 60, base=datetime.now(UTC)),
    )
    assert edge["count"] == truth == 3

    eng = next(p for p in seen["pipelines"] if p["id"] == co.type["Engineering"])
    in_stage = co.count("tasks", "type_id=? AND status NOT IN ('done','closed','declined')", co.type["Engineering"])
    assert eng["cards"] == in_stage == 1
    assert eng["overdue"] == 1

    product_dev = next(f for f in seen["factories"] if f["id"] == co.factory["Product development"])
    done_truth = co.count(
        "tasks",
        "type_id IN (?, ?) AND status='done' AND done_at>=?",
        co.type["Product"],
        co.type["Engineering"],
        stamp(7 * 24 * 60, base=datetime.now(UTC)),
    )
    assert product_dev["done"] == done_truth
