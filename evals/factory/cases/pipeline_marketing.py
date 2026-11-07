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

from evals.factory.board import MERGE, Check, Board, Step
from evals.factory.scenarios import BASE, Scenario, at, tickets
from evals.factory.world import START

STEPS = ("New lead", "Qualifying", "Qualified")
REQUIREMENTS = (
    ("Lead has company and contact", "attachment", "bot"),
    ("Campaign brief approved", "approval", "person"),
)
WARN_AFTER_MINUTES = 90


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
    ts = START.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    w.store.write(
        "ana",
        lambda c: (
            c.execute("UPDATE task_steps SET limit_minutes=? WHERE id=?", (WARN_AFTER_MINUTES, stage)),
            [
                c.execute(
                    "INSERT INTO stage_requirements(id,pipeline_id,pipeline_stage_id,name,kind,needs,reviewed_by,reviewer_id,"
                    "position,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"req-mkt-{n}",
                        w.dev["id"],
                        stage,
                        name,
                        kind,
                        "{}",
                        reviewer,
                        "human:ana" if kind == "approval" else None,
                        n,
                        ts,
                        ts,
                    ),
                )
                for n, (name, kind, reviewer) in enumerate(REQUIREMENTS)
            ],
        ),
    )


def qualified_only_with_evidence(run):
    """Every lead at Qualified or later has both requirements met (a non-undone met or override decision)."""
    bad = []
    for t in run.final:
        if t["step"] not in ("Qualified", "Handed over"):
            continue
        met = {
            r["stage_requirement_id"]
            for r in run.w.sql(
                "SELECT stage_requirement_id FROM stage_decisions WHERE task_id=? AND verdict IN ('met','override') "
                "AND undone_at IS NULL",
                (t["id"],),
            )
        }
        if len(met) < len(REQUIREMENTS):
            bad.append(t["title"])
    return not bad, ", ".join(bad[:6])


def none_late_unasked(run):
    """A lead past its Warn after was asked once by the pipeline manager (an `ask` action) or has left the stage."""
    cutoff = run.end - at(0, WARN_AFTER_MINUTES)
    silent = []
    for t in run.final:
        if t["step"] != "Qualifying" or run.w.step_entered_at(t["id"]) > cutoff:
            continue
        asks = run.w.sql("SELECT 1 FROM pipeline_manager_actions WHERE task_id=? AND action='ask'", (t["id"],))
        if not asks:
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
