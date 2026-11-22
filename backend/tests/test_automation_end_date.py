"""An automation's end date (`ends_at`, backend/automations.py): once it passes nothing new starts, by any trigger or
listening, while Run now still works; turning it back on needs a later end or none, and then its timing starts from
now, so the times it missed are skipped."""

import json
from datetime import datetime, timedelta, timezone

import time_machine

from backend import automations
from backend import listening as L
from backend.tests.helpers.api import get, post
from backend.tests.helpers.automation_api import NOTE_STEP, as_bot, make, patch
from backend.tests.helpers.automation_runs import sql
from backend.tests.helpers.listening import item, listening, save, source_id

T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)  # a Monday
END = "2026-11-03T00:00:00-08:00"  # Tuesday 08:00 UTC: Monday's and Tuesday's 09:00 runs fall either side of it


def at(hours=0.0):
    return T0 + timedelta(hours=hours)


def runs(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"]


def test_an_end_date_is_a_future_time_with_an_offset_on_a_routine_that_is_not_built_in(api):
    with time_machine.travel(T0, tick=False):
        assert "Z or an offset" in json.dumps(make(api, expected=422, ends_at="2026-11-10"))
        assert "in the future" in json.dumps(make(api, expected=422, ends_at="2026-11-02T07:59:00Z"))
        assert "Z or an offset" in json.dumps(make(api, expected=422, ends_at="9999-12-31T23:59:59-01:00"))
        made = make(api, ends_at=END)
        assert made["ends_at"] == "2026-11-03T08:00:00.000000Z" and made["version"] == 1 and "ended" not in made
        with api.app.state.store.transaction() as c:
            automations.ensure_builtins(
                c,
                [
                    {
                        "key": "triage",
                        "name": "Triage",
                        "owner": "bot:ops",
                        "trigger": {"type": "event", "name": "task.created"},
                        "steps": [NOTE_STEP],
                    }
                ],
            )
        refused = patch(api, "auto_builtin_triage", {"ends_at": "2026-12-01T00:00:00Z"}, expected=422)
        assert "no end date" in json.dumps(refused)


def test_once_ended_it_turns_on_only_with_a_later_end_and_skips_what_it_missed(api):
    with time_machine.travel(T0, tick=False):
        aid = make(api, ends_at=END)["id"]  # every day at 09:00 UTC
    store = api.app.state.store
    with time_machine.travel(at(1.5), tick=False):
        automations.tick(store, at(1.5))  # Monday 09:00 runs: before the end
    assert len(runs(api, aid)) == 1
    with time_machine.travel(at(48), tick=False):  # Wednesday 08:00
        automations.tick(store, at(48))  # Tuesday's 09:00 came after the end: passed over, its time kept
        assert len(runs(api, aid)) == 1
        assert sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,))[0]["next_due_at"] == (
            "2026-11-03T09:00:00.000000Z"
        )
        shown = get(api, f"automations/{aid}")
        assert shown["ended"] is True and shown["next_run"] is None and shown["enabled"] is True
        assert "set a later one or clear it" in json.dumps(patch(api, aid, {"enabled": True}, expected=422))
        patch(api, aid, {"enabled": True}, as_bot(api, "finance"), expected=403)  # no rights: not told it ended
        assert "set a later one" in json.dumps(post(api, f"automations/{aid}/versions/1/restore", {}, expected=422))
        assert patch(api, aid, {"name": "Renamed"})["ended"] is True  # the past end stays through other edits
        assert post(api, f"automations/{aid}/run", {})["run"]  # Run now is not admission
        assert len(runs(api, aid)) == 2
        extended = patch(api, aid, {"ends_at": "2026-11-20T00:00:00Z"})
        # Tuesday's missed 09:00 is skipped: it starts from the next time after now.
        assert "ended" not in extended and extended["next_run"] == "2026-11-04T09:00:00.000000Z"
        patch(api, aid, {"ends_at": None})
        assert "ends_at" not in get(api, f"automations/{aid}")


def test_an_ended_automation_hears_no_trigger(api):
    trigger = {"type": "task_received", "to": "bot:ops"}
    with time_machine.travel(T0, tick=False):
        aid = make(api, trigger=trigger, ends_at=END)["id"]
        post(api, "tasks", {"owner": "ops", "title": "Before the end", "body": "Please."})
    assert len(runs(api, aid)) == 1
    with time_machine.travel(at(25), tick=False):
        post(api, "tasks", {"owner": "ops", "title": "After the end", "body": "Please."})
        assert len(runs(api, aid)) == 1
        patch(api, aid, {"enabled": False})  # off with a passed date is just off, and may be turned off
        assert "ended" not in get(api, f"automations/{aid}")


def test_an_ended_listening_takes_scores_and_sends_nothing_and_shows_no_next_send(api):
    with time_machine.travel(T0 - timedelta(minutes=30), tick=False):
        aid = listening(api, ends_at=END)["id"]  # sends at 08:00 UTC
        source = source_id(api, aid)
        save(api, source, [item(1, "A post about standups")])
    later = at(25)  # Tuesday 09:00: past the end and Tuesday's send time
    store = api.app.state.store
    with time_machine.travel(later, tick=False):
        due = sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,))[0]["next_due_at"]
        save(api, source, [item(2, "Another post")], expected=409)
        assert get(api, f"listening/sources?due=1&automation={aid}")["sources"] == []
        L.run_pass(store, later)
        store.automations.drain()
        assert runs(api, aid) == []
        assert sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,))[0]["next_due_at"] == due
        shown = get(api, f"automations/{aid}")
        assert shown["listening"]["next_send_at"] is None and shown["next_run"] is None
        with store.read() as c:
            assert L.worst(c) is None and L.trouble(c) == [] and L.stalled(c) == []


def test_a_due_routine_back_from_its_end_skips_the_run_that_opened_while_it_was_ended(api):
    trigger = {"type": "due", "first_due": "2026-11-06", "repeats": "weekly", "open_days": 3, "tz": "UTC"}
    with time_machine.travel(T0, tick=False):
        aid = make(api, trigger=trigger, ends_at="2026-11-09T00:00:00Z")["id"]
    nov12 = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
    with time_machine.travel(nov12, tick=False):
        # Nov 13's run opened Nov 10, while it was ended: it starts again from Nov 20's, which opens Nov 17.
        assert patch(api, aid, {"ends_at": "2026-12-31T00:00:00Z"})["next_due_at"] == "2026-11-20"
        automations.tick(api.app.state.store, nov12)
    assert runs(api, aid) == []


def test_a_listening_pass_that_started_before_the_end_admits_nothing_after_it(api):
    with time_machine.travel(T0 - timedelta(minutes=30), tick=False):
        aid = listening(api, ends_at="2026-11-02T08:00:00Z")["id"]  # its first send is at 08:00 UTC too
        save(api, source_id(api, aid), [item(1, "A post about standups")])
    store = api.app.state.store
    with time_machine.travel(at(0.5), tick=False):  # the clock is past the end; the pass's own time is not
        L.run_pass(store, at(-0.01))
        L.run_pass(store, T0)
    assert sql(api, "SELECT status FROM listening_items WHERE automation_id=?", (aid,))[0]["status"] == "new"
    assert sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,))[0]["next_due_at"] == (
        "2026-11-02T08:00:00.000000Z"
    )
