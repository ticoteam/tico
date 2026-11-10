"""A bot's reply inherits the file refusal of what its run read, so deleting that page never refuses a comment write.

A run took in, as an input folded beside the comment that started it, the wake an OK's answer wrote about a page. The
bot replied on the task. Once the page is deleted the run is refused to everyone (task_privacy.attempt_pages_readable),
and so is the reply that names it: before, the reply stayed listed, and the comment write's response (which carries
the task's thread) named a run nobody may read, so require_payload refused every later comment after saving it.
"""

import json

import pytest

from backend import read_deps, task_privacy
from backend.auth import Identity
from backend.privacy_index import ReadIndex
from backend.store import H
from backend.tests.helpers.api import assign, headers, post, ready, runner
from backend.tests.helpers.file_reviews import answer as review_answer, ask as review_ask
from backend.tests.helpers.files import turn
from backend.tests.helpers.org_rules import org
from backend.tests.helpers.pages import page
from backend.tests.helpers.task_privacy_reads import sql

READERS = (
    ("ana-test", Identity("human:ana", "owner", email="ana@acme.example")),
    ("cara-test", Identity("human:cara", "human", email="cara@acme.example")),
)


def reply_seen(api, mid, who, token, settled):
    """Whether `who` reads the reply, by each path: the Python check, the read index and the SQL message list."""
    with api.app.state.store.read() as c:
        if settled:
            read_deps.sweep(c)
        row = H.message(c, mid, include_deleted=True)
        refs = row["refs"] if isinstance(row["refs"], str) else json.dumps(row["refs"])
        answers = {
            "python": task_privacy.message_readable(c, who.actor, row),
            "index": ReadIndex(c, who).message(mid, row["conversation_id"], refs, row["in_reply_to"]),
        }
    answers["sql"] = bool(sql(api, f"SELECT id FROM messages WHERE id='{mid}'", token))
    assert len(set(answers.values())) == 1, answers
    return answers["sql"]


@pytest.mark.parametrize("settled", [False, True])
def test_a_reply_from_a_run_that_read_a_deleted_page_leaves_and_comments_still_save(api, settled):
    org(api)
    _, attempt = turn(api)
    tid = post(
        api,
        "tasks",
        {"owner": "finance", "title": "Review folios", "body": "Review", "private": False},
        token="ops-test",
    )["id"]
    fid = page(api, attempt, task=tid, title="Folio").json()["page"]["file_id"]
    # The answer to an OK of the page wakes the owner bot, with the page among the wake's sources.
    review_answer(api, review_ask(api, "ops-test", [{"file": fid}], parent=tid, question="Is it ready?"), "Approve")
    post(api, f"tasks/{tid}/comments", {"text": "Start the review", "delivery": "steer"})
    machine = runner(api)
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    run = post(api, "jobs/claim", {"bot": "finance", "start_places": True}, token=machine["token"])["attempt"]
    post(api, f"attempts/{run['id']}/started", {"thread_id": "folio"}, token=machine["token"])
    # The running turn takes in what is queued for it on the task: the wake comes in beside the comment.
    post(api, f"attempts/{run['id']}/inputs", {}, token=machine["token"])
    with api.app.state.store.read() as c:
        root = c.execute(
            "SELECT m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            "WHERE a.id=?",
            (run["id"],),
        ).fetchone()[0]
        inputs = [
            r[0]
            for r in c.execute(
                "SELECT m.refs FROM attempt_inputs i JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=?",
                (run["id"],),
            )
        ]
    # The shape that was missed: the page is named by a folded input, not by the run's own message.
    assert fid not in (root or "") and any(fid in (refs or "") for refs in inputs), (root, inputs)
    done = post(
        api,
        f"attempts/{run['id']}/complete",
        {"outcome": "completed", "text": "Checked", "last_seq": 0},
        token=machine["token"],
    )
    reply = done["message"]["id"]
    for token, who in READERS:
        assert reply_seen(api, reply, who, token, settled) is True

    removed = api.delete("/api/v2/files/" + fid, headers=headers())
    assert removed.status_code == 200, removed.text
    for token, who in READERS:
        with api.app.state.store.read() as c:
            assert not task_privacy.attempt_readable(c, who.actor, run["id"])
        made = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Next step"}, headers=headers(token))
        assert made.status_code == 200, made.text
        assert run["id"] not in made.text and fid not in made.text
        # The reply names the run, so it goes with it, on every read path.
        assert reply_seen(api, reply, who, token, settled) is False
        thread = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers(token))
        assert thread.status_code == 200 and reply not in thread.text
        assert "Next step" in json.dumps(thread.json())
