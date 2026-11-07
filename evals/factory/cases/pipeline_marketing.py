"""A Marketing pipeline run by the engine: leads move New lead > Qualifying > Qualified, and nothing leaves Qualifying without
its evidence (Track G; the validation checklist in docs/pipelines-validation.md).

Written ahead of the engine tracks and NOT registered in cases/__init__.py: it needs Track A's requirement-aware board
(`Step.requirements`, bots that read a `requirement` refusal) and Track B's attach route, and it needs a
timings.json entry. When they land, register it (one import line, one timing) and run seeds 1-3.

The board is a Studio-style one (design_board's shape) with marketing names. Each ticket is a lead. Two requirements sit on
Qualifying, seeded in setup by direct insert as the plan says: "Lead has company and contact" (attachment, reviewed by a bot)
and "Campaign brief approved" (approval, ana). Some builders attach a lead record with no email: the judge says not met, the
lead stays, and the builder is told what is missing. ana approves the brief in review mode. The checks: no lead is at Qualified
without both requirements met at the current work version, none sits past its Warn after unasked, and no call got a server
error."""

import datetime as dt

from evals.factory.board import MERGE, Check, Board, Step
from evals.factory.scenarios import BASE, Scenario, at, tickets
from evals.factory.world import START

STEPS = ("New lead", "Qualifying", "Qualified")
REQUIREMENTS = (
    ("Lead has company and contact", "attachment", "bot"),
    ("Campaign brief approved", "approval", "person"),
)
WARN_AFTER_MINUTES = 90
REQUIREMENT_IDS = tuple(f"req-mkt-{n}" for n in range(len(REQUIREMENTS)))


def marketing_board():
    steps = [
        Step("New lead", "open", assign="builder"),
        Step("Qualifying", "doing", work=True, next="Peer Check"),
        Step("Peer Check", "review", check=Check("reviewer", MERGE, "Qualifying", timeout_hours=1.5, kind="review")),
        Step("Qualified", "review", can_hold=False, merged=True, release=True),
        Step("Handed over", "done", can_hold=False, merged=True),
        Step("Dropped", "closed", can_hold=False),
    ]
    return Board("Marketing leads", steps, on_merge="Qualified")


def setup(run, w, team):
    tickets(run, w, 6, title="Lead")
    stage = next(s["id"] for s in w.dev["steps"] if s["name"] == "Qualifying")
    stamp = w.H.now()
    with w.store.transaction() as c:
        c.execute("UPDATE task_steps SET limit_minutes=? WHERE id=?", (WARN_AFTER_MINUTES, stage))
        for n, (name, kind, reviewer) in enumerate(REQUIREMENTS):
            c.execute(
                "INSERT INTO stage_requirements(id,pipeline_id,pipeline_stage_id,name,kind,needs,reviewed_by,reviewer_id,"
                "position,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    REQUIREMENT_IDS[n],
                    w.dev["id"],
                    stage,
                    name,
                    kind,
                    "{}",
                    reviewer,
                    "human:ana" if kind == "approval" else None,
                    n,
                    "human:ana",
                    stamp,
                    stamp,
                ),
            )


def qualified_only_with_evidence(run):
    """Every lead at Qualified or later has both of this pipeline's requirements met at the lead's current work revision (a
    non-undone met or override decision on the requirement itself, not any decision), and at least one lead got there, so
    a stalled run cannot pass."""
    bad, reached = [], 0
    for t in run.final:
        if t["step"] not in ("Qualified", "Handed over"):
            continue
        reached += 1
        marks = ", ".join("?" * len(REQUIREMENT_IDS))
        met = {
            r["stage_requirement_id"]
            for r in run.w.sql(
                "SELECT d.stage_requirement_id FROM stage_decisions d JOIN tasks t ON t.id=d.task_id "
                "WHERE d.task_id=? AND d.verdict IN ('met','override') AND d.undone_at IS NULL "
                f"AND d.stage_requirement_id IN ({marks}) AND d.work_version=t.work_revision",
                (t["id"], *REQUIREMENT_IDS),
            )
        }
        if met != set(REQUIREMENT_IDS):
            bad.append(t["title"])
    if not reached:
        return False, "no lead reached Qualified"
    return not bad, ", ".join(bad[:6])


def _entered(run, task_id):
    """When a lead entered its current stage: its last step change, or the sim's start for a lead that never moved."""
    raw = run.w.step_entered_at(task_id)
    return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else START


def none_late_unasked(run):
    """A lead past its Warn after in Qualifying was asked exactly once by the pipeline manager during that visit (an `ask`
    action written after it entered the stage; D40). A lead that left the stage is not counted."""
    cutoff = run.end - at(0, WARN_AFTER_MINUTES)
    silent = []
    for t in run.final:
        if t["step"] != "Qualifying":
            continue
        entered = _entered(run, t["id"])
        if entered > cutoff:
            continue
        asks = run.w.sql(
            "SELECT count(*) AS n FROM pipeline_manager_actions WHERE task_id=? AND action='ask' AND created_at>=?",
            (t["id"], entered.strftime("%Y-%m-%dT%H:%M:%S.000000Z")),
        )
        if not asks or asks[0]["n"] != 1:
            silent.append(t["title"])
    return not silent, ", ".join(silent[:6])


SCENARIOS = [
    Scenario(
        "pipeline_marketing",
        "A Marketing pipeline: leads need a contact record and an approved brief before Qualified.",
        setup=setup,
        board=marketing_board,
        checks=[
            *BASE,
            ("no lead qualifies without its evidence", "pipeline requirements", qualified_only_with_evidence),
            ("every late lead was asked once", "pipeline manager", none_late_unasked),
        ],
    )
]
