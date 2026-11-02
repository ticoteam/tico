"""What the Strategy Bot does at level 2, "Suggests actions" (backend/strategy_suggest.py, backend/strategy_merge.py;
spec: Head of Product, Strategy Bot, tests 8, 13, 15 and 19). A red goal gets a recovery the owner approves, applied
exactly and in the Strategy Bot's name; several bots asking the owner the same thing are one item whose answer reaches
each of them; what can't be done comes back saying why; after 10 approvals in a row the Monday note may offer level 3,
once a month."""

from backend import strategy_suggest
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_strategy_bot_level import level
from backend.tests.test_company import bot, with_strategy_bot
from backend.tests import test_goal_asks
from backend.tests.test_goal_asks import asks, needs_tasks, sync
from backend.tests.test_kpis import goal_with_kpi, log

OWNER, STRATEGY_BOT = "human:ana", "bot:goal-manager"


def red_goal(api):
    goal, kpi = goal_with_kpi(api)
    log(api, kpi["id"], 10, period_end=H.now())
    return goal


def bot_task(api, slug, title, status="open"):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:" + slug, title, "Work it.", "bot:" + slug, lint=False)
        if status != "open":
            H.task_update(c, "bot:" + slug, made["id"], status=status)
    return made["id"]


def recovery(api, goal_id, ops_task, token, expected=200):
    payload = {"evidence": "Activation fell from 40% to 10% this week.", "pick": 0, "options": [
        {"label": "Ops fixes setup first", "actions": [
            {"do": "create", "owner": "ops", "title": "Fix the setup step that drops new accounts"},
            {"do": "reorder", "task_id": ops_task}]},
        {"label": "Finance pauses the campaign", "actions": [
            {"do": "create", "owner": "finance", "title": "Pause the signup campaign"}]}]}
    out = post(api, "goal-proposals", {"kind": "recovery", "goal_id": goal_id, "payload": payload,
                                       "reason": "Red for a week"}, token=token, expected=expected)
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
    recovery(api, goal["id"], second, strategy_token, expected=422)                     # level 1: it suggests nothing
    level(api, 2)
    recovery(api, goal["id"], second, bot(api, "ops"), expected=403)        # only the Strategy Bot proposes one
    # A task for a person stays the person's to make, at every level.
    post(api, "goal-proposals", {"kind": "recovery", "goal_id": goal["id"], "payload": {
        "evidence": "x", "options": [{"label": "Ana does it", "actions": [
            {"do": "create", "owner": OWNER, "title": "Fix it yourself"}]}]}}, token=strategy_token, expected=422)
    before = {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")}
    proposal = recovery(api, goal["id"], second, strategy_token)
    sync(api)
    mine = asks(api, OWNER)
    assert [a["kind"] for a in mine if a["kind"] in ("kpi_red", "recovery")] == ["recovery"]   # one item, not two
    [item] = [a for a in mine if a["kind"] == "recovery"]
    assert item["title"] == "Approve a recovery for Raise activation"
    assert "Activation fell" in item["body"]
    assert "Option 1: Ops fixes setup first (the Strategy Bot's pick)" in item["body"]
    assert {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")} == before                      # nothing yet
    assert [i["kind"] for i in get(api, "goals/needs-you")["items"]].count("recovery") == 1
    # Ben may not decide Ana's company goal; Ana approves the pick.
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    decided = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})["proposal"]
    assert decided["status"] == "confirmed" and decided["result"]["unchanged"] is True
    assert decided["result"]["left"] == [] and decided["result"]["follow_up"] is None
    ops = tasks_of(api, "bot:ops")
    [made] = [t for t in ops if t["id"] not in before]
    assert (made["title"], made["requester_id"], made["goal_id"]) == \
        ("Fix the setup step that drops new accounts", STRATEGY_BOT, goal["id"])
    live = sorted((t for t in ops if t["status"] in H.ACTIVE_STATUSES), key=lambda t: t["rank"])
    assert live[0]["id"] == second and first in {t["id"] for t in live}                            # moved to the top
    assert tasks_of(api, "bot:finance") == []                                                      # the other option: no
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


def ask_ana(api, slug, title):
    """A bot's task Ana asked for, where the bot asks her a question back."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, OWNER, title, "Please.", "bot:" + slug)
        H.task_ask(c, "bot:" + slug, made["id"], "Which pricing page goes live first?")
    return made["id"]


def test_three_bots_asking_the_same_thing_are_one_item_and_the_answer_reaches_each(api):
    """Spec test 13: three bots ask the owner the same thing (a question on a task, a task waiting on her, a task filed
    for her); the Strategy Bot merges them and Needs you shows one item, not three. Her one answer reaches each bot that
    asked, and the item is done. A merged item that ends unanswered puts the originals back."""
    strategy_token = with_strategy_bot(api)
    asked = ask_ana(api, "ops", "Ship the pricing page")
    waiting = bot_task(api, "coo", "Plan the launch")
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:coo", waiting, "Which pricing page first?", ask={"who": OWNER})     # `hub task question`
        H.task_update(c, "bot:coo", waiting, status="waiting", waiting_on=OWNER, note="Which pricing page first?")
        filed = H.task_create(c, "bot:finance", "Pick the pricing page to launch", "Which one?", OWNER)["id"]
    originals = [asked, waiting, filed]
    assert set(originals) <= needs_tasks(api, "ana-test")
    body = {"person": OWNER, "title": "Which pricing page goes live first?",
            "question": "Ops, COO and Finance each need to know which pricing page launches first.", "tasks": originals}
    post(api, "strategy-bot/merge", body, token=strategy_token, expected=422)                 # level 1: no merging
    level(api, 2)
    post(api, "strategy-bot/merge", body, token=bot(api, "ops"), expected=403)    # only the Strategy Bot
    merged = post(api, "strategy-bot/merge", body, token=strategy_token)
    item = merged["task"]
    assert (item["owner_id"], item["status"], item["waiting_on"]) == (STRATEGY_BOT, "waiting", OWNER)
    assert [o["task_id"] for o in merged["originals"]] == originals
    shown = needs_tasks(api, "ana-test")
    assert item["id"] in shown and not set(originals) & shown                # one item, not three
    # It names the originals, so the Tasks page, which lists Needs you from its own copy of the tasks, leaves them out.
    page = test_goal_asks.get(api, "needs-you", "ana-test")["items"]
    assert next(i for i in page if i["id"] == item["id"])["merged"] == originals
    # it counts toward the 5 a day, with the vision ask
    assert get(api, "strategy-bot")["asked_today"] == {OWNER: 2}
    # already merged
    post(api, "strategy-bot/merge", {**body, "tasks": originals[:2]}, token=strategy_token, expected=422)
    # Ana answers once, on the merged item.
    post(api, f"tasks/{item['id']}/comments", {"text": "The landlord page first."})
    with api.app.state.store.read() as c:
        assert H.task(c, item["id"])["status"] == "done"
        for task_id, to in ((asked, "bot:ops"), (waiting, "bot:coo")):
            reply = c.execute("SELECT * FROM messages WHERE kind='answer' AND recipient_id=? "
                              "AND json_extract(refs,'$.task')=?", (to, task_id)).fetchone()
            assert reply and "The landlord page first." in reply["body"] and '"quiet"' not in reply["refs"]
            assert not H.open_task_asks(c, H.task(c, task_id))
        said = c.execute("SELECT body FROM messages WHERE sender_id=? AND recipient_id='bot:finance' AND "
                         "json_extract(refs,'$.task')=? ORDER BY created_at DESC", (STRATEGY_BOT, filed)).fetchone()
        assert said and "The landlord page first." in said[0]
        assert H.task(c, waiting)["waiting_on"] is None
        assert {r[0] for r in c.execute("SELECT state FROM "
                                        "strategy_merges WHERE merged_into=?", (item["id"],))} == {"answered"}
    assert not set(originals + [item["id"]]) & needs_tasks(api, "ana-test")
    # Another merge whose item ends unanswered: its originals are back in Needs you.
    again = [ask_ana(api, "ops", "Ship the docs page"), ask_ana(api, "coo", "Ship the help page")]
    second = post(api, "strategy-bot/merge", {**body, "tasks": again}, token=strategy_token)["task"]
    assert not set(again) & needs_tasks(api, "ana-test")
    with api.app.state.store.transaction() as c:
        H.task_update(c, STRATEGY_BOT, second["id"], status="declined", note="Not the same question after all.")
    assert set(again) <= needs_tasks(api, "ana-test")
    # A new question after the answer was passed on is back in her Needs you, and may be merged again.
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:ops", asked, "And which price goes on it?", ask={"who": OWNER})
        H.task_comment(c, "bot:coo", waiting, "Which day do we launch?", ask={"who": OWNER})
        H.task_update(c, "bot:coo", waiting, status="waiting", waiting_on=OWNER, note="Which day do we launch?")
    assert {asked, waiting} <= needs_tasks(api, "ana-test") and filed not in needs_tasks(api, "ana-test")
    post(api, "strategy-bot/merge", {**body, "title": "Price and launch day?", "tasks": [asked, waiting]},
         token=strategy_token)
    # Answered as an open question (the "answer" step in Needs you) rather than a reply: the same pass-on.
    third = post(api, "strategy-bot/merge", {**body, "tasks": again}, token=strategy_token)["task"]
    with api.app.state.store.transaction() as c:
        ask = next(a for a in H.open_task_asks(c, H.task(c, third["id"])) if a["recipient_id"] == OWNER)
        H.answer(c, OWNER, ask["id"], "The help page first.")
    with api.app.state.store.read() as c:
        assert H.task(c, third["id"])["status"] == "done"
        assert not any(H.open_task_asks(c, H.task(c, t)) for t in again)


def test_a_merged_item_names_only_the_originals_its_reader_may_read(api):
    """The merged item's list of originals is the reader's: one Ana can no longer read is left out of it."""
    strategy_token = with_strategy_bot(api)
    asked = ask_ana(api, "ops", "Ship the pricing page")
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        filed = H.task_create(c, "bot:finance", "Pick the pricing page to launch", "Which one?", OWNER)["id"]
    level(api, 2)
    item = post(api, "strategy-bot/merge",
                {"person": OWNER, "title": "Which pricing page first?", "question": "Which one?",
                 "tasks": [asked, filed]}, token=strategy_token)["task"]
    with api.app.state.store.transaction() as c:      # Ops' task becomes its own private task, out of Ana's reach
        c.execute("UPDATE tasks SET private=1, requester_id='bot:ops', guest=NULL WHERE id=?", (asked,))
    page = test_goal_asks.get(api, "needs-you", "ana-test")["items"]
    assert next(i for i in page if i["id"] == item["id"])["merged"] == [filed]


def test_a_merged_answer_never_settles_a_question_asked_after_the_merge(api):
    """Ana answers one original where it was asked, and that bot asks her something else; her answer on the merged
    item reaches the other bot only, and the new question stays open in her Needs you."""
    strategy_token = with_strategy_bot(api)
    level(api, 2)
    first, second = ask_ana(api, "ops", "Ship the pricing page"), ask_ana(api, "coo", "Plan the launch")
    item = post(api, "strategy-bot/merge",
                {"person": OWNER, "title": "Which pricing page first?", "question": "Which one?",
                 "tasks": [first, second]}, token=strategy_token)["task"]
    with api.app.state.store.transaction() as c:
        [old] = H.open_task_asks(c, H.task(c, first))
        H.answer(c, OWNER, old["id"], "The landlord page.")
        H.task_comment(c, "bot:ops", first, "Which price goes on it?", ask={"who": OWNER})
    assert first in needs_tasks(api, "ana-test") and second not in needs_tasks(api, "ana-test")
    post(api, f"tasks/{item['id']}/comments", {"text": "The tenant page first."})
    with api.app.state.store.read() as c:
        [new] = H.open_task_asks(c, H.task(c, first))
        assert new["body"] == "Which price goes on it?" and not H.open_task_asks(c, H.task(c, second))
        states = dict(c.execute("SELECT original, state FROM "
                                "strategy_merges WHERE merged_into=?", (item["id"],)).fetchall())
    assert states == {first: "released", second: "answered"}
    assert first in needs_tasks(api, "ana-test")


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
            assert get(api, "strategy-bot", token=strategy_token)["level3_offer"]["due_at"] is False
            post(api, "strategy-bot/level3-offer", {}, token=strategy_token, expected=422)            # not due yet
    offer = get(api, "strategy-bot", token=strategy_token)["level3_offer"]
    assert (offer["streak"], offer["due_at"]) == (10, True)
    post(api, "strategy-bot/level3-offer", {}, token="ana-test", expected=403)          # the Strategy Bot's note only
    made = post(api, "strategy-bot/level3-offer", {}, token=strategy_token)["level3_offer"]
    assert made["due_at"] is False and made["last_offered_at"]
    # not again this month
    post(api, "strategy-bot/level3-offer", {}, token=strategy_token, expected=422)
    # never raises its own level
    level(api, 3, token=strategy_token, expected=403)
    with api.app.state.store.transaction() as c:                                   # a month on, it may again
        record = strategy_suggest.strategy_bot._record(c)
        record["level3_offers"] = [H.shift(H.now(), days=-31)]
        strategy_suggest.strategy_bot._save(c, record)
    assert get(api, "strategy-bot", token=strategy_token)["level3_offer"]["due_at"] is True
    assert get(api, "strategy-bot")["level"] == 2
