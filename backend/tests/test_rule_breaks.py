"""A Warn rule asks "Yes, block" or "Don't block" (backend/rule_breaks.py): each break of a bot's write is saved on the
rule, the person it went to (or an Admin, or the rule's editors) answers it, and a block takes the message back and
tells the bot. Synthetic rows only: the fictional company's people and bots."""

import pytest

from backend import org_rules as OR
from backend import rule_breaks as RB
from backend.store import H
from backend.tests.helpers.api import get, headers
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    color_rule,
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

COLORED = "Should the launch note say Acme's favorite color is teal?"
PLAIN = "Should the launch note go out on Friday?"


def ask_on(api, task, to="ana", text=COLORED, token="ops-test"):
    r = api.post(f"/api/v2/tasks/{task['id']}/questions", json={"to": to, "text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def ask_direct(api, to="ana", text=COLORED, token="ops-test"):
    r = api.post("/api/v2/questions", json={"to": to, "text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def breaks(api, where="1=1", args=()):
    return sql(api, f"SELECT * FROM org_rule_breaks WHERE {where} ORDER BY created_at, rowid", args)


def answer(api, break_id, value, token="ana-test", note=None):
    body = {"answer": value, **({"note": note} if note is not None else {})}
    return api.post(f"/api/v2/rule-breaks/{break_id}/answer", json=body, headers=headers(token))


def undo(api, break_id, token="ana-test"):
    return api.post(f"/api/v2/rule-breaks/{break_id}/undo", json={}, headers=headers(token))


def one_break(api):
    task = task_for_ops(api)
    asked = ask_on(api, task)
    [found] = breaks(api)
    return task, asked, found


def test_a_bots_question_that_breaks_two_warn_rules_is_saved_as_one_break_per_rule_on_its_message(ruled):
    api, judge = ruled
    made = color_rule(api, if_broken="warn")
    judge.needles = {COLOR: "favorite color is", OR.ASKS_TO_A_PERSON: "favorite color is"}
    task = task_for_ops(api)
    asked = ask_on(api, task)
    message = asked["comment"]["id"]
    found = breaks(api)
    builtin = sql(api, "SELECT id FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,))[0]["id"]
    assert sorted(b["rule_id"] for b in found) == sorted([made["id"], builtin])
    for b in found:
        assert (b["author"], b["message_id"], b["task_id"], b["answer"]) == ("bot:ops", message, task["id"], None)
        assert b["first_line"] == COLORED
        assert "human:ana" in b["recipients"]


def test_a_question_in_a_direct_chat_is_saved_on_its_message(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    asked = ask_direct(api)
    [b] = breaks(api)
    assert b["message_id"] == asked["id"] and b["task_id"] == "" and b["conversation_id"] == asked["conversation_id"]


def test_a_check_that_could_not_run_saves_no_break(ruled):
    api, judge = ruled
    color_rule(api, if_broken="warn")
    judge.fail = RuntimeError("the judge is out")
    ask_on(api, task_for_ops(api))
    assert breaks(api) == []


def test_a_question_that_keeps_the_rule_saves_no_break(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    ask_on(api, task_for_ops(api), text=PLAIN)
    assert breaks(api) == []


def test_dont_block_keeps_the_message_saves_the_answer_and_tells_the_bot_nothing(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task, asked, found = one_break(api)
    told = len(sql(api, "SELECT id FROM messages WHERE recipient_id='bot:ops'"))
    r = answer(api, found["id"], "pass", note="It's fine here")
    assert r.status_code == 200, r.text
    [after] = breaks(api)
    assert (after["answer"], after["answered_by"], after["note"]) == ("pass", "human:ana", "It's fine here")
    assert len(sql(api, "SELECT id FROM messages WHERE recipient_id='bot:ops'")) == told, "the bot is not told"
    assert (
        sql(api, "SELECT cleared_at FROM question_marks WHERE message_id=?", (asked["comment"]["id"],))[0]["cleared_at"]
        is None
    ), "the question still waits on its person"


def test_yes_block_clears_the_question_and_wakes_the_bot_on_its_task_with_the_rule_its_line_and_the_note(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task, asked, found = one_break(api)
    r = answer(api, found["id"], "block", note="Leave the color out")
    assert r.status_code == 200, r.text
    assert r.json()["break"]["answer"] == "block"
    [mark] = sql(
        api, "SELECT cleared_at, cleared_reason FROM question_marks WHERE message_id=?", (asked["comment"]["id"],)
    )
    assert mark["cleared_at"] and mark["cleared_reason"] == "rule_block"
    [told] = sql(
        api,
        "SELECT m.body, m.conversation_id FROM messages m WHERE m.recipient_id='bot:ops' AND m.body LIKE '%would block%'",
    )
    assert COLOR in told["body"] and COLORED in told["body"] and "Leave the color out" in told["body"]
    task_conv = sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]
    assert told["conversation_id"] == task_conv
    assert sql(api, "SELECT count(*) AS n FROM jobs WHERE bot_id='ops' AND state='queued'")[0]["n"] >= 1, "woken"


def test_yes_block_in_a_direct_chat_wakes_the_bot_in_that_chat(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    asked = ask_direct(api)
    [b] = breaks(api)
    assert answer(api, b["id"], "block").status_code == 200
    [told] = sql(api, "SELECT conversation_id FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%would block%'")
    assert told["conversation_id"] == asked["conversation_id"]


def test_someone_who_is_not_its_person_an_admin_or_an_editor_of_the_rule_may_not_answer(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    _, _, found = one_break(api)
    r = answer(api, found["id"], "block", token="cara-test")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["detail"] == RB.NOT_YOURS
    assert breaks(api)[0]["answer"] is None


def test_a_second_answer_is_refused_with_who_answered(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    _, _, found = one_break(api)
    assert answer(api, found["id"], "pass").status_code == 200
    r = answer(api, found["id"], "block")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["detail"].startswith("Already answered by ")
    assert breaks(api)[0]["answer"] == "pass"


def test_undo_within_a_day_removes_the_answer_by_whoever_gave_it_only(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    _, asked, found = one_break(api)
    assert answer(api, found["id"], "block").status_code == 200
    told = sql(api, "SELECT count(*) AS n FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%would block%'")
    r = undo(api, found["id"], token="ben-test")
    assert r.status_code == 403, r.text
    assert undo(api, found["id"]).status_code == 200
    after = breaks(api)[0]
    assert (after["answer"], after["answered_by"], after["note"]) == (None, None, None)
    assert (
        sql(api, "SELECT count(*) AS n FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%would block%'")
        == told
    )
    assert answer(api, found["id"], "pass").status_code == 200, "asked again"
    with api.app.state.store.transaction() as c:  # answered a day and an hour ago
        c.execute("UPDATE org_rule_breaks SET answered_at=? WHERE id=?", (H.shift(H.now(), hours=-25), found["id"]))
    r = undo(api, found["id"])
    assert r.status_code == 409, r.text


def test_the_rules_list_of_breaks_and_its_tally(ruled):
    api, _ = ruled
    made = color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    for _ in range(3):
        ask_on(api, task)
    first, second, _third = breaks(api)
    answer(api, first["id"], "block", note="no")
    answer(api, second["id"], "pass")
    view = get(api, f"rules/{made['id']}/breaks")
    assert view["tally"] == {"breaks": 3, "block": 1, "pass": 1, "open": 1}
    [newest, *_] = view["breaks"]
    assert newest["first_line"] == COLORED and newest["bot"] == "bot:ops" and newest["message_id"]
    assert {b["answer"] for b in view["breaks"]} == {"block", "pass", None}
    assert view["breaks"][-1]["answered_by_name"] and view["breaks"][-1]["note"] == "no"
    assert [b["may_answer"] for b in view["breaks"] if b["answer"] is None] == [True], "Ana is an Admin"
    assert not any(b["may_answer"] for b in view["breaks"] if b["answer"]), "answered once"


def test_the_chat_timeline_carries_each_messages_breaks_and_whether_the_reader_may_answer(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task, asked, found = one_break(api)
    conv = sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]
    rows = get(api, f"threads/{conv}/timeline?flat=1")
    [shown] = [m for m in rows["messages"] if m.get("id") == asked["comment"]["id"]]
    [b] = shown["rule_breaks"]
    assert (b["id"], b["rule_text"], b["answer"], b["may_answer"]) == (found["id"], COLOR, None, True)


def test_switched_off_no_break_is_saved(ruled, monkeypatch):
    api, _ = ruled
    monkeypatch.setenv(RB.SWITCH, "off")
    color_rule(api, if_broken="warn")
    asked = ask_on(api, task_for_ops(api))
    assert breaks(api) == []
    assert (
        "may break the rule" in sql(api, "SELECT body FROM messages WHERE id=?", (asked["comment"]["id"],))[0]["body"]
    )


def test_a_late_checked_comment_saves_its_break_on_the_comment_and_sends_the_bot_no_note(ruled):
    """Spec v2: the person's answer replaces the note the late check sent the bot, so it hears once per break."""
    from backend import org_rule_check as K

    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": COLORED}, headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    K.drain_late(api.app.state.store, api.app.state.judge)
    [b] = breaks(api)
    assert (b["message_id"], b["task_id"]) == (r.json()["comment"]["id"], task["id"])
    assert sql(api, "SELECT count(*) AS n FROM notes WHERE to_actor='bot:ops'")[0]["n"] == 0


@pytest.mark.parametrize("value", ["", "maybe"])
def test_an_answer_is_block_or_pass(ruled, value):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    _, _, found = one_break(api)
    assert answer(api, found["id"], value).status_code == 422
