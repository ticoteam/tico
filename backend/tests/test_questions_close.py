"""A bot's question to a person closes on its own (Decide cards part 1, Oct 21, 2026): with its own task (never with a
task above it, Dev 627), on a chat reply, on its fallback date, or back to its bot as "still needed?" after two business days."""

from datetime import datetime, timezone

from backend import approval_expiry, ask_deadlines, still_needed
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_tasks_board import api, get, post  # noqa: F401

ANA, OPS, CPO = "human:ana", "bot:ops", "bot:cpo"


def pick(c, task_id, text="Which month should the report use?", refs=None, default=None, by=OPS):
    ask = {"questions": [{"id": "month", "header": "Month", "question": text, "multi": False, "other": True,
                          "options": [{"label": "July", "description": ""}, {"label": "August", "description": ""}]}],
           "who": ANA, **({"if_no_answer": default} if default else {})}
    return H.task_comment(c, by, task_id, text, wake=False, ask=ask, extra_refs=refs)


def listed(c, task_id):
    raw = H.needs_you(c, ANA)
    return task_id in {t["id"] for t in raw["tasks"] + raw["waiting"]}


def needs(api):
    """Ana's whole Needs you over the API: every item's id, and its count."""
    return {i["id"] for i in get(api, "needs-you")["items"]}, get(api, "needs-you?count=1")["count"]


def waiting_on_ana(c, task_id):
    H.task_update(c, OPS, task_id, status="waiting", waiting_on=ANA, note="Waiting on Ana's pick.")


def cached_matches(c):
    """The asking bot's cached needs_human matches a fresh count."""
    return c.execute("SELECT needs_human FROM bot_status WHERE bot='ops'").fetchone()[0] == H.status_counts(c, OPS)[1]


def wakes(c, message_id):
    return c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (message_id,)).fetchone()[0]


def test_a_question_closes_with_its_own_task_and_outlives_a_task_above_it(api):
    with api.app.state.store.transaction() as c:
        top = H.task_create(c, ANA, "Plan the offsite", "x", OPS)
        mid = H.task_create(c, OPS, "Book the venue", "x", OPS, parent_id=top["id"])
        sub = H.task_create(c, OPS, "Pick the menu", "x", OPS, parent_id=mid["id"])
        other = H.task_create(c, OPS, "Pick the hotel", "x", OPS, parent_id=mid["id"])
        done_q, deep_q = pick(c, sub["id"]), pick(c, other["id"])
        assert listed(c, sub["id"]) and listed(c, other["id"])
        H.task_update(c, OPS, sub["id"], status="done", note="Menu picked.")      # its own task done: gone
        assert H.message(c, done_q["id"])["superseded_at"] and not listed(c, sub["id"])
        H.task_close(c, ANA, top["id"])                                 # the grandparent closed: its own task is open
        assert not H.message(c, deep_q["id"])["superseded_at"] and listed(c, other["id"])
        # One whose own task ended without the write path taking it (or a keeper "no progress" question from before
        # stuck checks went to BotOps) leaves at the hourly sweep; one below a finished task stays.
        late = H.task_create(c, OPS, "Pick the wine", "x", OPS, parent_id=mid["id"])
        late_q = pick(c, late["id"])
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (late["id"],))
        live = H.task_create(c, ANA, "Draft the agenda", "x", OPS)
        stuck = H.task_comment(c, H.KEEPER, live["id"], "No progress for 30 min after a status check.", wake=False,
                               ask={"questions": [{"id": "stuck", "header": "Stuck", "question": "What should it do?",
                                                   "options": [{"label": "Keep going"}, {"label": "Stop"}],
                                                   "multi": False, "other": True}], "who": ANA},
                               extra_refs={"stuck": True})
        kept = pick(c, live["id"])
        swept = approval_expiry.sweep(c)["asks"]
        assert set(swept) == {late_q["id"], stuck["id"]} and not H.message(c, kept["id"])["superseded_at"]
        assert not H.message(c, deep_q["id"])["superseded_at"]
        assert approval_expiry.sweep(c)["asks"] == []


def test_a_chat_note_keeps_a_pick_question_open_and_wakes_its_bot_once(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Draft the board report", "x", OPS)
        asked = pick(c, work["id"])
        reply = H.say(c, ANA, OPS, "Use August, it has the full close.", conversation_id=work["conversation_id"],
                      refs={"task": work["id"]})
        assert H.message(c, asked["id"])["answered_by_id"] is None
        assert listed(c, work["id"]) and wakes(c, reply["id"]) == 1


def test_a_question_closes_on_its_fallback_date_even_when_its_fallback_waits(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Tell the vendor", "x", OPS)
        asked = pick(c, work["id"], "Email the vendor the new date?",
                     default={"do": "Email them", "kind": "send", "by": H.shift(H.now(), days=1)})
        assert ask_deadlines.sweep(c)["expired"] == []                          # not yet
        assert ask_deadlines.sweep(c, H.shift(H.now(), days=2))["expired"] == [asked["id"]]
        answer = c.execute("SELECT * FROM messages WHERE in_reply_to=? AND kind='answer'", (asked["id"],)).fetchall()
        assert len(answer) == 1 and answer[0]["recipient_id"] == OPS and "never goes ahead" in answer[0]["body"]
        assert wakes(c, answer[0]["id"]) == 1 and not listed(c, work["id"])


def test_unanswered_questions_go_back_to_their_bot_in_one_weekday_morning_wake(api):
    friday = "2026-10-16T17:00:00.000000Z"              # 10:00 Pacific
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, ANA, f"Pick a {what}", "x", OPS) for what in ("venue", "caterer", "date")]
        asks = [pick(c, t["id"], f"Which {t['title'][7:]}?") for t in tasks]
        c.executemany("UPDATE messages SET created_at=? WHERE id=?", [(friday, a["id"]) for a in asks])
        assert still_needed.crossed_at(friday).isoformat().startswith("2026-10-20T10:00")    # the weekend skipped
        monday, tuesday, wednesday = ("2026-10-19T17:00:00.000000Z", "2026-10-20T15:30:00.000000Z",
                                      "2026-10-21T15:30:00.000000Z")      # Mon 10:00, Tue 8:30, Wed 8:30 Pacific
        assert still_needed.sweep(c, monday) == {} and still_needed.sweep(c, tuesday) == {}
        assert all(listed(c, t["id"]) for t in tasks)
        sent = still_needed.sweep(c, wednesday)
        assert list(sent) == [OPS] and set(sent[OPS]) == {a["id"] for a in asks}
        assert not any(listed(c, t["id"]) for t in tasks)
        wake = c.execute("SELECT id, body FROM messages WHERE recipient_id=? AND body LIKE 'Still needed?%'", (OPS,)).fetchall()
        assert len(wake) == 1 and all(f"Which {t['title'][7:]}?" in wake[0]["body"] for t in tasks)
        assert wakes(c, wake[0]["id"]) == 1
        assert still_needed.sweep(c, "2026-10-21T18:00:00.000000Z") == {}      # once a morning
        again = pick(c, tasks[0]["id"], "Which venue? The Hall or the Barn.")     # re-asked; the other two dropped
        assert [t["id"] for t in tasks if listed(c, t["id"])] == [tasks[0]["id"]]
        assert H.open_task_asks(c, H.task(c, tasks[0]["id"]))[0]["id"] == again["id"]
        assert H.ask_clock(H.message(c, again["id"])) > friday


def test_ending_a_task_above_keeps_the_question_and_wait_on_a_task_below_and_its_own_end_takes_both(api):
    with api.app.state.store.transaction() as c:
        top = H.task_create(c, ANA, "Plan the launch", "x", OPS)
        now_ = H.task_create(c, OPS, "Pick the launch date", "x", OPS, parent_id=top["id"])
        late = H.task_create(c, OPS, "Pick the launch venue", "x", OPS, parent_id=now_["id"])
        pick(c, now_["id"])
        waiting_on_ana(c, now_["id"])
        c.execute("INSERT INTO bot_status (bot, state, changed_at, updated_at) VALUES ('ops', 'idle', ?, ?)", (H.now(), H.now()))
        H._recount(c, OPS)
        assert H.status_counts(c, OPS)[1] and cached_matches(c)
    before, count = needs(api)
    assert now_["id"] in before and count == len(before)
    with api.app.state.store.transaction() as c:
        H.task_close(c, ANA, top["id"])                         # a task above ended: the question and its wait stay
    after, count = needs(api)
    assert now_["id"] in after and count == len(after)
    with api.app.state.store.transaction() as c:
        assert H.task(c, now_["id"])["waiting_on"] == ANA and cached_matches(c)
        # Its own task ended without the write path taking it: the hourly sweep takes the question and its wait.
        pick(c, late["id"])
        waiting_on_ana(c, late["id"])
        assert listed(c, late["id"])
        c.execute("UPDATE tasks SET status='declined' WHERE id=?", (late["id"],))
        approval_expiry.sweep(c)
        assert not listed(c, late["id"]) and not H.open_task_asks(c, H.task(c, late["id"])) and cached_matches(c)
    assert late["id"] not in needs(api)[0]


def test_a_dated_question_closes_on_its_date_after_a_clarify_without_going_ahead(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Tidy the old drafts", "x", OPS)
        asked = pick(c, work["id"], "Archive the old drafts?",
                     default={"do": "July", "kind": "reversible", "by": H.shift(H.now(), days=10)})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-4), asked["id"]))
    post(api, "needs-you/clarify", {"ask": asked["id"]})                       # Ana's word: no answer, no go-ahead
    with api.app.state.store.transaction() as c:
        assert ask_deadlines.sweep(c, H.shift(H.now(), days=11)) == {"went_ahead": [], "expired": [asked["id"]],
                                                                        "closed": []}
        answer = c.execute("SELECT * FROM messages WHERE in_reply_to=? AND kind='answer'", (asked["id"],)).fetchall()
        assert len(answer) == 1 and "does not happen on its own" in answer[0]["body"]
        assert "answer" not in H._json(answer[0]["refs"], {})              # no option picked for her
        assert wakes(c, answer[0]["id"]) == 1 and not listed(c, work["id"])


def test_old_undated_questions_go_back_only_in_the_weekday_batch_of_the_hourly_sweep(api):
    store, execution = api.app.state.store, api.app.state.execution
    with store.transaction() as c:
        tasks = [H.task_create(c, ANA, f"Pick a {what}", "x", OPS) for what in ("venue", "caterer", "date")]
        asks = [pick(c, t["id"], f"Which {t['title'][7:]}?") for t in tasks]
        old = "2026-09-24T17:00:00.000000Z"                                # weeks before, past the old 14-day expiry
        c.executemany("UPDATE messages SET created_at=? WHERE id=?", [(old, a["id"]) for a in asks])
        c.execute("UPDATE task_events SET created_at=?", (old,))

    def tick(at):
        Scheduler(store, execution).tick(datetime.fromisoformat(at).replace(tzinfo=timezone.utc))
        with store.read() as c:
            return [r[0] for r in c.execute("SELECT body FROM messages WHERE recipient_id=? AND sender_id=? AND "
                                            "(kind='answer' OR body LIKE 'Still needed?%')", (OPS, H.KEEPER))]

    assert tick("2026-10-17T17:00:00") == []           # Saturday: no batch, and no ask expires on its own
    assert tick("2026-10-19T14:00:00") == []           # Monday 7:00 Pacific: before the batch
    sent = tick("2026-10-19T15:30:00")                 # Monday 8:30 Pacific: one wake with all three
    assert len(sent) == 1 and all(f"Which {t['title'][7:]}?" in sent[0] for t in tasks)
    with store.read() as c:
        assert not any(listed(c, t["id"]) for t in tasks)


def asking(c):
    """The asking bot's cached needs_human. CPO asks on the tasks it filed for Ops below its own part of Ana's task, so
    closing Ana's task recounts Ana and Ops but only a recount of the asker moves this."""
    return c.execute("SELECT needs_human FROM bot_status WHERE bot='cpo'").fetchone()[0]


def test_bulk_closes_recount_the_asking_bot_and_one_bad_row_does_not_undo_the_sweep(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_status(bot,state) VALUES('cpo','idle')")
        top = H.task_create(c, ANA, "Plan the offsite", "x", OPS)
        mid = H.task_create(c, OPS, "Book the venue", "x", CPO, parent_id=top["id"])
        sub = H.task_create(c, CPO, "Pick the menu", "x", OPS, parent_id=mid["id"])
        pick(c, sub["id"], by=CPO)
        H._recount(c, CPO)
        assert asking(c) == 1
        H.task_close(c, ANA, top["id"])                                 # a task above ending keeps the question
        assert asking(c) == 1
        H.task_close(c, ANA, sub["id"])                                 # its own task ending recounts the asker
        assert asking(c) == 0
        # Asked on tasks that ended without the write path: the hourly sweep takes them one row at a time.
        bad, good = (H.task_create(c, CPO, f"Pick the {what}", "x", OPS, parent_id=mid["id"]) for what in ("wine", "band"))
        bad_q, good_q = pick(c, bad["id"], by=CPO), pick(c, good["id"], by=CPO)
        H._recount(c, CPO)
        assert asking(c) == 2
        c.execute("UPDATE tasks SET status='closed' WHERE id IN (?,?)", (bad["id"], good["id"]))
        drop = H.drop_asked_waits

        def failing(conn, task_id, *args):
            if task_id == bad["id"]:
                raise ValueError("bad row")
            return drop(conn, task_id, *args)

        monkeypatch.setattr(H, "drop_asked_waits", failing)
        assert approval_expiry.sweep(c)["asks"] == [good_q["id"]]
        assert H.message(c, good_q["id"])["superseded_at"] and not H.message(c, bad_q["id"])["superseded_at"]
        assert asking(c) == 0                                           # the sweep recounted the asker
        monkeypatch.setattr(H, "drop_asked_waits", drop)
        assert approval_expiry.sweep(c)["asks"] == [bad_q["id"]] and asking(c) == 0


def test_still_needed_keeps_the_wait_while_another_question_to_the_same_person_is_open(api):
    friday = "2026-10-16T17:00:00.000000Z"
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Pick a venue", "x", OPS)
        old = pick(c, work["id"], "Which venue?")
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (friday, old["id"]))
        waiting_on_ana(c, work["id"])
        # A second bot's newer question to Ana on the same task (one bot's newer question would replace its older one).
        fresh = H.task_comment(c, CPO, work["id"], "Which budget?", wake=False, ask={
            "questions": [{"id": "budget", "header": "Budget", "question": "Which budget?", "multi": False,
                           "other": True, "options": [{"label": "Low", "description": ""},
                                                      {"label": "High", "description": ""}]}], "who": ANA})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", ("2026-10-21T15:00:00.000000Z", fresh["id"]))
        assert still_needed.sweep(c, "2026-10-21T15:30:00.000000Z") == {OPS: [old["id"]]}
        assert H.task(c, work["id"])["waiting_on"] == ANA and listed(c, work["id"])
