"""A bot's computer dates files the hub already holds, without their bytes (POST /api/v2/files/dates, docs/files.md):
only a file of that bot, at that path, whose current version has that digest moves, only back, and only from a time
that says when Tico copied it."""

import hashlib

from backend.tests.helpers.api import assign, claim, expire, headers, post, ready, runner
from backend.tests.helpers.files import publish, turn

SEPT = 1757000000  # 2025-09-04T15:33:20Z, an old commit's time


def put(api, attempt, path, text):
    r = publish(api, attempt, path.rsplit("/", 1)[-1], text, path=path)
    assert r.status_code == 200, r.text
    return r.json()["file"]["id"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def times(api):
    r = api.get("/api/v2/bots/ops/files?limit=50", headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    return {f["id"]: f["last_activity_at"] for f in r.json()["files"]}


def test_a_date_moves_only_the_copy_of_that_content_back_and_needs_no_bytes(api):
    machine, attempt = turn(api)
    # A checkout copied by a computer before uploads carried times: every file stamped with the copy's time.
    old = put(api, attempt, "reports/2025-09-04-case-review.md", "# Case review")
    edited = put(api, attempt, "reports/plan.md", "# Plan")
    future = put(api, attempt, "reports/notes.md", "# Notes")
    copied = times(api)

    def dates(files, token=machine["token"], aid=attempt["id"], key=None):
        return api.post(
            "/api/v2/files/dates",
            json={"bot": "ops", "attempt": aid, "files": files},
            headers=headers(token, key),
        )

    sent = [
        {"path": "reports/2025-09-04-case-review.md", "digest": sha("# Case review"), "modified": SEPT},
        {"path": "reports/plan.md", "digest": sha("# Plan, edited on the computer"), "modified": SEPT},
        {"path": "reports/notes.md", "digest": sha("# Notes"), "modified": 4102444800},
        {"path": "reports/never-published.md", "digest": sha("x"), "modified": SEPT},
        {"path": "../outside.md", "digest": sha("# Case review"), "modified": SEPT},
    ]
    r = dates(sent, key="dates-1")
    assert r.status_code == 200, r.text
    assert r.json()["known"] == ["reports/2025-09-04-case-review.md", "reports/notes.md"], "only the digests it holds"
    assert r.json()["dated"] == 1
    now = times(api)
    assert now[old].startswith("2025-09-04T15:33:20"), "back to when it was written"
    assert now[edited] == copied[edited], "another content's time is no time for this one"
    assert now[future] == copied[future], "a time after the copy moves nothing"
    # A retry is the same answer; a later time never moves a file forward.
    assert dates(sent, key="dates-1").json() == r.json()
    assert dates([{**sent[0], "modified": SEPT + 86400 * 400}]).json()["dated"] == 0
    assert times(api)[old] == now[old]
    # Only this bot's own computer, for a turn it ran.
    other = runner(api, label="Other Mac")
    assert dates(sent[:1], token=other["token"]).status_code == 403
    assert dates(sent[:1], aid="").status_code == 422
    assert dates([{"path": "a.md", "digest": "not a digest"}]).status_code == 422


def test_a_new_computer_dates_what_another_computer_published(api):
    _, attempt = turn(api)
    fid = put(api, attempt, "reports/2025-09-04-case-review.md", "# Case review")
    # The bot moves to another computer, which has never published anything for it.
    expire(api, attempt["id"])
    second = runner(api, label="Hosted")
    assign(api, second, "ops", generation=1)
    ready(api, second, ["ops"])
    post(api, "chat/ops", {"text": "Carry on"}, "ben-test")
    later = claim(api, second, "ops")
    r = api.post(
        "/api/v2/files/dates",
        json={
            "bot": "ops",
            "attempt": later["id"],
            "files": [{"path": "reports/2025-09-04-case-review.md", "digest": sha("# Case review"), "modified": SEPT}],
        },
        headers=headers(second["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["known"] == ["reports/2025-09-04-case-review.md"]
    assert times(api)[fid].startswith("2025-09-04")
