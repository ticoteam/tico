"""Needs you cards say what they approve, lead with what is due, carry the group they belong in and the bot's
recommendation, show one card per blocker, and the list says how many items went away on their own this week."""

import json

from backend import batch, views
from backend import hubdb as H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_batch import approval_for_ana
from backend.tests.test_task_privacy_reads import traced

SEND = {"to": ["Jennie Ooten <jennie@acme.example>"], "cc": [], "subject": "$200 for each rental owner you introduce",
        "body_sha256": "a" * 64, "mailbox": "work"}


def test_cards_are_titled_dated_grouped_and_one_per_blocker(api):
    _, _, finance = setup_attempt(api, "finance")
    deal = post(api, "tasks", {"title": "Referral deal for rental owners", "body": "x", "owner": "bot:finance"})
    send = approval_for_ana(api, kind="send", payload=SEND, task=deal["id"], token=finance["token"])
    merge = approval_for_ana(api, kind="merge", payload={"repo": "acme/site", "pr": 12}, task=deal["id"],
                             token=finance["token"])
    keys = post(api, "tasks", {"title": "Add the new OpenAI keys", "body": "Rotate them", "owner": "human:ana",
                               "due": H.shift(H.now(), days=5)}, token=finance["token"])
    murphy = post(api, "tasks", {"title": "Call Murphy back", "body": "About the lease", "owner": "human:ana",
                                 "due": H.shift(H.now(), days=2)}, token=finance["token"])
    offsite = post(api, "tasks", {"title": "Plan next year's offsite", "body": "x", "owner": "human:ana",
                                  "due": H.shift(H.now(), days=60)}, token=finance["token"])
    # A bot's task waiting on Ana, its BotOps child waiting on her too (parked on her directly, as before a wait
    # became a subtask), and the subtask that asks her: one card.
    vendor = post(api, "tasks", {"title": "Switch the payroll vendor", "body": "x", "owner": "bot:finance"})
    with api.app.state.store.transaction() as c:
        fix = H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "bot:ops",
                            parent_id=vendor["id"])
        pick = H.task_create(c, "bot:finance", "Gusto or Rippling for payroll?", "Should payroll move to Gusto or Rippling?",
                             "human:ana", parent_id=vendor["id"])
        c.executemany("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?",
                      [("Waiting on Ana to pick the vendor.", vendor["id"]), ("Same pick.", fix["id"])])
        gusto = H.say(c, "bot:finance", "human:ana", "Should payroll move to Gusto or Rippling?", kind="ask",
              conversation_id=H.task(c, pick["id"])["conversation_id"],
              refs={"task": pick["id"], "if_no_answer": {"do": "Gusto", "kind": "reversible"},
                    "questions": [{"id": "q", "question": "Which?", "multi": False, "other": False,
                                   "options": [{"label": "Gusto", "then": "Go with Gusto"}, {"label": "Rippling", "then": "Go with Rippling"}]}]})
    page = get(api, "needs-you")
    items = {i["id"]: i for i in page["items"]}

    assert items[send["id"]]["title"] == "Send: '$200 for each rental owner you introduce' to Jennie Ooten"
    assert items[merge["id"]]["title"] == "Merge: 'Referral deal for rental owners' (acme/site #12)"
    assert (items[send["id"]]["need"], items[merge["id"]]["need"], items[keys["id"]]["need"]) == ("review", "review",
                                                                                                    "connect")
    # Due within the week first, soonest first; a date two months off does not jump the approvals.
    order = [i["id"] for i in page["items"]]
    assert order[:2] == [murphy["id"], keys["id"]] and order.index(offsite["id"]) > order.index(send["id"])

    card = items[pick["id"]]
    assert card["need"] == "decide" and card["recommend"]["do"] == "Gusto" and card["recommend"]["option"] == "Gusto"
    assert vendor["id"] not in items and fix["id"] not in items
    assert {f["id"] for f in card["folded"]} == {vendor["id"], fix["id"]}
    # The walk-through and brief say the folded waits with the card.
    assert {f["title"] for f in batch._row(card, "human:ana")["folded"]} == {"Switch the payroll vendor",
                                                                              "Give finance the payroll connector"}
    assert get(api, "needs-you?count=1")["count"] == len(page["items"])

    # Two cards asking Ana different things under one wait: the wait folds into neither.
    with api.app.state.store.transaction() as c:
        sign = H.task_create(c, "bot:finance", "Sign the payroll contract", "Can you sign the payroll contract today?",
                             "human:ana", parent_id=vendor["id"])
    assert {pick["id"], vendor["id"], fix["id"]} <= {i["id"] for i in get(api, "needs-you")["items"]}
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", sign["id"], status="done", note="Signed")

    assert page["went_away"]["total"] == 0
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "ask.expired", "m1", {"to": "human:ana", "bot": "bot:finance"})
        H.event(c, H.KEEPER, "ask.went_ahead", "m2", {"to": "human:ben", "bot": "bot:finance"})
    assert get(api, "needs-you")["went_away"]["expired"] == 1

    # Taking the recommendation picks the option it names, as a tap on it would, and finishes Ana's card, which only
    # asked it. Finance's own wait above it, the same question, stops waiting on her; BotOps's wait is BotOps's to clear.
    pick_version = get(api, f"tasks/{pick['id']}")["task"]["version"]
    post(api, "needs-you/recommended", {"ask": gusto["id"], "version": pick_version + 1}, expected=409)
    assert post(api, "needs-you/recommended", {"ask": gusto["id"], "version": pick_version})["done"] is True
    assert get(api, f"tasks/{pick['id']}")["task"]["status"] == "done"
    assert get(api, f"tasks/{pick['id']}/answers")["answers"][0]["answers"] == {"q": ["Gusto"]}
    assert not get(api, f"tasks/{vendor['id']}")["task"]["waiting_on"]
    assert get(api, f"tasks/{fix['id']}")["task"]["waiting_on"] == "human:ana"


def test_do_what_you_recommend_answers_and_closes_the_card(api):
    _, _, finance = setup_attempt(api, "finance")
    tool = post(api, "tasks", {"title": "Pick the outbound tool", "body": "x", "owner": "bot:finance"})
    with api.app.state.store.transaction() as c:
        conv = H.task(c, tool["id"])["conversation_id"]
        plain = H.say(c, "bot:finance", "human:ana", "Which day suits?", kind="ask", conversation_id=conv,
                      refs={"task": tool["id"]})
        asked = H.say(c, "bot:finance", "human:ana", "Shall I go with Apollo?", kind="ask", conversation_id=conv,
                      refs={"task": tool["id"], "if_no_answer": {"do": "Go with Apollo", "kind": "reversible"}})
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana' WHERE id=?", (tool["id"],))
    card = next(i for i in get(api, "needs-you")["items"] if i["id"] == tool["id"])
    assert card["recommend"]["do"] == "Go with Apollo" and card["need"] == "decide"
    post(api, "needs-you/recommended", {"ask": plain["id"]}, expected=409)        # no recommendation to take
    post(api, "needs-you/recommended", {"ask": asked["id"]}, token="ben-test", expected=403)
    post(api, "needs-you/recommended", {"ask": asked["id"]})
    with api.app.state.store.read() as c:
        said = c.execute("SELECT body FROM messages WHERE in_reply_to=? AND kind='answer'", (asked["id"],)).fetchone()
        assert said["body"] == "Yes, do what you recommend: Go with Apollo"
        assert not H.task(c, tool["id"])["waiting_on"]
    post(api, "needs-you/recommended", {"ask": asked["id"]}, expected=409)        # once


def test_do_what_you_recommend_answers_a_question_on_a_file_version(api):
    _, _, finance = setup_attempt(api, "finance")
    plan = post(api, "tasks", {"title": "Review the payroll plan", "body": "x", "owner": "bot:finance"})
    question = {"questions": [{"id": "verdict", "header": "Review", "question": "Ship it?", "other": False,
                               "options": [{"label": "Approve", "then": "Go with Approve"}, {"label": "Request changes", "then": "Go with Request changes"}]}], "who": "ana"}
    made = post(api, f"tasks/{plan['id']}/files", {"name": "plan.md", "text": "# Plan", "ask": question},
                token=finance["token"])
    with api.app.state.store.transaction() as c:
        ask_id = c.execute("SELECT ask_message_id FROM task_file_reviews WHERE file_id=?", (made["file_id"],)).fetchone()[0]
        asked = H.message(c, ask_id)
        c.execute("UPDATE messages SET refs=?, to_actor='human:ana' WHERE id=?",
                  (json.dumps({**asked["refs"], "if_no_answer": {"do": "Approve", "kind": "reversible"}}), ask_id))
    post(api, "needs-you/recommended", {"ask": ask_id})
    assert get(api, f"tasks/{plan['id']}/answers")["answers"][0]["answers"] == {"verdict": ["Approve"]}


def test_a_side_question_on_a_persons_task_is_answered_not_finished(api):
    _, _, finance = setup_attempt(api, "finance")
    deck = post(api, "tasks", {"title": "Prepare the board deck", "body": "For Thursday's board meeting",
                               "owner": "human:ana"}, token=finance["token"])
    with api.app.state.store.transaction() as c:
        asked = H.say(c, "bot:finance", "human:ana", "Shall I pull the Q3 numbers from Stripe for you?", kind="ask",
                      conversation_id=H.task(c, deck["id"])["conversation_id"],
                      refs={"task": deck["id"], "if_no_answer": {"do": "Pull them from Stripe", "kind": "reversible"}})
    assert post(api, "needs-you/recommended", {"ask": asked["id"]})["done"] is False
    task = get(api, f"tasks/{deck['id']}")["task"]
    assert task["status"] == "open" and deck["id"] in {i["id"] for i in get(api, "needs-you")["items"]}


def test_waits_under_one_project_without_a_card_above_them_each_keep_their_card(api):
    _, _, finance = setup_attempt(api, "finance")
    launch = post(api, "tasks", {"title": "Q4 launch", "body": "x", "owner": "bot:finance"})
    with api.app.state.store.transaction() as c:
        stripe = H.task_create(c, "bot:finance", "Pay the Stripe invoice", "x", "bot:finance", parent_id=launch["id"])
        venue = H.task_create(c, "bot:finance", "Book the venue", "x", "bot:finance", parent_id=launch["id"])
        which = H.task_create(c, "bot:finance", "Which venue?", "Which venue should we book?", "human:ana",
                              parent_id=launch["id"])
        c.executemany("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?", [
            ("Waiting on Ana to approve paying the $4,000 Stripe invoice.", stripe["id"]),
            ("Waiting on Ana to sign in to the venue site so I can book it.", venue["id"])])
    items = {i["id"]: i for i in get(api, "needs-you")["items"]}
    assert {stripe["id"], venue["id"], which["id"]} <= set(items)
    assert not items[which["id"]].get("folded")
    assert (items[stripe["id"]]["need"], items[venue["id"]]["need"]) == ("pay", "connect")


def test_what_it_takes_reads_money_and_sign_ins_not_every_mention():
    need = lambda title, **kw: views.need_of({"kind": "task", "title": title, **kw})  # noqa: E731
    for title in ("Ask Bill about the launch date", "Who's in charge of the onboarding?", "Spend an hour on the roadmap",
                  "Get buy-in from sales", "Connect with Jennie about the partnership", "Review the token budget"):
        assert need(title) not in ("pay", "connect"), title
    assert need("Which is the key message for the launch?", kind="question") == "decide"
    assert need("Spend $400 on the launch ads") == need("Pay the October invoice") == need("Renew the Notion plan") == "pay"
    assert need("Add the new OpenAI API key") == need("Sign in to the venue site") == "connect"


def test_a_question_asked_again_did_not_go_away(api):
    _, _, finance = setup_attempt(api, "finance")
    tool = post(api, "tasks", {"title": "Pick the outbound tool", "body": "x", "owner": "bot:finance"})
    with api.app.state.store.transaction() as c:
        conv = H.task(c, tool["id"])["conversation_id"]
        H.say(c, "bot:finance", "human:ana", "Apollo or Clay?", kind="ask", conversation_id=conv, refs={"task": tool["id"]})
        newer = H.say(c, "bot:finance", "human:ana", "Apollo, Clay or Outreach?", kind="ask", conversation_id=conv,
                      refs={"task": tool["id"]})
        H.supersede_asks(c, tool["id"], from_actor="bot:finance", to_actor="human:ana", keep=newer["id"])
    assert get(api, "needs-you")["went_away"]["withdrawn"] == 0
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (H.now(), newer["id"]))
    assert get(api, "needs-you")["went_away"]["withdrawn"] == 2


def test_a_wait_above_another_open_card_stays_after_a_one_tap_answer(api):
    _, _, finance = setup_attempt(api, "finance")
    launch = post(api, "tasks", {"title": "Q4 launch", "body": "x", "owner": "bot:finance"})
    with api.app.state.store.transaction() as c:
        venue = H.task_create(c, "bot:finance", "Which venue?", "Which venue should we book?", "human:ana",
                              parent_id=launch["id"])
        H.task_create(c, "bot:finance", "Sign the catering contract", "Can you sign it?", "human:ana",
                      parent_id=launch["id"])
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?",
                  ("Waiting on Ana to approve paying the $4,000 Stripe invoice.", launch["id"]))
        asked = H.say(c, "bot:finance", "human:ana", "Which venue should we book?", kind="ask",
                      conversation_id=H.task(c, venue["id"])["conversation_id"],
                      refs={"task": venue["id"], "if_no_answer": {"do": "The Loft", "kind": "reversible"}})
    assert post(api, "needs-you/recommended", {"ask": asked["id"]})["done"] is True
    assert get(api, f"tasks/{launch['id']}")["task"]["waiting_on"] == "human:ana"
    items = get(api, "needs-you")["items"]
    assert launch["id"] in {i["id"] for i in items} | {f["id"] for i in items for f in i.get("folded") or []}


def test_an_approval_keeps_its_needs_you_mark_while_only_the_clock_moves(api, monkeypatch):
    from backend import events as E
    store, auth, view = api.app.state.store, api.app.state.auth, api.app.state.task_view
    approval = approval_for_ana(api)
    other = post(api, "tasks", {"title": "Review the plan", "body": "x", "owner": "human:ana"})
    first = get(api, "needs-you")
    (card,) = [x for x in first["items"] if x["id"] == approval["id"]]
    real = H.now
    monkeypatch.setattr(H, "now", lambda: H.shift(real(), minutes=3))     # three minutes on, nothing else changed
    later = get(api, f"needs-you?version={first['version']}")
    assert later["unchanged"] is True
    (aged,) = [x for x in get(api, "needs-you")["items"] if x["id"] == approval["id"]]
    assert aged["age_s"] >= card["age_s"] + 180                           # the response still carries the age
    who = auth.authenticate({"authorization": "Bearer ana-test"})
    with store.read() as c:
        held, after = E.needs_baseline(c, auth, who, view), E.latest(c)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET title='Review the plan today' WHERE id=?", (other["id"],))
    monkeypatch.setattr(H, "now", lambda: H.shift(real(), minutes=4))
    with store.read() as c:
        event = E.read(c, auth, who, after, topics=("needs",), task_view=view, needs_held=held)[0][-1][2]
    assert event["changed"] == [other["id"]]                              # the approval is not named
    current = get(api, "needs-you")["version"]
    post(api, f"approvals/{approval['id']}", {"decision": "declined"})
    assert get(api, f"needs-you?version={current}").get("unchanged") is None   # a decision is a change


def test_pages_cut_the_whole_list_and_a_cursor_from_an_older_version_starts_again(api):
    _, _, finance = setup_attempt(api, "finance")
    for n in range(4):
        post(api, "tasks", {"title": f"Sign form {n}", "body": "x", "owner": "human:ana",
                            "due": H.shift(H.now(), days=n + 1)}, token=finance["token"])
    approval_for_ana(api, token=finance["token"])
    post(api, "tasks", {"title": "Plan the offsite", "body": "x", "owner": "human:ana"})
    whole = get(api, "needs-you")
    assert whole["next"] is None and whole["count"] == len(whole["items"]) >= 6 and "bots" not in whole
    seen, cursor, page = [], None, None
    while True:
        page = get(api, "needs-you?limit=2" + (f"&cursor={cursor}" if cursor else ""))
        assert page["version"] == whole["version"] and page["count"] == whole["count"] and len(page["items"]) <= 2
        seen += page["items"]
        cursor = page["next"]
        if not cursor:
            break
    ids = lambda xs: [x["id"] for x in xs]  # noqa: E731     (an approval's age_s moves with the clock)
    assert ids(seen) == ids(whole["items"])           # the same items, in the same order, as the whole read
    assert page["bots"] == views.needs_bots(whole["items"], "human:ana") and page["bots"]["bot:finance"] >= 4

    held = get(api, "needs-you?limit=2")["next"]
    post(api, "tasks", {"title": "Call the bank", "body": "x", "owner": "human:ana"})
    again = get(api, f"needs-you?limit=2&cursor={held}")
    assert again["restart"] is True and ids(again["items"]) == ids(get(api, "needs-you")["items"][:2])
    get(api, "needs-you?limit=2&cursor=nonsense", expected=422)


def test_each_card_task_is_read_by_id_once_however_many_cards(api):
    """The list reads its cards' task rows in one pass: the owner's rows are the read, and their visibility comes from
    the same statement as the chain's rows. A second read by id of every card cost a full row each (bodies included)."""
    for n in range(12):
        post(api, "tasks", {"title": f"Card {n}", "body": "x" * 4000, "owner": "human:ana"})
    page, statements = traced(api, lambda: get(api, "needs-you"))
    assert len([i for i in page["items"] if i["kind"] == "task"]) == 12
    by_id = [s for s in statements if "FROM tasks WHERE id IN" in s]
    assert len(by_id) <= 1, by_id
