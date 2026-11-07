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
    task_relations as TR,
)
from backend.config import Settings
from backend.store import Store

ANA, BEN, CAL = "human:ana", "human:ben", "human:cal"  # ana owns the pipelines; ben is her backup; cal is a bystander
PM, BUILDER = "bot:pipeline-manager", "bot:builder"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"

# The booted stage names (the preset's own names are overridden by the seed that a booted hub already has).
STAGES = {
    "Product": ["Ideas", "Specifying", "Review", "In development", "Validate", "Done", "Declined"],
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
        version = self.c.execute("SELECT version FROM stage_requirements WHERE id = ?", (requirement_id,)).fetchone()[0]
        result = pipeline_review.approve(self.c, actor, task_id, requirement_id, requirement_version=version)
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

    def revision(self, task_id):
        """The work version (tasks.work_revision): it goes up on every change of the work."""
        return self.c.execute("SELECT work_revision FROM tasks WHERE id=?", (task_id,)).fetchone()[0]

    def attach(self, task_id, req_id, by=BUILDER, digest="sha-1", url="https://example.test/shot.png"):
        self.insert(
            "requirement_attachments",
            id=f"att-{task_id[-6:]}-{req_id[-8:]}-{digest}",
            task_id=task_id,
            stage_requirement_id=req_id,
            source="link",
            url=url,
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

    def requirement(self, pipeline, stage, name):
        """The id of a live requirement on a stage (the preset seeds its own)."""
        found = self.c.execute(
            "SELECT id FROM stage_requirements WHERE pipeline_stage_id=? AND name=? AND retired_at IS NULL",
            (self.stage(pipeline, stage), name),
        ).fetchone()
        assert found is not None, f"{pipeline} {stage} has no requirement {name!r}"
        return found[0]

    def set_status(self, task_id, status, actor=ANA):
        row = H.task_update(self.c, actor, task_id, status=status)
        self.c.commit()
        return row

    def close(self, task_id, actor=BUILDER):
        """Close a task as its requester (a closed status is set by close, never by a status write)."""
        H.task_close(self.c, actor, task_id, note="Closed in test")
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
def hub(tmp_path):
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
    yield store, c
    c.close()


@pytest.fixture
def co(hub):
    store, c = hub
    yield Company(store, c)


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
SPEC = "https://tico.example/specs/dark-mode"
VALIDATION = "https://tico.example/pages/dark-mode-validation"


def subtasks(co, parent):
    """The Engineering subtasks a ticket has, oldest first."""
    return [
        r[0]
        for r in co.c.execute(
            "SELECT r.from_task FROM task_relations r JOIN tasks k ON k.id = r.from_task "
            "WHERE r.to_task = ? AND r.kind = 'parent' ORDER BY k.created_at, k.id",
            (parent,),
        )
    ]


def session_links(co, task_id):
    return co.c.execute(
        "SELECT id, url, detail FROM task_links WHERE task_id = ? AND kind = 'session' ORDER BY id", (task_id,)
    ).fetchall()


def primary_session(co, task_id):
    """The owning bot's primary task session, as its first turn on the task records it."""
    co.insert(
        "task_links",
        id=f"sess-{task_id[-8:]}",
        task_id=task_id,
        kind="session",
        url="session:builder/thread-1",
        title="builder",
        added_by=BUILDER,
        created_at=stamp(),
        detail=json.dumps({"primary": True, "bot": "builder", "harness": "claude"}),
        updated_at=stamp(),
    )
    co.c.commit()


def owner_and_session(co, card):
    """What must not change across the stages: the owner, the primary session link and the session root."""
    return (H.task(co.c, card)["owner_id"], session_links(co, card), H.session_root(co.c, "builder", card))


def review_card(co, title="Dark mode"):
    """A Product card in Review with its spec page attached, waiting for the person's approval."""
    card = co.ticket("Product", "Review", title)
    co.attach(card, co.requirement("Product", "Review", "Spec approved"), url=SPEC)
    return card


def approve_spec(co, card):
    """The person approves the spec. Approving the last requirement of Review moves the card on, so this files the
    Engineering subtask too."""
    co.approve(card, co.requirement("Product", "Review", "Spec approved"))


def test_an_approved_spec_files_one_engineering_subtask_with_its_spec_and_moves_on(co):
    """Plan M: P12 is approved in Review and moves to In development. The Product to Engineering spawn files D41 as a
    subtask of P12, in Engineering's first stage, requested by P12's owner, with the spec page carried over and one
    handoff row. P12 keeps its owner and its primary session."""
    card = review_card(co)
    primary_session(co, card)
    before = owner_and_session(co, card)
    approve_spec(co, card)
    assert co.where(card) == ("Product", "In development")
    (d41,) = subtasks(co, card)
    assert co.where(d41) == ("Engineering", "Todo")
    prefix = co.c.execute("SELECT prefix FROM task_types WHERE id = ?", (co.type["Engineering"],)).fetchone()[0]
    assert prefix == "D"
    assert H.task(co.c, d41)["requester_id"] == BUILDER
    assert co.count("task_links", "task_id = ? AND url = ?", d41, SPEC) == 1
    assert co.count("pipeline_handoffs", "source_task_id = ? AND target_task_id = ?", card, d41) == 1
    assert owner_and_session(co, card) == before


def test_the_subtask_tree_names_each_ticket_as_its_pipeline_does(co):
    """The Subtasks rail reads the tree: D41 is named by its pipeline's prefix and number, with its stage, and its parent
    is the Product card P12."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.c.execute("UPDATE tasks SET number = 41 WHERE id = ?", (d41,))  # a fresh hub numbers it D2; the plan says D41
    co.c.commit()
    (node,) = H.task_tree(co.c, card)
    assert (node["label"], node["step"]) == ("D41", "Todo")
    assert TR.parent_of(co.c, d41) == card


def test_the_full_loop_validates_and_is_done_when_both_subtasks_ship(co, monkeypatch):
    """P12 goes In development with D41. D42 is added by hand. Both ship (done), so P12 moves to Validate by its own
    wake, in its own session. A validation page the bot and then the person approve takes it to Done. The owner and
    the primary session do not change at any stage, and the wake that reaches Validate targets P12 for the same bot."""
    wakes = []
    real_wake = H._wake

    def spy(conn, task_row, target, body, refs=None, **kw):
        wakes.append((str(task_row["id"]), target, body))
        return real_wake(conn, task_row, target, body, refs=refs, **kw)

    monkeypatch.setattr(H, "_wake", spy)
    card = review_card(co)
    primary_session(co, card)
    at_review = owner_and_session(co, card)
    approve_spec(co, card)
    assert owner_and_session(co, card) == at_review  # at Specifying-or-later the owner and session are unchanged
    d41 = subtasks(co, card)[0]
    d42 = H.task_create(
        co.c,
        ANA,
        "Dark mode, part two",
        "",
        BUILDER,
        type=co.type["Engineering"],
        step=co.stage("Engineering", "Todo"),
        parent_id=card,
        lint=False,
    )["id"]
    co.c.commit()
    assert subtasks(co, card) == [d41, d42]

    co.move(d41, "Doing", actor=ANA)
    co.set_status(d41, "done")
    assert co.where(card) == ("Product", "In development")  # d42 is still open
    assert co.dots(card)["Engineering shipped"]["state"] == "waiting"

    wakes.clear()
    co.set_status(d42, "done")
    assert co.where(card) == ("Product", "Validate")
    reached = [w for w in wakes if w[2].startswith("All subtasks done")]
    assert reached and all(task == card and target == BUILDER for task, target, _ in reached)
    assert not [w for w in wakes if w[0] in (d41, d42) and w[1] == BUILDER]  # no child-row wake reaches the bot
    assert H.session_root(co.c, "builder", card) == card
    assert owner_and_session(co, card) == at_review

    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)
    co.decide(card, checked, verdict="unsure", judge="llm", actor=PM)  # the bot flags the page for a person
    co.approve(card, checked)
    assert co.where(card) == ("Product", "Done")
    assert owner_and_session(co, card) == at_review


def test_leaving_review_twice_files_one_subtask_until_the_handoff_is_returned(co):
    """The handoff key is the card and the link: while its handoff is live a second leave files nothing. An undone
    (returned) handoff allows a new spawn."""
    card = review_card(co)
    approve_spec(co, card)
    link = pipeline_links.link_for_stage(co.c, co.stage("Product", "Review"))
    assert pipeline_links.spawn(co.c, link, card) is None
    assert len(subtasks(co, card)) == 1
    (d41,) = subtasks(co, card)
    co.close(d41)  # an open ticket with the same title is a duplicate the rules refuse; a closed one is not
    co.c.execute("UPDATE pipeline_handoffs SET returned_at = ? WHERE source_task_id = ?", (stamp(), card))
    co.c.commit()
    assert pipeline_links.spawn(co.c, link, card) is not None
    assert len(subtasks(co, card)) == 2
    assert pipeline_links.spawn(co.c, link, card) is None  # the new handoff is live again


def test_not_validated_files_a_follow_up_and_returns_the_card_to_development(co):
    """Validate's "Not validated" (a person's call): P12 goes back to In development, one follow-up Engineering ticket is
    filed under it with "Follow-up: <reason>" in its body, and that stage waits on the new ticket until it ships. Only a
    person can make the call, and it is its own handoff."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.set_status(d41, "done")
    assert co.where(card) == ("Product", "Validate")
    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)

    with pytest.raises(pipeline_review.Declined):
        pipeline_review.not_validated(co.c, PM, card, checked, "The measures did not hold")  # a bot cannot make it
    co.c.rollback()

    result = pipeline_review.not_validated(co.c, ANA, card, checked, "Load time stayed over 4 s")
    co.c.commit()
    assert result["verdict"] == "failed" and result["moved_back_to"] == "In development"
    assert co.where(card) == ("Product", "In development")
    d43 = subtasks(co, card)[-1]
    assert d43 != d41 and co.where(d43) == ("Engineering", "Todo")
    assert H.task(co.c, d43)["body"].startswith("Follow-up: Load time stayed over 4 s. ")
    assert co.count("pipeline_handoffs", "source_task_id = ? AND target_task_id = ?", card, d43) == 1
    assert co.count("stage_decisions", "task_id = ? AND verdict = 'failed'", card) == 1
    assert co.dots(card)["Engineering shipped"]["state"] == "waiting"  # D43 is open: not met yet

    co.move(d43, "Doing", actor=ANA)
    co.set_status(d43, "done")
    assert co.where(card) == ("Product", "Validate")


def test_closing_a_product_card_closes_its_open_subtasks_and_keeps_the_shipped_ones(co):
    """The generic close cascade (hubdb._finish_children) reaches the spawned subtasks: the open one closes with the card,
    the one already done stays done."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    d42 = H.task_create(
        co.c,
        ANA,
        "Dark mode, part two",
        "",
        BUILDER,
        type=co.type["Engineering"],
        step=co.stage("Engineering", "Todo"),
        parent_id=card,
        lint=False,
    )["id"]
    co.c.commit()
    co.move(d41, "Doing", actor=ANA)
    co.set_status(d41, "done")
    co.close(card, actor=ANA)
    assert H.task(co.c, card)["status"] == "closed"
    assert H.task(co.c, d41)["status"] == "done"
    assert H.task(co.c, d42)["status"] == "closed"
    assert H.task(co.c, d42)["note"] == f"Closed with #{H.task(co.c, card)['number']}"  # the generic cascade's note


def test_subtasks_all_closed_with_none_shipped_does_not_pass(co):
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.close(d41)
    assert co.where(card) == ("Product", "In development")
    assert co.dots(card)["Engineering shipped"]["state"] == "failed"
    assert co.advance(card, actor=ANA)["moved"] is False


def test_a_subtask_starting_work_does_not_move_its_product_card(co):
    """The legacy roadmap sync (roadmap_flow.sync) never moves a card in a pipeline with requirements."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    assert co.where(card) == ("Product", "In development")


def test_an_engineering_ticket_with_no_product_parent_ships_as_it_does_today(co):
    products = co.count("tasks", "type_id = ?", co.type["Product"])
    ticket = co.ticket("Engineering", "Todo", "Hotfix login")
    for stage in ("Doing", "In review", "Ready to ship"):
        co.move(ticket, stage, actor=ANA)
    co.set_status(ticket, "done")
    co.move(ticket, "Done", actor=ANA)
    assert co.where(ticket) == ("Engineering", "Done")
    assert co.count("tasks", "type_id = ?", co.type["Product"]) == products
    assert co.count("pipeline_handoffs", "source_task_id = ?", ticket) == 0
    assert H.TR.parent_of(co.c, ticket) is None


def test_setup_moves_a_card_out_of_the_old_approved_stage_into_in_development(hub):
    """A hub that booted the roadmap seed has an Approved stage with cards in it. Setup retires that stage into In
    development and keeps the card."""
    store, c = hub
    product = c.execute("SELECT id FROM task_types WHERE role = 'product-roadmap'").fetchone()[0]
    approved = c.execute("SELECT id FROM task_steps WHERE type_id = ? AND name = 'Approved'", (product,)).fetchone()[0]
    card = H.task_create(c, ANA, "Old card", "", BUILDER, type=product, step=approved, lint=False)["id"]
    c.commit()
    pipeline_setup.apply_factory_setup(c, ANA)
    c.commit()
    names = {r[0] for r in c.execute("SELECT name FROM task_steps WHERE type_id = ?", (product,))}
    assert "Approved" not in names and "In development" in names
    step = c.execute(
        "SELECT s.name FROM tasks k JOIN task_steps s ON s.id = k.step_id WHERE k.id = ?", (card,)
    ).fetchone()
    assert step[0] == "In development"


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
    assert co.where(task) == ("Product", "Ideas")
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
    """The real path: the queue's webhooks mark the removal for checking, the maintenance pass reads the pull request on
    GitHub, and a removal that was not a merge ejects the card a stage back with the reason."""
    from types import SimpleNamespace

    from backend import github as G

    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")  # the pipeline has requirements
    task = co.ticket("Engineering", "Doing", "Merge me")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    co.move(task, "In review", actor=ANA)
    url = "https://github.com/acme/app/pull/7"
    co.insert("repositories", id="repo-acme-app", full_name="acme/app", enabled=1, reachable=1)
    co.insert(
        "task_links",
        id="lnk-pr-1",
        task_id=task,
        kind="pr",
        url=url,
        repo="acme/app",
        number=7,
        state="open",
        added_by=BUILDER,
        created_at=stamp(),
        detail=json.dumps({"head_sha": "abc123"}),
    )
    co.c.commit()
    pr = {"html_url": url, "number": 7, "state": "open", "merged": False, "head": {"sha": "abc123"}}
    G.pull_request(co.c, {"action": "enqueued", "pull_request": pr})
    G.pull_request(co.c, {"action": "dequeued", "reason": "checks failed", "pull_request": pr})
    co.c.commit()
    assert co.where(task) == ("Engineering", "In review")  # the webhook sends nothing for a removal that is not a merge
    assert (
        H.task_links(co.c, task)[0]["detail"]
        and "verify" in json.loads(H.task_links(co.c, task)[0]["detail"])["merge_queue"]
    )

    reads = []

    class GitHub:
        """The maintenance pass's service: the pull request is still open at the removed head."""

        store = co.store

        def row(self, c=None):
            return {"org": "acme"}

        def mint(self, repos, permissions, **kw):
            return "token", None

        def _call(self, method, path, headers=None, **kw):
            reads.append((method, path))
            return SimpleNamespace(
                status_code=200, json=lambda: {"state": "open", "merged": False, "head": {"sha": "abc123"}}
            )

    G.refresh_queue_removals(GitHub())
    assert reads == [("GET", "/repos/acme/app/pulls/7")]
    assert co.where(task) == ("Engineering", "Doing")
    notes = [
        row[0]
        for row in co.c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='queue_ejected'",
            (task,),
        )
    ]
    assert notes == ["checks failed"]
    G.refresh_queue_removals(GitHub())
    assert len(reads) == 1  # the removal was settled: nothing is read again


def test_a_pr_head_that_moves_is_new_work_that_makes_evidence_out_of_date_and_refuses_the_move(co):
    """The real path: a linked pull request's head moves by webhook (synchronize). The new head is new work (the work
    version goes up once, a redelivery of the same head does nothing), the met evidence goes out of date, and the card
    will not move on until the requirement is checked again."""
    from backend import github as G

    task = co.ticket("Engineering", "Doing", "Push me")
    url = "https://github.com/acme/app/pull/8"
    co.insert(
        "task_links",
        id="lnk-pr-8",
        task_id=task,
        kind="pr",
        url=url,
        repo="acme/app",
        number=8,
        state="open",
        added_by=BUILDER,
        created_at=stamp(),
        detail=json.dumps({"head_sha": "abc123"}),
    )
    shots = co.require("Engineering", "Doing", "Screenshots", reviewed_by="bot")
    co.attach(task, shots)
    co.decide(task, shots, judge="llm", actor=PM)
    assert co.dots(task)["Screenshots"]["state"] == "met"
    before = co.revision(task)

    moved = {"html_url": url, "number": 8, "state": "open", "merged": False, "head": {"sha": "def456"}}
    G.pull_request(co.c, {"action": "synchronize", "pull_request": moved, "sender": {"login": "builder-bot"}})
    co.c.commit()
    after = co.revision(task)
    assert after == before + 1
    assert json.loads(H.task_links(co.c, task)[0]["detail"])["head_sha"] == "def456"
    assert co.dots(task)["Screenshots"]["state"] == "out_of_date"
    assert co.refused(task, "In review")

    G.pull_request(
        co.c, {"action": "synchronize", "pull_request": moved, "sender": {"login": "builder-bot"}}
    )  # redelivery
    co.c.commit()
    assert co.revision(task) == after


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
        assert all(co.type["Legal"] not in (e["from"], e["to"]) for e in seen["edges"]), (
            "the flow map draws no hidden stage"
        )
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


NOTES = "https://docs.example.test/dark-mode-notes"


class _Guard:
    """The Auth the attachment API takes for reads of the ticket; the test's ticket is readable to its owner."""

    def task(self, conn, who, task_id):
        return None


def test_the_spawned_ticket_carries_the_spec_page_first_and_then_the_ordinary_links(co):
    """Fix V-5: the spec is attached through the real attachment API on a requirement named "Spec page", and an ordinary
    link is added to the card. Approving Review files D41 with both as its links, the spec first, so both show on D41."""
    from types import SimpleNamespace

    from backend import requirement_attachments

    card = co.ticket("Product", "Specifying", "Dark mode")
    spec_req = co.require("Product", "Specifying", "Spec page", needs={"formats": ["link"]})
    requirement_attachments.attach(
        co.c, _Guard(), SimpleNamespace(actor=BUILDER), card, spec_req, {"source": "link", "url": SPEC}
    )
    H.task_link(co.c, BUILDER, card, NOTES)
    co.c.commit()
    co.move(card, "Review")
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    assert [link["url"] for link in H.task_links(co.c, d41)] == [SPEC, NOTES]


def test_a_card_whose_spawn_the_rules_refuse_stays_in_review_and_files_nothing(co):
    """Fix V-8: the source owner is quarantined, so the Engineering ticket cannot be filed. The approval is refused with
    409 spawn_refused, the card stays in Review, no child and no handoff are written, and no approval is kept."""
    from backend.store import Problem

    card = review_card(co)
    co.c.execute("UPDATE bots SET state='quarantined' WHERE slug='builder'")
    co.c.commit()
    with pytest.raises(Problem) as refused_move:
        approve_spec(co, card)
    assert (refused_move.value.code, refused_move.value.status) == ("spawn_refused", 409)
    assert co.where(card) == ("Product", "Review")
    assert subtasks(co, card) == []
    assert co.count("pipeline_handoffs", "source_task_id = ?", card) == 0
    assert co.count("stage_decisions", "task_id = ? AND verdict = 'met'", card) == 0


def test_the_spawned_ticket_is_owned_as_the_engineering_intake_pool_assigns_it(co):
    """Fix V-11: Engineering's first stage has an assign pool naming the bot `eng`. D41 is owned by `eng`, as a new ticket
    filed at that stage is, and its requester stays the Product card's owner."""
    H.sync_registry(co.c, {"eng": {}}, None)
    co.c.execute(
        "UPDATE task_steps SET flow = ? WHERE id = ?",
        (json.dumps({"assign": {"pool": ["eng"], "slots": 4}}), co.stage("Engineering", "Todo")),
    )
    co.c.commit()
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    assert H.task(co.c, d41)["owner_id"] == "bot:eng"
    assert H.task(co.c, d41)["requester_id"] == BUILDER


def test_undoing_an_approval_closes_its_untouched_follow_up_and_a_picked_up_one_refuses(co):
    """O1: the approval filed the Engineering subtask. Undone while nobody has picked it up, the subtask closes and its
    handoff is returned; a new approval files a new one. Once it is picked up, the undo is refused and nothing changes."""
    card = review_card(co)
    approved = co.approve(card, co.requirement("Product", "Review", "Spec approved"))
    (d41,) = subtasks(co, card)
    pipeline_review.undo(co.c, ANA, approved["decision_id"])
    co.c.commit()
    assert co.where(card) == ("Product", "Review")
    assert co.c.execute("SELECT status FROM tasks WHERE id=?", (d41,)).fetchone()[0] == "closed"
    assert co.count("pipeline_handoffs", "target_task_id=? AND returned_at IS NOT NULL", d41) == 1
    again = co.approve(card, co.requirement("Product", "Review", "Spec approved"))
    assert again["advanced"] is True
    open_now = [t for t in subtasks(co, card) if t != d41]
    assert len(open_now) == 1 and co.where(open_now[0]) == ("Engineering", "Todo")

    picked = review_card(co, title="Light mode")
    kept = co.approve(picked, co.requirement("Product", "Review", "Spec approved"))
    (d_picked,) = [t for t in subtasks(co, picked)]
    co.move(d_picked, "Doing", actor=ANA)
    with pytest.raises(pipeline_review.Declined) as caught:
        pipeline_review.undo(co.c, ANA, kept["decision_id"])
    co.c.rollback()
    assert caught.value.code == "already_started"
    assert co.where(picked) == ("Product", "In development")
    assert co.c.execute("SELECT status FROM tasks WHERE id=?", (d_picked,)).fetchone()[0] != "closed"


def test_undo_refuses_a_not_validated_decision(co):
    """O2: "Not validated" filed a follow-up and returned the card. It is not undone with the approval's undo; the person
    closes that follow-up instead."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.set_status(d41, "done")
    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)
    result = pipeline_review.not_validated(co.c, ANA, card, checked, "Load time stayed over 4 s")
    co.c.commit()
    with pytest.raises(pipeline_review.Declined) as caught:
        pipeline_review.undo(co.c, ANA, result["decision_id"])
    co.c.rollback()
    assert caught.value.code == "not_undoable"
    assert co.where(card) == ("Product", "In development")


def test_undo_closes_an_untouched_subtask_past_an_unmet_requirement_on_its_stage(co):
    """O3 (Codex): a move into a later Closed stage is cancellation, not forward progress. The subtask the approval filed
    is still at Todo, which has an unmet due-date requirement. The undo closes it anyway, and the card returns to Review."""
    co.require("Engineering", "Todo", "Due", kind="field", needs={"field": "due"})
    card = review_card(co)
    approved = co.approve(card, co.requirement("Product", "Review", "Spec approved"))
    (d41,) = subtasks(co, card)
    pipeline_review.undo(co.c, ANA, approved["decision_id"])
    co.c.commit()
    assert co.c.execute("SELECT status FROM tasks WHERE id=?", (d41,)).fetchone()[0] == "closed"
    assert co.where(card) == ("Product", "Review")


def test_an_automatic_move_the_rules_refuse_is_logged_and_the_rest_of_the_write_stands(co):
    """Y1: an automatic step write refused by a stage requirement is undone alone. The card gets an auto_move_refused
    event with the reason, and a change made in the same transaction stands."""
    co.require("Engineering", "Todo", "Due", kind="field", needs={"field": "due"})
    task = co.ticket("Engineering", "Todo", "Dark mode")
    row = H.task(co.c, task)
    co.c.execute("UPDATE tasks SET note=? WHERE id=?", ("kept", task))
    assert H.try_auto_move(co.c, H.KEEPER, row, step=co.stage("Engineering", "Doing")) is False
    co.c.commit()
    assert co.where(task) == ("Engineering", "Todo")
    assert co.c.execute("SELECT note FROM tasks WHERE id=?", (task,)).fetchone()[0] == "kept"
    assert co.count("history_events", "subject_type='task' AND subject_id=? AND kind='auto_move_refused'", task) == 1
