"""Product's Validate: no bot's OK of a person (the decision model passing the Checked in production page ends it), no
check asked without that page, the OK and Not validated of an OK a bot asked before that rule, a bot's ❓ to a person
refused there as an OK ask would be, and the one-time sweep of tasks already in Validate (backend/validate_gate.py). A
question is a message marked ❓ now (backend/question_marks.py): the engine refuses one as an ask
(backend/tests/test_question_writers.py).

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
"""

import re

import pytest

from backend import answers as A
from backend import page_verdicts as V
from backend import pipeline_settings as PS
from backend import question_marks as Q
from backend import task_engine as E
from backend import task_requirements as R
from backend import validate_gate as G
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _team, legacy_pair

ANA, BEN, PM = X.ANA, "human:ben", "bot:pm"
CHECKED = "Checked on rc.13, phone and desktop width: the account row is 40 px and not clipped."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        _team(conn)
        _answers(conn, software="yes")
        legacy_pair(conn)  # the pair's Validate, which sends back to In development
        PS.ensure_presets(conn)
        yield conn


def _in_validate(c, title="Drawer account row"):
    row = H.task_create(c, ANA, title, "Make the account row at least 40 px tall.", ANA, pipeline="product", lint=False)
    row = E.move(c, row["id"], G.STAGE, ANA, "Straight to Validate for this test")
    assert row["owner_id"] == PM
    return row


def _as_document(c, row, file_id, version):
    """The page is the task's Checked in production document, as attaching it to Validate makes it: a live page at
    `version`, linked to the task with that role."""
    now = H.now()
    c.execute(
        "INSERT OR IGNORE INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
        "last_activity_at) VALUES (?,?,?,?,?,'file','text/html','tico_blob',?,?)",
        (file_id, "pm", "bot", file_id, "Checked in production", now, now),
    )
    c.execute("UPDATE bot_files SET current_version=max(current_version, ?) WHERE id=?", (version, file_id))
    c.execute(
        "INSERT OR IGNORE INTO pages (file_id, cadence, created_at, updated_at) VALUES (?, 'none', ?, ?)",
        (file_id, now, now),
    )
    c.execute(
        "INSERT OR IGNORE INTO page_links (id, page_id, target, role, created_by, created_at) VALUES (?,?,?,?,?,?)",
        (H.new_id(), file_id, "task:" + row["id"], "Checked in production", PM, now),
    )


def _attach(c, row, file_id="f-check", version=1):
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=? AND kind='page'", (G.STAGE,)).fetchone()[0]
    _as_document(c, row, file_id, version)
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


def _asked_before(c, row, monkeypatch, **kw):
    """A bot's OK of Ana asked before Validate refused every bot's OK (NOBODY_ASKED), showing the page as such an OK
    did: it is still answered."""

    def then(conn, row, by, person, shows):
        page, _ = G.page_of(conn, row)
        return [G.ref(page)] if page else shows

    with monkeypatch.context() as m:
        m.setattr(G, "check_ask", then)
        m.setattr(E, "shows_a_file", lambda *_: True)  # the page is rows of the test's own (_attach), no file
        return _ask(c, row, PM, "ok", ANA, CHECKED, **kw)


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
    with pytest.raises(H.Refused, match=re.escape(G.FIRST)):
        _ask(c, row, PM, "ok", ANA, "Is the row 40 px on your phone?")
    # With a page, sent back or passed, no bot asks an OK: the decision model passing it ends the stage.
    row = _jev(c, _attach(c, row), "not_met")
    with pytest.raises(H.Refused, match=re.escape(G.NOBODY_ASKED)):
        _ask(c, row, PM, "ok", ANA, CHECKED)
    row = _jev(c, row)
    with pytest.raises(H.Refused, match=re.escape(G.NOBODY_ASKED)):
        _ask(c, row, PM, "ok", ANA, CHECKED)
    assert _asks(c, row) == []
    assert _on_person(c, row["id"], ANA) == []


def _marks(c, row):
    return c.execute("SELECT id FROM question_marks WHERE task_id=?", (row["id"],)).fetchall()


def test_a_bots_question_to_a_person_in_validate_is_refused_as_its_own_task_or_an_ok_of_the_page(c):
    row = _in_validate(c)
    said = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    # No passed page: a check or a decision, never a question to the person.
    with pytest.raises(H.Refused, match=re.escape(f"{G.FIRST} {G.OWN_TASK}")):
        Q.ask(c, PM, row["id"], ANA, "Is the row 40 px on your phone?")
    row = _jev(c, _attach(c, row), "not_met")
    with pytest.raises(H.Refused, match=re.escape(G.OWN_TASK)):
        Q.ask(c, PM, row["id"], ANA, "Which host should we pay for?")
    # Passed: the person OKs the page, on its card.
    row = _jev(c, row)
    with pytest.raises(H.Refused, match=re.escape(G.ASK_OK)):
        Q.ask(c, PM, row["id"], ANA, "Does the row look right to you?")
    assert _marks(c, row) == [] and c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == said


def test_a_question_validate_does_not_cover_is_let_through(c):
    row = _in_validate(c)
    # Only a bot's question to a person on a task in Validate: a person's, one to a bot, or another stage is not its.
    for by, person, stage in ((ANA, BEN, G.STAGE), (PM, "bot:engineer", G.STAGE), (PM, ANA, "product-spec")):
        assert not G.applies({**row, "stage_id": stage}, by, person)
        G.check_question(c, {**row, "stage_id": stage}, by, person)
    row = E.move(c, row["id"], G.BACK_TO, ANA, "Back for this test")
    mark = Q.ask(c, row["owner_id"], row["id"], ANA, "Is the row 40 px on your phone?")
    assert mark["person_id"] == ANA and _marks(c, row)


def test_an_ok_for_an_action_is_that_actions_own_and_not_held_behind_the_page(c):
    row = _jev(c, _attach(c, _in_validate(c)), "unsure")
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
    out = _ask(c, row, ANA, "ok", BEN, "Does it look right to you?")
    assert _standing(c, row["id"], out["requirement_id"]).on == BEN


def test_an_ok_asked_before_the_rule_waits_on_jev_and_reaches_the_person_only_once_jev_passes_it(c, monkeypatch):
    row = _attach(c, _in_validate(c))
    rid = _asked_before(c, row, monkeypatch)["requirement_id"]
    waiting = _standing(c, rid=rid, task_id=row["id"])
    assert (waiting.state, waiting.on) == (R.WAITING, R.JEV)
    assert _on_person(c, row["id"], ANA) == []
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


def test_the_oks_check_rows_are_what_the_decision_model_passed_on_the_page_not_the_bots_words(c, monkeypatch):
    """The Validate OK's verdict rows come from the page's passed check, never from what the bot wrote on the ask."""
    from backend import pipeline_judges as PJ

    row = _attach(c, _in_validate(c))
    rid = _asked_before(c, row, monkeypatch)["requirement_id"]

    def entry():
        return next(r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == rid)

    assert "checks" not in entry()  # Jev has not passed the page yet
    _jev(c, row)
    assert entry()["checks"] == [part.found for part in PJ.CHECKS["validate"]]
    assert all(entry()["checks"])


def test_a_new_page_that_jev_sends_back_takes_the_ok_from_the_person_again(c, monkeypatch):
    row = _jev(c, _attach(c, _in_validate(c)))
    # Passed with an OK already asked (the bot asked while Jev was checking): the page passed, so it waits on Ana.
    c.execute("UPDATE page_verdicts SET verdict=NULL")
    rid = _asked_before(c, row, monkeypatch)["requirement_id"]
    _jev(c, row)
    assert _standing(c, row["id"], rid).on == ANA
    row = _jev(c, _attach(c, E.task(c, row["id"]), version=2), "not_met", version=2)
    held = _standing(c, row["id"], rid)
    assert (held.state, held.on) == (R.NOT_MET, PM)
    assert _on_person(c, row["id"], ANA) == []


def test_not_validated_moves_the_task_back_with_the_note_and_files_new_engineering_work(c, monkeypatch):
    row = _attach(c, _in_validate(c))
    rid = _asked_before(c, row, monkeypatch)["requirement_id"]
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
    assert "Sent back from Validate: Not validated: The row is still clipped at 390 px." in H.task_words(c, work["id"])


def test_a_passed_page_with_no_ask_goes_to_done_with_no_person(c):
    """Validate's page is the task's Checked in production document, and still nobody is asked."""
    row = _in_validate(c)
    (requirement,) = [r for r in R.for_visit(c, row["visit"]) if r["kind"] == "page"]
    assert R.document_of(requirement) == "Checked in production"
    row = _jev(c, _attach(c, row))
    assert E.advance(c, row["id"])["status"] == "done"
    assert not c.execute("SELECT 1 FROM answers WHERE by_id LIKE 'human:%'").fetchone()
