"""Pipeline engine, end to end: one fixture company, one short test per thing the engine promises.

Written against the plan's contract (`_pipeline-engine-plan.md` section 1 and the section 6 amendments), before tracks A-D
land. Every scenario that needs an engine track is marked xfail(strict=False): the file is green now, and each scenario
turns into a real check when its track merges (a track that lands makes its xfails XPASS; then delete the mark).

The company: factories Product development (Product -> Engineering) and Growth (Marketing -> Sales), a Success pipeline in
no factory, and a private Legal pipeline. Rows (requirements, attachments, decisions, links) are inserted directly, as the
plan says backend tracks test, so a scenario depends on the engine and not on another track's write path.

Contract assumptions, kept in the helpers at the top so one edit fixes them when a track names something differently:
- `pipeline_engine.dots_many` -> {task id: [{"name", "state", ...}]}, state in missing|waiting|met|failed (D39);
- a refused move raises hubdb.Refused with rule "requirement";
- `pipeline_review.approve(c, actor, task_id, requirement_id)` and `.mark_met(c, actor, task_id, requirement_id, note)`;
- `pipeline_review.queue_for(c, actor)` -> [{"task_id", ...}];
- `pipeline_manager.rail(c, pipeline_id)` -> {"late": [{"task_id"}], "log": [...]} and `.factory_rail(c, factory_id)`
  -> {"bottleneck": stage id | None};
- `pipeline_overview.read(c, actor, period)` -> {"factories", "pipelines", "edges", "inputs", "loops"} (period "7d").
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend import hubdb as H
from backend import (
    pipeline_engine,
    pipeline_judges,
    pipeline_manager,
    pipeline_overview,
    pipeline_review,
)
from backend.config import Settings
from backend.store import Store

ANA, BEN, CAL = "human:ana", "human:ben", "human:cal"  # ana owns the pipelines; ben is her backup; cal is a bystander
PM, BUILDER = "bot:pipeline-manager", "bot:builder"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"

needs_a = pytest.mark.xfail(strict=False, reason="needs Track A (engine core and links)")
needs_ab = pytest.mark.xfail(strict=False, reason="needs Tracks A and B (requirements, judges)")
needs_ac = pytest.mark.xfail(strict=False, reason="needs Tracks A and C (review mode)")
needs_ad = pytest.mark.xfail(strict=False, reason="needs Tracks A and D (pipeline manager)")
needs_af = pytest.mark.xfail(strict=False, reason="needs Tracks A and F (overview read)")


def stamp(minutes_ago=0):
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


# ----------------------------------------------------------------------------- the company
PIPELINES = {
    "Product": ["Idea", "Spec", "Ready for build"],
    "Engineering": ["Build", "Review", "Shipped"],
    "Marketing": ["New lead", "Qualifying", "Qualified"],
    "Sales": ["Intake", "Talking", "Won"],
    "Success": ["Onboarding", "Healthy"],
    "Legal": ["Request", "Review", "Signed"],
}
FACTORIES = {"Product development": ["Product", "Engineering"], "Growth": ["Marketing", "Sales"]}


class Company:
    """The fixture company and the few row-writing helpers every scenario shares."""

    def __init__(self, store, c):
        self.store, self.c = store, c
        self.type = {}
        self.factory = {}
        for name, stages in PIPELINES.items():
            # A booted hub already has Engineering and Marketing types; set them aside so these are the company's own.
            c.execute("UPDATE task_types SET name=name||' (seed)' WHERE name=? COLLATE NOCASE", (name,))
            steps = [{"name": s, "status": "open" if i == 0 else "doing"} for i, s in enumerate(stages)]
            steps[-1]["status"] = "done"
            row = H.type_create(c, ANA, name, steps, numbered=True, mover=True)
            self.type[name] = row["id"]
            c.execute("UPDATE task_types SET pipeline_owner_id=?, manager=? WHERE id=?", (ANA, PM, row["id"]))
        for position, (name, members) in enumerate(FACTORIES.items()):
            fid = f"fac-{name[:3].lower()}"
            self.factory[name] = fid
            c.execute(
                "INSERT INTO factories(id,name,owner_id,position,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (fid, name, ANA, position, stamp(), stamp()),
            )
            for member in members:
                c.execute("UPDATE task_types SET factory_id=? WHERE id=?", (fid, self.type[member]))
        c.commit()

    def stage(self, pipeline, name):
        return self.c.execute(
            "SELECT id FROM task_steps WHERE type_id=? AND name=?", (self.type[pipeline], name)
        ).fetchone()[0]

    def ticket(self, pipeline, stage, title="A ticket"):
        row = H.task_create(self.c, ANA, title, "", BUILDER, type=self.type[pipeline], step=stage, lint=False)
        self.c.commit()
        return row["id"]

    def move(self, task_id, stage, actor=BUILDER, **kw):
        row = H.task_update(self.c, actor, task_id, step=stage, mover=True, **kw)
        self.c.commit()
        return row

    def where(self, task_id):
        """(pipeline name, stage name) of a ticket now."""
        row = self.c.execute(
            "SELECT t.name, s.name FROM tasks k JOIN task_types t ON t.id=k.type_id JOIN task_steps s ON s.id=k.step_id "
            "WHERE k.id=?",
            (task_id,),
        ).fetchone()
        return tuple(row)

    def require(self, pipeline, stage, name, kind="attachment", reviewed_by="nobody", **extra):
        """A live requirement on a stage; returns its id."""
        rid = f"req-{name.lower().replace(' ', '-')}-{stage.lower().replace(' ', '-')}"
        needs = extra.pop("needs", {"formats": ["image"], "newer_than_change": True} if kind == "attachment" else {})
        columns = {
            "id": rid,
            "pipeline_id": self.type[pipeline],
            "pipeline_stage_id": self.stage(pipeline, stage),
            "name": name,
            "kind": kind,
            "needs": json.dumps(needs),
            "reviewed_by": "person" if kind == "approval" else reviewed_by,
            "reviewer_id": extra.pop("reviewer_id", ANA if kind == "approval" or "person" in reviewed_by else None),
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
        self.insert(
            "pipeline_links",
            id=f"lnk-{pipeline.lower()}-{to.lower()}",
            from_pipeline_id=self.type[pipeline],
            from_pipeline_stage_id=self.stage(pipeline, stage),
            to_pipeline_id=self.type[to],
            mode=mode,
            created_by=ANA,
            created_at=stamp(),
        )
        self.c.commit()

    def revision(self, task_id):
        return self.c.execute("SELECT work_revision FROM tasks WHERE id=?", (task_id,)).fetchone()[0]

    def push(self, task_id):
        """New work on the ticket (a push to its PR): the work version moves on."""
        self.c.execute("UPDATE tasks SET work_revision=work_revision+1 WHERE id=?", (task_id,))
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
            work_version=self.revision(task_id),
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
            work_version=self.revision(task_id),
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
        """{requirement name: dot} as the board shows them."""
        row = H.task(self.c, task_id)
        return {d["name"]: d for d in pipeline_engine.dots_many(self.c, [row]).get(task_id, [])}

    def age_visit(self, task_id, minutes):
        """The ticket has been in its stage `minutes` (its open visit started that long ago)."""
        self.c.execute(
            "UPDATE stage_visits SET entered_at=? WHERE task_id=? AND left_at IS NULL", (stamp(minutes), task_id)
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
        {"pipeline-manager": {}, "builder": {}},
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


# ----------------------------------------------------------------------------- now (no track needed)
def test_a_pipeline_with_no_requirements_moves_freely(co):
    """The off switch: no stage has a requirement, so a ticket walks its pipeline and no card carries a dot."""
    task = co.ticket("Engineering", "Build")
    co.move(task, "Review")
    co.move(task, "Shipped")
    assert co.where(task) == ("Engineering", "Shipped")
    assert pipeline_engine.dots_many(co.c, [H.task(co.c, task)]) == {}


def test_the_company_is_laid_out_as_the_plan_describes(co):
    names = {
        r[0]: r[1]
        for r in co.c.execute("SELECT t.name, f.name FROM task_types t JOIN factories f ON f.id=t.factory_id")
    }
    assert names == {
        "Product": "Product development",
        "Engineering": "Product development",
        "Marketing": "Growth",
        "Sales": "Growth",
    }


# ----------------------------------------------------------------------------- the happy path and handoffs
@needs_ac
def test_product_to_engineering_and_back_to_product(co):
    """A spec is approved, the card moves into Engineering, ships, and comes back to Product (the loop)."""
    co.link("Product", "Ready for build", "Engineering")
    co.link("Engineering", "Shipped", "Product")
    spec_ok = co.require("Product", "Ready for build", "Spec approved", kind="approval")
    shots = co.require("Engineering", "Review", "Screenshots", reviewed_by="bot")
    task = co.ticket("Product", "Ready for build", "Dark mode")

    pipeline_review.approve(co.c, ANA, task, spec_ok)
    assert co.where(task) == ("Engineering", "Build")

    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    co.move(task, "Review")
    co.move(task, "Shipped")
    assert co.where(task)[0] == "Product"
    assert [d for d in co.c.execute("SELECT direction FROM stage_visits WHERE task_id=? ORDER BY entered_at", (task,))][
        -1
    ][0] == "handoff"


@needs_ac
def test_a_marketing_lead_is_handed_to_sales(co):
    co.link("Marketing", "Qualified", "Sales")
    lead_ok = co.require("Marketing", "Qualified", "Lead has company and contact", kind="approval")
    task = co.ticket("Marketing", "Qualified", "Acme Lighting")
    pipeline_review.approve(co.c, ANA, task, lead_ok)
    assert co.where(task) == ("Sales", "Intake")


# ----------------------------------------------------------------------------- evidence
@needs_ab
def test_missing_evidence_blocks_the_move(co):
    shots = co.require("Engineering", "Build", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Build")
    assert co.refused(task, "Review")
    assert co.dots(task)["Screenshots"]["state"] == "missing"
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    assert not co.refused(task, "Review")


@needs_ab
def test_evidence_goes_stale_after_a_push(co):
    shots = co.require("Engineering", "Build", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Build")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    assert co.dots(task)["Screenshots"]["state"] == "met"
    co.push(task)
    assert co.dots(task)["Screenshots"]["state"] == "failed"
    assert co.refused(task, "Review")


# ----------------------------------------------------------------------------- judges
@needs_ab
def test_an_unsure_judge_goes_to_a_person_and_never_passes(co):
    shots = co.require("Engineering", "Build", "Screenshots", reviewed_by="bot_then_person", reviewer_id=ANA)
    task = co.ticket("Engineering", "Build")
    co.attach(task, shots)
    pipeline_judges.ENGINE = lambda state, questions, label=None: {
        "answers": {"met": {"noul": 0.5}},
        "usage": {},
        "model": "fake",
        "provider": "fake",
        "ms": 1,
    }
    try:
        pipeline_judges.tick(co.store, NOW)
    finally:
        pipeline_judges.ENGINE = None
    assert co.count("stage_decisions", "task_id=? AND verdict='unsure'", task) == 1
    assert co.count("stage_decisions", "task_id=? AND verdict='met'", task) == 0
    assert co.dots(task)["Screenshots"]["state"] == "waiting"
    assert task in [item["task_id"] for item in pipeline_review.queue_for(co.c, ANA)]


@needs_ad
def test_a_person_who_is_away_hands_the_review_to_the_backup(co):
    """The reviewer sits on it past Warn after: the manager asks the backup, who then sees it in the queue."""
    shots = co.require(
        "Engineering", "Review", "Screenshots", reviewed_by="person", reviewer_id=ANA, backup_reviewer_id=BEN
    )
    co.warn_after("Engineering", "Review", 60)
    task = co.ticket("Engineering", "Review")
    co.attach(task, shots)
    co.age_visit(task, 120)
    assert task not in [i["task_id"] for i in pipeline_review.queue_for(co.c, BEN)]
    pipeline_manager.tick(co.store, NOW)
    assert co.count("pipeline_manager_actions", "task_id=? AND action='ask_backup'", task) == 1
    assert task in [i["task_id"] for i in pipeline_review.queue_for(co.c, BEN)]


# ----------------------------------------------------------------------------- the managers
@needs_ad
def test_late_card_is_asked_once_and_the_factory_manager_sees_the_bottleneck(co):
    """Past Warn after the Pipeline Manager asks once per visit (two ticks, one ask); the Factory Manager reads the stage
    with the most Late cards across the factory as its bottleneck."""
    co.warn_after("Engineering", "Build", 60)
    tasks = [co.ticket("Engineering", "Build", f"Late {n}") for n in range(3)]
    shots = co.require("Engineering", "Build", "Screenshots", reviewed_by="bot")
    for task in tasks:
        co.age_visit(task, 180)
    pipeline_manager.tick(co.store, NOW)
    pipeline_manager.tick(co.store, NOW + timedelta(minutes=10))
    for task in tasks:
        assert (
            co.count("pipeline_manager_actions", "task_id=? AND stage_requirement_id=? AND action='ask'", task, shots)
            == 1
        )
    late = [item["task_id"] for item in pipeline_manager.rail(co.c, co.type["Engineering"])["late"]]
    assert sorted(late) == sorted(tasks)
    assert pipeline_manager.factory_rail(co.c, co.factory["Product development"])["bottleneck"] == co.stage(
        "Engineering", "Build"
    )


@needs_ad
def test_a_pipeline_manager_that_stops_opens_one_botops_task(co):
    old = stamp(40)
    co.insert(
        "pipeline_manager_runs",
        id="run-old",
        pipeline_id=co.type["Engineering"],
        started_at=old,
        finished_at=old,
        status="ok",
    )
    co.c.commit()
    pipeline_manager.watchdog(co.store, NOW)
    pipeline_manager.watchdog(co.store, NOW + timedelta(minutes=5))
    opened = co.c.execute(
        "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done','closed') AND title LIKE '%manager%'"
    ).fetchone()[0]
    assert opened == 1
    assert co.count("pipeline_manager_actions", "action='open_botops'") == 1


@needs_ad
def test_a_bot_that_never_answers_makes_the_card_late_not_silent(co):
    co.warn_after("Engineering", "Build", 45)
    task = co.ticket("Engineering", "Build", "Stuck runner")
    co.age_visit(task, 300)
    pipeline_manager.tick(co.store, NOW)
    rail = pipeline_manager.rail(co.c, co.type["Engineering"])
    assert task in [item["task_id"] for item in rail["late"]]
    assert rail["log"], "the run is logged even when nobody answers"


# ----------------------------------------------------------------------------- merge queue
@needs_a
def test_a_pr_ejected_from_the_merge_queue_sends_the_card_back_with_the_reason(co):
    from backend import github

    task = co.ticket("Engineering", "Review")
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
    assert co.where(task) == ("Engineering", "Build")
    comments = " ".join(str(m) for m in H.task_comments(co.c, task))
    assert "checks failed" in comments


# ----------------------------------------------------------------------------- Mark met
@needs_ac
def test_the_owner_can_mark_met_and_a_bot_cannot(co):
    shots = co.require("Engineering", "Build", "Screenshots", reviewed_by="bot")
    task = co.ticket("Engineering", "Build")
    with pytest.raises(H.Refused):
        pipeline_review.mark_met(co.c, PM, task, shots, "looks fine")
    with pytest.raises(H.Refused):
        pipeline_review.mark_met(co.c, ANA, task, shots, "")  # a note is required
    pipeline_review.mark_met(co.c, ANA, task, shots, "Shots live in the design doc")
    assert co.count("stage_decisions", "task_id=? AND verdict='override' AND judge_actor=?", task, ANA) == 1
    assert co.dots(task)["Screenshots"]["state"] == "met"
    co.push(task)
    assert co.dots(task)["Screenshots"]["state"] == "failed"  # new work makes it out of date like any met


# ----------------------------------------------------------------------------- privacy
@needs_af
def test_a_private_legal_pipeline_is_hidden_from_a_non_member(co):
    co.c.execute("UPDATE task_types SET visibility='members' WHERE id=?", (co.type["Legal"],))
    co.c.commit()
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
@needs_af
def test_flow_map_numbers_equal_the_database(co):
    """Handoff counts per period, the cards in each pipeline, and done / overdue are what the tables hold."""
    co.link("Product", "Ready for build", "Engineering")
    co.link("Marketing", "Qualified", "Sales")
    co.warn_after("Engineering", "Build", 60)
    spec_ok = co.require("Product", "Ready for build", "Spec approved", kind="approval")
    for n in range(3):  # three handoffs Product -> Engineering inside the week
        pipeline_review.approve(co.c, ANA, co.ticket("Product", "Ready for build", f"Spec {n}"), spec_ok)
    old = co.ticket("Engineering", "Build", "Old")
    co.age_visit(old, 500)
    done = co.ticket("Engineering", "Build", "Done one")
    co.move(done, "Review")
    co.move(done, "Shipped")

    seen = pipeline_overview.read(co.c, ANA, "7d")
    edge = next(
        e
        for e in seen["edges"]
        if e["from_pipeline_id"] == co.type["Product"] and e["to_pipeline_id"] == co.type["Engineering"]
    )
    truth = co.count(
        "stage_visits",
        "pipeline_id=? AND direction='handoff' AND entered_at>=?",
        co.type["Engineering"],
        stamp(7 * 24 * 60),
    )
    assert edge["count"] == truth == 3
    eng = next(p for p in seen["pipelines"] if p["id"] == co.type["Engineering"])
    in_stage = co.count("tasks", "type_id=? AND status NOT IN ('done','closed','declined')", co.type["Engineering"])
    assert eng["cards"] == in_stage
    assert eng["overdue"] == 1
    assert eng["done"] == co.count("tasks", "type_id=? AND status='done'", co.type["Engineering"])
