"""Flat replies and bot status in threads (docs/dev/threads-contract.md §4.8-4.10): a reply stays in the thread it is
posted to and quotes its target as far as the reader may read it, a person's reply wakes the bot it answers, the flat
timeline pages the whole open subtree in time order, routing says who it asked, and a thread says which bots it waits
on and why, live and only to its readers. Fictional people only: ana (owner), ben and cara (humans)."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend import queue_refusals, thread_bot_status, thread_routing, threads
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixture, helpers)
from backend.tests.test_thread_events import read_as, who
from backend.tests.test_threads_delivery import Model, bot_post, delivery, queued_job


def new_thread(api, members=("human:ben", "bot:ops"), title="Budget", token="ana-test"):
    return post(api, "threads", {"title": title, "audience": "members", "members": list(members)}, token=token)


def say(api, thread_id, text, token="ana-test", expected=200, **more):
    return post(api, f"threads/{thread_id}/messages", {"text": text, **more}, token=token, expected=expected)


def timeline(api, thread_id, token="ana-test", **params):
    r = api.get(f"/api/v2/threads/{thread_id}/timeline", params=params, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def bot_status(api, thread_id, token="ana-test"):
    r = api.get(f"/api/v2/threads/{thread_id}/bot-status", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["bots"]


def sub_thread(api, parent_id, token="ana-test"):
    return post(api, "threads", {"title": "Side", "audience": "members", "members": [],
                                 "parent_message_id": parent_id}, token=token)


def row_of(api, message_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())


# ----------------------------------------------------------------------------- replies
def test_a_reply_stays_in_the_thread_quotes_its_target_and_wakes_the_bot_it_answers(api):
    thread = new_thread(api)
    draft = bot_post(api, "bot:ops", thread["id"], "The **draft** is ready for review")
    reply = say(api, thread["id"], "Looks good, ship it", in_reply_to=draft["id"])
    stored = row_of(api, reply["id"])
    assert stored["conversation_id"] == thread["id"] and stored["in_reply_to"] == draft["id"]
    assert stored["restricted"] == 0            # a reply to a plain message is read as any message is
    assert reply["reply_to"]["id"] == draft["id"] and reply["reply_to"]["author"] == "bot:ops"
    assert "**" not in reply["reply_to"]["excerpt"]
    woken = delivery(api, reply["id"], "bot:ops")
    assert woken["reason"] == "reply" and woken["mode"] == "queue" and queued_job(api, woken["carrier_id"]) == "queued"
    # A reply is not routed: it is for the bot it answers.
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM thread_routings WHERE message_id=?", (reply["id"],)).fetchone()
    [shown] = [m for m in timeline(api, thread["id"], "ben-test")["messages"] if m["id"] == reply["id"]]
    assert shown["reply_to"]["id"] == draft["id"] and shown["reply_to"]["author_name"]


def test_a_bot_is_not_woken_by_a_reply_to_its_own_message(api):
    thread = new_thread(api)
    first = bot_post(api, "bot:ops", thread["id"], "Step one done")
    again = bot_post(api, "bot:ops", thread["id"], "Step two done", in_reply_to=first["id"])
    assert delivery(api, again["id"], "bot:ops") is None


def test_a_reply_target_must_be_in_the_same_roots_open_subtree(api):
    thread = new_thread(api)
    other = new_thread(api, title="Elsewhere")
    there = say(api, other["id"], "Not this thread")
    top = say(api, thread["id"], "Topic")
    side = sub_thread(api, top["id"])
    inside = say(api, side["id"], "A side note")
    for target in (there["id"], "no-such-message"):
        r = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "x", "in_reply_to": target},
                     headers=headers())
        assert r.status_code == 422 and r.json()["error"]["code"] == "reply_target_invalid", r.text
    both = api.post(f"/api/v2/threads/{thread['id']}/messages",
                    json={"text": "x", "in_reply_to": top["id"], "parent_message_id": top["id"]}, headers=headers())
    assert both.status_code == 422, both.text
    # A message in a sub-thread under the root is a fair target; the reply still goes in the root.
    answer = say(api, thread["id"], "Answering the side note", in_reply_to=inside["id"])
    assert row_of(api, answer["id"])["conversation_id"] == thread["id"]
    r = api.patch(f"/api/v2/threads/{side['id']}", json={"archived": True}, headers=headers())
    assert r.status_code == 200, r.text
    closed = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "x", "in_reply_to": inside["id"]},
                      headers=headers())
    assert closed.status_code == 422, closed.text


def test_an_unreadable_target_is_refused_and_its_quote_is_hidden(api, monkeypatch):
    thread = new_thread(api)
    secret = say(api, thread["id"], "Salary numbers")
    reply = say(api, thread["id"], "Noted", in_reply_to=secret["id"])
    real = threads.message_readable
    monkeypatch.setattr(threads, "message_readable",
                        lambda c, v, m, **kw: m.get("id") != secret["id"] if getattr(v, "actor", "") == "human:ben"
                        else real(c, v, m, **kw))
    r = api.post(f"/api/v2/threads/{thread['id']}/messages", json={"text": "x", "in_reply_to": secret["id"]},
                 headers=headers("ben-test"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "reply_target_invalid", r.text
    [shown] = [m for m in timeline(api, thread["id"], "ben-test")["messages"] if m["id"] == reply["id"]]
    assert shown["reply_to"] == {"id": secret["id"], "hidden": True}
    assert "Salary" not in str(shown)


# ----------------------------------------------------------------------------- flat timeline
def test_the_flat_timeline_is_the_open_subtree_in_time_order_paged_by_the_same_cursor(api):
    thread = new_thread(api)
    a = say(api, thread["id"], "A")
    side = sub_thread(api, a["id"])
    b = say(api, side["id"], "B in the side thread")
    c_ = say(api, thread["id"], "C")
    d = say(api, side["id"], "D in the side thread")
    tree = timeline(api, thread["id"])
    assert {m["id"]: m["depth"] for m in tree["messages"] if m.get("seq")}[b["id"]] == 1
    flat = timeline(api, thread["id"], flat=1)
    ids = [m["id"] for m in flat["messages"] if m.get("seq") is not None]
    assert ids == [a["id"], b["id"], c_["id"], d["id"]]
    assert all(m["depth"] == 0 and not m.get("sub_thread") for m in flat["messages"])
    first = timeline(api, thread["id"], flat=1, limit=2)
    assert [m["id"] for m in first["messages"]] == [c_["id"], d["id"]] and first["next_cursor"]
    rest = timeline(api, thread["id"], flat=1, limit=2, cursor=first["next_cursor"])
    assert [m["id"] for m in rest["messages"]] == [a["id"], b["id"]]
    assert "bot_status" in first and "bot_status" not in rest
    # The byte cap ends a page on a whole message and leaves a cursor for the rest.
    small = timeline(api, thread["id"], flat=1, max_bytes=1)
    assert len(small["messages"]) >= 1 and small["next_cursor"]


def test_a_flat_page_has_the_viewers_read_mark_in_the_root_and_each_sub_thread_on_it(api):
    thread = new_thread(api)
    a = say(api, thread["id"], "A")
    side = sub_thread(api, a["id"])
    b = say(api, side["id"], "B in the side thread")
    c_ = say(api, thread["id"], "C")
    assert "read_marks" not in timeline(api, thread["id"], "ben-test")
    assert timeline(api, thread["id"], "ben-test", flat=1)["read_marks"] == {thread["id"]: 0, side["id"]: 0}
    post(api, f"threads/{thread['id']}/read", {"seq": c_["seq"]}, token="ben-test")
    post(api, f"threads/{side['id']}/read", {"seq": b["seq"]}, token="ben-test")
    marks = timeline(api, thread["id"], "ben-test", flat=1)["read_marks"]
    assert marks == {thread["id"]: c_["seq"], side["id"]: b["seq"]}
    # A page with no sub-thread message still has the root's mark.
    assert timeline(api, thread["id"], "ben-test", flat=1, limit=1)["read_marks"] == {thread["id"]: c_["seq"]}


def test_a_flat_timeline_leaves_out_an_archived_sub_thread(api):
    thread = new_thread(api)
    a = say(api, thread["id"], "A")
    side = sub_thread(api, a["id"])
    gone = say(api, side["id"], "Archived side")
    assert api.patch(f"/api/v2/threads/{side['id']}", json={"archived": True}, headers=headers()).status_code == 200
    assert gone["id"] not in [m["id"] for m in timeline(api, thread["id"], flat=1)["messages"]]


# ----------------------------------------------------------------------------- routing line
def test_a_routed_message_says_who_routing_asked(api):
    store = api.app.state.store
    thread = new_thread(api, members=("human:ben", "bot:ops", "bot:cpo", "bot:coo"))
    asked = say(api, thread["id"], "Who can look at the budget?")
    plain = say(api, thread["id"], "@ops thanks")
    thread_routing.drain(store, primary=Model({"cpo": 0.99}))
    by_id = {m["id"]: m for m in timeline(api, thread["id"], "ben-test")["messages"]}
    assert [t["actor"] for t in by_id[asked["id"]]["routed"]["to"]] == ["bot:cpo"]
    assert by_id[plain["id"]]["routed"] is None


# ----------------------------------------------------------------------------- bot status
def machine_for(api, bots=("ops",)):
    machine = runner(api)
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    return machine


def test_a_bot_whose_computer_is_offline_cant_reply_with_a_fix(api):
    machine = machine_for(api)
    thread = new_thread(api)
    say(api, thread["id"], "@ops can you check this?")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at='2020-01-01T00:00:00Z' WHERE runner_id=?",
                  (machine["runner_id"],))
    [row] = bot_status(api, thread["id"])
    assert row["actor"] == "bot:ops" and row["state"] == "cant_reply" and row["detail"]
    assert row["fix"]["href"] == "#/settings/computers"
    assert timeline(api, thread["id"])["bot_status"] == [row]


def test_a_refused_claim_says_why_and_where_to_fix_it(api):
    machine = machine_for(api)
    thread = new_thread(api)
    say(api, thread["id"], "@ops can you check this?")
    store = api.app.state.store
    document = {"schema_version": 1, "runtimes": {"fake": {"installed": True, "authenticated": "missing"}},
                "bots": {"ops": {"ready": False, "runtime": "fake", "sign_in": "missing", "problems": ["Sign in"]}}}
    with store.transaction() as c:
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?", (H._dump(document), machine["runner_id"]))
    assert post(api, "jobs/claim", {}, token=machine["token"])["attempt"] is None
    with store.transaction() as c:
        queue_refusals.observe(c, store.settings)
        assert queue_refusals.state(c, "ops")["reason"] == "not_ready"
    [row] = bot_status(api, thread["id"])
    assert row["state"] == "cant_reply" and "Test Mac" in row["detail"]
    assert row["fix"]["href"] == "#/settings/computers"


def test_a_working_bot_names_its_run_and_up_next_never_names_a_place_the_viewer_cannot_read(api):
    machine = machine_for(api)
    private = new_thread(api, members=("bot:ops",), title="Secret plan")
    say(api, private["id"], "@ops draft the plan")
    attempt = claim(api, machine)
    assert attempt
    [working] = bot_status(api, private["id"])
    assert working["state"] == "working" and working["run_id"] == attempt["id"]
    shared = new_thread(api, title="Shared")
    say(api, shared["id"], "@ops and this one", token="ben-test")
    [for_ben] = bot_status(api, shared["id"], "ben-test")
    assert for_ben["state"] == "up_next" and "Secret" not in for_ben["detail"]
    [for_ana] = bot_status(api, shared["id"])
    assert for_ana["state"] == "up_next" and "Secret plan" in for_ana["detail"]


def test_a_bot_that_posted_since_is_no_longer_waited_on(api):
    machine_for(api)
    thread = new_thread(api)
    say(api, thread["id"], "@ops can you check this?")
    assert bot_status(api, thread["id"])
    bot_post(api, "bot:ops", thread["id"], "Checked.")
    assert bot_status(api, thread["id"]) == []


def test_bot_status_goes_live_only_to_the_roots_readers(api):
    machine_for(api)
    thread = new_thread(api)
    with api.app.state.store.read() as c:
        after = E.latest(c)
    say(api, thread["id"], "@ops can you check this?")

    def sent(token):
        return [d for _, topic, d in read_as(api, who(api, token), after, ("threads",))
                if topic == "threads" and d.get("kind") == "bot_status"]
    ben = sent("ben-test")
    assert ben and ben[-1]["thread_id"] == thread["id"] and ben[-1]["bots"][0]["actor"] == "bot:ops"
    assert sent("cara-test") == []


def test_at_most_five_bots_oldest_wait_first(api):
    bots = ("ops", "cpo", "coo", "finance", "product-design", "doc-updater")
    machine_for(api, bots)
    thread = new_thread(api, members=("human:ben", *("bot:" + b for b in bots)))
    for bot in bots:
        say(api, thread["id"], f"@{bot} please look")
    rows = bot_status(api, thread["id"])
    assert len(rows) == thread_bot_status.MAX_BOTS
    assert [r["since"] for r in rows] == sorted(r["since"] for r in rows)
    assert rows[0]["actor"] == "bot:ops"


def test_a_reader_of_only_a_migrated_homes_old_history_gets_no_bot_status(api, monkeypatch):
    machine_for(api)
    thread = new_thread(api)
    with api.app.state.store.read() as c:
        after = E.latest(c)
    say(api, thread["id"], "@ops can you check this?")
    assert bot_status(api, thread["id"], "ben-test")
    monkeypatch.setattr(threads, "legacy_reader_only", lambda c, v, row: getattr(v, "actor", "") == "human:ben")
    assert bot_status(api, thread["id"], "ben-test") == []
    assert timeline(api, thread["id"], "ben-test")["bot_status"] == []
    assert bot_status(api, thread["id"])                # the Home's own readers still see it
    sent = [d for _, topic, d in read_as(api, who(api, "ben-test"), after, ("threads",)) if d.get("kind") == "bot_status"]
    assert sent == []


def test_a_message_to_one_bot_that_names_another_is_only_the_first_bots_input(api):
    machine = machine_for(api, ("ops", "cpo"))
    thread = new_thread(api, members=("human:ben", "bot:ops", "bot:cpo"))
    say(api, thread["id"], "@ops draft it")
    attempt = claim(api, machine, "ops")
    assert attempt
    both = say(api, thread["id"], "@ops and ask @cpo to check")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], both["id"]))
    rows = {r["actor"]: r for r in bot_status(api, thread["id"])}
    assert rows["bot:ops"]["run_id"] == attempt["id"]
    assert rows["bot:cpo"]["run_id"] != attempt["id"] and rows["bot:cpo"]["state"] != "working"


def test_one_bots_many_old_wakes_never_crowd_out_another_bot(api, monkeypatch):
    machine_for(api, ("ops", "cpo"))
    thread = new_thread(api, members=("human:ben", "bot:ops", "bot:cpo"))
    for n in range(3):
        say(api, thread["id"], f"@ops note {n}")
    say(api, thread["id"], "@cpo please look")
    monkeypatch.setattr(thread_bot_status, "OPEN_PER_BOT", 1)
    assert {r["actor"] for r in bot_status(api, thread["id"])} == {"bot:ops", "bot:cpo"}


def test_an_offline_outside_agent_is_waited_on_without_a_job(api, monkeypatch):
    from backend import agents
    thread = new_thread(api)
    say(api, thread["id"], "@ops can you check this?")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
    assert bot_status(api, thread["id"]) == []          # a bot here: its turn ended, nothing waits
    monkeypatch.setattr(agents, "external_harness", lambda c, bot, *a, **kw: "acme-agent" if bot == "ops" else None)
    monkeypatch.setattr(agents, "presence", lambda c, bot, *a, **kw: {"online": False} if bot == "ops" else None)
    [row] = bot_status(api, thread["id"])
    assert row["actor"] == "bot:ops" and row["state"] == "cant_reply" and row["detail"] == thread_bot_status.OFFLINE


def test_a_step_the_throttle_held_back_is_sent_on_the_next_tick(api):
    machine = machine_for(api)
    thread = new_thread(api)
    say(api, thread["id"], "@ops can you check this?")
    attempt = claim(api, machine)
    store = api.app.state.store

    def rows():
        with store.read() as c:
            return c.execute("SELECT COUNT(*) FROM changes WHERE topic='threads' AND kind='bot_status' AND subject_id=?",
                             (thread["id"],)).fetchone()[0]
    with store.transaction() as c:
        assert thread_bot_status.send_trailing(c) == 0          # nothing moved since the run started
    before = rows()
    with store.transaction() as c:
        c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,9001,'tool',?,?)",
                  (attempt["id"], '{"name": "Read"}', H.now()))
    assert rows() == before                                     # within 5 s of the last one: held back
    with store.transaction() as c:
        c.execute("UPDATE changes SET created_at='2020-01-01T00:00:00.000Z' WHERE topic='threads' AND kind='bot_status' "
                  "AND subject_id=?", (thread["id"],))
        c.execute("UPDATE attempt_events SET created_at=? WHERE attempt_id=? AND seq=9001", (H.now(), attempt["id"]))
        assert thread_bot_status.send_trailing(c) == 1
        assert thread_bot_status.send_trailing(c) == 0          # sent once
    assert rows() == before + 1


def clone_wakes(c, message_id, count, tag):
    """`count` copies of a person's message and its open wakes, as one root's long backlog: new ids, and new seqs from
    the stamp trigger."""
    def columns(table):
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})") if r[1] != "id"]
    m, d = columns("messages"), columns("message_deliveries")
    for n in range(count):
        new = f"{message_id}-{tag}-{n}"
        c.execute(f"INSERT INTO messages(id,{','.join(m)}) SELECT ?,{','.join(m)} FROM messages WHERE id=?", (new, message_id))
        picked = ["?" if col == "message_id" else col for col in d]
        c.execute(f"INSERT INTO message_deliveries(id,{','.join(d)}) SELECT ?||recipient,{','.join(picked)} "
                  "FROM message_deliveries WHERE message_id=?", (new + ":", new, message_id))


def test_a_thousand_open_wakes_for_one_bot_are_read_in_a_bounded_number_of_statements(api):
    machine_for(api)
    thread = new_thread(api)
    first = say(api, thread["id"], "@ops can you check this?")
    store = api.app.state.store
    with store.transaction() as c:
        clone_wakes(c, first["id"], 1000, "a")

    def read():
        seen, steps = [], [0]
        with store.read() as c:
            c.set_trace_callback(seen.append)
            found = thread_bot_status.candidates(c, thread["id"])
            c.set_trace_callback(None)
            c.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1), 100)
            c.execute(thread_bot_status._OPEN_SQL, (thread["id"], thread_bot_status.OPEN_PER_BOT)).fetchall()
            c.set_progress_handler(None, 100)
        return found, len(seen), steps[0]
    found, statements, steps = read()
    assert set(found) == {"bot:ops"}
    assert statements <= 10
    with store.transaction() as c:
        clone_wakes(c, first["id"], 1000, "b")
    found, more_statements, more_steps = read()
    assert set(found) == {"bot:ops"} and more_statements == statements
    assert more_steps < 3 * steps                   # each bot's latest post is read once, not once per wake

    bot_post(api, "bot:ops", thread["id"], "Checked.")
    with store.read() as c:
        assert thread_bot_status.candidates(c, thread["id"]) == {}
    say(api, thread["id"], "@ops and this one?")
    with store.read() as c:
        assert set(thread_bot_status.candidates(c, thread["id"])) == {"bot:ops"}
