"""File reviews (backend/file_reviews.py): a bot's question about exact file versions, the verdict each answer records,
the spend a pick approves, and `hub file approved` reading it back."""

import pytest

from backend.store import H
from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401  (fixtures)


def publish(api, token, text, name="cut.md", path=None):
    r = api.post("/api/v2/files/uploads", json={"name": name, "text": text, **({"path": path} if path else {})},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["file"]


def ask(api, token, review, on="ana", expected=200, question="Is this cut ready?", **body):
    r = api.post("/api/v2/task-questions", json={"on": on, "question": question, "review": review, **body},
                 headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def answer(api, made, labels, other="", token="ana-test", expected=200):
    return post(api, f"tasks/{made['task']['id']}/answers", {"target": {"comment": made["question"]["id"]},
                "answers": {"decision": labels}, **({"other": other} if other else {})}, token, expected=expected)


def approved(api, token, **query):
    r = api.get("/api/v2/file-approvals", params=query, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_approve_binds_to_the_version_and_request_changes_supersedes_it(api):
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    v1 = publish(api, bot, "# Cut one")
    made = ask(api, bot, [{"file": v1["id"]}], topic="cut-1")
    refs = made["question"]["refs"]
    assert [o["label"] for o in made["question"]["ask"]["questions"][0]["options"]] == ["Approve", "Request changes"]
    assert refs["review"]["files"][0]["version"] == v1["version"] and refs["attachments"][0]["file_id"] == v1["id"]
    # The asking bot never answers its own review.
    answer(api, made, ["Approve"], token=bot, expected=403)
    out = answer(api, made, ["Approve"])
    assert out["answer"]["reviewed"] == [{"file": v1["id"], "version": v1["version"],
                                         "digest": refs["review"]["files"][0]["digest"], "verdict": "approve"}]
    got = approved(api, bot, file=v1["id"])
    assert got["approved"] and got["by"] == "human:ana" and got["task"] == made["task"]["id"]
    # A new version is a new review: it never inherits the old one's approval.
    v2 = publish(api, bot, "# Cut two")
    assert v2["id"] == v1["id"] and v2["version"] == v1["version"] + 1
    assert not approved(api, bot, file=v1["id"])["approved"]
    assert approved(api, bot, file=v1["id"], version=v1["version"])["approved"]
    # Request changes on the same version later wins over the earlier approval.
    again = ask(api, bot, [{"file": v1["id"], "version": v1["version"]}], topic="cut-1-again",
                question="Is the first cut ready after all?")
    answer(api, again, ["Request changes"], other="Tighten the intro.")
    got = approved(api, bot, file=v1["id"], version=v1["version"])
    assert not got["approved"] and got["verdict"] == "changes"


def test_a_pick_spend_is_approved_once_and_spent_once(api):
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    a, b = publish(api, bot, "# A", "teaser-a.md"), publish(api, bot, "# B", "teaser-b.md")
    made = ask(api, bot, [{"file": a["id"]}, {"file": b["id"]}], pick=True,
               spend={"label": "teaser a", "amount": "12.50", "what": "Render the final cut"})
    labels = [o["label"] for o in made["question"]["ask"]["questions"][0]["options"]]
    assert labels == ["teaser a", "teaser b", "Request changes"]
    out = answer(api, made, ["teaser a"])
    assert [r["verdict"] for r in out["answer"]["reviewed"]] == ["approve", "passed"]
    aid = out["answer"]["spend_approval"]
    # The same answer again keeps the one approval.
    assert answer(api, made, ["teaser a"])["answer"]["spend_approval"] == aid
    assert approved(api, bot, file=a["id"])["approved"] and not approved(api, bot, file=b["id"])["approved"]
    with api.app.state.store.transaction() as c:
        row = H.approval(c, aid)
        assert row["kind"] == "spend" and row["decision"] == "approved" and row["requested_by_id"] == "bot:ops"
        assert c.execute("SELECT count(*) FROM approvals WHERE kind='spend'").fetchone()[0] == 1
        H.approval_consume(c, "bot:ops", aid)
    with pytest.raises(H.Refused):
        with api.app.state.store.transaction() as c:
            H.approval_consume(c, "bot:ops", aid)
    # A spend on a label that is not an option is refused at ask time.
    ask(api, bot, [{"file": a["id"]}, {"file": b["id"]}], pick=True, topic="other", expected=422,
        spend={"label": "Neither", "amount": "5", "what": "Render"})


def test_the_person_asked_must_be_able_to_open_the_file(api):
    # The file sits in Ana's private chat with the bot: Ben cannot open it, so he cannot be asked to review it.
    _, _, attempt = setup_attempt(api)
    v1 = publish(api, attempt["token"], "# Private cut")
    r = api.post("/api/v2/task-questions", json={"on": "ben", "question": "Is this cut ready?",
                                                 "review": [{"file": v1["id"]}]}, headers=headers(attempt["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    r = api.get("/api/v2/file-approvals", params={"file": v1["id"]}, headers=headers("ben-test"))
    assert r.status_code == 404


def test_a_bot_checks_its_checkout_file_by_the_digest_of_its_bytes(api):
    import hashlib
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    r = api.post("/api/v2/files/uploads", json={"name": "cut.md", "text": "# Cut", "path": "cuts/cut.md"},
                 headers=headers(bot))
    assert r.status_code == 200, r.text
    answer(api, ask(api, bot, [{"file": r.json()["file"]["id"]}]), ["Approve"])
    same, edited = (hashlib.sha256(text.encode()).hexdigest() for text in ("# Cut", "# Cut, edited"))
    assert approved(api, bot, path="cuts/cut.md", digest=same)["approved"]
    assert not approved(api, bot, path="cuts/cut.md", digest=edited)["approved"]


def test_a_later_verdict_on_a_newer_version_with_the_same_bytes_decides_by_path(api):
    import hashlib
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    same = hashlib.sha256(b"# Cut").hexdigest()
    v1 = publish(api, bot, "# Cut", path="cuts/cut.md")
    answer(api, ask(api, bot, [{"file": v1["id"]}], topic="cut-v1"), ["Approve"])
    v2 = publish(api, bot, "# Cut, reworked", path="cuts/cut.md")
    v3 = publish(api, bot, "# Cut", path="cuts/cut.md")
    assert v2["id"] == v1["id"] and v3["id"] == v1["id"] and v3["version"] == 3
    changes = ask(api, bot, [{"file": v3["id"], "version": 3}], topic="cut-v3", question="Is the cut ready again?")
    answer(api, changes, ["Request changes"], other="Tighten the intro.")
    got = approved(api, bot, path="cuts/cut.md", digest=same)
    assert not got["approved"] and got["version"] == 3 and got["verdict"] == "changes"
    fixed = ask(api, bot, [{"file": v3["id"], "version": 3}], topic="cut-v3-fixed",
                question="Is the cut ready after the fix?")
    answer(api, fixed, ["Approve"])
    got = approved(api, bot, path="cuts/cut.md", digest=same)
    assert got["approved"] and got["version"] == 3 and got["verdict"] == "approve"


def review(api, token, body, expected=200):
    r = api.post("/api/v2/file-reviews", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_a_bot_reviews_another_bots_file_and_only_by_names_whose_verdicts_count(api):
    _, _, ops = setup_attempt(api)
    _, _, finance = setup_attempt(api, bot="finance")
    task = post(api, "tasks", {"owner_id": "bot:ops", "title": "Cut the launch video", "body": "For the launch."})
    r = api.post("/api/v2/files/uploads", json={"name": "cut.md", "text": "# Cut", "task": task["id"]},
                 headers=headers(ops["token"]))
    assert r.status_code == 200, r.text
    fid = r.json()["file"]["id"]
    # A bot never reviews its own bot's file.
    review(api, ops["token"], {"file": fid, "task": task["id"], "verdict": "approve"}, expected=403)
    got = review(api, finance["token"], {"file": fid, "task": task["id"], "verdict": "approve", "note": "Clean."})
    assert got["approved"] and got["by"] == "bot:finance" and got["question"] is None
    # A bot's verdict never satisfies the default: only a person's counts.
    assert not approved(api, ops["token"], file=fid)["approved"]
    assert approved(api, ops["token"], file=fid, by="bot:finance")["approved"]
    assert approved(api, ops["token"], file=fid, by="any")["approved"]
    assert not approved(api, ops["token"], file=fid, by="bot:writer")["approved"]
    assert api.get("/api/v2/file-approvals", params={"file": fid, "by": "everyone"},
                   headers=headers(ops["token"])).status_code == 422
    # Each --by reads its own newest verdict.
    review(api, finance["token"], {"file": fid, "task": task["id"], "verdict": "changes", "note": "Trim the end."})
    review(api, "ana-test", {"file": fid, "task": task["id"], "verdict": "approve"})
    assert approved(api, ops["token"], file=fid)["by"] == "human:ana"
    assert approved(api, ops["token"], file=fid)["approved"]
    assert not approved(api, ops["token"], file=fid, by="bot:finance")["approved"]
    assert approved(api, ops["token"], file=fid, by="any")["approved"]


def test_the_same_question_about_a_new_version_is_new_and_about_the_same_versions_a_duplicate(api):
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    v1 = publish(api, bot, "# Cut one")
    ask(api, bot, [{"file": v1["id"], "version": 1}], topic="first")
    v2 = publish(api, bot, "# Cut two")
    ask(api, bot, [{"file": v2["id"], "version": 2}], topic="second")
    again = ask(api, bot, [{"file": v2["id"], "version": 2}], topic="third", expected=422)
    assert again["error"]["code"] == "duplicate"


def test_deleting_a_review_question_is_allowed_and_its_verdicts_stop_counting(api):
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    v1 = publish(api, bot, "# Cut")
    made = ask(api, bot, [{"file": v1["id"]}])
    answer(api, made, ["Approve"])
    assert approved(api, bot, file=v1["id"])["approved"]
    # A verdict is not work done on the task (backend/task_delete.py FILE_WORK); the bot's wake by the answer is.
    from backend.task_delete import delete_tasks
    with api.app.state.store.read() as c:
        assert "bot_file_activity.task_id" not in delete_tasks(c, [made["task"]["id"]])["refused"]
    assert post(api, f"tasks/{made['task']['id']}/delete", {}) == {"deleted": made["task"]["id"]}
    assert not approved(api, bot, file=v1["id"])["approved"]


def test_a_reader_who_cannot_open_a_reviewed_file_sees_none_of_its_details_whatever_its_kind(api):
    _, _, attempt = setup_attempt(api)
    bot = attempt["token"]
    v1 = publish(api, bot, "# Cut one", "launch-cut.md")
    made = ask(api, bot, [{"file": v1["id"]}], topic="cut-1")

    def attachments(who):
        listed = api.get(f"/api/v2/tasks/{made['task']['id']}/comments", headers=headers(who))
        assert listed.status_code == 200, listed.text
        return next(m for m in listed.json()["comments"] if m["id"] == made["question"]["id"])["refs"]["attachments"]
    [mine] = attachments("ana-test")
    assert mine["title"] and mine["name"] == "launch-cut.md" and mine["size"]
    [theirs] = attachments("cara-test")
    assert theirs == {"id": v1["id"], "file_id": v1["id"], "version": 1, "ref": f"{v1['id']}@1", "kind": mine["kind"],
                      "removed": True, "current_version": None}
