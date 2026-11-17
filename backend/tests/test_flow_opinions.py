"""Flow's opinion on each ask (backend/flow_opinions.py): worked out when Flow opens, saved once per ask and shared, so
it must reach only someone the ask waits on, hold for 24 hours and only while the ask is as it was, keep its line to 80
characters, never be worked out twice at once, and fail to `none` without ever holding the screen up."""

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
GOOD = {"pick": "approve", "line": "Approve. It matches PO 1182, and it is due Friday.", "note": "", "score": 70}
NONE = {"state": "none", "pick": "", "line": "", "note": "", "score": None}
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


def ask_ben(api, line="OK the launch note?", kind="answer", choices=None, **more):
    """Ana asks Ben on a task of hers, titled after the ask; the ask's Needs-you id, as Ben's list gives it. An OK
    (`kind` "ok"), or a question as stored before a question was a ❓ (old_question): Flow's opinions read those."""
    with api.app.state.store.transaction() as c:
        title = "Write " + line.removeprefix("OK ").rstrip("?")
        task = H.task_create(c, ANA, title, "Due Friday. It must match PO 1182.", ANA, lint=False)
        if kind == "answer":
            asked = X.old_question(c, task["id"], ANA, BEN, line, choices=choices, **more)
        else:
            version = H.task(c, task["id"])["version"]
            asked = H.task_ask(c, ANA, task["id"], BEN, line, kind=kind, choices=choices, seen_version=version, **more)
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
    _, item = ask_ben(api, kind="ok")
    assert opinions(api, [item])[item]["state"] == "pending"
    assert opinions(api, [item])[item] == {"id": item, "state": "ready", **GOOD}
    assert len(writer.prompts) == 1
    prompt = writer.prompts[0]
    assert "Write the launch note" in prompt and "OK the launch note?" in prompt and "PO 1182" in prompt
    assert "ben" not in prompt.lower(), "written from the task alone, never from who is looking"


def test_a_reader_gets_nothing_for_an_ask_that_is_not_on_their_own_needs_you(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    task, item = ask_ben(api, kind="ok")
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
    task, item = ask_ben(api, kind="ok")
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
    long = {**GOOD, "line": "Approve. " + "It matches the purchase order and the receipt in every line. " * 2}
    writer = model(monkeypatch, long, GOOD)
    _, item = ask_ben(api, kind="ok")
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "ready" and len(writer.prompts) == 2
    assert [(s["label"], s["model"]) for s in spend(api)] == [("flow-opinion", "acme-opinion-model")] * 2
    store = api.app.state.store
    with store.transaction() as c:  # a cap with no room for one more call: the meter refuses it
        c.execute(
            "INSERT INTO registry_metadata VALUES('maintenance', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"monthly_cap_usd": sum(s["cost_usd"] for s in spend(api)) + 0.000001}),),
        )
    writer = model(monkeypatch, GOOD)
    _, other = ask_ben(api, line="OK the second note?", kind="ok")
    opinions(api, [other])
    assert opinions(api, [other])[other]["state"] == "none" and writer.prompts == [] and len(spend(api)) == 2
    with store.transaction() as c:  # the cap reached: nothing is even claimed
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='maintenance'", (json.dumps({"monthly_cap_usd": 0.0001}),)
        )
    _, third = ask_ben(api, line="OK the third note?", kind="ok")
    assert opinions(api, [third])[third]["state"] == "none" and writer.prompts == [] and len(rows(api)) == 2


def test_a_line_over_80_characters_is_asked_for_once_more_then_given_up(api, monkeypatch):
    long = {**GOOD, "line": "Approve. " + "It matches the purchase order and the receipt in every line. " * 2}
    writer = model(monkeypatch, long, GOOD)
    _, item = ask_ben(api, kind="ok")
    opinions(api, [item])
    assert opinions(api, [item])[item]["line"] == GOOD["line"] and len(writer.prompts) == 2
    assert "at most 80" in writer.prompts[1]
    writer = model(monkeypatch, long)
    _, other = ask_ben(api, line="OK the second note?", kind="ok")
    opinions(api, [other])
    assert opinions(api, [other])[other]["state"] == "none" and len(writer.prompts) == 2


def test_an_ok_picks_a_button_and_a_question_one_of_its_choices_or_nothing(api, monkeypatch):
    back = {"pick": "Send back", "line": "Send back. No price is given.", "note": "Add the price.", "score": 250}
    model(monkeypatch, back)
    _, item = ask_ben(api, kind="ok")
    opinions(api, [item])
    got = opinions(api, [item])[item]
    assert (got["pick"], got["note"], got["score"]) == ("send_back", "Add the price.", 100)
    model(monkeypatch, {"pick": "next month", "line": "Next month. The venue is booked until the 20th.", "score": 40})
    _, item = ask_ben(api, line="When should the offsite be?", choices=["This week", "Next month"])
    opinions(api, [item])
    got = opinions(api, [item])[item]
    assert (got["pick"], got["note"], got["score"]) == ("Next month", "", 40)
    assert got["line"] == "Pick Next month: The venue is booked until the 20th.", "a bare choice reads as done"
    model(monkeypatch, {"pick": "Never", "line": "Nothing says who hosts it; the venue would know.", "note": "Who?"})
    _, item = ask_ben(api, line="Which venue?", choices=["The hall", "The park"])
    opinions(api, [item])
    got = opinions(api, [item])[item]
    assert (got["state"], got["pick"], got["note"], got["score"]) == ("ready", "", "", 50)
    model(monkeypatch, {"pick": "maybe", "line": "Maybe."})  # not a button: no opinion
    _, item = ask_ben(api, line="OK the third note?", kind="ok")
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "none"


CHANNELS = "Which channels do you sell on, and where do your spend and customer numbers live?"


def test_a_question_is_asked_as_a_question_for_the_person_and_never_judged_as_a_page(api, monkeypatch):
    """Tico Team, Nov 15 (#3786): a bot's "which channels" got "Can't answer yet. Page names no channels or systems"."""
    wants = {"pick": "", "line": "Wants a list: your channels, and where spend and customer numbers live", "score": 60}
    found = model(monkeypatch, wants)
    _, item = ask_ben(api, line=CHANNELS)
    opinions(api, [item])
    assert opinions(api, [item])[item] == {"id": item, "state": "ready", "note": "", **wants}
    system, prompt = found.systems[0], found.prompts[0]
    assert system == F.QUESTION_SYSTEM != F.SYSTEM
    assert "send_back" not in system and "Unanswerable as written" in system and "empty" in system
    assert "Kind: a question for the person to answer, in their own words" in prompt
    assert "What the ask shows" not in prompt, "a question shows no page, so none is named to it"
    assert CHANNELS in prompt and "PO 1182" in prompt, "the task's own text, where the answer may be"
    found = model(monkeypatch, {"pick": "OK", "line": "Approve. It looks right."})
    _, item = ask_ben(api, line="OK the pitch?", kind="ok")
    opinions(api, [item])
    assert found.systems == [F.SYSTEM], "an OK keeps its own instructions"


@pytest.mark.parametrize(
    "line",
    [
        "Can't answer yet. Page names no channels or systems.",
        "Send back. The ask is too vague.",
        "Approve. It looks fine.",
        "Cannot answer: the task holds no list.",
        "Can't tell. Page doesn't say which channels.",
        "Not sure yet.",
        "No pick yet.",
        "No pick. Plan rows aren't shown here",
    ],
)
def test_a_question_line_that_judges_it_as_an_ok_is_no_line(api, monkeypatch, line):
    model(monkeypatch, {"pick": "send_back", "line": line, "note": "Name the channels.", "score": 70})
    _, item = ask_ben(api, line=CHANNELS)
    opinions(api, [item])
    assert opinions(api, [item])[item] == {"id": item, **NONE}


def test_a_question_line_saved_before_its_own_instructions_is_worked_out_again(api, monkeypatch):
    writer = model(monkeypatch, {"pick": "", "line": "Wants a list: your channels, and where the numbers live"})
    _, item = ask_ben(api, line=CHANNELS)
    opinions(api, [item])
    with api.app.state.store.transaction() as c:  # what Tico Team saved under the OK's instructions
        c.execute("UPDATE ask_opinions SET line=?", ("Can't answer yet. Page names no channels or systems.",))
    assert opinions(api, [item])[item]["state"] == "pending"
    assert opinions(api, [item])[item]["line"] == "Wants a list: your channels, and where the numbers live"
    assert len(writer.prompts) == 2
    assert opinions(api, [item])[item]["state"] == "ready" and len(writer.prompts) == 2, "then saved, as any other"


def test_an_ok_has_no_snooze_so_the_model_is_not_offered_it_and_a_snooze_pick_saves_nothing(api, monkeypatch):
    found = model(monkeypatch, {"pick": "snooze", "line": "Snooze. It is not due until Friday.", "score": 20})
    _, item = ask_ben(api, line="OK the pitch?", kind="ok")
    opinions(api, [item])
    assert opinions(api, [item])[item] == {"id": item, **NONE}
    assert [(r["state"], r["pick"], r["line"], r["error"]) for r in rows(api)] == [("none", "", "", "shape")]
    assert "snooze" not in (F.SYSTEM + found.prompts[0]).casefold()
    assert "Kind: an OK (approve or send_back)" in found.prompts[0]


def test_a_failure_or_no_model_is_none_and_nothing_waits(api, monkeypatch):
    model(monkeypatch, RuntimeError("provider down"))
    _, item = ask_ben(api, kind="ok")
    opinions(api, [item])
    assert opinions(api, [item])[item]["state"] == "none"
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: None)
    _, other = ask_ben(api, line="OK the other note?", kind="ok")
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
    _, item = ask_ben(api, kind="ok")
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
    items = [ask_ben(api, line=f"OK note {n}?", kind="ok")[1] for n in range(3)]
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
    assert f'stops at "{F.CUT}"' in found.systems[1] and "never fault or send back a page for being cut" in F.SYSTEM


def test_an_ok_that_shows_no_page_is_judged_on_the_task_text_as_the_pitch(api, monkeypatch):
    """Tico Team, rc.48: "OK the pitch" got "The pitch lists no appetite, owner or due date. Send back"."""
    found = model(monkeypatch, GOOD)
    _, item = ask_ben(api, line="OK the pitch?", kind="ok")
    opinions(api, [item])
    prompt = found.prompts[0]
    assert "What the ask shows: no page. The task's own text above is what is being OK'd." in prompt
    assert "Due Friday. It must match PO 1182." in prompt
    assert "never send it back for lacking a due date, an owner, an appetite, scope detail or a design" in F.SYSTEM


def saved_under_old_instructions(api, line):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE ask_opinions SET prompt_version=0, line=?", (line,))


def test_rows_written_under_old_instructions_are_worked_out_again_only_where_the_new_ones_differ(api, monkeypatch):
    writer = model(monkeypatch, GOOD)
    _, short, _ = page_ask(api, "<p>Version one says 40 dollars.</p>")
    _, long, _ = page_ask(
        api, LONG[:8000], title="Write the import spec"
    )  # over the old 6,000 cut, so its line was read from part of it
    _, pitch = ask_ben(api, line="OK the pitch?", kind="ok")
    opinions(api, [short, long, pitch])
    assert [r["prompt_version"] for r in rows(api)] == [F.PROMPT_VERSION] * 3 and len(writer.prompts) == 3
    saved_under_old_instructions(api, "Send back. The page cuts off mid-AC-20.")
    got = opinions(api, [short, long, pitch])
    assert (got[short]["state"], got[long]["state"], got[pitch]["state"]) == ("ready", "pending", "pending")
    assert len(writer.prompts) == 5
    got = opinions(api, [short, long, pitch])
    assert {o["line"] for o in got.values()} == {GOOD["line"], "Send back. The page cuts off mid-AC-20."}
    assert got[short]["line"] == "Send back. The page cuts off mid-AC-20.", "a short page read whole keeps its line"
    assert [r["prompt_version"] for r in rows(api)] == [F.PROMPT_VERSION] * 3, "and is marked current, read once"
    assert len(writer.prompts) == 5


def test_a_question_written_under_old_instructions_is_redone_and_its_snooze_fingerprint_stays(api, monkeypatch):
    """Tico Team, rc.48: "Removed it. Seen 4 times..." and "Not sure yet." outlived QUESTION_SYSTEM by up to a day."""
    writer = model(monkeypatch, {"pick": "Yes", "line": "Yes. The task says it was seen 4 times.", "score": 30})
    _, item = ask_ben(api, line="Did the bot remove it?", choices=["Yes", "No"])
    opinions(api, [item])
    before = rows(api)[0]["ask_version"]
    saved_under_old_instructions(api, "Removed it. Seen 4 times.")
    assert opinions(api, [item])[item]["state"] == "pending"
    assert opinions(api, [item])[item]["line"] == "Pick Yes: The task says it was seen 4 times."
    assert len(writer.prompts) == 2 and rows(api)[0]["ask_version"] == before, "the ask is the same ask"
    post(api, "needs-you/snooze", {"id": item, "until": H.shift(H.now(), days=1)}, token="ben-test")
    with api.app.state.store.read() as c:
        snoozed = c.execute("SELECT ask_version FROM ask_snoozes").fetchone()[0]
    assert snoozed == before, "a person's snooze holds the same fingerprint, so redoing a line ends no snooze"
    assert item not in {i["id"] for i in get(api, "needs-you", token="ben-test")["items"]}


def test_an_old_row_whose_page_cannot_be_read_now_is_worked_out_again(api, monkeypatch):
    model(monkeypatch, GOOD)
    _, item, _ = page_ask(api, "<p>Version one says 40 dollars.</p>")
    opinions(api, [item])
    saved_under_old_instructions(api, GOOD["line"])

    def unreadable(c, job):
        raise OSError("blob store away")

    monkeypatch.setattr(F, "whole", unreadable)
    assert opinions(api, [item])[item]["state"] == "pending", "claimed again, never served as if it were current"
