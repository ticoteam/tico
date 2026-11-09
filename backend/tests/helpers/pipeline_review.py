"""Helpers that other tests share, moved out of test_pipeline_review.py so they import no test file."""

import time
from pathlib import Path
from backend import hubdb as H
from backend import pipeline_review as R
from backend.config import Settings
from backend.store import Store


ANA, BEN, CHRIS = "human:ana", "human:ben", "human:chris"


FIXTURES = Path(__file__).parents[1] / "fixtures"


STEPS = [
    {"name": "Making", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


class World:
    """A Design pipeline: Making, Review (a Mockup attachment and a Sign-off approval, both ana's) and Shipped."""

    def __init__(self, path, reviewed_by="person", conn=None, steps=STEPS):
        self.path = path
        if conn is None:
            Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)
            conn = H.connect(path)
        self.c = conn
        H.sync_registry(
            self.c,
            {"cmo": {"name": "cmo", "runtime": "fake", "status": "active"}},
            {
                "people": [
                    {"id": "ana", "email": "ana@acme.example"},
                    {"id": "ben", "email": "ben@acme.example"},
                    {"id": "chris", "email": "chris@acme.example"},
                ]
            },
        )
        c = self.c
        self.pipeline = H.type_create(c, ANA, "Design", steps, mover=True)["id"]
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, self.pipeline))
        self.making, self.review, self.shipped = (self.step(n) for n in ("Making", "Review", "Shipped"))
        self.mockup = self.requirement("Mockup", "attachment", reviewed_by)
        self.signoff = self.requirement("Sign-off", "approval", "person")
        self.task = self.ticket("Logo")

    def step(self, name):
        return self.c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (self.pipeline, name)).fetchone()[
            0
        ]

    def requirement(self, name, kind, reviewed_by, reviewer=ANA, stage=None, **extra):
        rid = f"r-{name.lower().replace(' ', '-')}"
        values = {
            "id": rid,
            "pipeline_id": self.pipeline,
            "pipeline_stage_id": stage or self.review,
            "name": name,
            "kind": kind,
            "reviewed_by": reviewed_by,
            "reviewer_id": reviewer,
            "created_at": H.now(),
            "updated_at": H.now(),
            **extra,
        }
        self.c.execute(
            f"INSERT INTO stage_requirements ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
        return rid

    def ticket(self, title, step="Review", owner="bot:cmo"):
        task = H.task_create(self.c, ANA, title, "", owner, type=self.pipeline, step=step, lint=False)
        return task["id"]

    def attach(self, task=None, requirement=None, digest="d1", attachment_id=None):
        values = (
            attachment_id or f"a-{digest}-{time.monotonic_ns()}",
            task or self.task,
            requirement or self.mockup,
            "link",
            "https://example.test/mockup",
            digest,
            self.revision(task),
            H.now(),
        )
        self.c.execute(
            "INSERT INTO requirement_attachments (id, task_id, stage_requirement_id, source, url, digest, work_version, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            values,
        )

    def revision(self, task=None):
        return self.c.execute("SELECT work_revision FROM tasks WHERE id=?", (task or self.task,)).fetchone()[0]

    def push(self, task=None):
        self.c.execute("UPDATE tasks SET work_revision=work_revision+1 WHERE id=?", (task or self.task,))

    def step_of(self, task=None):
        return self.c.execute("SELECT step_id FROM tasks WHERE id=?", (task or self.task,)).fetchone()[0]

    def visit(self, task=None, at=None, step=None):
        """The ticket's open visit at `step`: the one the engine opened at creation is moved there, else one is opened
        (a ticket has one open visit at a time)."""
        ticket, when, stage = task or self.task, at or H.now(), step or self.review
        open_visit = self.c.execute(
            "SELECT id FROM stage_visits WHERE task_id=? AND left_at IS NULL", (ticket,)
        ).fetchone()
        if open_visit:
            self.c.execute(
                "UPDATE stage_visits SET pipeline_stage_id=?, entered_at=? WHERE id=?", (stage, when, open_visit[0])
            )
            return
        self.c.execute(
            "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"v-{time.monotonic_ns()}", ticket, self.pipeline, stage, "initial", when),
        )

    def decide(self, requirement, verdict, judge="llm", task=None, **extra):
        values = {
            "id": f"d-{time.monotonic_ns()}",
            "task_id": task or self.task,
            "pipeline_stage_id": self.review,
            "stage_requirement_id": requirement,
            "work_version": self.revision(task),
            "judge": judge,
            "verdict": verdict,
            "created_at": H.now(),
            **extra,
        }
        self.c.execute(
            f"INSERT INTO stage_decisions ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )

    def view(self, requirement=None, task=None):
        """What ana's queue shows for the item: the versions she would send back with Approve."""
        item = next(
            i
            for i in R.queue_for(self.c, ANA)
            if i["task_id"] == (task or self.task) and i["requirement_id"] == (requirement or self.mockup)
        )
        return {
            "work_version": item["work_version"],
            "attachment_digest": item["attachment_digest"],
            "requirement_version": item["requirement_version"],
        }

    def approve(self, requirement=None, actor=ANA, task=None, **kw):
        seen = {} if kw.get("seen") is False else self.view(requirement, task)
        kw.pop("seen", None)
        return R.approve(self.c, actor, task or self.task, requirement or self.mockup, **{**seen, **kw})
