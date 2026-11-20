"""Questions as tasks (backend/questions.py): with the company switch on, a bot's question to a person is a task filed
under the task it works on, answered in one write that relays the answer, restarts the parent and wakes the bot once.
Synthetic rows only: the fictional company's people and bots."""

from datetime import datetime, timezone

import pytest

from backend import late_rule
from backend import question_marks as QM
from backend import questions as Q
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

ANA, BEN, CARA = X.ANA, "human:ben", "human:cara"
BUILDER, REVIEWER, MANAGER = "bot:builder", "bot:reviewer", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
ASKED = "Should the launch note go out on Friday?\nBoth drafts are on the task."


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        Q.set_enabled(conn, True, H.TICO_ACTOR)
        yield conn


@pytest.fixture
def general(c):
    for slug in ("builder", "reviewer", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, manager=MANAGER)


def _parent(c, title="Write the launch note", by=ANA):
    row = X.new_task(c, by, title, assignee=BUILDER)
    X.ran(c, row["id"], BUILDER)
    return E.task(c, row["id"])


def _ask(c, parent, bot=BUILDER, person=ANA, text=ASKED, **more):
    return Q.ask(c, bot, parent["id"], person, text, **more)


def _queued(c, bot):
    return c.execute("SELECT COUNT(*) FROM jobs WHERE bot_id=? AND state='queued'", (H.actor_id(bot),)).fetchone()[0]


def test_an_ask_files_a_question_the_parent_waits_on(c, general):
    parent = _parent(c)
    asked = _ask(c, parent, link_url="https://pay.example/login")
    task = E.task(c, asked["task_id"])
    assert (task["owner_id"], task["requester_id"], task["title"]) == (ANA, BUILDER, ASKED.splitlines()[0])
    assert task["body"] == ASKED.splitlines()[1]
    # Not filed work: the filed-task holds and wakes never see it.
    assert c.execute("SELECT 1 FROM links WHERE to_task_id=?", (asked["task_id"],)).fetchone() is None
    parent = E.task(c, parent["id"])
    assert [(w.state, w.who, w.ask) for w in W.of(c, parent)] == [(W.PERSON, ANA, True)]
    assert W.turn(c, parent) is None and W.persons_turn(W.of(c, parent))
    with pytest.raises(H.Refused):
        QM.refuse_ending(c, BUILDER, parent["id"])
    [item] = [i for i in W.needs(c, ANA) if i["kind"] == Q.KIND]
    assert item["task_id"] == asked["task_id"] and "parent" not in item  # nothing of a task they may lose
    assert (item["asker"], item["round"], item["link_url"]) == (BUILDER, 1, "https://pay.example/login")
    # The parent's own asks list it once, as the question item, never as an ask on the parent.
    assert not [i for i in W.needs(c, ANA) if i.get("task_id") == parent["id"]]


def test_asking_again_reopens_the_same_question_and_two_bots_ask_two(c, general):
    parent = _parent(c, by=REVIEWER)  # both bots are its parties: only a party files under a task
    first = _ask(c, parent)
    again = _ask(c, parent, text="Friday or Monday, then?")
    assert again["task_id"] == first["task_id"] and again["round"] == 2
    Q.answer(c, first["task_id"], ANA, "Monday.", 2)
    third = _ask(c, E.task(c, parent["id"]), text="And the time?")
    assert (third["task_id"], third["round"], third["state"]) == (first["task_id"], 3, "open")
    assert E.task(c, first["task_id"])["status"] == "open"
    said = [m["body"] for m in H.task_comments(c, first["task_id"]) if m["sender_id"] == BUILDER]
    assert said == [ASKED, "Friday or Monday, then?", "And the time?"]  # old wording stays in its chat
    other = _ask(c, E.task(c, parent["id"]), bot=REVIEWER, text="Is the tone right?")
    assert other["task_id"] != first["task_id"]
    assert sorted(q["asker_id"] for q in Q.on_parent(c, E.task(c, parent["id"]))) == [BUILDER, REVIEWER]


def test_an_answer_ends_it_relays_it_restarts_the_parent_and_wakes_the_bot_once(c, general):
    parent = _parent(c)
    asked = _ask(c, parent)
    before = E.task(c, parent["id"])["visit"]
    assert _queued(c, BUILDER) == 0
    Q.answer(c, asked["task_id"], ANA, "Yes, Friday.", 1)
    row = Q.get(c, asked["task_id"])
    assert (row["ended_reason"], row["answered_by_id"]) == (Q.ANSWERED, ANA)
    assert E.task(c, asked["task_id"])["status"] == "closed"  # answered is its row's state, never stage work
    relayed = [m for m in H.task_comments(c, parent["id"]) if m["body"].startswith("Re: ")]
    assert [(m["sender_id"], m["body"]) for m in relayed] == [
        (ANA, "Re: Should the launch note go out on Friday?: Yes, Friday.")
    ]
    parent = E.task(c, parent["id"])
    assert W.turn(c, parent) == BUILDER
    assert _queued(c, BUILDER) == 1
    assert parent["visit"]["progress_mark"] != before["progress_mark"]
    visit = parent["visit"]
    assert late_rule.last_answers(c, [(visit["id"], parent["id"], visit["entered_at"])]) == {
        visit["id"]: row["answered_at"]
    }
    assert Q.read(c, asked["task_id"], "1")["answer_text"] == "Yes, Friday."


def test_an_answer_while_the_bot_still_waits_on_another_question_wakes_nobody(c, general):
    parent = _parent(c)
    first = _ask(c, parent)
    _ask(c, E.task(c, parent["id"]), person=BEN, text="Can you check the numbers?")
    Q.answer(c, first["task_id"], ANA, "Yes.", 1)
    assert _queued(c, BUILDER) == 0
    assert W.turn(c, E.task(c, parent["id"])) is None


def test_an_old_round_or_someone_else_is_told_to_look_again(c, general):
    asked = _ask(c, _parent(c))
    _ask(c, E.task(c, Q.get(c, asked["task_id"])["parent_id"]), text="Monday instead?")
    for by, at in ((ANA, 1), (BEN, 2)):
        with pytest.raises(Q.QuestionError) as refused:
            Q.answer(c, asked["task_id"], by, "Yes.", at)
        assert refused.value.status == 409
    with pytest.raises(Q.QuestionError) as automation:
        Q.answer(c, asked["task_id"], ANA, "Yes.", 2, via="automation")
    assert automation.value.status == 403
    assert Q.get(c, asked["task_id"])["ended_reason"] is None


def test_a_private_parent_its_person_cannot_read_keeps_the_answer_on_the_question(c, general):
    made = H.task_create(c, BUILDER, "Renew the lease", "", BUILDER, private=True, lint=False)
    X.ran(c, made["id"], BUILDER)
    asked = _ask(c, E.task(c, made["id"]), person=CARA, checked=False)  # lets Cara in, as a ❓ does
    c.execute("UPDATE tasks SET readers='[]' WHERE id=?", (made["id"],))
    Q.answer(c, asked["task_id"], CARA, "Renew it for a year.", 1)
    parent_chat = H.task_comments(c, made["id"])
    assert not [m for m in parent_chat if "Renew it for a year" in (m.get("body") or "")]
    told = c.execute(
        "SELECT body FROM messages WHERE wake_key LIKE ?", (f"{made['id']}:%:question:{asked['task_id']}:1",)
    ).fetchall()
    assert len(told) == 1 and asked["task_id"] in told[0][0]


def _two_stages(c):
    stage = {"owner": "assignee", "expected_minutes": 60, "requirements": [{"kind": "owner_done"}]}
    X.pipeline(c, "dev", "Dev", stages=({"name": "Build", **stage}, {"name": "Check", **stage}), manager=MANAGER)
    row = X.new_task(c, ANA, "Build the page", "dev", assignee=BUILDER)
    X.ran(c, row["id"], BUILDER)
    return E.task(c, row["id"])


@pytest.mark.parametrize(
    "ending",
    ["withdrawn", "closed_by_person", "parent_ended", "parent_moved", "asker_gone", "person_gone"],
)
def test_each_ending(c, general, ending):
    parent = _two_stages(c) if ending == "parent_moved" else _parent(c)
    asked = _ask(c, parent, person=BEN if ending == "person_gone" else ANA)
    qid = asked["task_id"]
    if ending == "withdrawn":
        Q.withdraw(c, qid, BUILDER)
    elif ending == "closed_by_person":
        E.move(c, qid, E.CLOSED, ANA, "Not mine")
    elif ending == "parent_ended":
        E.move(c, parent["id"], E.CLOSED, ANA, "Not needed")
    elif ending == "parent_moved":
        E.move(c, parent["id"], E.NEXT, ANA, "On to checking")
    elif ending == "asker_gone":
        c.execute("UPDATE bots SET state='archived' WHERE id='builder'")
        QM.clear_gone(c)
    else:
        X.left(c, BEN)
        QM.clear_gone(c)
    reason = {"closed_by_person": Q.WITHDRAWN}.get(ending, ending)
    assert Q.get(c, qid)["ended_reason"] == reason
    assert E.task(c, qid)["status"] == "closed"
    assert Q.on_parent(c, E.task(c, parent["id"])) == []
    if ending in ("closed_by_person", "person_gone"):
        assert _queued(c, BUILDER) == 1  # the bot is told on the parent


def test_only_its_person_snoozes_it_and_their_snooze_hides_it(c, general):
    asked = _ask(c, _parent(c))
    with pytest.raises(H.Refused):
        E.snooze(c, asked["task_id"], BUILDER, "Later")
    E.snooze(c, asked["task_id"], ANA, "After lunch", "2030-01-07T13:00:00Z")
    assert not [i for i in W.needs(c, ANA) if i["kind"] == Q.KIND]
    [held] = [i for i in W.needs(c, ANA, "only") if i["kind"] == Q.KIND]
    assert held["snoozed_until"] == "2030-01-07T13:00:00Z"


def test_give_to_moves_it_to_another_person_with_a_new_round_and_never_to_a_bot(c, general):
    asked = _ask(c, _parent(c))
    Q.give(c, asked["task_id"], BEN, ANA)
    assert Q.get(c, asked["task_id"])["person_id"] == BEN and E.task(c, asked["task_id"])["owner_id"] == BEN
    with pytest.raises(H.Refused):
        E.reassign(c, asked["task_id"], REVIEWER, BEN, "A bot can do it")
    Q.give(c, asked["task_id"], ANA, BEN)  # back to Ana: what she saw at round 1 is not current again
    with pytest.raises(Q.QuestionError) as stale:
        Q.answer(c, asked["task_id"], ANA, "Friday.", 1)
    assert stale.value.status == 409
    Q.answer(c, asked["task_id"], ANA, "Friday.", 3)
    assert Q.get(c, asked["task_id"])["answered_by_id"] == ANA


def test_the_backfill_turns_each_open_task_question_mark_once(c, general):
    parent = _parent(c)
    mark = QM.ask(c, BUILDER, parent["id"], ANA, ASKED)
    first = Q.backfill(c)
    assert (first["before"]["open_task_marks"], first["after"]["open_task_marks"]) == (1, 0)
    [turned] = first["turned"]
    row = Q.get(c, turned["task_id"])
    assert (row["source_mark_id"], row["parent_id"], row["person_id"], row["created_at"]) == (
        mark["id"],
        parent["id"],
        ANA,
        mark["created_at"],
    )
    assert QM.get(c, mark["id"])["cleared_reason"] == "question"
    again = Q.backfill(c)
    assert again["turned"] == [] and again["after"] == first["after"]


# ---- through the API -------------------------------------------------------------------------------------------------


def _switch(api, on):
    with api.app.state.store.transaction() as conn:
        Q.set_enabled(conn, on, H.TICO_ACTOR)


def test_the_route_asks_a_mark_with_the_switch_off_and_a_question_task_with_it_on(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions", json={"to": "ana", "text": "Which tone?"}, headers=headers("ops-test")
    )
    assert r.status_code == 200, r.text
    assert "question_task" not in r.json() and sql(api, "SELECT * FROM questions") == []
    _switch(api, True)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ben", "text": "Which tone?", "link": "https://acme.example/drafts"},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    asked = r.json()["question_task"]
    assert (asked["person"], asked["link_url"], asked["round"]) == ("human:ben", "https://acme.example/drafts", 1)
    path = f"/api/v2/tasks/{asked['task_id']}/question/answer"
    stale = api.post(path, json={"text": "Warm.", "round": 2}, headers=headers("ben-test"))
    assert stale.status_code == 409, stale.text
    done = api.post(path, json={"text": "Warm.", "round": 1}, headers=headers("ben-test"))
    assert done.status_code == 200 and done.json()["question"]["state"] == "answered", done.text
    read = api.get(f"/api/v2/tasks/{asked['task_id']}", headers=headers("ben-test")).json()["task"]["question"]
    assert (read["state"], read["answered_by"], read["answer_text"]) == ("answered", "human:ben", "Warm.")


def test_a_deleted_parent_ends_its_questions_and_a_deleted_question_releases_its_parent(c, general):
    from backend import task_delete

    parent = _parent(c)
    asked = _ask(c, parent)
    task_delete.delete_tasks(c, [asked["task_id"]], apply=True, actor=ANA, keep_work=True)
    assert Q.get(c, asked["task_id"])["ended_reason"] == Q.WITHDRAWN
    assert W.turn(c, E.task(c, parent["id"])) == BUILDER
    again = _ask(c, E.task(c, parent["id"]), text="Which day, then?")
    task_delete.delete_tasks(c, [parent["id"]], apply=True, actor=ANA, keep_work=True)
    assert Q.get(c, again["task_id"])["ended_reason"] == Q.PARENT_ENDED


def test_the_question_routes_reply_give_reassign_and_withdraw(ruled):
    api, _ = ruled
    _switch(api, True)
    task = task_for_ops(api)

    def ask(text):
        r = api.post(
            f"/api/v2/tasks/{task['id']}/questions", json={"to": "ben", "text": text}, headers=headers("ops-test")
        )
        assert r.status_code == 200, r.text
        return r.json()["question_task"]

    asked = ask("Which tone?")
    qid = asked["task_id"]
    # A post in the question's own chat is a comment: only the answer route, at the round shown, answers it.
    r = api.post(f"/api/v2/tasks/{qid}/comments", json={"text": "Warm, I think?"}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert sql(api, "SELECT ended_reason FROM questions WHERE task_id=?", (qid,)) == [{"ended_reason": None}]
    r = api.post(
        f"/api/v2/tasks/{qid}/question/answer", json={"text": "Warm.", "round": 1}, headers=headers("ben-test")
    )
    assert r.status_code == 200, r.text
    assert ask("And the length?")["round"] == 2
    r = api.post(f"/api/v2/tasks/{qid}/question/give", json={"to": "cara"}, headers=headers("ben-test"))
    assert r.status_code == 200 and r.json()["question"]["person"] == "human:cara", r.text
    assert r.json()["question"]["round"] == 3
    version = sql(api, "SELECT version FROM tasks WHERE id=?", (qid,))[0]["version"]
    r = api.post(
        f"/api/v2/tasks/{qid}/snooze",
        json={"why": "After lunch", "until": "2031-01-01T13:00:00Z", "version": version},
        headers=headers("cara-test"),
    )
    assert r.status_code == 200, r.text
    listed = api.get("/api/v2/needs-you", headers=headers("cara-test")).json()
    assert listed["snoozed_until"].startswith("2031-01-01T13:00:00") and not listed["items"]
    version = sql(api, "SELECT version FROM tasks WHERE id=?", (qid,))[0]["version"]
    r = api.post(
        f"/api/v2/tasks/{qid}/owner",
        json={"owner": "finance", "why": "A bot can do it", "version": version},
        headers=headers("ana-test"),
    )
    assert r.status_code >= 400 and Q.NOT_A_PERSON in r.text, r.text
    r = api.post(f"/api/v2/tasks/{qid}/question/withdraw", json={"reason": "Found it"}, headers=headers("ben-test"))
    assert r.status_code == 403, r.text
    r = api.post(f"/api/v2/tasks/{qid}/question/withdraw", json={"reason": "Found it"}, headers=headers("ops-test"))
    assert r.status_code == 200 and r.json()["question"]["state"] == "withdrawn", r.text
    parent = api.get(f"/api/v2/tasks/{task['id']}", headers=headers("ops-test")).json()["task"]
    assert "questions" not in parent


def test_a_question_task_never_leaves_its_stage_and_generic_done_is_no_answer(c, general):
    asked = _ask(c, _parent(c))
    qid = asked["task_id"]
    X.pipeline(c, "dev", "Dev", stages=({"name": "Build", "owner": "assignee"},), manager=MANAGER)
    for move in (
        lambda: E.move(c, qid, E.DONE, ANA, "Did it"),
        lambda: E.refile(c, qid, "dev", None, ANA, "Belongs in Dev"),
    ):
        with pytest.raises(H.Refused) as refused:
            move()
        assert refused.value.detail == Q.STAYS
    assert Q.get(c, qid)["ended_reason"] is None and E.task(c, qid)["status"] == "open"


def test_the_relay_is_quiet_and_only_the_turn_holder_is_woken(c, general):
    X.bot(c, "reviewer")
    parent = _parent(c, by=REVIEWER)
    X.ran(c, parent["id"], REVIEWER)
    asked = _ask(c, parent)
    before = _queued(c, REVIEWER)
    Q.answer(c, asked["task_id"], ANA, "Yes, and ask @reviewer to look too.", 1)
    relayed = [m for m in H.task_comments(c, parent["id"]) if m["body"].startswith("Re: ")]
    assert len(relayed) == 1
    mentioned = c.execute("SELECT COUNT(*) FROM message_mentions WHERE message_id=?", (relayed[0]["id"],))
    assert mentioned.fetchone()[0] == 0
    assert (_queued(c, REVIEWER) - before, _queued(c, BUILDER)) == (0, 1)


def test_a_client_ref_never_keeps_a_question_mark_open(c, general):
    parent = _parent(c)
    mark = QM.ask(c, BUILDER, parent["id"], ANA, "Friday?")
    H.task_comment(c, ANA, parent["id"], "Yes.", wake=False, extra_refs={"question_relay": True})
    assert QM.get(c, mark["id"])["cleared_reason"] == "replied"


def test_an_asker_gone_from_a_persons_task_writes_nothing_and_lets_nobody_in(c, general):
    row = H.task_create(c, BUILDER, "Book the venue", "", ANA, private=True, lint=False)
    asked = _ask(c, E.task(c, row["id"]), person=BEN, checked=False)
    readers = c.execute("SELECT readers FROM tasks WHERE id=?", (row["id"],)).fetchone()[0]
    before = len(H.task_comments(c, row["id"]))
    c.execute("UPDATE bots SET state='archived' WHERE id='builder'")
    QM.clear_gone(c)
    assert Q.get(c, asked["task_id"])["ended_reason"] == Q.ASKER_GONE
    assert len(H.task_comments(c, row["id"])) == before  # the ended question shows on it; nothing is said
    assert c.execute("SELECT readers FROM tasks WHERE id=?", (row["id"],)).fetchone()[0] == readers


def test_the_backfill_leaves_a_dreams_question_and_one_that_carries_more_than_words(c, general):
    dream = _parent(c, "Dream")
    H.event(c, ANA, "dream.start", dream["id"], {})
    QM.ask(c, BUILDER, dream["id"], ANA, "Keep the old rule?")
    shown = _parent(c, "Pick a draft")
    said = H.task_comment(c, BUILDER, shown["id"], "Which draft?", wake=False, extra_refs={"shows": ["page:draft"]})
    QM.mark(c, BUILDER, said["id"], ANA)
    replying = _parent(c, "Answer the client")
    first = H.task_comment(c, ANA, replying["id"], "What did they say?", wake=False)
    reply = H.task_comment(c, BUILDER, replying["id"], "Shall I send this?", wake=False)
    c.execute("UPDATE messages SET in_reply_to=? WHERE id=?", (first["id"], reply["id"]))
    QM.mark(c, BUILDER, reply["id"], ANA)
    done = Q.backfill(c)
    assert done["turned"] == []
    assert sorted(s["why"] for s in done["skipped"]) == [
        "it is a dream's question, which Learnings reads and answers",
        "its message is not a plain ❓ comment on its task: it carries shows",
        "its message replies to another, whose readers it keeps",
    ]
    assert done["after"]["open_task_marks"] == 3


def test_with_the_switch_off_a_task_read_has_no_question_keys(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    read = api.get(f"/api/v2/tasks/{task['id']}", headers=headers("ops-test")).json()["task"]
    assert "questions" not in read and "question" not in read


def test_answering_closes_the_question_and_never_enters_a_later_stage(c):
    for slug in ("builder", "manager"):
        X.bot(c, slug)
    X.pipeline(c, manager=MANAGER)
    # General with a second work stage after To do, holding a sign-off of its own.
    c.execute("UPDATE stages SET position=position+1 WHERE pipeline_id='general' AND kind<>'work'")
    todo = dict(c.execute("SELECT * FROM stages WHERE id='general-todo'").fetchone())
    review = {**todo, "id": "general-review", "name": "Review", "position": todo["position"] + 1}
    c.execute(f"INSERT INTO stages ({','.join(review)}) VALUES ({','.join('?' * len(review))})", tuple(review.values()))
    X.requirement(c, "general-review", "owner_done")
    asked = _ask(c, _parent(c))
    Q.answer(c, asked["task_id"], ANA, "Yes.", 1)
    task = E.task(c, asked["task_id"])
    assert (task["status"], task["stage"]["kind"]) == ("closed", "closed")
    entered = c.execute(
        "SELECT s.name FROM visits v JOIN stages s ON s.id = v.stage_id WHERE v.task_id=? ORDER BY v.rowid",
        (asked["task_id"],),
    ).fetchall()
    assert [r[0] for r in entered] == ["To do", "Closed"]
    assert Q.get(c, asked["task_id"])["ended_reason"] == Q.ANSWERED


def test_the_wake_never_carries_the_answer_and_a_snoozed_parent_gets_none(c, general):
    parent = _parent(c)
    asked = _ask(c, parent)
    Q.answer(c, asked["task_id"], ANA, "The code is 4417.", 1)
    [wake] = c.execute(
        "SELECT body FROM messages WHERE wake_key LIKE ?", (f"{parent['id']}:%:question:{asked['task_id']}:1",)
    ).fetchall()
    assert "4417" not in wake[0] and "answered your question" in wake[0]
    again = _ask(c, E.task(c, parent["id"]), text="And the backup code?")
    E.snooze(c, parent["id"], ANA, "Back Monday", "2030-01-14T09:00:00Z")
    Q.answer(c, again["task_id"], ANA, "None.", 2)
    assert (
        c.execute(
            "SELECT COUNT(*) FROM messages WHERE wake_key LIKE ?", (f"{parent['id']}:%:question:{again['task_id']}:2",)
        ).fetchone()[0]
        == 0
    )


def test_the_backfill_turns_only_a_plain_mark_on_its_tasks_own_thread(c, general):
    chat = H.say(c, BUILDER, ANA, "Morning")
    shared = H.task_create(c, ANA, "Plan the offsite", "", ANA, conversation_id=chat["conversation_id"], lint=False)
    QM.ask(c, BUILDER, shared["id"], ANA, "Which week?", checked=False)
    other = _parent(c, "Another")
    named = _parent(c, "Pick the venue")
    mark = QM.ask(c, BUILDER, named["id"], ANA, "Which venue?")
    c.execute("UPDATE messages SET refs=json_set(refs, '$.task', ?) WHERE id=?", (other["id"], mark["message_id"]))
    done = Q.backfill(c)
    assert done["turned"] == []
    assert sorted(s["why"] for s in done["skipped"]) == [
        "it is not on its task's own thread (a shared chat, a sub-thread, or another room)",
        "its message is not a plain ❓ comment on its task: unexpected task",
    ]


@pytest.mark.parametrize("hold", ["person_level", "an_ok"])
def test_an_answer_wakes_nobody_while_the_parent_was_held_for_a_person(c, general, hold):
    parent = _parent(c)
    asked = _ask(c, parent)
    if hold == "person_level":
        c.execute("UPDATE visits SET level='person' WHERE task_id=? AND left_at IS NULL", (parent["id"],))
    else:
        X.ok_ask(c, parent["id"], BUILDER, "ben", "OK the venue?")
    Q.answer(c, asked["task_id"], ANA, "Yes.", 1)
    assert _queued(c, BUILDER) == 0


def test_with_the_switch_off_a_link_is_left_out_and_the_mark_is_as_before(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": "Which tone?", "link": "https://acme.example/@ben/drafts"},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["comment"]["body"] == "Which tone?" and "question_task" not in r.json()
    assert sql(api, "SELECT COUNT(*) AS n FROM question_marks WHERE cleared_at IS NULL") == [{"n": 1}]


def test_a_reader_of_the_parent_who_cannot_read_the_question_sees_only_that_one_waits(c, general):
    from backend import task_calls as TC

    made = H.task_create(c, BUILDER, "Renew the lease", "", BUILDER, private=True, readers=["cara"], lint=False)
    X.ran(c, made["id"], BUILDER)
    asked = _ask(c, E.task(c, made["id"]), checked=False)  # Ana is let in; the question is hers and the bot's
    seen = TC.task_view(c, made["id"], CARA)
    [wait] = [w for w in seen["waits"] if w["state"] == W.PERSON]
    assert (wait["who"], wait["line"], wait["requirement_id"]) == (ANA, W.QUESTION_UNNAMED, None)
    assert asked["task_id"] not in str(seen["waits"]) and "Friday" not in str(seen["waits"])
    [own] = [w for w in TC.task_view(c, made["id"], ANA)["waits"] if w["state"] == W.PERSON]
    assert own["line"] == ASKED.splitlines()[0] and own["requirement_id"] == Q.wait_id(asked)


def test_the_backfill_skips_a_mark_whose_refs_name_another_task(c, general):
    secret = H.task_create(c, BUILDER, "Secret", "", BUILDER, private=True, lint=False)
    parent = _parent(c)
    mark = QM.ask(c, BUILDER, parent["id"], ANA, "Which venue?")
    c.execute(
        "UPDATE messages SET refs=json_set(refs, '$.send_defer_for', ?) WHERE id=?", (secret["id"], mark["message_id"])
    )
    other = _parent(c, "Pick a date")
    named = QM.ask(c, BUILDER, other["id"], ANA, "Which date?")
    c.execute(
        "UPDATE messages SET refs=json_set(refs, '$.question.note', ?) WHERE id=?", (secret["id"], named["message_id"])
    )
    done = Q.backfill(c)
    assert done["turned"] == []
    assert sorted(s["why"] for s in done["skipped"]) == [
        "its message is not a plain ❓ comment on its task: unexpected send_defer_for",
        "its message names another task, whose readers it keeps",
    ]
