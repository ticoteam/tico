"""A bot's question task to a person closes on its own (Decide cards part 1, Oct 21, 2026): with the task it was asked
under, or back to its bot as "still needed?" after two business days (its fallback date is in test_ask_deadlines)."""

from datetime import datetime, timezone

from backend import question_tasks as QT, still_needed, task_roundup
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_tasks_board import api, get  # noqa: F401

ANA, OPS, CPO = "human:ana", "bot:ops", "bot:cpo"
NOON_WED = "2026-10-21T19:00:00.000000Z"       # Wed 12:00 Pacific: a task roundup run
EVENING_WED = "2026-10-22T01:00:00.000000Z"    # Wed 18:00 Pacific: the next one


def pick(c, task_id, text="Which month should the report use?", by=OPS):
    """`by` asks Ana a question task under the task."""
    return QT.ask_plain(c, by, ANA, text, parent=H.task(c, task_id))


def age(c, card, when):
    """The question was asked at `when`."""
    c.execute("UPDATE tasks SET created_at=?, asked_at=? WHERE id=?", (when, when, card["id"]))


def listed(c, card_id):
    raw = H.needs_you(c, ANA)
    return card_id in {t["id"] for t in raw["tasks"] + raw["waiting"]}


def needs(api):
    """Ana's whole Needs you over the API: every item's id, and its count."""
    return {i["id"] for i in get(api, "needs-you")["items"]}, get(api, "needs-you?count=1")["count"]


def cached_matches(c):
    """The asking bot's cached needs_human matches a fresh count."""
    return c.execute("SELECT needs_human FROM bot_status WHERE bot='ops'").fetchone()[0] == H.status_counts(c, OPS)[1]


def wakes(c, message_id):
    return c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (message_id,)).fetchone()[0]


def roundup_messages(c, bot):
    """The task roundup lists sent to a bot, oldest first."""
    return c.execute("SELECT id, body FROM messages WHERE recipient_id=? AND refs LIKE '%task_roundup%' "
                     "ORDER BY created_at", (bot,)).fetchall()


def gone_back_line(title):
    """The roundup line for a question asked on a task titled `Pick a <what>`, asked as "Which <what>?"."""
    return f'no answer in two business days to "Which {title[7:]}?"'


def test_a_question_closes_with_the_task_it_was_asked_under_or_an_ancestor(api):
    with api.app.state.store.transaction() as c:
        top = H.task_create(c, ANA, "Plan the offsite", "x", OPS)
        mid = H.task_create(c, OPS, "Book the venue", "x", OPS, parent_id=top["id"])
        sub = H.task_create(c, OPS, "Pick the menu", "x", OPS, parent_id=mid["id"])
        other = H.task_create(c, OPS, "Pick the hotel", "x", OPS, parent_id=mid["id"])
        sub_q, deep_q = pick(c, sub["id"]), pick(c, other["id"])
        assert listed(c, sub_q["id"]) and listed(c, deep_q["id"])
        H.task_close(c, ANA, sub["id"])                                 # its own task closed: it closes with it
        assert not QT.is_open(H.task(c, sub_q["id"])) and not listed(c, sub_q["id"])
        assert listed(c, deep_q["id"])
        H.task_close(c, ANA, top["id"])                                 # the grandparent closes the open subtree
        assert not QT.is_open(H.task(c, deep_q["id"])) and not listed(c, deep_q["id"])


def test_unanswered_questions_go_back_to_their_bot_in_one_weekday_morning_wake(api):
    friday = "2026-10-16T17:00:00.000000Z"              # 10:00 Pacific
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, ANA, f"Pick a {what}", "x", OPS) for what in ("venue", "caterer", "date")]
        asks = [pick(c, t["id"], f"Which {t['title'][7:]}?") for t in tasks]
        for a in asks:
            age(c, a, friday)
        assert still_needed.crossed_at(friday).isoformat().startswith("2026-10-20T10:00")    # the weekend skipped
        monday, tuesday, wednesday = ("2026-10-19T17:00:00.000000Z", "2026-10-20T15:30:00.000000Z",
                                      "2026-10-21T15:30:00.000000Z")      # Mon 10:00, Tue 8:30, Wed 8:30 Pacific
        assert still_needed.sweep(c, monday) == {} and still_needed.sweep(c, tuesday) == {}
        assert all(listed(c, a["id"]) for a in asks)
        sent = still_needed.sweep(c, wednesday)
        assert list(sent) == [OPS] and set(sent[OPS]) == {a["id"] for a in asks}
        assert not any(listed(c, a["id"]) for a in asks)
        assert still_needed.sweep(c, "2026-10-21T18:00:00.000000Z") == {}      # once a morning
        # No wake of their own: the bot's noon roundup names each one, once, in its single list.
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")      # the creation wakes ran
        assert "ops" in task_roundup.run(c, NOON_WED)["woke"]
        found = roundup_messages(c, OPS)
        assert len(found) == 1 and wakes(c, found[0]["id"]) == 1
        assert all(found[0]["body"].count(gone_back_line(t["title"])) == 1 for t in tasks)
        assert "ops" not in task_roundup.run(c, EVENING_WED)["woke"], "not listed again"
        again = pick(c, tasks[0]["id"], "Which venue? The Hall or the Barn.")     # re-asked; the other two dropped
        assert [a["id"] for a in asks + [again] if listed(c, a["id"])] == [again["id"]]
        assert H.unanswered_ask(c, H.task(c, tasks[0]["id"]))["id"] == again["id"]
        assert QT.asked_at(H.task(c, again["id"])) > friday


def test_closing_a_parent_closes_the_questions_below_it(api):
    with api.app.state.store.transaction() as c:
        top = H.task_create(c, ANA, "Plan the launch", "x", OPS)
        now_ = H.task_create(c, OPS, "Pick the launch date", "x", OPS, parent_id=top["id"])
        asked = pick(c, now_["id"])
        c.execute("INSERT INTO bot_status (bot, state, changed_at, updated_at) VALUES ('ops', 'idle', ?, ?)", (H.now(), H.now()))
        H._recount(c, OPS)
        assert H.status_counts(c, OPS)[1] and cached_matches(c)
    before, count = needs(api)
    assert asked["id"] in before and count == len(before)
    with api.app.state.store.transaction() as c:
        H.task_close(c, ANA, top["id"])                         # its descendants and their questions close with it
    after, count = needs(api)
    assert asked["id"] not in after and count == len(after)
    with api.app.state.store.transaction() as c:
        assert H.task(c, now_["id"]).get("waiting_on") is None and cached_matches(c)    # the bot's count follows


def test_old_undated_questions_go_back_only_in_the_weekday_batch_of_the_hourly_sweep(api):
    store, execution = api.app.state.store, api.app.state.execution
    with store.transaction() as c:
        tasks = [H.task_create(c, ANA, f"Pick a {what}", "x", OPS) for what in ("venue", "caterer", "date")]
        asks = [pick(c, t["id"], f"Which {t['title'][7:]}?") for t in tasks]
        old = "2026-09-24T17:00:00.000000Z"                                # weeks before, past the old 14-day expiry
        for a in asks:
            age(c, a, old)
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task'", (old,))

    def tick(at):
        Scheduler(store, execution).tick(datetime.fromisoformat(at).replace(tzinfo=timezone.utc))
        with store.read() as c:
            return [a["id"] for a in asks if not QT.is_open(H.task(c, a["id"]))]

    assert tick("2026-10-17T17:00:00") == []           # Saturday: no batch, and no question expires on its own
    assert tick("2026-10-19T14:00:00") == []           # Monday 7:00 Pacific: before the batch
    assert set(tick("2026-10-19T15:30:00")) == {a["id"] for a in asks}   # Monday 8:30 Pacific: all three go back
    with store.transaction() as c:
        assert not any(listed(c, a["id"]) for a in asks)
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")  # the creation wakes ran
        task_roundup.run(c, "2026-10-19T19:00:00.000000Z")                 # Monday 12:00 Pacific: the roundup
        found = roundup_messages(c, OPS)
        assert all(sum(body.count(gone_back_line(t["title"])) for body in [r["body"] for r in found]) == 1 for t in tasks)


def asking(c):
    """The asking bot's cached needs_human. CPO asks on the tasks it filed for Ops below its own part of Ana's task, so
    closing Ana's task recounts Ana and Ops but only a recount of the asker moves this."""
    return c.execute("SELECT needs_human FROM bot_status WHERE bot='cpo'").fetchone()[0]


def test_a_bulk_close_recounts_the_asking_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_status(bot,state) VALUES('cpo','idle')")
        top = H.task_create(c, ANA, "Plan the offsite", "x", OPS)
        mid = H.task_create(c, OPS, "Book the venue", "x", CPO, parent_id=top["id"])
        sub = H.task_create(c, CPO, "Pick the menu", "x", OPS, parent_id=mid["id"])
        pick(c, sub["id"], by=CPO)
        H._recount(c, CPO)
        assert asking(c) == 1
        H.task_close(c, ANA, top["id"])                                 # the cascade recounts the asking bot
        assert asking(c) == 0
        H.task_close(c, ANA, sub["id"])                                 # an already closed subtask stays closed
        assert asking(c) == 0


def test_still_needed_keeps_the_wait_while_another_question_to_the_same_person_is_open(api):
    friday = "2026-10-16T17:00:00.000000Z"
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Pick a venue", "x", OPS)
        old = pick(c, work["id"], "Which venue?")
        age(c, old, friday)
        # A second bot's newer question to Ana on the same task.
        fresh = pick(c, work["id"], "Which budget?", by=CPO)
        age(c, fresh, "2026-10-21T15:00:00.000000Z")
        assert still_needed.sweep(c, "2026-10-21T15:30:00.000000Z") == {OPS: [old["id"]]}
        # Ana's other question on the task stays open, and so in her Needs you.
        assert H.unanswered_ask(c, H.task(c, work["id"]))["id"] == fresh["id"] and listed(c, fresh["id"])
