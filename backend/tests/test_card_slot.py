"""A card's slot on Video Production (backend/card_slot.py): one run at a time per (visit, agent).

Replays of an OK that moves a card from Storyboard to Draft and a person's question about it, in either order and
across a runner restart: the runs go one at a time, and each replay gives one delivered draft, one charge per paid shot
and an answered question. Fencing: a run settles its own job alone, so a run that ends after the card moved on or
changed hands settles none of the work that came after it.

Synthetic rows only: the fictional company's bots `ops` and `cpo`, and Ana."""

import json
from types import SimpleNamespace

from backend import paid_renders, task_wakes
from backend import task_engine as E
from backend.run_diagnostics import RESTART_TEXT
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, post, ready, runner

ANA = "human:ana"
QUESTION = "Did you get my OK on the storyboard?"
ANSWER = "Yes, I have your OK."
DRAFT = "Draft cut: PP-06 v1."
VIDEO = (
    {"name": "Storyboard", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
)


def _card(api, name="Video Production"):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    with api.app.state.store.transaction() as c:
        X.pipeline(c, "video", name, VIDEO, manager=ANA)
        task = X.new_task(c, ANA, "PP-06 launch video", "video", assignee="bot:ops")
        X.settled(c)
    return machine, task


def _ok(api, task):
    """Ana OKs the storyboard: the card moves to Draft, and its owner's wake for Draft is written with the move."""
    with api.app.state.store.transaction() as c:
        E.move(c, task["id"], "Draft", ANA, why="Storyboard OK'd")
        return E.task(c, task["id"])["visit"]["id"]


def _ask(api, task):
    return post(api, f"conversations/{task['conversation_id']}/messages", {"text": QUESTION})["message"]["id"]


class Agent:
    """The bot, scripted: a run handed the card's Draft turn renders its one paid shot and delivers the draft; a run
    handed the question answers it."""

    def __init__(self, api, machine):
        self.api, self.machine = api, machine
        self.drafts, self.charges, self.answers = 0, 0, 0

    def start(self):
        run = claim(self.api, self.machine)
        assert run, "nothing to claim"
        post(self.api, f"attempts/{run['id']}/started", {"thread_id": "fixture-thread"}, self.machine["token"])
        return run

    def render(self, run):
        def submit(key):
            self.charges += 1
            return 202, {"id": "vid_" + key[-6:]}, ""

        who = SimpleNamespace(actor="bot:ops", attempt_id=run["id"])
        shot = paid_renders.shot_of({"prompt": "Opening shot, kitchen"}, "opening")
        paid_renders.send(self.api.app.state.store, who, shot, submit)

    def inputs(self, run):
        return [m["id"] for m in post(self.api, f"attempts/{run['id']}/inputs", {}, self.machine["token"])["messages"]]

    def finish(self, run):
        body, said = run["message"]["body"], []
        if run["message"]["refs"].get("wake") == "task_turn" and "Draft" in body:  # handed the card's Draft turn
            self.render(run)  # the same shot again is the one it already paid for (backend/paid_renders.py)
            with self.api.app.state.store.transaction() as c:
                room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (run["message"]["id"],)).fetchone()
                H.say(c, "bot:ops", ANA, DRAFT, conversation_id=room[0])
            self.drafts += 1
        if QUESTION in body:
            self.answers += 1
            said.append(ANSWER)
        done = {"outcome": "completed", "last_seq": 0, "text": " ".join(said) or "Nothing to do."}
        post(self.api, f"attempts/{run['id']}/complete", done, self.machine["token"], key="done-" + run["id"])


def _delivered(api, task, asked):
    """What was kept: (drafts delivered in the card's room, paid shots started for it, answers posted there, every
    question delivered)."""
    with api.app.state.store.read() as c:

        def posted(text):
            return c.execute(
                "SELECT count(*) FROM messages WHERE conversation_id=? AND sender_id='bot:ops' AND body LIKE ?",
                (task["conversation_id"], f"%{text}%"),
            ).fetchone()[0]

        shots = c.execute(
            "SELECT count(*) FROM events WHERE action='paid_render.started' AND json_extract(detail,'$.task')=?",
            (task["id"],),
        ).fetchone()[0]
        read = all(c.execute("SELECT delivered_at FROM messages WHERE id=?", (i,)).fetchone()[0] for i in asked)
        return posted(DRAFT), shots, posted(ANSWER), read


def _queue(api, task, sender, body, audience=None, **refs):
    """A message to `ops` in the card's room as its writer queues it (enqueue), past the wake rules a say goes through."""
    from backend.enqueue import enqueue_stored

    mid = H.new_id()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at,legacy_audience) "
            "VALUES(?,?,?,'bot:ops','notice',?,?,?,?)",
            (mid, task["conversation_id"], sender, body, json.dumps({"task": task["id"], **refs}), H.now(), audience),
        )
        enqueue_stored(c, mid)
    return mid


def _jobs(api, *ids):
    with api.app.state.store.read() as c:
        return {i: c.execute("SELECT state FROM jobs WHERE id=?", (i,)).fetchone()[0] for i in ids}


def _slot(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT slot FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def _wake(api, task, visit):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.wake_key LIKE ? AND j.bot_id='ops'",
            (f"{task['id']}:{visit}:%",),
        ).fetchone()[0]


def test_ok_then_chat_runs_one_at_a_time_with_one_draft_and_the_answer(api):
    machine, task = _card(api)
    draft = _ok(api, task)
    asked = _ask(api, task)
    agent = Agent(api, machine)
    chat = agent.start()
    assert chat["message"]["id"] == asked  # a person's chat goes first
    assert claim(api, machine) is None  # the card's Draft turn waits for it
    agent.finish(chat)
    work = agent.start()
    assert work["message"]["id"] == _wake(api, task, draft)
    agent.finish(work)
    assert claim(api, machine) is None
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    assert _delivered(api, task, [asked]) == (1, 1, 1, True)


def test_chat_then_ok_answers_in_its_own_run_and_drafts_once_after(api):
    machine, task = _card(api)
    asked = _ask(api, task)
    agent = Agent(api, machine)
    chat = agent.start()
    assert chat["message"]["id"] == asked
    draft = _ok(api, task)
    wake = _wake(api, task, draft)
    assert wake not in agent.inputs(chat)  # a slot's run carries its own job alone
    agent.finish(chat)
    assert _jobs(api, wake)[wake] == "queued"  # the question's run settled nothing of Draft
    work = agent.start()
    assert work["message"]["id"] == wake
    agent.finish(work)
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    assert _delivered(api, task, [asked]) == (1, 1, 1, True)


def test_chat_then_ok_both_queued_before_the_first_claim(api):
    machine, task = _card(api)
    asked = _ask(api, task)
    draft = _ok(api, task)  # while the computer is still waking
    agent = Agent(api, machine)
    for _ in range(2):
        agent.finish(agent.start())
    assert claim(api, machine) is None
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    assert _delivered(api, task, [asked]) == (1, 1, 1, True)
    assert _jobs(api, _wake(api, task, draft)) == {_wake(api, task, draft): "completed"}


def test_ok_and_chat_across_a_runner_restart(api):
    machine, task = _card(api)
    draft = _ok(api, task)
    agent = Agent(api, machine)
    first = agent.start()
    agent.render(first)  # the paid shot went out before the restart
    asked = _ask(api, task)
    assert asked not in agent.inputs(first)  # a comment during the run waits for its own
    # The runner restarts mid-run and says so; the sweep puts the run back in line.
    stopped = {"outcome": "interrupted", "last_seq": 0, "text": RESTART_TEXT}
    post(api, f"attempts/{first['id']}/complete", stopped, machine["token"])
    with api.app.state.store.transaction() as c:
        assert [d["state"] for d in api.app.state.execution.auto_reconcile(c)] == ["queued"]
    chat = agent.start()  # a person's chat goes first; the resumed Draft turn waits for its own run
    assert chat["message"]["id"] == asked
    assert _wake(api, task, draft) not in {m["id"] for m in chat["history"]}  # nor is it read in this one
    agent.finish(chat)
    agent.finish(agent.start())
    assert claim(api, machine) is None
    assert _jobs(api, _wake(api, task, draft), asked) == {_wake(api, task, draft): "completed", asked: "completed"}
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    assert _delivered(api, task, [asked]) == (1, 1, 1, True)


def test_a_run_that_ends_after_a_stage_move_settles_no_successor_work(api):
    machine, task = _card(api)
    with api.app.state.store.transaction() as c:
        row = E.task(c, task["id"])
        task_wakes.add(c, row, "bot:ops", row["visit"], "PP-06 is in Storyboard. Board it.", try_n=1)
    agent = Agent(api, machine)
    board = agent.start()
    draft = _ok(api, task)
    wake = _wake(api, task, draft)
    assert wake not in agent.inputs(board)
    agent.finish(board)
    assert _jobs(api, wake)[wake] == "queued"
    assert agent.start()["message"]["id"] == wake


def test_a_run_that_ends_after_a_reassign_settles_none_of_the_new_owners_work(api):
    machine, task = _card(api)
    _ask(api, task)
    agent = Agent(api, machine)
    run = agent.start()
    with api.app.state.store.transaction() as c:
        E.reassign(c, task["id"], "bot:cpo", ANA)
        theirs = c.execute("SELECT id, slot FROM jobs WHERE bot_id='cpo' AND state='queued'").fetchall()
    assert theirs and all(row[1] and row[1].endswith(":cpo") for row in theirs)
    agent.finish(run)
    assert set(_jobs(api, *(row[0] for row in theirs)).values()) == {"queued"}


def test_a_slot_run_takes_in_and_settles_no_notice_waiting_in_the_room(api):
    machine, task = _card(api)
    kept = _queue(api, task, H.TICO_ACTOR, "For Ana only.", audience='["human:ana","bot:ops"]')  # narrower: no slot
    asked = _ask(api, task)
    agent = Agent(api, machine)
    run = agent.start()
    assert run["message"]["id"] == asked
    notes = _queue(api, task, H.TICO_ACTOR, "Notes wait in this thread.", wake="thread_notes")  # never a slot's
    assert _slot(api, kept) is None and not {kept, notes} & set(agent.inputs(run))
    agent.finish(run)
    assert _jobs(api, kept, notes) == {kept: "queued", notes: "queued"}


def test_other_pipelines_keep_the_older_path(api):
    machine, task = _card(api, name="Video")
    draft = _ok(api, task)
    asked = _ask(api, task)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE slot IS NOT NULL").fetchone()[0] == 0
    agent = Agent(api, machine)
    assert agent.start()["message"]["id"] == asked
    assert _wake(api, task, draft)


def test_a_card_with_work_from_before_the_switch_finishes_it_on_the_older_path(api):
    machine, task = _card(api, name="Video")
    draft = _ok(api, task)  # queued before the pipeline switched
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET name='Video Production' WHERE id='video'")
    asked = _ask(api, task)
    assert _slot(api, asked) is None
    agent = Agent(api, machine)
    agent.finish(agent.start())
    agent.finish(agent.start())
    assert claim(api, machine) is None and (agent.drafts, agent.answers) == (1, 1)
    assert _slot(api, _ask(api, task)) == f"{draft}:ops"  # its older work settled: it runs by slot now


def test_unsettled_older_work_keeps_the_card_on_the_older_path(api):
    _, task = _card(api, name="Video")
    asked = _ask(api, task)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (asked,))  # a run held for review
        c.execute("UPDATE pipelines SET name='Video Production' WHERE id='video'")
    assert _slot(api, _ask(api, task)) is None


def test_a_command_waiting_in_the_room_keeps_the_card_on_its_slot(api):
    _, task = _card(api)
    draft = _ok(api, task)
    _queue(api, task, H.TICO_ACTOR, "/goal clear", command=True)  # never a slot's, so never older work
    assert _slot(api, _ask(api, task)) == f"{draft}:ops"


def test_a_reassign_during_older_work_stays_on_the_older_path_until_it_settles(api):
    machine, task = _card(api, name="Video")
    _ask(api, task)
    assert Agent(api, machine).start()  # the old owner's run, from before the switch
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET name='Video Production' WHERE id='video'")
        E.reassign(c, task["id"], "bot:cpo", ANA)
        theirs = c.execute("SELECT slot FROM jobs WHERE bot_id='cpo' AND state='queued'").fetchall()
    assert theirs and all(row[0] is None for row in theirs)


def test_a_private_chat_that_names_the_card_gets_no_slot(api):
    _, task = _card(api)
    with api.app.state.store.transaction() as c:
        private = H.say(c, ANA, "bot:ops", "Between us: is the budget fine?", refs={"task": task["id"]})["id"]
    assert _slot(api, private) is None


def test_a_private_cards_own_room_is_the_baseline(api):
    _, task = _card(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task["id"],))
    draft = _ok(api, task)
    assert _slot(api, _ask(api, task)) == f"{draft}:ops"


def test_a_card_comment_through_a_thread_carrier_joins_the_slot(api):
    from backend import threads

    _, task = _card(api)
    draft = _ok(api, task)
    with api.app.state.store.transaction() as c:
        said = H.say(c, ANA, "human:ben", QUESTION, conversation_id=task["conversation_id"])
        carried = threads._carrier(c, H.conversation(c, task["conversation_id"]), said, "bot:ops", "mention", None)
    assert _slot(api, carried) == f"{draft}:ops"


def test_a_carrier_of_a_message_some_reader_may_not_read_gets_no_slot(api):
    from backend import threads

    _, task = _card(api)
    _ok(api, task)
    with api.app.state.store.transaction() as c:
        said = H.say(c, ANA, "human:ben", "Kept from the room.", conversation_id=task["conversation_id"])
        c.execute("UPDATE messages SET legacy_audience=? WHERE id=?", ('["human:ana","bot:ops"]', said["id"]))
        carried = threads._carrier(c, H.conversation(c, task["conversation_id"]), said, "bot:ops", "mention", None)
    assert carried and _slot(api, carried) is None


def test_a_deferred_comments_deadline_run_joins_the_slot(api):
    from backend import delivery

    _, task = _card(api)
    draft = _ok(api, task)
    waiting = _queue(api, task, ANA, QUESTION, quiet=True)  # deferred: no run until its deadline
    with api.app.state.store.transaction() as c:
        delivery.defer(c, "bot:ops", "comment", waiting, conversation_id=task["conversation_id"], task_id=task["id"])
        job = delivery.now(c, "ops", task["conversation_id"])
    assert job == waiting and _slot(api, job) == f"{draft}:ops"


def test_a_fired_deferral_stays_with_its_own_run(api):
    from backend import delivery

    machine, task = _card(api)
    _ok(api, task)
    run = Agent(api, machine).start()  # the card's Draft turn
    waiting = _queue(api, task, ANA, QUESTION, quiet=True)
    with api.app.state.store.transaction() as c:
        delivery.defer(c, "bot:ops", "comment", waiting, conversation_id=task["conversation_id"], task_id=task["id"])
        assert delivery.now(c, "ops", task["conversation_id"]) == waiting
        # Another turn's claim in the same room carries what is deferred there: not this.
        delivery.carried(c, "ops", run["id"], [task["conversation_id"]], job=run["job_id"])
        assert c.execute("SELECT settled_at FROM deferrals WHERE item=?", (waiting,)).fetchone()[0] is None
    assert _jobs(api, waiting)[waiting] == "queued"


def test_older_work_kept_from_some_of_the_room_does_not_hold_the_card(api):
    from backend import threads

    _, task = _card(api, name="Video")
    with api.app.state.store.transaction() as c:
        said = H.say(c, ANA, "human:ben", "Kept from the room.", conversation_id=task["conversation_id"])
        carried = threads._carrier(c, H.conversation(c, task["conversation_id"]), said, "bot:ops", "mention", None)
        # Its source is kept from some of the room's readers: neither it nor its carrier is the card's work.
        c.execute("UPDATE messages SET legacy_audience=? WHERE id=?", ('["human:ana","bot:ops"]', said["id"]))
        c.execute("UPDATE pipelines SET name='Video Production' WHERE id='video'")
    assert carried and _slot(api, carried) is None
    assert _slot(api, _ask(api, task))


def _private_card_and_secret(api):
    """A private card, and another private task some of its readers may not read."""
    machine, task = _card(api)
    with api.app.state.store.transaction() as c:
        secret = X.new_task(c, "human:ben", "Ben's salary review", assignee="human:ben")
        c.execute("UPDATE tasks SET private=1 WHERE id IN (?,?)", (task["id"], secret["id"]))
    return machine, task, secret


def test_in_a_private_cards_room_a_message_drawing_on_another_private_task_gets_no_slot(api):
    _, task, secret = _private_card_and_secret(api)
    draft = _ok(api, task)
    drawn = _queue(api, task, H.TICO_ACTOR, "From the salary review.", source={"task": secret["id"]})
    assert _slot(api, drawn) is None
    assert _slot(api, _ask(api, task)) == f"{draft}:ops"  # nor does it hold the card on the older path


def test_a_slot_run_reads_no_deferred_item_of_a_waiting_deadline_run(api):
    from backend import delivery

    machine, task = _card(api)
    draft = _ok(api, task)
    first = _queue(api, task, "bot:cpo", "The music is licensed.", quiet=True)
    second = _queue(api, task, "bot:cpo", "The voice-over is booked.", quiet=True)
    with api.app.state.store.transaction() as c:
        for item in (first, second):
            delivery.defer(c, "bot:ops", "comment", item, conversation_id=task["conversation_id"], task_id=task["id"])
        assert delivery.now(c, "ops", task["conversation_id"]) == second  # one deadline run carries both
    run = claim(api, machine)
    assert run["message"]["id"] == _wake(api, task, draft)
    assert not {first, second} & {m["id"] for m in run["history"]}


def test_a_narrower_deferred_item_keeps_its_own_deadline_run(api):
    from backend import delivery

    _, task = _card(api)
    _ok(api, task)
    kept = _queue(api, task, ANA, "For Ana only.", audience='["human:ana","bot:ops"]', quiet=True)
    open_ = _queue(api, task, ANA, QUESTION, quiet=True)
    with api.app.state.store.transaction() as c:
        for item in (kept, open_):
            delivery.defer(c, "bot:ops", "comment", item, conversation_id=task["conversation_id"], task_id=task["id"])
        job = delivery.now(c, "ops", task["conversation_id"])
        fired = dict(c.execute("SELECT item, fired_job FROM deferrals WHERE item IN (?,?)", (kept, open_)).fetchall())
    assert job == open_ and _slot(api, job) and fired == {kept: None, open_: open_}
