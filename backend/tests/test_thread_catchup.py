"""Catch up: a bounded summary of what is new in a thread, posted to the caller's Assistant DM. The summary reads only
what the caller may read, so a message they cannot read reaches neither the reply nor the model that writes it."""

import json

import pytest

from backend import thread_catchup, threads, updates
from backend.auth import Identity
from backend.tests.test_api import api, as_member, post  # noqa: F401

BEN = Identity("human:ben", "human", "ben@acme.example")


@pytest.fixture(autouse=True)
def plain_member(api):
    as_member(api, "ben@acme.example")          # the fixture makes Ben an admin; these tests are about membership


def thread(api, members=("human:ben",), title="Launch", parent=None):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=list(members),
                              parent_message_id=parent)["id"]


def say(api, thread_id, text, actor="human:cara"):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text)


def catch_up(api, thread_id, scope="subtree", token="ben-test", expected=200):
    return post(api, f"threads/{thread_id}/catch-up", {"scope": scope}, token=token, expected=expected)


def assistant_bodies(api):
    with api.app.state.store.read() as c:
        return [r["body"] for r in c.execute("SELECT body FROM messages WHERE refs_json LIKE '%catch_up%'").fetchall()]


def no_model(monkeypatch):
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: None)


def test_the_summary_never_includes_what_the_person_cannot_read(api, monkeypatch):
    tid = thread(api)
    visible = say(api, tid, "Lease renewal is due Friday.")
    hidden = say(api, tid, "Ben's offer is confidential.")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET legacy_audience='shared:nobody', restricted=1 WHERE id=?", (hidden["id"],))
        viewer = threads.viewer(api.app.state.auth, BEN)
        assert threads.readable(c, viewer, visible["id"])
        assert not threads.readable(c, viewer, hidden["id"])
    prompts = []

    def writer(system, user):
        prompts.append(user)
        return {"summary": "The lease renewal is due Friday."}

    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    catch_up(api, tid)
    assert prompts and any("Lease renewal" in p for p in prompts)
    assert all("confidential" not in p for p in prompts)
    assert all("confidential" not in body for body in assistant_bodies(api))


def test_catch_up_posts_one_summary_to_the_assistant_room(api, monkeypatch):
    no_model(monkeypatch)
    tid = thread(api)
    say(api, tid, "Lease renewal is due Friday.")
    response = catch_up(api, tid)
    assert response["messages"] == 1 and response["posted"]
    bodies = assistant_bodies(api)
    assert len(bodies) == 1 and "Lease renewal is due Friday." in bodies[0]


def test_nothing_new_posts_nothing(api, monkeypatch):
    no_model(monkeypatch)
    tid = thread(api)
    say(api, tid, "Lease renewal is due Friday.")
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, threads.viewer(api.app.state.auth, BEN), tid)
    response = catch_up(api, tid)
    assert response["posted"] is None
    assert assistant_bodies(api) == []


def test_a_failing_model_still_gets_the_excerpt(api, monkeypatch):
    def broken(system, user):
        raise RuntimeError("provider down")

    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: broken)
    tid = thread(api)
    say(api, tid, "Lease renewal is due Friday.")
    assert catch_up(api, tid)["posted"]
    assert "Lease renewal is due Friday." in assistant_bodies(api)[0]


def test_subtree_scope_reads_sub_threads_and_thread_scope_does_not(api, monkeypatch):
    no_model(monkeypatch)
    tid = thread(api)
    parent = say(api, tid, "Lease renewal is due Friday.")
    child = thread(api, parent=parent["id"], title="Renewal terms")
    say(api, child, "Terms are in the drive.")
    assert catch_up(api, tid, scope="thread")["messages"] == 1
    assert catch_up(api, tid, scope="subtree")["messages"] == 2


def test_a_thread_the_person_cannot_read_is_not_found(api, monkeypatch):
    no_model(monkeypatch)
    tid = thread(api, members=("human:ana",))       # Ben is not a member, so the thread does not exist for him
    say(api, tid, "Ana only.")
    catch_up(api, tid, expected=404)
    assert assistant_bodies(api) == []


def test_a_message_revoked_while_the_model_reads_is_not_posted(api, monkeypatch):
    """The model reads the thread, then a message in it stops being readable to Ben before the reply is written. The
    summary was built from it, so nothing is posted, and the reply says why."""
    tid = thread(api)
    say(api, tid, "Lease renewal is due Friday.")
    secret = say(api, tid, "Ben's offer is confidential.")

    def writer(system, user):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE messages SET legacy_audience='shared:nobody', restricted=1 WHERE id=?", (secret["id"],))
        return {"summary": "Lease renewal is due Friday. Ben's offer is confidential."}

    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    catch_up(api, tid, expected=409)
    assert assistant_bodies(api) == []


def test_the_budget_counts_the_lines_the_model_reads_not_the_raw_bodies(api, monkeypatch):
    no_model(monkeypatch)
    tid = thread(api)
    for i in range(30):
        say(api, tid, f"Note {i}: " + "x" * 3000)
    response = catch_up(api, tid)
    assert response["messages"] >= 20
    assert response["left_out"] == 30 - response["messages"]


def test_a_message_too_big_for_the_budget_is_reported_as_omitted_not_as_nothing_new(api, monkeypatch):
    no_model(monkeypatch)
    monkeypatch.setattr(thread_catchup, "MAX_BYTES", 10)
    tid = thread(api)
    say(api, tid, "Lease renewal is due Friday.")
    response = catch_up(api, tid)
    assert response["posted"] is None and response["messages"] == 0
    assert response["left_out"] == 1
    assert assistant_bodies(api) == []


def prompts_of(monkeypatch):
    prompts = []

    def writer(system, user):
        prompts.append(user)
        return {"summary": "Short."}

    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    return prompts


def test_someone_added_later_gets_only_what_came_after_they_joined_in_any_sub_thread(api, monkeypatch):
    tid = thread(api, members=("human:cara",))
    parent = say(api, tid, "Old root plan")
    sub = thread(api, members=(), parent=parent["id"])
    say(api, sub, "Old sub-thread plan")
    with api.app.state.store.transaction() as c:
        threads.add_member(c, "human:cara", tid, "human:ben")
    say(api, tid, "New root note")
    say(api, sub, "New sub-thread note")
    prompts = prompts_of(monkeypatch)
    got = catch_up(api, tid)
    assert got["messages"] == 2 and got["left_out"] == 0
    assert "New root note" in prompts[0] and "New sub-thread note" in prompts[0]
    assert "Old" not in prompts[0]


def test_what_the_migration_carried_and_imported_history_are_not_new(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        tid = threads.create(c, "human:cara", title="All hands", audience="company")["id"]
    say(api, tid, "Before the earlier-messages cut")
    opened = say(api, tid, "Earlier message opened to everyone")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET legacy_audience=NULL WHERE id=?", (opened["id"],))
        carried = c.execute("SELECT value FROM sequences WHERE name='messages'").fetchone()[0]
        c.execute("INSERT OR REPLACE INTO registry_metadata(key,value_json) VALUES(?,?)",
                  (threads.MIGRATION_KEY, json.dumps({"at": "2026-10-01T00:00:00Z", "seq": carried})))
        threads.post(c, "human:cara", tid, "Imported from Slack", refs={"import": True})
    say(api, tid, "Written after it")
    prompts = prompts_of(monkeypatch)
    got = catch_up(api, tid)
    assert got["messages"] == 1
    assert "Written after it" in prompts[0]
    for old in ("Before the", "Earlier message", "Imported"):
        assert old not in prompts[0]
    # Nothing else new: nothing is written, and the model is not asked.
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, threads.viewer(api.app.state.auth, BEN), tid)
    assert catch_up(api, tid)["posted"] is None and len(prompts) == 1
