"""Generated recommendations and spend decisions keep copied labels revocable."""

import json

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.file_reviews import ask as review_ask, answer as review_answer
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.page_audience_review4 import public_page


def test_spend_pick_history_events_and_notice_keep_the_label_source(api):
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    made = review_ask(
        api,
        "ops-test",
        [{"file": fid}, {"file": ordinary}],
        on="cara",
        parent=tid,
        private=False,
        pick=True,
        spend={"label": "Confidential draft", "amount": "12.50", "what": "Render"},
    )
    answered = review_answer(api, made, ["Confidential draft"], token="cara-test")
    aid = answered["answer"]["spend_approval"]
    assert any(
        e.get("note") == 'Picked "Confidential draft".'
        for e in get(api, "approvals/" + aid, token="cara-test")["history"]
    )
    with api.app.state.store.read() as c:
        event = c.execute("SELECT detail FROM events WHERE target=? AND action='approval.decide'", (aid,)).fetchone()[0]
        notice = H.message(
            c,
            c.execute(
                "SELECT id FROM messages WHERE json_extract(refs,'$.approval')=? AND sender_id=?", (aid, H.KEEPER)
            ).fetchone()[0],
        )
        assert H._json(event, {})["body_sources"] == notice["refs"]["body_sources"] == [{"file": fid, "version": 1}]
        assert "Confidential draft" in notice["body"]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert api.get("/api/v2/pages/" + fid, headers=headers("cara-test")).status_code == 404
        for path in ("approvals/" + aid, "tasks/" + made["task"]["id"], "inbox", "audit"):
            shown = json.dumps(get(api, path, token="cara-test"))
            assert "Confidential draft" not in shown and fid not in shown, (path, shown)
        with api.app.state.store.read() as c:
            for payload in (H._json(event, {}), notice):
                shown = files.readable_page_refs(c, api.app.state.auth, Identity("human:cara", "human"), payload)
                assert "Confidential draft" not in json.dumps(shown) and fid not in json.dumps(shown)


def test_held_review_pick_text_keeps_its_page_source(api):
    from backend.tests.helpers.org_rule_check import Judge
    from backend.tests.helpers.org_rules import rule

    tid, _, fid = public_page(api)
    text = "Never publish an unreviewed draft."
    api.app.state.judge = Judge(api.app.state.store, {text: "Confidential draft"})
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    rule(api, {"kind": "internal", "text": text, "who": {"bots": ["ops"]}, "if_broken": "escalate", "reviewer": "cara"})
    held = review_ask(
        api, "ops-test", [{"file": fid}, {"file": ordinary}], on="cara", parent=tid, private=False, pick=True
    )["held"]["id"]
    assert "Confidential draft" in get(api, "rule-holds/" + held, token="cara-test")["text"]
    with api.app.state.store.read() as c:
        stored = H._json(c.execute("SELECT payload FROM org_rule_holds WHERE id=?", (held,)).fetchone()[0], {})
        assert {"file": fid} in stored["body_sources"]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        shown = json.dumps(get(api, "rule-holds/" + held, token="cara-test"))
        assert "Confidential draft" not in shown and fid not in shown, shown
