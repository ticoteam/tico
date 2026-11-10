"""What the Strategy Bot does at level 2, "Suggests actions" (backend/strategy_suggest.py; spec: Head of Product,
Strategy Bot, tests 8, 15 and 19). A red goal gets a recovery the owner approves, applied exactly and in the Strategy
Bot's name; what can't be done comes back saying why; after 10 approvals in a row the Monday note may offer level 3,
once a month."""

from backend import strategy_suggest
from backend.store import H
from backend.tests.helpers.api import get, post  # noqa: F401
from backend.tests.helpers.strategy_bot_level import level
from backend.tests.helpers.company import bot, with_strategy_bot
from backend.tests.helpers.goal_asks import asks, sync
from backend.tests.helpers.kpis import goal_with_kpi, log
from backend.tests.helpers.strategy_bot_suggests import OWNER, STRATEGY_BOT, bot_task


def red_goal(api):
    goal, kpi = goal_with_kpi(api)
    log(api, kpi["id"], 10, period_end=H.now())
    return goal


def recovery(api, goal_id, ops_task, token, expected=200):
    payload = {
        "evidence": "Activation fell from 40% to 10% this week.",
        "pick": 0,
        "options": [
            {
                "label": "Ops fixes setup first",
                "actions": [
                    {"do": "create", "owner": "ops", "title": "Fix the setup step that drops new accounts"},
                    {"do": "reorder", "task_id": ops_task},
                ],
            },
            {
                "label": "Finance pauses the campaign",
                "actions": [{"do": "create", "owner": "finance", "title": "Pause the signup campaign"}],
            },
        ],
    }
    out = post(
        api,
        "goal-proposals",
        {"kind": "recovery", "goal_id": goal_id, "payload": payload, "reason": "Red for a week"},
        token=token,
        expected=expected,
    )
    return out.get("proposal")


def tasks_of(api, owner):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE owner_id=? ORDER BY created_at", (owner,))]


def test_at_level_2_a_red_goal_gets_one_recovery_the_owner_approves_exactly(api):
    """Spec test 8, level 2 half: a red goal is one proposal with options in Needs you, no task changes until the owner
    approves, and approving applies exactly the option chosen, as the Strategy Bot. Level 1 makes no proposal (refused),
    and nobody but the Strategy Bot proposes one."""
    strategy_token = with_strategy_bot(api)
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    goal = red_goal(api)
    first = bot_task(api, "ops", "Write the onboarding email")
    second = bot_task(api, "ops", "Clean up old reports")
    recovery(api, goal["id"], second, strategy_token, expected=422)  # level 1: it suggests nothing
    level(api, 2)
    recovery(api, goal["id"], second, bot(api, "ops"), expected=403)  # only the Strategy Bot proposes one
    # A task for a person stays the person's to make, at every level.
    post(
        api,
        "goal-proposals",
        {
            "kind": "recovery",
            "goal_id": goal["id"],
            "payload": {
                "evidence": "x",
                "options": [
                    {"label": "Ana does it", "actions": [{"do": "create", "owner": OWNER, "title": "Fix it yourself"}]}
                ],
            },
        },
        token=strategy_token,
        expected=422,
    )
    before = {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")}
    proposal = recovery(api, goal["id"], second, strategy_token)
    sync(api)
    mine = asks(api, OWNER)
    assert [a["kind"] for a in mine if a["kind"] in ("kpi_red", "recovery")] == ["recovery"]  # one item, not two
    [item] = [a for a in mine if a["kind"] == "recovery"]
    assert item["title"] == "Approve a recovery for Raise activation"
    assert "Activation fell" in item["body"]
    assert "Option 1: Ops fixes setup first (the Strategy Bot's pick)" in item["body"]
    assert {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")} == before  # nothing yet
    assert [i["kind"] for i in get(api, "goals/needs-you")["items"]].count("recovery") == 1
    # Ben may not decide Ana's company goal; Ana approves the pick.
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    decided = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})["proposal"]
    assert decided["status"] == "confirmed" and decided["result"]["unchanged"] is True
    assert decided["result"]["left"] == [] and decided["result"]["follow_up"] is None
    ops = tasks_of(api, "bot:ops")
    [made] = [t for t in ops if t["id"] not in before]
    assert (made["title"], made["requester_id"], made["goal_id"]) == (
        "Fix the setup step that drops new accounts",
        STRATEGY_BOT,
        goal["id"],
    )
    live = sorted((t for t in ops if t["status"] in H.ACTIVE_STATUSES), key=lambda t: t["rank"])
    assert live[0]["id"] == second and first in {t["id"] for t in live}  # moved to the top
    assert tasks_of(api, "bot:finance") == []  # the other option: no
    with api.app.state.store.read() as c:
        closed = H.task(c, item["id"])
    assert closed["status"] == "closed" and "Done: New task for ops" in closed["note"]


def test_what_cant_be_done_comes_back_saying_why_and_lowering_to_1_withdraws(api):
    """Spec test 15, proposal side: a bot that is paused when the owner approves gets nothing, and that part comes back
    as a new proposal that says why in plain words; nothing is dropped. Choosing another option is not "unchanged".
    Lowering the level to 1 withdraws what still waits."""
    strategy_token = with_strategy_bot(api)
    level(api, 2)
    goal = red_goal(api)
    proposal = recovery(api, goal["id"], bot_task(api, "ops", "Clean up old reports"), strategy_token)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
    decided = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm", "option": 1})["proposal"]
    result = decided["result"]
    assert result["unchanged"] is False and result["done"] == []
    assert result["left"][0]["why"] == "finance is paused right now" and result["follow_up"]
    assert tasks_of(api, "bot:finance") == []
    again = get(api, "goal-proposals")["proposals"]
    [back] = [p for p in again if p["id"] == result["follow_up"]]
    assert back["kind"] == "recovery" and "finance is paused right now" in back["reason"]
    sync(api)
    [item] = [a for a in asks(api, OWNER) if a["kind"] == "recovery" and a["status"] == "open"]
    assert "finance is paused right now" in item["body"]
    level(api, 1)
    assert [p for p in get(api, "goal-proposals")["proposals"] if p["kind"] == "recovery"] == []
    with api.app.state.store.read() as c:
        assert H.task(c, item["id"])["status"] == "closed"


def test_a_retry_approved_while_still_blocked_comes_back_again_and_then_applies(api):
    """Approving the retry while the bot is still paused files a new retry, not the one just decided; once the bot is
    back, approving that one makes the task."""
    strategy_token = with_strategy_bot(api)
    level(api, 2)
    proposal = recovery(api, red_goal(api)["id"], bot_task(api, "ops", "Clean up old reports"), strategy_token)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
    first = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm", "option": 1})["proposal"]
    second = post(api, f"goal-proposals/{first['result']['follow_up']}/decide", {"decision": "confirm"})["proposal"]
    retry = second["result"]["follow_up"]
    assert retry and retry != second["id"]
    assert [p["id"] for p in get(api, "goal-proposals")["proposals"] if p["kind"] == "recovery"] == [retry]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='finance'")
    done = post(api, f"goal-proposals/{retry}/decide", {"decision": "confirm"})["proposal"]["result"]
    assert done["left"] == [] and done["follow_up"] is None
    assert [t["title"] for t in tasks_of(api, "bot:finance")] == ["Pause the signup campaign"]


def test_ten_approvals_in_a_row_offer_level_3_once_a_month(api):
    """Spec test 19: at level 2, after 10 recoveries approved in a row with its pick unchanged, the Monday note may
    offer level 3: the Strategy Bot records the offer, which is refused before it is due and again within 30 days. It
    never raises its own level, and a rejection starts the count again."""
    strategy_token = with_strategy_bot(api)
    level(api, 2)
    goal = red_goal(api)
    for n in range(13):
        p = recovery(api, goal["id"], bot_task(api, "ops", f"Task {n}"), strategy_token)
        post(api, f"goal-proposals/{p['id']}/decide", {"decision": "reject" if n == 2 else "confirm"})
        if n == 2:
            assert get(api, "strategy-bot")["level3_offer"]["streak"] == 0
        if n == 11:
            assert get(api, "strategy-bot", token=strategy_token)["level3_offer"]["due"] is False
            post(api, "strategy-bot/level3-offer", {}, token=strategy_token, expected=422)  # not due yet
    offer = get(api, "strategy-bot", token=strategy_token)["level3_offer"]
    assert (offer["streak"], offer["due"]) == (10, True)
    post(api, "strategy-bot/level3-offer", {}, token="ana-test", expected=403)  # the Strategy Bot's note only
    made = post(api, "strategy-bot/level3-offer", {}, token=strategy_token)["level3_offer"]
    assert made["due"] is False and made["last_offered_at"]
    # not again this month
    post(api, "strategy-bot/level3-offer", {}, token=strategy_token, expected=422)
    # never raises its own level
    level(api, 3, token=strategy_token, expected=403)
    with api.app.state.store.transaction() as c:  # a month on, it may again
        record = strategy_suggest.strategy_bot._record(c)
        record["level3_offers"] = [H.shift(H.now(), days=-31)]
        strategy_suggest.strategy_bot._save(c, record)
    assert get(api, "strategy-bot", token=strategy_token)["level3_offer"]["due"] is True
    assert get(api, "strategy-bot")["level"] == 2
