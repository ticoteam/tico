"""Read costs stay bounded when every row has different provenance."""

import pytest

from backend import read_cache, task_privacy as privacy
from backend.store import H
from backend.tests.test_api import api, get, unrelated_task_managers  # noqa: F401
from backend.tests.test_task_privacy_reads import traced_get


@pytest.fixture(autouse=True)
def privacy_parties(api):
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created_at) VALUES('rn','Mac','ana','th',?)", (H.now(),))


def blob(c, bid):
    c.execute("INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
              "VALUES(?,'human:ana',?,1,'note.md','text/markdown',?)", (bid, bid, H.now()))


def seed(c, size):
    parent = H.task_create(c, "human:ana", f"Release {size}", "Checklist", "bot:ops", lint=False)
    for n in range(size):
        key = f"{size}-{n}"
        source = H.task_create(c, "human:ben", f"Source {key}", "Input", "human:ben",
                               private=n == 1, lint=False)
        input_ = H.task_comment(c, "human:ben", source["id"], "Input " + key, wake=False)
        moved = H.task_comment(c, "human:ben", source["id"], "Moved " + key, wake=False)
        reply = H.task_comment(c, "human:ana", parent["id"], "Request " + key, wake=False,
                               extra_refs={"inputs": [input_["id"]], "answers": [input_["id"]]})
        aid = "run-" + key
        c.execute("INSERT INTO jobs(id,message_id,bot,state,created_at,attempt_id) VALUES(?,?,'ops','done',?,?)",
                  ("job-" + key, reply["id"], H.now(), aid))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES(?,?,'ops','rn',0,?,'done',?,?)", (aid, "job-" + key, aid, H.now(), H.now()))
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, input_["id"]))
        H.event(c, "human:ana", privacy.INPUT_MOVED, aid,
                {"message_id": moved["id"], "tasks": [source["id"]], "to_attempt": "missing-" + key})
        c.execute("INSERT INTO turns(id,bot,started_at,task_id,exit) VALUES(?,'ops',?,?,'ok')",
                  (aid, H.now(), parent["id"]))
        H.task_comment(c, "bot:ops", parent["id"], "Done " + key, wake=False,
                       extra_refs={"turn_id": aid, "related_task": source["id"]})
        H._task_event(c, parent["id"], "human:ana", "note", None, source["id"], "")
        bid, fid = "blob-" + key, "file-" + key
        blob(c, bid)
        blob(c, "media-" + key)
        c.execute("INSERT INTO blob_media(blob_id,poster_blob_id,media_state) VALUES(?,?,'ready')",
                  ("media-" + key, bid))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (source["id"], "media-" + key))
        c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,locator,current_version,task_id,"
                  "first_activity_at,last_activity_at) VALUES(?,'ops',?,?,?,'document','tico_blob',1,?,?,?)",
                  (fid, "task:" + source["id"], fid, "File " + key, source["id"], H.now(), H.now()))
        c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,attempt_id,actor,created_at) "
                  "VALUES(?,1,?,?,1,'note.md','text/markdown',?,'bot:ops',?)", (fid, bid, bid, aid, H.now()))
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (reply["id"], bid))
        c.execute("INSERT INTO bot_file_activity(file_id,actor,action,task_id,created_at) VALUES(?,'human:ben','created',?,?)",
                  ("file-3-0" if size == 30 else fid, source["id"], H.now()))
    return parent["id"]


@pytest.mark.parametrize("surface", ["files", "task", "activity"])
def test_distinct_file_and_task_provenance_is_batched(api, monkeypatch, surface):
    counts = {}
    pages = {}
    for size in (3, 30):
        with api.app.state.store.transaction() as c:
            tid = seed(c, size)
        for viewer in ("ana-test", "ben-test"):
            path = {"files": "bots/ops/files?limit=2", "task": "tasks/" + tid,
                    "activity": "files/file-3-0/activity"}[surface]
            page, counts[size, viewer] = traced_get(api, path, viewer)
            pages[viewer] = path, page
            expected = size - (viewer == "ana-test")
            if surface == "files":
                # The whole list is filtered before its two-row page is cut.
                assert page["total"] == expected + (3 - (viewer == "ana-test") if size == 30 else 0)
                assert len(page["files"]) == 2
            elif surface == "task":
                assert len(page["runs"]) == expected
                assert len(page["comments"]) == expected * 2
            else:
                assert len(page["activity"]) == (1 if size == 3 else expected + 1)
    for viewer in ("ana-test", "ben-test"):
        few, many = counts[3, viewer], counts[30, viewer]
        print(f"{surface} {viewer}: {few} -> {many} statements")
        assert many - few <= 8, (viewer, surface, few, many)
    # Priming never changes the page or the visibility total, even with different provenance per row.
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    for viewer, (path, page) in pages.items():
        assert get(api, path, viewer) == page


def test_task_questions_batch_answers_without_exposing_private_replies(api, monkeypatch):
    counts, pages = {}, {}
    for size in (3, 30):
        with api.app.state.store.transaction() as c:
            parent = H.task_create(c, "human:ana", f"Review {size}", "Checklist", "bot:ops", lint=False)
            hidden = H.task_create(c, "human:ben", f"Private {size}", "Secret", "human:ben", private=True, lint=False)
            for n in range(size):
                ask = H.task_comment(c, "bot:ops", parent["id"], f"Question {n}", wake=False,
                                     ask={"who": "human:ana", "questions": [{"id": "ship", "question": "Ship?"}]})
                H.task_comment(c, "human:ana", parent["id"], f"Answer {n}", wake=False, answer_to=ask["id"],
                               extra_refs={"related_task": hidden["id"]} if n == 1 else None)
        for viewer in ("ana-test", "ben-test"):
            page, counts[size, viewer] = traced_get(api, "tasks/" + parent["id"], viewer)
            pages[viewer] = page
            questions = [m for m in page["comments"] if m["kind"] == "ask"]
            assert len(questions) == size
            expected = size - (viewer == "ana-test")
            assert sum(len(m["answers"]) for m in questions) == expected
            assert len(page["comments"]) == size + expected
    for viewer in ("ana-test", "ben-test"):
        assert counts[30, viewer] - counts[3, viewer] <= 8, counts
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    for viewer, page in pages.items():
        assert get(api, "tasks/" + parent["id"], viewer) == page


@pytest.mark.parametrize("primed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_blob_cycles_keep_private_provenance_in_both_orders(api, primed, reverse):
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, "human:ben", "Private", "Secret", "human:ben", private=True, lint=False)
        for bid in ("a", "b"):
            blob(c, bid)
        c.execute("INSERT INTO blob_media(blob_id,poster_blob_id,media_state) VALUES('a','b','ready'),('b','a','ready')")
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,'b')", (hidden["id"],))
    for actor, expected in (("human:ana", False), ("human:ben", True)):
        with api.app.state.store.read() as c:
            order = ["b", "a"] if reverse else ["a", "b"]
            if primed:
                privacy.prime_blobs(c, order)
            memo = {}
            assert [privacy.blob_readable(c, actor, bid, memo=memo) for bid in order] == [expected] * 2


@pytest.mark.parametrize("primed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_blob_depth_cap_cannot_reuse_a_shorter_walk(api, primed, reverse):
    with api.app.state.store.transaction() as c:
        for n in range(102):
            blob(c, f"depth-{n}")
        for n in range(101):
            c.execute("INSERT INTO blob_media(blob_id,poster_blob_id,media_state) VALUES(?,?,'ready')",
                      (f"depth-{n + 1}", f"depth-{n}"))
    with api.app.state.store.read() as c:
        order = ["depth-0", "depth-2"]
        if reverse:
            order.reverse()
        if primed:
            privacy.prime_blobs(c, order)
        memo = {}
        result = {bid: privacy.blob_readable(c, "human:ana", bid, memo=memo) for bid in order}
        assert result == {"depth-0": False, "depth-2": True}
