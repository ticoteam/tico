"""Per-person preferences (POST/GET /api/v2/preferences/{key}) whose values the server checks."""

from backend.tests.test_api import api, get, headers, post  # noqa: F401


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
