"""Held answer prose derives copied-label provenance from the question task it answers."""

import json

import pytest

from backend.auth import Identity
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.org_rules import rule
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.page_audience_review4 import public_page
from backend.tests.helpers.page_audience_review6 import held_answers


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
