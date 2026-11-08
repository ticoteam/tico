"""The pipeline manager on a stalled card (backend/pipeline_manager.py, Track D of the pipeline engine plan).

A "Launch case" pipeline has one stage, Building, with a Warn-after Limit of 30 minutes, and a Screenshot requirement
that engineering-lead reviews by bot. Its one card, made by ana and assigned to builder-openai, has no screenshot from
the start. Missing work is asked of its assignee (D30), so the manager asks builder-openai on its first pass;
builder-openai takes that wake and never answers (style silent). The card is past its
Limit from 30 minutes on, and the manager does not ask again in that visit. From 10:00 to 10:40 the manager's passes are
paused (its tick is not run, as if its process were down), so its watchdog opens one BotOps task for the stall, once,
and the manager runs again after the pause."""

from datetime import timedelta

from backend import hubdb as H
from backend import pipeline_manager as PM
from evals.factory.scenarios import BASE, Scenario, at
from evals.factory.world import START

NAME = "Launch case"
PERSON = "human:ana"
MANAGER = "engineering-lead"
BUILDER = "bot:builder-openai"
REVIEWER = "bot:engineering-lead"
PAUSE_AT = START + at(1)
PAUSE_UNTIL = PAUSE_AT + timedelta(minutes=40)
BOTOPS_TITLE = "Pipeline manager stopped: " + NAME


def paused(at_when):
    return PAUSE_AT <= at_when <= PAUSE_UNTIL


def setup(run, w, team):
    with w.store.transaction() as c:
        H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)
        typ = H.type_create(
            c,
            PERSON,
            NAME,
            [{"name": "Building", "status": "doing"}, {"name": "Done", "status": "done"}],
            mover=True,
        )
        c.execute(
            "UPDATE task_types SET manager=?, manager_every_minutes=10 WHERE id=?",
            (MANAGER, typ["id"]),
        )
        # The assignment trigger stamps manager_since on the wall clock; the watchdog judges it on the sim's clock.
        c.execute("UPDATE task_types SET manager_since=? WHERE id=?", (H.now(), typ["id"]))
        c.execute("UPDATE task_steps SET limit_minutes=30 WHERE type_id=? AND name='Building'", (typ["id"],))
        stage = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Building'", (typ["id"],)).fetchone()[0]
        now = H.now()
        c.execute(
            "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, "
            "reviewer_id, position, version, created_by, created_at, updated_at) "
            "VALUES ('req-launch-screenshot', ?, ?, 'Screenshot', 'attachment', '{}', 'bot', ?, 1, 1, ?, ?, ?)",
            (typ["id"], stage, REVIEWER, PERSON, now, now),
        )
        card = H.task_create(c, PERSON, "Launch page", "", BUILDER, type=typ["id"], step="Building", lint=False)
        if c.execute("SELECT 1 FROM stage_visits WHERE task_id=?", (card["id"],)).fetchone() is None:
            # The card entered Building when it was made; a task made without its visit is entered here.
            c.execute(
                "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_by, entered_at) "
                "VALUES (?,?,?,?,'initial',?,?)",
                (f"visit-{card['id']}", card["id"], typ["id"], stage, PERSON, now),
            )
    run.pipeline = typ["id"]
    run.card = card["id"]
    # The manager's tick is not run while it is paused. Its watchdog still runs (it is part of the same scheduler call).
    w.patch(PM, "tick", lambda original: lambda store, at_when: None if paused(at_when) else original(store, at_when))


def parse(run, text):
    return run.w.H.parse_ts(text)


def asks(run):
    return run.w.sql(
        "SELECT target_actor, created_at FROM pipeline_manager_actions WHERE pipeline_id=? AND action='ask' "
        "ORDER BY created_at",
        (run.pipeline,),
    )


def passes(run):
    return run.w.sql(
        "SELECT started_at, exceptions_count FROM pipeline_manager_runs WHERE pipeline_id=? AND status='ok' "
        "ORDER BY started_at",
        (run.pipeline,),
    )


def first_ask_once(run):
    """The builder is asked once, on the manager's first pass after the card arrives."""
    found = asks(run)
    ok = len(found) == 1 and found[0]["target_actor"] == BUILDER
    return ok, f"{len(found)} asks to {', '.join(sorted({a['target_actor'] for a in found})) or 'nobody'}"


def no_second_ask(run):
    """The visit is not asked again: the screenshot is still missing on later passes, and no second ask is made."""
    found = asks(run)
    after = [p for p in passes(run) if found and parse(run, p["started_at"]) > parse(run, found[0]["created_at"])]
    still = [p for p in after if p["exceptions_count"] > 0]
    ok = len(found) == 1 and len(still) >= 3
    return ok, f"{len(found)} asks; {len(still)} later passes still saw the screenshot missing"


def card_late(run):
    """The card shows Late: the rail lists it as over its Limit."""
    with run.w.store.read() as c:
        rail = PM.rail(c, run.pipeline, at=run.w.now())
    late = [item["task_id"] for item in (rail or {}).get("late", [])]
    return run.card in late, f"{len(late)} late cards on the rail"


def manager_paused(run):
    """No pass runs between 10:00 and 10:40; the manager runs again after the pause."""
    starts = [parse(run, p["started_at"]) for p in passes(run)]
    inside = [s for s in starts if PAUSE_AT <= s <= PAUSE_UNTIL]
    resumed = [s for s in starts if s > PAUSE_UNTIL]
    return not inside and bool(resumed), f"{len(inside)} passes in the pause, {len(resumed)} after it"


def one_botops_task(run):
    """One BotOps task opens for the stall, while the manager is paused."""
    found = run.w.sql(
        "SELECT created_at FROM tasks WHERE owner_id='bot:botops' AND title=?",
        (BOTOPS_TITLE,),
    )
    inside = [r for r in found if PAUSE_AT <= parse(run, r["created_at"]) <= PAUSE_UNTIL + timedelta(minutes=10)]
    return len(found) == 1 and len(inside) == 1, f"{len(found)} BotOps tasks, {len(inside)} opened in the pause"


SCENARIOS = [
    Scenario(
        "pipeline_manager",
        "A builder ignores the pipeline manager's ask: no second ask in the visit, the card shows Late, and the manager "
        "paused for 40 min opens one BotOps task.",
        setup=setup,
        checks=[
            *BASE,
            ("the builder is asked once for the missing screenshot", "pipeline manager", first_ask_once),
            ("no second ask in the same visit", "pipeline manager", no_second_ask),
            ("the card shows Late on the rail", "pipeline manager", card_late),
            ("the manager is paused for 40 min", "pipeline manager", manager_paused),
            ("one BotOps task opens for the stall", "pipeline manager", one_botops_task),
        ],
        styles={"builder-openai": {"silent": 1.0}},
        hours=3,
        drain_hours=0.5,
    )
]
