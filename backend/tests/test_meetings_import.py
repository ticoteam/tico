"""One import API for every source (`POST /api/v2/meetings/import`, docs/meetings.md)."""
from backend.tests.test_api import api, headers, runner, setup_attempt  # noqa: F401
from backend.tests.test_media import import_meeting, media_post

TEXT = "[00:05] Ana: Let's raise the annual plan.\n[01:10] Ben: Ten percent, then."


def meeting(api, rid, token="ana-test"):
    r = api.get("/api/meetings/" + rid, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_the_same_source_and_id_updates_the_meeting_and_never_duplicates_it(api):
    first = import_meeting(api, transcript=TEXT, source="granola", external_id="g-1", title="Pricing")
    again = import_meeting(api, transcript=TEXT, source="granola", external_id="g-1", title="Pricing")
    assert again["id"] == first["id"] and again["existing"] is True and again["changed"] is False
    changed = import_meeting(api, transcript="Ana: Changed our mind.", source="granola", external_id="g-1", title="Pricing", notes="## Decision\nHold.")
    assert changed["id"] == first["id"] and changed["changed"] is True and changed["turns"] == 1
    record = meeting(api, first["id"])
    assert record["title"] == "Pricing" and [t["text"] for t in record["turns"]] == ["Changed our mind."]
    assert record["notes"].startswith("## Decision")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM meeting_versions WHERE meeting_id=?", (first["id"],)).fetchone()[0] >= 2
    # The key is per source and per person: another source, or another person, is another meeting.
    assert import_meeting(api, transcript=TEXT, source="otter", external_id="g-1")["id"] != first["id"]
    assert import_meeting(api, "ben-test", transcript=TEXT, source="granola", external_id="g-1")["id"] != first["id"]
    # A meeting somebody deleted is not brought back.
    media_post(api, f"meetings/{first['id']}/delete", {})
    gone = import_meeting(api, transcript=TEXT, source="granola", external_id="g-1")
    assert gone == {"id": first["id"], "status": "deleted", "existing": True, "changed": False}


def test_participants_are_linked_to_the_roster_and_a_private_meeting_stays_theirs(api):
    made = import_meeting(api, "ben-test", participants=["Cara@Acme.example", "Ana", {"name": "Dana Reyes", "email": "dana@example.com"}],
                          private=True)
    record = meeting(api, made["id"], "ben-test")
    people = {p["email"] or p["name"]: p for p in record["participants"]}
    assert people["cara@acme.example"]["person_id"] == "cara" and people["cara@acme.example"]["name"]
    assert people["dana@example.com"]["person_id"] is None and people["dana@example.com"]["name"] == "Dana Reyes"
    assert [p["person_id"] for p in record["participants"]].count("ana") == 1
    assert "cara@acme.example" in record["confirmed_attendees"] and "Participants" in record["meeting_context"]
    # Private: its participants read it, and only its owner (or the company owner) edits it.
    assert meeting(api, made["id"], "cara-test")["can_edit"] is False


def test_a_bot_cannot_import_and_a_machine_files_only_for_a_person_on_the_roster(api):
    body = {"title": "From a machine", "transcript": TEXT, "source": "granola", "external_id": "g-7"}
    _, _, attempt = setup_attempt(api)
    assert api.post("/api/v2/meetings/import", json=body, headers=headers(attempt["token"])).status_code == 403
    machine, elsewhere = runner(api), runner(api, "ben", "Ben test Mac")
    for token, extra, status in ((machine["token"], {}, 404), (machine["token"], {"owner_email": "nobody@example.com"}, 404),
                                 (elsewhere["token"], {"owner_email": "ben@acme.example"}, 403)):
        assert api.post("/api/v2/meetings/import", json={**body, **extra}, headers=headers(token)).status_code == status
    made = api.post("/api/v2/meetings/import", json={**body, "owner_email": "Ben@Acme.example"}, headers=headers(machine["token"])).json()
    record = meeting(api, made["id"], "ben-test")
    assert record["owner"] == "ben@acme.example" and record["can_edit"] is True and record["uploaded_by"] == machine["runner_id"]
    # It is the same meeting whichever door files it: the key is the person's, not the machine's.
    again = import_meeting(api, "ben-test", title="From a machine", transcript=TEXT, source="granola", external_id="g-7")
    assert again["id"] == made["id"] and again["existing"] is True
    # A person files only their own, and Close is filed by its own worker.
    assert import_meeting(api, "ben-test", owner_email="ana@acme.example", expected=403)
    assert "own importer" in import_meeting(api, source="close", expected=422)["error"]["detail"]

