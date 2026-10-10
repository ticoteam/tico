"""Updates: every bot reports in once a day, one bot asked at a time; Friday is
the week in review; read state is per person; a reply is both a comment on the update and a
message in the bot's chat."""

from datetime import datetime, timezone

from backend import updates
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

MORNING = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)      # Tuesday 06:00 Pacific
FRIDAY = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)        # 03:00 Pacific, before the queue
BEFORE_FIVE = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # Monday 03:00 Pacific: no queue of its own yet


def out(c):
    return [dict(r) for r in c.execute("SELECT bot, state, kind FROM update_queue WHERE state='sent'")]


def test_the_queue_asks_one_bot_at_a_time_and_moves_on_when_it_posts(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert updates.dispatch(c, NIGHT) is None and not c.execute("SELECT 1 FROM update_queue").fetchone()
        first = updates.dispatch(c, MORNING)
        queued = c.execute("SELECT count(*) FROM update_queue WHERE day='2026-09-29'").fetchone()[0]
        active = c.execute("SELECT count(*) FROM bots WHERE state='active'").fetchone()[0]
    assert first and queued == active, "every active bot is queued, and the first one asked"
    with store.transaction() as c:
        assert updates.dispatch(c, MORNING) is None, "one at a time: nothing more while it is out"
        request = c.execute("SELECT m.body, m.refs_json FROM update_queue q JOIN messages m ON m.id=q.message_id "
                            "WHERE q.state='sent'").fetchone()
        assert "hub_update_create" in request["body"] and "Finished since your last update" in request["body"]
        assert c.execute("SELECT 1 FROM jobs WHERE bot=?", (first,)).fetchone(), "the request wakes the bot"
        updates.post(c, first, "- Shipped the pricing page", day="2026-09-29")
        second = updates.dispatch(c, MORNING)
        assert second and second != first
        assert c.execute("SELECT state FROM update_queue WHERE bot=? AND day='2026-09-29'", (first,)).fetchone()[0] == "posted"


def test_a_bot_posts_once_a_day_and_each_person_has_their_own_read_state(api, monkeypatch):
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")   # a Tuesday: a daily, whatever today is
    r, msg, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    first = api.post("/api/v2/updates", json={"body": "- Drafted the checklist"},
                     headers={**bot, "Idempotency-Key": "u1"})
    assert first.status_code == 200, first.text
    again = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist"},
                     headers={**bot, "Idempotency-Key": "u2"}).json()["update"]
    assert again["id"] == first.json()["update"]["id"], "posting again replaces the day's update"
    labelled = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist\n- Next: send it"},
                        headers={**bot, "Idempotency-Key": "u3"})
    assert labelled.status_code == 200 and "section label" in labelled.json()["update"]["warning"], "a writing slip is a warning"
    empty = api.post("/api/v2/updates", json={"body": ""}, headers={**bot, "Idempotency-Key": "u4"})
    assert empty.status_code == 422, "an empty update is still refused"
    again = api.post("/api/v2/updates", json={"body": "- Drafted and sent the checklist"},
                     headers={**bot, "Idempotency-Key": "u5"}).json()["update"]
    post(api, "updates", {"body": "- Me too"}, expected=403)           # people read, bots post
    feed = get(api, "updates")
    assert [u["headline"] for u in feed["updates"]] == ["Drafted and sent the checklist"]
    assert feed["updates"][0]["read"] is False and feed["unread"] == 1
    post(api, "updates/read", {"ids": [again["id"]]})
    assert get(api, "updates")["unread"] == 0
    assert get(api, "updates/unread") == {"unread": 0, "meetings_pending": 0}
    assert get(api, "updates", token="ben-test")["unread"] == 1, "Ana reading it is not Ben reading it"
    post(api, "updates/read", {"ids": [again["id"]], "read": False})
    assert get(api, "updates?unread=true")["updates"][0]["id"] == again["id"]
    post(api, "updates/read", {"all": True})
    assert get(api, "updates")["unread"] == 0


SLIDES = {"goal": "Grow organic signups to 400 a month; at 310 and on pace.",
          "kpis": [{"name": "Signups", "value": "310", "series": [240, 262, 281, 310], "note": "Two new pages ranked."}],
          "done": ["Shipped four landing pages", "- Linked them from six older posts"],
          "focus": ["Pitch the checklist to three newsletters"], "blockers": []}


def test_a_week_in_review_is_five_slides_and_an_owner_can_have_last_week_redone(api):
    """Owner, 2026-10-05: the weekly is a few swipeable slides: Goal, KPIs, done last week, focus next
    week, biggest blockers; and last week's can be run again in the new shape."""
    r, msg, attempt = setup_attempt(api, "ops")
    bot = {"Authorization": "Bearer " + attempt["token"]}
    send = lambda body, n: api.post("/api/v2/updates", json=body, headers={**bot, "Idempotency-Key": f"w-{n}"})
    ok = send({"kind": "weekly", "slides": SLIDES}, 3)
    assert ok.status_code == 200, ok.text
    week = ok.json()["update"]
    assert week["slides"]["done"] == ["Shipped four landing pages", "Linked them from six older posts"]

    friday = "2026-10-02"
    back = send({"kind": "weekly", "slides": SLIDES, "day": friday}, 4)
    assert back.status_code == 422, "a bot never back-dates a post on its own"
    post(api, "updates/redo", {"day": friday}, token="ben-test", expected=403)
    asked = post(api, "updates/redo", {"day": friday, "bots": ["ops"]})
    assert asked["bots"] == ["ops"]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot='ops'")      # its run is over, so it is free
        assert updates.dispatch(c, BEFORE_FIVE) == "ops", "a redo of a past day is sent like today's requests"
        request = c.execute("SELECT m.body FROM update_queue q JOIN messages m ON m.id=q.message_id "
                            "WHERE q.state='sent' AND q.redo=1").fetchone()["body"]
    assert "with day 2026-10-02" in request
    redone = send({"kind": "weekly", "slides": SLIDES, "day": friday}, 5)
    assert redone.status_code == 200 and redone.json()["update"]["day"] == friday, redone.text
    with store.transaction() as c:
        updates.dispatch(c, BEFORE_FIVE)
        assert c.execute("SELECT state FROM update_queue WHERE bot='ops' AND day=?", (friday,)).fetchone()[0] == "posted"
