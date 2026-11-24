"""Per-person preferences (POST/GET /api/v2/preferences/{key}) whose values the server checks."""

from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.tasks_board import bot_token


def test_send_way_keeps_only_a_way_the_web_menu_offers(api):
    # The web's Send reads this way on load and sends it with every comment, so a value no menu offers is refused
    # before it is stored, and the way kept before it stays.
    for way in ("steer", "queue", "defer:4h", "defer:never"):
        assert post(api, "preferences/compose.send_way", {"value": way})["value"] == way
        assert get(api, "preferences/compose.send_way")["value"] == way
    for wrong in ("goal", "defer", "defer:5h", "defer:4h:x", "Steer", True, {"way": "steer"}, None):
        r = api.post("/api/v2/preferences/compose.send_way", json={"value": wrong}, headers=headers())
        assert r.status_code == 422, (wrong, r.text)
        assert "compose.send_way" in r.json()["error"]["detail"], r.text
    assert get(api, "preferences/compose.send_way")["value"] == "defer:never"
    # Each person keeps their own.
    assert get(api, "preferences/compose.send_way", token="ben-test")["value"] is None


def test_tracker_seen_at_keeps_only_a_time_with_a_timezone(api):
    # Tico's tracker counts what came back since this time, so a value it cannot compare is refused.
    at = "2026-11-20T10:00:00+00:00"
    assert post(api, "preferences/ui.tracker_seen_at", {"value": at})["value"] == at
    for wrong in ("yesterday", "2026-11-20T10:00:00", 1763632800, None):
        r = api.post("/api/v2/preferences/ui.tracker_seen_at", json={"value": wrong}, headers=headers())
        assert r.status_code == 422 and "ui.tracker_seen_at" in r.json()["error"]["detail"], (wrong, r.text)
    assert get(api, "preferences/ui.tracker_seen_at")["value"] == at


def test_intros_seen_keeps_pages_with_times_for_people_only(api):
    # The web plays a page's first-visit intro once per person: what it keeps must read back as pages and times, and
    # an agent has no intros to keep.
    seen = {"tasks": "2026-11-23T10:00:00.000Z", "growth": "2026-11-23T10:05:00+00:00"}
    assert post(api, "preferences/intros.seen", {"value": seen})["value"] == seen
    for wrong in (
        ["tasks"],
        "tasks",
        {"tasks": "yesterday"},
        {"tasks": "2026-11-23T10:00:00"},
        {"Tasks!": "2026-11-23T10:00:00Z"},
        {f"p{n}": "2026-11-23T10:00:00Z" for n in range(65)},
    ):
        r = api.post("/api/v2/preferences/intros.seen", json={"value": wrong}, headers=headers())
        assert r.status_code == 422 and "intros.seen" in r.json()["error"]["detail"], (wrong, r.text)
    assert get(api, "preferences/intros.seen")["value"] == seen
    assert get(api, "preferences/intros.seen", token="ben-test")["value"] is None
    r = api.post("/api/v2/preferences/intros.seen", json={"value": {}}, headers=headers(bot_token(api)))
    assert r.status_code == 403, r.text
