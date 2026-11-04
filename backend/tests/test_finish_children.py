"""Closing or finishing a task closes open descendants, with the usual history and close permissions."""
import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

ANA, BEN, CMO, SEO = "human:ana", "human:ben", "bot:cmo", "bot:seo"


def hub(path):
    c = H.connect(path)
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cmo", "seo")},
                    {"people": [{"id": "ana", "email": "ana@acme.example"}, {"id": "ben", "email": "ben@acme.example"}]})
    return c


def make(c, actor, title, owner, parent=None):
    return H.task_create(c, actor, title, "", owner, parent_id=parent and parent["id"], lint=False)


def history(c, row):
    return [dict(r) for r in c.execute(
        "SELECT * FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at,rowid", (row["id"],))]


def finish(c, parent, operation):
    if operation == "close":
        return H.task_close(c, ANA, parent["id"], note="Launch cancelled")
    if operation == "bot_close":
        return H.task_close(c, CMO, parent["id"], note="Launch cancelled")
    if operation == "done":
        return H.task_update(c, CMO, parent["id"], status="done", note="Page is live")
    return H.task_update(c, ANA, parent["id"], step=operation)


@pytest.mark.parametrize("operation", ["close", "bot_close", "done", "Shipped", "Cancelled"])
def test_finish_closes_nested_open_subtasks_and_preserves_finished_subtasks(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                         {"name": "Shipped", "status": "done"}, {"name": "Cancelled", "status": "closed"}],
                          numbered=True, mover=True)
    parent = H.task_create(c, CMO if operation == "bot_close" else ANA, "Launch the pricing page", "", CMO,
                           type=board["id"], step="Build", lint=False)
    copy = make(c, CMO, "Write the pricing copy", SEO, parent)
    proof = make(c, CMO, "Proofread the pricing copy", SEO, copy)
    H.task_update(c, SEO, proof["id"], status="doing")
    finished = []
    for state in ("closed", "done", "declined"):
        kid = make(c, CMO, f"Check the {state} plan", SEO, parent)
        if state == "closed":
            H.task_close(c, CMO, kid["id"], note="Already cancelled")
        else:
            H.task_update(c, SEO, kid["id"], status=state, note="Already finished")
        finished.append(kid)
    # Descendants below finished children still close, without rewriting the finished children.
    late = make(c, CMO, "Check the plan names in German", SEO, finished[0])
    before = [(H.task(c, kid["id"]), history(c, kid)) for kid in finished]
    result = finish(c, parent, operation)
    assert result["status"] == ("done" if operation in ("done", "Shipped") else "closed")
    for kid in (copy, proof, late):
        after = H.task(c, kid["id"])
        assert after["status"] == "closed"
        assert after["note"] == f"Closed with #{parent['number']}"
        records = history(c, kid)
        assert any(e["kind"] == "status" and e["new"] == "closed" and e["note"] == after["note"] for e in records)
        assert any(e["kind"] == "closed_with" and e["new"] == parent["id"] for e in records)
        closed = c.execute("SELECT detail FROM events WHERE action='task.close' AND target=?", (kid["id"],)).fetchall()
        assert len(closed) == 1 and json.loads(closed[0][0])["with_parent"] == parent["id"]
        assert TR.parent_of(c, kid["id"]) == (copy["id"] if kid == proof else finished[0]["id"] if kid == late else parent["id"])
    assert [(H.task(c, kid["id"]), history(c, kid)) for kid in finished] == before
    assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.wake')='subtask_finished'").fetchone()
    H.task_update(c, ANA, parent["id"], status="open", note="One more change")
    assert all(H.task(c, kid["id"])["status"] == "closed" for kid in (copy, proof, late))
    assert any(e["kind"] == "children" and e["new"].startswith("Still closed") for e in history(c, parent))
    c.close()


def test_a_bot_finishing_its_task_closes_a_persons_subtask_with_one_parent_notice(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Launch the pricing page", CMO)
    ask = make(c, CMO, "Approve the final copy", ANA, parent)
    H.task_update(c, CMO, parent["id"], status="done", note="Page is live")
    assert H.task(c, ask["id"])["status"] == "closed"
    told = [r[0] for r in c.execute("SELECT body FROM messages WHERE recipient_id=? AND body NOT LIKE 'New task%'", (ANA,))]
    assert told == ["Finished: Launch the pricing page\nPage is live\nClosed with it: Approve the final copy"]
    c.close()


@pytest.mark.parametrize("operation", ["close", "done"])
def test_private_subtask_refuses_the_whole_finish_without_detaching_or_leaking_its_title(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Plan the offsite", CMO)
    visible = make(c, CMO, "Book the venue", SEO, parent)
    secret = H.task_create(c, CMO, "Compare confidential salaries", "", CMO,
                           parent_id=parent["id"], private=True, lint=False)
    assert not H.task_private_readable(c, ANA, secret)
    before = [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, visible, secret)]
    with pytest.raises(H.Refused, match="a private subtask") as exc:
        if operation == "close":
            H.task_close(c, ANA, parent["id"])
        else:
            H.task_update(c, ANA, parent["id"], status="done")
    assert secret["title"] not in str(exc.value)
    assert [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, visible, secret)] == before
    assert TR.parent_of(c, secret["id"]) == parent["id"]
    # A reader unrelated to the parent cannot gain close authority through its children.
    with pytest.raises(H.Refused):
        H.task_close(c, BEN, parent["id"], mover=False)
    c.close()


@pytest.mark.parametrize("operation", ["close", "done"])
def test_bot_cannot_close_a_persons_subtask_and_api_finish_rolls_back(api, operation):
    _, _, attempt = setup_attempt(api, "ops")
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x"},
                  token=attempt["token"] if operation == "close" else "ana-test")
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Draft the refund copy", "", "bot:cpo", parent_id=parent["id"], lint=False)
    child = post(api, "tasks", {"owner": "cpo", "title": "Approve the refund copy", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    with api.app.state.store.read() as c:
        before = [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, own, child)]
    body = {"version": parent["version"], "note": "Shipped", **({"close": True} if operation == "close" else {"status": "done"})}
    response = api.post("/api/v2/tasks/" + parent["id"], json=body, headers=headers(attempt["token"]))
    assert response.status_code >= 400, response.text
    assert child["title"] in response.text
    with api.app.state.store.read() as c:
        assert [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, own, child)] == before
        assert TR.parent_of(c, child["id"]) == parent["id"]
    version = get(api, "tasks/" + parent["id"])["task"]["version"]
    post(api, "tasks/" + parent["id"], {"version": version, **({"close": True} if operation == "close" else {"status": "done"})})
    assert get(api, "tasks/" + child["id"])["task"]["status"] == "closed"
    assert get(api, "tasks/" + own["id"])["task"]["status"] == "closed"
