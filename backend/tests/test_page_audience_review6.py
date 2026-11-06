"""Held answer prose derives copied-label provenance from the question task it answers."""

import json

import pytest

from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_file_reviews import ask as review_ask
from backend.tests.test_org_rule_check import Judge
from backend.tests.test_org_rules import rule
from backend.tests.test_page_audience import edit
from backend.tests.test_page_audience_review4 import public_page


def held_answers(api, tid, fid, label):
    """Create a hold through the pick production route (a question task answered with a copied label)."""
    ordinary = post(api, f"tasks/{tid}/files", {"name": "held-public.txt", "text": "Public choice"})["file_id"]
    pick = review_ask(
        api,
        "ops-test",
        [{"file": fid}, {"file": ordinary}],
        on="reader",
        parent=tid,
        topic="held-pick",
        private=False,
        pick=True,
        question="Which draft needs a held review?",
    )
    text = "Never publish an unreviewed draft."
    api.app.state.judge = Judge(api.app.state.store, {text: label})
    rule(
        api,
        {"kind": "internal", "text": text, "who": {"people": ["reader"]}, "if_broken": "escalate", "reviewer": "cara"},
    )
    cases = [(pick["task"]["id"], {"task": pick["task"]["id"]}, "decision")]
    holds = []
    for task, target, qid in cases:
        result = post(api, f"tasks/{task}/answers", {"target": target, "answers": {qid: [label]}}, token="reader-test")
        holds.append(result["held"]["id"])
        assert get(api, "rule-holds/" + holds[-1], token="cara-test")["text"] == qid + ": " + label
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
    api.app.state.store.settings.test_identities["reader-test"] = Identity(
        "human:reader", "human", "reader@acme.example"
    )
    label = "Confidential draft"
    holds = held_answers(api, tid, fid, label)
    if send:
        for hid in holds:
            view = get(api, "rule-holds/" + hid, token="cara-test")
            sent = post(api, "rule-holds/" + hid + "/send", {"nonce": view["nonce"]}, token="cara-test")
            assert sent["hold"]["state"] == "sent"
        # A sent answer is recorded on the question task it answers (its answer), not as a comment on the page's task.
        with api.app.state.store.read() as c:
            pick = c.execute("SELECT id FROM tasks WHERE question_topic LIKE '%held-pick%'").fetchone()[0]
        assert label in json.dumps(get(api, f"tasks/{pick}/answers", token="cara-test"))
    edit(api, fid, {"people": ["reader"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert api.get("/api/v2/pages/" + fid, headers=headers("cara-test")).status_code == 404
        for path in [
            *("rule-holds/" + hid for hid in holds),
            "rule-holds",
            f"tasks/{tid}/comments",
            f"tasks/{tid}/answers",
        ]:
            shown = json.dumps(get(api, path, token="cara-test"))
            assert label not in shown and fid not in shown, (path, shown)
