from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.media import import_meeting


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
