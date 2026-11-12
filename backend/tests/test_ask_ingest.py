"""Ask ingest (backend/ask_ingest.py, spec ask-ingest.md v2): a bot's task for a person is judged before it lands.
With checks On, the budget tightens as the person's plate fills; SENT_BACK or FYI refuses the write
and the error says why and how to fix it, naming the bot's manager. A person's own write is never gated, and any
failure lets the item through as before."""

import importlib
import json
import sqlite3
import threading

import pytest

from backend import ask_ingest as A
from backend import hubdb as H
from backend.tests.old_asks import old_ask
from backend.tests.helpers.api import unrelated_task_managers
from backend.tests.helpers.task_readers import run_on
from backend.tests.helpers.task_readers import task as private_task
from backend.tests.helpers.api import setup_attempt  # noqa: F401
from backend.tests.helpers.file_reviews import publish
from backend.tests.helpers.tasks_board import api, get, headers, post
from backend.tests.helpers.ask_ingest import FakeModel, calls, gate, items, plate


def file(api, token, title="Pick the launch date", body="Two dates: Nov 3 or Nov 10. Which one?"):
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": title, "body": body}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def write_state(api):
    """The domain rows a refused ask must leave intact, including delivery and refusal/quarantine state."""
    with api.app.state.store.read() as c:
        return {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "tasks",
                "messages",
                "jobs",
                "visits",
                "requirements",
                "snoozes",
                "history_events",
                "idempotency",
                "refusals",
                "quarantine_holds",
                "org_rule_holds",
            )
        }


def handed(c, task_id, actor, to, at):
    """`actor` gives the task to `to` at `at`: the engine's reassign (made here by Tico, as the engine writes every owner
    change), with its history row dated and signed as the handoff the replay reads."""
    H.task_reassign(c, H.KEEPER, task_id, to, "Handed on")
    c.execute(
        "UPDATE history_events SET created_at=?, actor_id=? WHERE rowid=(SELECT max(rowid) FROM history_events "
        "WHERE subject_type='task' AND subject_id=? AND kind='reassign')",
        (at, actor, task_id),
    )


def assert_refused(api, response, before=None):
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "ask_refused" and not error["retryable"], response.text
    if before is not None:
        assert write_state(api) == before, "refusal rolls back every domain row and counts toward no hold"
    [item] = items(api)[-1:]
    assert item["outcome"] in (A.SENT_BACK, A.FYI) and item["ref"] == "" and item["message_id"] is None
    return error["detail"]


# ---- the decision table, plain code ----------------------------------------------------------------------------


def answers(clear=True, kind="decision", meets="none", urgency=1, recommends=False, covered="new"):
    return {
        "clear": clear,
        "kind": kind,
        "meets": meets,
        "urgency": urgency,
        "recommends": recommends,
        "covered": covered,
    }


@pytest.mark.parametrize(
    "load,given,expected",
    [
        (4, answers(), (A.THROUGH, "under_budget")),  # criterion 1
        (15, answers(), (A.SENT_BACK, "criteria")),  # criterion 2
        (15, answers(meets="line-4"), (A.THROUGH, "criteria")),  # criterion 3
        (
            40,
            answers(meets="line-4", recommends=True, urgency=1),
            (A.THROUGH, "recommends"),
        ),  # criterion 4: no urgency rule
        (40, answers(meets="line-4", recommends=True, urgency=2), (A.THROUGH, "recommends")),
        (40, answers(meets="line-4", recommends=False, urgency=3), (A.THROUGH, "urgent")),
        (40, answers(meets="line-4", recommends=False, urgency=1), (A.SENT_BACK, "recommends")),
        (40, answers(meets="none", recommends=True, urgency=2), (A.SENT_BACK, "recommends")),
        (120, answers(meets="line-4", recommends=True, urgency=2), (A.SENT_BACK, "urgent_only")),  # criterion 5
        (120, answers(urgency=3), (A.THROUGH, "urgent")),
        (0, answers(kind="status"), (A.FYI, "status")),  # criterion 7
        (50, answers(kind="chore"), (A.SENT_BACK, "recommends")),  # criterion 8
        (0, answers(kind="own_work"), (A.SENT_BACK, "own_work")),  # criterion 9
        (0, answers(clear=False), (A.SENT_BACK, "unclear")),
        (0, answers(clear=False, urgency=3), (A.THROUGH, "urgent")),
        (0, answers(clear=None), (A.FAILED_OPEN, "below_threshold")),
        (15, answers(meets=None), (A.FAILED_OPEN, "below_threshold")),
        (40, answers(meets="line-1", recommends=None), (A.FAILED_OPEN, "below_threshold")),
        (
            40,
            answers(meets="line-1", recommends=True, urgency=None),
            (A.THROUGH, "recommends"),
        ),  # the band reads no urgency
    ],
)
def test_the_budget_tightens_as_the_plate_fills(load, given, expected):
    assert A.decide(given, 10, load) == expected


def test_answers_under_threshold_read_as_unsure():
    raw = {
        "clear": {"noul": 0.5},
        "kind": {"choice": "decision", "confidence": 0.4},
        "urgency": {"score": 2.6, "confidence": 0.9},
        "recommends": {"noul": 0.1},
        "meets": {"choice": "line-1", "confidence": 0.95},
    }
    got = A.read(raw, {"clear": 0.7, "kind": 0.7, "urgency": 0.6, "recommends": 0.7, "meets": 0.7}, {"line-1": "x"}, {})
    assert got == {"clear": None, "kind": None, "recommends": False, "urgency": 3, "meets": "line-1", "covered": "new"}


# ---- through the write -----------------------------------------------------------------------------------------


def test_a_clear_task_under_budget_reaches_the_person(gate):
    api, model, token = gate
    plate(api, 4)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben"
    assert "ingest" not in out, "an item that goes through reads as before"
    [item], [call] = items(api), calls(api)
    assert (item["outcome"], item["reason"]) == (A.THROUGH, "under_budget")
    assert (item["call_id"], item["mode"]) == (call["id"], A.ON) and not reasons(api)
    assert len(model.calls) == 1 and model.calls[0]["label"].startswith("person-ask-ingest@")
    assert "Pick the launch date" == model.calls[0]["state"]["item"]["title"]


def test_over_budget_with_no_criterion_refuses_without_creating_anything(gate):
    api, model, token = gate
    plate(api, 15)
    before = write_state(api)
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"},
        headers=headers(token),
    )
    text = assert_refused(api, r, before)
    assert "15 open items" in text and "cpo" in text
    assert "ask again with the line of their criteria it meets" in text
    [item], [call] = items(api), calls(api)
    assert (item["outcome"], item["reason"], item["call_id"]) == (A.SENT_BACK, "criteria", call["id"])
    assert "Pick the launch date" not in call["detail"]


def test_meeting_a_criterion_gets_it_through_at_that_load(gate):
    api, model, token = gate
    plate(api, 15)
    model.meets = "$200 a month"
    out = file(api, token, "Raise the Gateway cap to $400 a month")
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    assert [(i["outcome"], i["reason"]) for i in items(api)] == [(A.THROUGH, "criteria")]


def test_a_stored_rehearsal_reads_and_acts_as_on(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE ask_ingest_settings SET mode='rehearsal' WHERE person='human:ben'")
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["mode"] == "on"
    model.kind = "own_work"
    before = write_state(api)
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Verify the preview cleanup and charges", "body": "Nov 3 or Nov 10?"},
        headers=headers(token),
    )
    assert "work you can do yourself" in assert_refused(api, r, before)
    [item] = items(api)
    assert (item["mode"], item["outcome"], item["would"]) == ("on", A.SENT_BACK, A.SENT_BACK)


def test_migration_0066_moves_a_stored_rehearsal_to_on_and_leaves_the_rest():
    """backend/migrations/0066_ask_filter_no_rehearsal.py: a person's or the team default's 'rehearsal' becomes 'on'; the
    other modes stay. A database without the table (a new one) is left alone."""
    migration = importlib.import_module("backend.migrations.0066_ask_filter_no_rehearsal")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ask_ingest_settings(person TEXT PRIMARY KEY, mode TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO ask_ingest_settings VALUES(?,?)",
        [("*", "rehearsal"), ("human:ben", "rehearsal"), ("human:ana", "off"), ("human:cara", "on")],
    )
    migration.up(conn)
    assert dict(conn.execute("SELECT person, mode FROM ask_ingest_settings").fetchall()) == {
        "*": "on",
        "human:ben": "on",
        "human:ana": "off",
        "human:cara": "on",
    }
    fresh = sqlite3.connect(":memory:")
    migration.up(fresh)
    assert fresh.execute("SELECT name FROM sqlite_master").fetchall() == []


@pytest.mark.parametrize("how", ["error", "timeout", "cap", "busy"])
def test_it_fails_open_and_records_the_failure(gate, monkeypatch, how):
    api, model, token = gate
    plate(api, 120)
    if how == "error":
        model.fail = RuntimeError("provider down")
    elif how == "timeout":
        monkeypatch.setattr(A, "DEADLINE", 0.2)
        model.hold = threading.Event()
    elif how == "busy":
        monkeypatch.setattr(A, "CONCURRENCY", 1)
        assert A.calls_for(api.app.state.store).slots.acquire(blocking=False)  # the one slot is taken
    else:
        monkeypatch.setattr(A, "daily_cap", lambda settings, role: 0)
    out = file(api, token)
    if model.hold:
        model.hold.set()
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert item["outcome"] == A.FAILED_OPEN and item["ref"] == out["task"]["id"]
    if how == "cap":
        assert item["reason"] == "budget" and not model.calls
    elif how == "busy":
        # Refused at once: nothing was asked, and no judge.call row counts against the day.
        assert item["reason"] == "busy" and not model.calls and not calls(api)
    else:
        [call] = calls(api)
        assert ('"error": "%s"' % how) in call["detail"]


def test_ask_ingest_and_rule_checks_have_their_own_slots(gate, monkeypatch):
    from backend import org_rule_check as K

    api, model, token = gate
    monkeypatch.setattr(A, "CONCURRENCY", 1)
    store = api.app.state.store
    assert A.calls_for(store).slots.acquire(blocking=False)  # ask ingest's one slot is taken
    assert K.calls_for(store).slots.acquire(blocking=False)  # a rule check still gets one
    K.calls_for(store).slots.release()


def test_a_persons_own_task_for_someone_is_not_gated_but_counts(gate):
    api, model, token = gate
    out = post(api, "tasks", {"owner": "ben", "title": "Read the contract", "body": "By Friday."})
    assert out["owner_id"] == "human:ben" and not model.calls
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 1


def test_no_decision_model_is_decide_off(gate):
    api, model, token = gate
    api.app.state.judge = None
    api.app.state.store.settings.rehearsal = True
    plate(api, 120)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out and not items(api) and not calls(api)


def test_mode_off_asks_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out and not model.calls


def test_a_settings_change_applies_to_the_next_item_only(gate):
    api, model, token = gate
    first = file(api, token)["task"]
    plate(api, 15)
    r = api.put(
        "/api/v2/ask-ingest/settings",
        headers=headers("ben-test"),
        json={"mode": "on", "budget": 5, "criteria": ["Pricing"]},
    )
    assert r.status_code == 200, r.text
    before = write_state(api)
    refused = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Pick the launch venue", "body": "Nov 3 or Nov 10?"},
        headers=headers(token),
    )
    assert_refused(api, refused, before)
    with api.app.state.store.read() as c:
        assert H.task(c, first["id"])["owner_id"] == "human:ben"


def test_settings_are_a_persons_own_and_the_team_default_is_an_admins(gate):
    api, model, token = gate
    own = get(api, "ask-ingest/settings", token="priya-test")
    assert own["settings"]["mode"] == "on" and own["settings"]["budget"] == 10, "ships as On"
    assert own["modes"] == ["off", "on"]
    assert len(own["settings"]["criteria"]) == 6 and own["may_edit_team"] is False
    body = {"mode": "on", "budget": 8, "chores_budget": 3, "criteria": ["Pricing"], "team": True}
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("priya-test"), json=body)
    assert r.status_code == 403
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ana-test"), json=body)
    assert r.status_code == 200, r.text
    assert get(api, "ask-ingest/settings", token="priya-test")["settings"]["budget"] == 8
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["budget"] == 10, "Ben's own stays"
    r = api.get("/api/v2/ask-ingest/settings", headers=headers(token))
    assert r.status_code == 403
    r = api.put(
        "/api/v2/ask-ingest/settings",
        headers=headers("ben-test"),
        json={"mode": "loud", "budget": 8, "chores_budget": 3, "criteria": []},
    )
    assert r.status_code == 422 and "off or on" in r.text


def test_a_put_of_the_retired_rehearsal_is_saved_as_on(gate):
    """docs/api.md: a request that worked keeps working. A PUT with mode 'rehearsal' (before this release it worked) is
    saved as On and answered as On."""
    api, model, token = gate
    r = api.put(
        "/api/v2/ask-ingest/settings",
        headers=headers("ben-test"),
        json={"mode": "rehearsal", "budget": 12, "chores_budget": 4, "criteria": []},
    )
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["mode"] == "on" and r.json()["settings"]["budget"] == 12
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["mode"] == "on"


# ---- questions and waits ---------------------------------------------------------------------------------------


def own_task(api, token, title="Plan the November launch"):
    r = api.post(
        "/api/v2/tasks", json={"owner": "ops", "title": title, "body": "Dates and venue."}, headers=headers(token)
    )
    assert r.status_code == 200 and "ingest" not in r.json(), r.text
    return r.json()["task"]


def on_plate(api, tid, person="human:ben"):
    with api.app.state.store.read() as c:
        return A.on_plate(c, person, tid)


def test_the_decisions_readout_counts_refusals_and_reconciles_their_calls(gate):
    api, model, token = gate
    plate(api, 15)
    assert_refused(
        api,
        api.post(
            "/api/v2/tasks",
            json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"},
            headers=headers(token),
        ),
    )
    model.meets = "$200 a month"
    file(api, token, "Raise the Gateway cap to $400 a month")
    readout = get(api, "judge/readout")
    [row] = [r for r in readout["sets"] if r["set"] == A.QUESTION_SET]
    assert row["calls"] == 2
    assert row["gate"] == {"outcomes": {A.SENT_BACK: 1, A.THROUGH: 1}, "let_through": 0}
    assert row["reconcile"]["landed"] == 2 and row["reconcile"]["unexplained"] == 0


# ---- the same thing again: merged ------------------------------------------------------------------------------


def test_a_repeat_joins_the_open_item_and_the_answer_there_goes_back_to_the_bot(gate):
    api, model, token = gate
    first = file(api, token, "Pick the launch date for Acme", "Nov 3 or Nov 10?")["task"]
    model.covered = "launch date for Acme"
    second = file(api, token, "Launch date?", "Which day do we launch Acme: Nov 3 or Nov 10?")
    assert second["task"]["owner_id"] == "bot:ops", "the repeat stays with the bot"
    assert second["ingest"]["outcome"] == A.MERGED and "Pick the launch date for Acme" in second["ingest"]["text"]
    [_, merged] = items(api)
    assert (merged["merged_into"], merged["reason"]) == (first["id"], "covered")
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 1, "one item on the plate, not two"
    r = api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Nov 10."}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        passed = [m for m in H.task_comments(c, second["task"]["id"]) if "Nov 10." in (m["body"] or "")]
        assert len(passed) == 1 and H.task(c, second["task"]["id"])["owner_id"] == "bot:ops"
    api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Still Nov 10."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        assert not [m for m in H.task_comments(c, second["task"]["id"]) if "Still" in (m["body"] or "")], "once"


# ---- source access: a repeat joins only an item its bot may read in full (synthetic fixtures only) -----------------

RESTRICTED = {
    "see": {"everyone": True},
    "read": {"people": ["ana"], "teams": [], "bots": []},
    "write": {"everyone": True},
}


def add_task(api, actor, title, owner, private):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, actor, title, "", owner, lint=False, private=private)


def restrict(api, slug):
    """The bot's activity is read by Ana alone: its see stays open, so Ops sees the task and cannot read it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot=?", (json.dumps(RESTRICTED), slug))


def test_a_repeat_from_an_unrelated_bot_never_sees_another_bots_private_item(gate):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Acme pricing secret", "human:ben", private=True)["id"]
    model.covered = "Acme pricing"
    second = file(api, token, "Acme pricing again?", "Is the Acme price still $40?")["task"]
    assert all("covered" not in call["questions"] for call in model.calls), "no covered option was offered"
    assert all("Acme pricing secret" not in json.dumps(call) for call in model.calls)
    assert "Acme pricing secret" not in json.dumps(second)
    assert second["owner_id"] == "human:ben", "judged as new, under budget: it reaches the person"
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Use $40."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%Use $40.%'").fetchall()
        assert not told
        assert not [m for m in H.task_comments(c, second["id"]) if "Use $40." in (m["body"] or "")]


def test_a_company_task_whose_bot_hides_its_activity_from_the_asker_is_not_offered(gate):
    api, model, token = gate
    add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)
    restrict(api, "cmo")
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")["task"]
    assert all("covered" not in call["questions"] for call in model.calls)
    assert "Vendor escrow terms" not in json.dumps(model.calls)
    assert second["owner_id"] == "human:ben" and [i["merged_into"] for i in items(api)] == [None]


@pytest.mark.parametrize("how", ["private", "access", "plate"])
def test_an_item_lost_between_judgment_and_write_names_no_source(gate, how):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]

    def lose_access():
        if how == "private":
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE tasks SET private=1 WHERE id=?", (first,))
        elif how == "plate":  # still open and readable, but no longer the person's: handed to Ana
            with api.app.state.store.transaction() as c:
                H.task_reassign(c, H.KEEPER, first, "human:ana", "Handed to Ana")
        else:
            restrict(api, "cmo")

    model.before, model.covered = lose_access, "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")
    assert model.calls, "the repeat was judged"
    with api.app.state.store.read() as c:
        assert H.task(c, first)["status"] == "open", "the source stays open: only the lost right fails"
    assert "Vendor escrow terms" not in json.dumps(second), "no title in the write's reply"
    assert second["task"]["owner_id"] == "human:ben", "the fallback is the ordinary fail-open path"
    [row] = [i for i in items(api) if i["ref"] == second["task"]["id"]]
    # covered_gone can only follow a merge the model picked: the offered source was the one it chose
    assert (row["outcome"], row["reason"], row["merged_into"]) == (A.FAILED_OPEN, "covered_gone", None)


@pytest.mark.parametrize("how", ["private", "access"])
def test_a_reply_is_not_forwarded_once_the_asker_lost_the_item(gate, how):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")["task"]
    assert second["owner_id"] == "bot:ops", "merged: the asker keeps its own task"
    if how == "private":
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET private=1 WHERE id=?", (first,))
    else:
        restrict(api, "cmo")
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Take the 30-day term."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%30-day term%'").fetchall()
        assert not told
        assert not [m for m in H.task_comments(c, second["id"]) if "30-day" in (m["body"] or "")]
        assert c.execute("SELECT passed_at FROM ask_ingest_items WHERE merged_into=?", (first,)).fetchone()[0] is None


def test_a_readable_repeat_merges_and_its_answer_is_delivered_once(gate):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")
    assert second["ingest"]["outcome"] == A.MERGED
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Take the 30-day term."}, headers=headers("ben-test"))
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Still the 30-day term."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = [m for m in H.task_comments(c, second["task"]["id"]) if "30-day term" in (m["body"] or "")]
    assert len(told) == 1 and "Vendor escrow terms" in told[0]["body"]


# ---- Shared ask budget and the way back to the team default ---------------------------------------------------------


def test_a_chore_lands_as_an_ordinary_need_and_counts_under_the_main_budget(gate):
    api, model, token = gate
    plate(api, 9)
    model.kind = "chore"
    tid = file(api, token, "Grant ops read access to the billing repo", "One click in GitHub settings.")["task"]["id"]
    assert on_plate(api, tid)
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 10
    before = write_state(api)
    assert_refused(
        api,
        api.post(
            "/api/v2/tasks",
            json={"owner": "ben", "title": "Grant docs access", "body": "Nov 3 or Nov 10?"},
            headers=headers(token),
        ),
        before,
    )


def test_a_person_goes_back_to_the_team_default(gate):
    api, model, token = gate
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["own"] is True
    r = api.delete("/api/v2/ask-ingest/settings", headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert r.json()["settings"] == {**get(api, "ask-ingest/settings", token="ben-test")["team"], "own": False}
    plate(api, 15)
    assert_refused(
        api,
        api.post(
            "/api/v2/tasks",
            json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"},
            headers=headers(token),
        ),
    )
    assert api.delete("/api/v2/ask-ingest/settings", headers=headers(token)).status_code == 403


# ---- the replay before On --------------------------------------------------------------------------------------


def test_a_replay_judges_a_past_day_as_on_and_moves_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    plate(api, 15)
    tid = file(api, token)["task"]["id"]  # Off: both items land as filed
    with api.app.state.store.transaction() as c:  # an old loose ask, as an older file holds it
        old_ask(c, "bot:ops", "human:ben", "Which venue do you want?")
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "on", "budget": 10, "chores_budget": 5, "criteria": []})
    before, calls_before = (on_plate(api, tid), len(items(api))), len(calls(api))
    day = H.now()[:10]
    r = api.post(
        "/api/v2/ask-ingest/replay", json={"person": "ben", "date": day, "limit": 1}, headers=headers("ben-test")
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["total"] == 2 and first["next_offset"] == 1
    [one] = first["items"]
    assert (one["kind"], one["ref"], one["would"], one["reason"]) == ("task", tid, A.SENT_BACK, "criteria")
    assert one["load"] >= 15
    rest = api.post(
        "/api/v2/ask-ingest/replay", json={"person": "ben", "date": day, "offset": 1}, headers=headers("ben-test")
    ).json()
    assert [i["kind"] for i in rest["items"]] == ["loose"] and rest["next_offset"] is None
    assert (on_plate(api, tid), len(items(api))) == before, "nothing on the plate moved, no item rows"
    replayed = calls(api)[calls_before:]
    assert len(replayed) == 2 and all('"mode": "replay"' in str(row) for row in replayed)
    assert all("Which venue" not in str(row) and "Pick the launch date" not in str(row) for row in replayed)


def test_a_replay_is_the_persons_own_or_an_admins(gate):
    api, model, token = gate
    day = H.now()[:10]
    assert (
        api.post(
            "/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("priya-test")
        ).status_code
        == 403
    )
    assert (
        api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers(token)).status_code
        == 403
    )
    api.app.state.judge = None
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("ben-test"))
    assert r.status_code == 409, r.text


# ---- A bot's handoff to a person is gated --------------------


def test_a_bot_handing_its_task_to_a_person_is_refused_atomically(gate):
    api, model, token = gate
    plate(api, 15)
    task = own_task(api, token, "Pick the launch date")
    before = write_state(api)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/owner",
        json={"version": task["version"], "owner": "ben", "why": "Ben picks the date"},
        headers=headers(token),
    )
    assert_refused(api, r, before)
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["version"] == task["version"]


# ---- Review 2: every way a bot asks a person is judged, against the plate before the item ----------------------


def plate_ids(api, person="human:ben"):
    with api.app.state.store.read() as c:
        return set(A._plate_tasks(c, person))


# ---- Review 2: the replay sees the plate just before each item, and an admin only what they may read ----------


def test_a_replay_never_offers_an_item_as_its_own_repeat(gate):
    api, model, token = gate
    plate(api, 9)
    model.covered = "launch"
    first = file(api, token)["task"]["id"]
    second = file(api, token, "Choose the Acme launch day", "Nov 3 or Nov 10?")["task"]["id"]  # a true repeat
    live = {i["ref"]: i for i in items(api)}
    assert (live[first]["would"], live[first]["load"]) == (A.THROUGH, 9)
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": H.now()[:10]}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    got = {i["ref"]: i for i in r.json()["items"]}
    assert (got[first]["would"], got[first]["load"]) == (A.THROUGH, 9), "agrees with the gate at B-1"
    assert got[second]["would"] == A.MERGED
    offered = [call["questions"].get("covered", {}).get("criteria", {}) for call in model.calls[-2:]]
    assert not [t for t in offered[0].values() if "launch" in t]
    assert "Pick the launch date" in offered[1].values()


def test_a_replay_shows_an_admin_only_the_titles_they_may_read(gate, monkeypatch):
    api, model, token = gate
    auth = api.app.state.auth
    monkeypatch.setattr(auth, "bot_admins", {*auth.bot_admins, "priya@acme.example"})  # an admin, not a party
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Ben's private salary review", "body": "x", "private": True},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    file(api, token, "Pick the launch date")
    with api.app.state.store.transaction() as c:
        old_ask(c, "bot:ops", "human:ben", "Which doctor do you see?")
    day = H.now()[:10]
    admin = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("priya-test"))
    assert admin.status_code == 200, admin.text
    shown = sorted(i["title"] for i in admin.json()["items"])
    assert shown == ["", "", "Pick the launch date"]
    assert "salary" not in admin.text and "doctor" not in admin.text
    own = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("ben-test"))
    assert {"Ben's private salary review", "Which doctor do you see?"} <= {i["title"] for i in own.json()["items"]}


def test_a_replay_hides_a_private_task_the_filter_kept_with_its_bot(gate):
    """A private task sent back stays the bot's: the person is no party to it, so their own replay shows no title. Their
    own task, and a question to them outside any task, they always see."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Bot-only vendor margin", "", "bot:ops", lint=False, private=True)
        mine = H.task_create(c, "bot:ops", "Pick the launch date", "", "human:ben", lint=False, private=True)
        assert not A._shown(c, None, "human:ben", {"kind": "task", "ref": kept["id"]})
        assert not A._shown(c, "human:ben", "human:ben", {"kind": "task", "ref": kept["id"]})
        assert A._shown(c, None, "human:ben", {"kind": "task", "ref": mine["id"]})
        assert A._shown(c, None, "human:ben", {"kind": "loose", "ref": "m1"})
        assert not A._shown(c, "human:priya", "human:ben", {"kind": "loose", "ref": "m1"})


def test_a_replay_shows_a_bot_task_only_where_the_viewer_may_read_it(gate):
    """A company-visible task the filter kept with its bot shows its title only where the full task-read rule allows it
    (Read on the bot): privacy alone is not enough."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Vendor margin review", "", "bot:ops", lint=False)
        it = {"kind": "task", "ref": kept["id"]}
        assert A._shown(c, "human:priya", "human:ben", it)  # privacy alone: shown
        assert not A._shown(c, "human:priya", "human:ben", it, lambda c, task: False)
        assert A._shown(c, "human:priya", "human:ben", it, lambda c, task: True)


@pytest.mark.parametrize("owner_event_at", ["2026-10-06T09:00:00", "2026-10-06T09:00:00.200"])
def test_a_handoff_replays_on_the_day_it_was_put_on_them(gate, owner_event_at):
    """A task the filter acted on counts on the day its ask row was written, not the day the bot created the task."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Renew the vendor contract", "", "bot:ops", lint=False)
        c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00' WHERE id=?", (kept["id"],))
        c.execute(
            "INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
            "VALUES('ai1','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,'2026-10-06T09:00:00')",
            (kept["id"],),
        )
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    assert kept["id"] not in [i["ref"] for i in monday]
    assert [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"] == [
        (kept["id"], "2026-10-06T09:00:00")
    ]

    with api.app.state.store.transaction() as c:
        # Let it through makes it theirs: it still counts on the day it was put on them, under the bot that asked.
        # The handoff is an owner change, recorded in the history as the hub writes it, then the task's owner.
        handed(c, kept["id"], "bot:ops", "human:ben", owner_event_at)
        human = H.task_create(c, "human:priya", "Priya's task the bot handed on", "", "bot:ops", lint=False)
        c.execute("UPDATE tasks SET created_at='2026-10-05T11:00:00' WHERE id=?", (human["id"],))
        c.execute(
            "INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
            "VALUES('ai2','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,'2026-10-06T09:30:00')",
            (human["id"],),
        )
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    assert not [i for i in monday if i["kind"] == "task"]
    assert [(i["ref"], i["asker"]) for i in tuesday if i["kind"] == "task"] == [
        (kept["id"], "bot:ops"),
        (human["id"], "bot:ops"),
    ]


def test_a_bot_handoff_while_the_filter_is_off_replays_but_a_human_handoff_does_not(gate):
    """Owner history supplies an Off handoff with the same task fields as an ask-row handoff, but only for bot actors."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        bot_task = H.task_create(
            c, "bot:ops", "Renew the vendor contract", "Check the renewal date", "bot:ops", lint=False
        )
        human_task = H.task_create(
            c, "human:priya", "Review the launch plan", "Confirm the milestones", "human:priya", lint=False
        )
        for task, actor in ((bot_task, "bot:ops"), (human_task, "human:priya")):
            c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00' WHERE id=?", (task["id"],))
            handed(c, task["id"], actor, "human:ben", "2026-10-06T09:00:00")
        listed = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")

    tasks = [item for item in listed if item["kind"] == "task"]
    assert tasks == [
        {
            "kind": "task",
            "ref": bot_task["id"],
            "asker": "bot:ops",
            "title": "Renew the vendor contract",
            "text": "Check the renewal date",
            "asked": None,
            "created_at": "2026-10-06T09:00:00",
        }
    ]


def test_a_filing_the_gate_judged_and_a_later_handoff_each_list_once(gate):
    """Filed for Ben on Oct 5 with the filter On (its ask row at creation), moved to the bot that evening, and handed
    back on Oct 6 with a new ask row. Oct 5 lists the filing once, from its ask row; Oct 6 lists the handoff once."""
    api, model, token = gate
    tid = file(api, token)["task"]["id"]
    [filing] = [i for i in items(api) if i["ref"] == tid]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00.000000Z' WHERE id=?", (tid,))
        c.execute("UPDATE ask_ingest_items SET created_at='2026-10-05T10:00:00.200000Z' WHERE id=?", (filing["id"],))
        handed(c, tid, "human:ben", "bot:ops", "2026-10-05T18:00:00.000000Z")
        handed(c, tid, "bot:ops", "human:ben", "2026-10-06T09:00:00.000000Z")
        c.execute(
            "INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
            "VALUES('ai-handoff','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,"
            "'2026-10-06T09:00:00.000000Z')",
            (tid,),
        )
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    shown = [(i["ref"], i["created_at"]) for i in monday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-05T10:00:00.200000Z")]
    shown = [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-06T09:00:00.000000Z")]


def test_a_handoff_just_after_midnight_is_not_the_filing_from_just_before(gate):
    """Filed for Ben at 23:59:40 on Oct 5 with the filter Off (no ask row), moved to the bot at 23:59:50, and handed
    back on Oct 6 at 00:00:10 with its ask row. Thirty seconds apart across midnight is two placements, not one: Oct 5
    lists the filing, Oct 6 the handoff."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    tid = file(api, token, "Renew the vendor contract")["task"]["id"]
    assert not [i for i in items(api) if i["ref"] == tid], "Off asks nothing, so the filing has no ask row"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at='2026-10-05T23:59:40.000000Z' WHERE id=?", (tid,))
        handed(c, tid, "human:ben", "bot:ops", "2026-10-05T23:59:50.000000Z")
        handed(c, tid, "bot:ops", "human:ben", "2026-10-06T00:00:10.000000Z")
        c.execute(
            "INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
            "VALUES('ai-midnight','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,"
            "'2026-10-06T00:00:10.000000Z')",
            (tid,),
        )
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    shown = [(i["ref"], i["created_at"]) for i in monday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-05T23:59:40.000000Z")]
    shown = [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-06T00:00:10.000000Z")]


@pytest.mark.parametrize(
    "actor,role,via,expected",
    [
        ("bot:ops", "bot", "botops", True),
        ("bot:ops", "bot", "assistant", False),
        ("human:ben", "human", "", False),
        ("human:ben", "human", "botops", False),
        ("human:ben", "human", "assistant", False),
        ("bot:assistant", "bot", "", False),
        ("bot:assistant", "bot", "botops", False),
    ],
)
def test_gate_preserves_person_and_assistant_exclusions(actor, role, via, expected):
    from backend.auth import Identity

    assert A.gated(Identity(actor, role, via=via), "assistant") is expected


# A RuntimeError ("error") is covered by test_a_settings_failure_before_the_verdict_keeps_the_failed_open_record.
@pytest.mark.parametrize("where,reason", [("load_set", "not_found"), ("server_engine", "unavailable")])
def test_preflight_exception_keeps_failed_open_record(gate, monkeypatch, where, reason):
    from clients.judge import JudgeError

    api, model, token = gate

    def fail(*args, **kwargs):
        raise JudgeError(reason, "private item text must not be recorded")

    monkeypatch.setattr(A.J if where == "load_set" else A, where, fail)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, reason)
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert not calls(api) and not model.calls
    assert "private item text" not in str(item)


def test_exception_after_judging_keeps_call_link_and_fails_open(gate, monkeypatch):
    api, model, token = gate
    original = A._criteria
    seen = []

    def criteria(own):
        seen.append(True)
        if len(seen) == 2:
            raise RuntimeError("could not assemble verdict")
        return original(own)

    monkeypatch.setattr(A, "_criteria", criteria)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item], [call] = items(api), calls(api)
    assert (item["outcome"], item["reason"], item["call_id"]) == (A.FAILED_OPEN, "error", call["id"])
    assert len(model.calls) == 1


# ---- every judged call ends in a row or a named reason (Dev 702) ------------------------------------------------------


def reasons(api):
    """{call id: reason} for the unrecorded events the gate named (ask_ingest.unrecorded)."""
    with api.app.state.store.read() as c:
        return {
            r["target"]: json.loads(r["detail"])["why"]
            for r in H._rows(c.execute("SELECT target, detail FROM events WHERE action=?", (A.UNRECORDED,)))
        }


def test_a_saved_key_retry_makes_no_new_judge_call(gate):
    api, model, token = gate
    key = "retry-the-same-create"
    body = {"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"}
    first = api.post("/api/v2/tasks", json=body, headers={**headers(token), "Idempotency-Key": key})
    assert first.status_code == 200, first.text
    again = api.post("/api/v2/tasks", json=body, headers={**headers(token), "Idempotency-Key": key})
    assert again.status_code == 200 and again.json()["task"]["id"] == first.json()["task"]["id"]
    assert len(model.calls) == 1 and len(calls(api)) == 1 and len(items(api)) == 1


def test_a_refused_create_writes_no_row_and_its_call_is_labelled_refused(gate):
    api, model, token = gate
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Goal gone", "body": "x", "goal_id": "no-such-goal"},
        headers=headers(token),
    )
    assert r.status_code == 404, r.text
    [call] = calls(api)
    assert len(model.calls) == 1 and not items(api) and reasons(api) == {call["id"]: "refused"}


def test_a_privacy_refused_create_writes_no_row_and_its_call_is_labelled_refused(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    private_task(api, "bot:cpo", "bot:cmo", "Zip triage")
    attempt = run_on(api, "cmo")  # the run carries a private task: a company-visible create is refused
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?", "private": False},
        headers=headers(attempt["token"]),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    [call] = calls(api)
    assert len(model.calls) == 1 and not items(api) and reasons(api) == {call["id"]: "refused"}


def test_the_readout_reconciles_every_call_to_a_row_or_a_named_reason(gate):
    api, model, token = gate
    plate(api, 15)
    assert_refused(
        api,
        api.post(
            "/api/v2/tasks",
            json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"},
            headers=headers(token),
        ),
    )
    model.meets = "Strategy"
    api.post(
        "/api/v2/tasks",
        json={"owner": "ben", "title": "Goal gone", "body": "x", "goal_id": "no-such-goal"},
        headers=headers(token),
    )  # refused: a named reason
    row = next(r for r in get(api, "judge/readout")["sets"] if r["set"] == A.QUESTION_SET)
    assert row["reconcile"] == {"calls": 2, "landed": 1, "reasons": {"refused": 1}, "unexplained": 0, "no_call": 0}
    assert row["versions"] == ["2"], "the readout groups answers by the set's version, now 2"
    assert (
        "calls reconciled: 2 = 1 with an item row, refused 1; unexplained 0; rows with no call 0"
        in get(api, "judge/readout")["text"]
    )


def test_a_call_with_neither_a_row_nor_a_reason_is_shown_as_unexplained(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "judge.call", "person-ask-ingest@2", {"label": "person-ask-ingest@2", "mode": "on"})
    row = next(r for r in get(api, "judge/readout")["sets"] if r["set"] == A.QUESTION_SET)
    assert row["reconcile"]["unexplained"] == 1 and row["reconcile"]["calls"] == 1


def test_a_settings_failure_before_the_verdict_keeps_the_failed_open_record(gate, monkeypatch):
    """The settings read is the first thing preflight does after the person is known: a failure there still writes
    the failed_open row, and the create still succeeds (Dev 702 QA)."""
    api, model, token = gate
    real, seen = A.settings, []

    def fails_once(c, person):
        if not seen:
            seen.append(person)
            raise RuntimeError("settings unreadable")
        return real(c, person)

    monkeypatch.setattr(A, "settings", fails_once)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, "error")
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert "settings unreadable" not in str(item), "a plain error's message stays out of the failed-open record (#1798)"
    assert not calls(api) and not model.calls


def test_a_settings_read_that_always_fails_still_lands_a_failed_open_row(gate, monkeypatch):
    """The settings read fails on every call, in preflight and again inside the write: the create still succeeds,
    and its one row is a failed open with no judge call (Dev 702 QA retest)."""
    api, model, token = gate

    def always_fails(c, person):
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(A, "settings", always_fails)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, "error")
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert not calls(api) and not model.calls


# ---- what the model reads for a structured question (Dev 724) --------------------------------------------------

DATES = [
    {
        "id": "date",
        "header": "Date",
        "question": "Which launch date do you want?",
        "why": "It sets the venue deposit.",
        "options": [
            {"label": "Nov 3", "then": "Book the venue for Nov 3", "description": "The earlier date"},
            {"label": "Nov 10", "then": "Book the venue for Nov 10", "recommended": True},
        ],
    }
]


def model_text(model):
    return model.calls[-1]["state"]["item"]["text"]


def test_a_long_task_body_is_cut_and_the_lines_and_options_survive():
    """The lines of a structured question come first; the body fills the rest."""
    questions = [
        {
            "question": "Which launch date do you want?",
            "why": "It sets the venue deposit.",
            "options": [
                {"label": "Nov 3", "then": "Book Nov 3"},
                {"label": "Nov 10", "then": "Book Nov 10", "recommended": True},
            ],
        }
    ]
    text = A.structured_text(questions, "Long context. " * 300)
    assert len(text) == A.MAX_TEXT
    assert text.startswith(
        "Question: Which launch date do you want?\nWhy: It sets the venue deposit.\n"
        "Option: Nov 3 (then: Book Nov 3)\nOption: Nov 10 (then: Book Nov 10)\n"
        "Recommended: Nov 10\n\nLong context."
    )


def test_the_largest_question_set_keeps_every_question_option_and_recommendation(gate):
    """The schema's largest set: four questions of 300 characters, six options of 60 each, each with a why and a then.
    The lines are never cut, so every question, option and recommended option reads, in order, with no body."""
    qs = []
    for q in range(1, 5):
        options = [
            {"label": f"Q{q} option {o} " + "L" * 45, "then": f"Q{q} then {o} " + "T" * 100, "recommended": o == 6}
            for o in range(1, 7)
        ]
        qs.append({"question": f"Q{q} " + "Q" * 296, "why": f"Q{q} why " + "W" * 190, "options": options})
    text = A.structured_text(qs, "Body")
    cursor = 0
    for q in qs:
        for needle in [q["question"][:300], "Why: " + q["why"][:200]]:
            cursor = text.index(needle, cursor) + len(needle)
        for option in q["options"]:
            cursor = text.index(f"Option: {option['label'][:60]}", cursor) + 1
        cursor = text.index(f"Recommended: {q['options'][5]['label'][:60]}", cursor) + 1
    assert len(text) > A.MAX_TEXT and text.count("Recommended:") == 4
    assert "Body" not in text, "the lines are never cut to make room for a body"


def ana_gate(api):
    """Ana, asked by a bot's file review, with the filter On and a fake model."""
    model = FakeModel()
    api.app.state.judge = model
    with api.app.state.store.transaction() as c:
        A.save(
            c,
            "human:ana",
            "human:ana",
            {"mode": "on", "budget": 10, "chores_budget": 5, "criteria": A.DEFAULTS["criteria"]},
        )
    return model


@pytest.mark.parametrize("kind,path", [("own_work", path) for path in ("create", "handoff")] + [("status", "create")])
def test_refusals_preserve_all_domain_rows_across_write_paths(gate, kind, path):
    api, model, token = gate
    model.kind = kind
    if path == "handoff":
        task = own_task(api, token)
    if path == "create" and kind == "status":
        endpoint, body = (
            "tasks",
            {"owner": "ben", "title": "Merged; release at 17:00Z", "body": "Merged; release at 17:00Z."},
        )
    elif path == "create":
        endpoint, body = "tasks", {"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"}
    else:
        endpoint, body = f"tasks/{task['id']}/owner", {"version": task["version"], "owner": "ben", "why": "Ben decides"}
    before = write_state(api)
    r = api.post("/api/v2/" + endpoint, json=body, headers=headers(token))
    detail = assert_refused(api, r, before)
    if kind == "status":
        assert "Post it as an update instead." in detail
    [item], [call] = items(api), calls(api)
    assert item["call_id"] == call["id"]
    assert item["outcome"] == (A.FYI if kind == "status" else A.SENT_BACK)


def test_a_refusal_can_be_fixed_and_retried_with_the_same_key(gate):
    api, model, token = gate
    plate(api, 15)
    body = {"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"}
    keyed = {**headers(token), "Idempotency-Key": "fix-a-refused-ask"}
    before = write_state(api)
    assert_refused(api, api.post("/api/v2/tasks", json=body, headers=keyed), before)
    model.meets = "Strategy"
    body["body"] = "Strategy and positioning: Nov 3 or Nov 10? I recommend Nov 10."
    fixed = api.post("/api/v2/tasks", json=body, headers=keyed)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["task"]["owner_id"] == "human:ben"
    assert len(items(api)) == 2 and len(calls(api)) == 2


def test_repeated_ask_refusals_never_quarantine_or_hold_the_bot(gate):
    api, model, token = gate
    model.kind = "own_work"
    before = write_state(api)
    for _ in range(6):
        assert_refused(
            api,
            api.post(
                "/api/v2/tasks",
                json={"owner": "ben", "title": "Verify the deployment", "body": "Nov 3 or Nov 10?"},
                headers=headers(token),
            ),
            before,
        )
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
    assert len(items(api)) == 6


def test_off_at_write_time_does_not_refuse_an_ask_already_judged(gate):
    api, model, token = gate
    model.kind = "own_work"

    def turn_off():
        with api.app.state.store.transaction() as c:
            A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": "off"})

    model.before = turn_off
    assert file(api, token)["task"]["owner_id"] == "human:ben"
    assert items(api)[0]["outcome"] == A.THROUGH


def test_the_plate_is_the_persons_needs_you_as_the_engine_reads_it(company):
    """load, on_plate and open_items read waiting_on.needs: a task the person owns and an ask that names them each
    count, a snoozed task does not, and a bot is offered only what it may read."""
    with company.transaction() as c:
        mine = H.task_create(c, "bot:ops", "Pick the launch venue", "Two options.", "human:ben", lint=False)["id"]
        asked = H.task_create(c, "human:ana", "Draft the launch note", "For Monday.", "bot:ops", lint=False)["id"]
        H.task_ask(c, "bot:ops", asked, "human:ben", "Monday or Friday?", seen_version=H.task(c, asked)["version"])
        later = H.task_create(c, "bot:ops", "Plan the offsite", "Next quarter.", "human:ben", lint=False)["id"]
        H.task_snooze(c, "human:ben", later, "Next quarter", None)
        assert A.load(c, "human:ben") == 2
        assert A.on_plate(c, "human:ben", mine) and A.on_plate(c, "human:ben", asked)
        assert not A.on_plate(c, "human:ben", later)
        offered = {task_id for task_id, _title in A.open_items(c, "human:ben", "bot:ops").values()}
        assert offered == {mine, asked}
