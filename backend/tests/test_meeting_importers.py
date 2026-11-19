"""Retired importers disappear from setup while old data and authorized clients remain compatible."""

import json

import pytest

from backend.store import H
from backend.tests.helpers.api import get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.helpers.openapi_v2 import conforms


def historical_meeting(api, machine):
    return post(
        api,
        "meetings/import",
        {
            "source": "fireflies",
            "external_id": "historical-1",
            "title": "Historical meeting",
            "transcript": "Sam: Preserve the recording.",
            "notes": "Original shared notes",
            "owner_email": "ben@acme.example",
            "media_url": "https://example.com/recording/historical-1",
        },
        machine["token"],
    )


def persisted_setting(api, machine):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO meeting_importers VALUES(?,?,?,?,?)",
            ("fireflies", 1, machine["runner_id"], "human:ana", H.now()),
        )
        c.execute(
            "INSERT INTO service_health(service,last_succeeded_at,last_error,detail) VALUES(?,?,NULL,?)",
            ("recording:fireflies", H.now(), json.dumps({"imported_total": 7})),
        )


def test_historical_source_still_deduplicates_and_preserves_versions_and_files(api):
    machine = runner(api)
    first = historical_meeting(api, machine)
    again = historical_meeting(api, machine)
    assert first["id"] == again["id"] and not again["changed"]
    rid = first["id"]
    attached = api.post(
        "/api/meetings/" + rid + "/attachments",
        headers=headers("ben-test"),
        files={"files": ("history.txt", b"Historical attachment", "text/plain")},
    )
    assert attached.status_code == 200, attached.text
    before = api.get("/api/meetings/" + rid, headers=headers("ben-test")).json()
    persisted_setting(api, machine)
    assert post(api, "meeting-importers/fireflies", {"enabled": False, "runner_id": ""}) == {"ok": True}
    after = api.get("/api/meetings/" + rid, headers=headers("ben-test")).json()
    for field in ("notes", "turns", "media_url", "source", "attachments", "review_state"):
        assert after[field] == before[field]
    blob = after["attachments"][0]["id"]
    downloaded = api.get("/api/v2/files/" + blob, headers=headers("ben-test"))
    assert downloaded.status_code == 200 and downloaded.content == b"Historical attachment"
    versions = api.get("/api/meetings/" + rid + "/versions", headers=headers("ben-test")).json()["versions"]
    assert any(v["notes"] == "Original shared notes" for v in versions)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1


@pytest.mark.parametrize("actor", ["member", "revoked_runner"])
def test_retired_heartbeat_still_requires_authorized_importer(api, actor):
    machine = runner(api)
    token, expected = "ben-test", 403
    if actor == "revoked_runner":
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), machine["runner_id"]))
        token, expected = machine["token"], 401
    assert post(api, "imports/sources/fireflies/status", {"state": "ok"}, token, expected=expected)


def test_sources_say_why_an_importer_fails_and_removing_it_drops_it(api):
    machine = runner(api)
    assert post(api, "meeting-importers/zoom", {"enabled": True, "runner_id": machine["runner_id"]}) == {"ok": True}
    post(api, "imports/sources/zoom/status", {"state": "error", "error_code": "auth_failed"}, machine["token"])
    rows = {r["id"]: r for r in api.get("/api/meetings/sources", headers=headers("ben-test")).json()["sources"]}
    assert rows["zoom"]["status"] == "error" and rows["zoom"]["error"] == "The tool refused the credential."
    assert post(api, "meeting-importers/zoom", {"enabled": False, "runner_id": ""}) == {"ok": True}
    rows = {r["id"] for r in api.get("/api/meetings/sources", headers=headers("ben-test")).json()["sources"]}
    assert "zoom" not in rows


def declared(api, operation):
    document = get(api, "openapi.json")
    op = next(o for ops in document["paths"].values() for o in ops.values() if o["operationId"] == operation)
    return op["responses"]["200"]["content"]["application/json"]["schema"], document


def failing_zoom(api):
    machine = runner(api)
    assert post(api, "meeting-importers/zoom", {"enabled": True, "runner_id": machine["runner_id"]}) == {"ok": True}
    post(api, "imports/sources/zoom/status", {"state": "error", "error_code": "auth_failed"}, machine["token"])
    return machine


def test_v2_sources_answer_as_the_old_address_with_their_declared_shape_and_refuse_a_bot(api):
    failing_zoom(api)
    answer = get(api, "meetings/sources", "ben-test")
    old = api.get("/api/meetings/sources", headers=headers("ben-test")).json()
    assert answer["sources"] == old["sources"]
    rows = {r["id"]: r for r in answer["sources"]}
    assert list(rows) == ["import", "close", "zoom"]
    # Each row keeps its own health: Zoom's failure is not Close's.
    assert rows["zoom"]["error_code"] == "auth_failed" and rows["close"]["error_code"] == ""
    assert conforms(answer, *declared(api, "listMeetingSources")) is None
    _, _, attempt = setup_attempt(api)
    assert get(api, "meetings/sources", attempt["token"], expected=403)["error"]["code"] == "identity"


def test_importers_are_the_owners_alone_and_answer_their_declared_shape(api):
    machine = failing_zoom(api)
    for token in ("ben-test", setup_attempt(api)[2]["token"]):
        assert get(api, "meeting-importers", token, expected=403)
        assert post(api, "meeting-importers/zoom", {"enabled": False}, token, expected=403)
    answer = get(api, "meeting-importers")
    assert conforms(answer, *declared(api, "listMeetingImporters")) is None
    zoom = next(r for r in answer["importers"] if r["source"] == "zoom")
    assert (zoom["status"], zoom["runner_id"], zoom["error"]) == (
        "error",
        machine["runner_id"],
        "The tool refused the credential.",
    )
    assert machine["runner_id"] in {m["id"] for m in answer["computers"]}
