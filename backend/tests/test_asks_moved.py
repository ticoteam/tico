"""Asks are gone (threads + tasks, step 6c). The asks migration stages each open ask and the next start moves it
(question_tasks.move_asks): an ask on a task becomes a question subtask for the person, or joins the card they already
have there; a loose ask becomes a question task of its own; an ask to a bot becomes a DM flagged needs-action.
Answered asks stay as history, a private task's ask stays private, and it all happens once."""
from backend import hubdb as H, question_tasks as QT, task_relations as TR
from backend.tests.old_asks import old_answer, old_ask, with_refs
from backend.tests.test_api import api, get  # noqa: F401  (fixture)
from backend.tests.test_derived_waiting import _migration

UP = _migration("20261106131713_asks_to_question_tasks").up
QUESTIONS = [{"id": "q1", "header": "Date", "question": "Launch Tuesday or Thursday?", "why": "The press list needs it",
              "multi": False, "options": [{"label": "Tuesday", "then": "Launch Tuesday", "recommended": True},
                                          {"label": "Thursday", "then": "Launch Thursday"}]}]


def _task(c, title, owner="bot:ops", **kw):
    return H.task_create(c, "human:ana", title, "", owner, lint=False, private=kw.pop("private", False), **kw)


def _on(c, row, sender, to, body, **refs):
    m = old_ask(c, sender, to, body, conversation_id=row["conversation_id"], refs={"task": row["id"]})
    return with_refs(c, m["id"], **refs) if refs else m


def _cards(c, person):
    return [t for t in H.needs_you(c, person)["tasks"] if QT.is_open(t)]


def _staged(c):
    return {r[0] for r in c.execute("SELECT message_id FROM asks_moved")}


def test_each_kind_of_open_ask_moves_once_and_answered_ones_stay_history(api):
    store = api.app.state.store
    with store.transaction() as c:
        launch = _task(c, "Plan the launch")
        asked = _on(c, launch, "bot:ops", "human:ana", "Launch Tuesday or Thursday?", questions=QUESTIONS,
                    if_no_answer={"do": "Launch Tuesday", "after": "2099-01-01T00:00:00Z"})
        domain = _task(c, "Renew the domain")
        card = QT.ask_plain(c, "bot:ops", "human:ana", "Renew for one year?", parent=domain)
        joined = _on(c, domain, "bot:ops", "human:ana", "And move it to the new registrar?")
        loose = old_ask(c, "bot:cpo", "human:ana", "Can I book the venue?")
        bots = _task(c, "Close the books")
        to_bot = _on(c, bots, "bot:ops", "bot:finance", "Which month is closed?")
        done = _task(c, "Old question")
        answered = _on(c, done, "bot:ops", "human:ana", "Which format?")
        old_answer(c, "human:ana", answered, "Markdown.")
        before = len(_cards(c, "human:ana"))
        UP(c)
        assert _staged(c) == {asked["id"], joined["id"], loose["id"], to_bot["id"]}
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
    store.initialize(seed_market=False)
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='asks_moved'").fetchone()
        # On a task: a question subtask for Ana carrying the ask's questions and default; the task waits on it.
        [sub] = [i for i in TR.child_ids(c, launch["id"])]
        sub = H.task(c, sub)
        assert (sub["owner_id"], sub["requester_id"]) == ("human:ana", "bot:ops") and QT.is_open(sub)
        fields = QT.fields(sub)
        [q] = fields["questions"]
        assert q["question"] == "Launch Tuesday or Thursday?" and [o["label"] for o in q["options"]] == ["Tuesday", "Thursday"]
        assert fields["if_no_answer"]["do"] == "Launch Tuesday"
        assert QT.waiting_on(c, H.task(c, launch["id"])) == "human:ana"
        # On a task with Ana's card already: it joins that card, no second one.
        assert TR.child_ids(c, domain["id"]) == [card["id"]]
        assert "new registrar" in " ".join(m["body"] for m in H.task_comments(c, card["id"]))
        # Loose: a question task of its own, private to the two of them.
        [alone] = [t for t in _cards(c, "human:ana") if t["requester_id"] == "bot:cpo"]
        assert QT.fields(alone)["questions"][0]["question"] == "Can I book the venue?" and H.task_private(c, alone)
        # To a bot: one DM from ops to finance, flagged needs-action, with the ask's words; one wake.
        [dm] = H._rows(c.execute("SELECT * FROM messages WHERE sender_id='bot:ops' AND recipient_id='bot:finance' "
                                 "AND kind='say' AND rowid>?", (since,)))
        assert dm["body"] == "Which month is closed?" and H._json(dm["refs"], {})["needs_action"] is True
        woke = c.execute("SELECT j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE m.recipient_id='bot:finance' AND m.rowid>?", (since,)).fetchall()
        assert [r[0] for r in woke] == [dm["id"]]
        # Every open ask to Ana is in her Needs you: as its own card, or in the card it joined.
        assert len(_cards(c, "human:ana")) == before + 2
        # The old rows stay in their threads, out of every list; the answered one is untouched.
        for mid in (asked["id"], joined["id"], loose["id"], to_bot["id"]):
            m = H.message(c, mid)
            assert m["kind"] == "ask" and m["superseded_at"] and m["refs"]["moved"]["how"]
        untouched = H.message(c, answered["id"])
        assert untouched["kind"] == "ask" and not untouched["superseded_at"] and "moved" not in untouched["refs"]
        counts = [c.execute("SELECT count(*) FROM tasks").fetchone()[0],
                  c.execute("SELECT count(*) FROM messages").fetchone()[0]]
    store.initialize(seed_market=False)              # a second start moves nothing again
    with store.transaction() as c:
        UP(c)                                        # nor does the migration, run again: the moved asks are closed
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='asks_moved'").fetchone()
        assert [c.execute("SELECT count(*) FROM tasks").fetchone()[0],
                c.execute("SELECT count(*) FROM messages").fetchone()[0]] == counts


def test_a_private_tasks_ask_becomes_a_question_task_only_its_people_read(api):
    store = api.app.state.store
    from backend.tests.test_api import unrelated_task_managers
    with store.transaction() as c:
        unrelated_task_managers(c)
        secret = _task(c, "Zip triage", private=True)
        _on(c, secret, "bot:ops", "human:ben", "Ship the zip fix in the next release?")
        UP(c)
    store.initialize(seed_market=False)
    with store.read() as c:
        [card_id] = TR.child_ids(c, secret["id"])
        card = H.task(c, card_id)
        assert card["owner_id"] == "human:ben" and H.task_private(c, card)
    assert get(api, f"tasks/{card_id}", "ben-test")["task"]["id"] == card_id
    r = api.get(f"/api/v2/tasks/{card_id}", headers={"Authorization": "Bearer cara-test"})
    assert r.status_code in (403, 404), r.text
    assert card_id not in str(get(api, "needs-you", "cara-test"))


def test_an_ask_that_cannot_move_keeps_its_words_on_its_task(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        row = _task(c, "Plan the launch")
        asked = _on(c, row, "bot:ops", "human:ana", "Launch Tuesday or Thursday?")
        UP(c)

    def broken(*a, **k):
        raise RuntimeError("no room")
    monkeypatch.setattr(QT, "_ask_task", broken)
    with store.transaction() as c:
        QT.move_asks(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='asks_moved'").fetchone()
        assert H.message(c, asked["id"])["refs"]["moved"]["how"] == "kept"
        assert any("Launch Tuesday or Thursday?" in m["body"] for m in H.task_comments(c, row["id"]))


def test_a_file_with_no_open_asks_gets_no_staging_table(api):
    with api.app.state.store.transaction() as c:
        row = _task(c, "Old question")
        old_answer(c, "human:ana", _on(c, row, "bot:ops", "human:ana", "Which format?"), "Markdown.")
        UP(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='asks_moved'").fetchone()
