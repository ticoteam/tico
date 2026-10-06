"""Retired importers disappear from setup while old data and authorized clients remain compatible."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, headers, post, runner  # noqa: F401


def historical_meeting(api, machine):
    return post(api, "meetings/import", {"source": "fireflies", "external_id": "historical-1",
                "title": "Historical meeting", "transcript": "Sam: Preserve the recording.",
                "notes": "Original shared notes", "owner_email": "ben@acme.example",
                "media_url": "https://example.com/recording/historical-1"}, machine["token"])


def persisted_setting(api, machine):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO meeting_importers VALUES(?,?,?,?,?)",
                  ("fireflies", 1, machine["runner_id"], "human:ana", H.now()))
        c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES(?,?,NULL,?)",
                  ("recording:fireflies", H.now(), json.dumps({"imported_total": 7})))


def test_historical_source_still_deduplicates_and_preserves_versions_and_files(api):
    machine = runner(api)
    first = historical_meeting(api, machine)
    again = historical_meeting(api, machine)
    assert first["id"] == again["id"] and not again["changed"]
    rid = first["id"]
    attached = api.post("/api/meetings/" + rid + "/attachments", headers=headers("ben-test"),
                        files={"files": ("history.txt", b"Historical attachment", "text/plain")})
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
