from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401


def media_post(api, path, body=None, token="ana-test", key=None, expected=200, **kwargs):
    response = api.post("/api/" + path, json=body, headers=headers(token, key), **kwargs)
    assert response.status_code == expected, response.text
    return response.json()


def import_meeting(api, token="ana-test", expected=200, **fields):
    """A finished meeting of the caller's, filed through the import API."""
    body = {"title": "Pricing call", "transcript": "Ana: We agreed to ship on Friday.", "review": "live", **fields}
    response = api.post("/api/v2/meetings/import", json=body, headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json()


def test_a_private_meeting_reaches_its_participants_and_nobody_else(api):
    invited = import_meeting(api, "ben-test", title="Comp review", private=True, participants=["Cara@Acme.example"])[
        "id"
    ]
    elsewhere = import_meeting(api, "cara-test", title="Board prep", private=True, participants=["ana@acme.example"])[
        "id"
    ]
    # The spelling in the participant list is not the spelling in the roster; the match ignores case.
    assert api.get("/api/meetings/" + invited, headers=headers("cara-test")).json()["can_edit"] is False
    assert api.get("/api/meetings/" + elsewhere, headers=headers("ben-test")).status_code == 403
    assert [r["id"] for r in api.get("/api/meetings", headers=headers("ben-test")).json()] == [invited]
    assert sorted(r["id"] for r in api.get("/api/meetings", headers=headers()).json()) == sorted([invited, elsewhere])
