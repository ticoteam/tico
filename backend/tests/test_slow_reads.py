"""Statement growth stays bounded on the slow reads measured in flight."""

import json

import pytest

from backend import read_cache
from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_read_batches import privacy_parties  # noqa: F401
from backend.tests.test_task_privacy_reads import traced_get


def seed_turns(c, start, end, parent=None, tag="t"):
    if parent is None:
        parent = H.task_create(c, "human:ana", f"Turns {tag} {end}", "Checklist", "bot:ops", lint=False)
    for n in range(start, end):
        key = f"{tag}-{n}"
        reply = H.task_comment(c, "human:ana", parent["id"], "Request " + key, wake=False)
        aid = "run-" + key
        c.execute("INSERT INTO jobs(id,message_id,bot,state,created_at,attempt_id) VALUES(?,?,'ops','done',?,?)",
                  ("job-" + key, reply["id"], H.now(), aid))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES(?,?,'ops','rn',0,?,'done',?,?)", (aid, "job-" + key, aid, H.now(), H.now()))
        c.execute("INSERT INTO turns(id,bot,started_at,message_id,task_id,exit) VALUES(?,'ops',?,?,?,'ok')",
                  (aid, H.now(), reply["id"], parent["id"]))
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
        c.execute("UPDATE messages SET deleted_at=?, refs=? WHERE id=?",
                  (H.now(), json.dumps({"turn_id": ["not-a-run"], "task": parent["id"]}), dropped["id"]))
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


def test_operations_does_not_query_each_computer(api):
    with api.app.state.store.transaction() as c:
        for n in range(6):
            c.execute("INSERT INTO runners(id,label,operator,token_hash,created_at) VALUES(?,?, 'ana',?,?)",
                      (f"rn{n}", f"Mac {n}", f"th{n}", H.now()))
    from backend.tests.test_task_privacy_reads import traced
    _, text = traced(api, lambda: get(api, "operations"))
    joined = "\n".join(text)
    assert "FROM assignments WHERE runner_id=?" not in joined
    assert joined.count("FROM attempts a LEFT JOIN start_places") == 1


def test_covering_indexes_return_the_same_rows(tmp_path):
    conn = H.connect(tmp_path / "hub.db")
    try:
        task = H.task_create(conn, "human:ana", "Visible", "Body", "bot:ops", lint=False)
        hidden = H.task_create(conn, "human:ben", "Hidden", "Secret", "human:ben", private=True, lint=False)
        conn.commit()
        names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"tasks_private_parties", "conversations_id_task"} <= names
        id_plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN SELECT id FROM tasks"))
        assert "COVERING INDEX" in id_plan
        plain = [tuple(row) for row in conn.execute(
            "SELECT id,requester,owner,guest,private FROM tasks WHERE private IS NOT 0 ORDER BY id")]
        covered = [tuple(row) for row in conn.execute(
            "SELECT id,requester,owner,guest,private FROM tasks INDEXED BY tasks_private_parties "
            "WHERE private IS NOT 0 ORDER BY id")]
        assert plain == covered
        assert {row[0] for row in plain} == {hidden["id"]}
        rooms = dict(conn.execute("SELECT id,task_id FROM conversations"))
        indexed = dict(conn.execute("SELECT id,task_id FROM conversations INDEXED BY conversations_id_task"))
        assert rooms == indexed
        assert task["conversation_id"] in rooms
        plan = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id,task_id FROM conversations INDEXED BY conversations_id_task"))
        assert "conversations_id_task" in plan
        asked = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT t.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            "JOIN tasks t ON t.conversation_id=m.conversation_id "
            "WHERE m.kind='ask' AND m.to_actor='human:ana' AND m.answered_by IS NULL"))
        assert "messages_open_ask" in asked
    finally:
        conn.close()
