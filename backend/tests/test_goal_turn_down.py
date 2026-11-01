"""The owner's "no" in their Assistant chat to a goal suggestion (backend/goal_routes.py `turned-down`; spec
chat-first-goals.md, criterion 7): no read-back and no second yes. Tico stores it at once as a turned-down suggestion on
the goal, with the owner's words as its reason, and changes nothing else."""
from backend import goals as G
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_assistant import asked, turn_message
from backend.tests.test_goal_read_back import goal
from backend.tests.test_strategy_bot_chat import strategy_bot, turn


def suggestion(goal_id, **extra):
    """A number to aim for, recommended once in chat: a measure with a target on the goal."""
    return {"kind": "goal_kpi", "goal_id": goal_id,
            "payload": {"kpi": {"name": "Companies that tried Tico"},
                        "target": {"kind": "improve", "target": 100, "deadline": "2026-11-28"}},
            "reason": "A number to aim for, like 100 companies by Nov 28.", **extra}


def turn_down(api, attempt, body, expected=200):
    return asked(api, "goal-proposals/turned-down", body, attempt["token"], "turn", expected=expected)


def proposals(api, status="all"):
    return get(api, f"goal-proposals?status={status}")["proposals"]


def test_a_no_in_chat_is_stored_as_turned_down_with_the_owners_words_and_changes_nothing_else(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No, leave it without a number.")
    out = turn_down(api, attempt, suggestion(g["id"]))["proposal"]
    assert out["status"] == "rejected"
    assert out["decision_note"] == "No, leave it without a number."
    assert out["decided_by"] == "human:ana"
    # Stored on the goal: the next pass and the next chat read it from the rejected suggestions.
    stored = get(api, f"goal-proposals?status=rejected&goal_id={g['id']}")["proposals"]
    assert [p["id"] for p in stored] == [out["id"]]
    # Nothing else moved: no measure, no target, the goal as it was.
    assert get(api, "goals/" + g["id"])["goal"]["title"] == "Get 100 companies to try Tico"
    assert not proposals(api, "pending")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM kpis").fetchone()[0] == 0


def test_a_bare_no_is_stored_with_no_reason(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No.")
    out = turn_down(api, attempt, suggestion(g["id"]))["proposal"]
    assert (out["status"], out["decision_note"]) == ("rejected", "")


def test_a_reply_that_is_not_a_no_records_nothing(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "Yes, but make it 50")
    refused = turn_down(api, attempt, suggestion(g["id"]), expected=403)["error"]
    assert refused["code"] == "not_a_no"
    assert proposals(api) == []


def test_another_persons_assistant_cannot_turn_the_owners_suggestion_down(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ben-test", "No, leave it.")
    refused = turn_down(api, attempt, suggestion(g["id"]), expected=403)["error"]
    assert refused["code"] == "forbidden"
    assert proposals(api) == []


def test_without_the_owners_cited_turn_the_write_is_a_card_as_before(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No, leave it.")
    refused = api.post("/api/v2/goal-proposals/turned-down", json=suggestion(g["id"]),
                       headers={"Authorization": f"Bearer {attempt['token']}"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "confirm_required"
    assert proposals(api) == []


def test_a_no_cited_from_more_than_thirty_minutes_ago_is_refused(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No, leave it.")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created=? WHERE id=?",
                  (H.shift(H.now(), seconds=-31 * 60), turn_message(api, attempt)))
    refused = turn_down(api, attempt, suggestion(g["id"]), expected=403)["error"]
    assert refused["code"] == "on_behalf_of"
    assert proposals(api) == []


def test_a_why_is_not_turned_down_in_chat_so_a_waiting_one_is_left_alone(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No.")
    refused = turn_down(api, attempt, {"kind": "goal_why", "goal_id": g["id"], "payload": {"why": "Vision line."},
                                       "reason": "How it serves the vision."}, expected=403)["error"]
    assert refused["code"] == "forbidden"
    assert proposals(api) == []


def test_a_kind_that_is_not_a_goal_suggestion_is_refused(api):
    strategy_bot(api)
    g = goal(api, "Get 100 companies to try Tico", owner="ops")
    r, attempt = turn(api, "coo", "ana-test", "No.")
    refused = turn_down(api, attempt, {**suggestion(g["id"]), "kind": "strategy_draft"}, expected=403)["error"]
    assert refused["code"] == "forbidden"
    assert proposals(api) == []
