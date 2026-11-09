"""Who a task waits on is derived, not stored (threads + tasks, step 6b): `--status waiting --on <person>` files a
question task with no options under the task, the task's `waiting_on` is that question task's owner while it is
open, and answering it opens the task again with one wake. The migration's old rows become question tasks or the
bot's blocker; a private parent names its person only where the question task's words may flow up."""

from backend import hubdb as H, question_tasks as QT, task_holds, task_relations as TR
from backend.tests.old_asks import old_ask
from backend.tests.helpers.api import get, post  # noqa: F401  (fixture)
from backend.tests.helpers.question_tasks import _answer, _bot_only_private_parent, _mark, _to, _to_ops, _work
from backend.tests.helpers.derived_waiting import _migration

ASK = "Pick the launch date? I'll book the venue after."


def _wait(api, task_id, token, on="ana", note=ASK, expected=200):
    version = get(api, f"tasks/{task_id}", token)["task"]["version"]
    return post(
        api,
        f"tasks/{task_id}",
        {"version": version, "status": "waiting", "waiting_on": on, "note": note},
        token,
        expected=expected,
    )


def test_waiting_on_a_person_files_them_a_question_task_with_no_options(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"])
    with api.app.state.store.read() as c:
        [card_id] = TR.child_ids(c, work["id"])
        card = H.task(c, card_id)
        assert (card["owner_id"], card["requester_id"], card["status"]) == ("human:ana", "bot:ops", "open")
        [question] = QT.fields(card)["questions"]
        assert question["question"] == ASK and question["options"] == []
        assert card_id in TR.blocker_ids(c, work["id"])
        assert "waiting_on" not in H.task(c, work["id"])  # nothing stored on the task
    view = get(api, f"tasks/{work['id']}", attempt["token"])["task"]
    assert (view["status"], view["waiting_on"]) == ("waiting", "human:ana")
    listed = next(t for t in get(api, "tasks", attempt["token"])["tasks"] if t["id"] == work["id"])
    assert listed["waiting_on"] == "human:ana"
    assert [i for i in get(api, "needs-you")["items"] if i["id"] == card_id]


def test_answering_it_opens_the_task_again_and_wakes_its_bot_once(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"])
    with api.app.state.store.read() as c:
        [card_id] = TR.child_ids(c, work["id"])
    since = _mark(api)
    _answer(api, card_id, {"answers": {}, "other": "Thursday the 12th."})
    said = _to_ops(api, since)
    assert len(said) == 1 and "Thursday the 12th." in said[0], said
    view = get(api, f"tasks/{work['id']}", attempt["token"])["task"]
    assert view["status"] == "open" and view["waiting_on"] is None


def test_holds_come_from_children_and_blockers(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"])
    with api.app.state.store.transaction() as c:
        [card_id] = TR.child_ids(c, work["id"])
        other = H.task_create(c, "human:ana", "Book the van", "", "bot:finance", lint=False, private=False)
        TR.relate(c, "bot:ops", work["id"], other["id"], "blocked_by")
        held = task_holds.holds(c, H.task(c, work["id"]))
        assert [(h["kind"], h["on"]) for h in held] == [("child", "human:ana"), ("blocker", "bot:finance")]
        H.task_update(c, "human:ana", card_id, status="done", note="Thursday.")
        assert [(h["kind"], h["on"]) for h in task_holds.holds(c, H.task(c, work["id"]))] == [
            ("blocker", "bot:finance")
        ]
        assert QT.waiting_on(c, H.task(c, work["id"])) is None


def test_a_private_parents_waiting_names_its_person_only_where_the_answer_flows_up(api):
    parent, attempt = _bot_only_private_parent(api)
    _wait(api, parent["id"], attempt["token"], on="ben", note="Ship the zip fix in the next release? I'll tag it.")
    assert get(api, f"tasks/{parent['id']}", attempt["token"])["task"]["waiting_on"] == "human:ben"
    assert get(api, f"tasks/{parent['id']}", "ben-test")["task"]["waiting_on"] == "human:ben"
    r = api.get(f"/api/v2/tasks/{parent['id']}", headers={"Authorization": "Bearer cara-test"})
    assert r.status_code in (403, 404), r.text  # not a reader: no task, no waiting
    with api.app.state.store.transaction() as c:
        H.set_task_readers(c, "bot:cpo", parent["id"], remove=["human:ben"])
    # Ben no longer reads the parent: his question task's words stay off it, and so does his name.
    view = get(api, f"tasks/{parent['id']}", attempt["token"])["task"]
    assert view["status"] == "waiting" and view["waiting_on"] is None
    assert "Ben" not in str(view["holds"]) and "human:ben" not in str(view["holds"])


def test_a_file_with_no_waits_to_move_gets_no_staging_table(api):
    up = _migration("20261106112609_derived_waiting").up
    with api.app.state.store.transaction() as c:
        done = H.task_create(c, "human:ana", "Old one", "", "bot:ops", lint=False, private=False)
        c.execute("ALTER TABLE tasks ADD COLUMN waiting_on TEXT")
        c.execute("UPDATE tasks SET status='done', waiting_on='human:ana' WHERE id=?", (done["id"],))
        up(c)
        assert "waiting_on" not in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='task_waits_moved'").fetchone()


def test_the_migration_turns_old_waits_into_question_tasks_and_blockers(api):
    up = _migration("20261106112609_derived_waiting").up
    with api.app.state.store.transaction() as c:
        person = H.task_create(c, "human:ana", "Renew the domain", "", "bot:ops", lint=False, private=False)
        blocked = H.task_create(c, "human:ana", "Ship the gate", "", "bot:cpo", lint=False, private=False)
        pre = H.task_create(c, "human:ana", "Review the gate", "", "bot:ops", lint=False, private=False)
        TR.relate(c, "bot:cpo", blocked["id"], pre["id"], "blocked_by")
        loose = H.task_create(c, "human:ana", "Plan the launch", "", "bot:cpo", lint=False, private=False)
        done = H.task_create(c, "human:ana", "Old one", "", "bot:ops", lint=False, private=False)
        c.execute("ALTER TABLE tasks ADD COLUMN waiting_on TEXT")
        c.executemany(
            "UPDATE tasks SET status=?, waiting_on=?, note=? WHERE id=?",
            [
                (
                    "waiting",
                    "human:ana",
                    "Blocked on Ana Rivera: Renew it for a year? I'll pay the invoice.",
                    person["id"],
                ),
                ("waiting", "bot:ops", "Needs the review first.", blocked["id"]),
                ("waiting", "bot:ops", "Needs the plan from ops.", loose["id"]),
                ("done", "human:ana", "Finished.", done["id"]),
            ],
        )
        up(c)
        assert "waiting_on" not in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        assert {r[0] for r in c.execute("SELECT task_id FROM task_waits_moved")} == {
            person["id"],
            blocked["id"],
            loose["id"],
        }
        QT.move_waits(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='task_waits_moved'").fetchone()
        # A person: one open question task for them under the task, the old note as its question.
        [card_id] = TR.child_ids(c, person["id"])
        card = H.task(c, card_id)
        assert card["owner_id"] == "human:ana" and QT.is_open(card)
        assert QT.fields(card)["questions"][0]["question"] == "Renew it for a year? I'll pay the invoice."
        assert QT.waiting_on(c, H.task(c, person["id"])) == "human:ana"
        # A bot with a blocker it owns: the blocker holds it, as before; the note is kept.
        assert H.task(c, blocked["id"])["note"] == "Needs the review first." and TR.child_ids(c, blocked["id"]) == []
        assert H.wait_detail(c, H.task(c, blocked["id"]))["on"] == "bot:ops"
        # A bot with nothing of its blocking: a line on the note says who it waited on.
        assert H.task(c, loose["id"])["note"].startswith("Waiting on ")
        assert TR.child_ids(c, done["id"]) == []
        # Running again changes nothing.
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
        up(c)
        QT.move_waits(c)
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before


def test_the_strategy_bots_merged_question_task_passes_its_answer_on_when_closed(api):
    from backend.tests.helpers.strategy_bot_suggests import OWNER, STRATEGY_BOT, ask_ana, level, with_strategy_bot

    token = with_strategy_bot(api)
    level(api, 2)
    first, second = ask_ana(api, "ops", "Ship the pricing page"), ask_ana(api, "coo", "Plan the launch")
    item = post(
        api,
        "strategy-bot/merge",
        {"person": OWNER, "title": "Which pricing page first?", "question": "Which one?", "tasks": [first, second]},
        token=token,
    )["task"]
    assert (item["owner_id"], item["requester_id"], item["questions"][0]["options"]) == (OWNER, STRATEGY_BOT, [])
    since = _mark(api)
    _answer(api, item["id"], {"answers": {}, "other": "The landlord page."})
    for bot in ("bot:ops", "bot:coo"):
        said = [m for m in _to(api, bot, since) if "The landlord page." in m]
        assert len(said) == 1, (bot, _to(api, bot, since))  # each bot that asked hears it once
    with api.app.state.store.read() as c:
        assert H.task(c, item["id"])["status"] == "done"
        for task_id in (first, second):
            assert not H.unanswered_ask(c, H.task(c, task_id))
        assert {r[0] for r in c.execute("SELECT state FROM strategy_merges WHERE merged_into=?", (item["id"],))} == {
            "answered"
        }


def _bot_task(c, title="Renew the domain", owner="bot:ops", **kw):
    return H.task_create(c, "human:ana", title, "", owner, lint=False, private=kw.pop("private", False), **kw)


def _stage(c, *pairs):
    c.execute(
        "CREATE TABLE IF NOT EXISTS task_waits_moved(task_id TEXT PRIMARY KEY, waiting_on TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    for task_id, on, note in pairs:
        c.execute("UPDATE tasks SET status='waiting', note=? WHERE id=?", (note, task_id))
        c.execute("INSERT INTO task_waits_moved VALUES(?,?,?)", (task_id, on, H.now()))


def _another_bots_ask(c, row):
    old_ask(
        c,
        "bot:cpo",
        "human:ana",
        "Which registrar do we use?",
        conversation_id=row["conversation_id"],
        refs={"task": row["id"]},
    )


def test_the_next_start_moves_the_staged_waits_once(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = _bot_task(c)
        _stage(c, (row["id"], "human:ana", "Renew it for a year? I'll pay the invoice."))
    store.initialize(seed_market=False)
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='task_waits_moved'").fetchone()
        [card_id] = TR.child_ids(c, row["id"])
        assert QT.waiting_on(c, H.task(c, row["id"])) == "human:ana" and QT.is_open(H.task(c, card_id))
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    store.initialize(seed_market=False)  # every start calls it; with nothing staged it does nothing
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before


def test_another_bots_old_ask_does_not_stand_for_the_owners_wait(api):
    with api.app.state.store.transaction() as c:
        waited, moved = _bot_task(c), _bot_task(c, "Renew the certificate")
        _another_bots_ask(c, waited)
        _another_bots_ask(c, moved)
        H.task_update(c, "bot:ops", waited["id"], status="waiting", waiting_on="human:ana", note=ASK)
        _stage(c, (moved["id"], "human:ana", ASK))
        QT.move_waits(c)
        for row in (waited, moved):
            # Only the owner's own ask holds its task (task_holds): cpo's leaves it, so Ana gets ops's question task.
            [card_id] = TR.child_ids(c, row["id"])
            assert H.task(c, card_id)["requester_id"] == "bot:ops"
            assert QT.waiting_on(c, H.task(c, row["id"])) == "human:ana"


def test_a_refused_question_task_keeps_the_wait_as_a_note_line(api, monkeypatch):
    def refuse(*_a, **_k):
        raise H.Refused("caps", "Ana has too many open questions")

    with api.app.state.store.transaction() as c:
        row = _bot_task(c)
        _stage(c, (row["id"], "human:ana", "Blocked on Ana Rivera: Renew it for a year? I'll pay the invoice."))
        monkeypatch.setattr(H, "_ask_in_subtask", refuse)
        QT.move_waits(c)
        after = H.task(c, row["id"])
        assert after["note"].startswith("Waiting on Ana") and "Renew it for a year?" in after["note"]
        assert TR.child_ids(c, row["id"]) == []
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='task_waits_moved'").fetchone()


def test_on_nobody_withdraws_the_question_task_quietly(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"])
    with api.app.state.store.read() as c:
        [card_id] = TR.child_ids(c, work["id"])
    since = _mark(api)
    _wait(api, work["id"], attempt["token"], on="", note="Not needed after all.")
    with api.app.state.store.read() as c:
        assert H.task(c, card_id)["status"] == "closed"
        assert QT.waiting_on(c, H.task(c, work["id"])) is None
    assert _to_ops(api, since) == []  # taking it back wakes nobody
    assert not [i for i in get(api, "needs-you")["items"] if i["id"] == card_id]


def test_the_person_a_bots_task_waits_on_cannot_close_the_bots_task(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"], on="ben")
    with api.app.state.store.transaction() as c:
        try:
            H.task_close(c, "human:ben", work["id"], note="Not now")
        except H.Refused as refused:
            assert refused.rule == "close"
        else:
            raise AssertionError("Ben closed the bot's task")
        [card_id] = TR.child_ids(c, work["id"])
        assert H.task(c, card_id)["owner_id"] == "human:ben"  # his question task is his to answer or decline


def test_a_board_reads_private_waits_in_the_same_statements_however_many(api):
    def statements(rows):
        with api.app.state.store.read() as c:
            seen = []
            c.set_trace_callback(seen.append)
            try:
                found = QT.waiting_on_many(c, rows)
            finally:
                c.set_trace_callback(None)
        return found, len(seen)

    with api.app.state.store.transaction() as c:
        ids = []
        for n in range(5):
            row = _bot_task(c, f"Plan launch {n}", private=True)
            H.task_update(c, "bot:ops", row["id"], status="waiting", waiting_on="human:ana", note=ASK)
            c.execute(
                "UPDATE tasks SET private=1 WHERE id IN (SELECT from_task FROM task_relations WHERE to_task=?)",
                (row["id"],),
            )
            ids.append(row["id"])
        rows = [H.task(c, i) for i in ids]
    one, few = statements(rows[:1]), statements(rows)
    assert one[0] == {ids[0]: "human:ana"} and few[0] == {i: "human:ana" for i in ids}
    assert one[1] == few[1], (one[1], few[1])


def test_hub_sql_reads_who_a_task_waits_on_from_task_waits(api):
    work, attempt = _work(api)
    _wait(api, work["id"], attempt["token"])
    r = api.post(
        "/api/v2/sql",
        json={"sql": "SELECT task_id, waiting_on FROM task_waits"},
        headers={"Authorization": "Bearer ana-test"},
    )
    assert r.status_code == 200, r.text
    assert [work["id"], "human:ana"] in r.json()["rows"]


def test_a_stalled_tasks_stand_keeps_the_slot_where_waiting_on_was(api, monkeypatch):
    seen, dumps = [], H.json.dumps
    with api.app.state.store.transaction() as c:
        row = H.task(c, _bot_task(c)["id"])
        monkeypatch.setattr(H.json, "dumps", lambda value, **kw: seen.append(value) or dumps(value, **kw))
        H._stall_stand(c, row)
        monkeypatch.undo()
    # status, step, owner, the old waiting_on (None now, so stands recorded before keep their hash), relations, comments
    [parts] = [v for v in seen if isinstance(v, list) and len(v) == 6]
    assert parts[:4] == [row["status"], row.get("step_id"), row["owner_id"], None]


def test_changing_a_groups_members_makes_a_private_waits_reason_again(api, monkeypatch):
    from backend import groups

    with api.app.state.store.transaction() as c:
        row = _bot_task(c, private=True)
        H.task_update(c, "bot:ops", row["id"], status="waiting", waiting_on="human:ana", note=ASK)
        c.execute(
            "UPDATE tasks SET private=1 WHERE id IN (SELECT from_task FROM task_relations WHERE to_task=?)",
            (row["id"],),
        )
        refreshed = []
        monkeypatch.setattr(task_holds, "refresh_many", lambda c, ids, cleared=None: refreshed.extend(ids))
        task_holds.refresh_private_names(c)
        assert row["id"] in refreshed
        called = []
        monkeypatch.setattr(groups, "_save", lambda *a, **k: None)
        monkeypatch.setattr(groups, "refresh_teams", lambda *a, **k: None)
        monkeypatch.setattr(task_holds, "refresh_private_names", lambda c: called.append(True))
        groups._finish(c, {}, None, [])
        assert called
