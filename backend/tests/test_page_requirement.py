"""The page requirement (docs/pipelines-v3.md "The nine requirement kinds", 3): a page attached at a pinned version
(backend/task_pages.py), Jev's verdict on it (backend/page_verdicts.py, the Heartbeat's `jev_verdicts`), then the named
person's OK of that exact version (backend/answers.py). The lines are docs/dev/pipelines-v3-build.md's.

Synthetic rows only (backend/tests/helpers/engine.py). Jev is a fake engine (pipeline_judges.ENGINE); never a model.
"""

import json
import uuid

import pytest

from backend import answers as A
from backend import decide_groups as DG
from backend import item_metadata as IM
from backend import page_rounds as PR
from backend import page_verdicts as V
from backend import pipeline_judges as PJ
from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend.item_sources import github as F
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _stages, _task, _v

ANA, BEN = X.ANA, "human:ben"
WRITER, REVIEWER = "bot:writer", "bot:reviewer"


@pytest.fixture
def store(company):
    company.blobs = Blobs(company.settings)
    return company


@pytest.fixture
def c(store):
    with store.transaction() as conn:
        for slug in ("writer", "reviewer", "manager"):
            X.bot(conn, slug)
        yield conn


@pytest.fixture
def seeded(store):
    """For the Jev step, which opens its own reads and writes: the rows go in through a write of their own."""

    def seed(build):
        with store.transaction() as conn:
            for slug in ("writer", "reviewer", "manager"):
                X.bot(conn, slug)
            return build(conn)

    return seed


def _attach(c, row, ref, by=WRITER, requirement="Spec page"):
    seen = E.task(c, row["id"])["version"]
    return P.attach(c, row["id"], requirement, ref, by, seen_version=seen)


def _standing(c, row):
    (standing,) = R.standings(c, E.task(c, row["id"]))
    return standing


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


# ----------------------------------------------------------------------------- attach
def test_no_page_is_the_owners_turn_and_attaching_owes_jev_a_verdict(c, store):
    _pipeline(c)
    row = _task(c)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, WRITER, "No page attached")
    fid, _ = _page(c, store)
    _page(c, store, "<p>v2</p>", fid=fid)
    made = _attach(c, row, "page:" + fid)
    assert made["version"] == 2, "no version pins the newest"
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, R.JEV, "Jev is checking")
    owed = V.owed(c, H.shift(H.now(), seconds=1))
    assert [(o["file_id"], o["version"], o["rubric_hash"]) for o in owed] == [(fid, 2, made["rubric_hash"])]
    assert "page_attached" in X.events(c, row["id"])
    assert json.loads(made["judged_against"]) == {"rubric": "Spec page", "context": {"task": row["id"]}}


def test_attaching_again_replaces_the_page_and_the_same_page_twice_is_one(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    first = _attach(c, row, f"page:{fid}@1")
    assert _attach(c, row, f"page:{fid}@1")["id"] == first["id"]
    _page(c, store, "<p>v2</p>", fid=fid)
    second = _attach(c, row, f"page:{fid}@2")
    assert P.current(c, row["visit"]["id"], first["requirement_id"])["id"] == second["id"]
    assert H._one(c, "SELECT replaced_at FROM requirement_pages WHERE id=?", (first["id"],))["replaced_at"]


def test_attach_refuses_what_it_cannot_take(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    with _refused("task_requirement"):
        _attach(c, row, "page:" + fid, requirement="No such thing")
    with _refused("task_requirement"):
        _attach(c, row, f"page:{fid}@7")
    with _refused("task_requirement"):
        _attach(c, row, "page:file-" + "0" * 24)
    with _refused("task_requirement"):
        _attach(c, row, "file-abc")
    with _refused("task_changed"):
        P.attach(c, row["id"], "Spec page", "page:" + fid, WRITER)  # a bot says which version it saw
    with _refused("identity"):
        _attach(c, row, "page:" + fid, by=REVIEWER)
    assert _attach(c, row, "page:" + fid, by=BEN)["attached_by_id"] == BEN  # any person may
    E.move(c, row["id"], "product-closed", ANA, "Not now")
    with _refused("task_ended"):
        P.attach(c, row["id"], "Spec page", "page:" + fid, ANA)


# ----------------------------------------------------------------------------- Jev's verdict, then the person's OK
def test_jev_passed_with_no_person_named_meets_it_and_the_task_moves(c, store):
    _pipeline(c)
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "met")
    assert _standing(c, row).line == "Jev passed it"
    assert E.advance(c, row["id"])["stage"]["name"] == "Done"


def test_jev_sends_it_back_to_the_owner_with_the_reasons(c, store):
    _pipeline(c)
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "not_met", "No  acceptance\ncriteria.")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.NOT_MET,
        WRITER,
        "Jev sent it back: No acceptance criteria.",
    )


def test_after_jev_passes_the_named_person_oks_that_exact_version(c, store):
    rid = _pipeline(c, person=ANA)
    row = _task(c)
    fid, _ = _page(c, store)
    made = _attach(c, row, "page:" + fid)
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "ok")  # Jev has not looked yet
    _say(c, made, "met")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, ANA, "OK Ana (version 1)")
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, BEN, "ok")  # asked of Ana
    with _refused("task_why"):
        A.answer(c, row["id"], rid, ANA, "no")
    sent = A.answer(c, row["id"], rid, ANA, "no", "Say how errors show", page_version=f"{fid}@1")
    assert sent["stage"]["name"] == "Spec"
    assert _standing(c, row).line == f"Sent back: Say how errors show. {R.PAGE_OK_HINT}", "what the bot does next"
    assert H._one(c, "SELECT page_version FROM answers WHERE id=?", (sent["answer_id"],))["page_version"] == 1
    _page(c, store, "<p>v2 with errors</p>", fid=fid)
    second = _attach(c, row, "page:" + fid)
    assert _standing(c, row).line == "Jev is checking", "a new version starts the verdict and the OK over"
    _say(c, second, "met")
    assert _standing(c, row).line == 'Version 2, after the send back: "Say how errors show". OK Ana (version 2)', (
        "the send back was for version 1: the new version comes back marked with it"
    )
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{fid}@1")  # she was shown version 1
    done = A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{fid}@2")
    assert done["stage"]["name"] == "Done"


def test_an_ok_of_one_page_does_not_approve_another_page_at_the_same_version(c, store):
    rid = _pipeline(c, person=ANA)
    row = _task(c)
    shown, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + shown), "met")
    other, _ = _page(c, store, "<p>Another spec</p>")  # page B, also at version 1, replaces page A
    _say(c, _attach(c, row, "page:" + other), "met")
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{shown}@1")  # she was shown page A
    assert E.task(c, row["id"])["stage"]["name"] == "Spec"
    done = A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{other}@1")
    assert done["stage"]["name"] == "Done"


def test_the_named_persons_needs_you_has_the_page_at_the_version_to_ok(c, store):
    from backend import waiting_on as W

    _pipeline(c, person=ANA)
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "met")
    (item,) = [i for i in W.needs(c, ANA) if i["kind"] == "ask"]
    (ask,) = [r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == item["requirement_id"]]
    assert (ask["kind"], ask["shows"], ask["page"]["version"]) == ("page", [f"page:{fid}@1"], 1)
    assert sorted(ask["may"]) == ["no", "ok"]


def test_a_private_tasks_page_ok_falls_to_the_tasks_person_when_the_named_one_cannot_open_it(c, store):
    from backend import waiting_on as W

    _pipeline(c, person=BEN)
    row = _task(c)
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
    _say(c, _attach(c, row, "page:" + _page(c, store)[0]), "met")
    assert W.unreadable(c, E.task(c, row["id"]), R.standings(c, E.task(c, row["id"]))) == BEN


def _owned(c, owner):
    """A task whose stage is held by `owner` rather than the stage's own bot."""
    row = _task(c)
    E.reassign(c, row["id"], owner, ANA)
    return E.task(c, row["id"])


def _again(c, store, row, fid, verdict="unsure", reasons="Still can't tell", text="<p>v2</p>"):
    """The bot publishes a new version of the page and attaches it; Jev says `verdict` of it."""
    _page(c, store, text, fid=fid)
    return _say(c, _attach(c, row, "page:" + fid), verdict, reasons)


def test_unsure_on_a_bots_stage_is_the_pipelines_managers_to_decide_at_once(c, store):
    """Jev unsure of a page on a stage a bot holds: the manager bot looks once, with Jev's line naming the part and never
    the whole check, and decides with why. The page's bot is not sent back to rewrite it (task #351). No person."""
    rid = _pipeline(c, page_settings={"rubric": "Says what done looks like"})
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell if the scope is set.")
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.WAITING, "bot:manager")
    assert standing.line == f"{R.DECIDE} Jev is unsure: Can't tell if the scope is set."
    for who in (BEN, WRITER):
        with _refused("task_requirement"):
            A.answer(c, row["id"], rid, who, "ok", "Looks fine", seen_version=_v(c, row))
    with _refused("task_why"):
        A.answer(c, row["id"], rid, "bot:manager", "ok", seen_version=_v(c, row))  # a decision says why
    A.answer(c, row["id"], rid, "bot:manager", "no", "Name what is out of scope", seen_version=_v(c, row))
    assert _standing(c, row).line == "Sent back: Name what is out of scope"
    _again(c, store, row, fid, text="<p>v3</p>")
    assert _standing(c, row).on == "bot:manager", "unsure after the manager sent it back: the manager decides again"
    done = A.answer(
        c,
        row["id"],
        rid,
        "bot:manager",
        "ok",
        "Scope is in its second section; it meets the check",
        seen_version=_v(c, row),
    )
    assert done["stage"]["name"] == "Done"
    notes = [
        n[0]
        for n in c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer'", (row["id"],)
        )
    ]
    assert "Scope is in its second section; it meets the check" in notes, "the decision is on the task with its why"


def test_jevs_unsure_wakes_the_manager_with_the_line_not_the_bot(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c, page_settings={"rubric": "Says what done looks like"})
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.5, [], reason=""))
    V.run(store, H.shift(H.now(), seconds=1))

    def woken(who):
        with store.read() as r:
            return [
                m[0]
                for m in r.execute(
                    "SELECT body FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (who, row["id"] + ":%:jev:%")
                )
            ]

    assert woken(WRITER) == []
    (asked,) = woken("bot:manager")
    assert 'Jev is unsure of "Spec page": you decide it, as the pipeline\'s manager' in asked and R.DECIDE in asked
    assert "Says what done looks like" not in asked, "the line, never the whole check"


def test_unsure_goes_to_a_person_when_no_manager_bot_can_decide(c, store):
    """No manager bot, or the manager is the bot that holds the stage: a person decides, once per visit, as before."""
    rid = _pipeline(c)
    c.execute("UPDATE pipelines SET manager_id=NULL WHERE id='product'")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, None, "Jev is unsure: Can't tell"), (
        "no bot round"
    )
    A.answer(c, row["id"], rid, BEN, "no", "Set the scope")
    _again(c, store, row, fid, text="<p>v3</p>")
    assert (_standing(c, row).state, _standing(c, row).on) == (R.NOT_MET, WRITER), "a person decided one this visit"


def test_unsure_on_the_managers_own_stage_is_a_persons(c, store):
    _pipeline(c)
    row = _owned(c, "bot:manager")
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid, by="bot:manager"), "unsure", "Can't tell")
    assert (_standing(c, row).state, _standing(c, row).on) == (R.WAITING, None)


def test_unsure_on_a_persons_stage_is_theirs_at_once(c, store):
    _pipeline(c)
    row = _owned(c, BEN)
    _say(c, _attach(c, row, "page:" + _page(c, store)[0], by=BEN), "unsure", "Can't tell")
    assert (_standing(c, row).state, _standing(c, row).line) == (R.WAITING, "Jev is unsure: Can't tell")


def test_a_page_a_person_oks_anyway_goes_to_them_every_version_and_after_a_send_back_to_the_same_person(c, store):
    """Acme case 22 (Nov 11): a people-only page, Jev unsure, the person sent it back; the bot attached v2, Jev unsure
    again, and nobody was asked, so the Builder improvised its own asks. Now each version is the person's at once, and
    after a send back it is asked of the one who sent it back, marked with what they said."""
    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell")
    standing = _standing(c, row)
    assert (standing.state, standing.line) == (R.WAITING, "OK someone (version 1)"), "no bot round trip, no Jev line"
    A.answer(c, row["id"], rid, BEN, "no", "Round the result to 10 places")
    assert _standing(c, row).on == WRITER
    _again(c, store, row, fid)
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.WAITING, BEN), "asked again of the person who sent it back"
    assert standing.line == ('Version 2, after the send back: "Round the result to 10 places". OK Ben (version 2)')
    # The bot never asks its own OK of the page, or "OK to merge?" of it: Tico asks the person.
    page = [f"page:{fid}@2"]
    for kind, choices, shows in (("ok", None, page), ("ok", None, None), ("answer", ["Yes", "No"], page)):
        with _refused("Attach the new version; Tico asks the person"):
            E.add_requirement(
                c, row["id"], WRITER, kind, BEN, "OK to merge?", choices=choices, shows=shows, seen_version=_v(c, row)
            )
    # Nor a question of the person the page waits on (Tico Team, Nov 12): it goes on the page.
    with _refused("The stage already asks Ben to OK Spec page"):
        E.add_requirement(c, row["id"], WRITER, "answer", BEN, "Round half up or half even?", seen_version=_v(c, row))
    asked = E.add_requirement(
        c, row["id"], WRITER, "answer", ANA, "Round half up or half even?", seen_version=_v(c, row)
    )
    assert asked["requirement_id"], "a real question of someone else is still the bot's to ask"
    assert A.answer(c, row["id"], rid, BEN, "ok")["stage"]["name"] == "Spec", "the question is still open"


def test_a_page_a_person_oks_anyway_that_jev_sends_back_twice_is_the_persons_to_decide(c, store):
    """Initech (Nov 11): Jev sent back every version of a Go-live page a person OKs anyway, so the person could never OK
    it and the only way on was a push-past. Jev's first send back is the bot's to fix; a second in the same visit is the
    person's to decide, with Jev's reason, and their OK moves the task on. A page no person OKs stays the bot's."""
    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "not_met", "No prediction")
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.NOT_MET, WRITER), "the bot's one round first"
    _again(c, store, row, fid, verdict="not_met", reasons="Says no brand check passed")
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.WAITING, None)
    assert standing.line == "Jev sent it back again: Says no brand check passed"
    (ask,) = [r for r in H.task_view(c, row["id"], BEN)["to_leave"] if r["requirement_id"] == rid]
    assert sorted(ask["may"]) == ["no", "ok"], "the person may OK it"
    assert A.answer(c, row["id"], rid, BEN, "ok", "No brand card yet; the copy is fine")["stage"]["name"] != "Spec"


def test_a_page_no_person_oks_that_jev_sends_back_twice_stays_the_bots(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "not_met", "No prediction")
    _again(c, store, row, fid, verdict="not_met", reasons="Still no prediction")
    assert (_standing(c, row).state, _standing(c, row).on) == (R.NOT_MET, WRITER)


def test_a_plain_yes_no_question_on_a_stage_with_a_page_a_person_oks_is_allowed(c, store):
    """Lead's review of #3283: the Product Owner asking a person a clarifying yes/no in Spec is normal. Only a yes/no
    that shows the people-only page itself is refused (it would be a second OK of that page)."""
    po = "bot:product-owner"
    X.bot(c, "product-owner")
    _pipeline(c, who_oks="people_only")
    row = _owned(c, po)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid, by=po), "met")
    assert _standing(c, row).state == R.WAITING, "the person OKs the Spec page"
    with _refused("Attach the new version; Tico asks the person"):
        E.add_requirement(
            c,
            row["id"],
            po,
            "answer",
            BEN,
            "Is this spec OK?",
            choices=["Yes", "No"],
            shows=[f"page:{fid}@1"],
            seen_version=_v(c, row),
        )
    asked = E.add_requirement(
        c,
        row["id"],
        po,
        "answer",
        BEN,
        "Should negate accept strings?",
        choices=["Yes", "No"],
        seen_version=_v(c, row),
    )
    assert asked["requirement_id"], "a plain clarifying yes/no is the Product Owner's to ask"


def test_a_bot_does_not_ask_the_person_the_stages_page_ok_already_asks(c, store):
    """Tico Team rc.7 (Nov 12): the Product Owner attached the Spec page, which asks the person to OK it, and also asked
    them "Please OK the attached spec" as a question of its own. Needs you showed two items and Spec waited on both.
    The question is refused while Jev checks the page and once it waits on the person; a question of anyone else still
    works; the page OK alone moves the task."""
    po = "bot:product-owner"
    X.bot(c, "product-owner")
    rid = _pipeline(c, person=BEN)
    row = _owned(c, po)
    fid, _ = _page(c, store)
    page_row = _attach(c, row, "page:" + fid, by=po)
    for choices in (None, ["Looks good", "Needs changes"]):
        with _refused("The stage already asks Ben to OK Spec page. Do not ask them again"):
            E.add_requirement(
                c,
                row["id"],
                po,
                "answer",
                BEN,
                "Please OK the attached spec.",
                choices=choices,
                seen_version=_v(c, row),
            )
    _say(c, page_row, "met")
    assert (_standing(c, row).state, _standing(c, row).on) == (R.WAITING, BEN)
    with _refused("The stage already asks Ben to OK Spec page"):
        E.add_requirement(c, row["id"], po, "answer", BEN, "Please OK the attached spec.", seen_version=_v(c, row))
    assert [s.requirement["id"] for s in R.unmet(c, E.task(c, row["id"]))] == [rid], "one thing waits: the page OK"
    assert A.answer(c, row["id"], rid, BEN, "ok")["stage"]["name"] != "Spec", "the page OK alone moves it"


def test_a_bots_question_of_someone_else_while_the_page_ok_waits_still_works(c, store):
    po = "bot:product-owner"
    X.bot(c, "product-owner")
    _pipeline(c, person=BEN)
    row = _owned(c, po)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid, by=po), "met")
    asked = E.add_requirement(c, row["id"], po, "answer", ANA, "Which importer first?", seen_version=_v(c, row))
    assert asked["requirement_id"]


def test_a_bots_question_of_a_person_no_stage_ok_waits_on_still_works(c, store):
    """Before a page is attached nothing waits on the person: the Product Owner's clarifying question is asked."""
    po = "bot:product-owner"
    X.bot(c, "product-owner")
    _pipeline(c, person=BEN)
    row = _owned(c, po)
    asked = E.add_requirement(c, row["id"], po, "answer", BEN, "Should negate accept strings?", seen_version=_v(c, row))
    assert asked["requirement_id"]


def test_after_a_send_back_a_met_page_a_person_oks_is_asked_of_the_same_person(c, store):
    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "met")
    A.answer(c, row["id"], rid, BEN, "no", "Add the rounding")
    _again(c, store, row, fid, verdict="met")
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.WAITING, BEN)
    assert standing.line.startswith('Version 2, after the send back: "Add the rounding". OK '), standing.line


def test_a_new_version_asked_of_a_person_waits_from_when_it_was_asked_not_the_stage_start(c, store):
    """Acme rc.4: both version-2 asks showed `since` as stage entry. The wait counts from Jev's verdict on that version
    (when it was asked), so Needs you ages it from then."""
    from backend import waiting_on as W

    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    long_ago = H.shift(H.now(), hours=-5)
    c.execute("UPDATE visits SET entered_at=? WHERE id=?", (long_ago, row["visit"]["id"]))
    fid, _ = _page(c, store)
    first = _say(c, _attach(c, row, "page:" + fid), "met")
    (wait,) = [w for w in W.of(c, E.task(c, row["id"])) if w.requirement_id == rid]
    assert wait.since == first["updated_at"] and wait.since > long_ago
    A.answer(c, row["id"], rid, BEN, "no", "Add the rounding")
    c.execute("UPDATE page_verdicts SET updated_at=? WHERE id=?", (H.shift(H.now(), hours=-4), first["id"]))
    second = _again(c, store, row, fid, verdict="met")
    (item,) = [i for i in W.needs(c, BEN) if i.get("requirement_id") == rid]
    assert item["since"] == second["updated_at"], "the version-2 ask counts from when it was asked"


def test_the_manager_asked_to_decide_is_told_both_answers_and_its_move_on_is_its_ok(c, store):
    """Acme rc.4: the Product pipeline's manager, asked to decide, moved the task on with a reason; it was kept as a push
    past with no answer. Its wake names the two answers, and its move with a reason is kept as its OK of that version."""
    from backend import task_wakes as TW

    rid = _pipeline(c, page_settings={"rubric": "Says what done looks like"})
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell")
    _again(c, store, row, fid)
    assert R.asked_to_decide(_standing(c, row), "bot:manager")
    told = TW.body(c, E.task(c, row["id"]), to="bot:manager")
    assert f'tico task answer {row["id"]} {rid} ok -m "<why it meets the check>" --version N' in told, told
    assert f'tico task answer {row["id"]} {rid} no -m "<what to change>" --version N' in told
    assert "tico task answer" not in TW.body(c, E.task(c, row["id"]), to=WRITER), "only the one asked to decide"
    visit = E.task(c, row["id"])["visit"]["id"]
    moved = E.move(c, row["id"], E.NEXT, "bot:manager", "Scope is in its second section", seen_version=_v(c, row))
    assert moved["stage"]["name"] == "Done"
    kept = [
        tuple(a) for a in c.execute("SELECT by_id, answer, notes, page_version FROM answers WHERE visit_id=?", (visit,))
    ]
    assert kept == [("bot:manager", "ok", "Scope is in its second section", 2)], "its decision, not a push past"
    how = c.execute("SELECT how FROM visits WHERE id=?", (visit,)).fetchone()[0]
    assert how == "met"


def test_a_bot_not_asked_to_decide_still_pushes_past_with_a_reason(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell")
    _again(c, store, row, fid)
    visit = E.task(c, row["id"])["visit"]["id"]
    E.move(c, row["id"], E.NEXT, WRITER, "Shipping it", seen_version=_v(c, row))
    kept = [tuple(a) for a in c.execute("SELECT by_id, answer FROM answers WHERE visit_id=?", (visit,))]
    assert kept == [(WRITER, "pushed_past")]


def test_a_failed_check_on_a_bots_stage_is_the_pipelines_managers_to_decide_at_once(c, store):
    """Nov 11 (two sign-offs rule): Jev could not check, after its retries, on a stage a bot holds. The page may be
    fine and only Jev was down, so there is no bot round: the manager bot decides it, with why. No person."""
    rid = _pipeline(c, page_settings={"rubric": "Says what done looks like"})
    row = _task(c)
    _say(c, _attach(c, row, "page:" + _page(c, store)[0]), "unsure", "no model", failed=True)
    standing = _standing(c, row)
    assert (standing.state, standing.on) == (R.WAITING, "bot:manager")
    assert standing.line == f"{R.DECIDE} Jev couldn't check this: no model."
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, BEN, "ok")
    with _refused("task_why"):
        A.answer(c, row["id"], rid, "bot:manager", "ok", seen_version=_v(c, row))
    done = A.answer(c, row["id"], rid, "bot:manager", "ok", "Says done is a nightly run", seen_version=_v(c, row))
    assert done["stage"]["name"] == "Done"


def test_a_failed_check_on_the_managers_own_stage_is_a_persons(c, store):
    _pipeline(c)
    row = _owned(c, "bot:manager")
    _say(c, _attach(c, row, "page:" + _page(c, store)[0], by="bot:manager"), "unsure", "no model", failed=True)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, None, "Jev couldn't check this: no model")


def test_a_failed_check_on_a_page_a_person_oks_anyway_is_theirs(c, store):
    _pipeline(c, who_oks="people_only")
    row = _task(c)
    _say(c, _attach(c, row, "page:" + _page(c, store)[0]), "unsure", "no model", failed=True)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, None, "OK someone (version 1)")


def test_a_failed_check_goes_to_a_person_once_per_visit_with_no_manager_bot(c, store):
    rid = _pipeline(c)
    c.execute("UPDATE pipelines SET manager_id=NULL WHERE id='product'")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "no model", failed=True)
    assert (_standing(c, row).state, _standing(c, row).on) == (R.WAITING, None), "no manager bot: a person"
    A.answer(c, row["id"], rid, BEN, "no", "Set the scope")
    _page(c, store, "<p>v2</p>", fid=fid)
    second = _attach(c, row, "page:" + fid)
    _say(c, second, "unsure", "Still can't tell", failed=True)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.NOT_MET,
        WRITER,
        "Jev couldn't check this: Still can't tell",
    ), "a person already decided one this visit: the next goes back to the owner, not to their list"


def test_a_failed_check_is_a_persons_to_decide_and_their_ok_meets_it(c, store):
    rid = _pipeline(c)
    c.execute("UPDATE pipelines SET manager_id=NULL WHERE id='product'")
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "unsure", "no model", failed=True)
    assert _standing(c, row).line == "Jev couldn't check this: no model", (
        "the failure says why: it was Jev, not the page"
    )
    assert A.answer(c, row["id"], rid, BEN, "ok")["stage"]["name"] == "Done"


def _link_pr(c, task_id, repo, number, head):
    now = H.now()
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (H.new_id(), task_id, "pr", f"https://github.com/{repo}/pull/{number}", repo, number, f"{repo}#{number}", now),
    )
    _push(c, repo, number, head)


def _push(c, repo, number, head, state="open", merged_sha=None):
    key = f"{repo}#{number}"
    old = (IM.get(c, "github", "pr", key) or {}).get("fields") or {}
    IM.save(c, "github", "pr", key, {**old, "state": state, "head_sha": head, "merged_sha": merged_sha}, 1)


def test_a_review_page_counts_only_for_the_newest_commit(c, store):
    _pipeline(c, page_settings={"at_head": True, "rubric": "Reviewed"}, line="Review")
    row = _task(c)
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (H.new_id(), row["id"], "pr", "https://github.com/acme/app/pull/12", "acme/app", 12, "acme/app#12", H.now()),
    )
    fid, _ = _page(c, store)
    with _refused("Tico is still reading acme/app#12"):
        _attach(c, row, "page:" + fid, requirement="review")
    _push(c, "acme/app", 12, "aaa")
    made = _attach(c, row, "page:" + fid, requirement="review")
    assert json.loads(made["item_versions"]) == {"acme/app#12": "aaa"}
    assert json.loads(made["judged_against"])["context"] == {"task": row["id"], "heads": {"acme/app#12": "aaa"}}
    _say(c, made, "met")
    assert _standing(c, row).met
    _push(c, "acme/app", 12, "bbb")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, WRITER, "New commits since the review")
    again = _attach(c, row, "page:" + fid, requirement="review")
    assert again["rubric_hash"] != made["rubric_hash"], "the same page for a new commit is a new verdict"
    assert _standing(c, row).line == "Jev is checking"


def _in_qa(c, store, *more):
    """Review (Reviewer's page at 62de57f, Jev passed it), then QA: the Writer (the Builder) owns it, and a person
    OKs its Pre-release page (people only). `more`: QA's other requirements. Returns (task, Pre-release id)."""
    made = X.pipeline(
        c,
        "eng",
        "Engineering",
        (
            {
                "name": "Review",
                "owner": REVIEWER,
                "requirements": [{"kind": "page", "line": "Review", "settings": {"at_head": True}}],
            },
            {
                "name": "QA",
                "owner": WRITER,
                "requirements": [{"kind": "page", "line": "Pre-release", "who_oks": "people_only"}, *more],
            },
        ),
        manager="bot:manager",
    )
    rid = made["requirements"]["QA"][0]
    row = X.new_task(c, ANA, "Absolute value", "eng", assignee=REVIEWER)
    _link_pr(c, row["id"], "acme/app", 45, "62de57f623fc")
    review, _ = _page(c, store, "<p>Reviewed 62de57f</p>")
    _say(c, _attach(c, row, "page:" + review, by=REVIEWER, requirement="Review"), "met")
    assert E.advance(c, row["id"])["stage"]["name"] == "QA"
    assert R.unreviewed(c, E.task(c, row["id"])) is None, "the head is the reviewed one"
    return row, rid


def _pre_release(c, store, row, verdict="met", reasons="", fid=None):
    """The Builder's Pre-release page and Jev's verdict on it, nudging the task as the Jev step does."""
    fid, _ = _page(c, store, "<p>Built and checked</p>", fid=fid)
    said = _say(c, _attach(c, row, "page:" + fid, requirement="Pre-release"), verdict, reasons)
    V._nudge(c, said, V._waiting(c, said))
    return fid


def _bot(c, row, rid, by, value, notes=None):
    """A bot's answer, at the task's version it saw."""
    return A.answer(c, row["id"], rid, by, value, notes, seen_version=E.task(c, row["id"])["version"])


def _review_wakes(c, row):
    return [(key, to) for key, to in X.wakes(c, row["id"]) if ":review_new:" in key]


def test_commits_after_review_2_are_reviewed_by_a_bot_before_the_person_is_asked(c, store, monkeypatch):
    """Live, rc.7 (Nov 12, 00:48Z): the Builder pushed dce5a62 in QA, after both reviews; Jev was unsure (p 0.51)
    and the pre-release OK went to the person with no bot's review of that commit, which broke an acceptance criterion.
    Now the bot that wrote the newest review reads only the new commits first; the person is asked after it OKs them."""
    from backend import task_wakes as TW
    from backend import waiting_on as W

    row, rid = _in_qa(c, store)
    _push(c, "acme/app", 45, "dce5a62aa000")
    told = TW.body(c, E.task(c, row["id"]), to=WRITER)
    assert R.NOT_REVIEWED not in told, "the reviewer reviews them: the Builder's page need not say so"
    _pre_release(c, store, row, "unsure", "Can't tell")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, REVIEWER), "not the person's yet"
    assert standing.line.startswith(f"{R.REVIEW_NEW} since the reviews (now at dce5a62): read only them")
    assert W.turn(c, E.task(c, row["id"])) == REVIEWER
    ((key, to),) = _review_wakes(c, row)
    assert to == REVIEWER and key.endswith(":review_new:acme/app#45@dce5a62aa000")
    body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
    assert body.startswith(TW.REVIEW_NEW_LEAD) and f"tico task answer {row['id']} {rid} ok -m" in body
    with _refused("This is asked of"):
        A.answer(c, row["id"], rid, BEN, "ok")  # no person OK before the review
    with _refused("Say which commits you read"):
        _bot(c, row, rid, REVIEWER, "ok")
    _bot(c, row, rid, REVIEWER, "ok", "Read dce5a62: sign() fixed, the tests cover 0.")
    assert R.unreviewed(c, E.task(c, row["id"])) is None, "the reviewer's OK names the commit it read"
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, None), "now any person's"
    assert standing.line == (
        "reviewer reviewed the commits since the reviews: Read dce5a62: sign() fixed, the tests cover 0. "
        "OK someone (version 1)"
    )
    # The decision view draws the page's rounds instead of this line: the bot's read stays on the face as a notice,
    # and so does a merge conflict the person's OK hands to the Builder (as Needs you adds it to the line).
    read = "reviewer reviewed the commits since the reviews: Read dce5a62: sign() fixed, the tests cover 0."
    assert standing.notices == (read,)
    assert PR.notices(c, row["id"], rid) == [read]
    from backend import pr_conflicts as PC

    held = R.Standing({"id": "conflict:x", "kind": PC.KIND}, R.NOT_MET, WRITER)
    standings = R.standings
    monkeypatch.setattr(R, "standings", lambda conn, task: [held, *standings(conn, task)])
    monkeypatch.setattr(PC, "held", lambda s: s is held)
    assert PR.notices(c, row["id"], rid) == [read, "The PR has a merge conflict: writer fixes it after your OK."]
    monkeypatch.undo()
    A.answer(c, row["id"], rid, BEN, "ok")
    assert PR.notices(c, row["id"], rid) == [], "nothing to say once it is OK'd"
    assert E.task(c, row["id"])["stage"]["name"] != "QA"


def test_a_qa_with_no_new_commits_goes_straight_to_the_person(c, store):
    row, _ = _in_qa(c, store)
    _pre_release(c, store, row)
    standing = _f2(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, None, "OK someone (version 1)")
    assert _review_wakes(c, row) == []


def test_the_reviewers_send_back_is_the_builders_and_after_two_rounds_the_person_is_asked_anyway(c, store):
    row, rid = _in_qa(c, store)
    _push(c, "acme/app", 45, "bbbbbbb00000")
    _pre_release(c, store, row)
    _bot(c, row, rid, REVIEWER, "no", "offline.length === computers.length became offline.length.")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.NOT_MET, WRITER)
    assert standing.line == (
        "reviewer sent back the new commits: offline.length === computers.length became offline.length."
    )
    assert any(to == WRITER and "sent this back" in body for to, body in _bodies(c, row))
    # The Builder's fix is round 2: the reviewer again.
    _push(c, "acme/app", 45, "ccccccc00000")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, REVIEWER)
    assert standing.line.startswith(f"{R.REVIEW_NEW} since the reviews (now at ccccccc)")
    _bot(c, row, rid, REVIEWER, "no", "Still off by one.")
    assert _f2(c, row).on == WRITER
    # Two rounds this visit: the next commits go to the person, who is told no bot reviewed them.
    _push(c, "acme/app", 45, "ddddddd00000")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, None)
    assert standing.line == (f"New commits since the reviews (now at ddddddd): {R.NOT_REVIEWED} OK someone (version 1)")


def _oked_at_bbbbbbb(c, store):
    """QA with the Builder's own "done" still to come, so the task stays: bbbbbbb pushed after the review, read and
    OK'd by the reviewer, then Ben's OK. Returns (task, Pre-release id, done id)."""
    row, rid = _in_qa(c, store, {"kind": "owner_done"})
    done_id = next(r["id"] for r in R.for_visit(c, E.task(c, row["id"])["visit"]) if r["kind"] == "owner_done")
    _push(c, "acme/app", 45, "bbbbbbb00000")
    _pre_release(c, store, row)
    _bot(c, row, rid, REVIEWER, "ok", "Read bbbbbbb.")
    A.answer(c, row["id"], rid, BEN, "ok")
    assert _f2(c, row).met
    return row, rid, done_id


def _ben_asked(c, row):
    from backend import waiting_on as W

    return [n for n in W.needs(c, BEN) if n["task_id"] == row["id"]]


def test_a_rebase_after_the_ok_keeps_it_and_asks_nobody(c, store):
    """Velocity (~100 PRs a day, Nov 12): main moves under an OK'd PR and the Builder rebases. The new commit changes
    the same lines, so Ben's OK stands: no bot's read, no person asked."""
    row, rid, done_id = _oked_at_bbbbbbb(c, store)
    _push(c, "acme/app", 45, "ccccccc00000")
    standing = _f2(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.WAITING,
        "github",
        "Reading what the commits since Ben's OK change",
    )
    F.save_diff(c, "acme/app", "bbbbbbb00000", "d1")
    F.save_diff(c, "acme/app", "ccccccc00000", "d1")
    standing = _f2(c, row)
    assert standing.met and standing.line == "OK'd by Ben; the commits since change the same lines"
    assert not _ben_asked(c, row) and not [k for k, _ in _review_wakes(c, row) if k.endswith("@ccccccc00000")]
    _bot(c, row, done_id, WRITER, "done")
    assert E.task(c, row["id"])["stage"]["name"] != "QA"


def test_a_conflict_fix_after_the_ok_is_read_by_the_reviewer_and_asks_nobody(c, store):
    row, rid, done_id = _oked_at_bbbbbbb(c, store)
    _push(c, "acme/app", 45, "ccccccc00000")
    F.save_diff(c, "acme/app", "bbbbbbb00000", "d1")
    F.save_diff(c, "acme/app", "ccccccc00000", "d2")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, REVIEWER)
    assert standing.line.startswith(f"{R.REVIEW_NEW} since the reviews (now at ccccccc)")
    _bot(c, row, done_id, WRITER, "done")
    assert E.task(c, row["id"])["stage"]["name"] == "QA", "the OK'd commit is not this one"
    _bot(c, row, rid, REVIEWER, "ok", "Read ccccccc: the conflict in sign() kept both changes.")
    assert not _ben_asked(c, row)
    assert E.task(c, row["id"])["stage"]["name"] != "QA", "Ben's OK stands on the reviewer's"


def test_a_send_back_of_the_commits_after_the_ok_asks_the_person_again_after_the_next_read(c, store):
    row, rid, done_id = _oked_at_bbbbbbb(c, store)
    _push(c, "acme/app", 45, "ccccccc00000")
    _bot(c, row, done_id, WRITER, "done")
    F.save_diff(c, "acme/app", "bbbbbbb00000", "d1")
    F.save_diff(c, "acme/app", "ccccccc00000", "d2")
    _bot(c, row, rid, REVIEWER, "no", "The conflict fix dropped the 0 case.")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.NOT_MET, WRITER)
    assert standing.line == "reviewer sent back the new commits: The conflict fix dropped the 0 case."
    assert not _ben_asked(c, row)
    _push(c, "acme/app", 45, "ddddddd00000")
    F.save_diff(c, "acme/app", "ddddddd00000", "d3")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, REVIEWER), "round 2 since the OK"
    _bot(c, row, rid, REVIEWER, "ok", "Read ddddddd: 0 is back.")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, BEN), "the person whose OK it was"
    assert standing.line == (
        "New commits since Ben's OK. reviewer reviewed the commits since the reviews: Read ddddddd: 0 is back. "
        "OK Ben (version 1)"
    )
    assert E.task(c, row["id"])["stage"]["name"] == "QA"
    A.answer(c, row["id"], rid, BEN, "ok")
    assert E.task(c, row["id"])["stage"]["name"] != "QA"


def test_a_page_published_again_with_no_new_commits_keeps_the_persons_ok(c, store):
    """Live, Nov 13 (#3584): Chris OK'd the Fixes 2 page, then the Builder published the same page again with the PR's
    head unchanged, and Chris was asked "OK Chris (version 3)" again. The OK was for that code: a new version of the
    same page at the same commits keeps it, and nobody is asked."""
    row, rid, done_id = _oked_at_bbbbbbb(c, store)
    fid = P.current(c, E.task(c, row["id"])["visit"]["id"], rid)["file_id"]
    _pre_release(c, store, row, fid=fid)
    standing = _f2(c, row)
    assert standing.met and standing.line == "OK'd by Ben on version 1; no new commits since"
    assert not _ben_asked(c, row)
    _bot(c, row, done_id, WRITER, "done")
    assert E.task(c, row["id"])["stage"]["name"] != "QA"


def test_a_carried_ok_writes_a_history_line_and_tells_the_bot(c, store):
    """Validate, rc.31 (#266's sequence, criterion 2): the OK carried to the new version, but nothing said so. Now the
    task's history says whose OK carried from which version to which, and the attach reply tells the bot the OK at that
    commit stands and the person is not asked again."""
    row, rid, _ = _oked_at_bbbbbbb(c, store)
    fid = P.current(c, E.task(c, row["id"])["visit"]["id"], rid)["file_id"]
    fid, _ = _page(c, store, "<p>Built and checked, with the demo steps</p>", fid=fid)
    made = _attach(c, row, "page:" + fid, requirement="Pre-release")
    assert made["said"] == "Ben's OK at bbbbbbb stands. This version is kept on the task and doesn't ask Ben again."
    (line,) = c.execute(
        "SELECT actor_id, new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='ok_kept'",
        (row["id"],),
    ).fetchall()
    assert tuple(line) == ("keeper", "Ben's OK (version 1) carried to version 2: same commits")
    assert not _ben_asked(c, row)


def test_a_page_attached_after_new_commits_says_nothing_of_an_ok(c, store):
    """No carry, no line: a new version after a new commit is the person's to OK again."""
    row, rid, _ = _oked_at_bbbbbbb(c, store)
    fid = P.current(c, E.task(c, row["id"])["visit"]["id"], rid)["file_id"]
    _push(c, "acme/app", 45, "ccccccc00000")
    fid, _ = _page(c, store, "<p>Built and checked again</p>", fid=fid)
    made = _attach(c, row, "page:" + fid, requirement="Pre-release")
    assert "said" not in made
    assert "ok_kept" not in X.events(c, row["id"])


def test_the_person_asked_again_is_told_how_many_commits_came_since_their_ok(c, store):
    """Validate, rc.31 (criterion 4): Ben was asked again with only "New commits since Ben's OK". The card now says how
    many commits came, the newest one's title and how many files they changed, from what GitHub's compare gave for the
    commit Ben OK'd and the newest one."""
    row, rid, done_id = _oked_at_bbbbbbb(c, store)
    old = [["a" * 40, "Absolute value"]]
    F.save_diff(c, "acme/app", "bbbbbbb00000", "d1", commits=old, files={"abs.py": "h1", "test_abs.py": "t1"})
    _push(c, "acme/app", 45, "ccccccc00000")
    _bot(c, row, done_id, WRITER, "done")
    F.save_diff(c, "acme/app", "ccccccc00000", "d2")
    _bot(c, row, rid, REVIEWER, "no", "The conflict fix dropped the 0 case.")
    _push(c, "acme/app", 45, "ddddddd00000")
    new = [
        *old,
        ["c" * 40, "Handle the 0 case\n\nThe conflict fix dropped it."],
        ["d" * 40, "card shows a pick wait's line"],
    ]
    F.save_diff(c, "acme/app", "ddddddd00000", "d3", commits=new, files={"abs.py": "h2", "test_abs.py": "t1"})
    _bot(c, row, rid, REVIEWER, "ok", "Read ddddddd: 0 is back.")
    standing = _f2(c, row)
    assert (standing.state, standing.on) == (R.WAITING, BEN)
    assert standing.line == (
        "2 new commits since your OK at bbbbbbb: 'card shows a pick wait's line' and 1 more, 1 file. "
        "reviewer reviewed the commits since the reviews: Read ddddddd: 0 is back. OK Ben (version 1)"
    )


def test_another_page_after_the_ok_is_asked_again(c, store):
    """The OK carries only for the same page: another file at the same commits is a person's to OK again."""
    row, _, _ = _oked_at_bbbbbbb(c, store)
    _pre_release(c, store, row)  # a new file replaces the OK'd one
    standing = _f2(c, row)
    assert standing.state == R.WAITING and standing.line.endswith("OK someone (version 1)")


def test_a_page_published_again_after_new_commits_is_asked_again(c, store):
    """The OK carries only at the commits it was given at: a new version after a new commit is asked again."""
    row, rid, _ = _oked_at_bbbbbbb(c, store)
    fid = P.current(c, E.task(c, row["id"])["visit"]["id"], rid)["file_id"]
    _push(c, "acme/app", 45, "ccccccc00000")
    F.save_diff(c, "acme/app", "bbbbbbb00000", "d1")
    F.save_diff(c, "acme/app", "ccccccc00000", "d2")
    _bot(c, row, rid, REVIEWER, "ok", "Read ccccccc.")
    assert _f2(c, row).met, "the reviewer's read keeps Ben's OK on version 1"
    _pre_release(c, store, row, fid=fid)
    standing = _f2(c, row)
    assert (standing.state, standing.met) == (R.WAITING, False)
    assert standing.line.endswith("OK someone (version 2)")


def test_an_ok_brought_back_from_merge_is_not_carried_to_a_newer_version(c, store):
    """An OK carried back from Merge (`page_for`) is left to the rules for a task back from Merge: the carry-over to a
    newer version of the same page applies only on the visit where the person gave it."""
    made = X.pipeline(
        c,
        "eng",
        "Engineering",
        (
            {
                "name": "Review",
                "owner": REVIEWER,
                "requirements": [{"kind": "page", "line": "Review", "settings": {"at_head": True}}],
            },
            {
                "name": "QA",
                "owner": WRITER,
                "requirements": [
                    {"kind": "page", "line": "Pre-release", "who_oks": "people_only"},
                    {"kind": "owner_done"},
                ],
            },
            {
                "name": "Merge",
                "owner": WRITER,
                "on_enter": {"act": "github.queue"},
                "requirements": [{"kind": "owner_done"}],
            },
        ),
        manager="bot:manager",
    )
    rid = made["requirements"]["QA"][0]
    row = X.new_task(c, ANA, "Absolute value", "eng", assignee=REVIEWER)
    _link_pr(c, row["id"], "acme/app", 45, "62de57f623fc")
    review, _ = _page(c, store, "<p>Reviewed 62de57f</p>")
    _say(c, _attach(c, row, "page:" + review, by=REVIEWER, requirement="Review"), "met")
    assert E.advance(c, row["id"])["stage"]["name"] == "QA"
    fid = _pre_release(c, store, row)
    A.answer(c, row["id"], rid, BEN, "ok")
    _pre_release(c, store, row, fid=fid)  # version 2, same commits: Ben's OK stays
    assert _f2(c, row).met
    done_id = next(r["id"] for r in R.for_visit(c, E.task(c, row["id"])["visit"]) if r["kind"] == "owner_done")
    _bot(c, row, done_id, WRITER, "done")
    assert E.task(c, row["id"])["stage"]["name"] == "Merge"
    E.move(c, row["id"], "QA", ANA, "The queue dropped it")
    standing = _f2(c, row)
    assert not standing.met and standing.line.endswith("(version 2)")


def _bodies(c, row):
    return c.execute(
        "SELECT recipient_id, body FROM messages WHERE wake_key LIKE ? ORDER BY created_at, id", (row["id"] + ":%",)
    ).fetchall()


def _f2(c, row):
    (standing,) = [s for s in R.standings(c, E.task(c, row["id"])) if s.name == "Pre-release"]
    return standing


def test_a_validate_page_is_judged_against_the_release_its_filed_work_went_out_in(c, store):
    _pipeline(c, page_settings={"with_release": True})
    row = _task(c)
    filed = X.new_task(c, ANA, "Build the importer", assignee=WRITER)
    c.execute(
        "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
        (H.new_id(), row["id"], filed["id"], row["visit"]["id"], "handoff", H.now()),
    )
    _link_pr(c, filed["id"], "acme/app", 3, "abc")
    _push(c, "acme/app", 3, "abc", state="merged", merged_sha="m1")
    for tag, at in (("v1.1.0", "2026-11-01T00:00:00Z"), ("v1.2.0", "2026-11-05T00:00:00Z")):
        rid = H.new_id()
        c.execute(
            "INSERT INTO github_releases (id, repo, tag, commit_sha, published_at, metadata_version, read_at, created_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (rid, "acme/app", tag, "x", at, at, at),
        )
        c.execute(
            "INSERT INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,1,?)",
            (rid, "m1", at),
        )
    made = _attach(c, row, "page:" + _page(c, store)[0])
    context = json.loads(made["judged_against"])["context"]
    assert context == {"task": row["id"], "release": ["v1.1.0"]}, "the first release it is in"


def test_a_new_running_release_ends_a_bots_snooze_that_waits_for_production_to_run_it(c, store):
    """Tico Team, Nov 11: the Product Owner found production on rc.7 with the fix in rc.8 and slept three Validate tasks
    to "the next release window, 10:00 Pacific". rc.8 landed minutes later and nothing woke them. A new release on the
    server whose repository holds the filed work ends a bot's snooze there; a person's snooze, a stage that checks no
    release and work in another repository stay asleep."""
    _pipeline(c, page_settings={"with_release": True})
    until = H.shift(H.now(), days=2)

    def waiting(title, number, repo="acme/app", by=WRITER):
        row = X.new_task(c, ANA, title, "product", assignee=WRITER)
        filed = X.new_task(c, ANA, "Build " + title, assignee=WRITER)
        c.execute(
            "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
            (H.new_id(), row["id"], filed["id"], row["visit"]["id"], "handoff", H.now()),
        )
        _link_pr(c, filed["id"], repo, number, "abc")
        X.say_done(c, filed)
        E.advance(c, filed["id"])  # the work is done; production may not run it yet
        seen = E.task(c, row["id"])["version"]
        E.snooze(c, row["id"], by, "production runs 1.1.0; the change ships in 1.2.0", until, seen_version=seen)
        return row["id"]

    bots, persons, elsewhere = waiting("Importer", 1), waiting("Exporter", 2, by=ANA), waiting("Docs", 3, "acme/other")
    X.settled(c)
    assert E.end_release_snoozes(c, "1.1.0", "acme/app") == []  # the first start only records what runs
    assert E.end_release_snoozes(c, "1.1.0", "Acme/App") == []  # the same release again: nothing
    assert E.end_release_snoozes(c, "1.2.0", "Acme/App") == [bots]
    assert not E.task(c, bots).get("snooze")
    assert E.task(c, persons)["snooze"] and E.task(c, elsewhere)["snooze"]
    said = [body for who, body in _bodies(c, E.task(c, bots)) if who == WRITER][-1]
    assert said.startswith("Production runs 1.2.0 now (it ran 1.1.0).")
    ended = c.execute("SELECT ended_by_id FROM snoozes WHERE task_id=? AND ended_at IS NOT NULL", (bots,))
    assert ended.fetchone()[0] == E.SYSTEM


def test_any_page_on_a_task_with_prs_is_judged_with_their_newest_commits_and_an_unread_one_never_holds_it_up(c, store):
    """A pre-release page says the checks pass at the newest commit: Jev is shown the linked PRs' newest commits so it
    can check that. Only an at_head page waits for a PR Tico has not read; any other page leaves that PR out."""
    _pipeline(c, page_settings={"rubric": "Shows the checks at the newest commit"}, line="Review page")
    row = _task(c)
    fid, _ = _page(c, store)
    made = _attach(c, row, "page:" + fid, requirement="review page")
    assert json.loads(made["judged_against"])["context"] == {"task": row["id"]}, "no PR: no heads"
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (H.new_id(), row["id"], "pr", "https://github.com/acme/app/pull/12", "acme/app", 12, "acme/app#12", H.now()),
    )
    fid2, _ = _page(c, store, "<p>v2</p>")
    unread = _attach(c, row, "page:" + fid2, requirement="review page")
    assert json.loads(unread["judged_against"])["context"] == {"task": row["id"]}, "a PR not read yet is left out"
    _link_pr(c, row["id"], "acme/app", 13, "ccc")
    read = _attach(c, row, "page:" + fid, requirement="review page")
    assert json.loads(read["judged_against"])["context"] == {"task": row["id"], "heads": {"acme/app#13": "ccc"}}
    _say(c, read, "met")
    _push(c, "acme/app", 13, "ddd")
    assert _standing(c, row).met, "a new commit does not undo a page that is not at_head"


# ----------------------------------------------------------------------------- the Jev step
def _engine(p, calls, reason="Clear and complete"):
    def ask(model, state, questions, label):
        calls.append(state)
        return {"p": p, "reason": reason, "model": model, "cost_usd": 0.01}

    return ask


def test_the_jev_step_judges_the_page_text_and_moves_the_task(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c, page_settings={"rubric": "Says what done looks like"})
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store, "<p>Done means the importer runs nightly.</p>")[0])
        return row

    row = seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.95, calls))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    (state,) = calls
    assert state["requirement"]["rubric"] == "Says what done looks like"
    assert "importer runs nightly" in state["attachment"]["text"]
    assert state["work"]["title"] == "Importer spec"
    with store.read() as r:
        assert E.task(r, row["id"])["stage"]["name"] == "Done"
        assert "jev_verdict" in X.events(r, row["id"])
    assert V.run(store, H.shift(H.now(), seconds=1)) == 0, "judged once"


def test_jev_is_not_told_the_pinned_page_is_out_of_date(store, seeded, monkeypatch):
    """The question set says evidence made for an older work_version does not count unless older_evidence_ok. A page
    is pinned by file and version, and the task's version says nothing about it: Jev must not read it as stale."""

    def build(c):
        _pipeline(c)
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        return row

    seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.95, calls))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    (state,) = calls
    assert state["work"]["work_version"] > 0
    assert state["attachment"]["work_version"] == state["work"]["work_version"], (
        "the page is shown as made for the work as it is now: version 0 of work at version N made Jev hedge (rc.4)"
    )
    assert state["requirement"].get("older_evidence_ok") is True, state


def test_the_same_page_on_two_tasks_is_judged_for_each_task(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        fid, _ = _page(c, store)
        one = _task(c)
        two = X.new_task(c, ANA, "Billing spec", "product", assignee=WRITER)
        return _attach(c, one, "page:" + fid), _attach(c, two, "page:" + fid)

    first, second = seeded(build)
    assert first["rubric_hash"] != second["rubric_hash"]
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.95, calls))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 2
    assert sorted(state["work"]["title"] for state in calls) == ["Billing spec", "Importer spec"]


def test_jevs_send_back_wakes_the_bot_owner_with_the_reasons(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        X.settled(c)
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.05, [], reason="No scope"))
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as r:
        bodies = [
            m[0]
            for m in r.execute(
                "SELECT body FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (WRITER, row["id"] + ":%:jev:%")
            )
        ]
        assert _standing(r, row).line == "Jev sent it back: No scope"
    assert len(bodies) == 1 and 'Jev sent back "Spec page": No scope' in bodies[0]


def test_jev_with_no_words_of_its_own_still_gives_every_verdict_a_reason(store, seeded, monkeypatch):
    """Jev answers a probability and no prose (Acme, Nov 11: three Review pages "Jev is unsure" with nothing after it).
    The verdict, its history line and the standing the manager reads all name the requirement, never its rubric."""

    def build(c):
        _pipeline(c, page_settings={"rubric": "Says what done looks like"})
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.5, [], reason=""))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    with store.read() as r:
        line = _standing(r, row).line
        notes = [
            n[0]
            for n in r.execute(
                "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='jev_verdict'",
                (row["id"],),
            )
        ]
    assert line == f'{R.DECIDE} Jev is unsure: Can\'t tell if it meets "Spec page".', line
    assert notes == ['Can\'t tell if it meets "Spec page".'], notes


def test_the_judge_is_told_the_one_linked_prs_repo_number_and_author(store, seeded, monkeypatch):
    """A Review page is judged against the work's PR, as any attachment is: its repo, number and the login that opened
    it. With two PRs linked, each is in the context's heads instead."""

    def build(c):
        _pipeline(c, page_settings={"at_head": True, "rubric": "Reviewed"}, line="Review")
        row = _task(c)
        _link_pr(c, row["id"], "acme/app", 12, "aaa")
        c.execute(
            "UPDATE task_links SET detail=? WHERE task_id=? AND number=12",
            (json.dumps({"author_login": "builder-bot"}), row["id"]),
        )
        _attach(
            c, row, "page:" + _page(c, store, "<p>Reviewed commit aaa. Nothing blocks.</p>")[0], requirement="Review"
        )
        return row

    seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.95, calls))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    (state,) = calls
    work = state["work"]
    assert (work["repo"], work["pr"], work["author"]) == ("acme/app", 12, "builder-bot")
    assert work["context"]["heads"] == {"acme/app#12": "aaa"}


def test_three_failed_calls_give_the_page_to_the_pipelines_manager(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        return row, _attach(c, row, "page:" + _page(c, store)[0])

    row, made = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", None)
    monkeypatch.setattr(PJ, "_installed_store", None)  # no judge configured: every call fails
    at = H.now()
    for tries in range(1, V.TRIES + 1):
        at = H.shift(at, seconds=V.RETRY_S[-1] + 1)
        V.run(store, at)
        with store.read() as r:
            verdict = V.get(r, made["file_id"], made["version"], made["rubric_hash"])
            assert verdict["tries"] == tries
    assert verdict["verdict"] == "failed" and verdict["last_error"] == "No judge is configured on this server"
    with store.read() as r:
        standing = _standing(r, row)
        assert standing.on == "bot:manager"
        assert standing.line.startswith(f"{R.DECIDE} Jev couldn't check this: No judge is configured on this server.")
        (woken,) = [
            m[0]
            for m in r.execute(
                "SELECT body FROM messages WHERE recipient_id='bot:manager' AND wake_key LIKE ?",
                (row["id"] + ":%:jev:%",),
            )
        ]
    assert 'Jev could not check "Spec page": you decide it, as the pipeline\'s manager.' in woken


def test_a_failed_call_waits_before_the_next_try(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        _attach(c, _task(c), "page:" + _page(c, store)[0])

    seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", None)
    monkeypatch.setattr(PJ, "_installed_store", None)
    at = H.shift(H.now(), seconds=1)
    V.run(store, at)
    V.run(store, H.shift(at, seconds=V.RETRY_S[0] - 5))
    with store.read() as r:
        assert r.execute("SELECT tries FROM page_verdicts").fetchone()[0] == 1


def test_a_verdict_no_open_task_waits_on_is_put_off_not_judged(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        E.move(c, row["id"], "product-closed", ANA, "Not now")

    seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.9, calls))
    now = H.shift(H.now(), seconds=1)
    assert V.run(store, now) == 0 and calls == []
    with store.read() as r:
        assert r.execute("SELECT next_try_at FROM page_verdicts").fetchone()[0] > H.shift(now, hours=23)


def test_a_page_for_an_ok_must_be_html_and_one_that_is_not_is_refused_in_the_same_call(c, store):
    """The decide view draws a page for an OK as HTML: a Markdown one read as one run-on paragraph with raw `##`. The
    attach refuses it at once, with what to do instead, as a writing correction (no wake, no quarantine count); a
    page already attached stays where it is."""
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store, "## What was built\n\n- **Retry** on a failed paste\n")
    with _refused("lint") as refused:
        _attach(c, row, f"page:{fid}@1")
    assert refused.value.detail == P.NOT_HTML and "build-the-page" in P.NOT_HTML
    assert P.current(c, row["visit"]["id"], _standing(c, row).requirement["id"]) is None
    assert "page_attached" not in X.events(c, row["id"])
    _page(c, store, "\ufeff\n  <!doctype html><title>Built</title><p>Retry on a failed paste.</p>", fid=fid)
    assert _attach(c, row, f"page:{fid}@2")["version"] == 2
    _page(c, store, "<p>Version three</p>", fid=fid)
    assert _attach(c, row, f"page:{fid}@3")["version"] == 3
    assert (P.is_html("<section>x</section>"), P.is_html("Plain words"), P.is_html("")) == (True, False, False)


# ----------------------------------------------------------------------------- the route and the task's read
def test_the_route_attaches_a_readable_page_and_the_task_read_shows_it(api):
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        rid = _pipeline(conn, person=ANA)
        row = _task(conn)
        fid, _ = _page(conn, store)
    r = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:" + fid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"] == {"file_id": fid, "version": 1}
    (entry,) = [e for e in body["task"]["to_leave"] if e["requirement_id"] == rid]
    assert entry["page"] == {"file_id": fid, "version": 1, "ref": f"page:{fid}@1"}
    assert entry["standing"] == "Jev is checking" and entry["may"] == []
    missing = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:file-" + "1" * 24},
        headers=headers("ana-test"),
    )
    assert missing.status_code == 404, missing.text
    with store.transaction() as conn:
        _say(conn, P.current(conn, row["visit"]["id"], rid), "met")
    stale = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": f"{fid}@2"},
        headers=headers("ana-test"),
    )
    assert stale.status_code == 409, stale.text
    bare = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": 1},
        headers=headers("ana-test"),
    )
    assert bare.status_code == 422, "a version without its file says nothing about which page was shown"
    ok = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": f"{fid}@1"},
        headers=headers("ana-test"),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["task"]["stage"]["name"] == "Done"


def test_a_refused_attach_on_a_private_task_keeps_none_of_its_words(api):
    """The company owner reads refusals and events but not a private task: a refusal of a write on one is kept as
    "Private task write refused", never with the PR it was about."""
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        _pipeline(conn, page_settings={"at_head": True, "rubric": "Reviewed"}, line="Review")
        row = _task(conn)
        conn.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
        conn.execute(
            "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                H.new_id(),
                row["id"],
                "pr",
                "https://github.com/acme/secret/pull/12",
                "acme/secret",
                12,
                "github",
                "pr",
                "acme/secret#12",
                H.now(),
            ),
        )
        fid, _ = _page(conn, store)
    r = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Review", "page": "page:" + fid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 409 and r.json()["error"] == "task_requirement", r.text
    with store.read() as conn:
        refusals = [str(x[0]) for x in conn.execute("SELECT detail FROM refusals").fetchall()]
        events = [" ".join(map(str, x)) for x in conn.execute("SELECT * FROM events").fetchall()]
    assert any("Private task write refused" in d for d in refusals), refusals
    assert not [line for line in refusals + events if "acme/secret" in line]


def test_the_route_refuses_a_markdown_page_with_a_422_the_bot_can_act_on(api):
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        _pipeline(conn, person=ANA)
        row = _task(conn)
        fid, _ = _page(conn, store, "# Spec\n\nBuild the importer.\n")
    r = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:" + fid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "lint" and r.json()["error"]["detail"] == P.NOT_HTML


# ----------------------------------------------------------------------------- the CLI and the bot tool
def test_tico_task_attach_with_req_attaches_a_page_through_the_pages_route(monkeypatch):
    from clients import hubcli, hubtools, remotecli

    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "bot:writer", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"task": {}}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    argv = ["task", "attach", "T-1", "page:file-abc@2", "--req", "Spec page", "--version", "4"]
    remotecli.run(hubcli.parser().parse_args(argv))
    assert sent[-1] == (
        "POST",
        "tasks/T-1/pages",
        {"requirement": "Spec page", "page": "page:file-abc@2", "version": 4},
    )
    with pytest.raises(ValueError, match="version"):
        hubtools.task_attach(Client(), {"id": "T-1", "req": "Spec page", "page": "page:file-abc"})
    with pytest.raises(ValueError, match="both req and page"):
        hubtools.task_attach(Client(), {"id": "T-1", "req": "Spec page", "version": 4})
    with pytest.raises(ValueError, match="name"):
        hubtools.task_attach(Client(), {"id": "T-1"})


# ----------------------------------------------------------------------------- several pages, several requirements
class RawJev:
    """Jev as judge.server_engine answers it (PJ.ASK), for the grouped Jev step: counts calls, answers each requirement's
    `met` from its own rubric (`ps`, by rubric; 0.95 otherwise), and reads each item of a grouped call on its own.
    `drop` names a page text whose answer goes missing in a grouped call; `fail` makes every call fail."""

    def __init__(self, ps=None, drop=None, fail=None):
        self.ps, self.drop, self.fail, self.calls = ps or {}, drop, fail, []

    def answer(self, one, own):
        requirement = one["requirement"] if own == "met" else one["requirements"]["requirement" + own[3:]]
        return {"type": "noul", "noul": self.ps.get(requirement["rubric"], 0.95)}

    def __call__(self, state, questions, label):
        self.calls.append({"state": state, "questions": sorted(questions), "label": label})
        if self.fail:
            raise self.fail
        parts = DG.split(state, questions) or [("", state, {q: q for q in questions})]
        answers = {}
        for _name, one, ids in parts:
            if len(parts) > 1 and self.drop and self.drop in str(one["attachment"]["text"]):
                continue
            for own, key in ids.items():
                answers[key] = self.answer(one, own)
        return {"answers": answers, "model": "tico-decide-low", "provider": "gateway", "cost_usd": 0.003, "ms": 7}


RUBRICS = ("Says what done looks like", "Names the risks", "Lists who is affected")


def _three(c):
    stages = (
        {
            "name": "Spec",
            "owner": WRITER,
            "requirements": [
                {"kind": "page", "line": line, "person": None, "settings": {"rubric": rubric}, "who_oks": "none"}
                for line, rubric in zip(("Spec page", "Risk page", "Who page"), RUBRICS, strict=True)
            ],
        },
    )
    X.pipeline(c, "product", "Product", stages, manager="bot:manager")


def _verdicts(store):
    with store.read() as r:
        return {row["rubric_hash"]: dict(row) for row in r.execute("SELECT * FROM page_verdicts").fetchall()}


def test_one_page_on_three_requirements_is_read_once_and_each_has_its_own_verdict(store, seeded, monkeypatch):
    """AC-7: one page attached to three requirements is one call, its text sent once, three questions, and each
    requirement takes its own answer."""

    def build(c):
        _three(c)
        row = _task(c)
        fid, _ = _page(c, store, "<p>Done means the importer runs nightly.</p>")
        return [_attach(c, row, "page:" + fid, requirement=line) for line in ("Spec page", "Risk page", "Who page")]

    attached = seeded(build)
    jev = RawJev(ps={RUBRICS[0]: 0.95, RUBRICS[1]: 0.05, RUBRICS[2]: 0.5})
    monkeypatch.setattr(PJ, "ASK", jev)
    monkeypatch.setattr(PJ, "SAME_PAGE", True)
    assert V.run(store, H.shift(H.now(), seconds=1)) == 3
    (call,) = jev.calls
    assert call["questions"] == ["met1", "met2", "met3"] and call["label"] == PJ.QUESTION_SET
    assert json.dumps(call["state"]).count("importer runs nightly") == 1, "the page is sent once"
    assert sorted(r["rubric"] for r in call["state"]["requirements"].values()) == sorted(RUBRICS)
    got = _verdicts(store)
    by_rubric = {}
    for row in attached:
        rubric = json.loads(row["judged_against"])["rubric"] if row.get("judged_against") else None
        by_rubric[rubric] = got[row["rubric_hash"]]
    assert {k: v["verdict"] for k, v in by_rubric.items()} == {
        RUBRICS[0]: "met",
        RUBRICS[1]: "not_met",
        RUBRICS[2]: "unsure",
    }
    assert round(sum(v["cost_usd"] for v in got.values()), 6) == 0.003, "the call's cost is shared, not repeated"


def test_as_shipped_one_page_on_three_requirements_is_three_calls_as_before(store, seeded, monkeypatch):
    """SAME_PAGE off (until the real-Jev replay): each requirement is its own call with today's one `met` question."""

    def build(c):
        _three(c)
        row = _task(c)
        fid, _ = _page(c, store, "<p>Done means the importer runs nightly.</p>")
        return [_attach(c, row, "page:" + fid, requirement=line) for line in ("Spec page", "Risk page", "Who page")]

    seeded(build)
    jev = RawJev(ps={RUBRICS[0]: 0.95, RUBRICS[1]: 0.05, RUBRICS[2]: 0.5})
    monkeypatch.setattr(PJ, "ASK", jev)
    assert PJ.SAME_PAGE is False
    assert V.run(store, H.shift(H.now(), seconds=1)) == 3
    assert [call["questions"] for call in jev.calls] == [["met"], ["met"], ["met"]]


def _five(c, store):
    _pipeline(c, page_settings={"rubric": "Says what done looks like"})
    rows = []
    for n in range(5):
        row = X.new_task(c, ANA, f"Spec {n}", "product", assignee=WRITER)
        rows.append(row)
        _attach(c, row, "page:" + _page(c, store, f"<p>Page {n}: done means it runs.</p>")[0])
    return rows


def test_five_small_pages_owed_take_fewer_than_five_calls(store, seeded, monkeypatch):
    """AC-7: five small pages owed, grouped: fewer than five calls, and every page judged on its own."""
    monkeypatch.setattr(PJ, "GROUP", DG.MOST)
    rows = seeded(lambda c: _five(c, store))
    jev = RawJev()
    monkeypatch.setattr(PJ, "ASK", jev)
    assert V.run(store, H.shift(H.now(), seconds=1)) == 5
    assert len(jev.calls) < 5
    with store.read() as r:
        assert all(E.task(r, row["id"])["stage"]["name"] == "Done" for row in rows)
    assert all(v["verdict"] == "met" for v in _verdicts(store).values())


def test_five_pages_at_one_per_call_are_five_calls_as_before(store, seeded, monkeypatch):
    """GROUP = 1 (what ships until the replay): each page its own call, its state exactly as alone."""
    monkeypatch.setattr(PJ, "GROUP", 1)
    seeded(lambda c: _five(c, store))
    jev = RawJev()
    monkeypatch.setattr(PJ, "ASK", jev)
    assert V.run(store, H.shift(H.now(), seconds=1)) == 5
    assert len(jev.calls) == 5 and all(call["questions"] == ["met"] for call in jev.calls)
    assert all("requirement" in call["state"] for call in jev.calls)


def test_the_spend_cap_is_read_again_before_each_page_call(store, seeded, monkeypatch):
    """A beat that crosses the monthly cap makes no more calls after it, as when the cap was read before every page."""
    monkeypatch.setattr(PJ, "GROUP", 1)
    seeded(lambda c: _five(c, store))
    jev = RawJev()
    monkeypatch.setattr(PJ, "ASK", jev)
    monkeypatch.setattr(PJ.judge, "over_spend_cap", lambda c, settings: "capped" if jev.calls else None)
    V.run(store, H.shift(H.now(), seconds=1))
    assert len(jev.calls) == 1


def test_a_page_whose_answer_is_missing_in_a_group_is_asked_again_alone(store, seeded, monkeypatch):
    """AC-9: the page with no answer is asked again on its own; no page takes another page's answer."""
    monkeypatch.setattr(PJ, "GROUP", DG.MOST)
    seeded(lambda c: _five(c, store))
    jev = RawJev(drop="Page 2:")
    monkeypatch.setattr(PJ, "ASK", jev)
    assert V.run(store, H.shift(H.now(), seconds=1)) == 5
    assert len(jev.calls) == 2
    assert "Page 2:" in jev.calls[1]["state"]["attachment"]["text"] and jev.calls[1]["questions"] == ["met"]
    assert all(v["verdict"] == "met" for v in _verdicts(store).values())


def test_a_failed_grouped_call_counts_a_try_on_each_page(store, seeded, monkeypatch):
    """AC-10: a grouped call that fails is each page's failed call: a try counted, the verdict still owed, retried."""
    monkeypatch.setattr(PJ, "GROUP", DG.MOST)
    seeded(lambda c: _five(c, store))
    jev = RawJev(fail=PJ.JudgeError("unavailable", "Jev did not answer", retryable=True))
    monkeypatch.setattr(PJ, "ASK", jev)
    assert V.run(store, H.shift(H.now(), seconds=1)) == 0
    assert len(jev.calls) == 1
    got = list(_verdicts(store).values())
    assert len(got) == 5 and all(v["verdict"] is None and v["tries"] == 1 and v["next_try_at"] for v in got)


def test_needs_you_names_a_page_ok_by_its_task_and_decision_not_the_requirement(api):
    """#330: Needs you read "Spec page" or "OK Ana (version 1)" for a stage's page OK, saying neither which task nor
    what was asked. It reads what it is, the task and the decision; what the stage said besides (Jev's reason) stays."""
    from backend import views

    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        rid = _pipeline(conn, person=ANA)
        row = _task(conn)
        fid, _ = _page(conn, store)
        _say(conn, _attach(conn, row, "page:" + fid), "met")
    r = api.get("/api/v2/needs-you", headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    (item,) = [i for i in r.json()["items"] if i.get("requirement_id") == rid]
    assert item["line"] == "Spec for Importer spec, ready for your OK", item["line"]
    assert views.page_ask_line("Review page", "Share chat", 'Jev is unsure: no checks. OK Ana (version 2)') == (
        "Review for Share chat, ready for your OK. Jev is unsure: no checks."
    )
    assert views.page_ask_line("Results page", "", "OK someone (version 1)") == "Results page, ready for your OK"
    # Only the stage's own last phrase goes: one inside Jev's reason stays, and a name with brackets goes whole.
    said = "Jev is unsure: says OK Ana (version 2) earlier. OK Ana (version 2)"
    assert (
        views.page_ask_line("Spec page", "X", said)
        == "Spec for X, ready for your OK. " + said[: -len(" OK Ana (version 2)")]
    )
    assert views.page_ask_line("Spec page", "X", "OK Chris (Owner) (version 3)") == "Spec for X, ready for your OK"
    conflict = "OK Ana (version 1). The PR has a merge conflict: builder fixes it after your OK."
    assert views.page_ask_line("Review page", "X", conflict) == (
        "Review for X, ready for your OK. The PR has a merge conflict: builder fixes it after your OK."
    )


# ----------------------------------------------------------------------------- Jev says what is missing (task #351)
def _shipped(name):
    from backend import pipeline_settings as PS

    return getattr(PS, name)


def _parts(ps):
    """Jev (pipeline_judges.ENGINE) answering each part it is asked with ps[key], 0.95 when not named."""

    def ask(model, state, questions, label):
        answers = {qid: {"noul": ps.get(qid.removeprefix("part_"), 0.95)} for qid in questions}
        return PJ._read({"answers": answers, "model": "tico-decide-low"}, list(questions), model)

    return ask


def _jev_lines(r, row):
    notes = [
        n[0]
        for n in r.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='jev_verdict'",
            (row["id"],),
        )
    ]
    return [n for n in notes if n]


@pytest.mark.parametrize("rubric", ["SPEC_RUBRIC", "QA_RUBRIC"])
@pytest.mark.parametrize("ps", [{}, {"goal": 0.4, "built": 0.4}], ids=["every part yes", "a part unsure"])
def test_a_page_a_person_oks_shows_nothing_from_jev_unless_a_part_is_missing(store, seeded, monkeypatch, rubric, ps):
    def build(c):
        _pipeline(c, page_settings={"rubric": _shipped(rubric)}, who_oks="people_only")
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _parts(ps))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    with store.read() as r:
        standing = _standing(r, row)
        assert standing.state == R.WAITING and standing.line == "OK someone (version 1)", standing.line
        assert "Jev" not in standing.line
        assert _jev_lines(r, row) == [], "the task page shows no Jev line either"
        view = H.task_view(r, row["id"], ANA)
        assert "Jev" not in json.dumps(view.get("to_leave")), view.get("to_leave")


def test_a_spec_page_missing_a_part_goes_to_the_person_with_that_line_not_back_to_the_bot(store, seeded, monkeypatch):
    """Jev's verdict on a spec predicts nothing a person decides, so it never gates a Spec page: the person sees the one
    line, and the bot that wrote it is not sent back."""

    def build(c):
        _pipeline(c, page_settings={"rubric": _shipped("SPEC_RUBRIC")}, who_oks="people_only")
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        X.settled(c)
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _parts({"out_of_scope": 0.05}))
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as r:
        standing = _standing(r, row)
        assert (standing.state, standing.line) == (R.WAITING, "Jev: No out-of-scope list found.")
        assert standing.on != WRITER
        assert _jev_lines(r, row) == ["No out-of-scope list found."]
        sent = r.execute(
            "SELECT 1 FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (WRITER, row["id"] + ":%:jev:%")
        ).fetchall()
        assert not sent, "the spec's bot is not woken to rewrite it"


def test_a_qa_page_missing_a_part_still_goes_back_to_its_bot_first(c, store):
    rid = _pipeline(c, page_settings={"rubric": _shipped("QA_RUBRIC")}, who_oks="people_only")
    assert rid
    row = _task(c)
    _say(c, _attach(c, row, "page:" + _page(c, store)[0]), "not_met", "Reviewer 2's findings are missing.")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.NOT_MET,
        WRITER,
        "Jev sent it back: Reviewer 2's findings are missing.",
    )


def _validate(c, store):
    _pipeline(c, page_settings={"rubric": _shipped("VALIDATE_RUBRIC")})
    row = _task(c)
    fid, _ = _page(c, store)
    _attach(c, row, "page:" + fid)
    X.settled(c)
    return row, fid


def _jev_wakes(r, row, who):
    return [
        m[0]
        for m in r.execute(
            "SELECT body FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (who, row["id"] + ":%:jev:%")
        )
    ]


def test_unsure_on_a_bots_stage_wakes_the_manager_once_per_version_and_not_the_pages_bot(store, seeded, monkeypatch):
    row, fid = seeded(lambda c: _validate(c, store))
    monkeypatch.setattr(PJ, "ENGINE", _parts({"version": 0.35}))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    with store.read() as r:
        standing = _standing(r, row)
        assert (standing.state, standing.on) == (R.WAITING, "bot:manager")
        assert standing.line == f"{R.DECIDE} Jev is unsure: Can't tell if the version production runs is named."
        (asked,) = _jev_wakes(r, row, "bot:manager")
        assert "Can't tell if the version production runs is named." in asked
        assert _shipped("VALIDATE_RUBRIC") not in asked, "the line, never the whole check"
        assert _jev_wakes(r, row, WRITER) == [], "the page's own bot is not woken to rewrite it"
    # Judged again at the same version (as at release: an open unsure is judged once more): no new wake.
    with store.transaction() as c:
        c.execute("UPDATE page_verdicts SET verdict=NULL, next_try_at=NULL WHERE file_id=?", (fid,))
    assert V.run(store, H.shift(H.now(), seconds=2)) == 1
    with store.read() as r:
        assert len(_jev_wakes(r, row, "bot:manager")) == 1, "a second unsure of the same version wakes nobody new"
        assert _jev_wakes(r, row, WRITER) == []


def test_a_part_that_is_no_on_a_bots_stage_still_goes_back_to_the_pages_bot(store, seeded, monkeypatch):
    row, _fid = seeded(lambda c: _validate(c, store))
    monkeypatch.setattr(PJ, "ENGINE", _parts({"shown": 0.05}))
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as r:
        standing = _standing(r, row)
        assert (standing.state, standing.on) == (R.NOT_MET, WRITER)
        assert (
            standing.line == "Jev sent it back: Doesn't say what was run or looked at in production and what it gave."
        )
        (told,) = _jev_wakes(r, row, WRITER)
        assert "Doesn't say what was run or looked at in production" in told
        assert _jev_wakes(r, row, "bot:manager") == []


def test_at_release_an_open_unsure_is_judged_again_and_old_lines_lose_their_probability(c, store):
    """The migration that ships this: the open task's unsure page is owed again; a closed one's verdict stays, and
    every line in the old template is said the new way."""
    import importlib

    migration = importlib.import_module("backend.migrations.20261113070000_jev_reasons_by_part")
    _pipeline(c)
    open_row = _task(c)
    closed_row = X.new_task(c, ANA, "Billing spec", "product", assignee=WRITER)
    old = "Jev could not tell whether it meets the check (p 0.70, between 0.2 and 0.75): Spec page"
    waiting = _say(c, _attach(c, open_row, "page:" + _page(c, store)[0]), "unsure", old)
    shut = _say(c, _attach(c, closed_row, "page:" + _page(c, store)[0]), "unsure", old)
    E.move(c, closed_row["id"], E.CLOSED, ANA, "not needed")
    migration.up(c)
    again = V.get(c, waiting["file_id"], waiting["version"], waiting["rubric_hash"])
    assert (again["verdict"], again["reasons"], again["tries"]) == (None, None, 0)
    assert V.owed(c, H.shift(H.now(), seconds=1)) and again["id"] in [
        o["id"] for o in V.owed(c, H.shift(H.now(), seconds=1))
    ]
    kept = V.get(c, shut["file_id"], shut["version"], shut["rubric_hash"])
    assert (kept["verdict"], kept["reasons"]) == ("unsure", "Can't tell if it meets the check.")
    migration.up(c)  # a second run changes nothing it already changed
    assert (
        V.get(c, shut["file_id"], shut["version"], shut["rubric_hash"])["reasons"]
        == "Can't tell if it meets the check."
    )
