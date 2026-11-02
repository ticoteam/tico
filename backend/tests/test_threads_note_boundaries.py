"""What a thread's private message may reach once it is waiting or queued for a bot: a deferred note stays inside its
source's session, and a turn of its own in that thread carries it (one per thread, no loop, nothing lost); `/notes` asks
whether the person or agent asking reads the source; a bot removed before its claim gets no run and no grant.
Fictional people only: ana (owner), ben (admin), cara (human); the bots come from the api fixture.
"""
import json

from backend import threads
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, assign, headers, post, ready, runner  # noqa: F401


def bot_runner(api, bot="cpo"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def claim(api, r, bot):
    return post(api, "jobs/claim", {"next_run": True, "bot": bot}, token=r["token"])["attempt"]


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def new_thread(api, audience="members", members=("bot:cpo", "bot:ops"), title="Board prep", token="ana-test"):
    payload = {"title": title, "audience": audience}
    if audience == "members":
        payload["members"] = list(members)
    answer = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()["id"]


def say(api, thread_id, text, token="ana-test", parent=None):
    payload = {"text": text, **({"parent_message_id": parent} if parent else {})}
    answer = api.post(f"/api/v2/threads/{thread_id}/messages", json=payload, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return answer.json()


def bot_says(api, thread_id, text, bot="bot:ops", **kwargs):
    """A bot's plain mention of another bot: it waits for that bot's next run (bot_to_bot), so it starts none."""
    with api.app.state.store.transaction() as c:
        return threads.post(c, bot, thread_id, text, **kwargs)


def finish(api, r, attempt, text="Done"):
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": f"t-{aid}"}, token=r["token"])
    return post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 0}, token=r["token"])


def job_count(api, bot="cpo"):
    return rows(api, "SELECT COUNT(*) AS n FROM jobs WHERE bot_id=?", bot)[0]["n"]


def wakes(api, bot="cpo"):
    """Wakes written so far, whatever came of them."""
    return rows(api, "SELECT COUNT(*) AS n FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? "
                     "AND json_extract(m.refs,'$.wake')='thread_notes'", bot)[0]["n"]


def waiting(api, bot="bot:cpo"):
    return rows(api, f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {H.NOTE_WAITING_SQL} ORDER BY n.created_at", bot)


def drain(api, r, bot="cpo", most=12):
    """Every run the runner would get, in order: each one's root and the words of the notes it carried."""
    runs = []
    while True:
        attempt = claim(api, r, bot)
        if not attempt:
            return runs
        runs.append({"root": attempt["conversation"].get("root_id"), "class": attempt["conversation"].get("audience_class"),
                     "notes": [n["text"] for n in attempt.get("notes") or []], "prompt": json.dumps(attempt)})
        finish(api, r, attempt)
        assert len(runs) < most, "a wake loop"


# ----------------------------------------------------------------------------- a private note and a public session
def test_a_private_note_stays_out_of_a_public_run_and_one_run_in_its_thread_carries_them_all(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    before = job_count(api)
    bot_says(api, private, "@cpo Zebrawire salary 41000")
    bot_says(api, private, "@cpo Zebrawire salary 42000")
    assert job_count(api) == before  # a bot's plain mention waits for the next run, and starts none per message
    say(api, public, "@cpo update the handbook")

    public_run, *rest = drain(api, r)
    assert public_run["root"] == public and public_run["class"] == "public"
    assert public_run["notes"] == [] and "Zebrawire" not in public_run["prompt"]
    assert len(rest) == 1  # once per thread, with no further wake
    assert rest[0]["root"] == private and rest[0]["class"] == "private"
    assert ["41000" in rest[0]["notes"][0], "42000" in rest[0]["notes"][1]] == [True, True]
    assert waiting(api) == []


def test_a_run_in_the_notes_own_thread_or_a_sub_thread_carries_them_with_no_extra_run(api):
    r = bot_runner(api)
    private = new_thread(api)
    top = say(api, private, "Top")
    sub = say(api, private, "Below", parent=top["id"])["conversation_id"]
    bot_says(api, sub, "@cpo Zebrawire below")
    bot_says(api, private, "@cpo Zebrawire above")
    say(api, private, "@cpo Your turn")
    runs = drain(api, r)
    assert len(runs) == 1 and runs[0]["root"] == private and len(runs[0]["notes"]) == 2
    assert waiting(api) == []


def test_each_private_thread_gets_one_run_and_a_second_run_elsewhere_adds_none(api):
    r = bot_runner(api)
    one, two = new_thread(api, title="One"), new_thread(api, title="Two")
    public = new_thread(api, audience="company", title="Handbook")
    bot_says(api, one, "@cpo Zebra one")
    bot_says(api, two, "@cpo Zebra two")
    say(api, public, "@cpo first")
    first = claim(api, r, "cpo")
    assert first["notes"] == []
    finish(api, r, first)
    say(api, public, "@cpo second")
    runs = drain(api, r)
    assert sorted(run["root"] for run in runs if run["notes"]) == sorted([one, two])
    assert sum(run["root"] == public for run in runs) == 1  # only the second message
    assert not [run for run in runs if run["root"] != public and not run["notes"]]  # no run for nothing
    assert all("Zebra" not in run["prompt"] for run in runs if run["root"] == public)
    assert waiting(api) == []


def test_one_private_threads_note_stays_out_of_anothers_run(api):
    r = bot_runner(api)
    one, two = new_thread(api, title="One"), new_thread(api, title="Two")
    bot_says(api, one, "@cpo Zebra one")
    say(api, two, "@cpo work here")
    runs = drain(api, r)
    assert [run["root"] for run in runs] == [two, one]
    assert runs[0]["notes"] == [] and "Zebra" not in runs[0]["prompt"]
    assert "Zebra one" in runs[1]["notes"][0]


def test_a_note_with_no_source_and_a_public_ones_ride_any_public_run_as_before(api):
    r = bot_runner(api)
    public = new_thread(api, audience="company", title="Handbook")
    other = new_thread(api, audience="company", title="Roadmap")
    post(api, "notes", {"to": "cpo", "text": "Plain note"})
    bot_says(api, other, "@cpo Public word")
    say(api, public, "@cpo work here")
    runs = drain(api, r)
    assert len(runs) == 1 and len(runs[0]["notes"]) == 2


def test_a_bots_direct_message_still_rides_its_next_run(api):
    r = bot_runner(api)
    public = new_thread(api, audience="company", title="Handbook")
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:ops", "bot:cpo", "Just so you know")
    say(api, public, "@cpo work here")
    runs = drain(api, r)
    assert len(runs) == 1 and len(runs[0]["notes"]) == 1


def test_a_note_whose_bot_left_its_thread_is_cancelled_and_wakes_nothing(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    bot_says(api, private, "@cpo Zebrawire")
    gone = api.delete(f"/api/v2/threads/{private}/members/bot:cpo", headers=headers())
    assert gone.status_code == 200, gone.text
    say(api, public, "@cpo work here")
    runs = drain(api, r)
    assert [run["root"] for run in runs] == [public] and runs[0]["notes"] == []
    assert rows(api, "SELECT body, canceled_at FROM notes WHERE to_actor='bot:cpo'")[0]["body"] == ""


def test_a_failed_run_leaves_its_note_waiting_and_starts_no_loop(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    bot_says(api, private, "@cpo Zebrawire")
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    carrying = claim(api, r, "cpo")
    assert "Zebrawire" in carrying["notes"][0]["text"]
    aid = carrying["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-failed"}, token=r["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "failed", "text": "", "last_seq": 0}, token=r["token"])
    assert len(waiting(api)) == 1  # not lost
    assert claim(api, r, "cpo") is None  # the bot waits out the failure, as any failed run makes it
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE run_backoff SET fails=0,wait_until=NULL")
    runs = drain(api, r)
    assert sum("Zebrawire" in text for run in runs for text in run["notes"]) == 1 and waiting(api) == []


def test_a_backlog_of_private_notes_ends_with_nothing_left_and_no_loop(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    for n in range(60):
        bot_says(api, private, f"@cpo Zebra {n}")
    say(api, public, "@cpo work here")
    runs = drain(api, r)
    assert runs[0]["root"] == public and runs[0]["notes"] == []
    assert sum(len(run["notes"]) for run in runs) == 60 and len(runs) <= 4
    assert waiting(api) == []


# ----------------------------------------------------------------------------- /notes asks who is reading
def private_note(api):
    thread = new_thread(api, members=("human:ben", "bot:cpo", "bot:ops"), token="ben-test")
    bot_says(api, thread, "@cpo Zebrawire salary")
    return thread


def listed(api, token, to="cpo"):
    answer = api.get("/api/v2/notes", params={"to": to}, headers=headers(token))
    assert answer.status_code == 200, answer.text
    return [n["text"] for n in answer.json()["notes"]]


def test_notes_shows_a_private_note_to_who_reads_its_source_and_to_nobody_else(api):
    private_note(api)
    assert any("Zebrawire" in text for text in listed(api, "ben-test"))
    assert not any("Zebrawire" in text for text in listed(api, "cara-test"))  # cara reads cpo, not the thread
    assert not any("Zebrawire" in text for text in listed(api, "ana-test"))  # nor does the owner, who is not in it


def test_cancelling_a_note_asks_the_same(api):
    private_note(api)
    nid = rows(api, "SELECT id FROM notes WHERE to_actor='bot:cpo'")[0]["id"]
    answer = api.post(f"/api/v2/notes/{nid}/cancel", json={}, headers=headers("cara-test"))
    assert answer.status_code == 404


def test_a_bots_own_run_reads_the_note_it_carries(api):
    r = bot_runner(api)
    thread = private_note(api)
    say(api, thread, "@cpo Your turn", token="ben-test")
    attempt = claim(api, r, "cpo")
    assert "Zebrawire" in attempt["notes"][0]["text"]
    assert any("Zebrawire" in text for text in listed(api, attempt["token"]))


def test_a_run_in_a_public_session_cannot_read_a_private_note_through_the_endpoint(api):
    r = bot_runner(api)
    private_note(api)
    public = new_thread(api, audience="company", title="Handbook")
    post(api, "notes", {"to": "cpo", "text": "Plain note"})
    say(api, public, "@cpo work here")
    attempt = claim(api, r, "cpo")
    seen = listed(api, attempt["token"])
    assert not any("Zebrawire" in text for text in seen) and any("Plain note" in text for text in seen)


def test_an_external_agent_reads_a_note_only_by_its_own_rights(api):
    private_note(api)  # cpo is a member here, so its agent reads this one
    public = new_thread(api, audience="company", title="Handbook")
    bot_says(api, public, "@cpo Quillframe")  # an agent has no company standing here, and no grant
    agent, store = Identity("bot:cpo", "bot", agent="hermes"), api.app.state.store
    with store.read_transaction() as c:
        shown = {n["body"]: threads.note_source_shown(c, api.app.state.auth, agent, n)
                 for n in rows(api, "SELECT * FROM notes WHERE to_actor='bot:cpo'")}
    assert [body for body, ok in shown.items() if "Zebrawire" in body and ok]
    assert [body for body, ok in shown.items() if "Quillframe" in body and not ok]
    assert any("Quillframe" in text for text in listed(api, "ana-test"))  # a person reads the company thread


def test_a_run_for_a_threads_notes_ends_as_moot_when_another_run_there_carried_them(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    bot_says(api, private, "@cpo Zebrawire")
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE notes SET settled_at=? WHERE message_id IS NOT NULL", (H.now(),))
    assert claim(api, r, "cpo") is None
    assert rows(api, "SELECT state FROM jobs WHERE state IN ('queued','leased')") == []


# ----------------------------------------------------------------------------- the wake's own session and anchor
def turn_task_private(api, tid):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (tid,))


def task_about(api, requester="bot:cpo"):
    """A task on a person, so no run of its own starts: `requester` is who keeps reading it once it is private."""
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, "Launch plan", "Plan it.", "human:ben", lint=False)["id"]


def test_the_wake_for_a_note_narrower_than_its_public_thread_runs_in_a_private_session(api):
    r = bot_runner(api)
    company, other = new_thread(api, audience="company", title="Launch"), new_thread(api, audience="company", title="Handbook")
    tid = task_about(api)
    bot_says(api, company, "@cpo Zebrawire", refs={"task": tid})
    turn_task_private(api, tid)                     # cpo asked for the task, so it still reads the message
    say(api, other, "@cpo work here")
    public = claim(api, r, "cpo")
    assert public["conversation"]["audience_class"] == "public" and public["notes"] == []
    finish(api, r, public)
    wake = claim(api, r, "cpo")
    assert wake["conversation"]["audience_class"] == "private" and wake["conversation"]["root_id"] == company
    assert ["Zebrawire" in n["text"] for n in wake["notes"]] == [True]
    finish(api, r, wake)
    assert waiting(api) == [] and claim(api, r, "cpo") is None


def test_a_narrower_note_enters_only_a_private_session_of_its_own_thread(api):
    bot_runner(api)
    company = new_thread(api, audience="company", title="Launch")
    tid = task_about(api)
    posted = bot_says(api, company, "@cpo Zebrawire", refs={"task": tid})
    turn_task_private(api, tid)
    with api.app.state.store.transaction() as c:
        conv = H.conversation(c, company)
        note = dict(c.execute("SELECT * FROM notes WHERE message_id=?", (posted["id"],)).fetchone())
        assert not threads.note_fits_run(c, note, conv, "public")
        assert threads.note_fits_run(c, note, conv, "private")


def session_class(api, thread_id, message_id):
    from backend.execution import thread_payload
    with api.app.state.store.read() as c:
        return thread_payload(c, H.conversation(c, thread_id), H.message(c, message_id))["audience_class"]


def test_a_carrier_for_a_message_narrower_than_its_company_thread_gets_a_private_session(api):
    r = bot_runner(api)
    company = new_thread(api, audience="company", title="Launch")
    tid = task_about(api)
    bot_says(api, company, "@cpo Zebrawire", bot="human:ben", refs={"task": tid})
    turn_task_private(api, tid)                     # cpo asked for the task, so the claim still hands it out
    attempt = claim(api, r, "cpo")
    assert attempt["conversation"]["audience_class"] == "private" and attempt["conversation"]["root_id"] == company
    assert "Zebrawire" in attempt["message"]["body"]


def test_a_company_threads_ordinary_turns_keep_the_public_session(api):
    r = bot_runner(api)
    company = new_thread(api, audience="company", title="Launch")
    say(api, company, "@cpo first")
    first = claim(api, r, "cpo")
    assert first["conversation"]["audience_class"] == "public"
    finish(api, r, first, text="Our reply")
    reply = rows(api, "SELECT * FROM messages WHERE body='Our reply'")[0]
    assert reply["restricted"] == 1 and json.loads(reply["refs"])["turn_id"] == first["id"]
    assert session_class(api, company, reply["id"]) == "public"     # flagged for its turn_id, and every reader reads it
    bot_says(api, company, "@cpo about that", bot="human:ana", in_reply_to=reply["id"])
    answered = claim(api, r, "cpo")
    assert answered["conversation"]["audience_class"] == "public"
    finish(api, r, answered)
    bot_says(api, company, "@cpo on the plan", bot="human:ana", refs={"task": task_about(api)})   # a task all may read
    tied = claim(api, r, "cpo")
    assert tied["conversation"]["audience_class"] == "public"


def test_a_private_runs_reply_is_narrower_than_its_company_thread(api):
    r = bot_runner(api)
    company = new_thread(api, audience="company", title="Launch")
    say(api, company, "@cpo first")
    first = claim(api, r, "cpo")
    finish(api, r, first, text="Our reply")
    reply = rows(api, "SELECT id FROM messages WHERE body='Our reply'")[0]["id"]
    tid = task_about(api)
    with api.app.state.store.transaction() as c:    # the run drew on a task that is private now
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (first["id"], tid))
    turn_task_private(api, tid)
    assert session_class(api, company, reply) == "private"


def delete(api, message_id, author="bot:ops"):
    with api.app.state.store.transaction() as c:
        threads.delete_message(c, author, message_id)


def sweep(api):
    """The claim passes a wake it may not read over for good; the scheduler's minute drops it."""
    with api.app.state.store.transaction() as c:
        return api.app.state.execution.drop_unreadable(c)


def two_notes_and_a_wake(api, r, refs=None):
    """Notes A and B wait in one private thread; a public turn has written the one wake, anchored to A."""
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    a = bot_says(api, private, "@cpo Zebrawire", **({"refs": refs} if refs else {}))
    bot_says(api, private, "@cpo Quillframe")
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    assert wakes(api) == 1
    return private, a


def test_deleting_the_wakes_anchor_starts_a_new_wake_for_the_other_note(api):
    r = bot_runner(api)
    private, a = two_notes_and_a_wake(api, r)
    delete(api, a["id"])
    assert claim(api, r, "cpo") is None             # passed over, then dropped, and a new one is written for B
    assert len(sweep(api)) == 1 and wakes(api) == 2
    runs = drain(api, r)
    assert [run["notes"] for run in runs] == [["@cpo Quillframe"]] and runs[0]["root"] == private
    assert waiting(api) == [] and claim(api, r, "cpo") is None


def test_the_wakes_anchor_turning_unreadable_starts_a_new_wake_for_the_other_note(api):
    r = bot_runner(api)
    tid = task_about(api, requester="human:ana")
    private, _ = two_notes_and_a_wake(api, r, refs={"task": tid})
    turn_task_private(api, tid)                     # cpo is no party to it: its note is taken back with the wake
    assert claim(api, r, "cpo") is None
    assert len(sweep(api)) == 1 and wakes(api) == 2
    runs = drain(api, r)
    assert [run["notes"] for run in runs] == [["@cpo Quillframe"]] and runs[0]["root"] == private
    assert waiting(api) == [] and claim(api, r, "cpo") is None


def test_a_whole_read_of_unreadable_notes_does_not_strand_the_readable_one_behind_it(api, monkeypatch):
    monkeypatch.setattr(threads, "NOTES_READ", 2)
    r = bot_runner(api)
    tid = task_about(api, requester="human:ana")
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    for word in ("Zebrawire", "Larkspindle", "Copperfen"):
        bot_says(api, private, f"@cpo {word}", refs={"task": tid})
    bot_says(api, private, "@cpo Quillframe")
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    assert wakes(api) == 1
    turn_task_private(api, tid)                     # every note a read of two can see is taken back, and one more after them
    assert claim(api, r, "cpo") is None
    assert len(sweep(api)) == 1 and wakes(api) == 2
    runs = drain(api, r)
    assert [run["notes"] for run in runs] == [["@cpo Quillframe"]] and runs[0]["root"] == private
    assert waiting(api) == [] and claim(api, r, "cpo") is None


def test_a_wake_whose_notes_are_all_unreadable_writes_no_other(api, monkeypatch):
    monkeypatch.setattr(threads, "NOTES_READ", 2)
    r = bot_runner(api)
    tid = task_about(api, requester="human:ana")
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    for word in ("Zebrawire", "Larkspindle", "Copperfen"):
        bot_says(api, private, f"@cpo {word}", refs={"task": tid})
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    assert wakes(api) == 1
    turn_task_private(api, tid)
    assert claim(api, r, "cpo") is None
    sweep(api)
    assert wakes(api) == 1 and waiting(api) == [] and claim(api, r, "cpo") is None


def test_a_notice_whose_message_was_deleted_stays_out_of_the_threads_next_run(api):
    r = bot_runner(api)
    assign(api, r, "ops")
    ready(api, r, ["cpo", "ops"])
    private = new_thread(api)
    first = say(api, private, "@cpo @ops Zebrawire")
    for bot in ("cpo", "ops"):
        finish(api, r, claim(api, r, bot))
    with api.app.state.store.transaction() as c:
        threads.delete_message(c, "human:ana", first["id"])
    say(api, private, "@cpo @ops next")
    for bot in ("cpo", "ops"):
        attempt = claim(api, r, bot)
        assert "Zebrawire" not in json.dumps(attempt) and attempt["message"]["body"].endswith("next")
        finish(api, r, attempt)


def test_a_refused_wake_with_nothing_left_to_carry_starts_nothing(api):
    r = bot_runner(api)
    private, public = new_thread(api), new_thread(api, audience="company", title="Handbook")
    a = bot_says(api, private, "@cpo Zebrawire")
    say(api, public, "@cpo work here")
    finish(api, r, claim(api, r, "cpo"))
    delete(api, a["id"])
    assert len(sweep(api)) == 1 and sweep(api) == []
    assert claim(api, r, "cpo") is None and wakes(api) == 1 and waiting(api) == []


def test_a_bot_removed_from_the_thread_gets_no_new_wake_and_loses_the_notes(api):
    r = bot_runner(api)
    private, _ = two_notes_and_a_wake(api, r)
    remove(api, private)                            # the claim itself refuses this one
    assert claim(api, r, "cpo") is None and claim(api, r, "cpo") is None
    assert wakes(api) == 1 and waiting(api) == []


def test_deleting_each_anchor_in_turn_ends_with_nothing_left_and_no_loop(api):
    r = bot_runner(api)
    private, a = two_notes_and_a_wake(api, r)
    third = bot_says(api, private, "@cpo Larkspur")
    delete(api, a["id"])
    assert len(sweep(api)) == 1
    b = rows(api, "SELECT message_id FROM notes WHERE body LIKE '%Quillframe%'")[0]["message_id"]
    delete(api, b)
    assert len(sweep(api)) == 1
    delete(api, third["id"])
    assert len(sweep(api)) == 1 and sweep(api) == []
    assert claim(api, r, "cpo") is None and wakes(api) == 3 and waiting(api) == []


# ----------------------------------------------------------------------------- a bot removed before its claim
def remove(api, thread_id, actor="bot:cpo", token="ana-test"):
    gone = api.delete(f"/api/v2/threads/{thread_id}/members/{actor}", headers=headers(token))
    assert gone.status_code == 200, gone.text


def addressed(api, thread_id, text="Zebrawire salary", bot="bot:cpo"):
    with api.app.state.store.transaction() as c:
        return H.say(c, "human:ana", bot, text, conversation_id=thread_id)


def job_of(api, message_id):
    return rows(api, "SELECT state, attempt_id FROM jobs WHERE message_id=?", message_id)[0]


def test_a_bot_removed_before_its_claim_gets_no_run_and_no_grant(api):
    r = bot_runner(api)
    thread = new_thread(api)
    message = addressed(api, thread)
    assert job_of(api, message["id"])["state"] == "queued"
    remove(api, thread)
    assert claim(api, r, "cpo") is None
    job = job_of(api, message["id"])
    assert job["state"] == "canceled" and job["attempt_id"] is None
    assert rows(api, "SELECT 1 FROM attempt_conversations WHERE conversation_id=?", thread) == []
    assert claim(api, r, "cpo") is None  # settled, not retried


def test_a_removed_bots_primary_message_does_not_fold_into_its_running_turn(api):
    r = bot_runner(api)
    thread = new_thread(api)
    say(api, thread, "@cpo First")
    attempt = claim(api, r, "cpo")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    message = addressed(api, thread)
    remove(api, thread)
    folded = post(api, f"attempts/{attempt['id']}/inputs", {}, token=r["token"])
    assert message["id"] not in [m["id"] for m in folded["messages"]] and "Zebrawire" not in json.dumps(folded)


def test_a_primary_message_a_member_still_holds_and_a_legacy_chat_still_claim(api):
    r = bot_runner(api)
    thread = new_thread(api)
    message = addressed(api, thread)
    attempt = claim(api, r, "cpo")
    assert attempt and attempt["conversation"]["id"] == thread
    assert job_of(api, message["id"])["state"] == "leased"
    finish(api, r, attempt)
    post(api, "chat/cpo", {"text": "Legacy word"})
    attempt = claim(api, r, "cpo")
    assert attempt and attempt["bot"] == "cpo"


def test_a_task_turn_still_claims(api):
    r = bot_runner(api)
    post(api, "tasks", {"owner": "cpo", "title": "Ship", "body": "Do it"})
    attempt = claim(api, r, "cpo")
    assert attempt and attempt["task"]["title"] == "Ship"
