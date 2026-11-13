"""Org rule checks after the write (backend/org_rule_check.py `late`): when every rule a comment, a done note or a
person's ask meets only warns, the write is saved and answered first and its one check runs right after. A broken rule
then puts today's warning line on the saved words and tells the author; a check that cannot run records the same
`rules.unchecked`; a rule that can hold still holds before anything is saved."""

import json
import threading
import time

from backend import org_rule_check as K
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.org_rules import rule
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    color_rule,
    events,
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

EVERYONE = {"everyone": True}
SAFE = "Draft is in progress."
BROKEN = "Fun fact: our favorite color is red."


def settle(api):
    """Every late check queued so far, run now (the drain thread may already have run some)."""
    return K.drain_late(api.app.state.store, api.app.state.judge)


def everyone_rule(api, if_broken="warn"):
    return rule(api, {"kind": "internal", "text": COLOR, "who": EVERYONE, "if_broken": if_broken})


def comment(api, task, text, token="ops-test", key=None):
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": text}, headers=headers(token, key))
    assert r.status_code == 200, r.text
    return r.json()


def body_of(api, message_id):
    return sql(api, "SELECT body FROM messages WHERE id=?", (message_id,))[0]["body"]


def detail(rows):
    return [json.loads(r["detail"]) for r in rows]


def test_writes_under_a_warn_rule_answer_without_waiting_for_the_judge(ruled):
    """AC-1 and AC-6: a comment, a bot's done note and a person's ask each answer while the judge has not answered."""
    api, judge = ruled
    everyone_rule(api)
    task = task_for_ops(api)  # a new task is not left for later yet: checked before it is made
    before = len(judge.calls)
    judge.hold = threading.Event()  # the judge answers only when released (or after 5 s, the check's deadline)
    try:
        started = time.monotonic()
        comment(api, task, SAFE)
        asked = post(api, f"tasks/{task['id']}/asks", {"to": "ben", "line": "Which day suits you?", "version": 1})
        version = sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"]
        done = api.post(
            f"/api/v2/tasks/{task['id']}/done",
            json={"version": version, "note": "Done: the draft is in."},
            headers=headers("ops-test"),
        )
        assert done.status_code == 200, done.text
        assert time.monotonic() - started < 3, "a write waited for the judge"
        assert asked["requirement_id"] and "rule_warning" not in done.json()
    finally:
        judge.hold.set()
    settle(api)
    assert len(judge.calls) - before == 3  # each write checked once, after it was saved
    assert not any(judge.locked)
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 0


def test_a_broken_warn_rule_puts_todays_line_on_the_saved_comment_and_tells_the_bot(ruled):
    """AC-2: the line lands on the saved words, the bot gets today's note, and the comment is saved once."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    made = comment(api, task, BROKEN)
    assert "rule_warning" not in made
    settle(api)
    mid = made["comment"]["id"]
    assert body_of(api, mid).startswith("Warning:") and body_of(api, mid).endswith(BROKEN)
    assert len(sql(api, "SELECT id FROM messages WHERE body LIKE ?", (f"%{BROKEN}%",))) == 1
    notes = sql(api, "SELECT body FROM notes WHERE to_actor='bot:ops'")
    assert any("was delivered with a warning" in n["body"] for n in notes)
    assert detail(events(api, "rules.warned"))[-1]["late"] is True
    assert len(judge.calls) == 1


def test_a_late_warning_reaches_a_done_note_and_a_persons_ask(ruled):
    """AC-2 for done and asks: the line goes where today's does (the done note; before the task's note for an ask),
    and a person is told with a notice."""
    api, _ = ruled
    everyone_rule(api)
    task = task_for_ops(api)
    post(api, f"tasks/{task['id']}/asks", {"to": "ben", "line": "Is our favorite color is red okay?", "version": 1})
    settle(api)
    note = sql(api, "SELECT note FROM tasks WHERE id=?", (task["id"],))[0]["note"]
    assert note.startswith("Warning:")
    told = sql(api, "SELECT body FROM messages WHERE kind='notice' AND body LIKE '%delivered with a warning%'")
    assert told
    version = sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/done",
        json={"version": version, "note": "Done: favorite color is red."},
        headers=headers("ops-test"),
    )
    assert r.status_code == 200 and "rule_warning" not in r.json(), r.text
    settle(api)
    said = sql(api, "SELECT notes FROM answers ORDER BY rowid DESC LIMIT 1")[0]["notes"]
    assert said.startswith("Warning:") and said.endswith("Done: favorite color is red.")


def test_a_block_rule_or_a_mix_with_one_still_holds_before_anything_is_saved(ruled):
    """AC-3: a rule that can hold is checked before the write, alone or beside a warn rule."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    color_rule(api, if_broken="block")
    task = task_for_ops(api)
    held = comment(api, task, BROKEN)
    assert held["held"]
    assert sql(api, "SELECT id FROM messages WHERE body LIKE ?", (f"%{BROKEN}%",)) == []
    assert sql(api, "SELECT count(*) AS n FROM org_rule_late")[0]["n"] == 0
    assert len(judge.calls) == 1


def test_a_bots_ask_is_still_checked_before_it_is_saved(ruled):
    """A warn rule refuses a bot's ask it covers (task_rule_gate `covering`), so that ask is never left for later."""
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": "ana", "line": "Is our favorite color is red okay?", "version": task["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 409 and r.json()["error"] == "task_rule", r.text


def test_a_judge_that_fails_after_the_write_keeps_it_and_records_it_unchecked(ruled):
    """AC-4: the write stays as saved, with the same `rules.unchecked` record as a check before the write makes."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    judge.fail = RuntimeError("down")
    made = comment(api, task, BROKEN)
    settle(api)
    assert body_of(api, made["comment"]["id"]) == BROKEN
    [unchecked] = detail(events(api, "rules.unchecked"))
    assert unchecked["operation"] == "comment" and unchecked["reason"] == "error" and unchecked["late"] is True
    assert events(api, "rules.warned") == []


def test_a_busy_judge_leaves_the_check_for_a_later_pass(ruled, monkeypatch):
    """No free call slot: the check waits for the next pass instead of going unchecked at once."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    monkeypatch.setattr(K, "_ask", lambda *a, **k: (None, "busy"))
    made = comment(api, task, BROKEN)
    settle(api)
    assert sql(api, "SELECT tries FROM org_rule_late")[0]["tries"] >= 1
    monkeypatch.undo()
    api.app.state.judge = judge
    settle(api)
    assert body_of(api, made["comment"]["id"]).startswith("Warning:")


def test_two_hundred_comments_with_retries_are_saved_once_each_in_order(ruled):
    """AC-5: 200 comments on one task, each sent again with its Idempotency-Key: saved once, in order, each retry
    answered as the first was, and each checked once."""
    api, judge = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    for n in range(200):
        key = f"p01-comment-{n}"
        first = comment(api, task, f"Step {n} is done.", key=key)
        again = comment(api, task, f"Step {n} is done.", key=key)
        assert again["comment"]["id"] == first["comment"]["id"]
    settle(api)
    said = [
        r["body"] for r in sql(api, "SELECT body FROM messages WHERE body LIKE 'Step % is done.' ORDER BY rowid", ())
    ]
    assert said == [f"Step {n} is done." for n in range(200)]
    assert len(judge.calls) == 200


def test_a_late_check_starts_on_its_own_once_the_write_commits(ruled):
    """AC-2's "within 10 s" without the scheduler: the write's commit starts the drain thread, nobody drains by hand."""
    api, _ = ruled
    color_rule(api, if_broken="warn")
    task = task_for_ops(api)
    mid = comment(api, task, BROKEN)["comment"]["id"]
    deadline = time.monotonic() + 10
    while not body_of(api, mid).startswith("Warning:") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert body_of(api, mid).startswith("Warning:")
