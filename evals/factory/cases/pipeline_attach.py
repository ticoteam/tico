"""Bots attach screenshots to a requirement through the API; one bot re-attaches old shots after a push and they are refused.

The Dev board's In Progress step has an attachment requirement (screenshot links, reviewed by nobody; the judge has its
own tests). Builders attach their shots when their work is done, through the real API with their own attempt tokens. One
builder pushes again after attaching and then attaches the old shots once more: shots taken before the push are out of
date, so the engine must not count them. The judge's fake Decide engine is injected through the
`pipeline_judges.ENGINE` seam.

NOT REGISTERED: `SCENARIOS` is empty, so the sim runs nothing from this file. Three things the sim does not have yet:
1. The board is not requirement-aware: `evals/factory/board.py` has no requirement on a step, so `apply` cannot seed one,
   and the bots (`bots.py`) have no attach action. Both are Track A / sim-core files this track may not edit.
2. A bot's attempt token exists only inside its turn, so a scenario hook outside a turn cannot attach as a bot.
3. Attaching a stale shot is accepted and stored by `requirement_attachments.attach`; refusing it is the engine's
   evaluation of the requirement (work_version against the attachment, Track A's pipeline_engine), not the attach route.
Run locally with ATTACH registered (seeds 1-3, one process), the seeding works and the six standard checks pass; the
attach check fails with "no bot attached anything", which is gap 1. When those land, set `SCENARIOS = [ATTACH]` (cases/__init__.py already imports this module). The checks below read only
the database, so they hold as written."""

from evals.factory.scenarios import Scenario, standard, tickets

REQUIREMENT = "Pipeline requirements"  # the product rules this scenario guards (backend/requirement_attachments.py)
STAGE = "In Progress"


def seed_requirement(run, w, team):
    """Four tickets and the screenshot requirement on the In Progress stage."""
    from backend import pipeline_requirements as R

    with w.store.transaction() as c:
        stage = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (w.dev["id"], STAGE)).fetchone()[0]
        R.add(
            c,
            "human:ana",
            stage,
            {
                "name": "Screenshots",
                "kind": "attachment",
                "needs": {"formats": ["link"], "newer_than_change": True},
                "reviewed_by": "nobody",
            },
        )
    return tickets(run, w, 4)


def attachments(w):
    return w.sql("SELECT task_id, work_version, digest, attached_by, removed_at FROM requirement_attachments")


def every_attach_by_a_bot_is_recorded(run):
    """Every attachment records the digest it was attached as and the ticket's work version, and who attached it."""
    rows = attachments(run.w)
    bad = [r["task_id"] for r in rows if not r["digest"] or r["work_version"] is None or not r["attached_by"]]
    if not rows:
        bad.append("no bot attached anything")
    return not bad, ", ".join(bad[:6]) or f"{len(rows)} attachments recorded"


def stale_shots_do_not_count(run):
    """A ticket whose shots predate its newest push is not met on them: no decision of 'met' cites an attachment older
    than the ticket's work version at the time."""
    rows = run.w.sql(
        "SELECT d.task_id FROM stage_decisions d JOIN tasks t ON t.id=d.task_id "
        "WHERE d.verdict='met' AND d.work_version < t.work_revision"
    )
    bad = [r["task_id"] for r in rows]
    return not bad, ", ".join(bad[:6]) or "no met on an out-of-date attachment"


ATTACH = Scenario(
    "pipeline_attach",
    "Bots attach screenshots through the API; shots re-attached after a push are out of date and refused.",
    setup=seed_requirement,
    checks=standard(
        ("each attachment records its digest and work version", REQUIREMENT, every_attach_by_a_bot_is_recorded),
        ("out-of-date shots never count", REQUIREMENT, stale_shots_do_not_count),
    ),
)

SCENARIOS = []  # see the docstring: register ATTACH once the board and bots can attach
