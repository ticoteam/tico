"""Helpers that other tests share, moved out of test_page_audience_review6.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.file_reviews import ask as review_ask
from backend.tests.helpers.org_rule_check import Judge
from backend.tests.helpers.org_rules import rule


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
