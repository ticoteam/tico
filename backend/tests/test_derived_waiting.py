"""Who a task waits on is derived, not stored (threads + tasks, step 6b): `--status waiting --on <person>` files a
question task with no options under the task, the task's `waiting_on` is that question task's owner while it is
open, and answering it opens the task again with one wake. The migration's old rows become question tasks or the
bot's blocker; a private parent names its person only where the question task's words may flow up."""
import importlib.util
from pathlib import Path

from backend import hubdb as H, question_tasks as QT, task_holds, task_relations as TR
from backend.tests.test_api import api, get, post  # noqa: F401  (fixture)
from backend.tests.test_question_tasks import _answer, _bot_only_private_parent, _mark, _to, _to_ops, _work

ASK = "Pick the launch date? I'll book the venue after."


def _wait(api, task_id, token, on="ana", note=ASK, expected=200):
    version = get(api, f"tasks/{task_id}", token)["task"]["version"]
    return post(api, f"tasks/{task_id}", {"version": version, "status": "waiting", "waiting_on": on, "note": note},
                token, expected=expected)


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
        assert "waiting_on" not in H.task(c, work["id"])          # nothing stored on the task
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
        assert [(h["kind"], h["on"]) for h in task_holds.holds(c, H.task(c, work["id"]))] == [("blocker", "bot:finance")]
        assert QT.waiting_on(c, H.task(c, work["id"])) is None


def test_a_private_parents_waiting_names_its_person_only_where_the_answer_flows_up(api):
    parent, attempt = _bot_only_private_parent(api)
    _wait(api, parent["id"], attempt["token"], on="ben", note="Ship the zip fix in the next release? I'll tag it.")
    assert get(api, f"tasks/{parent['id']}", attempt["token"])["task"]["waiting_on"] == "human:ben"
    assert get(api, f"tasks/{parent['id']}", "ben-test")["task"]["waiting_on"] == "human:ben"
    r = api.get(f"/api/v2/tasks/{parent['id']}", headers={"Authorization": "Bearer cara-test"})
    assert r.status_code in (403, 404), r.text                 # not a reader: no task, no waiting
    with api.app.state.store.transaction() as c:
        H.set_task_readers(c, "bot:cpo", parent["id"], remove=["human:ben"])
    # Ben no longer reads the parent: his question task's words stay off it, and so does his name.
    view = get(api, f"tasks/{parent['id']}", attempt["token"])["task"]
    assert view["status"] == "waiting" and view["waiting_on"] is None
    assert "Ben" not in str(view["holds"]) and "human:ben" not in str(view["holds"])


def _migration(name):
    path = Path(__file__).resolve().parents[1] / "migrations" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        c.executemany("UPDATE tasks SET status=?, waiting_on=?, note=? WHERE id=?", [
            ("waiting", "human:ana", "Blocked on Ana Rivera: Renew it for a year? I'll pay the invoice.", person["id"]),
            ("waiting", "bot:ops", "Needs the review first.", blocked["id"]),
            ("waiting", "bot:ops", "Needs the plan from ops.", loose["id"]),
            ("done", "human:ana", "Finished.", done["id"])])
        up(c)
        assert "waiting_on" not in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        assert {r[0] for r in c.execute("SELECT task_id FROM task_waits_moved")} == \
            {person["id"], blocked["id"], loose["id"]}
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
    from backend.tests.test_strategy_bot_suggests import OWNER, STRATEGY_BOT, ask_ana, level, with_strategy_bot
    token = with_strategy_bot(api)
    level(api, 2)
    first, second = ask_ana(api, "ops", "Ship the pricing page"), ask_ana(api, "coo", "Plan the launch")
    item = post(api, "strategy-bot/merge", {"person": OWNER, "title": "Which pricing page first?",
                                            "question": "Which one?", "tasks": [first, second]}, token=token)["task"]
    assert (item["owner_id"], item["requester_id"], item["questions"][0]["options"]) == (OWNER, STRATEGY_BOT, [])
    since = _mark(api)
    _answer(api, item["id"], {"answers": {}, "other": "The landlord page."})
    for bot in ("bot:ops", "bot:coo"):
        said = [m for m in _to(api, bot, since) if "The landlord page." in m]
        assert len(said) == 1, (bot, _to(api, bot, since))         # each bot that asked hears it once
    with api.app.state.store.read() as c:
        assert H.task(c, item["id"])["status"] == "done"
        for task_id in (first, second):
            assert not H.open_task_asks(c, H.task(c, task_id))
        assert {r[0] for r in c.execute("SELECT state FROM strategy_merges WHERE merged_into=?", (item["id"],))} == \
            {"answered"}
