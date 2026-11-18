"""A turn that ends with a reply opening with ❓ meant to ask a person, but typed words mark nothing: woken by Tico's
notice the reply stays on the run, and posted it is a plain message, so nobody's Needs you shows it. Its bot is woken
on the task once to ask with a real ❓ (question_marks.unasked_reply); a turn that did ask, or that this wake started,
is not.

Synthetic rows only: the fictional company's bot `ops` and Ana."""

from backend import question_marks as Q
from backend import task_engine as E
from backend import task_wakes
from backend.tests.helpers.api import assign, claim, post, ready, runner

ASKED = "❓ **Ana, which reports page gets the export button: the old one or the new one?**"


def _start(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def _task(api, title="Add the export button"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "On the reports page."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return task


def _poke(api, task_id):
    """Tico's own notice to ops on the task, nobody's act behind it: its run's reply is for nobody unless it asks."""
    with api.app.state.store.transaction() as c:
        row = E.task(c, task_id)
        return task_wakes.add(c, row, "bot:ops", row["visit"], "You stopped before this task was finished.", try_n=1)


def _finish(api, machine, attempt, text, during=None):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, machine["token"])
    if during:
        during(attempt)
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    return post(api, f"attempts/{attempt['id']}/complete", body, machine["token"], key="done")["message"]


def _unasked(api, task_id):
    """The bodies of the wakes that told ops its ❓ asked nobody."""
    with api.app.state.store.read() as c:
        return [
            row[0]
            for row in c.execute(
                f"SELECT body FROM messages WHERE json_extract(refs, '$.task')=? AND json_extract(refs, '$.{Q.UNASKED}') "
                "ORDER BY rowid",
                (task_id,),
            )
        ]


def _marks(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM question_marks WHERE task_id=?", (task_id,)).fetchone()[0]


def test_a_reply_that_opens_with_a_question_mark_wakes_its_bot_once_to_ask_for_real(api):
    machine = _start(api)
    task = _task(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    assert _finish(api, machine, attempt, ASKED) is None, "a notice's turn keeps its text on the run"
    assert _marks(api, task["id"]) == 0
    [told] = _unasked(api, task["id"])
    assert f"tico task ask {task['id']} --to" in told and Q.KEPT in told

    again = claim(api, machine)
    assert again["message"]["body"] == told, "the wake starts the bot's next turn on the task"
    _finish(api, machine, again, ASKED)
    assert len(_unasked(api, task["id"])) == 1, "the turn that wake started is not woken for it again"


def test_a_turn_that_asked_with_a_real_mark_or_wrote_no_mark_is_not_woken(api):
    machine = _start(api)
    task = _task(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)

    def ask(run):
        question = {"to": "ana", "text": "Which reports page gets the export button?"}
        post(api, f"tasks/{task['id']}/questions", question, run["token"])

    _finish(api, machine, attempt, "❓ Asked Ana which reports page gets the button.", during=ask)
    assert _marks(api, task["id"]) == 1 and _unasked(api, task["id"]) == []

    other = _task(api, "Add the print button")
    _poke(api, other["id"])
    attempt = claim(api, machine)
    assert attempt["task"]["id"] == other["id"]
    _finish(api, machine, attempt, "Still waiting on Ana's answer. ❓ stays open.")
    assert _unasked(api, other["id"]) == [], "only a reply that opens with ❓ was meant as a question"


def test_the_wake_names_what_its_words_must_fix_first(api):
    machine = _start(api)
    task = _task(api)
    _poke(api, task["id"])
    attempt = claim(api, machine)
    _finish(api, machine, attempt, "❓ Ana, five questions are in the comment above: yes to all?")
    [told] = _unasked(api, task["id"])
    assert '"comment above"' in told, "the shape check's fix comes with the wake, so one rewrite asks"
