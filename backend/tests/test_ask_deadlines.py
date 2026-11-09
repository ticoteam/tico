"""A bot's question task to a person goes ahead on the bot's default when it is safe to, and closes on its date."""

from backend.tests.helpers.api import headers, post  # noqa: F401  (fixture)
from backend import ask_deadlines, question_tasks as QT, team_rules
from backend.store import H


def _ask(c, title, default=None, days=0):
    """Ops asks Ana a Yes / No question task under its own task (which waits on it), `days` ago."""
    task = H.task_create(c, "bot:ops", title, "x", "bot:ops", lint=False)
    card = QT.ask_plain(c, "bot:ops", "human:ana", title, parent=task)
    QT.store(
        c,
        card["id"],
        {
            "questions": [
                {
                    "id": "decision",
                    "header": "",
                    "question": title,
                    "why": "",
                    "options": [{"label": "Yes"}, {"label": "No"}],
                    "multi": False,
                }
            ],
            **({"if_no_answer": default} if default else {}),
        },
    )
    then = H.shift(H.now(), days=-days)
    c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (task["id"],))
    c.execute("UPDATE tasks SET created_at=?, asked_at=? WHERE id IN (?,?)", (then, then, card["id"], task["id"]))
    c.execute(
        "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?)",
        (then, card["id"], task["id"]),
    )
    return task, H.task(c, card["id"])


def test_a_reversible_default_goes_ahead_after_the_teams_days_and_a_send_waits(api):
    with api.app.state.store.transaction() as c:
        safe, safe_ask = _ask(c, "Archive the old draft?", {"do": "Yes", "kind": "reversible"}, days=4)
        send, send_ask = _ask(c, "Email the vendor?", {"do": "Yes", "kind": "send"}, days=4)
        fresh, fresh_ask = _ask(c, "Rename the board?", {"do": "Yes", "kind": "reversible"}, days=1)
        done = ask_deadlines.sweep(c)
    assert done["went_ahead"] == [safe_ask["id"]]
    with api.app.state.store.read() as c:
        answered = H.task(c, safe_ask["id"])
        answer = QT.fields(answered)["answer"]
        assert answered["status"] == "done" and not QT.is_open(answered)
        assert answer["by"] == H.KEEPER and answer["answers"] == {"decision": ["Yes"]}
        assert "goes ahead" in answer["text"]
        assert QT.waiting_on(c, H.task(c, safe["id"])) is None  # settled: it waits on her no more
        assert QT.is_open(H.task(c, send_ask["id"])) and QT.is_open(H.task(c, fresh_ask["id"]))
        assert QT.waiting_on(c, H.task(c, send["id"])) == "human:ana"

    # The owner turns it off: nothing goes ahead on its own.
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"bots_go_ahead": False})
        _, later = _ask(c, "Move the card?", {"do": "Yes", "kind": "reversible"}, days=5)
        assert ask_deadlines.sweep(c)["went_ahead"] == []


def test_a_task_handed_to_a_person_closes_after_two_weeks_and_an_undated_question_waits_for_the_batch(api):
    with api.app.state.store.transaction() as c:
        _, old = _ask(c, "Which logo do we use?", days=15)
        handed = H.task_create(c, "bot:ops", "Sign the venue form", "x", "human:ana", lint=False)
        touched = H.task_create(c, "bot:ops", "Pick the venue", "x", "human:ana", lint=False)
        later = H.task_create(
            c, "bot:ops", "Renew the domain before Dec 1", "x", "human:ana", H.shift(H.now(), days=40)[:10], lint=False
        )
        for row in (handed, touched, later):
            c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(H.now(), days=-15), row["id"]))
            c.execute(
                "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
                (H.shift(H.now(), days=-15), row["id"]),
            )
        H.task_comment(c, "human:ana", touched["id"], "Still looking, give me a few days")
        done = ask_deadlines.sweep(c)
    # Undated questions go back to their bot in the weekday batch (backend/still_needed.py), never one by one here.
    assert done["expired"] == [] and done["closed"] == [handed["id"]]
    with api.app.state.store.read() as c:
        assert QT.is_open(H.task(c, old["id"]))
        closed = H.task(c, handed["id"])
        assert closed["status"] == "closed" and "expired" in closed["note"]
        assert H.task(c, touched["id"])["status"] == "open"
        assert H.task(c, later["id"])["status"] == "open"  # due later: not expired

    # A wait on a person (`--status waiting --on`) is a question task with no date: it never closes here; it goes
    # back to its bot in the weekday batch (test_question_tasks).
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Book the venue", "x", "bot:ops", private=False)
        card = H.task_update(
            c,
            "bot:ops",
            task["id"],
            status="waiting",
            waiting_on="human:ana",
            note="Approve the venue for Friday? If yes, I will book it.",
        )["asked_in"]
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(H.now(), days=-15), card["id"]))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
            (H.shift(H.now(), days=-15), card["id"]),
        )
        assert card["id"] not in ask_deadlines.sweep(c)["closed"]
        assert H.task(c, card["id"])["status"] == "open" and H.task(c, task["id"])["status"] == "waiting"

    # A go-ahead date after day 14 is refused when asked; here one is set anyway: the question waits for its date,
    # then goes ahead and closes.
    with api.app.state.store.transaction() as c:
        _, far = _ask(
            c,
            "Archive the spring drafts?",
            {"do": "Yes", "kind": "reversible", "by": H.shift(H.now(), days=5)},
            days=15,
        )
        assert ask_deadlines.sweep(c) == {"went_ahead": [], "expired": [], "closed": []}
        assert ask_deadlines.sweep(c, H.shift(H.now(), days=6))["went_ahead"] == [far["id"]]


def test_a_bot_date_never_beats_the_teams_days_and_the_cli_must_say_the_kind(api):
    import pytest
    from clients import hubtools

    with api.app.state.store.transaction() as c:
        _, soon = _ask(
            c, "Tidy the board?", {"do": "Yes", "kind": "reversible", "by": H.shift(H.now(), minutes=-1)}, days=1
        )
        assert ask_deadlines.sweep(c)["went_ahead"] == []  # 1 day in: the team's 3 days still apply
    with pytest.raises(ValueError, match="default-kind"):
        hubtools.task_question(
            None, {"question": "Send the reply to the vendor?", "on": "ana", "if_no_answer": "Send it"}
        )


def test_a_bot_puts_its_default_on_a_question_and_a_past_or_far_deadline_is_refused(api):
    from datetime import datetime, timedelta, timezone
    from backend.tests.helpers.api import setup_attempt
    from backend.tests.helpers.mcp import call

    _, _, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"bots_go_ahead": False})  # on, a reversible default is not asked at all
    pacific = timezone(timedelta(hours=-8))
    by = (datetime.now(pacific) + timedelta(days=5)).replace(hour=9, minute=0, second=0, microsecond=0)
    err, out = call(
        api,
        "hub_task_question",
        {
            "question": "Archive the September drafts?",
            "on": "ana",
            "if_no_answer": {"do": "Yes", "kind": "reversible", "by": by.isoformat()},
        },
        token=attempt["token"],
    )
    assert not err, out
    refs = out["question"]["refs"]
    assert out["task"]["if_no_answer"] == refs["if_no_answer"]
    assert refs["if_no_answer"] == {
        "do": "Yes",
        "kind": "reversible",
        "by": f"{by.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S}.000000Z",
    }
    err, out = call(
        api,
        "hub_task_question",
        {
            "question": "Archive the August drafts?",
            "on": "ana",
            "if_no_answer": {"do": "Yes", "kind": "reversible", "by": "2020-01-01T00:00:00Z"},
        },
        token=attempt["token"],
    )
    assert err and "already past" in str(out)
    err, out = call(
        api,
        "hub_task_question",
        {
            "question": "Archive the July drafts?",
            "on": "ana",
            "if_no_answer": {"do": "Yes", "kind": "reversible", "by": "2099-01-02T00:00:00Z"},
        },
        token=attempt["token"],
    )
    assert err and "more than 14 days" in str(out)


def test_a_due_routine_occurrence_survives_the_sweep_and_an_ordinary_handed_task_does_not(api):
    # A bot hands Ana a task; it is past the team's days. A due-date routine's occurrence she owns is the same kind of
    # task, but its day is its deadline, so the sweep leaves it open whoever owns it now.
    old = H.shift(H.now(), days=-(ask_deadlines.EXPIRE_DAYS + 5))
    with api.app.state.store.transaction() as c:
        plain = H.task_create(c, "bot:ops", "Send the form", "x", "human:ana", lint=False)
        occurrence = H.task_create(
            c, "bot:ops", "Payroll", "x", "human:ana", lint=False, occurrence="auto_1:2026-01-02"
        )
        for task in (plain, occurrence):
            c.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, task["id"]))
        done = ask_deadlines.sweep(c)
    assert done["closed"] == [plain["id"]]
    with api.app.state.store.read() as c:
        assert H.task(c, occurrence["id"])["status"] == "open"
        assert H.task(c, occurrence["id"])["occurrence"] == "auto_1:2026-01-02"
