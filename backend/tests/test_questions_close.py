"""A bot's question to a person closes on its own (Decide cards part 1, Oct 21, 2026): with its task or any task above
it, on a chat reply, on its fallback date, or back to its bot as "still needed?" after two business days."""

from backend import approval_expiry, ask_deadlines, still_needed
from backend.store import H
from backend.tests.test_tasks_board import api  # noqa: F401

ANA, OPS = "human:ana", "bot:ops"


def pick(c, task_id, text="Which month should the report use?", refs=None, default=None):
    ask = {"questions": [{"id": "month", "header": "Month", "question": text, "multi": False, "other": True,
                          "options": [{"label": "July", "description": ""}, {"label": "August", "description": ""}]}],
           "who": ANA, **({"if_no_answer": default} if default else {})}
    return H.task_comment(c, OPS, task_id, text, wake=False, ask=ask, extra_refs=refs)


def listed(c, task_id):
    return task_id in {t["id"] for t in H.needs_you(c, ANA)["tasks"]}


def wakes(c, message_id):
    return c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (message_id,)).fetchone()[0]


def test_a_question_closes_when_its_task_or_any_task_above_it_ends(api):
    with api.app.state.store.transaction() as c:
        top = H.task_create(c, ANA, "Plan the offsite", "x", OPS)
        mid = H.task_create(c, OPS, "Book the venue", "x", OPS, parent_id=top["id"])
        sub = H.task_create(c, OPS, "Pick the menu", "x", OPS, parent_id=mid["id"])
        other = H.task_create(c, OPS, "Pick the hotel", "x", OPS, parent_id=mid["id"])
        done_q, deep_q = pick(c, sub["id"]), pick(c, other["id"])
        assert listed(c, sub["id"]) and listed(c, other["id"])
        H.task_update(c, OPS, sub["id"], status="done", note="Menu picked.")      # its own task done: gone
        assert H.message(c, done_q["id"])["superseded_at"] and not listed(c, sub["id"])
        H.task_close(c, ANA, top["id"])                                            # the grandparent closed: gone
        assert H.message(c, deep_q["id"])["superseded_at"] and not listed(c, other["id"])
        # One asked under a task that already ended (or a keeper "no progress" question from before stuck checks went
        # to BotOps) leaves at the hourly sweep.
        late = H.task_create(c, OPS, "Pick the wine", "x", OPS, parent_id=mid["id"])
        late_q = pick(c, late["id"])
        live = H.task_create(c, ANA, "Draft the agenda", "x", OPS)
        stuck = H.task_comment(c, H.KEEPER, live["id"], "No progress for 30 min after a status check.", wake=False,
                               ask={"questions": [{"id": "stuck", "header": "Stuck", "question": "What should it do?",
                                                   "options": [{"label": "Keep going"}, {"label": "Stop"}],
                                                   "multi": False, "other": True}], "who": ANA},
                               extra_refs={"stuck": True})
        kept = pick(c, live["id"])
        swept = approval_expiry.sweep(c)["asks"]
        assert set(swept) == {late_q["id"], stuck["id"]} and not H.message(c, kept["id"])["superseded_at"]
        assert approval_expiry.sweep(c)["asks"] == []


def test_a_chat_reply_answers_a_pick_question_and_wakes_its_bot_once(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Draft the board report", "x", OPS)
        asked = pick(c, work["id"])
        reply = H.say(c, ANA, OPS, "Use August, it has the full close.", conversation_id=work["conversation_id"],
                      refs={"task": work["id"]})
        assert H.message(c, asked["id"])["answered_by"] == reply["id"]       # the reply is recorded as the answer
        assert not listed(c, work["id"]) and wakes(c, reply["id"]) == 1


def test_a_question_closes_on_its_fallback_date_even_when_its_fallback_waits(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Tell the vendor", "x", OPS)
        asked = pick(c, work["id"], "Email the vendor the new date?",
                     default={"do": "Email them", "kind": "send", "by": H.shift(H.now(), days=1)})
        assert ask_deadlines.sweep(c)["expired"] == []                          # not yet
        assert ask_deadlines.sweep(c, H.shift(H.now(), days=2))["expired"] == [asked["id"]]
        answer = c.execute("SELECT * FROM messages WHERE in_reply_to=? AND kind='answer'", (asked["id"],)).fetchall()
        assert len(answer) == 1 and answer[0]["to_actor"] == OPS and "never goes ahead" in answer[0]["body"]
        assert wakes(c, answer[0]["id"]) == 1 and not listed(c, work["id"])


def test_unanswered_questions_go_back_to_their_bot_in_one_weekday_morning_wake(api):
    friday = "2026-10-16T17:00:00.000000Z"              # 10:00 Pacific
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, ANA, f"Pick a {what}", "x", OPS) for what in ("venue", "caterer", "date")]
        asks = [pick(c, t["id"], f"Which {t['title'][7:]}?") for t in tasks]
        c.executemany("UPDATE messages SET created=? WHERE id=?", [(friday, a["id"]) for a in asks])
        assert still_needed.crossed_at(friday).isoformat().startswith("2026-10-20T10:00")    # the weekend skipped
        monday, tuesday, wednesday = ("2026-10-19T17:00:00.000000Z", "2026-10-20T15:30:00.000000Z",
                                      "2026-10-21T15:30:00.000000Z")      # Mon 10:00, Tue 8:30, Wed 8:30 Pacific
        assert still_needed.sweep(c, monday) == {} and still_needed.sweep(c, tuesday) == {}
        assert all(listed(c, t["id"]) for t in tasks)
        sent = still_needed.sweep(c, wednesday)
        assert list(sent) == [OPS] and set(sent[OPS]) == {a["id"] for a in asks}
        assert not any(listed(c, t["id"]) for t in tasks)
        wake = c.execute("SELECT id, body FROM messages WHERE to_actor=? AND body LIKE 'Still needed?%'", (OPS,)).fetchall()
        assert len(wake) == 1 and all(f"Which {t['title'][7:]}?" in wake[0]["body"] for t in tasks)
        assert wakes(c, wake[0]["id"]) == 1
        assert still_needed.sweep(c, "2026-10-21T18:00:00.000000Z") == {}      # once a morning
        again = pick(c, tasks[0]["id"], "Which venue? The Hall or the Barn.")     # re-asked; the other two dropped
        assert [t["id"] for t in tasks if listed(c, t["id"])] == [tasks[0]["id"]]
        assert H.open_task_asks(c, H.task(c, tasks[0]["id"]))[0]["id"] == again["id"]
        assert H.ask_clock(H.message(c, again["id"])) > friday
