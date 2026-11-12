"""Statement growth stays bounded on the slow reads measured in flight."""

import json

import pytest

from backend import read_cache
from backend.store import H
from backend.tests.helpers.api import get  # noqa: F401
from backend.tests.helpers.read_batches import privacy_parties  # noqa: F401
from backend.tests.helpers.task_privacy_reads import traced_get


def seed_turns(c, start, end, parent=None, tag="t"):
    if parent is None:
        parent = H.task_create(c, "human:ana", f"Turns {tag} {end}", "Checklist", "bot:ops", lint=False)
    for n in range(start, end):
        key = f"{tag}-{n}"
        reply = H.task_comment(c, "human:ana", parent["id"], "Request " + key, wake=False)
        aid = "run-" + key
        c.execute(
            "INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,'ops','completed',?,?)",
            ("job-" + key, reply["id"], H.now(), aid),
        )
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES(?,?,'ops','rn',0,?,'completed',?,?)",
            (aid, "job-" + key, aid, H.now(), H.now()),
        )
        c.execute(
            "UPDATE attempts SET trigger='message',message_id=?,task_id=?,exit='ok' WHERE id=?",
            (reply["id"], parent["id"], aid),
        )
    return parent


def test_turn_page_stays_bounded_as_turns_grow(api, monkeypatch):
    counts, pages = {}, {}
    with api.app.state.store.transaction() as c:
        parent = seed_turns(c, 0, 3)
    page, counts[3] = traced_get(api, "bots/ops/turns", "ana-test")
    pages[3] = [row["id"] for row in page]
    assert len(page) == 3
    with api.app.state.store.transaction() as c:
        seed_turns(c, 3, 30, parent)
    page, counts[30] = traced_get(api, "bots/ops/turns", "ana-test")
    pages[30] = [row["id"] for row in page]
    assert len(page) == 30
    few, many = counts[3], counts[30]
    print(f"turns: {few} -> {many} statements")
    assert many - few <= 12, (few, many)
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    again = [row["id"] for row in get(api, "bots/ops/turns")]
    assert again == pages[30]


def test_deleted_message_with_a_list_run_ref_does_not_abort_the_page(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Bad run ref", "Checklist", "bot:ops", lint=False)
        kept = H.task_comment(c, "human:ana", parent["id"], "Keep", wake=False)
        dropped = H.task_comment(c, "human:ana", parent["id"], "Drop", wake=False)
        c.execute(
            "UPDATE messages SET deleted_at=?, refs=? WHERE id=?",
            (H.now(), json.dumps({"turn_id": ["not-a-run"], "task": parent["id"]}), dropped["id"]),
        )
        cid = parent["conversation_id"]
    ids = [row["id"] for row in get(api, "conversations/" + cid + "/messages")]
    assert kept["id"] in ids
    assert dropped["id"] not in ids


def test_message_page_stays_bounded_as_messages_grow(api, monkeypatch):
    counts, pages = {}, {}
    rooms = {}
    for size in (3, 30):
        with api.app.state.store.transaction() as c:
            rooms[size] = seed_turns(c, 0, size, tag=f"m{size}")["conversation_id"]
        page, counts[size] = traced_get(api, "conversations/" + rooms[size] + "/messages", "ana-test")
        pages[size] = [row["id"] for row in page]
        assert len(page) >= size
    few, many = counts[3], counts[30]
    print(f"messages: {few} -> {many} statements")
    assert many - few <= 12, (few, many)
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    again = [row["id"] for row in get(api, "conversations/" + rooms[30] + "/messages")]
    assert again == pages[30]
