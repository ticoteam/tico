"""A page approval naming several approvers (requirements.person_ids): each of them sees the one ask in Needs you, the
first answer (OK or send back) settles it for all of them, an answer names the version it was given on, and with
every approver gone it goes to the company's person, with a Health line.

Synthetic rows only (backend/tests/helpers/engine.py). Jev is a recorded verdict; never a model.
"""

import json
from types import SimpleNamespace

import pytest

from backend import answers as A
from backend import health
from backend import pipeline_settings as S
from backend import task_asks as T
from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend import waiting_on as W
from backend.blobs import Blobs
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _task

ANA, BEN, CARA = X.ANA, "human:ben", "human:cara"


@pytest.fixture
def c(company):
    company.blobs = Blobs(company.settings)
    with company.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        yield conn


def _shared(c, *people):
    rid = _pipeline(c, person=people[0])
    c.execute("UPDATE requirements SET person_ids=? WHERE id=?", (json.dumps(list(people)), rid))
    return rid


def _submitted(c, company, row, fid=None, text="<p>Go live</p>"):
    fid, _ = _page(c, company, text, fid=fid)
    seen = E.task(c, row["id"])["version"]
    made = P.attach(c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=seen)
    _say(c, made, "met")
    return fid, f"{fid}@{made['version']}"


def _asks(c, person):
    return [(n["task_id"], n["requirement_id"]) for n in W.needs(c, person) if n["kind"] == "ask"]


def _answer(c, row, rid, by, value, version, note=None):
    return A.answer(
        c,
        row["id"],
        rid,
        by,
        value,
        note,
        page_version=version,
        visit_id=E.task(c, row["id"])["visit"]["id"],
        keyed=True,
        page_reader=lambda refs: True,
        own_click=True,
    )


def test_either_approver_approves_and_the_first_answer_clears_it_for_both(c, company):
    rid = _shared(c, CARA, BEN)
    row = _task(c)
    _, version = _submitted(c, company, row)
    assert _asks(c, CARA) == _asks(c, BEN) == [(row["id"], rid)]
    with pytest.raises(H.Refused, match="page_version"):
        _answer(c, row, rid, BEN, "ok", None)  # an answer names the version it was given on
    mark = c.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
    assert _answer(c, row, rid, BEN, "ok", version)["stage"]["name"] == "Done"
    assert _asks(c, CARA) == _asks(c, BEN) == []
    pushed = {r[0] for r in c.execute("SELECT subject_id FROM changes WHERE seq>? AND topic='needs'", (mark,))}
    assert CARA in pushed, "the other approver's Needs you is told at once"
    with pytest.raises(H.Refused):
        _answer(c, row, rid, CARA, "no", version, "Too late")


def test_a_send_back_by_either_settles_it_and_the_next_version_asks_both_again(c, company):
    rid = _shared(c, ANA, BEN)
    row = _task(c)
    fid, version = _submitted(c, company, row)
    _answer(c, row, rid, ANA, "no", version, "Fix the headline")
    assert _asks(c, BEN) == []
    with pytest.raises(H.Refused, match="Ana already answered this"):
        _answer(c, row, rid, BEN, "ok", version)
    shared = R.get(c, rid) or {}
    assert not R._same_code_ok(c, row, shared, {}, {})  # pyright: ignore[reportPrivateUsage]  # no approval carries
    _, newer = _submitted(c, company, row, fid, "<p>Go live, v2</p>")
    assert _asks(c, ANA) == _asks(c, BEN) == [(row["id"], rid)]
    with pytest.raises(H.Refused, match="task_changed"):
        _answer(c, row, rid, BEN, "ok", version)  # an answer to the old version never settles the new one
    assert _answer(c, row, rid, BEN, "ok", newer)["stage"]["name"] == "Done"


def test_an_approver_taken_off_the_list_can_no_longer_answer(c, company):
    rid = _shared(c, ANA, BEN)
    row = _task(c)
    _, version = _submitted(c, company, row)
    assert health._page_approvers_check(c) is None  # pyright: ignore[reportPrivateUsage]
    c.execute("UPDATE requirements SET person_ids=NULL WHERE id=?", (rid,))
    assert _asks(c, BEN) == []
    with pytest.raises(H.Refused, match="This is asked of Ana"):
        _answer(c, row, rid, BEN, "ok", version)


def test_with_every_approver_gone_it_goes_to_the_owner_and_health_says_so(c, company):
    rid = _shared(c, "human:gone1", "human:gone2")
    row = _task(c)
    _, version = _submitted(c, company, row)
    assert W.admin_person(c) == ANA
    assert _asks(c, ANA) == [(row["id"], rid)]
    with pytest.raises(H.Refused, match="This is asked of Ana"):
        _answer(c, row, rid, BEN, "ok", version)
    line = health._page_approvers_check(c)  # pyright: ignore[reportPrivateUsage]
    assert line and line["status"] == "warn" and "Ana is asked" in line["summary"]
    assert _answer(c, row, rid, ANA, "ok", version)["stage"]["name"] == "Done"


def test_with_the_owner_gone_an_admin_answers_never_an_ordinary_member(c, company):
    people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    for person in people["people"]:
        person["hidden"] = person["id"] == "ana"
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
    c.execute(
        "UPDATE registry_metadata SET value=? WHERE key='access'", (json.dumps({"admins": ["cara@acme.example"]}),)
    )
    rid = _pipeline(c, person=None)
    c.execute("UPDATE requirements SET person_ids='[]' WHERE id=?", (rid,))  # an empty list stays empty
    row = _task(c)
    _, version = _submitted(c, company, row)
    assert (W.company_person(c), W.admin_person(c)) == (BEN, CARA)
    assert _asks(c, CARA) == [(row["id"], rid)] and _asks(c, BEN) == []
    assert _answer(c, row, rid, CARA, "no", version, "Needs a price")["stage"]["name"] == "Spec"


def test_a_second_approver_answers_through_the_task_api(api):
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        rid = _shared(conn, ANA, BEN)
        row = _task(conn)
        _, version = _submitted(conn, store, row)
    for token in ("ana-test", "ben-test"):
        read = api.get(f"/api/v2/tasks/{row['id']}", headers=headers(token))
        (entry,) = [e for e in read.json()["task"]["to_leave"] if e["requirement_id"] == rid]
        assert sorted(entry["may"]) == ["no", "ok"], token
        needs = api.get("/api/v2/needs-you", headers=headers(token)).json()
        assert rid in json.dumps(needs), f"{token}: the ask is in Needs you"
    ok = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": version, "visit_id": row["visit"]["id"]},
        headers=headers("ben-test"),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["task"]["stage"]["name"] == "Done"


def test_a_stage_takes_a_list_of_approvers_for_a_document_approval_only(c, company):
    _pipeline(c, person=ANA)
    record = S.read(c, "product")
    record["stages"][0]["requirements"][0]["person_ids"] = [BEN, ANA]
    with pytest.raises(Problem, match="Only a document approval names a list") as refused:
        S.save(c, ANA, record)  # a plain page
    assert refused.value.status == 422
    record["stages"][0]["requirements"][0]["settings"] = {"document": "Spec", "rubric": "States the goal."}
    S.save(c, ANA, record)
    record = S.read(c, "product")
    stage = record["stages"][0]
    stage["requirements"][0]["person_ids"] = [BEN, ANA]
    saved = S.save(c, ANA, record)
    req = saved["stages"][0]["requirements"][0]
    assert (req["person_id"], req["person_ids"]) == (BEN, [BEN, ANA])
    stage = saved["stages"][0]
    stage["requirements"][0]["person_ids"] = [ANA]
    req = S.save(c, ANA, saved)["stages"][0]["requirements"][0]
    assert (req["person_id"], req["person_ids"]) == (ANA, [ANA]), "a list of one stays a list"
    mark = c.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
    record = S.read(c, "product")
    record["stages"][0]["requirements"][0]["person_ids"] = [ANA, CARA]
    S.save(c, ANA, record)
    told = {r[0] for r in c.execute("SELECT subject_id FROM changes WHERE seq>? AND topic='needs'", (mark,))}
    assert {ANA, CARA} <= told, "a stage's approvers edited move their Needs you"
    record = S.read(c, "product")
    record["stages"][0]["requirements"].append({"kind": "ok", "person_ids": [ANA, BEN]})
    with pytest.raises(Problem, match="Only a document approval names a list"):
        S.save(c, ANA, record)
    record = S.read(c, "product")
    record["stages"][0]["requirements"][0]["person_ids"] = ["bot:writer"]
    with pytest.raises(Problem, match="bot:writer is not"):
        S.save(c, ANA, record)  # a list of one is still read as people
    record["stages"][0]["requirements"][0]["person_ids"] = []
    req = S.save(c, ANA, record)["stages"][0]["requirements"][0]
    assert (req["person_id"], req["person_ids"]) == (None, [])


def test_only_an_approvers_own_click_answers_a_shared_page_ok_or_send_back(c, company):
    rid = _shared(c, ANA, BEN)
    token = SimpleNamespace(actor=BEN, task_actor="", via="", via_token=True)
    assistant = SimpleNamespace(actor="bot:assistant", task_actor=BEN, via="assistant")
    assert T.refuses_ok_by(c, token, rid, "no") == T.SHARED_OWN
    assert T.refuses_ok_by(c, assistant, rid, "ok") == T.SHARED_OWN
    assert T.refuses_ok_by(c, SimpleNamespace(actor=BEN, task_actor="", via=""), rid, "ok") is None
    c.execute("UPDATE requirements SET person_ids=NULL WHERE id=?", (rid,))
    assert T.refuses_ok_by(c, token, rid, "no") is None, "one approver's send back stays theirs to give"


def test_a_list_of_approvers_names_people_who_can_approve(c, company):
    _pipeline(c, person=ANA, page_settings={"document": "Spec", "rubric": "States the goal."})
    record = S.read(c, "product")
    for listed in ([ANA, "bot:writer"], ["bot:writer", ANA], [ANA, None], [ANA, "  "], [ANA, 7]):
        record["stages"][0]["requirements"][0]["person_ids"] = listed
        with pytest.raises(Problem) as refused:
            S.save(c, ANA, record)
        assert refused.value.status == 422, listed
    record["stages"][0]["requirements"][0]["person_ids"] = [ANA, "human:gone"]
    with pytest.raises(Problem, match="human:gone is not"):
        S.save(c, ANA, record)


def test_the_answer_engine_takes_a_shared_page_answer_only_from_an_own_click(c, company):
    rid = _shared(c, ANA, BEN)
    row = _task(c)
    _, version = _submitted(c, company, row)
    with pytest.raises(H.Refused, match="own click"):
        A.answer(c, row["id"], rid, BEN, "no", "Not yet", page_version=version)


def test_with_nobody_who_can_act_it_waits_and_nobody_may_answer(c, company):
    people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    for person in people["people"]:
        person["hidden"] = person["id"] == "ana"
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
    c.execute("UPDATE registry_metadata SET value=? WHERE key='access'", (json.dumps({"admins": []}),))
    rid = _shared(c, "human:gone1", "human:gone2")
    row = _task(c)
    _, version = _submitted(c, company, row)
    assert W.admin_person(c) is None
    assert _asks(c, BEN) == _asks(c, CARA) == []
    with pytest.raises(H.Refused, match="This is asked of nobody"):
        _answer(c, row, rid, BEN, "ok", version)
    line = health._page_approvers_check(c)  # pyright: ignore[reportPrivateUsage]
    assert line and "nobody is asked" in line["summary"]


def test_a_private_shared_page_waits_to_be_shared_only_when_no_approver_can_open_it(c, company):
    rid = _shared(c, BEN, CARA)
    row = _task(c)
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
    _submitted(c, company, row)
    task = E.task(c, row["id"])
    assert W.unreadable(c, task, R.standings(c, task)) == BEN, "the ladder hands it to whoever can share it"
    (wait,) = [w for w in W.of(c, task) if w.requirement_id == rid]
    assert wait.share == (BEN, CARA) and wait.who == W.person_for(c, task)
    c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps([CARA]), row["id"]))
    task = E.task(c, row["id"])
    assert W.unreadable(c, task, R.standings(c, task)) is None
    assert [(w.who, w.share) for w in W.of(c, task) if w.requirement_id == rid] == [(CARA, None)]


def test_a_stored_list_that_is_not_json_names_only_the_one_approver():
    assert R.approvers({"kind": "page", "person_id": ANA, "person_ids": "not json"}) == (ANA,)
    assert R.approvers({"kind": "page", "person_id": ANA, "person_ids": json.dumps([ANA, BEN])}) == (ANA, BEN)
