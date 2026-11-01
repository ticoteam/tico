"""Catch up: a bounded summary of what is new in a thread, posted to the caller's Assistant DM. The summary reads only
what the caller may read, so a message they cannot read reaches neither the reply nor the model that writes it."""

from backend import threads, updates
from backend.auth import Identity
from backend.tests.test_api import api, post  # noqa: F401

EVE = Identity("human:eve", "human", "eve@acme.example")


def thread(api, members=("human:eve",), title="Launch", parent=None):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=list(members),
                              parent_message_id=parent)["id"]


def say(api, thread_id, text, actor="human:cara"):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text)


def catch_up(api, thread_id, scope="subtree", token="eve-test", expected=200):
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
        viewer = threads.viewer(api.app.state.auth, EVE)
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
        threads.mark_all_read(c, threads.viewer(api.app.state.auth, EVE), tid)
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
    tid = thread(api, members=("human:ben",))
    say(api, tid, "Ben only.")
    catch_up(api, tid, expected=404)
    assert assistant_bodies(api) == []
