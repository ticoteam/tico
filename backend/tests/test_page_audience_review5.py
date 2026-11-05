"""Generated recommendations and spend decisions keep copied labels revocable."""
import json

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_file_reviews import ask as review_ask, answer as review_answer
from backend.tests.test_page_audience import edit
from backend.tests.test_page_audience_review4 import public_page
from backend.tests.test_task_review import ask


def test_multi_recommendation_fallback_keeps_its_copied_source(api):
    tid, _, fid = public_page(api)
    question = ask("cara")
    question["questions"][0]["multi"] = True
    question["questions"][0]["options"][0].update(label="Confidential draft", file=fid + "@1", recommended=True)
    question["if_no_answer"] = {"do": "Confidential draft", "kind": "reversible"}
    sibling = post(api, f"tasks/{tid}/files", {"name": "recommendation.txt", "text": "Public notes", "ask": question},
                   token="ops-test")
    with api.app.state.store.read() as c:
        asked = c.execute("SELECT ask_message_id FROM task_file_reviews WHERE file_id=?", (sibling["file_id"],)).fetchone()[0]
    batch = post(api, "batch", {}, token="cara-test")
    assert batch["item"]["recommend"] == "Confidential draft"
    post(api, "needs-you/recommended", {"ask": asked}, token="cara-test")
    with api.app.state.store.read() as c:
        message = H.message(c, c.execute("SELECT id FROM messages WHERE in_reply_to=?", (asked,)).fetchone()[0])
        assert "Confidential draft" in message["body"]
        assert message["refs"]["body_sources"] == [{"file": fid, "version": 1}]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for token in ("cara-test", "finance-test"):
            for path in (f"tasks/{tid}", f"tasks/{tid}/comments", f"tasks/{tid}/answers", "inbox", "batch"):
                shown = json.dumps(get(api, path, token=token))
                assert "Confidential draft" not in shown and fid not in shown, (path, shown)


def test_spend_pick_history_events_and_notice_keep_the_label_source(api):
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    made = review_ask(api, "ops-test", [{"file": fid}, {"file": ordinary}], on="cara", parent=tid,
                      private=False, pick=True, spend={"label": "Confidential draft", "amount": "12.50", "what": "Render"})
    answered = review_answer(api, made, ["Confidential draft"], token="cara-test")
    aid = answered["answer"]["spend_approval"]
    assert any(e.get("note") == 'Picked "Confidential draft".'
               for e in get(api, "approvals/" + aid, token="cara-test")["history"])
    with api.app.state.store.read() as c:
        event = c.execute("SELECT detail FROM events WHERE target=? AND action='approval.decide'", (aid,)).fetchone()[0]
        notice = H.message(c, c.execute("SELECT id FROM messages WHERE json_extract(refs,'$.approval')=? "
                                       "AND sender_id=?", (aid, H.KEEPER)).fetchone()[0])
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
