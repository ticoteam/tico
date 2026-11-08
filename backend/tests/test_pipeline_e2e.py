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
    pipeline_rights,
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
        """A ticket at `stage`. The app makes a ticket at its pipeline's first stage and then moves it, so a Product ticket
        past Specifying is made at Ideas, moved to Specifying and given its Spec page (`spec`) before it moves on."""
        past_spec = pipeline == "Product" and stage not in ("Ideas", "Specifying")
        row = H.task_create(
            self.c, ANA, title, "", BUILDER, type=self.type[pipeline], step=None if past_spec else stage, lint=False
        )
        self.c.commit()
        if past_spec:
            self.move(row["id"], "Specifying", actor=ANA)
            self.spec(row["id"])
            self.move(row["id"], stage, actor=ANA)
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

    def spec(self, task_id):
        """The Spec page of a ticket at Specifying is met, so the ticket may leave Specifying. The owner marks it met; the
        bot's review and a person's are the production path, and a scenario about them does its own."""
        self.mark_met(
            ANA, task_id, self.requirement("Product", "Specifying", "Spec page"), "Spec page written in the test"
        )

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

    def create_spawn_link(self, pipeline, stage, to):
        """A spawn link from a stage to another pipeline, made by the real helper."""
        pipeline_links.create_link(self.c, ANA, self.stage(pipeline, stage), self.type[to], "spawn")
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


def tico_spec_page(co, card):
    """A Tico page on the card, written by a spec bot: its file, one version and its page row, so the file satisfies a
    page format. Returns the file id."""
    co.c.execute(
        "INSERT INTO bots(id,slug,display_name,created_at) VALUES('bot-spec','spec','Spec writer',?)", (H.now(),)
    )
    co.c.execute(
        "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,current_version,first_activity_at,"
        "last_activity_at) VALUES('file-dark-spec','bot-spec',?,'spec','Dark mode spec','file','text/html',"
        "'tico_blob',1,?,?)",
        ("task:" + card, H.now(), H.now()),
    )
    co.c.execute(
        "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
        "VALUES('blob-dark-spec',?,'sha-spec',10,'dark-spec.html','text/html',?)",
        (ANA, H.now()),
    )
    co.c.execute(
        "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
        "VALUES('file-dark-spec',1,'blob-dark-spec','sha-spec',10,'dark-spec.html','text/html',?,?)",
        (ANA, H.now()),
    )
    co.c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES('file-dark-spec',?,?)", (H.now(), H.now()))
    return "file-dark-spec"


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
    """Finding R5: a subtask closed without finishing is cancelled, not shipped. A Close lands it in Engineering's Done
    column (status closed), and that must not meet "Engineering shipped" or move the card to Validate. A decline is
    the same: with nothing shipped the card does not pass."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.close(d41, actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert co.where(d41) == ("Engineering", "Done")
    assert co.where(card) == ("Product", "In development")
    assert co.dots(card)["Engineering shipped"]["state"] == "failed"
    assert co.advance(card, actor=ANA)["moved"] is False

    other = review_card(co, "Dark mode, second try")
    approve_spec(co, other)
    (d44,) = subtasks(co, other)
    co.set_status(d44, "declined")
    assert co.where(other) == ("Product", "In development")
    assert co.dots(other)["Engineering shipped"]["state"] == "failed"
    assert co.advance(other, actor=ANA)["moved"] is False


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
    # The manager was set a day ago (the watchdog judges passes from the assignment, F11).
    co.c.execute("UPDATE task_types SET manager_since=? WHERE id=?", (stamp(24 * 60), co.type["Engineering"]))
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
    # The same webhook write records the new head's check from the engine's standing (not met) itself, not a later pass.
    check = co.c.execute(
        "SELECT state FROM pipeline_check_runs WHERE task_id=? AND stage_requirement_id=? AND head_sha=?",
        (task, shots, "def456"),
    ).fetchone()
    assert check is not None and check[0] == "waiting"  # stored as waiting: not met, no failure
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

    def identity_for_actor(self, conn, actor):
        """The identity a reader of `actor`'s name reads as: a bot reviewer, who may read the ticket's files."""
        from types import SimpleNamespace

        return SimpleNamespace(actor=actor, role="bot", task_actor=None)


def test_the_spawned_ticket_carries_the_spec_page_first_and_then_the_ordinary_links(co, monkeypatch):
    """Fix V-5: the spec is a Tico page attached through the real attachment API on the Specifying stage's "Spec page", and
    an ordinary link is added to the card. Approving Review files D41 with both as its links, the spec first."""
    from types import SimpleNamespace

    from backend import requirement_attachments

    # A page's link needs the hub's origin.
    monkeypatch.setattr(pipeline_links, "hub_origin", "https://hub.example.test")
    card = co.ticket("Product", "Specifying", "Dark mode")
    file_id = tico_spec_page(co, card)
    requirement_attachments.attach(
        co.c,
        _Guard(),
        SimpleNamespace(actor=ANA, role="human", task_actor=None),
        card,
        co.requirement("Product", "Specifying", "Spec page"),
        {"source": "file", "file_id": file_id},
    )
    H.task_link(co.c, BUILDER, card, NOTES)
    co.c.commit()
    co.spec(card)
    co.move(card, "Review")
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    spec_url = f"https://hub.example.test/api/v2/files/{file_id}/versions/1"
    assert [link["url"] for link in H.task_links(co.c, d41)] == [spec_url, NOTES]


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


def test_the_spawned_ticket_carries_a_tico_page_attached_to_the_spec_first(co, monkeypatch):
    """Fix V-5 (page): a spec is usually a Tico page, a company file with a pages row. Attached through the real attach API
    to Product Review's "Spec" requirement, it is carried to the Engineering ticket as a link to its version, first, ahead
    of the approval's own evidence and the ordinary links. The address is absolute on the hub's public origin."""
    from types import SimpleNamespace

    from backend import requirement_attachments

    monkeypatch.setattr(pipeline_links, "hub_origin", "https://hub.example.test")
    card = co.ticket("Product", "Review", "Dark mode")
    file_id = tico_spec_page(co, card)
    spec_req = co.require("Product", "Review", "Spec", needs={"formats": ["page"]}, reviewed_by="person")
    requirement_attachments.attach(
        co.c,
        _Guard(),
        SimpleNamespace(actor=ANA, role="human", task_actor=None),
        card,
        spec_req,
        {"source": "file", "file_id": file_id},
    )
    H.task_link(co.c, BUILDER, card, NOTES)
    co.attach(card, co.requirement("Product", "Review", "Spec approved"), url=SPEC)
    co.c.commit()
    co.approve(card, spec_req)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    links = [link["url"] for link in H.task_links(co.c, d41)]
    assert links[0] == f"https://hub.example.test/api/v2/files/{file_id}/versions/1"
    assert NOTES in links


# ----------------------------------------------------------------------------- finding 6: shipping is a visit, not a status
def standing_names(co, card, stage="In development"):
    """{requirement name: state} for a card read as if it sat at `stage` (a closed card has no dots, so this reads the
    engine's standings directly)."""
    row = {"id": card, "type_id": co.type["Product"], "step_id": co.stage("Product", stage), "status": "doing"}
    return {s.requirement["name"]: s.state for s in pipeline_engine.standings(co.c, [row]).get(card, [])}


def test_a_subtask_moved_into_the_engineering_done_stage_ships_its_product_card(co):
    """Finding 6: the booted Engineering Done stage has status closed. A real move there is shipped, so Product leaves In
    development for Validate without a status "done" write."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert co.where(card) == ("Product", "Validate")


def test_a_step_move_into_engineering_done_is_held_to_a_waiting_approval(co):
    """Finding F1: Engineering's Done stage has status closed, but a move into it is progress, not a cancellation. A named
    person's approval waiting in Doing refuses the move, the subtask stays in Doing and the card does not advance."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.require("Engineering", "Doing", "Ana signs off", kind="approval", reviewed_by="person")
    co.move(d41, "Doing", actor=ANA)
    with pytest.raises(H.Refused):
        co.move(d41, "Done", actor=ANA)
    co.c.rollback()
    assert co.where(d41) == ("Engineering", "Doing")
    assert co.where(card) == ("Product", "In development")


def test_a_status_done_on_an_engineering_subtask_is_held_to_a_waiting_approval(co):
    """Finding B1: a status-only completion has no step of its own (booted Engineering's Done is closed, so `status=done`
    resolves to none), and it is progress all the same. The waiting approval in Doing refuses it, the card does not
    advance, and it goes through once the person has approved."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    signed = co.require("Engineering", "Doing", "Ana signs off", kind="approval", reviewed_by="person")
    co.move(d41, "Doing", actor=ANA)
    with pytest.raises(H.Refused):
        co.set_status(d41, "done", actor=ANA)
    co.c.rollback()
    assert co.where(d41) == ("Engineering", "Doing")
    assert co.where(card) == ("Product", "In development")
    co.approve(d41, signed)
    co.set_status(d41, "done", actor=ANA)
    assert H.task(co.c, d41)["status"] == "done"
    assert co.where(card) == ("Product", "Validate")


def test_closing_into_done_then_marking_it_done_is_still_held_to_the_waiting_approval(co):
    """Codex r5 blocker 1: a Close puts the subtask in Engineering's Done without its approval (a cancellation), and a
    `status=done` there changed no stage, so neither gate ran: it shipped and Product reached Validate. Marking it done
    in place is held to its stages' requirements like any finish."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    signed = co.require("Engineering", "Todo", "Ana signs off", kind="approval", reviewed_by="person")
    co.close(d41, actor=ANA)
    assert co.where(d41) == ("Engineering", "Done")
    with pytest.raises(H.Refused):
        co.set_status(d41, "done", actor=ANA)
    co.c.rollback()
    assert H.task(co.c, d41)["status"] == "closed"
    assert co.where(card) == ("Product", "In development")
    co.move(d41, "Todo", actor=ANA)  # reopened where its approval waits
    co.approve(d41, signed)
    co.set_status(d41, "done", actor=ANA)
    assert H.task(co.c, d41)["status"] == "done"
    assert co.where(card) == ("Product", "Validate")


def test_a_status_done_write_with_no_approval_waiting_still_finishes_the_subtask(co):
    """Finding B1, the other side: with nothing waiting, a status-only completion is progress that the gate lets through,
    and it ships the subtask."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.set_status(d41, "done", actor=ANA)
    assert H.task(co.c, d41)["status"] == "done"
    assert co.where(card) == ("Product", "Validate")


def test_a_person_approved_due_date_stops_standing_once_the_date_is_cleared(co):
    """Finding R6: Ben approves a populated due-date requirement, Ana clears the date, and the requirement is no longer met.
    The card does not move on with the spec approval, and the due-date dot shows the requirement as not met. The person's
    approval stood only while the date did."""
    due = co.require(
        "Product", "Review", "Due date", kind="field", reviewed_by="person", reviewer_id=BEN, needs={"field": "due"}
    )
    card = review_card(co)
    H.task_update(co.c, ANA, card, due="2026-11-20T17:00:00.000000Z")
    co.c.commit()
    co.approve(card, due, actor=BEN)
    H.task_update(co.c, ANA, card, due="")
    co.c.commit()
    approve_spec(co, card)
    assert co.where(card) == ("Product", "Review")
    assert co.dots(card)["Due date"]["state"] != "met"


def test_a_subtask_that_shipped_then_closed_still_counts_as_shipped(co):
    """Finding R5: a subtask marked done ships the card, and closing it afterwards does not take the shipment back. The
    done history is the evidence, so the requirement stays met on the card."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.set_status(d41, "done")
    assert co.where(card) == ("Product", "Validate")
    co.close(d41, actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert standing_names(co, card)["Engineering shipped"] == "met"


def test_a_closed_subtask_reopened_and_finished_ships_the_card(co):
    """Finding F6: shipment is judged per transition. The subtask's Close into Done is a cancellation; reopened and genuinely
    moved into Done, it finished, so Engineering shipped is met."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    # A requirement on Engineering's first stage makes its moves write stage visits, as a real pipeline's do.
    signed = co.require("Engineering", "Todo", "Ana signs off", kind="approval", reviewed_by="person")
    co.approve(d41, signed)
    co.close(d41, actor=ANA)
    assert standing_names(co, card)["Engineering shipped"] == "failed"
    co.set_status(d41, "doing", actor=ANA)  # reopened
    co.move(d41, "Done", actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert co.where(card) == ("Product", "Validate")
    assert standing_names(co, card)["Engineering shipped"] == "met"


def test_a_close_into_done_with_no_marker_from_before_the_update_does_not_ship(co):
    """Finding F6: a Close written before the stage_closed marker existed leaves no marker in history, and a pipeline with
    no requirements writes no visit. The subtask sits in Done, so it must not count as shipped."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.close(d41, actor=ANA)
    co.c.execute("DELETE FROM history_events WHERE subject_id = ? AND kind = 'stage_closed'", (d41,))
    co.c.commit()
    assert co.where(d41) == ("Engineering", "Done")
    assert standing_names(co, card)["Engineering shipped"] == "failed"
    assert co.advance(card, actor=ANA)["moved"] is False


def test_a_closed_subtask_with_its_close_marker_removed_still_does_not_ship(co):
    """Finding B4: shipment is per transition, and a visit is not a finish. The Close of an unfinished subtask into Done
    writes a visit (this pipeline has requirements) and a `stage_closed` marker. With the marker gone the visit alone
    must not ship the card: the markerless fallback is dropped."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.require("Engineering", "Todo", "Ana signs off", kind="approval", reviewed_by="person")
    co.close(d41, actor=ANA)
    co.c.execute("DELETE FROM history_events WHERE subject_id = ? AND kind = 'stage_closed'", (d41,))
    co.c.commit()
    assert co.where(d41) == ("Engineering", "Done")
    assert standing_names(co, card)["Engineering shipped"] == "failed"
    assert pipeline_engine.shipped(co.c, [d41]) == set()


def test_a_shipment_reports_the_moment_it_first_shipped(co):
    """Finding B4: shipped_at gives each shipped ticket the moment of its first completion, the finish marker's time, and
    a ticket that only ever closed has none."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    finished = co.c.execute(
        "SELECT created_at FROM history_events WHERE subject_id = ? AND kind = 'stage_finished'", (d41,)
    ).fetchone()[0]
    assert pipeline_engine.shipped_at(co.c, [d41]) == {d41: finished}


def test_a_subtask_closed_in_done_after_it_shipped_still_counts_as_shipped(co):
    """Finding F6 (the decision in pipeline_engine._shipped_ids): a ticket that finished and reached Done keeps its
    shipment when someone later closes it in place. The finish is a marker, and a close in place writes nothing to take it back."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    assert co.where(card) == ("Product", "Validate")
    co.close(d41, actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert pipeline_engine.shipped(co.c, [d41]) == {d41}


def test_a_spawn_does_not_carry_a_members_only_source_to_a_non_member_owner(co):
    """Finding R1 (D67): a Legal card (members-only) spawns an Engineering ticket owned by Cal, who is not a member of
    Legal. Cal's ticket gets no link to the confidential source, and the skip is logged on the child by requirement name
    only, never the URL. A child owned by a Legal member gets none either: its company-wide readers include Cal."""
    from backend import pipeline_links

    confidential = "https://counsel.example.test/memo"
    card = co.ticket("Legal", "Review", "NDA with Acme")
    H.task_link(co.c, ANA, card, confidential, "Counsel memo")
    child = H.task_create(
        co.c,
        ANA,
        "Build the NDA intake",
        "",
        CAL,
        type=co.type["Engineering"],
        step=co.stage("Engineering", "Todo"),
        parent_id=card,
        lint=False,
    )["id"]
    co.c.commit()
    pipeline_links._carry(co.c, BUILDER, card, child, "Review")
    co.c.commit()
    assert confidential not in [link["url"] for link in H.task_links(co.c, child)]
    skipped = co.c.execute(
        "SELECT new, note FROM history_events WHERE subject_id = ? AND kind = 'carry_skipped'", (child,)
    ).fetchall()
    assert (
        len(skipped) == 1
        and confidential not in str(tuple(skipped[0]))
        and "Counsel memo" not in str(tuple(skipped[0]))
    )

    mine = H.task_create(
        co.c,
        ANA,
        "Owned by the Legal owner",
        "",
        ANA,
        type=co.type["Engineering"],
        step=co.stage("Engineering", "Todo"),
        parent_id=card,
        lint=False,
    )["id"]
    co.c.commit()
    pipeline_links._carry(co.c, BUILDER, card, mine, "Review")
    co.c.commit()
    # Owned by a Legal member, but on the company-wide Engineering pipeline Cal reads it too: still nothing carried (F2/F3).
    assert confidential not in [link["url"] for link in H.task_links(co.c, mine)]


def test_a_subtask_closed_by_a_cascade_is_neither_shipped_nor_blocking(co):
    """Finding 6: a subtask closed with its card is gone. d41 shipped through its Done visit; d42 closed with the card.
    The shipped one meets the requirement and the cascade-closed one neither counts nor blocks."""
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
    co.move(d41, "Done", actor=ANA)  # shipped; d42 is still open, so the card waits in development
    assert co.where(card) == ("Product", "In development")
    co.close(card, actor=ANA)  # the cascade closes d42 with its card
    assert H.task(co.c, d42)["status"] == "closed"
    assert standing_names(co, card)["Engineering shipped"] == "met"


def test_a_subtask_that_only_closed_with_its_card_never_passes_the_card(co):
    """Finding 6: with nothing shipped and only a cascade-closed subtask, the card is not met (the PM is asked)."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.close(card, actor=ANA)
    assert H.task(co.c, d41)["status"] == "closed"
    assert standing_names(co, card)["Engineering shipped"] == "failed"


# ----------------------------------------------------------------------------- finding 7: decline cascades like close
def test_the_owner_can_decline_a_card_with_an_unmet_approval_without_an_override(co):
    """Finding R7: a cancellation is not progress, so the explicit move into Declined is not held to the stage's completion
    requirements (the same exemption the status write has). The owner declines the card in Review, where the spec approval
    is unmet, with no override. A bystander still cannot decline it."""
    card = review_card(co)
    declined = co.stage("Product", "Declined")
    with pytest.raises(H.Refused):
        H.task_update(co.c, CAL, card, step=declined, note="Dropped")  # not the owner: the decline is refused
    co.c.rollback()
    co.move(card, "Declined", actor=ANA, note="Dropped")
    assert co.where(card) == ("Product", "Declined")


def test_declining_a_product_card_closes_its_open_subtask_and_keeps_the_shipped_one(co):
    """Finding 7 (D66): an authorized move of a Product card into the Declined stage closes its open Engineering subtask
    with "Parent declined"; a subtask already done stays done. The editor passes the card's open requirements with an
    override, as the move gate asks."""
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
    co.move(d41, "Done", actor=ANA)  # shipped: the card moves to Validate
    co.move(card, "Declined", actor=ANA, override_requirements=True, note="Dropped")
    assert co.where(card) == ("Product", "Declined")
    assert H.task(co.c, d42)["status"] == "closed"
    assert H.task(co.c, d42)["note"] == "Parent declined"
    assert H.task(co.c, d41)["status"] == "closed"  # shipped stays shipped: it is in Done
    assert co.where(d41) == ("Engineering", "Done")


# ----------------------------------------------------------------------------- finding 13: a named reviewer can decide
def test_a_named_reviewer_who_is_not_an_editor_can_file_not_validated(co):
    """Finding 13 (D64/D66): Ben is Validate's named reviewer and not a pipeline editor. Not validated succeeds, the card
    goes back to In development, and the events name Ben as the actor."""
    ben_card = review_card(co)
    approve_spec(co, ben_card)
    (d41,) = subtasks(co, ben_card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    assert co.where(ben_card) == ("Product", "Validate")
    checked = co.requirement("Product", "Validate", "Validated")
    co.c.execute("UPDATE stage_requirements SET reviewer_id = ? WHERE id = ?", (BEN, checked))
    co.c.commit()
    assert not pipeline_rights.may_edit_rules(co.c, BEN, co.type["Product"])  # Ben is not an editor
    co.attach(ben_card, checked, url=VALIDATION)
    result = pipeline_review.not_validated(co.c, BEN, ben_card, checked, "Load time stayed over 4 s")
    co.c.commit()
    assert result["verdict"] == "failed"
    assert co.where(ben_card) == ("Product", "In development")
    actors = {
        r[0]
        for r in co.c.execute(
            "SELECT actor_id FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='step'",
            (ben_card,),
        )
    }
    assert BEN in actors


def test_a_person_who_is_neither_the_reviewer_nor_an_editor_cannot_file_not_validated(co):
    """Finding 13: Cal is neither the named reviewer nor an editor. Refused with 403, and nothing moves."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    checked = co.requirement("Product", "Validate", "Validated")
    co.c.execute("UPDATE stage_requirements SET reviewer_id = ? WHERE id = ?", (BEN, checked))
    co.c.commit()
    co.attach(card, checked, url=VALIDATION)
    with pytest.raises(pipeline_review.Declined) as caught:
        pipeline_review.not_validated(co.c, CAL, card, checked, "Load time stayed over 4 s")
    co.c.rollback()
    assert caught.value.status == 403
    assert co.where(card) == ("Product", "Validate")


# ----------------------------------------------------------------------------- ST10: the follow-up binds to the spawn that filed the subtasks
def test_not_validated_files_into_the_spawn_that_filed_the_subtasks_not_a_nearer_link(co):
    """ST10: a later In development -> Support spawn link is nearer Validate than the Review -> Engineering spawn that filed
    the card's subtasks. The follow-up still goes into Engineering, the link whose handoff the card's subtasks came from."""
    card = review_card(co)
    approve_spec(co, card)
    (d41,) = subtasks(co, card)
    co.move(d41, "Doing", actor=ANA)
    co.move(d41, "Done", actor=ANA)
    co.create_spawn_link("Product", "In development", "Success")
    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)
    result = pipeline_review.not_validated(co.c, ANA, card, checked, "Load time stayed over 4 s")
    co.c.commit()
    assert co.where(result["follow_up"]) == ("Engineering", "Todo")


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


def _card_with_marketing_and_engineering(co):
    """Codex's repro through real setup: Specifying spawns Marketing, the card moves to Review and Marketing's lead ships,
    then the person approves the spec, which spawns Engineering into In development. Returns (card, marketing lead,
    engineering subtask)."""
    card = co.ticket("Product", "Specifying", "Dark mode")
    co.create_spawn_link("Product", "Specifying", "Marketing")
    co.spec(card)
    co.move(card, "Review", actor=ANA)
    (lead,) = subtasks(co, card)
    assert co.where(lead)[0] == "Marketing"
    co.set_status(lead, "done")
    co.attach(card, co.requirement("Product", "Review", "Spec approved"), url=SPEC)
    approve_spec(co, card)
    assert co.where(card) == ("Product", "In development")
    (build,) = [kid for kid in subtasks(co, card) if co.where(kid)[0] == "Engineering"]
    return card, lead, build


def test_a_cancelled_engineering_subtask_does_not_meet_engineering_shipped_because_marketing_shipped(co):
    """Codex blocker 5: Marketing's shipped subtask must not stand in for the Engineering subtask the requirement counts. With
    Engineering cancelled, the card stays in development and does not reach Validate."""
    card, _, build = _card_with_marketing_and_engineering(co)
    co.close(build, actor=ANA)
    assert co.dots(card)["Engineering shipped"]["state"] == "failed"
    assert co.advance(card, actor=ANA)["moved"] is False
    assert co.where(card) == ("Product", "In development")


def test_not_validated_files_into_engineering_when_engineering_and_marketing_both_shipped(co):
    """Codex blocker 5, the other half: with Engineering shipped as well, Not validated files the follow-up into Engineering,
    the pipeline the requirement names, and not into Marketing, whose work also shipped through an earlier stage."""
    card, _, build = _card_with_marketing_and_engineering(co)
    co.move(build, "Doing", actor=ANA)
    co.move(build, "Done", actor=ANA)
    assert co.where(card) == ("Product", "Validate")
    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)
    result = pipeline_review.not_validated(co.c, ANA, card, checked, "Load time stayed over 4 s")
    co.c.commit()
    assert co.where(result["follow_up"]) == ("Engineering", "Todo")
    assert co.where(card) == ("Product", "In development")


def test_not_validated_files_one_engineering_ticket_when_specifying_and_review_both_spawned_shipped_engineering(co):
    """Codex r5 blocker 3: Specifying and Review each spawn Engineering work, and both ships. The subtasks run names
    Engineering, so every candidate link files into it and the destination is not ambiguous. Not validated files one
    Engineering ticket through the nearest link, Review's, and does not refuse with `development_link`."""
    card = co.ticket("Product", "Specifying", "Dark mode")
    co.create_spawn_link("Product", "Specifying", "Engineering")
    specifying_link = co.c.execute(
        "SELECT id FROM pipeline_links WHERE from_pipeline_stage_id=? AND mode='spawn'",
        (co.stage("Product", "Specifying"),),
    ).fetchone()[0]
    co.spec(card)
    co.move(card, "Review", actor=ANA)
    (lead,) = subtasks(co, card)
    H.task_update(co.c, ANA, lead, owner=BEN)  # an open twin under the same owner would be refused as a duplicate
    co.c.commit()
    co.attach(card, co.requirement("Product", "Review", "Spec approved"), url=SPEC)
    approve_spec(co, card)
    assert co.where(card) == ("Product", "In development")
    review_link = co.c.execute(
        "SELECT id FROM pipeline_links WHERE from_pipeline_stage_id=? AND mode='spawn' AND to_pipeline_id=?",
        (co.stage("Product", "Review"), co.type["Engineering"]),
    ).fetchone()[0]
    builds = [kid for kid in subtasks(co, card) if co.where(kid)[0] == "Engineering"]
    assert len(builds) == 2
    for build in builds:
        co.move(build, "Doing", actor=ANA)
        co.move(build, "Done", actor=ANA)
    assert co.where(card) == ("Product", "Validate")
    checked = co.requirement("Product", "Validate", "Validated")
    co.attach(card, checked, url=VALIDATION)
    result = pipeline_review.not_validated(co.c, ANA, card, checked, "Load time stayed over 4 s")
    co.c.commit()
    assert co.where(result["follow_up"]) == ("Engineering", "Todo")
    filed = co.c.execute(
        "SELECT pipeline_link_id FROM pipeline_handoffs WHERE target_task_id=?", (result["follow_up"],)
    ).fetchone()[0]
    assert filed == review_link and filed != specifying_link
    assert co.where(card) == ("Product", "In development")


def test_a_card_moved_into_product_review_from_outside_is_held_to_the_spec_page(co):
    """Codex GB2 finding 2: a General card moved into Product at Review skipped Specifying's exit requirement, because the
    gate read only the card's source stages. A bot is refused; the card is let in once its spec page is attached and met."""
    from types import SimpleNamespace

    from backend import requirement_attachments

    card = H.task_create(co.c, ANA, "Dark mode from outside", "", BUILDER, lint=False)["id"]
    co.c.commit()
    with pytest.raises(H.Refused) as refused:
        H.task_update(co.c, BUILDER, card, type=co.type["Product"], step="Review")
    assert refused.value.rule == "requirement"
    assert co.c.execute("SELECT type_id FROM tasks WHERE id=?", (card,)).fetchone()[0] != co.type["Product"]

    co.move(card, "Specifying", actor=ANA, type=co.type["Product"])
    assert co.where(card) == ("Product", "Specifying")
    file_id = tico_spec_page(co, card)
    requirement_attachments.attach(
        co.c,
        _Guard(),
        SimpleNamespace(actor=ANA, role="human", task_actor=None),
        card,
        co.requirement("Product", "Specifying", "Spec page"),
        {"source": "file", "file_id": file_id},
    )
    co.c.commit()
    co.spec(card)
    co.move(card, "Review", actor=BUILDER)
    assert co.where(card) == ("Product", "Review")


def test_a_pipeline_editor_may_still_override_the_spec_page_when_a_card_enters_review(co):
    """The override the gate gives a pipeline editor is unchanged: Ana moves a card in from outside past the Spec page with a
    note, and the override is written."""
    card = H.task_create(co.c, ANA, "Dark mode, moved by Ana", "", BUILDER, lint=False)["id"]
    co.c.commit()
    H.task_update(
        co.c,
        ANA,
        card,
        type=co.type["Product"],
        step="Review",
        mover=True,
        override_requirements=True,
        note="The spec is in the meeting notes",
    )
    co.c.commit()
    assert co.where(card) == ("Product", "Review")
    overrides = co.c.execute(
        "SELECT COUNT(*) FROM stage_decisions WHERE task_id=? AND verdict='override'", (card,)
    ).fetchone()[0]
    assert overrides >= 1


def test_a_card_with_no_stage_in_product_is_held_to_the_spec_page_when_it_enters_review(co):
    """Codex GB2 finding 1: a bot puts a card into Product with no stage, then moves it to Review. The second move is a
    card entering the pipeline at a stage, so the Spec page holds it."""
    card = H.task_create(co.c, ANA, "Dark mode, no stage", "", BUILDER, lint=False)["id"]
    co.c.commit()
    H.task_update(co.c, BUILDER, card, type=co.type["Product"], step="")
    co.c.commit()
    assert co.c.execute("SELECT step_id FROM tasks WHERE id=?", (card,)).fetchone()[0] is None
    with pytest.raises(H.Refused) as refused:
        H.task_update(co.c, BUILDER, card, step="Review")
    assert refused.value.rule == "requirement"
    assert co.c.execute("SELECT step_id FROM tasks WHERE id=?", (card,)).fetchone()[0] is None


def test_a_bot_may_not_clear_the_stage_of_a_card_in_a_pipeline(co):
    """Codex GB2 finding 1: a bot clears a card's stage with step="" and is refused, as a backward move is. The card keeps
    its stage."""
    card = co.ticket("Engineering", "Todo")
    with pytest.raises(H.Refused) as refused:
        H.task_update(co.c, BUILDER, card, step="")
    assert refused.value.rule == "requirement"
    assert co.where(card) == ("Engineering", "Todo")


def test_a_pipeline_editor_keeps_the_override_for_a_card_with_no_stage(co):
    """A person who can edit the pipeline still moves a stage-less card past the Spec page with an override."""
    card = H.task_create(co.c, ANA, "Dark mode, Ana's", "", BUILDER, lint=False)["id"]
    co.c.commit()
    H.task_update(co.c, ANA, card, type=co.type["Product"], step="")
    co.c.commit()
    H.task_update(
        co.c, ANA, card, step="Review", mover=True, override_requirements=True, note="The spec is in the meeting notes"
    )
    co.c.commit()
    assert co.where(card) == ("Product", "Review")
