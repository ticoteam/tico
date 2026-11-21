"""A card's slot on Video Production (backend/card_slot.py): one run at a time per (visit, agent).

Replays of an OK that moves a card from Storyboard to Draft and a person's question about it, in either order and
across a runner restart: each gives one delivered draft, one charge per paid shot and an answered question. Fencing: a
run that ends after the card moved on or changed hands settles none of the work that came after it.

Synthetic rows only: the fictional company's bots `ops` and `cpo`, and Ana."""

from types import SimpleNamespace

from backend import paid_renders, task_wakes
from backend import task_engine as E
from backend.run_diagnostics import RESTART_TEXT
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, post, ready, runner

ANA = "human:ana"
QUESTION = "Did you get my OK on the storyboard?"
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
        paid_renders.send(self.api.app.state.store, who, {"prompt": "Opening shot, kitchen"}, "opening", submit)

    def inputs(self, run):
        return [m["id"] for m in post(self.api, f"attempts/{run['id']}/inputs", {}, self.machine["token"])["messages"]]

    def finish(self, run):
        body = run["message"]["body"]
        if run["message"]["refs"].get("wake") == "task_turn" and "Draft" in body:
            self.render(run)  # the same shot again is the one it already paid for (backend/paid_renders.py)
            self.drafts += 1
        if QUESTION in body:
            self.answers += 1
        done = {"outcome": "completed", "last_seq": 0, "text": "Done."}
        post(self.api, f"attempts/{run['id']}/complete", done, self.machine["token"], key="done-" + run["id"])


def _jobs(api, *ids):
    with api.app.state.store.read() as c:
        return {i: c.execute("SELECT state FROM jobs WHERE id=?", (i,)).fetchone()[0] for i in ids}


def _wake(api, task, visit):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.wake_key LIKE ? AND j.bot_id='ops'",
            (f"{task['id']}:{visit}:%",),
        ).fetchone()[0]


def test_ok_then_chat_is_one_run_with_the_draft_and_the_answer(api):
    machine, task = _card(api)
    draft = _ok(api, task)
    asked = _ask(api, task)
    agent = Agent(api, machine)
    run = agent.start()
    assert run["message"]["id"] == _wake(api, task, draft)  # the stage's work is the run, the question rides in it
    assert QUESTION in run["message"]["body"]
    agent.finish(run)
    assert claim(api, machine) is None  # nothing left for a second run
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    assert set(_jobs(api, asked, run["message"]["id"]).values()) == {"completed"}


def test_chat_then_ok_answers_in_its_own_run_and_drafts_once_after(api):
    machine, task = _card(api)
    asked = _ask(api, task)
    agent = Agent(api, machine)
    chat = agent.start()
    assert chat["message"]["id"] == asked
    draft = _ok(api, task)
    wake = _wake(api, task, draft)
    assert wake not in agent.inputs(chat)  # another slot's work is never steered in
    agent.finish(chat)
    assert _jobs(api, wake)[wake] == "queued"  # the question's run settled nothing of Draft
    work = agent.start()
    assert work["message"]["id"] == wake
    agent.finish(work)
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)


def test_ok_and_chat_across_a_runner_restart(api):
    machine, task = _card(api)
    draft = _ok(api, task)
    agent = Agent(api, machine)
    first = agent.start()
    agent.render(first)  # the paid shot went out before the restart
    asked = _ask(api, task)
    assert asked not in agent.inputs(first)  # a comment during the run stays pending
    # The runner restarts mid-run and says so; the sweep puts the run back in line.
    stopped = {"outcome": "interrupted", "last_seq": 0, "text": RESTART_TEXT}
    post(api, f"attempts/{first['id']}/complete", stopped, machine["token"])
    with api.app.state.store.transaction() as c:
        assert [d["state"] for d in api.app.state.execution.auto_reconcile(c)] == ["queued"]
    again = agent.start()
    assert again["message"]["id"] == _wake(api, task, draft)
    assert QUESTION in again["message"]["body"]
    agent.finish(again)
    assert claim(api, machine) is None
    assert (agent.drafts, agent.charges, agent.answers) == (1, 1, 1)
    with api.app.state.store.read() as c:
        rev = c.execute("SELECT slot_rev FROM jobs WHERE id=?", (again["job_id"],)).fetchone()[0]
        assert rev == c.execute("SELECT rowid FROM messages WHERE id=?", (asked,)).fetchone()[0]


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


def test_other_pipelines_keep_the_older_path(api):
    machine, task = _card(api, name="Video")
    draft = _ok(api, task)
    asked = _ask(api, task)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE slot IS NOT NULL").fetchone()[0] == 0
    agent = Agent(api, machine)
    assert agent.start()["message"]["id"] == asked  # a person's chat goes first, in a run of its own
    assert _wake(api, task, draft)


def test_a_card_with_work_from_before_the_switch_finishes_it_on_the_older_path(api):
    machine, task = _card(api, name="Video")
    draft = _ok(api, task)  # queued before the pipeline switched
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET name='Video Production' WHERE id='video'")
    asked = _ask(api, task)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT slot FROM jobs WHERE id=?", (asked,)).fetchone()[0] is None
    agent = Agent(api, machine)
    agent.finish(agent.start())
    agent.finish(agent.start())
    assert claim(api, machine) is None and (agent.drafts, agent.answers) == (1, 1)
    later = _ask(api, task)  # the card's older work has settled: it runs by slot now
    with api.app.state.store.read() as c:
        assert c.execute("SELECT slot FROM jobs WHERE id=?", (later,)).fetchone()[0] == f"{draft}:ops"


def test_a_computer_that_starts_only_peoples_messages_runs_the_question_alone(api, monkeypatch):
    from backend import start_places

    machine, task = _card(api)
    draft = _ok(api, task)
    asked = _ask(api, task)
    # The computer is busy: it holds every new start but a person's own message.
    monkeypatch.setattr(
        start_places, "computer_hold", lambda *a, person=False, **k: None if person else ("busy", "Busy")
    )
    agent = Agent(api, machine)
    chat = agent.start()
    assert chat["message"]["id"] == asked and "Draft" not in chat["message"]["body"]
    agent.finish(chat)
    wake = _wake(api, task, draft)
    assert _jobs(api, wake)[wake] == "queued"  # the stage's work waits for the hold to lift
    monkeypatch.undo()
    assert agent.start()["message"]["id"] == wake
