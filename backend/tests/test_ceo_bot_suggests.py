"""What the CEO Bot does at level 2, "Suggests actions" (backend/ceo_suggest.py, backend/ceo_merge.py; spec: Head of
Product, CEO Bot, tests 8, 13, 15 and 19). A red goal gets a recovery the owner approves, applied exactly and in the
CEO Bot's name; several bots asking the owner the same thing are one item whose answer reaches each of them; what
can't be done comes back saying why; after 10 approvals in a row the Monday note may offer level 3, once a month."""

from backend import ceo_suggest
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_ceo_bot_level import level
from backend.tests.test_company import bot, switched_on
from backend.tests.test_goal_asks import asks, needs_tasks, sync
from backend.tests.test_kpis import goal_with_kpi, log

OWNER, CEO = "human:ana", "bot:goal-manager"


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
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE owner=? ORDER BY created", (owner,))]


def test_at_level_2_a_red_goal_gets_one_recovery_the_owner_approves_exactly(api):
    """Spec test 8, level 2 half: a red goal is one proposal with options in Needs you, no task changes until the owner
    approves, and approving applies exactly the option chosen, as the CEO Bot. Level 1 makes no proposal (refused), and
    nobody but the CEO Bot proposes one."""
    ceo = switched_on(api)
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    goal = red_goal(api)
    first = bot_task(api, "ops", "Write the onboarding email")
    second = bot_task(api, "ops", "Clean up old reports")
    recovery(api, goal["id"], second, ceo, expected=422)                     # level 1: it suggests nothing
    level(api, 2)
    recovery(api, goal["id"], second, bot(api, "ops"), expected=403)        # only the CEO Bot proposes one
    # A task for a person stays the person's to make, at every level.
    post(api, "goal-proposals", {"kind": "recovery", "goal_id": goal["id"], "payload": {
        "evidence": "x", "options": [{"label": "Ana does it", "actions": [
            {"do": "create", "owner": OWNER, "title": "Fix it yourself"}]}]}}, token=ceo, expected=422)
    before = {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")}
    proposal = recovery(api, goal["id"], second, ceo)
    sync(api)
    mine = asks(api, OWNER)
    assert [a["kind"] for a in mine if a["kind"] in ("kpi_red", "recovery")] == ["recovery"]   # one item, not two
    [item] = [a for a in mine if a["kind"] == "recovery"]
    assert item["title"] == "Approve a recovery for Raise activation"
    assert "Activation fell" in item["body"] and "Option 1: Ops fixes setup first (the CEO Bot's pick)" in item["body"]
    assert {t["id"]: t["rank"] for t in tasks_of(api, "bot:ops")} == before                      # nothing yet
    assert [i["kind"] for i in get(api, "goals/needs-you")["items"]].count("recovery") == 1
    # Ben may not decide Ana's company goal; Ana approves the pick.
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    decided = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})["proposal"]
    assert decided["status"] == "confirmed" and decided["result"]["unchanged"] is True
    assert decided["result"]["left"] == [] and decided["result"]["follow_up"] is None
    ops = tasks_of(api, "bot:ops")
    [made] = [t for t in ops if t["id"] not in before]
    assert (made["title"], made["requester"], made["goal_id"]) == \
        ("Fix the setup step that drops new accounts", CEO, goal["id"])
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
    ceo = switched_on(api)
    level(api, 2)
    goal = red_goal(api)
    proposal = recovery(api, goal["id"], bot_task(api, "ops", "Clean up old reports"), ceo)
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


def ask_ana(api, slug, title):
    """A bot's task Ana asked for, where the bot asks her a question back."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, OWNER, title, "Please.", "bot:" + slug)
        H.task_ask(c, "bot:" + slug, made["id"], "Which pricing page goes live first?")
    return made["id"]


def test_three_bots_asking_the_same_thing_are_one_item_and_the_answer_reaches_each(api):
    """Spec test 13: three bots ask the owner the same thing (a question on a task, a task waiting on her, a task filed
    for her); the CEO Bot merges them and Needs you shows one item, not three. Her one answer reaches each bot that
    asked, and the item is done. A merged item that ends unanswered puts the originals back."""
    ceo = switched_on(api)
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
    post(api, "ceo-bot/merge", body, token=ceo, expected=422)                 # level 1: no merging
    level(api, 2)
    post(api, "ceo-bot/merge", body, token=bot(api, "ops"), expected=403)    # only the CEO Bot
    merged = post(api, "ceo-bot/merge", body, token=ceo)
    item = merged["task"]
    assert (item["owner"], item["status"], item["waiting_on"]) == (CEO, "waiting", OWNER)
    assert [o["task_id"] for o in merged["originals"]] == originals
    shown = needs_tasks(api, "ana-test")
    assert item["id"] in shown and not set(originals) & shown                # one item, not three
    assert get(api, "ceo-bot")["asked_today"] == {OWNER: 2}       # it counts toward the 5 a day, with the vision ask
    post(api, "ceo-bot/merge", {**body, "tasks": originals[:2]}, token=ceo, expected=422)      # already merged
    # Ana answers once, on the merged item.
    post(api, f"tasks/{item['id']}/comments", {"text": "The landlord page first."})
    with api.app.state.store.read() as c:
        assert H.task(c, item["id"])["status"] == "done"
        for task_id, to in ((asked, "bot:ops"), (waiting, "bot:coo")):
            reply = c.execute("SELECT * FROM messages WHERE kind='answer' AND to_actor=? "
                              "AND json_extract(refs_json,'$.task')=?", (to, task_id)).fetchone()
            assert reply and "The landlord page first." in reply["body"] and '"quiet"' not in reply["refs_json"]
            assert not H.open_task_asks(c, H.task(c, task_id))
        said = c.execute("SELECT body FROM messages WHERE from_actor=? AND to_actor='bot:finance' AND "
                         "json_extract(refs_json,'$.task')=? ORDER BY created DESC", (CEO, filed)).fetchone()
        assert said and "The landlord page first." in said[0]
        assert H.task(c, waiting)["waiting_on"] is None
        assert {r[0] for r in c.execute("SELECT state FROM ceo_merges WHERE merged_into=?", (item["id"],))} == {"answered"}
    assert not set(originals + [item["id"]]) & needs_tasks(api, "ana-test")
    # Another merge whose item ends unanswered: its originals are back in Needs you.
    again = [ask_ana(api, "ops", "Ship the docs page"), ask_ana(api, "coo", "Ship the help page")]
    second = post(api, "ceo-bot/merge", {**body, "tasks": again}, token=ceo)["task"]
    assert not set(again) & needs_tasks(api, "ana-test")
    with api.app.state.store.transaction() as c:
        H.task_update(c, CEO, second["id"], status="declined", note="Not the same question after all.")
    assert set(again) <= needs_tasks(api, "ana-test")
    # Answered as an open question (the "answer" step in Needs you) rather than a reply: the same pass-on.
    third = post(api, "ceo-bot/merge", {**body, "tasks": again}, token=ceo)["task"]
    with api.app.state.store.transaction() as c:
        ask = next(a for a in H.open_task_asks(c, H.task(c, third["id"])) if a["to_actor"] == OWNER)
        H.answer(c, OWNER, ask["id"], "The help page first.")
    with api.app.state.store.read() as c:
        assert H.task(c, third["id"])["status"] == "done"
        assert not any(H.open_task_asks(c, H.task(c, t)) for t in again)


def test_ten_approvals_in_a_row_offer_level_3_once_a_month(api):
    """Spec test 19: at level 2, after 10 recoveries approved in a row with its pick unchanged, the Monday note may
    offer level 3: the CEO Bot records the offer, which is refused before it is due and again within 30 days. It
    never raises its own level, and a rejection starts the count again."""
    ceo = switched_on(api)
    level(api, 2)
    goal = red_goal(api)
    for n in range(13):
        p = recovery(api, goal["id"], bot_task(api, "ops", f"Task {n}"), ceo)
        post(api, f"goal-proposals/{p['id']}/decide", {"decision": "reject" if n == 2 else "confirm"})
        if n == 2:
            assert get(api, "ceo-bot")["level3_offer"]["streak"] == 0
        if n == 11:
            assert get(api, "ceo-bot", token=ceo)["level3_offer"]["due"] is False
            post(api, "ceo-bot/level3-offer", {}, token=ceo, expected=422)            # not due yet
    offer = get(api, "ceo-bot", token=ceo)["level3_offer"]
    assert (offer["streak"], offer["due"]) == (10, True)
    post(api, "ceo-bot/level3-offer", {}, token="ana-test", expected=403)          # the CEO Bot's note only
    made = post(api, "ceo-bot/level3-offer", {}, token=ceo)["level3_offer"]
    assert made["due"] is False and made["last_offered_at"]
    post(api, "ceo-bot/level3-offer", {}, token=ceo, expected=422)                 # not again this month
    level(api, 3, token=ceo, expected=403)                                         # never raises its own level
    with api.app.state.store.transaction() as c:                                   # a month on, it may again
        record = ceo_suggest.ceo_bot._record(c)
        record["level3_offers"] = [H.shift(H.now(), days=-31)]
        ceo_suggest.ceo_bot._save(c, record)
    assert get(api, "ceo-bot", token=ceo)["level3_offer"]["due"] is True
    assert get(api, "ceo-bot")["level"] == 2
