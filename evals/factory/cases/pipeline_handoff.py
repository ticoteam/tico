"""A ticket crosses from one pipeline to the next and the handoff is recorded once (Track G).

Written ahead of the engine tracks and NOT registered in cases/__init__.py: it needs Track A's links
(`pipeline_links`, a `move` link entering the target's first stage) and a second pipeline in the sim world, which the sim
core does not have yet (World builds one Dev type). When the world can build a second type, register this (one import
line, one timing) and run seeds 1-3.

The setup links Product (stage "Ready for build") to Engineering (the sim's board) by a `move` link. Tickets reach Ready for
build with "Spec approved" met; ana approves them in review mode, a few within a minute of each other. The checks, all read
from the tables: every approved ticket is in Engineering's first stage, exactly one open stage visit each, one handoff
visit per crossing (no double handoff when ana approves twice), and no call got a server error. The Engineering pipeline
does not count a handed-over ticket as new input of its own twice (cards created = handoffs + direct)."""

from evals.factory.scenarios import BASE, Scenario, tickets

LINK = "lnk-product-engineering"


def setup(run, w, team):
    made = tickets(run, w, 5, title="Spec")
    run.handed = [t["id"] for t in made]
    ts = w.now().strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    product = w.post("task-types", {"name": "Product", "steps": [{"name": "Ready for build", "status": "open"}]})[
        "type"
    ]
    run.product = product["id"]
    w.store.write(
        "ana",
        lambda c: c.execute(
            "INSERT INTO pipeline_links(id,from_pipeline_id,from_pipeline_stage_id,to_pipeline_id,mode,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (LINK, product["id"], product["steps"][0]["id"], w.dev["id"], "move", "human:ana", ts),
        ),
    )


def crossed_once(run):
    """Each ticket has exactly one 'handoff' visit into Engineering and one open visit."""
    bad = []
    for ticket in run.handed:
        visits = run.w.sql(
            "SELECT direction, left_at FROM stage_visits WHERE task_id=? AND pipeline_id=?", (ticket, run.w.dev["id"])
        )
        handoffs = [v for v in visits if v["direction"] == "handoff"]
        opened = [v for v in visits if v["left_at"] is None]
        if len(handoffs) != 1 or len(opened) > 1:
            bad.append(f"{run.w.title(ticket)}: {len(handoffs)} handoffs, {len(opened)} open")
    return not bad, ", ".join(bad[:6])


def enter_at_first_stage(run):
    first = run.w.board.first().name
    off = [
        run.w.title(t)
        for t in run.handed
        if run.w.sql(
            "SELECT 1 FROM stage_visits WHERE task_id=? AND direction='handoff' ORDER BY entered_at LIMIT 1", (t,)
        )
        and not run.w.sql(
            "SELECT 1 FROM stage_visits v JOIN task_steps s ON s.id=v.pipeline_stage_id "
            "WHERE v.task_id=? AND v.direction='handoff' AND s.name=?",
            (t, first),
        )
    ]
    return not off, ", ".join(off[:6])


SCENARIOS = [
    Scenario(
        "pipeline_handoff",
        "Product hands approved specs to Engineering through a move link; each crossing is recorded once.",
        setup=setup,
        checks=[
            *BASE,
            ("each crossing is recorded once", "pipeline links", crossed_once),
            ("a handed-over ticket enters the first stage", "pipeline links", enter_at_first_stage),
        ],
    )
]
