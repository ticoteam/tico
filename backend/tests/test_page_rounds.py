"""A page that comes back (backend/page_rounds.py, spec #263): what a bot says with a new version (`changed`,
`answered`, its note), the quote-back refusal, and the rounds the decision view draws its right side and review history
from. Synthetic rows only (backend/tests/helpers/engine.py); Jev is recorded directly, never a model."""

import json

import pytest

from backend import answers as A
from backend import page_rounds as PR
from backend import pipeline_judges as J
from backend import task_pages as P
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _task, _v

ANA, WRITER = X.ANA, "bot:writer"
NOTE = (
    "The table of evidence is missing its sources, and the second section repeats the first one almost word for word, "
    "so cut it. Also the phone picture is cropped."
)


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


def _attach(c, row, ref, **said):
    return P.attach(c, row["id"], "Spec page", ref, WRITER, seen_version=_v(c, row), **said)


def _refused(text):
    return pytest.raises(H.Refused, match=text)


def test_parse_answered_takes_the_point_before_the_last_done_and_the_why_after():
    assert PR.parse_answered("Sources on the evidence table: done, each row links its query") == {
        "point": "Sources on the evidence table",
        "done": True,
        "why": "each row links its query",
    }
    assert PR.parse_answered("Phone picture: cropped: Not done, the shot needs a person's phone") == {
        "point": "Phone picture: cropped",
        "done": False,
        "why": "the shot needs a person's phone",
    }
    for bad in ("done", "Sources: finished, yes", "Sources: done", ": done, why", "Sources done, why"):
        assert PR.parse_answered(bad) is None, bad


def test_quoted_back_finds_twelve_words_in_a_row_whatever_the_case_and_punctuation():
    eleven = "the second section repeats the first one almost word for word, so"
    assert not PR.quoted_back(NOTE, [eleven.replace(", so", "")])
    assert PR.quoted_back(NOTE, ["You said: The second section REPEATS the first one, almost word for word. so cut it"])
    assert not PR.quoted_back(None, [NOTE]) and not PR.quoted_back(NOTE, [])


def test_an_attach_keeps_what_the_bot_said_and_rounds_show_each_version_with_its_answers_and_jev(c, store):
    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid, note="First draft"), "unsure", "Can't tell who uses it")
    A.answer(c, row["id"], rid, ANA, "no", NOTE)
    _page(c, store, "<h1>Spec</h1><p>Build the importer, with sources.</p>", fid=fid)
    made = _attach(
        c,
        row,
        "page:" + fid,
        changed=["Sources on every evidence row", "  Cut the repeated second section  "],
        answered=[
            "Sources on the evidence table: done, each row links its query",
            "Repeated section: done, cut",
            "Phone picture: not done, needs a person's phone",
        ],
    )
    _say(c, made, "met", "Clear and complete")
    event = c.execute(
        "SELECT data FROM history_events WHERE subject_id=? AND kind='page_attached' ORDER BY created_at DESC, id DESC",
        (row["id"],),
    ).fetchone()
    data = json.loads(event[0])
    assert data["changed"] == ["Sources on every evidence row", "Cut the repeated second section"]
    assert data["answered"][2] == {"point": "Phone picture", "done": False, "why": "needs a person's phone"}

    one, two = PR.rounds(c, row["id"], rid)
    assert (one["version"], one["replaced"], one["note"], one["changed"]) == (1, True, "First draft", [])
    assert one["jev"] == {"verdict": "unsure", "reason": "Can't tell who uses it"}
    assert [(a["by"], a["answer"], a["note"]) for a in one["answers"]] == [(ANA, "no", NOTE)]
    assert (two["version"], two["replaced"], two["answers"]) == (2, False, [])
    assert two["jev"] == {"verdict": "met", "reason": "Clear and complete"}, "no p-value, only the verdict and reason"
    assert [p["done"] for p in two["answered"]] == [True, True, False]
    A.answer(c, row["id"], rid, ANA, "ok")
    assert [a["answer"] for a in PR.rounds(c, row["id"], rid)[1]["answers"]] == ["ok"]


def test_a_version_jev_has_not_judged_has_no_verdict_and_another_requirement_has_no_rounds(c, store):
    rid = _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    _attach(c, row, "page:" + fid)
    (only,) = PR.rounds(c, row["id"], rid)
    assert only["jev"] == {"verdict": None, "reason": ""} and only["note"] == "" and only["answered"] == []
    assert PR.rounds(c, row["id"], "no-such-requirement") == []


_OLD_REASON = {
    "met": "Jev found it meets the check (p {p:.2f}): {check}",
    "not_met": "Jev found it does not meet the check (p {p:.2f}): {check}",
    "unsure": "Jev could not tell whether it meets the check (p {p:.2f}, between 0.2 and 0.75): {check}",
}


@pytest.mark.parametrize(
    ("verdict", "p", "shown"),
    [
        ("met", 0.81, "Jev found it meets the check: States the goal and who it is for"),
        ("not_met", 0.12, "Jev found it does not meet the check: States the goal and who it is for"),
        ("unsure", 0.73, "Jev could not tell whether it meets the check: States the goal and who it is for"),
    ],
)
def test_jev_reason_on_the_chip_drops_the_p_value_jev_writes(verdict, p, shown):
    """Validate on rc.27: the chip showed 'Jev found it meets the check (p 0.81): ...'. Verdicts stored before Jev
    named parts still carry that sentence; the chip shows it without the '(p ...)' part. A reason Jev writes now has
    no p-value and reaches the chip as it is."""
    stored = _OLD_REASON[verdict].format(p=p, check="States the goal and who it is for")
    assert PR._jev({"verdict": verdict, "reasons": stored}) == {"verdict": verdict, "reason": shown}
    assert PR._jev({"verdict": verdict, "reasons": "Clear (and complete)"})["reason"] == "Clear (and complete)"
    own = _OLD_REASON[verdict].format(p=p, check="Cites the rate (p 0.05) for each claim")
    assert PR._jev({"verdict": verdict, "reasons": own})["reason"].endswith(": Cites the rate (p 0.05) for each claim")
    now = J.reason_for(verdict, "Spec page")
    assert "(p " not in now and PR._jev({"verdict": verdict, "reasons": now})["reason"] == now


def test_an_attach_that_quotes_the_send_back_or_breaks_the_forms_is_refused_and_nothing_is_attached(c, store):
    rid = _pipeline(c, who_oks="people_only")
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "unsure", "Can't tell")
    A.answer(c, row["id"], rid, ANA, "no", NOTE)
    _page(c, store, "<p>v2</p>", fid=fid)
    quoted = "You wrote: the table of evidence is missing its sources, and the second section repeats the first one"
    for said in (
        {"note": quoted},
        {"changed": [quoted[:120]]},
        {"answered": [f"Sources: done, {quoted}"]},
    ):
        with _refused("Don't quote their note back; say what you did with each point"):
            _attach(c, row, "page:" + fid, **said)
    for said, why in (
        ({"answered": ["Sources were added"]}, '--answered takes "<point>: done|not done, <why>"'),
        ({"changed": ["one", "two", "three", "four", "five", "six"]}, "at most 5 lines"),
        ({"changed": ["x" * 121]}, "at most 120 characters"),
        ({"answered": [f"Point {i}: done, ok" for i in range(13)]}, "at most 12 points"),
    ):
        with _refused(why):
            _attach(c, row, "page:" + fid, **said)
    assert [r["version"] for r in PR.rounds(c, row["id"], rid)] == [1], "nothing refused was attached"
    assert _attach(c, row, "page:" + fid, changed=["Sources on every row"])["version"] == 2


def test_the_rounds_route_reads_for_a_task_reader_and_the_attach_route_takes_the_new_fields(api, monkeypatch):
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
        json={
            "requirement": "Spec page",
            "page": "page:" + fid,
            "note": "First draft for the importer",
            "changed": ["Wrote the importer spec"],
            "answered": [],
        },
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    bad = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:" + fid, "answered": ["Sources added"]},
        headers=headers("ana-test"),
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "lint", bad.text
    got = api.get(f"/api/v2/tasks/{row['id']}/asks/{rid}/rounds", headers=headers("ana-test"))
    assert got.status_code == 200, got.text
    assert got.json()["notices"] == [], "nothing besides Jev while no commits or conflict came"
    (only,) = got.json()["rounds"]
    assert (only["version"], only["note"], only["changed"]) == (
        1,
        "First draft for the importer",
        ["Wrote the importer spec"],
    )
    # The notices the standing keeps (new commits, a merge conflict) reach the decision view through this route.
    monkeypatch.setattr(PR, "notices", lambda conn, task_id, requirement_id: ["New commits since Ana's OK."])
    again = api.get(f"/api/v2/tasks/{row['id']}/asks/{rid}/rounds", headers=headers("ana-test"))
    assert again.json()["notices"] == ["New commits since Ana's OK."]
    with store.transaction() as conn:
        conn.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
    hidden = api.get(f"/api/v2/tasks/{row['id']}/asks/{rid}/rounds", headers=headers("cara-test"))
    assert hidden.status_code in (403, 404), hidden.text


def test_tico_task_attach_sends_changed_and_answered_with_a_page(monkeypatch):
    from clients import hubcli, remotecli

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
    argv = ["task", "attach", "T-1", "page:file-abc@3", "--req", "Spec page", "--version", "4"]
    argv += ["--changed", "Sources on every row", "--changed", "Cut the repeated section"]
    argv += ["--answered", "Sources: done, each row links its query", "--note", "Version 3"]
    remotecli.run(hubcli.parser().parse_args(argv))
    assert sent[-1] == (
        "POST",
        "tasks/T-1/pages",
        {
            "requirement": "Spec page",
            "page": "page:file-abc@3",
            "version": 4,
            "note": "Version 3",
            "changed": ["Sources on every row", "Cut the repeated section"],
            "answered": ["Sources: done, each row links its query"],
        },
    )
