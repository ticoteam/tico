"""Flow's opinion on each ask (backend/flow_opinions.py): a score worked out when Flow opens, saved once per ask and
shared, so it must reach only someone the ask waits on, hold for 24 hours and only while the ask is as it was, never be
worked out twice at once, and fail to `none` without ever holding the screen up."""

import json
import threading

import pytest

from backend import flow_opinions as F
from backend import updates
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.page_requirement import _page

ANA, BEN = "human:ana", "human:ben"
GOOD = {"score": 70}
NONE = {"state": "none", "score": None}
STARTED: list[threading.Thread] = []  # the opinions' threads, this test's


class Model:
    """A writer that answers in turn from `answers` (the last one again once they run out) and keeps each prompt."""

    def __init__(self, *answers):
        self.answers, self.prompts, self.systems, self.model = list(answers), [], [], "acme-opinion-model"

    def __call__(self, system, user):
        self.prompts.append(user)
        self.systems.append(system)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def threads(monkeypatch):
    """Each opinion is worked out on its own thread, as in production: a commit hook runs inside the write it follows."""
    STARTED.clear()

    def spawn(work):
        STARTED.append(threading.Thread(target=work))
        STARTED[-1].start()

    monkeypatch.setattr(F, "spawn", spawn)


def model(monkeypatch, *answers):
    found = Model(*answers)
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: found)
    return found


def ask_ben(api, line="OK the launch note?"):
    """Ana asks Ben for an OK on a task of hers, titled after the ask; the ask's Needs-you id, as Ben's list gives
    it."""
    with api.app.state.store.transaction() as c:
        title = "Write " + line.removeprefix("OK ").rstrip("?")
        task = H.task_create(c, ANA, title, "Due Friday. It must match PO 1182.", ANA, lint=False)
        version = H.task(c, task["id"])["version"]
        asked = H.task_ask(c, ANA, task["id"], BEN, line, kind="ok", seen_version=version)
    items = get(api, "needs-you", token="ben-test")["items"]
    return task, next(i["id"] for i in items if i.get("requirement_id") == asked["requirement_id"])


def page_ask(api, text, people=None, private_home=False, versioned=True, title="Write the price page"):
    """Ana asks Ben, on a task of hers, to OK a page (a Tico file) at its first version: a page of that task, or with
    `private_home` of another task of hers that is private. `people`, when given, are the only ones who may open it;
    `versioned` False shows it as `page:<id>`, at whatever its version is. (task, Needs-you id, file id)."""
    store = api.app.state.store
    with store.transaction() as c:
        X.bot(c, "writer")
        task = H.task_create(c, ANA, title, "Due Friday. It must match PO 1182.", ANA, lint=False)
        home = task
        if private_home:
            home = H.task_create(c, ANA, "Salary review", "Private.", ANA, private=True, lint=False)
        fid, version = _page(c, store, text)
        c.execute("UPDATE bot_files SET scope=?, task_id=? WHERE id=?", ("task:" + home["id"], home["id"], fid))
        if people:
            c.execute(
                "INSERT INTO pages(file_id, audience, created_at, updated_at) VALUES (?,?,?,?)",
                (fid, json.dumps({"people": people}), H.now(), H.now()),
            )
        shows = [f"page:{fid}@{version}" if versioned else f"page:{fid}"]
        asked = H.task_ask(
            c,
            ANA,
            task["id"],
            BEN,
            "OK the price page?",
            kind="ok",
            shows=shows,
            seen_version=H.task(c, task["id"])["version"],
        )
    items = get(api, "needs-you", token="ben-test")["items"]
    return task, next(i["id"] for i in items if i.get("requirement_id") == asked["requirement_id"]), fid


def give_cara(api, task, item):
    with api.app.state.store.transaction() as c:
        H.task_give(c, BEN, task["id"], item.partition(":")[2], "human:cara", H.task(c, task["id"])["version"])


def rows(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ask_opinions")]


def opinions(api, ids, token="ben-test", expected=200, wait=True):
    """The call's answers by id; with `wait`, once the work it started is done."""
    found = post(api, "flow/opinions", {"items": ids}, token=token, expected=expected)
    for thread in STARTED if wait else []:
        thread.join(10)
    return {o["id"]: o for o in found["opinions"]} if expected == 200 else found


def test_an_ask_gets_its_opinion_once_and_everyone_after_reads_the_saved_one(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, item = ask_ben(api)
    assert opinions(api, [item])[item]["state"] == "pending"
    assert opinions(api, [item])[item] == {"id": item, "state": "ready", **GOOD}
    assert len(writer.prompts) == 1
    prompt = writer.prompts[0]
    assert "Write the launch note" in prompt and "OK the launch note?" in prompt and "PO 1182" in prompt
    assert "Kind: an OK (approve or send back)" in prompt and '{"score": 0}' in writer.systems[0]
    assert "ben" not in prompt.lower(), "written from the task alone, never from who is looking"


def test_a_reader_gets_nothing_for_an_ask_that_is_not_on_their_own_needs_you(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    task, item = ask_ben(api)
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "ready"
    for token in ("cara-test", "ana-test"):  # Cara reads none of it; Ana asked, and the ask waits on Ben, not her
        got = opinions(api, [item, task["id"], "nothing:here"], token=token)
        assert list(got.values()) == [{"id": i, **NONE} for i in (item, task["id"], "nothing:here")]
    assert len(writer.prompts) == 1
    with api.app.state.store.transaction() as c:  # an ask that leaves Ben's list takes its opinion with it
        H.task_close(c, ANA, task["id"], "Done elsewhere.")
    assert opinions(api, [item])[item] == {"id": item, **NONE}


def test_an_opinion_runs_out_after_a_day_and_when_the_ask_changes_but_not_on_a_comment(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    task, item = ask_ben(api)
    opinions(api, [item])
    visit, _, rid = item.partition(":")
    with api.app.state.store.transaction() as c:
        H.task_comment(c, ANA, task["id"], "Unrelated: lunch is at noon.", wake=False)
    assert opinions(api, [item])[item]["state"] == "ready" and len(writer.prompts) == 1
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE ask_opinions SET written_at=? WHERE visit_id=? AND requirement_id=?",
            (H.shift(H.now(), hours=-25), visit, rid),
        )
    assert opinions(api, [item])[item]["state"] == "pending"
    assert opinions(api, [item])[item]["state"] == "ready" and len(writer.prompts) == 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE requirements SET line='OK the launch note and the price?' WHERE id=?", (rid,))
    assert opinions(api, [item])[item]["state"] == "pending"
    assert len(writer.prompts) == 3 and "the price?" in writer.prompts[-1]


def test_a_page_ok_is_read_at_the_version_it_asks_about_and_only_moving_that_ref_makes_a_new_ask(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, item, fid = page_ask(api, "<p>Version one says 40 dollars.</p>")
    assert api.get(f"/api/v2/files/{fid}/versions/1", headers=headers("ben-test")).status_code == 200
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "ready"
    assert "40 dollars" in writer.prompts[0]
    store = api.app.state.store
    with store.transaction() as c:  # a newer version of the page: the ask is still about version 1
        _page(c, store, "<p>Version two says 90 dollars.</p>", fid=fid)
    assert opinions(api, [item])[item]["state"] == "ready" and len(writer.prompts) == 1
    visit, _, rid = item.partition(":")
    with store.transaction() as c:  # worked out again (a day on), it still reads version 1
        c.execute(
            "UPDATE ask_opinions SET written_at=? WHERE visit_id=? AND requirement_id=?",
            (H.shift(H.now(), hours=-25), visit, rid),
        )
    opinions(api, [item])
    assert "40 dollars" in writer.prompts[1] and "90 dollars" not in writer.prompts[1]
    with store.transaction() as c:  # the ask moves to version 2: a new ask, read at version 2
        c.execute("UPDATE requirements SET shows=? WHERE id=?", (json.dumps([f"page:{fid}@2"]), rid))
    assert opinions(api, [item])[item]["state"] == "pending"
    assert "90 dollars" in writer.prompts[2] and "40 dollars" not in writer.prompts[2]


def test_a_reader_who_may_not_open_the_page_gets_none_even_with_an_opinion_saved(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    task, item, _ = page_ask(api, "<p>Salaries: 40 dollars an hour.</p>", people=["ana", "ben"])
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "ready"
    give_cara(api, task, item)  # on Cara's Needs you now, but she may not open the page
    assert item in {i["id"] for i in get(api, "needs-you", token="cara-test")["items"]}
    assert opinions(api, [item], token="cara-test")[item] == {"id": item, **NONE}
    assert len(writer.prompts) == 1 and [r["state"] for r in rows(api)] == ["ready"]


def test_two_readers_of_one_ask_share_one_opinion(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    task, item, _ = page_ask(api, "<p>Version one says 40 dollars.</p>")
    opinions(api, [item])
    ben = opinions(api, [item])[item]
    give_cara(api, task, item)
    assert opinions(api, [item], token="cara-test")[item] == ben
    assert len(writer.prompts) == 1 and len(rows(api)) == 1


def test_a_page_on_a_private_task_the_reader_may_not_open_never_reaches_the_model(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, item, fid = page_ask(api, "<p>Salaries: 40 dollars an hour.</p>", private_home=True)
    assert api.get(f"/api/v2/files/{fid}/versions/1", headers=headers("ben-test")).status_code == 404
    assert opinions(api, [item])[item] == {"id": item, **NONE}
    assert writer.prompts == [] and rows(api) == []


def test_a_page_shown_at_no_version_is_read_and_hashed_at_its_current_one(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, item, fid = page_ask(api, "<p>Version one says 40 dollars.</p>", versioned=False)
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "ready" and "40 dollars" in writer.prompts[0]
    store = api.app.state.store
    with store.transaction() as c:
        _page(c, store, "<p>Version two says 90 dollars.</p>", fid=fid)
    assert opinions(api, [item])[item]["state"] == "pending", "the advice on version 1 is not served beside version 2"
    assert "90 dollars" in writer.prompts[1] and "40 dollars" not in writer.prompts[1]
    assert f"page:{fid}@2" in writer.prompts[1]


def spend(api):
    with api.app.state.store.read() as c:
        return [json.loads(r["detail"]) for r in c.execute("SELECT detail FROM events WHERE action='judge.spend'")]


def test_each_attempt_is_metered_and_over_the_cap_is_none_with_no_call(api, monkeypatch):
    writer = model(monkeypatch, {"line": "No score here."}, GOOD)
    _, item = ask_ben(api)
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "none", "an answer with no score is no opinion, and paid for"
    _, again = ask_ben(api, line="OK the first note?")
    opinions(api, [again])
    assert opinions(api, [again])[again]["state"] == "ready" and len(writer.prompts) == 2
    assert [(s["label"], s["model"]) for s in spend(api)] == [("flow-opinion", "acme-opinion-model")] * 2
    store = api.app.state.store
    with store.transaction() as c:  # a cap with no room for one more call: the meter refuses it
        c.execute(
            "INSERT INTO registry_metadata VALUES('maintenance', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"monthly_cap_usd": sum(s["cost_usd"] for s in spend(api)) + 0.000001}),),
        )
    writer = model(monkeypatch, GOOD)
    _, other = ask_ben(api, line="OK the second note?")
    opinions(api, [other])
    assert opinions(api, [other])[other]["state"] == "none" and writer.prompts == [] and len(spend(api)) == 2
    with store.transaction() as c:  # the cap reached: nothing is even claimed
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='maintenance'", (json.dumps({"monthly_cap_usd": 0.0001}),)
        )
    _, third = ask_ben(api, line="OK the third note?")
    assert opinions(api, [third])[third]["state"] == "none" and writer.prompts == [] and len(rows(api)) == 3


def test_a_score_is_kept_to_0_to_100_and_an_answer_with_none_is_no_opinion(api, monkeypatch):
    for answer, score in (({"score": 250}, 100), ({"score": "40"}, 40), ({"score": -3}, 0)):
        model(monkeypatch, answer)
        _, item = ask_ben(api, line=f"OK note {score}?")
        opinions(api, [item])
        assert opinions(api, [item])[item] == {"id": item, "state": "ready", "score": score}
    for n, answer in enumerate(({"pick": "approve", "line": "Approve. It looks fine."}, {"score": "high"}, "70")):
        model(monkeypatch, answer)
        _, item = ask_ben(api, line=f"OK the other note {n}?")
        opinions(api, [item])
        assert opinions(api, [item])[item] == {"id": item, **NONE}
    assert [r["error"] for r in rows(api) if r["state"] == "none"] == ["shape"] * 3


def test_a_failure_or_no_model_is_none_and_nothing_waits(api, monkeypatch):
    model(monkeypatch, RuntimeError("provider down"))
    _, item = ask_ben(api)
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "none"
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: None)
    _, other = ask_ben(api, line="OK the other note?")
    assert opinions(api, [other])[other]["state"] == "none"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM ask_opinions").fetchone()[0] == 1, "no claim without a model"


def test_two_callers_at_once_work_out_an_ask_once(api, monkeypatch):
    release, calls = threading.Event(), []

    def slow(system, user):
        calls.append(user)
        assert release.wait(10)
        return GOOD

    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: slow)
    _, item = ask_ben(api)
    assert opinions(api, [item], wait=False)[item]["state"] == "pending"
    assert opinions(api, [item], wait=False)[item]["state"] == "pending"
    release.set()
    for thread in STARTED:
        thread.join(10)
    assert len(STARTED) == 1 and len(calls) == 1
    assert opinions(api, [item])[item]["state"] == "ready"


def test_one_call_takes_at_most_30_ids_and_works_out_at_most_the_pool_at_once(api, monkeypatch):
    opinions(api, [f"x:{n}" for n in range(F.MAX_ITEMS + 1)], expected=422)
    model(monkeypatch, GOOD)
    monkeypatch.setattr(F, "spawn", lambda work: None)  # every claim stays running: its slot stays taken
    monkeypatch.setattr(F, "CONCURRENCY", 2)
    items = [ask_ben(api, line=f"OK note {n}?")[1] for n in range(3)]
    got = opinions(api, items)
    assert [got[i]["state"] for i in items] == ["pending"] * 3
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM ask_opinions").fetchone()[0] == 2, "the third waits for a slot"


LONG = "".join(
    f"<p>AC-{n}: the import keeps row {n} as it was given, with its date and amount.</p>" for n in range(400)
)


def test_a_long_page_is_read_to_30000_characters_and_marked_where_tico_cut_it(api, monkeypatch):
    """Tico Team, rc.48: a spec cut at 6,000 got "The page cuts off mid-AC-20; AC-21 to AC-24 are missing. Send back"."""
    found = model(monkeypatch, GOOD)
    _, item, _ = page_ask(api, LONG[: len(LONG) // 2])  # about 15,000 characters: read whole, no mark
    opinions(api, [item])
    assert "AC-199:" in found.prompts[0] and F.CUT not in found.prompts[0]
    _, item, _ = page_ask(
        api, LONG, title="Write the import spec"
    )  # about 30,000 and more: cut, and the cut says it is Tico's
    opinions(api, [item])
    prompt = found.prompts[1]
    assert "AC-399:" not in prompt and prompt.rstrip().endswith(F.CUT)
    assert len(prompt.partition("What the ask shows:\n")[2]) <= F.SHOWN_MAX + len(F.CUT) + 1
    assert f'stops at "{F.CUT}"' in found.systems[1]


def test_an_ok_that_shows_no_page_is_read_from_the_task_text(api, monkeypatch):
    """Tico Team, rc.48: "OK the pitch" was read as if it showed nothing; the task's own text is what is OK'd."""
    found = model(monkeypatch, GOOD)
    _, item = ask_ben(api, line="OK the pitch?")
    opinions(api, [item])
    prompt = found.prompts[0]
    assert "What the ask shows: no page. The task's own text above is what is being OK'd." in prompt
    assert "Due Friday. It must match PO 1182." in prompt


def test_a_score_written_under_older_instructions_still_stands(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, item, _ = page_ask(api, "<p>Version one says 40 dollars.</p>")
    opinions(api, [item])
    assert [r["prompt_version"] for r in rows(api)] == [F.PROMPT_VERSION]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE ask_opinions SET prompt_version=0")
    assert opinions(api, [item])[item] == {"id": item, "state": "ready", **GOOD}
    assert len(writer.prompts) == 1, "not worked out again"


def test_a_persons_snooze_holds_the_opinions_fingerprint_of_the_ask(api, monkeypatch):
    """The opinion and a person's snooze of the ask share one fingerprint (`ask_version`), which keeps the keys a
    question once filled: an ask a person snoozed before questions went keeps its hash."""
    model(monkeypatch, GOOD)
    _, item = ask_ben(api)
    opinions(api, [item])
    before = rows(api)[0]["ask_version"]
    post(api, "needs-you/snooze", {"id": item, "until": H.shift(H.now(), days=1)}, token="ben-test")
    with api.app.state.store.read() as c:
        snoozed = c.execute("SELECT ask_version FROM ask_snoozes").fetchone()[0]
    assert snoozed == before
    assert item not in {i["id"] for i in get(api, "needs-you", token="ben-test")["items"]}
