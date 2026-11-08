"""A computer's screen: only its operator or an admin sets who may open it or opens it, only once the computer offers
one; a ticket opens one session once, and the hub pipes the bytes both ways."""

import pytest

from backend.events import runner_read
from backend.tests.test_api import assign, get, headers, post, runner  # noqa: F401  (the api fixture)


pytestmark = pytest.mark.slow


@pytest.fixture
def api(tmp_path):
    from backend.tests.test_screen_relay_contract import running_service
    with running_service(tmp_path) as service:
        yield service.api


def offer(api, r):
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": ["screen_v1"],
         "readiness": {"schema_version": 1, "screen": {"available": True, "width": 1280, "height": 800}}},
         token=r["token"])


def test_only_the_operator_or_an_admin_sets_or_opens_a_screen_and_only_once_the_computer_offers_one(api):
    r = runner(api)
    rid = r["runner_id"]
    assign(api, r, "ops")
    assert post(api, f"computers/{rid}/screen", {"mode": "view"}, expected=409)["error"]["code"] == "screen_unavailable"
    offer(api, r)
    post(api, f"computers/{rid}/screen", {"mode": "view"}, token="cara-test", expected=403)
    assert post(api, f"computers/{rid}/screen", {"mode": "view"}) == {"screen": "view", "screen_available": True}
    row = next(m for m in get(api, "operations")["computers"] if m["id"] == rid)
    assert (row["screen"], row["screen_available"]) == ("view", True) and "capabilities" not in row
    ops = next(e for e in api.get("/api/employees", headers=headers()).json() if e["name"] == "ops")
    assert {"screen": "view", "screen_available": True}.items() <= ops["machine"].items()      # the bot page

    post(api, f"computers/{rid}/screen/sessions", {"control": False}, token="cara-test", expected=403)
    from backend.auth import Identity
    api.app.state.store.settings.test_identities["ana-token"] = Identity("human:ana", "owner", "ana@acme.example",
                                                                         via_token=True)
    post(api, f"computers/{rid}/screen/sessions", {"control": False}, token="ana-token", expected=403)  # in person
    post(api, f"computers/{rid}/screen/sessions", {"control": True}, expected=403)          # view only
    opened = post(api, f"computers/{rid}/screen/sessions", {"control": False}, token="ben-test")   # an admin
    assert (opened["width"], opened["height"], opened["control"]) == (1280, 800, False)
    assert opened["ws_path"] == f"/api/v2/runners/{rid}/screen/ws?ticket=" + opened["ticket"]
    # The computer hears of it on its stream and finds it, with view or control, in its pending list.
    with api.app.state.store.read() as c:
        events, _, _, _ = runner_read(c, rid, "runner:" + rid, 0)
    assert any(e["kind"] == "screen_open" and e["session_id"] == opened["session_id"] for _, e in events)
    assert get(api, "runners/me/screen/sessions", token=r["token"]) == {
        "sessions": [{"session_id": opened["session_id"], "control": False}]}
    get(api, "runners/me/screen/sessions", expected=403)                                    # a person is no computer

    post(api, f"computers/{rid}/screen", {"mode": "off"})
    assert get(api, "runners/me/screen/sessions", token=r["token"]) == {"sessions": []}     # off closes it
    post(api, f"computers/{rid}/screen/sessions", {"control": False}, expected=403)


def test_a_new_computer_starts_at_control_for_its_operator_and_admins_and_no_one_else(api):
    caras, anas = runner(api, operator="cara"), runner(api, operator="ana", label="Ana's Mac")
    for r in (caras, anas):
        offer(api, r)
    computers = {m["id"]: m["screen"] for m in get(api, "operations")["computers"]}
    assert computers[caras["runner_id"]] == computers[anas["runner_id"]] == "control"
    for token in ("cara-test", "ben-test"):              # its operator (a member), then an admin, drive it at once
        assert post(api, f"computers/{caras['runner_id']}/screen/sessions", {"control": True}, token=token)["control"]
    for control in (True, False):                        # a member opens no one else's computer
        post(api, f"computers/{anas['runner_id']}/screen/sessions", {"control": control}, token="cara-test",
             expected=403)
    post(api, f"computers/{anas['runner_id']}/screen", {"mode": "view"}, token="cara-test", expected=403)


def test_turn_on_from_off_at_control_is_the_operators_or_an_admins_and_they_drive_it_at_once(api):
    """The bot page's Turn on (Control): the operator or an admin turns an Off screen on at Control, audited as them, and
    drives it at once; a member who isn't the operator is refused and changes nothing."""
    import json
    caras, anas = runner(api, operator="cara"), runner(api, operator="ana", label="Ana's Mac")
    for r in (caras, anas):
        offer(api, r)
        post(api, f"computers/{r['runner_id']}/screen", {"mode": "off"}, token="ben-test")
    for rid, token, actor in ((caras["runner_id"], "cara-test", "human:cara"), (anas["runner_id"], "ben-test", "human:ben")):
        assert post(api, f"computers/{rid}/screen", {"mode": "control"}, token=token) == {
            "screen": "control", "screen_available": True}
        with api.app.state.store.read() as c:
            event = c.execute("SELECT actor,detail FROM events WHERE action='runner.screen' AND target=? "
                              "ORDER BY rowid DESC LIMIT 1", (rid,)).fetchone()
        assert (event["actor"], json.loads(event["detail"])) == (actor, {"mode": "control"})
        assert post(api, f"computers/{rid}/screen/sessions", {"control": True}, token=token)["control"]
    post(api, f"computers/{anas['runner_id']}/screen", {"mode": "off"}, token="ben-test")
    for mode in ("control", "view"):                 # a member, not Ana's computer's operator
        post(api, f"computers/{anas['runner_id']}/screen", {"mode": mode}, token="cara-test", expected=403)
    assert next(m for m in get(api, "operations")["computers"] if m["id"] == anas["runner_id"])["screen"] == "off"
