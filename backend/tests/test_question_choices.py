"""A ❓ as a card the person answers in place (backend/question_marks.py): up to three suggested answers, a tap that
answers it once (POST /api/v2/tasks/{tid}/comments with `ask`), and the bot's take-back. Synthetic rows only: the
fictional company's people and bots."""

import threading

from backend import models as M
from backend import org_rule_check
from backend import question_marks as Q
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.org_rule_check import COLOR, NEEDLE, ruled, sql, task_for_ops  # noqa: F401 - fixtures
from backend.tests.helpers.org_rules import rule

ASKED = "Should the launch note go out on Friday?"
CHOICES = ["Yes, Friday", "Next week"]


def ask(api, task, choices=CHOICES, text=ASKED, path=None):
    r = api.post(
        f"/api/v2/{path or 'tasks/' + task['id'] + '/questions'}",
        json={"to": "ana", "text": text, "choices": choices},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    return r.json()["question"]


def questions(api, token="ana-test"):
    view = get(api, "needs-you", token=token)
    return [i for i in (view["items"] if isinstance(view, dict) else view) if i["kind"] == "question"]


def tap(api, mark, text, token="ana-test"):
    return api.post(
        f"/api/v2/tasks/{mark['task_id']}/comments",
        json={"text": text, "ask": "qmark:" + mark["id"]},
        headers=headers(token),
    )


def replies(api, mark):
    """Ana's messages in the question's chat since it was asked."""
    return sql(
        api,
        "SELECT body FROM messages WHERE conversation_id=? AND sender_id='human:ana' AND deleted_at IS NULL "
        "AND created_at >= ?",
        (mark["conversation_id"], mark["created_at"]),
    )


def cleared(api, mark):
    return sql(api, "SELECT cleared_reason FROM question_marks WHERE id=?", (mark["id"],))[0]["cleared_reason"]


def test_an_ask_with_choices_shows_them_and_a_tap_answers_and_clears_it(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = ask(api, task)
    assert mark["choices"] == CHOICES
    [item] = questions(api)
    assert (item["line"], item["choices"]) == (ASKED, CHOICES)
    assert "detail" not in item  # one line: nothing more to show
    [wait] = [w for w in get(api, f"tasks/{task['id']}")["task"]["waits"] if w["requirement_id"] == item["id"]]
    assert wait["choices"] == CHOICES  # the task's own card reads them too

    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 200, r.text
    assert questions(api) == []
    [row] = sql(api, "SELECT cleared_reason, cleared_message_id FROM question_marks WHERE id=?", (mark["id"],))
    assert row == {"cleared_reason": "replied", "cleared_message_id": r.json()["comment"]["id"]}
    assert [m["body"] for m in replies(api, mark)] == ["Yes, Friday"]


def test_a_question_with_numbered_lines_lists_them_whole(ruled):
    # A card answered from Needs you shows every question the ❓ asks, not its first line alone (ask_shape).
    api, _ = ruled
    ask(api, task_for_ops(api), text=f"{ASKED}\n1. Is the price line final?\n2. Should beta users get it too?")
    [item] = questions(api)
    assert item["line"] == ASKED
    assert item["detail"] == "1. Is the price line final?\n2. Should beta users get it too?"


def test_a_long_question_lists_whole_lines_up_to_its_cap():
    # A ❓ may run to many lines: the list carries whole lines up to DETAIL_MAX, then "…" (the chat has it all).
    lines = [f"{n}. " + "word " * 40 for n in range(1, 40)]
    detail = Q.detail_of({"body": ASKED + "\n" + "\n".join(lines)})
    assert len(detail) <= Q.DETAIL_MAX and detail.endswith("…")
    kept = [line.rstrip() for line in detail[:-1].splitlines()]
    assert len(kept) > 1 and kept == [line.rstrip() for line in lines[: len(kept)]]  # whole lines, in order
    one = Q.detail_of({"body": ASKED + "\n" + "x" * 5000})  # one line longer than the cap: cut short
    assert len(one) == Q.DETAIL_MAX and one.endswith("…")
    assert Q.detail_of({"body": ASKED + "\n1. Short?"}) == "1. Short?"


def test_a_questions_words_beyond_its_line_reach_only_who_may_read_its_message(ruled, monkeypatch):
    """Needs you lists a ❓'s answers and its lines after the first only when the request's own viewer (its Auth: an
    Assistant run's scope, an external credential) may read the marked message, as a read of it would; never by a
    viewer rebuilt from the actor id. Here the request's scope is denied it: the ❓ still lists, its line alone."""
    from backend import threads

    api, _ = ruled
    mark = ask(api, task_for_ops(api), text=f"{ASKED}\n1. Is the price line final?")
    real = threads.message_readable

    def scoped(c, v, message, **k):
        if message["id"] == mark["message_id"] and getattr(v, "auth", None) is not None:
            return False
        return real(c, v, message, **k)

    monkeypatch.setattr(threads, "message_readable", scoped)
    [item] = questions(api)
    assert item["line"] == ASKED
    assert item["choices"] == [] and "detail" not in item


def test_more_than_three_choices_are_refused_and_nothing_is_asked(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": ASKED, "choices": ["Friday", "Monday", "Tuesday", "Never"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 422, r.text
    assert questions(api) == []


def test_a_choice_is_plain_words_no_mention_and_no_command(ruled):
    """A tap sends the choice as the person's own reply: an @mention would reach (and let in) whoever it names, and a
    leading / would run a skill, on their word."""
    api, _ = ruled
    task = task_for_ops(api)
    for bad in ("Yes @cara", "/deploy now"):
        r = api.post(
            f"/api/v2/tasks/{task['id']}/questions",
            json={"to": "ana", "text": ASKED, "choices": ["Next week", bad]},
            headers=headers("ops-test"),
        )
        assert r.status_code >= 400 and "plain words" in r.text, r.text
    assert questions(api) == [] and sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0


def test_the_rules_read_the_choices_on_both_routes(ruled):
    """A Block rule finds the words in a suggested answer as it would in the question itself."""
    api, _ = ruled
    rule(api, {"kind": "internal", "text": COLOR, "who": {"bots": ["ops"]}, "if_broken": "block"})
    task = task_for_ops(api)
    colored = [f"Our {NEEDLE} teal"]
    for path in (f"tasks/{task['id']}/questions", "questions"):
        r = api.post(
            f"/api/v2/{path}", json={"to": "ana", "text": ASKED, "choices": colored}, headers=headers("ops-test")
        )
        assert r.status_code == 409 and r.json()["error"] == "task_rule", r.text
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0


def test_the_rules_read_the_choices_as_the_card_shows_them():
    assert Q.with_choices(ASKED, ["  Yes,   Friday ", "Yes, Friday", ""]) == f"{ASKED}\n- Yes, Friday"


def test_two_concurrent_answers_produce_exactly_one_reply(ruled):
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    start, said = threading.Barrier(2), []

    def answer(text):
        start.wait()
        said.append(tap(api, mark, text).status_code)

    both = [threading.Thread(target=answer, args=(text,)) for text in CHOICES]
    for t in both:
        t.start()
    for t in both:
        t.join(30)
    assert sorted(said) == [200, 409]
    assert len(replies(api, mark)) == 1


def test_a_tap_on_a_question_answered_already_is_refused(ruled):
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    assert tap(api, mark, "Next week").status_code == 200
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 409 and "Already answered" in r.text, r.text
    assert [m["body"] for m in replies(api, mark)] == ["Next week"]


def test_an_answer_by_name_not_in_the_persons_own_hand_is_refused_and_leaves_it_open(ruled, monkeypatch):
    """Agent Ops or an automation writing as the person never clears a ❓ (question_marks.replied): so it may not
    answer one by name either, or the one-reply check would pass writes that leave it open."""
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    monkeypatch.setattr(Q, "by_hand", lambda: False)
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 422, r.text
    assert replies(api, mark) == [] and cleared(api, mark) is None


def test_an_answer_its_write_did_not_clear_is_not_written(ruled, monkeypatch):
    """The one-reply check is the write itself: a reply by name that left its ❓ open is refused, nothing sent."""
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    monkeypatch.setattr(Q, "replied", lambda *_: [])
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 409 and "Already answered" in r.text, r.text
    assert replies(api, mark) == [] and cleared(api, mark) is None


def test_only_the_persons_own_hand_answers():
    for via, own in (("", True), ("assistant", True), ("slack", True), ("botops", False), ("automation:a1", False)):
        token = H.VIA.set(via)
        try:
            assert Q.by_hand() is own, via
        finally:
            H.VIA.reset(token)


def test_a_tap_answers_its_own_question_alone_and_wakes_the_bot_that_asked(ruled):
    """Two agents ask Ana on one task: her tap on one card answers that one, reaches the agent that asked it (not on
    the task otherwise) and leaves the other open. A plain reply still answers whatever is open."""
    api, _ = ruled
    task = task_for_ops(api)
    mine = ask(api, task)
    with api.app.state.store.transaction() as c:
        theirs = Q.ask(c, "bot:finance", task["id"], "human:ana", "Should the invoice name the venue?", checked=False)
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    r = tap(api, {**theirs, "created_at": mine["created_at"]}, "No venue")
    assert r.status_code == 200, r.text
    assert cleared(api, theirs) == "replied" and cleared(api, mine) is None
    assert "finance" in [j["bot_id"] for j in sql(api, "SELECT bot_id FROM jobs WHERE state='queued'")]
    post(api, f"tasks/{task['id']}/comments", {"text": "Friday works."})
    assert cleared(api, mine) == "replied"


def test_send_anyway_of_a_held_answer_checks_again_that_the_question_is_open(ruled):
    """A company rule held Ana's answer; she answered again meanwhile. Sending the held one would be a second reply."""
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    rule(
        api, {"kind": "internal", "text": COLOR, "who": {"people": ["ana"]}, "if_broken": "escalate", "reviewer": "ben"}
    )
    r = tap(api, mark, f"Yes, and our {NEEDLE} teal")
    assert r.status_code == 200 and "held" in r.json(), r.text
    org_rule_check.settle_parts()
    assert cleared(api, mark) is None  # held: nothing sent, still open
    assert tap(api, mark, "Next week").status_code == 200
    held = r.json()["held"]
    view = get(api, f"rule-holds/{held['id']}", token="ben-test")
    sent = api.post(f"/api/v2/rule-holds/{held['id']}/send", json={"nonce": view["nonce"]}, headers=headers("ben-test"))
    assert sent.status_code == 409, sent.text
    assert [m["body"] for m in replies(api, mark)] == ["Next week"]


def test_take_back_removes_it_from_needs_you_and_keeps_the_message(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = ask(api, task)
    assert api.delete(f"/api/v2/tasks/{task['id']}/questions", headers=headers("ana-test")).status_code == 403
    r = api.delete(f"/api/v2/tasks/{task['id']}/questions", headers=headers("ops-test"))
    assert r.status_code == 200 and r.json()["question"]["id"] == mark["id"], r.text
    assert questions(api) == []
    assert cleared(api, mark) == "taken_back"
    assert sql(api, "SELECT deleted_at FROM messages WHERE id=?", (mark["message_id"],)) == [{"deleted_at": None}]
    r = api.delete(f"/api/v2/tasks/{task['id']}/questions", headers=headers("ops-test"))
    assert r.status_code == 200 and r.json()["question"] is None, r.text  # nothing open: nothing taken back
    assert tap(api, mark, "Yes, Friday").status_code == 409  # a card left open answers nothing


def test_take_back_in_a_direct_chat_is_the_asking_bots_only(ruled):
    api, _ = ruled
    mark = ask(api, None, path="questions")
    assert [i["choices"] for i in questions(api)] == [CHOICES]
    assert api.delete(f"/api/v2/questions/{mark['id']}", headers=headers("ana-test")).status_code == 403
    assert len(questions(api)) == 1
    r = api.delete(f"/api/v2/questions/qmark:{mark['id']}", headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    assert questions(api) == []


def test_taking_back_a_question_just_answered_is_no_refusal(ruled):
    """The person's reply beat the bot's take-back: nothing to do, and nothing on the bot's record."""
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    assert tap(api, mark, "Next week").status_code == 200
    before = sql(api, "SELECT count(*) AS n FROM refusals WHERE actor='bot:ops'")[0]["n"]
    r = api.delete(f"/api/v2/questions/{mark['id']}", headers=headers("ops-test"))
    assert r.status_code == 200 and r.json()["question"] is None, r.text
    assert sql(api, "SELECT count(*) AS n FROM refusals WHERE actor='bot:ops'")[0]["n"] == before
    assert cleared(api, mark) == "replied"


def test_an_old_free_text_question_is_a_question_card_with_no_choices(ruled):
    api, _ = ruled
    mark = ask(api, task_for_ops(api), choices=[])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE question_marks SET choices=NULL WHERE id=?", (mark["id"],))  # as a ❓ from before
    [item] = questions(api)
    assert (item["line"], item["choices"]) == (ASKED, [])
    assert tap(api, mark, "Friday works.").status_code == 200  # its reply box answers it the same way
    assert questions(api) == []


def test_a_question_with_no_choices_hashes_as_it_did_before_them():
    """An idempotency key recorded before the upgrade still matches its retry."""
    assert M.Question(to="ana", text=ASKED).model_dump() == {"to": "ana", "text": ASKED, "not_known": False}
    assert M.Question(to="ana", text=ASKED, choices=["Yes"]).model_dump()["choices"] == ["Yes"]


def test_a_named_answer_clears_its_question_even_posted_in_another_room(ruled):
    """A legacy task moved to its own room: the reply posts there, not in the room the ❓ was asked in. It still
    answers that ❓ in the same write, so a second tap is refused, never a second reply."""
    api, _ = ruled
    mark = ask(api, task_for_ops(api))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE question_marks SET room_id='an-older-room' WHERE id=?", (mark["id"],))
    assert tap(api, mark, "Next week").status_code == 200
    assert cleared(api, mark) == "replied"
    r = tap(api, mark, "Yes, Friday")
    assert r.status_code == 409, r.text
    assert [m["body"] for m in replies(api, mark)] == ["Next week"]


def test_a_questions_choices_reach_only_who_may_read_its_message(ruled, monkeypatch):
    """A task's reader whose own read of the marked message is denied (an external agent's credential scoped away
    from it, say) sees the ❓ wait, never the answers it offers. The request's own identity decides, as a read of that
    message would: here the denial is the credential's (a viewer with the request's Auth), not the actor's."""
    from backend import threads

    api, _ = ruled
    task = task_for_ops(api)
    mark = ask(api, task)
    real = threads.message_readable

    def scoped(c, v, message, **k):
        if message["id"] == mark["message_id"] and getattr(v, "auth", None) is not None and v.actor == "bot:ops":
            return False
        return real(c, v, message, **k)

    monkeypatch.setattr(threads, "message_readable", scoped)

    def waits(token):
        view = get(api, f"tasks/{task['id']}", token=token)["task"]["waits"]
        return [w.get("choices") for w in view if Q.mark_id(w["requirement_id"])]

    assert waits("ana-test") == [CHOICES]
    assert waits("ops-test") == [None]


def test_a_client_cannot_narrow_what_a_plain_reply_answers(ruled):
    """`answers` in a thread post's refs is the client's word: a plain reply still answers each ❓ to them there."""
    api, _ = ruled
    task = task_for_ops(api)
    mine = ask(api, task)
    with api.app.state.store.transaction() as c:
        theirs = Q.ask(c, "bot:finance", task["id"], "human:ana", "Should the invoice name the venue?", checked=False)
    r = api.post(
        f"/api/v2/threads/{mine['conversation_id']}/messages",
        json={"text": "Friday, and no venue.", "refs": {"answers": theirs["id"]}},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    assert (cleared(api, mine), cleared(api, theirs)) == ("replied", "replied")
