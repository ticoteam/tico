"""The skills helper's devices: the device code flow, the token's reach, and the receipt and usage it reports."""

import json

from backend.store import H, digest
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.helpers.skill_devices import add_skill, ask, bearer, connect, install_for, poll, rows


def test_device_code_flow_pending_approve_once_expired_denied(api):
    code = ask(api)
    assert code["expires_in"] == 600 and code["interval"] == 5
    assert len(code["user_code"]) == 9 and code["user_code"][4] == "-"
    assert code["verification_url"].endswith("/#/skills/connect?code=" + code["user_code"])
    assert poll(api, code).status_code == 428
    shown = get(api, "skills/devices/codes/" + code["user_code"].lower(), token="cara-test")
    assert shown["label"] == "Cara's laptop"
    # A personal API token cannot connect a device; the browser can.
    token = post(api, "me/tokens", {"label": "script"})["token"]
    r = api.post(
        "/api/v2/skills/devices/codes/" + code["user_code"] + "/approve",
        json={"reporting_enabled": True},
        headers=headers(token),
    )
    assert r.status_code == 403, r.text
    post(api, "skills/devices/codes/" + code["user_code"] + "/approve", {"reporting_enabled": True}, token="cara-test")
    got = poll(api, code)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["token"].startswith("tico_sd_") and body["device_id"].startswith("skd_") and body["reporting_enabled"]
    # Exactly once: the next poll is gone, and only the hash is kept.
    again = poll(api, code)
    assert again.status_code == 410 and again.json()["error"]["code"] == "used"
    stored = rows(api, "SELECT person_id,token_hash,reporting_enabled,label FROM skill_devices")
    assert stored == [
        {"person_id": "cara", "token_hash": digest(body["token"]), "reporting_enabled": 1, "label": "Cara's laptop"}
    ]
    assert body["token"] not in json.dumps(rows(api, "SELECT * FROM events"))
    # Expired.
    late = ask(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE skill_device_codes SET expires_at=? WHERE user_code=?",
            (H.shift(H.now(), seconds=-1), late["user_code"].replace("-", "")),
        )
    gone = poll(api, late)
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "expired"
    post(api, "skills/devices/codes/" + late["user_code"] + "/approve", {}, token="cara-test", expected=404)
    # Denied.
    no = ask(api)
    post(api, "skills/devices/codes/" + no["user_code"] + "/deny", {}, token="cara-test")
    denied = poll(api, no)
    assert denied.status_code == 410 and denied.json()["error"]["code"] == "denied"
    assert api.post("/api/v2/skills/devices/token", json={"device_code": "nope"}).status_code == 404


def test_codes_are_rate_limited_per_address(api):
    for _ in range(10):
        ask(api)
    r = api.post("/api/v2/skills/devices/codes", json={"label": "x"})
    assert r.status_code == 429 and r.json()["error"]["retryable"]


def test_the_token_reaches_only_its_routes_and_a_revoked_one_nothing(api):
    mine, other = connect(api), connect(api)
    device = bearer(mine["token"])
    assert api.get("/api/v2/skills/marketplace.json", headers=device).status_code == 200
    for method, path in (
        ("GET", "/api/v2/tasks"),
        ("POST", "/api/v2/sql"),
        ("GET", "/api/v2/skills/devices"),
        ("POST", "/api/v2/me/tokens"),
        ("PATCH", "/api/v2/skills/devices/" + mine["device_id"]),
    ):
        r = api.request(method, path, headers=device, json={} if method != "GET" else None)
        assert r.status_code == 403, (method, path, r.text)
    # A device revokes itself, never another.
    assert api.delete("/api/v2/skills/devices/" + other["device_id"], headers=device).status_code == 403
    listed = get(api, "skills/devices", token="cara-test")["devices"]
    assert {d["id"] for d in listed} == {mine["device_id"], other["device_id"]}
    assert "token_hash" not in json.dumps(listed)
    assert get(api, "skills/devices", token="ben-test")["devices"] == []
    # An admin may revoke anyone's device.
    assert api.delete("/api/v2/skills/devices/" + other["device_id"], headers=headers("ben-test")).status_code == 200
    assert api.get("/api/v2/skills/marketplace.json", headers=bearer(other["token"])).status_code == 401
    assert api.delete("/api/v2/skills/devices/" + mine["device_id"], headers=device).status_code == 200
    assert api.get("/api/v2/skills/marketplace.json", headers=device).status_code == 401
    assert (
        api.post("/api/v2/sql", json={"sql": "SELECT token_hash FROM skill_devices"}, headers=headers()).status_code
        == 422
    )


def test_a_member_cannot_revoke_someone_elses_device(api):
    from backend.tests.helpers.api import as_member

    as_member(api, "ben@acme.example")
    mine = connect(api)
    assert api.delete("/api/v2/skills/devices/" + mine["device_id"], headers=headers("ben-test")).status_code == 404
    assert api.get("/api/v2/skills/marketplace.json", headers=bearer(mine["token"])).status_code == 200


def test_receipt_and_usage_need_the_device_and_reporting(api):
    alpha, _ = add_skill(api, "alpha", "# Alpha v1")
    install_for(api, alpha, "cara")
    sha = rows(api, "SELECT sha256 FROM skill_versions WHERE skill_id=?", alpha)[0]["sha256"]
    mine = connect(api, reporting=False)
    device = bearer(mine["token"])
    receipt = api.post(
        "/api/v2/skills/devices/receipt",
        headers=device,
        json={
            "staged": [
                {"name": "alpha", "sha256": sha},
                {"name": "ghost", "sha256": sha},
                {"name": "alpha2", "sha256": "x"},
            ],
            "claude_version": "2.1.240",
        },
    )
    assert receipt.status_code == 200 and receipt.json() == {"staged": 1, "dropped": 2}
    assert rows(api, "SELECT target_type,target_id,skill_id,sha256,source FROM skill_receipts") == [
        {"target_type": "Human", "target_id": "cara", "skill_id": alpha, "sha256": sha, "source": "helper"}
    ]
    assert rows(api, "SELECT claude_version FROM skill_devices")[0]["claude_version"] == "2.1.240"
    event = {
        "name": "alpha",
        "trigger": "model",
        "session_key": "a" * 64,
        "tool_use_id": "toolu_1",
        "used_at": H.shift(H.now(), hours=-1),
    }
    off = api.post("/api/v2/skills/usage", headers=device, json={"events": [event]})
    assert off.status_code == 403 and off.json()["error"]["code"] == "reporting_off"
    r = api.patch(
        "/api/v2/skills/devices/" + mine["device_id"], json={"reporting_enabled": True}, headers=headers("cara-test")
    )
    assert r.status_code == 200, r.text
    assert api.get("/api/v2/skills/marketplace.json", headers=device).headers["x-tico-reporting"] == "on"
    used = api.post(
        "/api/v2/skills/usage",
        headers=device,
        json={
            "events": [
                event,
                dict(event),
                {**event, "name": "ghost", "tool_use_id": "toolu_2"},
                {**event, "trigger": "person", "tool_use_id": "prompt_1", "used_at": "2999-01-01T00:00:00Z"},
            ]
        },
    )
    assert used.status_code == 200 and used.json() == {"recorded": 2, "duplicates": 1, "dropped": 1}
    assert rows(api, "SELECT * FROM unlisted_skill_usage_dailies") == [], (
        "a person's skill outside the directory is never kept"
    )
    stored = rows(
        api,
        "SELECT skill_id,sha256,actor_type,actor_id,trigger,source,session_key,tool_use_id,used_at "
        "FROM skill_usages ORDER BY tool_use_id",
    )
    assert [(u["trigger"], u["tool_use_id"]) for u in stored] == [("person", "prompt_1"), ("model", "toolu_1")]
    assert all(
        (u["skill_id"], u["sha256"], u["actor_type"], u["actor_id"], u["source"])
        == (alpha, sha, "Human", "cara", "helper")
        for u in stored
    )
    assert stored[0]["used_at"] <= H.now()  # a time ahead of the server's is now
    # A person's own session is not the helper.
    assert api.post("/api/v2/skills/usage", headers=headers("cara-test"), json={"events": [event]}).status_code == 403
    # A later receipt replaces the earlier one.
    api.post("/api/v2/skills/devices/receipt", headers=device, json={"staged": []})
    assert rows(api, "SELECT * FROM skill_receipts") == []
