"""Held answer prose derives copied-label provenance from the question it answers."""
import json

import pytest

from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_file_reviews import ask as review_ask
from backend.tests.test_org_rule_check import Judge
from backend.tests.test_files import turn
from backend.tests.test_org_rules import org, rule
from backend.tests.test_page_audience import edit
from backend.tests.test_page_audience_review4 import public_page
from backend.tests.test_pages import page
from backend.tests.test_task_review import ask


def held_answers(api, tid, fid, label):
    """Create holds through the pick, comment, ordinary-file and typed-answer production routes."""
    ordinary = post(api, f"tasks/{tid}/files", {"name": "held-public.txt", "text": "Public choice"})["file_id"]
    pick = review_ask(api, "ops-test", [{"file": fid}, {"file": ordinary}], on="reader", parent=tid,
                      topic="held-pick", private=False, pick=True, question="Which draft needs a held review?")
    question = ask("reader")
    question["questions"][0]["options"][0].update(label=label, file=fid + "@1")
    comment = post(api, f"tasks/{tid}/comments", {"text": "Choose the draft", "ask": question}, token="ops-test")["comment"]
    typed_question = post(api, f"tasks/{tid}/comments", {"text": "Name the draft", "ask": question}, token="ops-test")["comment"]
    sibling = post(api, f"tasks/{tid}/files", {"name": "held-notes.txt", "text": "Public notes", "ask": question}, token="ops-test")
    text = "Never publish an unreviewed draft."
    api.app.state.judge = Judge(api.app.state.store, {text: label})
    rule(api, {"kind": "internal", "text": text, "who": {"people": ["reader"]},
               "if_broken": "escalate", "reviewer": "cara"})
    cases = [(pick["task"]["id"], {"comment": pick["question"]["id"]}, "decision"),
             (tid, {"comment": comment["id"]}, "verdict"),
             (tid, {"file": sibling["file_id"], "version": 1}, "verdict")]
    holds = []
    for task, target, qid in cases:
        result = post(api, f"tasks/{task}/answers", {"target": target, "answers": {qid: [label]}}, token="reader-test")
        holds.append(result["held"]["id"])
        assert get(api, "rule-holds/" + holds[-1], token="cara-test")["text"] == qid + ": " + label
    typed = post(api, f"messages/{typed_question['id']}/answer", {"text": label}, token="reader-test")
    holds.append(typed["held"]["id"])
    for hid in holds:
        view = get(api, "rule-holds/" + hid, token="cara-test")
        assert label in view["text"] and view["can_send"]
        with api.app.state.store.read() as c:
            stored = H._json(c.execute("SELECT payload FROM org_rule_holds WHERE id=?", (hid,)).fetchone()[0], {})
            assert {"file": fid, "version": 1} in stored["body_sources"]
            assert "source" not in stored["replay"]["body"]
    return holds


@pytest.mark.parametrize("send", [False, True])
def test_held_and_replayed_answers_revoke_question_option_labels(api, send):
    tid, _, fid = public_page(api)
    reader = post(api, "access/people", {"name": "Reader", "email": "reader@acme.example"})
    assert reader["person"] == "reader"
    api.app.state.store.settings.test_identities["reader-test"] = Identity("human:reader", "human", "reader@acme.example")
    label = "Confidential draft"
    holds = held_answers(api, tid, fid, label)
    if send:
        for hid in holds:
            view = get(api, "rule-holds/" + hid, token="cara-test")
            sent = post(api, "rule-holds/" + hid + "/send", {"nonce": view["nonce"]}, token="cara-test")
            assert sent["hold"]["state"] == "sent"
        assert label in json.dumps(get(api, f"tasks/{tid}/comments", token="cara-test"))
    edit(api, fid, {"people": ["reader"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert api.get("/api/v2/pages/" + fid, headers=headers("cara-test")).status_code == 404
        for path in [*("rule-holds/" + hid for hid in holds), "rule-holds", f"tasks/{tid}/comments", f"tasks/{tid}/answers"]:
            shown = json.dumps(get(api, path, token="cara-test"))
            assert label not in shown and fid not in shown, (path, shown)


def test_held_runner_answer_keeps_its_question_option_source(api):
    org(api)
    machine, attempt = turn(api)
    label = "Confidential draft"
    tid = post(api, "tasks", {"owner": "ops", "title": "Review drafts", "body": "Review", "private": False})["id"]
    made = page(api, attempt, task=tid, title=label)
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    question = ask("bot:ops")
    question["questions"][0]["options"][0].update(label=label, file=fid + "@1")
    asked = post(api, f"tasks/{tid}/comments", {"text": "Choose the draft", "ask": question})["comment"]
    # Give the already-claimed test turn this question as its input, as a runner claim would.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET message_id=? WHERE id=(SELECT job_id FROM attempts WHERE id=?)",
                  (asked["id"], attempt["id"]))
    text = "Never publish an unreviewed draft."
    api.app.state.judge = Judge(api.app.state.store, {text: label})
    rule(api, {"kind": "internal", "text": text, "who": {"bots": ["ops"]},
               "if_broken": "escalate", "reviewer": "cara"})
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "held-answer"}, token=machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": label, "last_seq": 0},
         token=machine["token"])
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id,payload FROM org_rule_holds WHERE operation='reply'").fetchone()
        assert row and {"file": fid, "version": 1} in H._json(row["payload"], {})["body_sources"]
        hid = row["id"]
    assert get(api, "rule-holds/" + hid, token="cara-test")["text"] == label
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        shown = json.dumps(get(api, "rule-holds/" + hid, token="cara-test"))
        assert label not in shown and fid not in shown
