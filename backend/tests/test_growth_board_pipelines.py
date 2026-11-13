"""The Growth page reads tests in the Growth pipelines (backend/cards.py marketing_tasks, backend/growth_numbers.py
_markers): a task in Growth Experiments (Ideas, Building, Running) or Growth Monitoring (Watching, Slipping) shows on
the Growth board and its channel's row and marks the charts when it starts and finishes, with or without the older
stage labels. Success Experiments does not count. Examples use Acme, a home-cleaning company; synthetic rows only."""

import json
import time

from backend import growth_numbers as GN
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post, put

ANA = "human:ana"
OWNER = "bot:ops"
IDEAS, BUILDING, RUNNING = "growth-experiments-ideas", "growth-experiments-building", "growth-experiments-running"
WATCHING, SLIPPING = "growth-monitoring-watching", "growth-monitoring-slipping"


def _set_up(c):
    """Setup finished and the presets made, so the company has the Growth and Success pipelines."""
    record = {"answers": {"software_product": "no"}, "completed": H.now()}
    c.execute(
        "INSERT INTO registry_metadata VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(record),),
    )
    X.bot(c, "ops")
    PS.ensure_presets(c)


def _filed(c, title, pipeline, stage=None, labels=(), private=None):
    return H.task_create(
        c,
        ANA,
        title,
        "Try it.",
        OWNER,
        pipeline=pipeline,
        stage=stage,
        labels=list(labels),
        private=private,
        lint=False,
    )["id"]


def _move(c, task_id, to, why="Moving it on"):
    return E.move(c, task_id, to, ANA, why, seen_version=E.task(c, task_id)["version"])


def _board(api, token="ana-test"):
    r = api.get("/api/v2/places/growth", headers=headers(token))
    assert r.status_code == 200, r.text
    cards = {p["card"]["template"]: p["card"]["data"] for p in r.json()["pins"]}
    return cards


class _ReadsAll:
    """auth as the markers read it, for a reader who may read every task."""

    def task_sql(self, c, who):
        return "1"


def test_a_test_in_each_growth_pipeline_stage_shows_in_its_lane_without_labels(api):
    """AC-1, AC-2, AC-3, AC-4, AC-9: each Experiments and Monitoring stage has its lane; a task with a pipeline stage
    and an older label shows once, at its stage; a labelled General task shows as today; Success does not count."""
    with api.app.state.store.transaction() as c:
        _set_up(c)
        idea = _filed(c, "Retarget trial drop-offs", "growth-experiments", IDEAS)
        building = _filed(c, "Comparison pages", "growth-experiments", BUILDING)
        live = _filed(c, "Pricing page headline test", "growth-experiments", RUNNING)
        watching = _filed(c, "Welcome email", "growth-monitoring", WATCHING)
        slipping = _filed(c, "Referral banner", "growth-monitoring", SLIPPING)
        both = _filed(c, "Weekend discount", "growth-experiments", RUNNING, labels=["idea:on-deck"])
        older = _filed(c, "Door hangers", "general", labels=["growth:running"])
        _filed(c, "Onboarding call script", "success-experiments", "success-experiments-running")
    lanes = {lane["stage"]: lane for lane in _board(api)["growth_board"]["lanes"]}
    ids = {stage: sorted(card["id"] for card in lane["cards"]) for stage, lane in lanes.items()}
    assert ids == {
        "testing": sorted([live, both, older]),
        "scaling": sorted([watching, slipping]),
        "measuring": [],
        "building": [building],
        "idea": [idea],
    }
    assert {stage: lane["count"] for stage, lane in lanes.items()} == {
        "testing": 3,
        "scaling": 2,
        "measuring": 0,
        "building": 1,
        "idea": 1,
    }
    marked = {card["id"]: card.get("slipping", False) for card in lanes["scaling"]["cards"]}
    assert marked == {watching: False, slipping: True}


def test_a_running_pipeline_test_shows_on_its_channels_row_and_a_private_one_only_to_its_readers(api):
    """AC-5, AC-10: a Running task labelled channel:seo is a live test on the SEO row; a private one is in no lane
    count or row for a member who may not read it."""
    post(api, "growth/channels", {"slug": "seo", "name": "SEO", "sort": 1})
    put(api, "growth/cohorts", {"channel": "seo", "month": "2026-10", "spend": 300, "new_customers": 9})
    with api.app.state.store.transaction() as c:
        _set_up(c)
        live = _filed(c, "Comparison pages", "growth-experiments", RUNNING, labels=["channel:seo"])
        hidden = _filed(
            c, "Cold email to dentists", "growth-experiments", RUNNING, labels=["channel:seo"], private=True
        )
    ana = _board(api)
    seo = next(r for r in ana["growth_channels"]["rows"] if r["slug"] == "seo")
    assert sorted((t["id"], t["stage"]) for t in seo["tasks"]) == sorted([(live, "testing"), (hidden, "testing")])
    assert {lane["stage"]: lane for lane in ana["growth_board"]["lanes"]}["testing"]["count"] == 2
    assert api.get("/api/v2/tasks/" + hidden, headers=headers("cara-test")).status_code == 404
    cara = _board(api, "cara-test")
    seo = next(r for r in cara["growth_channels"]["rows"] if r["slug"] == "seo")
    assert [(t["id"], t["stage"]) for t in seo["tasks"]] == [(live, "testing")]
    lanes = {lane["stage"]: lane for lane in cara["growth_board"]["lanes"]}
    assert lanes["testing"]["count"] == 1 and [c["id"] for c in lanes["testing"]["cards"]] == [live]
    assert hidden not in {m["task_id"] for m in get(api, "growth/numbers", "cara-test")["markers"]}


def test_the_board_is_hidden_with_no_growth_test_in_a_pipeline_or_labelled(api):
    """AC-11: no open task in a Growth pipeline and none labelled, so the card shows nothing, as today."""
    with api.app.state.store.transaction() as c:
        _set_up(c)
        _filed(c, "Onboarding call script", "success-experiments", "success-experiments-running")
        _filed(c, "Fix the login page", "general")
    assert _board(api).get("growth_board") is None


def test_a_move_into_running_starts_a_test_and_done_or_closed_finishes_it_with_its_result(company):
    """AC-6, AC-7, AC-8: Building to Running starts (and again after a move back); Done finishes as kept with its
    result tag; Closed finishes as dropped; filing straight into Running starts; a Success task and a restore mark
    nothing."""
    with company.transaction() as c:
        _set_up(c)
        failed = _filed(c, "Pricing headline", "growth-experiments", BUILDING, labels=["channel:cro"])
        _move(c, failed, RUNNING)
        _move(c, failed, BUILDING, "Copy needs another pass")
        _move(c, failed, RUNNING)
        H.task_update(c, ANA, failed, labels=["channel:cro", "result:failed"])
        _move(c, failed, E.DONE, "Measured: no lift")
        stopped = _filed(c, "Door hangers", "growth-experiments", RUNNING)
        _move(c, stopped, E.CLOSED, "Stopped: printer fell through")
        kept = _filed(c, "Weekend discount", "growth-experiments", RUNNING)
        _move(c, kept, E.DONE, "Measured")
        other = _filed(c, "Onboarding call", "success-experiments", "success-experiments-building")
        _move(c, other, "success-experiments-running")
        _move(c, other, E.CLOSED, "Not worth it")
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    got = sorted((m["task_id"], m["kind"], m["outcome"], m["result"], tuple(m["channels"])) for m in found)
    assert got == sorted(
        [
            (failed, "started", None, None, ("cro",)),
            (failed, "started", None, None, ("cro",)),
            (failed, "finished", "kept", "failed", ("cro",)),
            (stopped, "started", None, None, ()),
            (stopped, "finished", "dropped", None, ()),
            (kept, "started", None, None, ()),
            (kept, "finished", "kept", None, ()),
        ]
    )


def test_a_test_with_both_a_running_stage_and_a_running_label_starts_once(company):
    """A task with both forms counts once and its pipeline stage wins: filed into Running with growth:running, or
    moved into Running and labelled idea:live later, gives one start. A label-only idea still starts by its label."""
    with company.transaction() as c:
        _set_up(c)
        filed = _filed(c, "Pricing headline", "growth-experiments", RUNNING, labels=["growth:running"])
        moved = _filed(c, "Door hangers", "growth-experiments", BUILDING)
        _move(c, moved, RUNNING)
        H.task_update(c, ANA, moved, labels=["idea:live"])
        labelled = H.task_create(c, ANA, "Weekend discount", "Try it.", OWNER, labels=["idea:on-deck"], lint=False)
        labelled = labelled["id"]
        H.task_update(c, ANA, labelled, labels=["idea:live"])
        found = GN._markers(c, _ReadsAll(), None, "2000-01-01T00:00:00")
    starts = sorted(m["task_id"] for m in found if m["kind"] == "started")
    assert starts == sorted([filed, moved, labelled])


def test_a_test_restored_from_the_trash_does_not_finish_or_start_again(api):
    with api.app.state.store.transaction() as c:
        _set_up(c)
        tid = _filed(c, "Pricing headline", "growth-experiments", RUNNING)
        _move(c, tid, E.CLOSED, "Stopped")
    before = [(m["kind"], m["outcome"]) for m in get(api, "growth/numbers")["markers"] if m["task_id"] == tid]
    assert sorted(before) == [("finished", "dropped"), ("started", None)]
    post(api, f"tasks/{tid}/delete", {})
    r = api.post(f"/api/v2/tasks/{tid}/restore", headers=headers("ana-test"), json={})
    assert r.status_code == 200, r.text
    after = [(m["kind"], m["outcome"]) for m in get(api, "growth/numbers")["markers"] if m["task_id"] == tid]
    assert sorted(after) == sorted(before)


def test_the_growth_board_reads_a_thousand_open_tasks_in_a_few_statements(company):
    """AC-12's guard: the board reads pipeline tasks in one query, labelled ones in another and the channels of
    unlabelled pipeline tasks in one more per 500, so 1,000 open tasks read in a few statements, not one per task."""
    from backend import cards as C

    with company.transaction() as c:
        _set_up(c)
        for n in range(1000):
            pipeline, stage = [
                ("growth-experiments", RUNNING),
                ("growth-monitoring", WATCHING),
                ("general", None),
            ][n % 3]
            _filed(c, f"Test {n}", pipeline, stage, labels=["channel:seo"] if n % 2 else [])
        statements = []
        c.set_trace_callback(statements.append)

        class Ctx:
            def __init__(self):
                self.c, self.memos = c, {}

            def visible(self):
                return "1"

            def memo(self, key, make):
                return self.memos.setdefault(key, make())

        start = time.perf_counter()
        tasks = C.marketing_tasks(Ctx())
        took = time.perf_counter() - start
        c.set_trace_callback(None)
    assert len(tasks) == 667
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) <= 4
    assert took < 2.0
