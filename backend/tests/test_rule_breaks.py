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


def post_in(api, conv, text, token="ops-test"):
    r = api.post(f"/api/v2/threads/{conv}/messages", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_post_on_a_tasks_own_chat_that_names_no_task_is_saved_on_that_task(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    conv = sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]
    posted = post_in(api, conv, COLORED)
    [b] = breaks(api)
    assert (b["message_id"], b["task_id"], b["conversation_id"]) == (posted["id"], task["id"], conv)


def test_a_thread_whose_first_message_breaks_the_rule_saves_the_break_on_that_message(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    r = api.post(
        "/api/v2/threads",
        json={"title": "Launch", "members": ["human:ana"], "body": COLORED},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    [b] = breaks(api)
    [first] = sql(api, "SELECT id, conversation_id FROM messages WHERE id=?", (b["message_id"],))
    assert first["conversation_id"] == r.json()["id"] == b["conversation_id"] and b["task_id"] == ""


def test_an_edit_that_breaks_the_rule_saves_the_break_on_the_edited_message(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    conv = sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]
    posted = post_in(api, conv, PLAIN)
    assert breaks(api) == []
    r = api.patch(f"/api/v2/messages/{posted['id']}", json={"text": COLORED}, headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    [b] = breaks(api)
    assert (b["message_id"], b["task_id"]) == (posted["id"], task["id"])


def edit_message(api, message_id, text, token="ops-test"):
    r = api.patch(f"/api/v2/messages/{message_id}", json={"text": text}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def task_chat(api, task):
    return sql(api, "SELECT conversation_id FROM tasks WHERE id=?", (task["id"],))[0]["conversation_id"]


def test_a_bots_end_of_turn_reply_that_breaks_the_rule_saves_the_break_on_that_reply(ruled):
    from backend.tests.helpers.api import post as api_post, setup_attempt

    api, _ = ruled
    color_rule(api, if_broken="warn")
    r, msg, attempt = setup_attempt(api)
    msg = msg.get("message", msg)
    api_post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, r["token"])
    api_post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": "Acme's favorite color is teal.", "last_seq": 0},
        token=r["token"],
    )
    [b] = breaks(api)
    [reply] = sql(api, "SELECT id, conversation_id FROM messages WHERE id=?", (b["message_id"],))
    assert reply["conversation_id"] == msg["conversation_id"] == b["conversation_id"]


def test_a_bots_ask_for_an_ok_saves_its_break_on_its_task_and_yes_block_wakes_the_bot_there(ruled):
    from backend.tests.helpers import engine as X

    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": COLORED, "kind": "ok", "show": X.shown_on(api), "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    [b] = breaks(api)
    assert b["task_id"] == task["id"]
    assert answer(api, b["id"], "block").status_code == 200
    [told] = sql(api, "SELECT conversation_id FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%would block%'")
    assert told["conversation_id"] == task_chat(api, task)
    assert breaks(api)[0]["bot_told"] == 1


def test_an_edit_that_keeps_the_rule_takes_the_unanswered_question_off_the_message_and_the_rule(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    posted = post_in(api, task_chat(api, task_for_ops(api)), COLORED)
    assert len(breaks(api)) == 1
    edit_message(api, posted["id"], PLAIN)
    assert breaks(api) == []


def test_an_edit_that_still_breaks_the_rule_is_asked_once_about_its_new_words(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    posted = post_in(api, task_chat(api, task_for_ops(api)), COLORED)
    again = "Acme's favorite color is teal, so the note says so."
    edit_message(api, posted["id"], again)
    [b] = breaks(api)
    assert (b["message_id"], b["first_line"]) == (posted["id"], again)


def test_an_answered_break_stays_on_the_rule_but_leaves_an_edited_message(ruled):
    api, _ = ruled
    made = color_rule(api, if_broken="warn")
    posted = post_in(api, task_chat(api, task_for_ops(api)), COLORED)
    [first] = breaks(api)
    assert answer(api, first["id"], "pass").status_code == 200
    edit_message(api, posted["id"], PLAIN)
    [kept] = breaks(api)
    assert (kept["id"], kept["answer"], kept["message_id"]) == (first["id"], "pass", "")
    assert get(api, f"rules/{made['id']}/breaks")["tally"] == {"breaks": 1, "block": 0, "pass": 1, "open": 0}


def test_a_late_result_for_a_comment_edited_since_saves_no_break(ruled, monkeypatch):
    from backend import org_rule_check as K

    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    monkeypatch.setattr(K, "_ask", lambda *a, **k: (None, "busy"))  # the late check waits for a slot
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": COLORED}, headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:  # edited before the late check came back
        c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), r.json()["comment"]["id"]))
    monkeypatch.undo()
    api.app.state.judge = judge
    K.drain_late(api.app.state.store, judge)
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 0, "the late check ran"
    assert breaks(api) == []


def private_break(api, rule_id, line=COLORED, note="Leave it out"):
    """A break on a private task of Ben's that only Ben reads, as if a bot that lost it since had written there."""
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)["id"]
        bid = H.new_id()
        c.execute(
            "INSERT INTO org_rule_breaks(id, rule_id, author, operation, recipients, first_line, task_id, created_at, "
            "note) VALUES(?,?,?,?,?,?,?,?,?)",
            (bid, rule_id, "bot:ops", "task_comment", '["human:ben"]', line, private, H.now(), note),
        )
    return bid, private


def test_a_rule_reader_who_may_not_read_the_private_task_sees_the_answer_but_not_its_words(ruled):
    api, _ = ruled
    made = color_rule(api, if_broken="warn")
    bid, _ = private_break(api, made["id"])
    view = get(api, f"rules/{made['id']}/breaks")
    [b] = view["breaks"]
    assert b["id"] == bid and b["first_line"] == "" and b["note"] is None and b["task_id"] is None
    assert view["tally"]["breaks"] == 1, "still counted"


def test_yes_block_for_a_bot_that_lost_the_private_task_tells_it_nothing_and_says_so_on_the_break(ruled):
    api, _ = ruled
    made = color_rule(api, if_broken="warn")
    bid, _ = private_break(api, made["id"])
    told = len(sql(api, "SELECT id FROM messages WHERE recipient_id='bot:ops'"))
    r = answer(api, bid, "block")
    assert r.status_code == 200, r.text
    assert r.json()["break"]["bot_told"] is False
    assert len(sql(api, "SELECT id FROM messages WHERE recipient_id='bot:ops'")) == told, "no chat, no notice"
    assert sql(api, "SELECT count(*) AS n FROM notes WHERE to_actor='bot:ops'")[0]["n"] == 0
    assert breaks(api)[0]["bot_told"] == 0


def test_switched_off_old_questions_hide_answers_are_refused_and_given_answers_stay_read_only(ruled, monkeypatch):
    api, _ = ruled
    made = color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    blocked, open_one = ask_on(api, task), ask_on(api, task)
    first, second = breaks(api)
    assert answer(api, first["id"], "block").status_code == 200
    monkeypatch.setenv(RB.SWITCH, "off")
    rows = get(api, f"threads/{task_chat(api, task)}/timeline?flat=1")["messages"]
    shown = {m["id"]: m.get("rule_breaks") for m in rows}
    assert shown[open_one["comment"]["id"]] is None, "no question shows"
    [kept] = shown[blocked["comment"]["id"]]
    assert kept["answer"] == "block" and not kept["may_undo"], "a blocked message stays folded"
    r = answer(api, second["id"], "pass")
    assert r.status_code == 409 and r.json()["error"]["detail"] == RB.SWITCHED_OFF
    assert undo(api, first["id"]).status_code == 409
    view = get(api, f"rules/{made['id']}/breaks")
    assert view["tally"] == {"breaks": 2, "block": 1, "pass": 0, "open": 1}
    assert not any(b["may_answer"] or b["may_undo"] for b in view["breaks"])
    monkeypatch.setenv(RB.SWITCH, "on")
    rows = get(api, f"threads/{task_chat(api, task)}/timeline?flat=1")["messages"]
    assert [b["may_answer"] for m in rows if m["id"] == open_one["comment"]["id"] for b in m["rule_breaks"]] == [True]


def test_a_post_naming_its_task_as_a_list_saves_the_break_on_that_task(ruled):
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    conv = task_chat(api, task)
    r = api.post(
        f"/api/v2/threads/{conv}/messages",
        json={"text": COLORED, "refs": {"task": [task["id"]]}},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    [b] = breaks(api)
    assert (b["message_id"], b["task_id"]) == (r.json()["id"], task["id"])
