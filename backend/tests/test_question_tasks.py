"""Question tasks (backend/question_tasks.py, spec D8): `hub task question` files a task the person owns with the
question on its fields and no ask message; answering closes it and the bot hears it once."""

from backend import ask_deadlines, still_needed, hubdb as H, task_relations as TR
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401
from backend.tests.test_mcp import call


def _ask(api, token, question, **extra):
    err, out = call(api, "hub_task_question", {"question": question, "on": "ana", **extra}, token=token)
    assert not err, out
    return out


def _work(api, title="Pick a launch date", **extra):
    """A task of the bot's and a run on it: a question asked from that run is filed under it."""
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner": "ops", "title": title, "body": "Choose when the Acme launch goes out.", **extra})
    return work, claim(api, r)


def _read(api, sql, args=()):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchall()


def _mark(api):
    return _read(api, "SELECT coalesce(max(rowid), 0) FROM messages")[0][0]


def _to_ops(api, since):
    return [r[0] for r in _read(api, "SELECT body FROM messages WHERE recipient_id='bot:ops' AND rowid>? ORDER BY rowid",
                                (since,))]


def _answer(api, task_id, body, token="ana-test", expected=200):
    return post(api, f"tasks/{task_id}/answers", {"target": {"task": task_id}, **body}, token=token, expected=expected)


def _cards(api, token="ana-test"):
    return [i for i in get(api, "needs-you", token=token)["items"] if i["kind"] == "question"]


def test_a_question_from_a_run_is_a_subtask_the_person_owns_with_no_ask_message(api):
    work, attempt = _work(api)
    out = _ask(api, attempt["token"], "Launch on Tuesday or Thursday?", why="The press list needs a date",
               options=[{"label": "Tuesday", "then": "Launch Tuesday", "recommended": True},
                        {"label": "Thursday", "then": "Launch Thursday"}])
    card = out["task"]
    assert card["owner_id"] == "human:ana" and card["requester_id"] == "bot:ops" and not out["folded"]
    assert card["questions"][0]["question"] == "Launch on Tuesday or Thursday?"
    assert [o["label"] for o in card["questions"][0]["options"]] == ["Tuesday", "Thursday"]
    assert card["questions"][0]["options"][0]["recommended"] and card["answer"] is None
    assert out["question"]["question_task"] and out["question"]["refs"]["target"] == {"task": card["id"]}
    assert _read(api, "SELECT count(*) FROM messages WHERE kind='ask'")[0][0] == 0
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, card["id"]) == work["id"]
        assert card["id"] in TR.blocker_ids(c, work["id"])
        assert H.ask_card(c, H.task(c, card["id"])) == "human:ana"
        assert H.task(c, work["id"])["owner_id"] == "bot:ops"


def test_with_no_task_to_file_under_it_stands_alone(api):
    _, _, attempt = setup_attempt(api)
    out = _ask(api, attempt["token"], "Archive the September drafts?")
    card = out["task"]
    assert card["owner_id"] == "human:ana" and card["requester_id"] == "bot:ops"
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, card["id"]) is None
    # No options is a Yes / No.
    assert [o["label"] for o in card["questions"][0]["options"]] == ["Yes", "No"]


def test_a_bot_never_files_a_question_task_for_another_bot(api):
    _, _, attempt = setup_attempt(api)
    err, out = call(api, "hub_task_question", {"question": "Is the export done?", "on": "coo"}, token=attempt["token"])
    assert err and "DM" in str(out) and "hub message send" in str(out), out
    assert not _read(api, "SELECT 1 FROM tasks WHERE title='Is the export done?'")


def test_a_pick_closes_it_done_with_the_answer_and_wakes_the_parent_once(api):
    work, attempt = _work(api)
    card = _ask(api, attempt["token"], "Launch on Tuesday or Thursday?",
                options=[{"label": "Tuesday", "then": "Launch Tuesday"}, {"label": "Thursday", "then": "Launch Thursday"}]
                )["task"]
    since = _mark(api)
    out = _answer(api, card["id"], {"answers": {"decision": ["Thursday"]}, "other": "after the newsletter"})
    assert out["answer"]["answers"] == {"decision": ["Thursday"]} and out["answer"]["by"] == "human:ana"
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["status"] == "done" and done["answer"]["answers"] == {"decision": ["Thursday"]}
    assert "Thursday" in done["note"] and "after the newsletter" in done["note"]
    said = _to_ops(api, since)
    assert len(said) == 1 and "Thursday" in said[0], said
    with api.app.state.store.read() as c:
        assert card["id"] not in TR.blocker_ids(c, work["id"])
    # The same answer again (a retry) records nothing new; another one is late.
    assert _answer(api, card["id"], {"answers": {"decision": ["Thursday"]}, "other": "after the newsletter"}
                   )["answer"] == out["answer"]
    _answer(api, card["id"], {"answers": {"decision": ["Tuesday"]}}, expected=409)
    assert len(_to_ops(api, since)) == 1


def test_the_asker_cannot_answer_and_a_label_must_be_one_of_its_options(api):
    _, attempt = _work(api)
    card = _ask(api, attempt["token"], "Ship the Acme banner?")["task"]
    _answer(api, card["id"], {"answers": {"decision": ["Maybe"]}}, expected=422)
    r = api.post(f"/api/v2/tasks/{card['id']}/answers", json={"target": {"task": card["id"]},
                                                             "answers": {"decision": ["Yes"]}},
                 headers=headers(attempt["token"]))
    assert r.status_code == 403, r.text


def test_a_decline_closes_it_declined(api):
    _, _, attempt = setup_attempt(api)
    card = _ask(api, attempt["token"], "Archive the September drafts?")["task"]
    since = _mark(api)
    _answer(api, card["id"], {"dismiss": True})
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["status"] == "declined" and done["answer"]["dismiss"]
    # Standalone: the bot hears it once, as the task's requester.
    assert len(_to_ops(api, since)) == 1


def test_done_from_the_task_itself_is_the_answer(api):
    """Decide's Yes / No and the Assistant close the task: a note naming an option picks it."""
    _, attempt = _work(api)
    card = _ask(api, attempt["token"], "Ship the Acme banner?")["task"]
    post(api, f"tasks/{card['id']}", {"status": "done", "note": "Yes. Use the blue one", "version": card["version"]})
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["answer"]["answers"] == {"decision": ["Yes"]} and done["answer"]["other"] == "Use the blue one"


def test_one_open_question_task_per_topic(api):
    work, attempt = _work(api)
    first = _ask(api, attempt["token"], "Give Builder a backup model?", topic="Backup model")
    since = _mark(api)
    again = _ask(api, attempt["token"], "Give Builder and Analyst a backup model?", topic="backup-model",
                 options=[{"label": "Both", "then": "Set both"}, {"label": "Builder only", "then": "Set Builder"}])
    assert again["folded"] and again["task"]["id"] == first["task"]["id"]
    assert _read(api, "SELECT count(*) FROM messages WHERE rowid>? AND recipient_id='human:ana'", (since,))[0][0] == 0
    card = again["task"]
    assert card["title"] == "Give Builder and Analyst a backup model?"
    assert "Asked before:\n- Give Builder a backup model?" in card["body"]
    assert card["questions"][0]["id"] == "decision-2"
    assert len([c for c in _cards(api) if c["id"] == card["id"]]) == 1
    # A pick made on the earlier wording is refused, never taken as an answer to this one.
    _answer(api, card["id"], {"answers": {"decision": ["Yes"]}}, expected=409)
    other = _ask(api, attempt["token"], "Move the team to yearly billing?", topic="billing")
    assert not other["folded"] and other["task"]["id"] != card["id"]
    _answer(api, card["id"], {"answers": {"decision-2": ["Both"]}})
    assert get(api, f"tasks/{card['id']}")["task"]["status"] == "done"


def test_the_if_no_answer_default_goes_ahead_at_by(api):
    work, attempt = _work(api)
    by = H.shift(H.now(), days=4)
    card = _ask(api, attempt["token"], "Send the Acme recap on Friday?",
                if_no_answer={"do": "Yes", "kind": "reversible", "by": by})["task"]
    assert card["if_no_answer"]["kind"] == "reversible"
    item = next(i for i in _cards(api) if i["id"] == card["id"])
    assert item["ask"]["goes_ahead_at"] and item["recommend"]["option"] == "Yes"
    with api.app.state.store.transaction() as c:
        assert ask_deadlines.sweep(c, H.shift(by, hours=-1))["went_ahead"] == []
    since = _mark(api)
    with api.app.state.store.transaction() as c:
        assert ask_deadlines.sweep(c, H.shift(by, hours=1))["went_ahead"] == [card["id"]]
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["status"] == "done" and done["answer"]["by"] == "keeper"
    assert done["answer"]["answers"] == {"decision": ["Yes"]}
    assert len(_to_ops(api, since)) == 1


def test_a_send_default_never_goes_ahead_and_the_question_closes_at_by(api):
    _, _, attempt = setup_attempt(api)
    by = H.shift(H.now(), days=4)
    card = _ask(api, attempt["token"], "Email the vendor the Acme quote?",
                if_no_answer={"do": "Send the quote", "kind": "send", "by": by})["task"]
    with api.app.state.store.transaction() as c:
        assert ask_deadlines.sweep(c, H.shift(by, hours=1))["expired"] == [card["id"]]
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["status"] == "closed" and "never goes ahead" in done["note"]


def test_with_no_date_it_goes_back_to_the_bot_after_two_business_days(api):
    work, attempt = _work(api)
    card = _ask(api, attempt["token"], "Rename the Acme newsletter?")["task"]
    at = H.shift(still_needed.crossed_at(H.now()).isoformat(), days=3)
    while not still_needed.batch_due(at):
        at = H.shift(at, hours=1)
    with api.app.state.store.transaction() as c:
        sent = still_needed.sweep(c, at)
    assert sent == {"bot:ops": [card["id"]]}
    done = get(api, f"tasks/{card['id']}")["task"]
    assert done["status"] == "closed"
    assert not [c for c in _cards(api) if c["id"] == card["id"]]
    with api.app.state.store.read() as c:
        assert card["id"] not in TR.blocker_ids(c, work["id"])


def test_needs_you_and_decide_count_a_question_task_once(api):
    _, attempt = _work(api)
    card = _ask(api, attempt["token"], "Launch on Tuesday or Thursday?",
                options=[{"label": "Tuesday", "then": "Launch Tuesday"}, {"label": "Thursday", "then": "Launch Thursday"}]
                )["task"]
    items = [i for i in get(api, "needs-you")["items"] if i["id"] == card["id"]]
    assert len(items) == 1 and items[0]["kind"] == "question"
    ask = items[0]["ask"]
    assert ask["question_task"] and ask["sender_id"] == "bot:ops" and ask["recipient_id"] == "human:ana"
    assert [o["label"] for o in ask["refs"]["questions"][0]["options"]] == ["Tuesday", "Thursday"]
    assert "answer" not in items[0]["actions"] and "update" in items[0]["actions"]
    before = get(api, "needs-you?count=1")
    assert before["plate_count"] >= 1
    _answer(api, card["id"], {"answers": {"decision": ["Tuesday"]}})
    after = get(api, "needs-you?count=1")
    assert after["plate_count"] == before["plate_count"] - 1
    assert not [i for i in get(api, "needs-you")["items"] if i["id"] == card["id"]]


def test_a_private_parents_question_task_is_only_for_its_readers(api):
    work, attempt = _work(api, title="Private pricing review", private=True)
    card = _ask(api, attempt["token"], "Raise the Acme plan price?")["task"]
    assert card["private"]
    assert get(api, f"tasks/{card['id']}")["task"]["id"] == card["id"]
    r = api.get(f"/api/v2/tasks/{card['id']}", headers=headers("ben-test"))
    assert r.status_code in (403, 404), r.text
    assert not [i for i in get(api, "needs-you", token="ben-test")["items"] if i["id"] == card["id"]]
    assert [i for i in _cards(api) if i["id"] == card["id"]]
