"""Per-person preferences (POST/GET /api/v2/preferences/{key}) whose values the server checks."""

from backend.tests.helpers.api import get, headers, post  # noqa: F401


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


def test_assistant_look_keeps_a_name_one_of_eight_colors_and_the_sprite(api):
    # Each person names their own assistant and picks its Dot's look (web/src/tico/look.ts); only theirs changes.
    look = {"name": "Penny", "color": "violet", "sprite": "pixel"}
    assert post(api, "preferences/ui.assistant", {"value": look})["value"] == look
    assert get(api, "preferences/ui.assistant")["value"] == look
    assert get(api, "preferences/ui.assistant", token="ben-test")["value"] is None
    assert (
        post(api, "preferences/ui.assistant", {"value": {"color": "jade", "sprite": None}})["value"]["color"] == "jade"
    )
    for wrong in (
        "Penny",
        {"name": ""},
        {"name": " Penny"},
        {"name": "x" * 41},
        {"color": "red"},
        {"sprite": "cat"},
        {"face": "pixel"},
    ):
        r = api.post("/api/v2/preferences/ui.assistant", json={"value": wrong}, headers=headers())
        assert r.status_code == 422, (wrong, r.text)
        assert "ui.assistant" in r.json()["error"]["detail"], r.text
    assert get(api, "preferences/ui.assistant")["value"] == {"color": "jade", "sprite": None}
