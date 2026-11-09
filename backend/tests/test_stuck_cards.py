"""The stuck rule (backend/stuck_cards.py), a pipeline rule: a card with no progress wakes the pipeline's manager once per
spell, a person gets one question when it is still stuck, and a question to a person expires after the pipeline's days.
Time moves by passing `at` to the sweeps; nothing waits."""

from backend import ask_deadlines
from backend import hubdb as H
from backend import question_tasks as QT
from backend import stuck_cards
from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)

ANA = "human:ana"
STAGES = [
    {"name": "Building", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


def later(hours):
    return H.shift(H.now(), hours=hours)


def pipeline(c, name="Launch", manager="coo", **settings):
    typ = H.type_create(c, ANA, name, STAGES, mover=True)
    c.execute("UPDATE task_types SET manager=?, pipeline_owner_id=? WHERE id=?", (manager, ANA, typ["id"]))
    for column, value in settings.items():
        c.execute(f"UPDATE task_types SET {column}=? WHERE id=?", (value, typ["id"]))
    return typ["id"]


def card(c, type_id, title="Ship the launch page", owner="bot:ops"):
    return H.task_create(c, ANA, title, "", owner, type=type_id, step="Building", lint=False)


def wakes(c, task_id, manager="bot:coo"):
    room = H.task(c, task_id)["conversation_id"]
    return [
        r[0]
        for r in c.execute(
            "SELECT body FROM messages WHERE conversation_id=? AND recipient_id=? AND refs LIKE '%stuck_cards%' "
            "ORDER BY created_at",
            (room, manager),
        )
    ]


def questions(c, task_id):
    return H._rows(
        c.execute(
            "SELECT q.* FROM task_relations r JOIN tasks q ON q.id=r.from_task WHERE r.to_task=? AND r.kind='parent' "
            "AND q.requester_id=? AND q.questions IS NOT NULL",
            (task_id, H.KEEPER),
        )
    )


def test_a_quiet_card_wakes_its_manager_once_and_progress_ends_the_spell(api):
    with api.app.state.store.transaction() as c:
        pid = pipeline(c)
        row = card(c, pid)
        assert stuck_cards.sweep(c, later(3))["woken"] == []  # under 4 h: not stuck
        assert stuck_cards.sweep(c, later(5))["woken"] == [row["id"]]
        [notice] = wakes(c, row["id"])
        assert notice.startswith("Stuck: Ship the launch page\nNo progress in 5 h.")
        assert "Unblock it, reassign it, split it or close it" in notice
        # Same spell: no second wake, and the manager's own line on the card is not progress.
        H.task_comment(c, "bot:coo", row["id"], "Reassigning this to Ben.")
        assert stuck_cards.sweep(c, later(6))["woken"] == []
        assert len(wakes(c, row["id"])) == 1
        # A comment from someone else is progress: the spell ends, and a new one wakes the manager again 4 h later.
        # (Its time is now, so the passes after it run from now again.)
        H.task_comment(c, ANA, row["id"], "I added the copy.")
        assert stuck_cards.sweep(c, later(2))["ended"] == [row["id"]]
        assert stuck_cards.sweep(c, later(3))["woken"] == []
        assert stuck_cards.sweep(c, later(5))["woken"] == [row["id"]]
        assert len(wakes(c, row["id"])) == 2
        # A finished card leaves the rule.
        H.task_update(c, ANA, row["id"], status="done", note="Shipped")
        assert stuck_cards.sweep(c, later(30)) == {"woken": [], "asked": [], "relayed": [], "ended": [row["id"]]}


def test_nothing_tracked_to_wait_on_is_stuck_at_once_and_a_person_question_under_a_day_is_not(api):
    from backend import task_holds

    with api.app.state.store.transaction() as c:
        pid = pipeline(c)
        stranded = card(c, pid, "Fix the footer")
        H.task_update(c, "bot:ops", stranded["id"], status="waiting", note="Waiting on the build")
        c.execute(
            "UPDATE task_holds SET cause='stranded', reason=? WHERE task_id=? AND kind='blocked' AND released_at IS NULL",
            (task_holds.NOTHING, stranded["id"]),
        )
        asking = card(c, pid, "Book the venue")
        H.task_update(
            c, "bot:ops", asking["id"], status="waiting", waiting_on=ANA, note="Approve the venue for Friday?"
        )
        done = stuck_cards.sweep(c, later(0.1))
        assert done["woken"] == [stranded["id"]]
        assert task_holds.NOTHING in wakes(c, stranded["id"])[0]
        # The question to Ana is 5 h old: waiting on a named person is not stuck. Past a day it is.
        assert asking["id"] not in stuck_cards.sweep(c, later(5))["woken"]
        assert stuck_cards.sweep(c, later(25))["woken"] == [asking["id"]]


def test_a_new_hold_reason_is_progress(api):
    from backend import task_holds

    with api.app.state.store.transaction() as c:
        pid = pipeline(c)
        row = card(c, pid)
        H.task_update(c, "bot:ops", row["id"], status="waiting", note="Waiting on the vendor")
        c.execute("UPDATE task_holds SET created_at=? WHERE task_id=? AND kind='blocked'", (later(-10), row["id"]))
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (later(-10), row["id"]))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (later(-10), row["id"])
        )
        task_holds.change(c, row["id"], "blocked", reason="Waiting on the vendor")  # same reason: not progress
        assert stuck_cards.sweep(c)["woken"] == [row["id"]]
        task_holds.change(c, row["id"], "blocked", reason="Waiting on the vendor's new quote")
        assert stuck_cards.sweep(c)["ended"] == [row["id"]]


def test_still_stuck_at_a_day_asks_the_pipeline_owner_once_and_the_manager_hears_the_answer(api):
    with api.app.state.store.transaction() as c:
        pid = pipeline(c)
        row = card(c, pid)
        assert stuck_cards.sweep(c, later(5))["woken"] == [row["id"]]
        assert stuck_cards.sweep(c, later(23))["asked"] == []
        assert stuck_cards.sweep(c, later(25))["asked"] == [row["id"]]
        [question] = questions(c, row["id"])
        assert question["owner_id"] == ANA and question["title"] == "Stuck: Ship the launch page"
        assert QT.is_open(H.task(c, question["id"]))
        assert "Unblock, reassign, split or close it" in QT.fields(question)["questions"][0]["question"]
        # Its own question does not end the spell, and nothing asks again.
        assert stuck_cards.sweep(c, later(30)) == {"woken": [], "asked": [], "relayed": [], "ended": []}
        H.task_update(c, ANA, question["id"], status="done", note="Give it to Ben.")
        assert stuck_cards.sweep(c, later(31))["relayed"] == [row["id"]]
        assert "Ana answered: Give it to Ben." in wakes(c, row["id"])[-1]
        assert stuck_cards.sweep(c, later(32))["relayed"] == []
        # A later spell on the same card wakes the manager again but never asks a second question.
        H.task_comment(c, ANA, row["id"], "Ben has it now.")
        stuck_cards.sweep(c)
        stuck_cards.sweep(c, later(10))
        assert stuck_cards.sweep(c, later(60))["asked"] == []
        assert len(questions(c, row["id"])) == 1


def test_pipeline_settings_set_the_hours_and_unset_ones_use_the_defaults(api):
    with api.app.state.store.transaction() as c:
        quick = pipeline(c, "Support", stuck_after_hours=1, ask_person_after_hours=2)
        slow = pipeline(c, "Launch")
        fast, normal = card(c, quick, "Answer the refund"), card(c, slow)
        done = stuck_cards.sweep(c, later(1.5))
        assert done["woken"] == [fast["id"]]
        assert stuck_cards.sweep(c, later(2.5))["asked"] == [fast["id"]]
        assert stuck_cards.sweep(c, later(4.5))["woken"] == [normal["id"]]
        row = c.execute("SELECT * FROM task_types WHERE id=?", (slow,)).fetchone()
        assert stuck_cards.settings(dict(row)) == {
            "stuck_after_hours": 4,
            "ask_person_after_hours": 24,
            "question_expiry_days": 7,
        }
        # No manager: the rule does not run on that pipeline.
        c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (quick,))
        other = card(c, quick, "Refund the second order")
        assert other["id"] not in stuck_cards.sweep(c, later(30))["woken"]


def test_a_question_to_a_person_expires_after_the_pipelines_days_with_a_note(api):
    assert ask_deadlines.EXPIRE_DAYS == 7
    with api.app.state.store.transaction() as c:
        pid = pipeline(c, question_expiry_days=3)
        row = card(c, pid)
        stuck_cards.sweep(c, later(5))
        stuck_cards.sweep(c, later(25))
        [question] = questions(c, row["id"])
        assert ask_deadlines.expiry_days(c, H.task(c, question["id"])) == 3
        at = H.shift(H.now(), days=2)
        assert question["id"] not in ask_deadlines.sweep(c, at)["expired"]
        at = H.shift(H.now(), days=3, minutes=1)
        assert ask_deadlines.sweep(c, at)["expired"] == [question["id"]]
        gone = H.task(c, question["id"])
        assert not QT.is_open(gone)
        assert "this question expired and closed. Tico: ask again if it still matters." in gone["note"]
        assert stuck_cards.sweep(c, at)["relayed"] == [row["id"]]
        assert "closed with no answer. Decide it yourself." in wakes(c, row["id"])[-1]
        # Off a pipeline that sets its own days, a question waits the default 7.
        plain = H.task_create(c, "bot:ops", "Pick the logo", "x", "bot:ops", lint=False)
        assert ask_deadlines.expiry_days(c, plain) == ask_deadlines.EXPIRE_DAYS


def test_the_settings_api_reads_and_writes_the_rule_and_null_goes_back_to_the_default(api):
    with api.app.state.store.transaction() as c:
        pid = pipeline(c)
    path = f"/api/v2/pipelines/{pid}/settings"
    body = get(api, f"pipelines/{pid}/settings")
    assert body["stuck_after_hours"] is None
    assert body["stuck_rule_defaults"] == {
        "stuck_after_hours": 4,
        "ask_person_after_hours": 24,
        "question_expiry_days": 7,
    }
    saved = api.patch(
        path,
        json={"stuck_after_hours": 2, "ask_person_after_hours": 12, "question_expiry_days": 5},
        headers=headers("ana-test"),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["stuck_after_hours"] == 2 and saved.json()["question_expiry_days"] == 5
    assert api.patch(path, json={"stuck_after_hours": 0}, headers=headers("ana-test")).status_code == 422
    assert api.patch(path, json={"stuck_after_hours": 2}, headers=headers("cara-test")).status_code in (403, 404)
    cleared = api.patch(path, json={"stuck_after_hours": None}, headers=headers("ana-test")).json()
    assert cleared["stuck_after_hours"] is None and cleared["ask_person_after_hours"] == 12


def test_a_parent_moves_with_its_open_children_and_is_not_woken_when_a_child_is_stuck(api):
    with api.app.state.store.transaction() as c:
        product, dev = pipeline(c, "Launch plans", manager="cpo"), pipeline(c, "Launch builds")
        epic = card(c, product, "Design the launch")
        ticket = H.task_create(
            c, ANA, "Build the launch page", "", "bot:ops", parent_id=epic["id"], type=dev, step="Building", lint=False
        )
        for row in (epic, ticket):
            c.execute("UPDATE tasks SET created_at=? WHERE id=?", (later(-10), row["id"]))
            c.execute(
                "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
                (later(-10), row["id"]),
            )
        # The child just moved: that is the parent's progress too, so neither is stuck.
        H.task_update(c, ANA, ticket["id"], step="Review")
        assert stuck_cards.sweep(c)["woken"] == []
        # Five hours on, the child is stuck: Engineering's manager hears it, Product's manager does not.
        done = stuck_cards.sweep(c, later(5))
        assert done["woken"] == [ticket["id"]]
        assert wakes(c, epic["id"], "bot:cpo") == []
        assert stuck_cards.sweep(c, later(30))["woken"] == []
        assert wakes(c, epic["id"], "bot:cpo") == []
        # The child finishes and the parent sits: now the parent is stuck on its own.
        H.task_update(c, ANA, ticket["id"], status="done", note="Shipped")
        assert stuck_cards.sweep(c, later(5))["woken"] == [epic["id"]]


def test_a_parent_whose_child_waits_on_a_person_is_not_stuck(api):
    with api.app.state.store.transaction() as c:
        product, dev = pipeline(c, "Launch plans", manager="cpo"), pipeline(c, "Launch builds")
        epic = card(c, product, "Design the launch")
        ticket = H.task_create(
            c, ANA, "Build the launch page", "", "bot:ops", parent_id=epic["id"], type=dev, step="Building", lint=False
        )
        for row in (epic, ticket):
            c.execute("UPDATE tasks SET created_at=? WHERE id=?", (later(-10), row["id"]))
            c.execute(
                "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
                (later(-10), row["id"]),
            )
        H.task_update(c, "bot:ops", ticket["id"], status="waiting", waiting_on=ANA, note="Which hero image?")
        # Five hours on, the child still waits on Ana (under a day): neither the child nor its parent is stuck.
        assert stuck_cards.sweep(c, later(5))["woken"] == []
        assert wakes(c, epic["id"], "bot:cpo") == []
        # Past a day the child is stuck and its own manager hears it; the parent is still not woken.
        assert stuck_cards.sweep(c, later(25))["woken"] == [ticket["id"]]
        assert wakes(c, epic["id"], "bot:cpo") == []
