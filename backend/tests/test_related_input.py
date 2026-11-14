"""Related input (backend/related_input.py, docs/pipelines-v3.md "Related input"): Jev scores the company's bots against
a task's pitch and spec, the top 1 to 2 at the bar are asked once to comment, and the stage is met on their comments,
when nobody clears the bar, or after its wait. It never blocks.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots. Jev is
a fake set on `related_input.ENGINE`, as the idea check's is.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as N
from backend import pipeline_settings as PS
from backend import related_input as RI
from backend import task_engine as E
from backend import task_wakes
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
OWNER, MANAGER, REVIEWER = "bot:owner", "bot:manager", "bot:reviewer"
BILLING, MAIL, DOCS = "bot:billing", "bot:mail", "bot:docs"
START = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
JOBS = {
    "owner": "Owns the refunds feature",
    "manager": "Manages the product pipeline",
    "reviewer": "Reviews pull requests",
    "billing": "Owns billing, invoices and refunds",
    "mail": "Owns the email the product sends",
    "docs": "Owns the help center",
}


def _stages(settings=None):
    return (
        {"name": "Spec", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {
            "name": "Related",
            "owner": "assignee",
            "requirements": [{"kind": "related_input", "settings": settings or {}}],
        },
        {"name": "OK spec", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review", "owner": "pick", "pick": {"bots": [REVIEWER]}, "requirements": [{"kind": "owner_done"}]},
    )


class Jev:
    """A fake Jev: each call's state and questions are kept, and each bot gets the score `says` gives its name (0.1
    when unnamed). With `fail`, every call raises."""

    def __init__(self, says=None, fail=False):
        self.calls = []
        self.says = says or {}
        self.fail = fail

    def __call__(self, state, questions, label):
        self.calls.append((state, questions, label))
        if self.fail:
            raise RI.J.JudgeError("unavailable", "down", retryable=True)
        answers = {}
        for qid in questions:
            name = state["bots"]["b" + qid.removeprefix("rel_")].split(":")[0]
            answers[qid] = {"noul": self.says.get(name, 0.1)}
        return {"answers": answers, "model": "fake-jev", "cost_usd": 0.0}


@pytest.fixture
def jev(monkeypatch):
    fake = Jev()
    monkeypatch.setattr(RI, "ENGINE", fake)
    return fake


def _company(store, settings=None, bots=("billing", "mail", "docs")):
    with store.transaction() as c:
        for slug in ("owner", "manager", "reviewer", *bots):
            X.bot(c, slug)
            c.execute("UPDATE bot_config SET description=? WHERE bot=?", (JOBS[slug], slug))
        X.pipeline(c, "features", "Features", _stages(settings), manager=MANAGER)


@pytest.fixture
def features(company, time_machine):
    time_machine.move_to(START, tick=False)
    _company(company)
    return company


def _related(store, title="Refund button", body="Let a customer refund an order from its page."):
    """A task the owner files, moved into Related input."""
    with store.transaction() as c:
        row = H.task_create(c, OWNER, title, body, OWNER, pipeline="features", lint=False)
        row = E.task(c, row["id"])
        X.say_done(c, row)
        E.advance(c, row["id"])
        row = E.task(c, row["id"])
        assert row["stage_id"] == "features-related"
        X.settled(c)
        return row


def _stage(store, row):
    with store.read() as c:
        return E.task(c, row["id"])["stage_id"]


def _events(store, row):
    with store.read() as c:
        return [
            (r[0], r[1])
            for r in c.execute(
                "SELECT new, note FROM history_events WHERE subject_type='task' AND subject_id=? "
                "AND kind='related_input' ORDER BY rowid",
                (row["id"],),
            ).fetchall()
        ]


def _asked(store, row):
    with store.read() as c:
        return sorted(r for _k, r in X.wakes(c, row["id"]) if ":related:" in _k)


def _beat(store):
    """One beat: the related input step, then the never-stuck pass."""
    done = RI.run(store)
    with store.transaction() as c:
        N.check(c)
    return done


def test_jev_scores_the_bots_and_the_top_two_over_the_bar_are_asked_once(features, jev):
    jev.says = {"billing": 0.95, "mail": 0.86, "docs": 0.83, "owner": 0.99, "reviewer": 0.99, "manager": 0.99}
    row = _related(features)
    with features.read() as c:
        (wait,) = W.of(c, E.task(c, row["id"]))
        assert (wait.state, wait.line) == (W.JEV, "Jev is picking related bots")
    _beat(features)
    (state, questions, label) = jev.calls[0]
    assert label == "related-bots@1" and len(jev.calls) == 1
    # Never asked about: the task's owner, the pipeline's manager and the bots that hold its stages (the reviewer).
    assert sorted(name.split(":")[0] for name in state["bots"].values()) == ["billing", "docs", "mail"]
    assert state["work"]["title"] == "Refund button" and "refund an order" in state["work"]["pitch"]
    assert all(q["type"] == "noul" for q in questions.values())
    assert _asked(features, row) == [BILLING, MAIL]
    assert _stage(features, row) == "features-related"
    with features.read() as c:
        saved = RI.state_of(c, row["id"])
        assert saved["picks"] == [BILLING, MAIL] and saved["outcome"] == RI.PICKED
        assert [(s["bot"], s["score"]) for s in saved["scores"]] == [(BILLING, 0.95), (MAIL, 0.86), (DOCS, 0.83)]
        assert saved["scores"][0]["why"] == JOBS["billing"]
        wake = c.execute(
            "SELECT body FROM messages WHERE wake_key LIKE ? AND recipient_id=?", (row["id"] + "%related%", BILLING)
        ).fetchone()[0]
        assert "comment on the task once from your area" in wake and f"tico task comment {row['id']}" in wake
        (wait,) = W.of(c, E.task(c, row["id"]))
        assert (wait.state, wait.line) == (W.JEV, "Waiting on comments from billing and mail")
    assert _events(features, row) == [("picked", "Related input: Jev picked billing (0.95), mail (0.86)")]
    with features.transaction() as c:
        # A pick has no turn on the task, yet its one wake may start its run; the rules that hold at all times hold.
        message = c.execute(
            "SELECT id FROM messages WHERE wake_key LIKE ? AND recipient_id=?", (row["id"] + "%related%", MAIL)
        ).fetchone()[0]
        assert task_wakes.may_start(c, message)
        assert N.rules_hold(c) == []
    _beat(features)
    assert len(jev.calls) == 1 and len(_asked(features, row)) == 2, "scored and asked once"


def test_the_bar_and_how_many_are_asked_are_the_stages_settings(company, time_machine, jev):
    time_machine.move_to(START, tick=False)
    _company(company, {"bar": 0.9, "most": 1})
    jev.says = {"billing": 0.95, "mail": 0.93}
    row = _related(company)
    _beat(company)
    assert _asked(company, row) == [BILLING]


def test_nobody_over_the_bar_is_met_at_once_and_the_task_moves_on(features, jev):
    jev.says = {"billing": 0.79, "mail": 0.4}
    row = _related(features)
    _beat(features)
    assert _asked(features, row) == []
    assert _stage(features, row) == "features-ok-spec"
    assert _events(features, row) == [
        ("none", "Related input: No bot cleared the bar (0.8); the closest was billing (0.79)")
    ]


def test_no_other_bot_is_met_at_once_without_asking_jev(company, time_machine, jev):
    time_machine.move_to(START, tick=False)
    _company(company, bots=())
    row = _related(company)
    _beat(company)
    assert jev.calls == []
    assert _stage(company, row) == "features-ok-spec"
    assert _events(company, row) == [("none", "Related input: No other bot to ask")]


def test_the_comments_meet_it_once_every_pick_has_commented(features, jev, time_machine):
    jev.says = {"billing": 0.95, "mail": 0.9}
    row = _related(features)
    _beat(features)
    time_machine.move_to(START + timedelta(minutes=20), tick=False)
    with features.transaction() as c:
        H.task_comment(c, BILLING, row["id"], "Refunds over $500 need a second look; say so in the spec.")
    _beat(features)
    assert _stage(features, row) == "features-related"
    with features.read() as c:
        (wait,) = W.of(c, E.task(c, row["id"]))
        assert wait.line == "Waiting on comments from mail"
    with features.transaction() as c:
        H.task_comment(c, MAIL, row["id"], "Send the refund receipt from the existing template.")
    _beat(features)
    assert _stage(features, row) == "features-ok-spec"
    assert _events(features, row)[-1] == ("done", "Related input: Comments from billing and mail")


def test_after_four_hours_it_is_met_with_a_note_and_never_blocks(features, jev, time_machine):
    jev.says = {"billing": 0.95, "mail": 0.9}
    row = _related(features)
    _beat(features)
    with features.transaction() as c:
        H.task_comment(c, BILLING, row["id"], "Fine from billing.")
    time_machine.move_to(START + timedelta(hours=3, minutes=59), tick=False)
    _beat(features)
    assert _stage(features, row) == "features-related"
    time_machine.move_to(START + timedelta(hours=4, minutes=1), tick=False)
    _beat(features)
    assert _stage(features, row) == "features-ok-spec"
    assert _events(features, row)[-1] == ("done", "Related input: 4 hours passed: no comment from mail")


def test_a_jev_that_fails_is_tried_again_then_the_stage_is_met(features, jev, time_machine):
    jev.fail = True
    row = _related(features)
    for minutes in (0, 2, 8, 30):
        time_machine.move_to(START + timedelta(minutes=minutes), tick=False)
        _beat(features)
    assert len(jev.calls) == RI.TRIES
    assert _asked(features, row) == []
    assert _stage(features, row) == "features-ok-spec"
    assert _events(features, row)[-1] == ("done", "Related input: Jev couldn't pick related bots, so nobody was asked")


def test_a_private_task_asks_only_the_bots_who_can_read_it(features, jev):
    jev.says = {"billing": 0.95, "mail": 0.9, "docs": 0.85}
    row = _related(features)
    with features.transaction() as c:
        c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA, MAIL]), row["id"]))
    _beat(features)
    (state, _questions, _label) = jev.calls[0]
    assert [name.split(":")[0] for name in state["bots"].values()] == ["mail"]
    assert _asked(features, row) == [MAIL]
    with features.read() as c:
        assert not H.task_private_readable(c, BILLING, E.task(c, row["id"])), "no reader was added"


def test_a_private_task_no_other_bot_reads_moves_on_with_a_note(features, jev):
    row = _related(features)
    with features.transaction() as c:
        c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA]), row["id"]))
    _beat(features)
    assert jev.calls == [] and _asked(features, row) == []
    assert _stage(features, row) == "features-ok-spec"
    assert _events(features, row) == [
        ("none", "Related input: No other bot can read this private task, so nobody was asked")
    ]


def test_the_spec_page_of_an_earlier_stage_is_what_jev_reads(features, jev):
    from backend.blobs import Blobs
    from backend.tests.helpers.page_requirement import _page

    features.blobs = Blobs(features.settings)
    row = _related(features)
    with features.transaction() as c:
        X.bot(c, "writer")  # the helper's page is the writer bot's file
        fid, version = _page(c, features, "<h1>Spec</h1><p>Refunds: what changes for billing.</p>")
        spec_visit = c.execute(
            "SELECT id FROM visits WHERE task_id=? AND stage_id='features-spec'", (row["id"],)
        ).fetchone()[0]
        requirement = c.execute("SELECT id FROM requirements WHERE stage_id='features-spec'").fetchone()[0]
        c.execute(
            "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, attached_by_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (H.new_id(), spec_visit, requirement, fid, version, OWNER, H.now()),
        )
    _beat(features)
    spec = jev.calls[0][0]["work"]["spec"]
    assert "Refunds: what changes for billing." in spec and "spec_truncated" not in jev.calls[0][0]["work"]


def test_settings_out_of_range_are_refused_at_save(company):
    assert RI.check({"bar": 0.8, "most": 2, "wait_hours": 4}, "Related") == []
    assert RI.check({"bar": 1.5, "most": 0, "wait_hours": 0}, "Related") == [
        "The bar on Related is a number over 0, up to 1.",
        "Most asked on Related is a whole number from 1 to 5.",
        "Wait on Related is a number of hours over 0, up to 72.",
    ]
    with company.transaction() as c:
        record = PS.preset(c, "general")
        record["stages"][0]["requirements"].append({"kind": "related_input", "settings": {"bar": 2, "color": "red"}})
        with pytest.raises(H.Refused) as refused:
            PS.save(c, E.SYSTEM, record)
    said = str(refused.value)
    assert "The bar on" in said or "has no setting color" in said
    assert RI.settings_of({"settings": {"bar": 2, "most": True}}) == (RI.BAR, RI.MOST, RI.WAIT_HOURS)


def test_the_migration_adds_the_kind_and_keeps_every_row():
    from importlib import import_module

    migration = import_module("backend.migrations.20261114075516_related_input")
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE requirements (id TEXT PRIMARY KEY, stage_id TEXT, "
        "kind TEXT NOT NULL CHECK (kind IN ('owner_done', 'ok', 'page', 'item', 'linked_done', 'answer')), "
        "person_id TEXT, CHECK (person_id IS NULL OR kind IN ('ok', 'page', 'answer')))"
    )
    conn.execute("CREATE INDEX requirements_stage ON requirements(stage_id)")
    conn.execute("INSERT INTO requirements VALUES ('r1', 's1', 'owner_done', NULL)")
    migration.up(conn)
    migration.up(conn)  # again: nothing changes
    conn.execute("INSERT INTO requirements VALUES ('r2', 's1', 'related_input', NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO requirements VALUES ('r3', 's1', 'related_input', 'human:ana')")
    assert [r[0] for r in conn.execute("SELECT id FROM requirements ORDER BY id")] == ["r1", "r2"]
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='requirements_stage'").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM related_inputs").fetchone()[0] == 0
