"""Product's Validate: no ask of a person without a passed Checked in production page, the card's OK and Not
validated, and the one-time sweep of tasks already in Validate (backend/validate_gate.py).

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
"""

import re

import pytest

from backend import answers as A
from backend import page_verdicts as V
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend import validate_gate as G
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _team

ANA, BEN, PM = X.ANA, "human:ben", "bot:pm"
CHECKED = "Checked on rc.13, phone and desktop width: the account row is 40 px and not clipped."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        _team(conn)
        _answers(conn, software="yes")
        PS.ensure_presets(conn)
        yield conn


def _in_validate(c, title="Drawer account row"):
    row = H.task_create(c, ANA, title, "Make the account row at least 40 px tall.", ANA, pipeline="product", lint=False)
    row = E.move(c, row["id"], G.STAGE, ANA, "Straight to Validate for this test")
    assert row["owner_id"] == PM
    return row


def _attach(c, row, file_id="f-check", version=1):
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=? AND kind='page'", (G.STAGE,)).fetchone()[0]
    c.execute("DELETE FROM requirement_pages WHERE visit_id=? AND requirement_id=?", (row["visit"]["id"], rid))
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], rid, file_id, version, "{}", PM, H.now()),
    )
    return E.task(c, row["id"])


def _jev(c, row, verdict="met", file_id="f-check", version=1):
    found = V.owe(c, file_id, version, "")
    c.execute("UPDATE page_verdicts SET verdict=?, reasons='' WHERE id=?", (verdict, found["id"]))
    return E.task(c, row["id"])


def _ask(c, row, by, kind, person, line, **kw):
    seen = E.task(c, row["id"])["version"]
    return E.add_requirement(c, row["id"], by, kind, person, line, seen_version=seen, **kw)


def _asks(c, row):
    return c.execute(
        "SELECT id FROM requirements WHERE visit_id=? AND by_bot=1 AND withdrawn_at IS NULL", (row["visit"]["id"],)
    ).fetchall()


def _standing(c, task_id, rid):
    row = E.task(c, task_id)
    return next(s for s in R.standings(c, row) if s.requirement["id"] == rid)


def _may(c, row, rid):
    """What the task page offers Ana on the ask."""
    return next(r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == rid)["may"]


def _on_person(c, task_id, person):
    return [w for w in W.of(c, E.task(c, task_id)) if w.state == W.PERSON and w.who == person]


# ----------------------------------------------------------------------------- the ask
def test_with_no_passed_page_a_bots_check_of_a_person_is_refused_and_nothing_reaches_them(c):
    row = _in_validate(c)
    for kind, choices in (("ok", None), ("answer", ["yes", "no"]), ("answer", ["Yes", "No", "Something else"])):
        with pytest.raises(H.Refused, match=re.escape(G.FIRST)):
            _ask(c, row, PM, kind, ANA, "Is the row 40 px on your phone?", choices=choices)
    with pytest.raises(H.Refused, match=re.escape(G.FIRST)):  # a free question is a check too
        _ask(c, row, PM, "answer", ANA, "How tall is the row on your phone?")
    # A page Jev sent back is no proof either.
    row = _jev(c, _attach(c, row), "not_met")
    with pytest.raises(H.Refused, match=re.escape(G.FIRST)):
        _ask(c, row, PM, "ok", ANA, CHECKED)
    assert _asks(c, row) == []
    assert _on_person(c, row["id"], ANA) == []


def test_a_decision_that_is_not_a_check_is_its_own_task_or_an_ok_for_that_action(c):
    row = _jev(c, _attach(c, _in_validate(c)), "unsure")
    for choices in (["Fly", "Render"], ["Fly", "Render", "Neither"]):
        with pytest.raises(H.Refused, match=re.escape(G.OWN_TASK)):
            _ask(c, row, PM, "answer", ANA, "Which host do we pay for?", choices=choices)
    # An OK bound to an action (task_asks.ask_ok) is that action's own: asked, and waiting on the person.
    out = _ask(c, row, PM, "ok", ANA, "OK to pay for the host?", for_action=True)
    c.execute(
        "INSERT INTO ok_grants (requirement_id, asked_by_id, asked_of_id, subject, content_hash, content, filed, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (out["requirement_id"], PM, ANA, "board:host", "h", "{}", 0, H.now()),
    )
    assert _standing(c, row["id"], out["requirement_id"]).on == ANA


def test_a_person_asks_whatever_they_like(c):
    row = _in_validate(c)
    out = _ask(c, row, ANA, "answer", BEN, "Does it look right to you?", choices=["yes", "no"])
    assert _standing(c, row["id"], out["requirement_id"]).on == BEN


def test_the_ok_shows_the_page_waits_on_jev_and_reaches_the_person_only_once_jev_passes_it(c):
    row = _attach(c, _in_validate(c))
    out = _ask(c, row, PM, "ok", ANA, CHECKED, shows=["file:f-shot@2"])
    rid = out["requirement_id"]
    assert R.get(c, rid)["shows"] == ["page:f-check@1", "file:f-shot@2"]  # the card opens the page first
    waiting = _standing(c, rid=rid, task_id=row["id"])
    assert (waiting.state, waiting.on) == (R.WAITING, R.JEV)
    assert _on_person(c, row["id"], ANA) == []
    # A yes/no beside it is a free question: the card is the OK.
    with pytest.raises(H.Refused, match=re.escape(G.FIRST)):
        _ask(c, row, PM, "answer", ANA, "Does it pass?", choices=["yes", "no"])
    # Nor from the task page: the person neither OKs it nor sends it back before Jev passed the page.
    assert not {"ok", "no"} & set(_may(c, row, rid))
    for value, notes in (("ok", None), ("no", "Looks clipped")):
        with pytest.raises(H.Refused, match=G.HELD):
            A.answer(c, row["id"], rid, ANA, value, notes)
    row = _jev(c, row)
    assert E.advance(c, row["id"]) is None  # Jev passing the page leaves the OK the bot asked
    asked = _standing(c, row["id"], rid)
    assert (asked.state, asked.on) == (R.WAITING, ANA)
    assert _on_person(c, row["id"], ANA)
    assert set(_may(c, row, rid)) >= {"ok", "no"}
    A.answer(c, row["id"], rid, ANA, "ok")
    assert E.task(c, row["id"])["status"] == "done"


def test_a_new_page_that_jev_sends_back_takes_the_ok_from_the_person_again(c):
    row = _jev(c, _attach(c, _in_validate(c)))
    # Passed with an OK already asked (the bot asked while Jev was checking): the page passed, so it waits on Ana.
    c.execute("UPDATE page_verdicts SET verdict=NULL")
    rid = _ask(c, row, PM, "ok", ANA, CHECKED)["requirement_id"]
    _jev(c, row)
    assert _standing(c, row["id"], rid).on == ANA
    row = _jev(c, _attach(c, E.task(c, row["id"]), version=2), "not_met", version=2)
    held = _standing(c, row["id"], rid)
    assert (held.state, held.on) == (R.NOT_MET, PM)
    assert _on_person(c, row["id"], ANA) == []


def test_not_validated_moves_the_task_back_with_the_note_and_files_new_engineering_work(c):
    row = _attach(c, _in_validate(c))
    rid = _ask(c, row, PM, "ok", ANA, CHECKED)["requirement_id"]
    row = _jev(c, row)
    with pytest.raises(H.Refused, match="task_why"):
        A.answer(c, row["id"], rid, ANA, "no")
    A.answer(c, row["id"], rid, ANA, "no", "The row is still clipped at 390 px.")
    after = E.task(c, row["id"])
    assert after["stage_id"] == G.BACK_TO
    moved = c.execute(
        "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' "
        "ORDER BY rowid DESC LIMIT 1",
        (row["id"],),
    ).fetchone()[0]
    assert moved == "Not validated: The row is still clipped at 390 px."
    (filed,) = c.execute(
        "SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff'", (row["id"],)
    ).fetchall()
    work = E.task(c, filed[0])
    assert work["pipeline_id"] == "engineering" and work["status"] == "open"
    assert "Sent back from Validate: Not validated: The row is still clipped at 390 px." in work["body"]


def test_a_passed_page_with_no_ask_goes_to_done_with_no_person(c):
    row = _jev(c, _attach(c, _in_validate(c)))
    assert E.advance(c, row["id"])["status"] == "done"
    assert not c.execute("SELECT 1 FROM answers WHERE by_id LIKE 'human:%'").fetchone()


# ----------------------------------------------------------------------------- the sweep at the release
def _old_ask(c, monkeypatch, row, kind, line, *, by=PM, person=ANA, choices=None, shows=None):
    """An ask made before the gate shipped."""
    with monkeypatch.context() as m:
        m.setattr(G, "check_ask", lambda conn, task, who, to, k, s, ch: s)
        m.setattr(G, "held", lambda conn, task, requirement: None)
        return _ask(c, row, by, kind, person, line, choices=choices, shows=shows)["requirement_id"]


def test_the_sweep_withdraws_bare_asks_once_and_leaves_a_persons_and_a_shown_passed_page(c, monkeypatch):
    bare = _in_validate(c, "Bare check")
    _old_ask(c, monkeypatch, bare, "answer", "On a phone, is the row 40 px?", choices=["yes", "no", "Something else"])
    decision = _in_validate(c, "Load gate host")
    _old_ask(c, monkeypatch, decision, "answer", "Which host do we pay for?", choices=["Fly", "Render"])
    passed = _jev(c, _attach(c, _in_validate(c, "Passed page"), "f-passed"), file_id="f-passed")
    _old_ask(c, monkeypatch, passed, "answer", "Does this pass?", choices=["yes", "no"])
    persons = _in_validate(c, "A person's ask")
    kept = _old_ask(c, monkeypatch, persons, "answer", "Ben, does it look right?", by=ANA, person=BEN)
    shown = _jev(c, _attach(c, _in_validate(c, "Shown page"), "f-shown"), file_id="f-shown")
    shown_ok = _old_ask(c, monkeypatch, shown, "ok", CHECKED, shows=["page:f-shown@1"])
    X.settled(c)

    assert sorted(G.sweep(c)) == sorted([bare["id"], decision["id"], passed["id"]])
    later = _old_ask(c, monkeypatch, bare, "answer", "And on desktop?", choices=["yes", "no"])
    assert G.sweep(c) == []  # once: a later ask is the gate's at the ask, not the sweep's
    assert R.get(c, later)["withdrawn_at"] is None

    for row, why in ((bare, G.FIRST), (decision, G.OWN_TASK)):
        after = E.task(c, row["id"])
        assert (after["status"], after["stage_id"]) == ("open", G.STAGE)
        assert [a for (a,) in _asks(c, row) if a != later] == []
        (comment,) = [m["body"] for m in H.task_comments(c, row["id"]) if why in (m.get("body") or "")]
        assert comment.startswith("Withdrew the question to ")
        assert [who for key, who in X.wakes(c, row["id"]) if key.endswith(G.SWEPT)] == [PM]
    assert _on_person(c, decision["id"], ANA) == []
    assert E.task(c, passed["id"])["status"] == "done"
    assert _standing(c, persons["id"], kept).on == BEN
    assert _standing(c, shown["id"], shown_ok).on == ANA
