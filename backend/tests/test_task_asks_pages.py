"""An ask that shows a page: its words come from that page, so a reader who may no longer open it reads none of them."""

import json

from backend.tests.helpers.api import get, headers
from backend.tests.helpers.engine import old_question
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.page_audience_review4 import public_page


def test_an_ask_that_showed_a_page_goes_dark_for_whoever_may_no_longer_open_it(api):
    # Codex review 2 of the asks track, finding 7: the ask's line, choices, answer, history and wake words stayed
    # readable after its page was narrowed away from the reader, or deleted. Nothing writes a question with choices now
    # (a question is a ❓); a company's older ones are read as before, so this one is written as the engine wrote it.
    tid, _, fid = public_page(api)
    label = "Confidential draft"
    brief = {"why": "It decides what ships next.", "details": ["It ships.", "Nothing ships."], "recommend": label}
    with api.app.state.store.transaction() as c:
        asked = old_question(
            c, tid, "bot:ops", "cara", f"Which: {label}?", [label, "Neither"], shows=["page:" + fid], brief=brief
        )
    rid = asked["requirement_id"]
    answered = api.post(
        f"/api/v2/tasks/{tid}/asks/{rid}/answer",
        json={"value": label, "version": get(api, f"tasks/{tid}")["task"]["version"]},
        headers=headers("cara-test"),
    )
    assert answered.status_code == 200, answered.text
    assert label in json.dumps(get(api, f"tasks/{tid}", token="cara-test"))
    with api.app.state.store.read() as c:
        wakes = [
            json.loads(row[0] or "{}")
            for row in c.execute(
                "SELECT refs FROM messages WHERE wake_key IS NOT NULL AND body LIKE ?", (f"%{label}%",)
            )
        ]
    # The bot's wake carries where its words came from, so its message reads go through the same page check.
    assert wakes and all("page:" + fid in json.dumps(refs.get("body_sources")) for refs in wakes), wakes

    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert api.get("/api/v2/pages/" + fid, headers=headers("cara-test")).status_code == 404
        for path in (f"tasks/{tid}", f"tasks/{tid}/comments", f"tasks/{tid}/context"):
            shown = json.dumps(get(api, path, token="cara-test"))
            assert label not in shown and fid not in shown, (deleted, path, shown)
