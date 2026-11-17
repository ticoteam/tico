"""A bot's ❓ on its own chat message (backend/question_marks.py): it needs one named person's reply, files no task and
sends nothing. Synthetic rows only: the fictional company's people and bots."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as NS
from backend import org_rules as OR
from backend import question_marks as Q
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

ANA = X.ANA
BUILDER, MANAGER = "bot:builder", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
ASKED = "Should the launch note go out on Friday?"


# ---- the engine: the ladder, the list, the clears ------------------------------------------------------------------


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager=MANAGER)


@pytest.fixture
def later(c, time_machine):
    def step(**delta):
        time_machine.shift(timedelta(**delta))
        done = NS.check(c)
        assert NS.rules_hold(c) == []
        return {key: n for key, n in done.items() if n}

    return step


def _ask(c, task_id, bot=BUILDER, person=ANA, text=ASKED):
    said = H.task_comment(c, bot, task_id, text, wake=False)
    return Q.mark(c, bot, said["id"], person)


def _open(c, task_id):
    return c.execute("SELECT * FROM question_marks WHERE task_id=? AND cleared_at IS NULL", (task_id,)).fetchall()


def test_a_bot_waiting_on_its_own_question_mark_is_not_chased_and_is_again_once_the_person_replied(c, general, later):
    """Silent break 1: the never-stuck ladder never wakes a bot for a task it left waiting on its own ❓."""
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    X.ran(c, task_id, BUILDER)
    mark = _ask(c, task_id)
    row = E.task(c, task_id)
    assert W.turn(c, row) is None
    assert [(w.state, w.who, w.ask) for w in W.of(c, row)] == [(W.PERSON, ANA, True)]
    for _ in range(4):
        assert later(hours=2) == {}  # nobody is woken, however long Ana takes
    [item] = [i for i in W.needs(c, ANA) if i["kind"] == "question"]
    assert item["id"] == "qmark:" + mark["id"] and item["task_id"] == task_id and item["line"] == ASKED

    H.task_comment(c, ANA, task_id, "Yes, Friday works.")
    assert _open(c, task_id) == []
    assert W.turn(c, E.task(c, task_id)) == BUILDER  # her reply hands the task back to the ladder
    assert [i for i in W.needs(c, ANA) if i["kind"] == "question"] == []
    X.ran(c, task_id, BUILDER)  # the run her reply started ended, and the bot did not answer
    assert later(seconds=900) == {"woken": 1}


def test_a_reply_by_the_persons_assistant_clears_it_and_says_so(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    mark = _ask(c, task_id)
    token = H.VIA.set("assistant")
    try:
        H.task_comment(c, ANA, task_id, "Ana says Friday works.")
    finally:
        H.VIA.reset(token)
    row = dict(c.execute("SELECT * FROM question_marks WHERE id=?", (mark["id"],)).fetchone())
    assert (row["cleared_reason"], row["cleared_by_id"], row["cleared_via"]) == ("replied", ANA, "assistant")


def test_a_reply_ends_the_bots_snooze_of_the_task(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    _ask(c, task_id)
    row = E.task(c, task_id)
    E.snooze(c, task_id, BUILDER, "waiting on Ana", "2099-01-01T00:00:00Z", seen_version=row["version"])
    H.task_comment(c, ANA, task_id, "Yes, Friday works.")
    assert not E.task(c, task_id).get("snooze")


def test_botops_writing_as_the_person_does_not_clear_it(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    _ask(c, task_id)
    token = H.VIA.set("botops")
    try:
        H.task_comment(c, ANA, task_id, "Checking in on this.")
    finally:
        H.VIA.reset(token)
    assert len(_open(c, task_id)) == 1


def test_one_open_question_mark_per_bot_per_chat_and_the_message_carries_it(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    first = _ask(c, task_id)
    second = _ask(c, task_id, text="Which tone should the note take?")
    [open_one] = _open(c, task_id)
    assert open_one["id"] == second["id"]
    gone = dict(c.execute("SELECT * FROM question_marks WHERE id=?", (first["id"],)).fetchone())
    assert gone["cleared_reason"] == "replaced"
    assert "question" not in (H.message(c, first["message_id"])["refs"] or {})
    assert H.message(c, second["message_id"])["refs"]["question"] == {"id": second["id"], "to": ANA}


@pytest.mark.parametrize("end", ["done", "closed", "merged", "deleted"])
def test_the_task_ending_clears_it(c, general, end):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    mark = _ask(c, task_id)
    if end == "done":
        E.move(c, task_id, "done", ANA, "Written")
    elif end == "closed":
        H.task_close(c, ANA, task_id, "Not needed")
    elif end == "merged":
        Q.end_tasks(c, [task_id], "merged", ANA)
    else:
        E.on_delete(c, [task_id], ANA)
    row = dict(c.execute("SELECT * FROM question_marks WHERE id=?", (mark["id"],)).fetchone())
    assert row["cleared_at"] and row["cleared_reason"] == end
    assert "question" not in (H.message(c, mark["message_id"])["refs"] or {})
    assert [i for i in W.needs(c, ANA) if i["kind"] == "question"] == []


def test_only_a_bot_marks_and_only_its_own_message(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    said = H.task_comment(c, ANA, task_id, ASKED, wake=False)
    with pytest.raises(H.Refused):
        Q.mark(c, ANA, said["id"], ANA)
    with pytest.raises(H.Refused):
        Q.mark(c, BUILDER, said["id"], ANA)  # not its message
    with pytest.raises(H.Refused):
        Q.mark(c, H.KEEPER, said["id"], ANA)


def test_a_question_mark_follows_the_shape_of_an_ask_to_a_person(c, general):
    task_id = X.new_task(c, ANA, "Write the launch note", assignee=BUILDER)["id"]
    said = H.task_comment(c, BUILDER, task_id, "See backend/question_marks.py line 12 and tell me", wake=False)
    with pytest.raises(H.Refused):
        Q.mark(c, BUILDER, said["id"], ANA)
    assert _open(c, task_id) == []


# ---- the routes: the org rule, the refusals, Give to, snooze, a direct chat ----------------------------------------


def ask_on(api, task, to="ana", text=ASKED, token="ops-test"):
    return api.post(f"/api/v2/tasks/{task['id']}/questions", json={"to": to, "text": text}, headers=headers(token))


def needs(api, token="ana-test"):
    view = get(api, "needs-you", token=token)
    return view["items"] if isinstance(view, dict) else view


def questions(api, token="ana-test"):
    return [i for i in needs(api, token) if i["kind"] == "question"]


def test_the_asks_to_a_person_rule_checks_a_question_mark(ruled):
    """Silent break 3: the built-in "Asks to a person" rule is judged for a bot's ❓, and refuses one that breaks it."""
    api, judge = ruled
    judge.needles = {OR.ASKS_TO_A_PERSON: "**"}
    task = task_for_ops(api)
    r = ask_on(api, task)
    assert r.status_code == 200, r.text
    [call] = judge.calls
    assert [rule_row["text"] for rule_row in call["state"]["rules"]] == [OR.ASKS_TO_A_PERSON]
    r = ask_on(api, task, text="Should it go out **Friday**?")
    assert r.status_code == 200 and r.json()["held"]["state"] == "held", r.text  # held for Ana: not posted, not marked
    assert "question" not in r.json()
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body LIKE '%**Friday**%'")[0]["n"] == 0
    assert sql(api, "SELECT count(*) AS n FROM question_marks WHERE line LIKE '%Friday**%'")[0]["n"] == 0


def test_a_question_mark_is_in_the_persons_needs_you_until_they_reply_and_files_nothing(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    before = sql(api, "SELECT count(*) AS n FROM tasks")[0]["n"]
    r = ask_on(api, task)
    assert r.status_code == 200, r.text
    mark = r.json()["question"]
    assert sql(api, "SELECT count(*) AS n FROM tasks")[0]["n"] == before
    [item] = questions(api)
    assert (item["id"], item["from_id"], item["line"], item["message_id"]) == (
        "qmark:" + mark["id"],
        "bot:ops",
        ASKED,
        mark["message_id"],
    )
    assert questions(api, "ben-test") == []
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Yes, Friday.", "ask": mark["id"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    assert questions(api) == []


def test_only_a_bot_asks_and_only_someone_who_can_read_the_chat_is_asked(ruled):
    api, _ = ruled
    task = post(api, "tasks", {"owner": "ops", "title": "Write the launch note", "body": "Draft it.", "private": True})
    assert ask_on(api, task, token="ana-test").status_code == 403
    r = ask_on(api, task, to="cara")
    assert r.status_code == 422 and r.json()["error"]["code"] == "private", r.text
    assert "Cara can't see" in r.json()["error"]["detail"]  # the reason, and who can fix it
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body=?", (ASKED,))[0]["n"] == 0


def test_give_to_moves_an_open_question_mark_to_another_person(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = ask_on(api, task).json()["question"]
    r = api.post(f"/api/v2/questions/{mark['id']}/give", json={"to": "ben"}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert questions(api) == [] and [i["mark_id"] for i in questions(api, "ben-test")] == [mark["id"]]
    r = api.post(f"/api/v2/questions/{mark['id']}/give", json={"to": "ana"}, headers=headers("cara-test"))
    assert r.status_code in (403, 409), r.text


def test_a_snooze_of_a_question_mark_is_keyed_on_the_mark(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = ask_on(api, task).json()["question"]
    until = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = api.post("/api/v2/needs-you/snooze", json={"id": "qmark:" + mark["id"], "until": until}, headers=headers())
    assert r.status_code == 200, r.text
    assert questions(api) == []
    [row] = sql(api, "SELECT visit_id, requirement_id FROM ask_snoozes")
    assert row == {"visit_id": "qmark", "requirement_id": mark["id"]}
    again = ask_on(api, task, text="Which tone should the note take?").json()["question"]
    assert [i["mark_id"] for i in questions(api)] == [again["id"]]  # a new ❓ is a new mark: it is back


def test_a_question_mark_in_a_direct_chat_is_listed_and_the_reply_there_clears_it(ruled):
    api, _ = ruled
    r = api.post("/api/v2/questions", json={"to": "ana", "text": ASKED}, headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    mark = r.json()["question"]
    [item] = questions(api)
    assert item["task_id"] is None and item["conversation_id"] == mark["conversation_id"]
    r = api.post(
        "/api/v2/messages",
        json={"to": "ops", "text": "Friday is fine.", "conversation_id": mark["conversation_id"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    assert questions(api) == []


# ---- a bot's end-of-turn words on its own open task ---------------------------------------------------------------


def _turn(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Check the packet", "body": "Check the fixture packet"})
    with api.app.state.store.transaction() as c:  # a task not filed from a chat (H.task_from_chat), as a stage's is
        c.execute("DELETE FROM events WHERE action IN ('task.started_in', 'task.room') AND target=?", (task["id"],))
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    return machine, task, attempt


def _finish(api, machine, attempt, text):
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    return post(api, f"attempts/{attempt['id']}/complete", body, machine["token"], key="completion").get("message")


def _said(api, text):
    return sql(api, "SELECT count(*) AS n FROM messages WHERE sender_id='bot:ops' AND body=?", (text,))[0]["n"]


def test_a_bots_end_of_turn_text_on_its_open_task_is_posted_to_the_tasks_chat(api):
    machine, task, attempt = _turn(api)
    reply = _finish(api, machine, attempt, "Two items are left to check.")
    assert reply and reply["refs"]["task"] == task["id"]
    assert _said(api, "Two items are left to check.") == 1


def test_a_turn_that_already_wrote_in_the_tasks_chat_posts_no_end_of_turn_text(api):
    machine, task, attempt = _turn(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Halfway through the packet."}, attempt["token"])
    assert not _finish(api, machine, attempt, "Done with the first half.")
    assert _said(api, "Done with the first half.") == 0
