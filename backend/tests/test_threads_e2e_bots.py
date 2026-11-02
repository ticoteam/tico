"""Bots in threads, over the real API with a runner that claims and answers turns (spec section 9, "Bots").

A runner claims a bot's job and answers it through the attempt endpoints, as runner-rs does, so each test follows
the path a real reply takes: the wake, the claim, the turn's grant and its reply. The Rust runner itself runs in the
remote browser and runner journeys (scripts/remote.py --setup rust); these tests cover what the server decides.
"""

from backend.tests.test_api import api, claim, assign, post, ready, runner  # noqa: F401  (the api fixture)
from backend.tests.threads_e2e import (call, create_thread, home_id, ok, read_status, require_threads_api,
                                       say, timeline)


def bot_runner(api, *bots):
    """A computer assigned these bots, ready for them: the same setup as the runner's first heartbeat."""
    r = runner(api)
    for bot in bots:
        assign(api, r, bot)
    ready(api, r, list(bots))
    return r


def finish(api, r, attempt, text):
    """The runner's answer to a turn: started, one final event, and completion (as test_api's turn test does)."""
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "local-thread"}, token=r["token"])
    event = {"events": [{"seq": 1, "kind": "message", "payload": {"text": text, "final": True}}]}
    post(api, f"attempts/{aid}/events", event, token=r["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 1}, token=r["token"])


def home_of(api, slug, token="ana-test"):
    return home_id(api, slug)


def test_a_bot_answers_in_the_sub_thread_it_was_asked_in_and_nowhere_else(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo")
    t = create_thread(api, title="Launch", members=["bot:cpo"])
    m = say(api, "ana-test", t["id"], "Kickoff notes")
    asked = say(api, "ana-test", t["id"], "@cpo list the risks", parent_message_id=m["id"])
    attempt = claim(api, r, "cpo")
    assert attempt is not None, "a mention in a sub-thread wakes the bot"
    # A mention without `to` wakes the bot through a keeper carrier that names the message (contract 4.4): the run
    # starts from the carrier, and the carrier carries the asked message.
    woke = attempt["message"]
    assert ((woke.get("refs") or {}).get("carries") or woke["id"]) == asked["id"], woke
    finish(api, r, attempt, "Risks: pricing, timing")
    rows = timeline(api, "ana-test", t["id"])
    parent = next(row for row in rows if row["id"] == m["id"])
    sub_id = parent["sub_thread"]["id"]
    assert sub_id == asked["thread_id"], "the parent's summary points at the sub-thread the question went into"
    replies = timeline(api, "ana-test", sub_id)
    reply = next((row for row in replies if row.get("body") == "Risks: pricing, timing"), None)
    assert reply is not None, ("the answer never reached the sub-thread; it holds "
                               f"{[row.get('body') for row in replies]}")
    assert reply["thread_id"] == sub_id, "the answer lands in the sub-thread that asked"
    assert "depth" in reply, "every timeline message carries depth (contract 10)"
    assert parent["sub_thread"]["count"] == len(replies), "the parent's summary counts the sub-thread's messages"
    assert "Risks: pricing, timing" not in [row.get("body") for row in rows], "the root shows the answer only via the summary"
    assert [row["body"] for row in rows] == ["Kickoff notes"]


def test_a_mention_in_a_bots_home_wakes_it_once_for_that_message(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo")
    home = home_of(api, "cpo")
    # The mention and the Home both wake the bot (spec section 1, wake precedence): one run, not two.
    say(api, "ana-test", home, "@cpo a question for you")
    attempt = claim(api, r, "cpo")
    assert attempt is not None
    finish(api, r, attempt, "Answered once")
    assert claim(api, r, "cpo") is None, "the same message does not wake the bot a second time"


def test_the_loop_guard_stops_bots_pinging_each_other_and_a_person_resumes_them(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo", "product-design")
    t = create_thread(api, title="Ping pong", members=["bot:cpo", "bot:product-design"])
    say(api, "ana-test", t["id"], "@cpo start the loop")
    other = {"cpo": "product-design", "product-design": "cpo"}
    runs = 0
    for _ in range(20):
        attempt = next((a for a in (claim(api, r, bot) for bot in other) if a), None)
        if attempt is None:
            break
        runs += 1
        finish(api, r, attempt, f"@{other[attempt['bot']]} keep going {runs}")
    # One person's message starts the run, then the bots' own wakes are capped at six an hour per thread (the default
    # TICO_THREAD_LOOP_GUARD): the first run plus six bot-caused runs, and then the guard holds the rest.
    assert 1 <= runs <= 7, f"the loop ran {runs} times"
    assert claim(api, r, "cpo") is None and claim(api, r, "product-design") is None
    # The sidebar's `attention` holds each root with capped wakes (contract 4.5, 10), held here by hand: a bot's post that
    # names Ana is not attention by itself, and this loop may stop before the guard does.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE message_deliveries SET state='capped' WHERE root_id=? AND recipient LIKE 'bot:%' AND state='pending'",
                  (t["id"],))
    attention = ok(call(api, "GET", "threads", "ana-test"))["attention"]
    assert [row["capped"] for row in attention if row.get("root_id") == t["id"]] == [1], attention
    say(api, "ana-test", t["id"], "@cpo carry on")
    assert claim(api, r, "cpo") is not None, "a person's message resumes the deferred wakes"


def test_a_bot_only_sees_the_thread_it_was_woken_in_and_its_sub_threads(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo")
    mine = create_thread(api, title="Pricing", members=["bot:cpo"])
    other = create_thread(api, title="Payroll", members=["human:cara"])
    m = say(api, "ana-test", mine["id"], "Pricing numbers")
    sub = say(api, "ana-test", mine["id"], "@cpo check the tiers", parent_message_id=m["id"])["thread_id"]
    attempt = claim(api, r, "cpo")
    assert attempt is not None
    bot = attempt["token"]
    assert read_status(api, bot, mine["id"]) == 200
    assert read_status(api, bot, sub) == 200
    assert read_status(api, bot, other["id"]) == 404, "a turn reads its own thread and sub-threads, not the others"


def test_a_public_thread_and_a_private_one_never_share_a_turn_context(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo")
    public = create_thread(api, title="Handbook", audience="company", members=["bot:cpo"])
    private = create_thread(api, title="Board prep", members=["bot:cpo"])
    say(api, "ana-test", public["id"], "@cpo update the handbook intro")
    first = claim(api, r, "cpo")
    finish(api, r, first, "Done with the handbook")
    say(api, "ana-test", private["id"], "@cpo draft the board note")
    second = claim(api, r, "cpo")
    conv_a, conv_b = first["conversation"], second["conversation"]
    assert conv_a["root_id"] == public["id"] and conv_b["root_id"] == private["id"]
    assert (conv_a["audience_class"], conv_b["audience_class"]) == ("public", "private"), \
        "the turn context says which class it runs in, so the runner keys its session by it (contract section 8)"


def test_a_bot_post_is_activity_for_a_person_and_not_an_unread_count(api):
    require_threads_api(api)
    r = bot_runner(api, "cpo")
    t = create_thread(api, title="Standup", members=["bot:cpo", "human:cara"])
    say(api, "cara-test", t["id"], "@cpo standup: what is the status?")
    unread = ok(call(api, "GET", f"threads/{t['id']}", "ana-test"))["me"]["unread"]
    assert unread == 1, "a person's message is unread"
    attempt = claim(api, r, "cpo")
    assert attempt is not None
    # The bot answers without addressing anyone: activity, which shows as a dot, never a count.
    posted = call(api, "POST", f"threads/{t['id']}/messages", attempt["token"], {"text": "Status: green"})
    assert posted.status_code in (200, 201), posted.text
    me = ok(call(api, "GET", f"threads/{t['id']}", "ana-test"))["me"]
    assert me["unread"] == unread and me["activity"] is True
