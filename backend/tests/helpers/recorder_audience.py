"""Helpers that other tests share, moved out of test_recorder_audience.py so they import no test file."""

import json
from backend.tests.helpers.api import headers, post


def readable(api, rid, token):
    return api.get("/api/meetings/" + rid, headers=headers(token)).status_code == 200


def on_roster(api, *names):
    """eve and fay sign in like the others; here they are on the roster too."""
    with api.app.state.store.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for name in names:
            c.execute(
                "INSERT OR IGNORE INTO humans(id, name, email) VALUES(?,?,?)",
                (name, name.title(), name + "@acme.example"),
            )
            raw["people"].append({"id": name, "email": name + "@acme.example", "name": name.title()})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))


def live_meeting(api, *, visibility="private", joiner=None, added=None):
    on_roster(api, "eve", "fay")
    mid = post(api, "live-meetings", {"title": "Sync", "visibility": visibility}, token="ben-test")["id"]
    api.post(
        f"/api/v2/live-meetings/{mid}/chunks",
        headers=headers("ben-test"),
        json={"chunks": [{"seq": 1, "speaker": "Ben", "start_ms": 0, "end_ms": 900, "text": "Synthetic words"}]},
    )
    post(api, f"live-meetings/{mid}/chat", {"text": "Synthetic chat"}, token="ben-test")
    if added:
        post(api, f"live-meetings/{mid}/access", {"add": [added]}, token="ben-test")
    if joiner:
        post(api, f"live-meetings/{mid}/join", {}, token=f"{joiner}-test")
    return mid
