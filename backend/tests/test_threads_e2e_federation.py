"""Shared threads between two installs, end to end over each install's API (spec section 9, "federation with two
installs and a fake relay").

Each install is a real app with its own database (test_federation's `make_side`), and HQ is test_federation's in-memory
relay. The threads are read and written through `/api/v2/threads` on each side, and shared, paused and unshared through
`/api/v2/federation`; the relay is ticked by hand, on one fake clock, as the running servers would tick it.
"""

import pytest

from backend.tests.test_federation import OWNER, clock, connect, drive, hq, make_side, row_for, share, status  # noqa: F401
from backend.tests.threads_e2e import call, create_thread, ok, require_threads_api, say, timeline


def sidebar_titles(side, token="ana-test"):
    return {t.get("title"): t["id"] for t in ok(call(side.client, "GET", "threads", token))["threads"]}


def replica_id(side, title, token="ana-test"):
    """The id of the replica of a shared thread on this install, as the sidebar lists it to the person reading."""
    return sidebar_titles(side, token).get(title)


def add_reader(side, thread_id, actor):
    return side.client.post(f"/api/v2/federation/threads/{thread_id}/readers", json={"actor": actor}, headers=OWNER)


def texts(side, thread_id, token="ana-test"):
    return [m.get("body") for m in timeline(side.client, token, thread_id)]


@pytest.fixture
def peered(make_side, hq, clock):  # noqa: F811  (the fixtures, as the module imports them)
    alpha, beta = make_side("alpha"), make_side("beta")
    connect(alpha, beta, hq)
    return alpha, beta


def shared_plan(alpha, beta, title="Launch plan"):
    """A members thread on alpha, shared with beta, with one message already in it."""
    t = create_thread(alpha.client, title=title, audience="members")
    share(alpha, t["id"], beta)
    say(alpha.client, "ana-test", t["id"], "Launch is Tuesday")
    return t


def test_a_thread_shared_with_a_peer_gets_its_messages_on_the_other_install(peered, hq, clock):
    alpha, beta = peered
    require_threads_api(alpha.client)
    shared_plan(alpha, beta)
    drive(alpha, beta)
    rid = replica_id(beta, "Launch plan")
    assert rid, "the shared thread shows on the peer's sidebar"
    add_reader(beta, rid, "human:ana")                 # a replica's readers are the people an admin adds on that install
    assert "Launch is Tuesday" in texts(beta, rid), "the message crossed the relay"


def test_a_person_on_the_peer_reads_a_replica_only_once_an_admin_adds_them(peered, hq, clock):
    alpha, beta = peered
    require_threads_api(alpha.client)
    shared_plan(alpha, beta)
    drive(alpha, beta)
    rid = replica_id(beta, "Launch plan")
    assert rid
    hidden = beta.client.get(f"/api/v2/threads/{rid}", headers={"Authorization": "Bearer ben-test"})
    assert hidden.status_code == 404, hidden.text
    assert "Launch is Tuesday" not in str(hidden.text), "a hidden replica leaks no text"
    assert add_reader(beta, rid, "human:ben").status_code == 200
    seen = beta.client.get(f"/api/v2/threads/{rid}", headers={"Authorization": "Bearer ben-test"})
    assert seen.status_code == 200, seen.text


def test_a_reply_on_the_peer_comes_back_to_the_install_that_owns_the_thread(peered, hq, clock):
    alpha, beta = peered
    require_threads_api(alpha.client)
    t = shared_plan(alpha, beta)
    drive(alpha, beta)
    rid = replica_id(beta, "Launch plan")
    add_reader(beta, rid, "human:ana")
    say(beta.client, "ana-test", rid, "Confirmed from beta")
    drive(alpha, beta)
    assert "Confirmed from beta" in texts(alpha, t["id"]), "the reply came back to alpha's thread"


def test_pausing_a_peering_stops_both_directions_and_resuming_catches_up(peered, hq, clock):
    alpha, beta = peered
    require_threads_api(alpha.client)
    t = shared_plan(alpha, beta)
    drive(alpha, beta)
    rid = replica_id(beta, "Launch plan")
    add_reader(beta, rid, "human:ana")
    peering = row_for(alpha, beta.id)
    paused = alpha.client.post(f"/api/v2/federation/peerings/{peering}/pause", json={"paused": True}, headers=OWNER)
    assert paused.status_code == 200, paused.text
    say(alpha.client, "ana-test", t["id"], "Sent while paused")
    drive(alpha, beta)
    assert "Sent while paused" not in texts(beta, rid), "nothing crosses a paused peering"
    resumed = alpha.client.post(f"/api/v2/federation/peerings/{peering}/pause", json={"paused": False}, headers=OWNER)
    assert resumed.status_code == 200, resumed.text
    drive(alpha, beta)
    assert "Sent while paused" in texts(beta, rid), "resuming repairs what was missed"


def test_an_unshared_thread_stops_reaching_the_peer(peered, hq, clock):
    alpha, beta = peered
    require_threads_api(alpha.client)
    t = shared_plan(alpha, beta)
    drive(alpha, beta)
    rid = replica_id(beta, "Launch plan")
    add_reader(beta, rid, "human:ana")
    gone = alpha.client.delete(f"/api/v2/federation/threads/{t['id']}/installs/{beta.id}", headers=OWNER)
    assert gone.status_code == 200, gone.text
    say(alpha.client, "ana-test", t["id"], "After the unshare")
    drive(alpha, beta)
    assert "After the unshare" not in texts(beta, rid), "an unshared thread sends nothing more"
