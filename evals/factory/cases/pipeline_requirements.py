"""Stage requirements on a board (pipeline engine, Track A): the design board with two requirements seeded straight into the
database in setup, as a pipeline's owner would configure them. Design must show a due date before the ticket moves to
Build, and Build needs green GitHub checks on the pull request before it moves to Peer Check.

The bots read the server's `requirement` refusal and act on it (bots.Builder.meet_requirements): they set the due date,
and wait for CI. A push for fixes sends the checks back to pending, so the Build requirement is unmet again until CI
passes on the new head. The checks read the facts themselves (task_links.checks, tasks.due_at) at every forward move the
server makes, never the engine's own answer: no card leaves a stage with an unmet requirement, the situation did happen
(the server refused a move and a push re-opened the requirement), and no call got a server error."""

import json

from ..board import design_board
from ..scenarios import Scenario, standard, tickets

GATE = "stage requirements"
DUE = {"name": "Due date set", "kind": "field", "needs": {"field": "due"}, "reviewed_by": "nobody"}
CHECKS = {"name": "CI green", "kind": "run", "needs": {"source": "github_checks"}, "reviewed_by": "nobody"}


def requirement_board():
    board = design_board()
    board.step("Design").requirements = (DUE,)
    board.step("Build").requirements = (CHECKS,)
    return board


def seed(w):
    """The requirements as rows, written the way the pipeline owner's tool will (the sim has no route for them yet)."""
    stamp = w.H.now()
    with w.store.transaction() as c:
        for step in w.board.steps:
            ident = next(s["id"] for s in w.dev["steps"] if s["name"] == step.name)
            for position, r in enumerate(step.requirements):
                c.execute(
                    "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, "
                    "position, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        w.H.new_id(),
                        w.dev["id"],
                        ident,
                        r["name"],
                        r["kind"],
                        json.dumps(r["needs"]),
                        r["reviewed_by"],
                        position,
                        "human:ana",
                        stamp,
                        stamp,
                    ),
                )


def watch(run, w):
    """Record every forward move that leaves a stage with a requirement the facts do not meet. The facts are read here, from
    the rows the bots and the server wrote, not from the engine."""
    from backend import pipeline_engine

    def wrap(original):
        def on_step_change(conn, actor, row, type_id, step_id, ts):
            old = conn.execute("SELECT position FROM task_steps WHERE id = ?", (row.get("step_id"),)).fetchone()
            new = conn.execute("SELECT position FROM task_steps WHERE id = ?", (step_id,)).fetchone()
            if old and new and new[0] > old[0] and row.get("type_id") == type_id:
                stages = [
                    r[0]
                    for r in conn.execute(
                        "SELECT s.id FROM task_steps s WHERE s.type_id = ? AND s.position <= ?", (type_id, old[0])
                    )
                ]
                for stage_id in stages:
                    for name, kind, needs in conn.execute(
                        "SELECT name, kind, needs FROM stage_requirements WHERE pipeline_stage_id = ? "
                        "AND retired_at IS NULL",
                        (stage_id,),
                    ).fetchall():
                        if not _met(conn, row["id"], kind, json.loads(needs)):
                            overridden = conn.execute(
                                "SELECT 1 FROM stage_decisions WHERE task_id = ? AND verdict = 'override'", (row["id"],)
                            ).fetchone()
                            if not overridden:
                                run.violations["unmet_exit"].append((w.now(), f"{row['id'][:8]} {name}"))
            return original(conn, actor, row, type_id, step_id, ts)

        return on_step_change

    w.patch(pipeline_engine, "on_step_change", wrap)


def _met(conn, task_id, kind, needs):
    if kind == "field":
        return bool(conn.execute("SELECT due_at FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])
    states = {
        r[0] or "pending"
        for r in conn.execute("SELECT checks FROM task_links WHERE task_id = ? AND kind = 'pr'", (task_id,))
    }
    return states == {"passing"}


def setup(run, w, team):
    seed(w)
    watch(run, w)
    tickets(run, w, 6)


def never_leaves_unmet(run):
    seen = run.violations["unmet_exit"]
    return not seen, ", ".join(f"{t}" for _, t in seen[:6])


def requirement_was_exercised(run):
    """Both requirements refused a move (a builder moved before the date was set, and before CI was green), and tickets
    still reached Peer Check: a push for fixes sends CI back to pending, so Build is unmet again until it passes."""
    refused = run.w.sql("SELECT detail FROM refusals WHERE rule = 'requirement'")
    due = sum("Due date set" in r["detail"] for r in refused)
    ci = sum("CI green" in r["detail"] for r in refused)
    moved = run.w.sql(
        "SELECT count(*) n FROM history_events WHERE subject_type = 'task' AND kind = 'step' "
        "AND new IN (SELECT id FROM task_steps WHERE name = 'Peer Check')"
    )[0]["n"]
    return (
        due > 0 and ci > 0 and moved > 0,
        f"refused for the date {due}x and for CI {ci}x; {moved} moves into Peer Check",
    )


SCENARIOS = [
    Scenario(
        "pipeline_requirements",
        "The design board with a due-date requirement on Design and green CI on Build: the server refuses a move that "
        "leaves either unmet, builders set the date and wait for CI, and a push for fixes re-opens the CI requirement.",
        setup=setup,
        board=requirement_board,
        styles={"reviewer": {"changes": 0.4}},
        checks=standard(
            ("no card leaves a stage with an unmet requirement", GATE, never_leaves_unmet),
            ("both requirements refused a move, and tickets still reached Peer Check", GATE, requirement_was_exercised),
        ),
    ),
]
