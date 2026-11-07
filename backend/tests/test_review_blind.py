"""A later review judges a change without the earlier reviews' findings (backend/review_blind.py)."""

import json

from backend import hubdb as H
from backend import task_relations as TR
from backend.auth import Identity
from backend.store import Problem
from backend.tests.test_check_pools import RECEIPT, checks, set_pool, team, ticket
from backend.tests.test_github import api, get  # noqa: F401
from backend.tests.test_new_bot_flows import bot_token
from backend.tests.test_review_gate import HEAD, move

PERSON = "ana-test"  # the owner's test token (backend/tests/test_github.py headers)


def read(api, token, path):
    return api.get("/api/v2/" + path, headers={"Authorization": "Bearer " + token})


def review_1_judged(api):
    """A ticket whose Review 1 (rev-a) approved with findings, and whose Review 2 (rev-b) is filed."""
    team(api)
    task = ticket(api, RECEIPT)
    set_pool(api, task, "Review 1", ["rev-a"])
    set_pool(api, task, "Review 2", ["rev-b"])
    assert move(api, task).status_code == 200
    [first] = checks(api, task)
    with api.app.state.store.transaction() as c:
        H.task_update(
            c,
            "bot:rev-a",
            first["id"],
            status="done",
            note=f"VERDICT: approve PR #412 head {HEAD}\nFindings: the retry loop has no backoff.",
        )
    task = get(api, "tasks/" + task["id"])["task"]
    assert move(api, task, "Review 2").status_code == 200
    [second] = checks(api, task)
    with api.app.state.store.read() as c:
        findings = [
            r[0] for r in c.execute("SELECT id FROM messages WHERE json_extract(refs,'$.subtask')=?", (first["id"],))
        ]
    assert findings, "Review 1's result was forwarded to the ticket"
    return task, first, second, findings


def test_review_2_cannot_read_review_1_subtask_or_its_forwarded_findings(api):
    task, first, second, findings = review_1_judged(api)
    rev_b = bot_token(api, "rev-b")
    assert read(api, rev_b, f"tasks/{first['id']}").status_code == 404
    page = read(api, rev_b, f"tasks/{task['id']}")
    assert page.status_code == 200
    seen = json.dumps(page.json())
    assert first["id"] not in seen, "Review 1's subtask is not listed to Review 2"
    for mid in findings:
        assert mid not in seen, "Review 1's findings are not in Review 2's view of the ticket"
    assert read(api, rev_b, f"tasks/{second['id']}").status_code == 200, "Review 2 reads its own check"


def test_builder_and_a_person_still_read_review_1(api):
    task, first, _, findings = review_1_judged(api)
    builder = read(api, bot_token(api, "cpo"), f"tasks/{task['id']}")
    assert builder.status_code == 200
    assert all(mid in json.dumps(builder.json()) for mid in findings)
    person = read(api, PERSON, f"tasks/{task['id']}")
    assert person.status_code == 200
    assert all(mid in json.dumps(person.json()) for mid in findings)
    assert read(api, PERSON, f"tasks/{first['id']}").status_code == 200


def test_prompt_relations_leave_out_review_1(api):
    task, first, second, _ = review_1_judged(api)
    who = Identity(H.bot_actor("rev-b"), "bot", agent="test")
    auth = api.app.state.auth
    with api.app.state.store.read() as c:

        def readable(row):
            try:
                auth.task_row(c, who, row)
                return True
            except Problem:
                return False

        lines = TR.for_prompt(c, task["id"], lambda other: bool(other) and readable(other))
    ids = {line["id"] for line in lines}
    assert second["id"] in ids
    assert first["id"] not in ids


def test_the_blindness_ends_when_review_2_closes(api):
    task, first, second, findings = review_1_judged(api)
    rev_b = bot_token(api, "rev-b")
    assert read(api, rev_b, f"tasks/{first['id']}").status_code == 404
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:rev-b", second["id"], status="done", note=f"VERDICT: approve PR #412 head {HEAD}")
    assert read(api, rev_b, f"tasks/{first['id']}").status_code == 200
    seen = json.dumps(read(api, rev_b, f"tasks/{task['id']}").json())
    assert all(mid in seen for mid in findings)


def test_the_check_request_says_it_reviews_independently(api):
    _, _, second, _ = review_1_judged(api)
    assert "Review independently" in second["body"]
