"""Feedback on bot work (backend/feedback.py): one row per person and subject, only for a subject a bot made and the
rater reads, a private Assistant chat's feedback seen by its person alone, and the two ways a 👎 goes on: a note that
stays in its conversation's session, and an org rule added with the person's own rights.
Fictional people only: ana (owner), ben (admin, made a member where his own rights matter), cara (member)."""
import json

import pytest

from backend import feedback
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.test_api import api, as_member, headers, post  # noqa: F401
from backend.tests.test_threads_note_boundaries import bot_runner, bot_says, drain, finish, new_thread, say
from backend.tests.test_assistant import assistant_turn


def give(api, subject_id, rating="down", token="ana-test", expected=200, subject_type="message", **more):
    return post(api, "feedback", {"subject_type": subject_type, "subject_id": subject_id, "rating": rating, **more},
                token=token, expected=expected)


def markers(api, ids, token="ana-test", subject_type="message"):
    answer = api.get("/api/v2/feedback", params={"subject_type": subject_type, "subject_ids": ",".join(ids)},
                     headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()["items"]


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def bot_message(api, thread, text="The launch is on Friday", bot="bot:cpo"):
    return bot_says(api, thread, text, bot=bot)["id"]


def test_rating_again_replaces_the_one_row_and_bumps_updated_at(api):
    thread = new_thread(api)
    mid = bot_message(api, thread)
    first = give(api, mid, "up", reason="Clear")
    again = give(api, mid, "down", reason="Wrong date")
    assert again["id"] == first["id"] and again["created_at"] == first["created_at"]
    assert again["updated_at"] > first["updated_at"]
    assert [(m["rating"], m["reason"], m["bot_id"]) for m in markers(api, [mid])] == [("down", "Wrong date", "bot:cpo")]
    assert rows(api, "SELECT count(*) AS n FROM feedbacks")[0]["n"] == 1
    # A members thread is private: who rated in it stays out of the audit trail. On company-wide work the event
    # targets the rated message, so the SQL event gate reads it as that message's provenance says.
    assert rows(api, "SELECT count(*) AS n FROM events WHERE action LIKE 'feedback.%'")[0]["n"] == 0
    public = bot_message(api, new_thread(api, audience="company", title="Handbook"))
    rated = give(api, public, "up")
    assert [(e["target"], json.loads(e["detail"])["feedback"]) for e in rows(
        api, "SELECT target, detail FROM events WHERE action='feedback.given'")] == [(public, rated["id"])]


def test_markers_carry_only_the_callers_own_rows_and_everyones_counts(api):
    thread = new_thread(api, members=("bot:cpo", "human:ben", "human:cara"))
    mid, other = bot_message(api, thread), bot_message(api, thread, "Second draft")
    give(api, mid, "down", reason="Wrong date")
    give(api, mid, "up", token="ben-test", reason="Clear")
    give(api, mid, "down", token="cara-test", reason="Too long")
    answer = api.get("/api/v2/feedback", params={"subject_type": "message", "subject_ids": f"{mid},{other}"},
                     headers=headers("ben-test")).json()
    assert [(i["person_id"], i["reason"]) for i in answer["items"]] == [("human:ben", "Clear")]
    assert answer["counts"] == [{"subject_id": mid, "up": 1, "down": 2}]


def test_only_a_bots_work_that_the_rater_reads_takes_feedback_and_only_from_a_person(api):
    thread = new_thread(api)
    own = say(api, thread, "My own words")["id"]
    give(api, own, expected=422)                                  # a person's message
    mid = bot_message(api, thread)
    give(api, mid, token="cara-test", expected=404)               # cara is not in the thread: never confirmed
    give(api, mid, "up", tell_bot=True, expected=422)             # telling the bot goes with a 👎
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "human:ana", "Plan the launch", "", "bot:cpo")
        filed = H.task_create(c, "bot:cpo", "Book the venue", "Book the hall for Friday.", "bot:ops")
        with pytest.raises(Problem) as refused:
            feedback.give(c, api.app.state.auth, Identity("bot:ops", "bot"),
                          {"subject_type": "message", "subject_id": mid, "rating": "down", "reason": "", "tell_bot": False})
        assert refused.value.status == 403
    give(api, mine["id"], subject_type="task", expected=422)      # a task a person filed
    rated = give(api, filed["id"], subject_type="task", reason="Wrong venue")
    assert rated["bot_id"] == "bot:cpo"
    assert rows(api, "SELECT subject_revision FROM feedbacks WHERE id=?", rated["id"])[0]["subject_revision"] == 1


def test_a_private_assistant_chats_feedback_is_its_persons_alone(api):
    r, attempt = assistant_turn(api, "cara-test", text="hi")       # the Assistant's bot turn answers, as the model does
    finish(api, r, attempt, "Hi Cara, what can I do?")
    chat = api.get("/api/v2/assistant", headers=headers("cara-test")).json()["messages"]
    reply = [m for m in chat if m["sender_id"] == "bot:coo" and m["body"] == "Hi Cara, what can I do?"][-1]["id"]
    rated = give(api, reply, token="cara-test", reason="Too chirpy")
    assert rows(api, "SELECT attempt_id FROM feedbacks WHERE id=?", rated["id"])[0]["attempt_id"] == attempt["id"]
    assert [m["id"] for m in markers(api, [reply], token="cara-test")] == [rated["id"]]
    # Who rated a private reply, and how, is not in the audit trail the owner reads whole.
    assert rows(api, "SELECT count(*) AS n FROM events WHERE action LIKE 'feedback.%'")[0]["n"] == 0
    for other in ("ana-test", "ben-test"):                        # the owner and an admin included
        assert markers(api, [reply], token=other) == []
        listed = api.get("/api/v2/feedback/list", headers=headers(other)).json()
        assert listed["items"] == []
        give(api, reply, token=other, expected=404)
        assert api.delete("/api/v2/feedback/" + rated["id"], headers=headers(other)).status_code == 404
    assert [i["id"] for i in api.get("/api/v2/feedback/list", headers=headers("cara-test")).json()["items"]] == [rated["id"]]


def test_recent_reasons_are_the_callers_own_latest_distinct_ones_with_their_counts(api):
    thread = new_thread(api)
    ids = [bot_message(api, thread, f"Draft {n}") for n in range(8)]
    for mid, reason in zip(ids, ["Too long", "Wrong tone", " too long ", "", "Missed the date", "Wrong tone",
                                 "No source", "Typos"]):
        give(api, mid, reason=reason)
    give(api, ids[0], reason="Off topic", token="ana-test")       # replaces the first "Too long"
    answer = api.get("/api/v2/feedback/recent-reasons", headers=headers()).json()["reasons"]
    assert answer == [{"text": "Off topic", "count": 1}, {"text": "Typos", "count": 1}, {"text": "No source", "count": 1},
                      {"text": "Wrong tone", "count": 2}, {"text": "Missed the date", "count": 1}]
    assert api.get("/api/v2/feedback/recent-reasons", headers=headers("cara-test")).json()["reasons"] == []


def test_telling_the_bot_reaches_only_a_run_in_the_private_conversation_it_came_from(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    mid = bot_message(api, private, "Revenue was 41000")
    rated = give(api, mid, reason="Zebrawire figure is wrong", tell_bot=True)
    assert rated["outcome"] == "told_bot" and rated["note_status"] == "waiting"
    note = rows(api, "SELECT * FROM notes WHERE id=?", rated["note_id"])[0]
    assert note["message_id"] == mid and note["to_actor"] == "bot:cpo" and note["from_actor"] == "human:ana"
    say(api, public, "@cpo update the handbook")
    runs = drain(api, r)
    assert runs[0]["root"] == public and "Zebrawire" not in runs[0]["prompt"]
    carried = [run for run in runs if any("Zebrawire" in n for n in run["notes"])]
    assert [(run["root"], run["class"]) for run in carried] == [(private, "private")]
    assert all("Zebrawire" not in run["prompt"] for run in runs if run["root"] != private)
    assert markers(api, [mid])[0]["note_status"] in ("carried", "settled")
    # A run's reply names its run, with the model and harness it was handed, for training.
    reply = rows(api, "SELECT id, json_extract(refs,'$.run.attempt_id') AS aid FROM messages WHERE conversation_id=? "
                      "AND sender_id='bot:cpo' AND json_extract(refs,'$.run.attempt_id') IS NOT NULL", public)[0]
    rated = rows(api, "SELECT * FROM feedbacks WHERE id=?", give(api, reply["id"], "up")["id"])[0]
    handed = rows(api, "SELECT handed_runtime FROM attempts WHERE id=?", reply["aid"])[0]["handed_runtime"]
    assert (rated["attempt_id"], rated["harness"]) == (reply["aid"], handed) and handed


def test_removing_feedback_takes_back_its_waiting_note_and_only_its_rater_removes_it(api):
    thread = new_thread(api, members=("bot:cpo", "human:cara"))
    mid = bot_message(api, thread)
    rated = give(api, mid, reason="Wrong date", tell_bot=True)
    assert api.delete("/api/v2/feedback/" + rated["id"], headers=headers("cara-test")).status_code == 404
    answer = api.delete("/api/v2/feedback/" + rated["id"], headers=headers())
    assert answer.status_code == 200, answer.text
    assert rows(api, "SELECT canceled_at FROM notes WHERE id=?", rated["note_id"])[0]["canceled_at"]
    assert markers(api, [mid]) == []
    # A second 👎 that tells the bot again takes back the first note and leaves one with the new reason.
    first = give(api, mid, reason="Wrong date", tell_bot=True)
    second = give(api, mid, reason="Wrong place", tell_bot=True)
    assert second["note_id"] != first["note_id"]
    assert rows(api, "SELECT canceled_at FROM notes WHERE id=?", first["note_id"])[0]["canceled_at"]
    assert "Wrong place" in rows(api, "SELECT body FROM notes WHERE id=?", second["note_id"])[0]["body"]


def test_a_rule_from_a_thumbs_down_is_active_for_the_bots_manager_and_suggested_otherwise(api, monkeypatch):
    as_member(api, "ben@acme.example")                            # ben manages cpo as its owner, not as an admin
    thread = new_thread(api, members=("bot:cpo", "human:ben", "human:cara"))
    mid = bot_message(api, thread, "Revenue was up")
    ben = give(api, mid, token="ben-test", reason="No source")
    cara = give(api, mid, token="cara-test", reason="No source")
    monkeypatch.setattr(feedback, "WRITER", lambda system, user: {"rule": "Cite a source for every figure."})
    drafted = post(api, f"feedback/{ben['id']}/rule-draft", {}, token="ben-test")
    assert drafted == {"text": "Cite a source for every figure.", "drafted": True}
    post(api, f"feedback/{cara['id']}/rule-draft", {}, token="ben-test", expected=404)    # another's row
    made = post(api, f"feedback/{ben['id']}/rule", {"text": drafted["text"]}, token="ben-test")
    assert made["rule"]["status"] == "active" and made["feedback"]["outcome"] == "rule"
    assert made["feedback"]["rule_status"] == "active"
    stored = rows(api, "SELECT who, suggested_by FROM org_rules WHERE id=?", made["rule"]["id"])[0]
    assert json.loads(stored["who"])["bots"] == ["cpo"] and stored["suggested_by"] == "human:ben"
    assert post(api, f"feedback/{cara['id']}/rule", {"text": "Cite sources."}, token="cara-test")["rule"]["status"] \
        == "suggested"
    # Every bot is a wider Who than the one ben manages: only the owner and admins make that active.
    assert post(api, f"feedback/{ben['id']}/rule", {"text": "Cite sources.", "all_bots": True},
                token="ben-test")["rule"]["status"] == "suggested"
    up = give(api, bot_message(api, thread, "Done"), "up", token="ben-test")
    post(api, f"feedback/{up['id']}/rule", {"text": "Keep it up."}, token="ben-test", expected=422)


def test_the_mcp_tools_give_list_and_remove_feedback_as_the_caller(api):
    from backend.tests.test_mcp import call
    thread = new_thread(api)
    mid = bot_message(api, thread)
    failed, rated = call(api, "hub_feedback_give", {"subject_type": "message", "subject_id": mid, "rating": "down",
                                                    "reason": "Wrong date"})
    assert not failed and rated["reason"] == "Wrong date"
    assert call(api, "hub_feedback_markers", {"subject_type": "message", "subject_ids": [mid]})[1]["items"][0]["id"] \
        == rated["id"]
    assert [i["id"] for i in call(api, "hub_feedback_list", {"limit": 10})[1]["items"]] == [rated["id"]]
    assert call(api, "hub_feedback_reasons")[1]["reasons"] == [{"text": "Wrong date", "count": 1}]
    assert call(api, "hub_feedback_remove", {"id": rated["id"]})[1]["ok"] is True
    assert markers(api, [mid]) == []
