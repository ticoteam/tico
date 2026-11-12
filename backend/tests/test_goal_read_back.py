"""Goals agreed in words (backend/goal_read_back.py; spec chat-first-goals.md, criteria 1-5, 8, 9, 11, 12): the owner's
Assistant asks Tico to read back changes in the owner's chat, and the owner's yes to that message saves exactly what it
read back, or nothing."""

import json

import pytest

from backend import goal_read_back as GR
from backend import goals as G
from backend.store import H
from backend.tests.helpers.api import get, post  # noqa: F401
from backend.tests.helpers.assistant import asked, say, turn_message
from backend.tests.helpers.strategy_bot_chat import strategy_bot, turn
from backend.tests.helpers.goal_read_back import goal


def finish(api, r, attempt, text="Done."):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "goals"}, token=r["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": text, "last_seq": 0},
        token=r["token"],
    )


def reply(api, r, attempt, words):
    """The owner's Assistant turn ends on its read-back; the owner answers, and that starts its next turn."""
    finish(api, r, attempt, "Want me to save it?")
    assert say(api, words)
    return post(api, "jobs/claim", {}, token=r["token"])["attempt"]


def room_messages(api):
    room = get(api, "assistant")["room_id"]
    with api.app.state.store.read() as c:
        return [
            dict(m)
            for m in c.execute(
                "SELECT id, sender_id, body, created_at FROM messages WHERE conversation_id=? "
                "ORDER BY created_at, rowid",
                (room,),
            )
        ]


def backdate(api, minutes, *ids):
    with api.app.state.store.transaction() as c:
        for i, mid in enumerate(ids):
            c.execute(
                "UPDATE messages SET created_at=? WHERE id=?",
                (H.shift(H.now(), seconds=-minutes * 60 - len(ids) + i), mid),
            )


def read_back(api, attempt, *changes, expected=200):
    return post(api, "goals/read-back", {"changes": list(changes)}, token=attempt["token"], expected=expected)


def save(api, attempt, step, expected=200, ref="turn"):
    return asked(
        api, step["path"].removeprefix("/api/v2/"), json.loads(step["body"]), attempt["token"], ref, expected=expected
    )


NEW_GOAL = {
    "kind": "goal_create",
    "title": "Get 100 companies to try Tico",
    "owner": "ops",
    "measure": {"name": "Companies that tried Tico"},
    "target": {"kind": "improve", "target": 100, "deadline": "2026-11-28"},
}


def test_a_new_goal_is_read_back_by_tico_and_a_plain_yes_saves_exactly_it_as_the_owner(api):
    strategy_bot(api)
    serves = goal(api, "Developers and small companies keep using Tico", owner="company")
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops: get 100 companies to try Tico")
    # Only the owner's Assistant, in their own chat: not the owner's own click, not another bot.
    post(api, "goals/read-back", {"changes": [NEW_GOAL]}, expected=403)
    back = read_back(api, attempt, {**NEW_GOAL, "parent_id": serves["id"]})
    text = back["text"]
    assert text.startswith("I'll save this goal:\n**Get 100 companies to try Tico** · owner ")
    assert "supports “Developers and small companies keep using Tico”" in text
    assert "measure “Companies that tried Tico” (new: higher is better, weekly), target 100 by Nov 28" in text
    assert text.endswith("Shall I save it?")
    # Tico wrote it, as the Assistant's reply in the owner's chat; the turn's closing text is not posted after it.
    said = room_messages(api)
    assert (said[-1]["id"], said[-1]["sender_id"]) == (back["message_id"], "bot:assistant")
    # Before the yes, nothing saves, cited or not.
    first, measure = back["changes"][0]["steps"]
    assert save(api, attempt, first, expected=403)["error"]["code"] == "not_agreed"
    assert (
        post(api, "goals", json.loads(first["body"]), token=attempt["token"], expected=403)["error"]["code"]
        == "not_agreed"
    )
    attempt = reply(api, r, attempt, "yes")
    assert room_messages(api)[-2]["id"] == back["message_id"]
    made = save(api, attempt, first)
    gid = made["goal"]["id"]
    link = made["read_back"]["link"]
    assert made["read_back"] == {"change": 1, "step": 1, "steps": 2, "saved": True, "link": link}
    assert link.endswith(f"/#/goals/{gid}")
    # The measure goes on the goal this read-back made, and nowhere else.
    save(api, attempt, {**measure, "path": f"/api/v2/goals/{serves['id']}/kpis"}, expected=403)
    save(api, attempt, {**measure, "path": measure["path"].replace(GR.NEW, gid)})
    # Saved exactly as read back, and once.
    shown = get(api, "goals/" + gid)["goal"]
    assert (shown["title"], shown["owner_id"], shown["parent_id"]) == (
        "Get 100 companies to try Tico",
        "bot:ops",
        serves["id"],
    )
    assert [(k["name"], k["target_label"]) for k in shown["kpis"]] == [("Companies that tried Tico", "→ 100 by Nov 28")]
    assert save(api, attempt, first, expected=409)["error"]["code"] == "conflict"
    # Recorded as the owner, via their Assistant, citing their yes and the read-back.
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor, detail FROM events WHERE action='goal.create' AND target=?", (gid,)).fetchone()
    detail = H._json(row["detail"], {})
    assert row["actor"] == "human:ana" and detail["via"] == "assistant"
    assert {k: detail["delegation"][k] for k in ("for", "cited", "assistant", "goal_read_back", "change", "words")} == {
        "for": "human:ana",
        "cited": turn_message(api, attempt),
        "assistant": "bot:assistant",
        "goal_read_back": back["message_id"],
        "change": 1,
        "words": "yes",
    }
    status = get(api, "goals/read-back/" + back["message_id"], attempt["token"])["changes"][0]
    assert (status["saved"], status["agreed"], status["link"]) == (True, True, made["read_back"]["link"])


def test_a_plan_of_28_goals_is_read_back_once_and_one_yes_saves_all_28(api):
    # Spec one-yes-per-request, criterion 1: the whole plan at once, one yes, every change saved, no second read-back.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set goals for all my owner bots")
    plan = [{"kind": "goal_create", "title": f"Owner goal {i}", "owner": "ops"} for i in range(1, 29)]
    back = read_back(api, attempt, *plan)
    assert len(back["changes"]) == 28
    assert back["text"].startswith("I'll save these 28 changes:\n1. ")
    assert back["text"].endswith("Shall I save all 28?")
    attempt = reply(api, r, attempt, "yes")
    for ch in back["changes"]:
        save(api, attempt, ch["steps"][0])
    titles = {g["title"] for g in get(api, "goals?all=true")["goals"]}
    assert {f"Owner goal {i}" for i in range(1, 29)} <= titles
    # One plan, one read-back: nothing else of Tico's in the chat after the yes.
    reads = [m for m in room_messages(api) if m["body"].startswith("I'll save")]
    assert [m["id"] for m in reads] == [back["message_id"]]


def test_agreement_in_the_owners_own_words_saves(api):
    # Criterion 2: any reply that clearly agrees; the Assistant judges the words, Tico no longer lists them.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops")
    for words in ("sure, go for it", "do it", "No problem, save it", "y"):
        step = read_back(api, attempt, {**NEW_GOAL, "title": words})["changes"][0]["steps"][0]
        attempt = reply(api, r, attempt, words)
        assert save(api, attempt, step)["goal"]["title"] == words, words


def test_the_owners_words_by_number_still_limit_what_is_saved():
    six = set(range(1, 7))
    for words in ("yes", "sure, go for it", "ok, thanks", "yes but make it 50", "go"):
        assert GR.agreed(words, 6) == six, words
    assert GR.agreed("yes to 1 and 3", 6) == {1, 3}
    assert GR.agreed("yes to 1, 3 and 5, no to 2", 6) == {1, 3, 5}
    assert GR.agreed("yes to #2", 6) == {2}
    assert GR.agreed("yes except 2", 6) == {1, 3, 4, 5, 6}
    assert GR.agreed("yes to all but 2 and 4", 6) == {1, 3, 5, 6}
    # A number the plan does not have is not a yes: the Assistant asks again.
    for words in ("yes to 7", "yes to 1, no to 1", "yes except 1, 2, 3, 4, 5, 6", "yes to 0"):
        assert GR.agreed(words, 6) is None, words


def test_a_question_between_the_plan_and_the_yes_keeps_the_plan(api):
    # Criterion 3: a question, its answer, a notice in between: the later yes saves the plan without a new read-back.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops")
    back = read_back(api, attempt, NEW_GOAL)
    step = back["changes"][0]["steps"][0]
    attempt = reply(api, r, attempt, "what does weekly mean here?")
    finish(api, r, attempt, "It is counted every Monday.")
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, get(api, "assistant")["room_id"])
        H._write_message(
            c, H.KEEPER, "human:ana", "Ops finished a task", room, "notice", {"assistant": True}, None, None
        )
    assert say(api, "great, go ahead")
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    assert save(api, attempt, step)["read_back"]["saved"] is True
    assert [m["id"] for m in room_messages(api) if m["body"].startswith("I'll save")] == [back["message_id"]]


def test_a_change_asked_for_replaces_the_plan_and_only_the_changed_plan_saves(api):
    # Criterion 4: nothing is saved until the owner agrees to the changed plan; the old plan no longer saves.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops")
    old = read_back(api, attempt, NEW_GOAL)["changes"][0]["steps"][0]
    attempt = reply(api, r, attempt, "make it 50 companies")
    changed = {**NEW_GOAL, "title": "Get 50 companies to try Tico"}
    new = read_back(api, attempt, changed)["changes"][0]["steps"][0]
    attempt = reply(api, r, attempt, "yes")
    refused = save(api, attempt, old, expected=403)["error"]
    assert refused["code"] == "not_agreed"
    assert save(api, attempt, new)["goal"]["title"] == "Get 50 companies to try Tico"
    # A plan read back after the yes replaces the one the yes answered.
    attempt = reply(api, r, attempt, "and one for Support")
    first = read_back(api, attempt, {**NEW_GOAL, "title": "Answer support in a day"})["changes"][0]["steps"][0]
    attempt = reply(api, r, attempt, "yes")
    read_back(api, attempt, {**NEW_GOAL, "title": "Answer support in an hour"})
    refused = save(api, attempt, first, expected=403)["error"]
    assert refused["code"] == "not_agreed" and "newer read-back" in refused["detail"]
    titles = {g["title"] for g in get(api, "goals?all=true")["goals"]}
    assert NEW_GOAL["title"] not in titles and "Answer support in a day" not in titles


def test_after_the_yes_thanks_changes_nothing_and_stop_halts_the_rest(api):
    # Criterion 5: what is agreed is saved; only a stop halts it, and the read-back says what was and wasn't saved.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set three goals for Ops")
    plan = [{"kind": "goal_create", "title": f"Ops goal {i}", "owner": "ops"} for i in (1, 2, 3)]
    back = read_back(api, attempt, *plan)
    steps = [ch["steps"][0] for ch in back["changes"]]
    attempt = reply(api, r, attempt, "yes")
    save(api, attempt, steps[0])
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, get(api, "assistant")["room_id"])
        H._write_message(c, "human:ana", "bot:assistant", "thanks!", room, "say", {"assistant": True}, None, None)
    save(api, attempt, steps[1])
    with api.app.state.store.transaction() as c:
        H._write_message(c, "human:ana", "bot:assistant", "stop", room, "say", {"assistant": True}, None, None)
    refused = save(api, attempt, steps[2], expected=403)["error"]
    assert refused["code"] == "not_agreed" and "stop" in refused["detail"]
    status = get(api, "goals/read-back/" + back["message_id"], attempt["token"])["changes"]
    assert [s["saved"] for s in status] == [True, True, False]
    assert "stop" in status[2]["reason"]
    titles = {g["title"] for g in get(api, "goals?all=true")["goals"]}
    assert {"Ops goal 1", "Ops goal 2"} <= titles and "Ops goal 3" not in titles


def test_the_stop_words_halt_and_a_yes_that_opens_with_no_does_not():
    for words in ("stop", "Stop!", "wait", "hold on", "cancel that", "no", "don't save the rest", "never mind"):
        assert GR.halts(words), words
    for words in ("thanks", "no problem", "no worries, thanks", "great", "what about Support?"):
        assert not GR.halts(words), words


def test_a_plan_stands_past_thirty_minutes(api):
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops")
    asked_for = turn_message(api, attempt)
    back = read_back(api, attempt, NEW_GOAL)
    backdate(api, 120, asked_for, back["message_id"])
    attempt = reply(api, r, attempt, "yes")
    assert save(api, attempt, back["changes"][0]["steps"][0])["goal"]["title"] == NEW_GOAL["title"]


def test_only_the_owners_reply_after_the_plan_saves(api):
    # Stays: Tico still needs the owner's own message after the plan, and saves only what the plan shows.
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal for Ops")
    step = read_back(api, attempt, NEW_GOAL)["changes"][0]["steps"][0]
    # The turn that read the plan back cites the request, which came before it: not agreed.
    assert save(api, attempt, step, expected=403)["error"]["code"] == "not_agreed"
    assert [g for g in get(api, "goals?all=true")["goals"] if g["title"] == NEW_GOAL["title"]] == []


def test_a_save_that_differs_from_the_read_back_is_refused_and_saves_nothing(api):
    strategy_bot(api)
    other = goal(api, "Keep the bots running")
    target = goal(api, "Launch the docs")
    r, attempt = turn(api, "assistant", "ana-test", "Rename the docs goal")
    back = read_back(api, attempt, {"kind": "goal_update", "goal_id": target["id"], "title": "Ship the docs"})
    step = back["changes"][0]["steps"][0]
    attempt = reply(api, r, attempt, "yes")
    for changed in (
        {**step, "body": '{"title": "Ship the docs v2"}'},  # another value
        {**step, "body": '{"title": "Ship the docs", "owner": "assistant"}'},  # another field
        {**step, "path": f"/api/v2/goals/{other['id']}"},  # another goal
        {**step, "path": f"/api/v2/goals/{target['id']}/why", "body": '{"why": "Ship the docs"}'},
    ):
        assert save(api, attempt, changed, expected=403)["error"]["code"] == "not_agreed"
    assert [get(api, "goals/" + g["id"])["goal"]["title"] for g in (target, other)] == [
        "Launch the docs",
        "Keep the bots running",
    ]
    # The same save, said another way (an owner by slug or by actor), is the same save.
    assert save(api, attempt, step)["goal"]["title"] == "Ship the docs"


def test_a_change_to_a_goal_reads_back_was_and_now_for_the_changed_fields_only(api):
    strategy_bot(api)
    serves = goal(api, "Grow the company", owner="company")
    row = goal(api, "Launch the docs", parent_id=serves["id"])
    r, attempt = turn(api, "assistant", "ana-test", "Fix the docs goal")
    text = read_back(
        api,
        attempt,
        {
            "kind": "goal_update",
            "goal_id": row["id"],
            "title": "Ship the docs",
            "owner": "ops",
            "parent_id": serves["id"],
            "why": "Docs bring developers",
        },
    )["text"]
    assert (
        "Change **Launch the docs**: title was “Launch the docs”, now “Ship the docs”; Why was none, now "
        "“Docs bring developers”"
    ) in text
    assert "owner_id" not in text and "supports" not in text
    refused = read_back(
        api, attempt, {"kind": "goal_update", "goal_id": row["id"], "title": "Launch the docs"}, expected=409
    )["error"]
    assert refused["code"] == "no_change"


def six_changes(api):
    strategy_bot(api)
    a, b, c3 = goal(api, "Launch the docs"), goal(api, "Keep the bots running"), goal(api, "Win ten customers")
    with api.app.state.store.transaction() as c:
        kpi = G.kpi_create(
            c,
            "human:ana",
            "bot:ops",
            {"name": "Uptime", "unit": "%"},
            goal_id=b["id"],
            target={"kind": "improve", "target": 99, "deadline": "2026-12-31"},
        )
        wording = G.propose(c, G.AUTO_ACTOR, "goal_wording", c3["id"], payload={"title": "Win ten paying customers"})
    return (
        a,
        b,
        c3,
        kpi,
        wording,
        [
            {"kind": "goal_update", "goal_id": a["id"], "title": "Ship the docs"},
            {"kind": "measure_add", "goal_id": a["id"], "measure": {"name": "Docs pages"}},
            {
                "kind": "target_set",
                "goal_id": b["id"],
                "kpi_id": kpi["id"],
                "target": {"kind": "improve", "target": 99.9, "deadline": "2027-03-31"},
            },
            {"kind": "suggestion", "proposal_id": wording["id"], "decision": "confirm"},
            {"kind": "goal_create", "title": "Answer support in a day", "owner": "ops"},
            {"kind": "measure_remove", "goal_id": b["id"], "kpi_id": kpi["id"]},
        ],
    )


def test_yes_to_1_and_3_of_six_saves_only_those_and_says_why_not_for_the_rest(api):
    a, b, c3, kpi, wording, changes = six_changes(api)
    r, attempt = turn(api, "assistant", "ana-test", "Go through my goals")
    back = read_back(api, attempt, *changes)
    lines = back["text"].split("\n")
    assert lines[0] == "I'll save these 6 changes:" and lines[1].startswith("1. Change **Launch the docs**: title was")
    assert lines[3].startswith("3. Change the target of “Uptime” on **Keep the bots running**: was 99")
    assert "Dec 31, now 99.9" in lines[3] and "Mar 31" in lines[3]
    assert lines[4] == (
        "4. Accept the suggestion on **Win ten customers**: Title was “Win ten customers”, now "
        "“Win ten paying customers”"
    )
    assert back["text"].endswith("Shall I save all 6?")
    steps = [ch["steps"][0] for ch in back["changes"]]
    attempt = reply(api, r, attempt, "yes to 1 and 3")
    save(api, attempt, steps[0])
    save(api, attempt, steps[2])
    for n in (2, 4, 5, 6):
        refused = save(api, attempt, steps[n - 1], expected=403)["error"]
        assert refused["code"] == "not_agreed" and f"did not say yes to change {n}" in refused["detail"]
    assert get(api, "goals/" + a["id"])["goal"]["title"] == "Ship the docs"
    assert [k["name"] for k in get(api, "goals/" + a["id"])["goal"]["kpis"]] == []
    assert get(api, "goals/" + c3["id"])["goal"]["title"] == "Win ten customers"
    status = get(api, "goals/read-back/" + back["message_id"], attempt["token"])["changes"]
    assert [(s["n"], s["saved"], s["agreed"]) for s in status] == [
        (1, True, True),
        (2, False, False),
        (3, True, True),
        (4, False, False),
        (5, False, False),
        (6, False, False),
    ]
    assert {s["reason"] for s in status if not s["saved"]} == {"The owner did not say yes to it"}


def test_yes_to_all_settles_a_suggestion_and_its_card_leaves(api):
    a, b, c3, kpi, wording, changes = six_changes(api)
    r, attempt = turn(api, "assistant", "ana-test", "Go through the suggestions")
    back = read_back(api, attempt, changes[3])
    attempt = reply(api, r, attempt, "go ahead")
    assert save(api, attempt, back["changes"][0]["steps"][0])["proposal"]["status"] == "confirmed"
    assert get(api, "goals/" + c3["id"])["goal"]["title"] == "Win ten paying customers"


def test_a_yes_takes_a_measure_off_its_goal_and_keeps_the_measure(api):
    a, b, c3, kpi, wording, changes = six_changes(api)
    r, attempt = turn(api, "assistant", "ana-test", "Drop uptime from the bots goal")
    back = read_back(api, attempt, changes[5])
    attempt = reply(api, r, attempt, "yes")
    step = back["changes"][0]["steps"][0]
    assert step["path"] == f"/api/v2/goals/{b['id']}/kpis/{kpi['id']}/unlink"
    save(api, attempt, step)
    assert get(api, "goals/" + b["id"])["goal"]["kpis"] == []
    # Once: the same yes does not save it again.
    assert save(api, attempt, step, expected=409)["error"]["code"] == "conflict"
    with api.app.state.store.read() as c:
        assert len(GR.saves(c, back["message_id"])) == 1
    with api.app.state.store.read() as c:
        assert G.kpi(c, kpi["id"])["name"] == "Uptime"


def test_a_suggestion_someone_settled_after_the_read_back_saves_nothing_and_says_so(api):
    a, b, c3, kpi, wording, changes = six_changes(api)
    r, attempt = turn(api, "assistant", "ana-test", "Go through the suggestions")
    back = read_back(api, attempt, changes[3])
    attempt = reply(api, r, attempt, "yes")
    with api.app.state.store.transaction() as c:
        G.decide(c, "human:ana", wording["id"], "reject")
    refused = save(api, attempt, back["changes"][0]["steps"][0], expected=409)["error"]
    assert refused["code"] == "goal_changed" and "ana turned down the suggestion" in refused["detail"]
    assert get(api, "goals/" + c3["id"])["goal"]["title"] == "Win ten customers"


def test_a_goal_someone_changed_after_the_read_back_saves_nothing_and_says_what_changed(api):
    strategy_bot(api)
    row = goal(api, "Launch the docs")
    r, attempt = turn(api, "assistant", "ana-test", "Rename the docs goal")
    back = read_back(
        api,
        attempt,
        {"kind": "goal_update", "goal_id": row["id"], "title": "Ship the docs"},
        {"kind": "measure_add", "goal_id": row["id"], "measure": {"name": "Docs pages"}},
    )
    attempt = reply(api, r, attempt, "yes")
    with api.app.state.store.transaction() as c:
        G.update(c, "human:ben", row["id"], title="Publish the docs")
    for ch in back["changes"]:
        refused = save(api, attempt, ch["steps"][0], expected=409)["error"]
        assert refused["code"] == "goal_changed"
        assert "ben changed the title from “Launch the docs” to “Publish the docs”" in refused["detail"]
    shown = get(api, "goals/" + row["id"])["goal"]
    assert shown["title"] == "Publish the docs" and shown["kpis"] == []
    # Its own saves are not someone else's change: a fresh read-back saves both.
    attempt = reply(api, r, attempt, "Try again")
    back = read_back(
        api,
        attempt,
        {"kind": "goal_update", "goal_id": row["id"], "title": "Ship the docs"},
        {"kind": "measure_add", "goal_id": row["id"], "measure": {"name": "Docs pages"}},
    )
    attempt = reply(api, r, attempt, "yes")
    for ch in back["changes"]:
        save(api, attempt, ch["steps"][0])
    shown = get(api, "goals/" + row["id"])["goal"]
    assert shown["title"] == "Ship the docs" and [k["name"] for k in shown["kpis"]] == ["Docs pages"]


def test_what_stays_a_click_is_refused_at_read_back_with_one_reason_and_nothing_is_posted(api):
    strategy_bot(api)
    row = goal(api, "Launch the docs")
    r, attempt = turn(api, "assistant", "ana-test", "Make the docs goal green and log 40 pages")
    before = len(room_messages(api))
    for change, word in (
        ({"kind": "goal_update", "goal_id": row["id"], "status": "green"}, "colour"),
        ({"kind": "colour", "goal_id": row["id"], "status": "red"}, "colour"),
        ({"kind": "done", "goal_id": row["id"]}, "done or dropped"),
        ({"kind": "dropped", "goal_id": row["id"]}, "done or dropped"),
        ({"kind": "archive", "kpi_id": "k1"}, "archiving"),
        ({"kind": "reading", "kpi_id": "k1", "value": 40}, "reading"),
        ({"kind": "vision", "vision": "Every company runs on Tico"}, "Vision"),
        ({"kind": "recovery", "goal_id": row["id"]}, "recovery suggestion"),
    ):
        refused = read_back(
            api, attempt, {"kind": "goal_update", "goal_id": row["id"], "title": "Ship the docs"}, change, expected=403
        )["error"]
        assert refused["code"] == "stays_a_click" and word in refused["detail"], (change, refused)
        assert refused["detail"].count(".") == 1  # one sentence the Assistant can say
        assert refused["detail"].startswith("Nothing was saved: ")
    assert len(room_messages(api)) == before
    assert get(api, "goals/" + row["id"])["goal"]["title"] == "Launch the docs"
    # What could not be saved by the owner's own click is refused before it is read back.
    refused = read_back(
        api, attempt, {"kind": "goal_update", "goal_id": row["id"], "parent_id": row["id"]}, expected=422
    )["error"]
    assert "Change 1 can't be saved" in refused["detail"]
    read_back(
        api,
        attempt,
        *[{"kind": "goal_create", "title": f"Goal {i}", "owner": "ops"} for i in range(GR.MOST + 1)],
        expected=422,
    )


def test_only_the_owners_strategy_bot_turn_saves_on_a_yes(api):
    strategy_bot(api)
    from backend.tests.helpers.assistant import assistant_turn

    r, attempt = assistant_turn(api, "ben-test", "Set a goal for Ops")
    read_back(api, attempt, NEW_GOAL, expected=403)
    # Another person's Assistant may not make a goal: the route's own owner check refuses it as that person.
    refused = asked(api, "goals", {"title": "x", "owner": "ops"}, attempt["token"], "turn", expected=403)["error"]
    assert refused["code"] == "forbidden"


def test_repeated_changes_are_refused_before_posting_a_draft(api):
    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Set a goal")
    before = room_messages(api)
    result = read_back(api, attempt, NEW_GOAL, dict(reversed(list(NEW_GOAL.items()))), expected=422)
    assert result['error']['code'] == 'validation'
    assert 'repeated' in result['error']['detail']
    assert room_messages(api) == before


def test_cli_read_back_and_cited_save_use_the_server_agreement(api, monkeypatch, capsys):
    from clients import hubcli, remotecli
    from clients.tico import APIError
    from backend.tests.helpers.api import headers

    class TurnClient:
        def __init__(self, *args, **kwargs):
            pass

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            extra = {'X-Tico-On-Behalf-Of': delegate} if delegate else {}
            response = api.request(method, '/api/v2/' + path, json=body, headers={**headers(attempt['token']), **extra})
            value = response.json()
            if response.status_code >= 400:
                error = value['error']
                raise APIError(error['code'], error['detail'], response.status_code, False)
            return value

        def get(self, path, **query):
            response = api.get('/api/v2/' + path, headers=headers(attempt['token']))
            assert response.status_code == 200, response.text
            return response.json()

        def post(self, path, body, key=None):
            return self.call('POST', path, body, key)

    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", 'Set two goals')
    monkeypatch.setattr(remotecli, 'Client', TurnClient)
    monkeypatch.setenv('HUB_API_URL', 'https://synthetic.invalid')
    changes = [
        {'kind': 'goal_create', 'owner': 'company', 'title': 'First', 'why': 'Purpose'},
        {'kind': 'goal_create', 'owner': 'company', 'title': 'Second'},
    ]
    assert hubcli.main(['goal', 'read-back', json.dumps(changes)]) == 0
    back = json.loads(capsys.readouterr().out)
    assert back['message_id'] == room_messages(api)[-1]['id']
    assert back['text'].endswith('Shall I save both?')
    attempt = reply(api, r, attempt, 'yes')
    # No citation is not authorization, even during the yes turn.
    assert hubcli.main(['goal', 'create', '--owner', 'company', '--title', 'First']) == 2
    assert json.loads(capsys.readouterr().out)['error'] == 'not_agreed'
    assert hubcli.main(['goal', 'create', '--owner', 'company', '--title', 'First', '--on-behalf-of', 'turn']) == 0
    gid = json.loads(capsys.readouterr().out)['id']
    step = back['changes'][0]['steps'][1]
    assert (
        hubcli.main(
            [
                'goal',
                'save',
                step['method'],
                step['path'],
                step['body'],
                '--created-goal',
                gid,
                '--on-behalf-of',
                'turn',
            ]
        )
        == 0
    )
    capsys.readouterr()
    with api.app.state.store.read() as c:
        assert G.goal(c, gid)['why'] == 'Purpose'
        assert not c.execute("SELECT id FROM goals WHERE title='Second'").fetchone()


def test_read_back_refuses_goals_measures_and_suggestions_the_strategy_bot_cannot_read(api):
    from backend.tests.helpers.goal_cards import propose
    from backend.tests.helpers.groups_leads import as_bot

    hidden = goal(api, "Private inbox objective", owner="inbox")
    public = goal(api, "Publish the guide")
    kpi = post(api, f"goals/{hidden['id']}/kpis", {"name": "Private inbox measure"})["kpi"]
    suggestion = propose(api, "goal_kpi", {"kpi_id": kpi["id"]}, public["id"])
    strategy_bot(api)
    as_bot(api, "strategy-owner", "strategy-test")
    # The Strategy Owner itself is refused the private bot's work; the owner's
    # Assistant must preserve that boundary when it asks Tico for a read-back.
    get(api, "status?bot=inbox", "strategy-test", expected=404)
    get(api, "status?bot=ops", "strategy-test")
    _, attempt = turn(api, "assistant", "ana-test", "Go through suggestions")
    changes = [
        {"kind": "goal_update", "goal_id": hidden["id"], "title": "Changed"},
        {"kind": "measure_add", "goal_id": public["id"], "measure": {"kpi_id": kpi["id"]}},
        {"kind": "suggestion", "proposal_id": suggestion["id"], "decision": "confirm"},
        {"kind": "goal_create", "title": "New", "owner": "inbox"},
        {"kind": "goal_create", "title": "New", "owner": "ops", "parent_id": hidden["id"]},
    ]
    before = room_messages(api)
    for change in changes:
        refused = read_back(api, attempt, change, expected=403)
        assert refused["error"]["detail"] == "The Strategy Owner can't see that bot's goals."
        assert "Private inbox" not in str(refused)
        assert room_messages(api) == before
    assert get(api, "goals/" + hidden["id"])["goal"]["title"] == "Private inbox objective"
    assert get(api, "goals/" + public["id"])["goal"]["kpis"] == []


def test_a_yes_saves_a_goal_without_optional_measures_and_later_sync_does_not_nag(api):
    from backend import goal_asks as GA

    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Publish the guide")
    back = read_back(api, attempt, {"kind": "goal_create", "title": "Publish the guide", "owner": "ops"})
    assert "warning" not in back["text"].lower()
    step = back["changes"][0]["steps"][0]
    assert save(api, attempt, step, expected=403)["error"]["code"] == "not_agreed"
    attempt = reply(api, r, attempt, "yes")
    made = save(api, attempt, step)["goal"]
    assert made["kpis"] == []
    with api.app.state.store.transaction() as c:
        GA.sync(c, "ana", at=H.shift(H.now(), seconds=40 * 86400))
        assert not c.execute("SELECT 1 FROM goal_proposals WHERE goal_id=?", (made["id"],)).fetchone()


def test_unattended_strategy_bot_can_file_a_card_but_cannot_save_a_goal_or_target(api):
    from backend.tests.helpers.groups_leads import as_bot

    strategy_bot(api)
    as_bot(api, "strategy-owner", "strategy-test")
    row = goal(api, "Publish the guide")
    kpi = post(api, f"goals/{row['id']}/kpis", {"name": "Guides read"})["kpi"]
    for path, body, status in (
        ("goals", {"title": "Unattended", "owner": "ops"}, 403),
        ("goals/" + row["id"], {"title": "Unattended"}, 409),
        (f"goals/{row['id']}/kpis/{kpi['id']}", {"kind": "improve", "target": 30, "deadline": "2099-12-31"}, 409),
    ):
        post(api, path, body, token="strategy-test", expected=status)
    p = post(
        api,
        "goal-proposals",
        {
            "kind": "kpi_target",
            "goal_id": row["id"],
            "kpi_id": kpi["id"],
            "payload": {"kind": "improve", "target": 30, "deadline": "2099-12-31"},
            "reason": "Number stated by owner",
        },
        token="strategy-test",
    )["proposal"]
    assert p["status"] == "pending"
    shown = get(api, "goals/" + row["id"])["goal"]
    assert shown["title"] == "Publish the guide" and shown["kpis"][0]["link"]["kind"] == "none"


def test_read_back_cannot_be_read_or_saved_after_strategy_bot_read_access_is_revoked(api):
    from backend.tests.helpers.api import restrict

    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Publish the guide")
    row = goal(api, "Publish the guide")
    back = read_back(api, attempt, {"kind": "goal_update", "goal_id": row["id"], "title": "Publish guide today"})
    attempt = reply(api, r, attempt, "yes")
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    denied = get(api, "goals/read-back/" + back["message_id"], attempt["token"], expected=403)
    assert denied["error"]["detail"] == "The Strategy Owner can't see that bot's goals."
    save(api, attempt, back["changes"][0]["steps"][0], expected=403)
    assert get(api, "goals/" + row["id"])["goal"]["title"] == "Publish the guide"


def test_stored_read_back_keeps_access_checks_for_the_old_parent_it_named(api):
    from backend.tests.helpers.api import restrict

    strategy_bot(api)
    parent = goal(api, "Financial parent", owner="finance")
    child = goal(api, "Publish the guide", parent_id=parent["id"])
    _, attempt = turn(api, "assistant", "ana-test", "Unlink the parent")
    back = read_back(api, attempt, {"kind": "goal_update", "goal_id": child["id"], "parent_id": ""})
    assert "Financial parent" in back["text"]
    # Someone moves the child; the old parent is now named only in the stored read-back.
    post(api, "goals/" + child["id"], {"parent_id": ""})
    with api.app.state.store.transaction() as c:
        restrict(c, "finance", people=["ana"])
    denied = get(api, "goals/read-back/" + back["message_id"], attempt["token"], expected=403)
    assert denied["error"]["detail"] == "The Strategy Owner can't see that bot's goals."


def test_unmeasured_flags_are_refused_without_saving_a_proposal(api):
    from backend.tests.helpers.groups_leads import as_bot

    strategy_bot(api)
    as_bot(api, "strategy-owner", "strategy-test")
    row = goal(api, "Publish the guide")
    for token in ("ana-test", "strategy-test"):
        refused = post(
            api,
            "goal-proposals",
            {"kind": "flag", "goal_id": row["id"], "payload": {"issue": "unmeasured", "note": "No KPI"}},
            token=token,
            expected=422,
        )
        assert "vague|duplicate" in refused["error"]["detail"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM goal_proposals WHERE goal_id=?", (row["id"],)).fetchone()
    # Allowed flags still work; pre-existing rows are not deleted or rewritten.
    allowed = post(
        api,
        "goal-proposals",
        {"kind": "flag", "goal_id": row["id"], "payload": {"issue": "vague"}},
        token="strategy-test",
    )["proposal"]
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE goal_proposals SET payload=? WHERE id=?", (json.dumps({"issue": "unmeasured"}), allowed["id"])
        )
    assert get(api, "goal-proposals")["proposals"][0]["payload"]["issue"] == "unmeasured"


def test_legacy_read_backs_require_a_fresh_draft_before_read_or_save(api):
    from backend.tests.helpers.api import restrict

    strategy_bot(api)
    parent = goal(api, "Private old parent", owner="finance")
    child = goal(api, "Publish the guide", parent_id=parent["id"])
    r, attempt = turn(api, "assistant", "ana-test", "Unlink the parent")
    back = read_back(api, attempt, {"kind": "goal_update", "goal_id": child["id"], "parent_id": ""})
    attempt = reply(api, r, attempt, "yes")
    with api.app.state.store.transaction() as c:
        stored = c.execute(
            "SELECT detail FROM events WHERE action=? AND target=?", (GR.EVENT, back["message_id"])
        ).fetchone()
        old = json.loads(stored["detail"])
        for change in old["changes"]:
            change.pop("read_owners")
        c.execute(
            "UPDATE events SET detail=? WHERE action=? AND target=?", (json.dumps(old), GR.EVENT, back["message_id"])
        )
        G.update(c, "human:ana", child["id"], parent_id="")
        restrict(c, "finance", people=["ana"])
    for denied in (
        get(api, "goals/read-back/" + back["message_id"], attempt["token"], expected=403),
        save(api, attempt, back["changes"][0]["steps"][0], expected=403),
    ):
        assert denied["error"]["code"] == "not_agreed"
        assert "Read it back again" in denied["error"]["detail"]
        assert "Private old parent" not in str(denied)
    with api.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM events WHERE action=? AND target=?", (GR.SAVED, back["message_id"])
        ).fetchone()


@pytest.mark.parametrize("change_kind", ["goal", "suggestion"])
def test_conflicts_do_not_name_a_bot_editor_in_stored_read_or_save(api, change_kind):
    from backend.tests.helpers.goal_cards import propose

    strategy_bot(api)
    row = goal(api, "Publish the guide")
    suggestion = propose(api, "goal_wording", {"title": "Publish guide today"}, row["id"])
    r, attempt = turn(api, "assistant", "ana-test", "Update the guide goal")
    change = (
        {"kind": "goal_update", "goal_id": row["id"], "title": "Publish guide today"}
        if change_kind == "goal"
        else {"kind": "suggestion", "proposal_id": suggestion["id"], "decision": "confirm"}
    )
    back = read_back(api, attempt, change)
    attempt = reply(api, r, attempt, "yes")
    with api.app.state.store.transaction() as c:
        if change_kind == "goal":
            G.update(c, "bot:inbox", row["id"], title="Publish guide tomorrow")
        else:
            G.decide(c, "bot:inbox", suggestion["id"], "reject")
    stored = get(api, "goals/read-back/" + back["message_id"], attempt["token"])
    refused = save(api, attempt, back["changes"][0]["steps"][0], expected=409)
    assert "someone" in stored["changes"][0]["reason"] and "someone" in refused["error"]["detail"]
    assert "inbox" not in str(stored) and "inbox" not in str(refused)
    assert get(api, "goals/" + row["id"])["goal"]["title"] != "Publish guide today"


@pytest.mark.parametrize("edited", ["title", "why", "owner", "created_owner"])
def test_revoked_read_back_access_is_checked_before_conflict_details(api, edited):
    from backend.tests.helpers.api import restrict

    strategy_bot(api)
    r, attempt = turn(api, "assistant", "ana-test", "Publish the guide")
    if edited == "created_owner":
        change = {"kind": "goal_create", "title": "Publish the guide", "owner": "ops", "why": "Public purpose"}
    else:
        row = goal(api, "Publish the guide")
        change = {"kind": "goal_update", "goal_id": row["id"], "title": "Publish guide today"}
    back = read_back(api, attempt, change)
    attempt = reply(api, r, attempt, "yes")
    steps = back["changes"][0]["steps"]
    if edited == "created_owner":
        row = save(api, attempt, steps[0])["goal"]
        step = {**steps[1], "path": steps[1]["path"].replace(GR.NEW, row["id"])}
    else:
        step = steps[0]
    # Access revocation alone and an accessible edit each passed earlier tests.
    # Combine them: even a refusal must not reveal the newly private values.
    with api.app.state.store.transaction() as c:
        if edited in ("owner", "created_owner"):
            G.update(c, "human:ana", row["id"], owner="bot:inbox")
        else:
            restrict(c, "ops", people=["ana"])
            if edited == "why":
                from backend import company

                company.set_why(c, "human:ana", row["id"], "Private revised words")
            else:
                G.update(c, "human:ana", row["id"], title="Private revised words")
        before = dict(G.goal(c, row["id"]))
        events = c.execute(
            "SELECT count(*) FROM history_events WHERE subject_type='goal' AND subject_id=?", (row["id"],)
        ).fetchone()[0]
        saved = c.execute(
            "SELECT count(*) FROM events WHERE action=? AND target=?", (GR.SAVED, back["message_id"])
        ).fetchone()[0]
    for denied in (
        get(api, "goals/read-back/" + back["message_id"], attempt["token"], expected=403),
        save(api, attempt, step, expected=403),
    ):
        assert denied["error"] == {
            "code": "forbidden",
            "detail": "The Strategy Owner can't see that bot's goals.",
            "retryable": False,
        }
        assert "Private revised words" not in str(denied) and "inbox" not in str(denied)
    with api.app.state.store.read() as c:
        assert dict(G.goal(c, row["id"])) == before
        assert (
            c.execute(
                "SELECT count(*) FROM history_events WHERE subject_type='goal' AND subject_id=?", (row["id"],)
            ).fetchone()[0]
            == events
        )
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action=? AND target=?", (GR.SAVED, back["message_id"])
            ).fetchone()[0]
            == saved
        )
