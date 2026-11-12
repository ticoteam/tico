"""Live events (backend/events.py): one stream of tasks, messages, runs, bots and Needs you, each sent
as it commits and only to people who may read it; resumable, reset when too far behind, and ended
when sign-in lapses. Against a real listening server, so delivery is the browser's."""

import json
import sqlite3
import uuid

import pytest

from backend import events as E
from backend import hubdb as H
from backend.auth import Identity
from backend.store import Problem
from backend.tests.helpers.api import headers, post, restrict, setup_attempt  # noqa: F401
from backend.tests.helpers.events import live, ready, topic


def task_event(task_id):
    return lambda e: e["event"] == "tasks" and e["data"]["id"] == task_id


@pytest.mark.slow
def test_a_task_change_reaches_another_person_signed_and_never_a_non_party(api, live):
    ana, cara = live(), live("cara-test")
    ready(ana), ready(cara)
    task = post(api, "tasks", {"owner": "ben", "title": "Review the launch copy", "body": "Please."})
    secret = post(
        api, "tasks", {"owner": "ben", "title": "Review the salary bands", "body": "Please.", "private": True}
    )
    moved = post(api, f"tasks/{task['id']}/done", {"note": "Reviewed."}, token="ben-test")
    seen = cara.until(
        lambda e: (
            e["event"] == "tasks" and e["data"]["id"] == task["id"] and e["data"]["task"]["version"] == moved["version"]
        )
    )
    assert seen["data"]["task"]["version"] == moved["version"]
    assert seen["data"]["actor"] == "human:ben" and int(seen["id"]) == seen["data"]["seq"]
    # The requester sees the private one; Cara never does, nor anything about it.
    ana.until(task_event(secret["id"]))
    cara.drain(1.0)
    assert secret["id"] not in json.dumps(cara.seen)


@pytest.mark.slow
def test_resume_from_after_or_last_event_id_sends_only_what_came_since(api, live):
    first = post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    second = post(api, "tasks", {"owner": "ben", "title": "Book the room", "body": "Please."})
    for stream in (
        live("ben-test", f"?topics=tasks&after={cursor}"),
        live("ben-test", "?topics=tasks&after=0", {"Last-Event-ID": str(cursor)}),
    ):
        got = stream.until(task_event(second["id"]))
        assert got["data"]["task"]["title"] == "Book the room"
        assert first["id"] not in json.dumps(stream.seen)


@pytest.mark.slow
def test_task_and_party_send_only_those_tasks_and_a_partys_including_one_moved_away(api, live):
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    bens = post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    caras = post(api, "tasks", {"owner": "cara", "title": "Book the room", "body": "Please."})
    handed = post(api, "tasks", {"owner": "ben", "title": "Order the lunch", "body": "Please."})
    handed = post(
        api, f"tasks/{handed['id']}/owner", {"version": handed["version"], "owner": "cara", "why": "Cara orders"}
    )
    last = post(api, "tasks", {"owner": "cara", "title": "Print the badges", "body": "Please."})
    party = live("ana-test", f"?topics=tasks&after={cursor}&party=human:ben")
    party.until(
        lambda e: (
            e["event"] == "tasks" and e["data"]["id"] == handed["id"] and e["data"]["task"]["owner_id"] == "human:cara"
        )
    )
    party.drain(1.0)
    sent = {e["data"]["id"] for e in party.seen if e["event"] == "tasks"}
    assert sent == {bens["id"], handed["id"]}
    one = live("ana-test", f"?topics=tasks&after={cursor}&task={caras['id']}")
    one.until(task_event(caras["id"]))
    one.drain(1.0)
    assert {e["data"]["id"] for e in one.seen if e["event"] == "tasks"} == {caras["id"]}
    assert last["id"] not in json.dumps(one.seen)


@pytest.mark.slow
def test_a_client_further_behind_than_the_log_keeps_is_told_to_reset(api, live):
    post(api, "tasks", {"owner": "ben", "title": "Clear the old work", "body": "Please."})
    with api.app.state.store.read() as c:
        old = E.latest(c)
    assert E.sweep(api.app.state.store, H.shift(H.now(), hours=E.KEEP_HOURS + 1)) > 0
    post(api, "tasks", {"owner": "ben", "title": "Plan the new work", "body": "Please."})
    stream = live("ben-test", "?after=1")
    reset = stream.until(topic("reset"))
    assert reset["data"]["seq"] > old
    # A cursor ahead of the log (another server's, or a restored database) resets too.
    assert live("ben-test", "?after=999999").until(topic("reset"))


@pytest.mark.slow
def test_a_consumer_that_leaves_a_frame_unread_is_reset_rather_than_replayed(api, live, monkeypatch):
    """A slow consumer is sent `reset` and the newest change number, not the rest of the backlog it fell behind on."""
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    made = [post(api, "tasks", {"owner": "ben", "title": f"Stall item {n}", "body": "Please."})["id"] for n in range(3)]
    with api.app.state.store.read() as c:
        newest = E.latest(c)
    monkeypatch.setattr(E, "STALL_SECONDS", -1.0)  # every read's frames count as ones the client was slow to take
    monkeypatch.setattr(E, "ROUND_ROWS", 1)  # and each read holds one change
    stream = live("ben-test", f"?topics=tasks&after={cursor}")
    reset = stream.until(topic("reset"))
    assert reset["data"]["seq"] >= newest
    sent = [e["data"]["id"] for e in stream.seen if e["event"] == "tasks"]
    assert len(sent) == 1 and sent[0] in made


@pytest.mark.slow
def test_a_stream_ends_with_expired_when_sign_in_lapses_and_ends_on_its_own(api, live, monkeypatch):
    monkeypatch.setattr(E, "SIGN_IN_RECHECK_SECONDS", 0)  # every poll checks, so the lapse ends it before its data
    stream = live("ben-test")
    ready(stream)
    real = api.app.state.auth.authenticate

    def lapsed(headers, *args, **kwargs):
        if headers.get("authorization") == "Bearer ben-test":
            raise Problem("unauthenticated", "Sign in again", 401)
        return real(headers, *args, **kwargs)

    monkeypatch.setattr(api.app.state.auth, "authenticate", lapsed)
    # Re-checked at the next read (or keepalive), whichever comes first.
    post(api, "tasks", {"owner": "ben", "title": "After sign-out", "body": "Please."})
    stream.until(topic("expired"))
    assert "After sign-out" not in json.dumps(stream.seen)
    monkeypatch.setattr(api.app.state.auth, "authenticate", real)
    monkeypatch.setattr(E, "LIFETIME_SECONDS", 0.3)
    short = live("ben-test")
    short.until(lambda e: e["event"] == "closed", timeout=5)


def test_events_are_for_people(api):
    r, _, attempt = setup_attempt(api)
    assert api.get("/api/v2/events", headers=headers(attempt["token"])).status_code == 403
    assert api.get("/api/v2/events", headers=headers(r["token"])).status_code == 403
    assert api.get("/api/v2/events?topics=tasks,gossip", headers=headers()).status_code == 422


@pytest.mark.slow
def test_a_runs_output_and_state_reach_the_room_and_respect_bot_read_access(api, live):
    ana = live("ana-test", "?topics=runs,messages")
    ready(ana)
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t1"}, token=r["token"])
    post(
        api,
        f"attempts/{aid}/events",
        {"events": [{"seq": 1, "kind": "delta", "payload": {"text": "Working on it", "delta_kind": "text"}}]},
        token=r["token"],
    )
    out = ana.until(lambda e: e["event"] == "runs" and "output" in e["data"])
    assert out["data"]["run_id"] == aid and out["data"]["conversation_id"] == msg["conversation_id"]
    assert out["data"]["output"]["payload"]["text"] == "Working on it" and out["data"]["bot_id"] == "ops"
    states = [e["data"]["state"] for e in ana.seen if e["event"] == "runs" and "state" in e["data"]]
    assert "running" in states, states
    # Ana's room with ops is hers: Cara gets nothing of it, and with ops's activity closed to her,
    # nothing narrowed to ops either.
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    cara = live("cara-test", "?topics=runs&bot=ops")
    ready(cara)
    post(
        api,
        f"attempts/{aid}/events",
        {"events": [{"seq": 2, "kind": "delta", "payload": {"text": "Still going", "delta_kind": "text"}}]},
        token=r["token"],
    )
    ana.until(lambda e: e["event"] == "runs" and (e["data"].get("output") or {}).get("seq") == 2)
    cara.drain(1.0)
    assert "Still going" not in json.dumps(cara.seen)


def test_every_source_writes_its_topic(api):
    """Trigger coverage: each table a page follows logs the topic it belongs to."""
    store = api.app.state.store
    r, msg, attempt = setup_attempt(api)

    def logged(write):
        with store.transaction() as c:
            before = E.latest(c)
            write(c)
            return {(row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))}

    aid, cid = attempt["id"], msg["conversation_id"]
    task = post(api, "tasks", {"owner": "ben", "title": "Coverage", "body": "Please."})
    assert logged(lambda c: c.execute("UPDATE tasks SET title='Covered' WHERE id=?", (task["id"],))) == {
        ("tasks", "update"),
        ("needs", "task"),
    }
    assert logged(
        lambda c: c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,created_at) VALUES('l1',?,'pr','u',?)", (task["id"], H.now())
        )
    ) == {("tasks", "link")}
    # What a task's row shows besides its own columns: its tags, and a tag's own name.
    with store.transaction() as c:
        c.execute(
            "INSERT INTO tags(id,key,label,created_at,updated_at) VALUES('tg1','coverage','Coverage',?,?)",
            (H.now(), H.now()),
        )
    assert logged(lambda c: c.execute("INSERT INTO task_tags(task_id,tag_id) VALUES(?,'tg1')", (task["id"],))) == {
        ("tasks", "tags")
    }
    assert logged(lambda c: c.execute("UPDATE tags SET label='Covered' WHERE id='tg1'")) == {("tasks", "tags")}
    assert ("messages", "update") in logged(
        lambda c: c.execute("UPDATE messages SET body='edited' WHERE id=?", (msg["id"],))
    )
    assert logged(lambda c: c.execute("UPDATE messages SET read_at=? WHERE id=?", (H.now(), msg["id"]))) == set()
    assert logged(
        lambda c: c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,99,'delta','{}',?)",
            (aid, H.now()),
        )
    ) == {("runs", "output")}
    assert logged(lambda c: c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.now(), aid))) == set()
    # A thread run's or its job's state change moves that thread's bot status too.
    assert logged(lambda c: c.execute("UPDATE attempts SET state='running' WHERE id=?", (aid,))) == {
        ("runs", "attempt"),
        ("threads", "bot_status"),
    }
    assert logged(lambda c: c.execute("UPDATE jobs SET state='completed' WHERE attempt_id=?", (aid,))) == {
        ("runs", "job"),
        ("threads", "bot_status"),
    }
    assert logged(lambda c: c.execute("INSERT INTO bot_status(bot,state) VALUES('finance','idle')")) == {
        ("bots", "status")
    }
    assert logged(lambda c: c.execute("UPDATE bot_status SET updated_at='x' WHERE bot='finance'")) == set()
    assert logged(lambda c: c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")) == {("bots", "state")}
    assert logged(lambda c: c.execute("INSERT INTO bot_control(bot,draining) VALUES('finance',1)")) == {
        ("bots", "control")
    }
    assert logged(
        lambda c: c.execute(
            "INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
            "VALUES('g1',?,'ops','Ship it','active','human:ana',?,?)",
            (cid, H.now(), H.now()),
        )
    ) == {("messages", "goal"), ("runner", "work")}  # ops's computer: its running turn reads the goal again
    loose = post(api, "tasks", {"owner": "ben", "title": "Remove this", "body": "Please."})
    with store.read() as c:
        before = E.latest(c)
    post(api, "tasks/" + loose["id"] + "/delete", {})
    with store.read() as c:
        assert ("tasks", "delete") in {
            (row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))
        }
    # Every trigger defined is installed, so a source cannot quietly drop out.
    with store.read() as c:
        installed = {
            row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'changes_%'")
        }
        tables = {
            row[0]: {r[1] for r in c.execute(f"PRAGMA table_info({row[0]})")}
            for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert installed == {
        name
        for name, _, table, *_ in E.TRIGGERS
        if table in tables
        and all(
            need.split('.')[0] in tables and ('.' not in need or need.split('.')[1] in tables[need.split('.')[0]])
            for need in E.NEEDS.get(name, ())
        )
    }


def test_the_engines_tables_log_their_task_and_the_needs_of_its_people(api):
    """A task's row changes only on a move, a reassign or a field edit: an ask, an answer, a snooze and a task it
    files log the task too, and an ask moves the Needs-you list of the person it names."""
    store = api.app.state.store

    def logged(write):
        with store.read() as c:
            before = E.latest(c)
        write()
        with store.read() as c:
            return [
                (row["topic"], row["kind"], row["subject_id"])
                for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))
            ]

    task = post(api, "tasks", {"owner": "ana", "title": "Coverage", "body": "Please."})
    asked = []
    found = logged(
        lambda: asked.append(
            api.post(
                f"/api/v2/tasks/{task['id']}/asks", json={"to": "ben", "line": "Which day?"}, headers=headers()
            ).json()["requirement_id"]
        )
    )
    assert ("tasks", "ask", task["id"]) in found and ("needs", "ask", "human:ben") in found
    found = logged(
        lambda: post(api, f"tasks/{task['id']}/asks/{asked[0]}/answer", {"value": "Monday"}, token="ben-test")
    )
    assert ("tasks", "answer", task["id"]) in found and ("needs", "ask", "human:ben") in found
    found = logged(lambda: post(api, f"tasks/{task['id']}/snooze", {"until": "2099-01-05", "why": "Later"}))
    assert ("tasks", "snooze", task["id"]) in found and ("needs", "task", "human:ana") in found
    child = []
    found = logged(
        lambda: child.append(post(api, "tasks", {"owner": "ana", "title": "Sub", "body": "x", "parent": task["id"]}))
    )
    assert ("tasks", "filed", task["id"]) in found and ("tasks", "filed", child[0]["id"]) in found


def test_the_log_is_installed_idempotently_and_follows_its_definitions(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        E.ensure(c)
        E.ensure(c)
        before = c.execute("SELECT sql FROM sqlite_master WHERE name='changes_bots_update'").fetchone()[0]
    # A changed definition replaces the installed trigger at the next start; one removed goes.
    monkeypatch.setattr(
        E,
        "TRIGGERS",
        [
            t if t[0] != "changes_bots_update" else (*t[:3], "1", t[4])
            for t in E.TRIGGERS
            if t[0] != "changes_bot_control_delete"
        ],
    )
    with store.transaction() as c:
        E.ensure(c)
        after = c.execute("SELECT sql FROM sqlite_master WHERE name='changes_bots_update'").fetchone()[0]
        assert after != before and " WHEN 1 " in after
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='changes_bot_control_delete'").fetchone()
    # A table that is not there (yet) is skipped, not an error.
    conn = sqlite3.connect(":memory:")
    E.ensure(conn)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0


def test_a_task_link_rewritten_with_the_same_values_logs_nothing_and_a_change_to_any_column_logs_once(api):
    """The link triggers fire on a real change to any column (read from the table), never on a rewrite with the same values:
    a worktree heartbeat rewrote closed tasks' links every few minutes, and each write rebuilt every open board."""
    store = api.app.state.store
    setup_attempt(api)
    task = post(api, "tasks", {"owner": "ben", "title": "Review the links", "body": "Please."})
    with store.transaction() as c:
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,state,computer_id,created_at) "
            "VALUES('wt1',?,'worktree','u','present','rc1',?)",
            (task["id"], H.now()),
        )

    def logged(sql):
        with store.transaction() as c:
            before = E.latest(c)
            c.execute(sql)
            return {(row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))}

    with store.read() as c:
        columns = [row[1] for row in c.execute("PRAGMA table_info(task_links)")]
    assert {"repo", "detail", "computer_id", "pr_merged_at"} <= set(columns)
    for column in columns:
        assert logged(f"UPDATE task_links SET {column}={column} WHERE id='wt1'") == set(), column
    assert logged("UPDATE task_links SET state='unknown' WHERE id='wt1'") == {
        ("tasks", "link"),
        ("runner", "worktrees"),
    }
    for column in columns:
        if column in ("id", "task_id"):
            continue
        assert ("tasks", "link") in logged(f"UPDATE task_links SET {column}='x{column}' WHERE id='wt1'"), column
    # A pull request link is no worktree: the task still hears of it, the computers do not.
    with store.transaction() as c:
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,created_at) VALUES('pr1',?,'pr','https://github.com/acme/app/pull/9',?)",
            (task["id"], H.now()),
        )
    assert logged("UPDATE task_links SET title='Renamed' WHERE id='pr1'") == {("tasks", "link")}


def test_a_ring_after_a_quiet_window_is_read_at_once_and_a_burst_still_once_a_window(monkeypatch):
    monkeypatch.setattr(E.time, "monotonic", lambda: 100.0)
    assert E.gathered(0.25, read_at=0.0) == E.LEAD_SECONDS  # quiet: read almost at once
    assert abs(E.gathered(0.25, read_at=99.9) - 0.15) < 1e-9  # just read: only the rest of the window


class CountingAuth:
    """Stands in for Auth.authenticate: counts calls and answers with `role`, which a test may change."""

    def __init__(self, role="human"):
        self.calls, self.role = 0, role

    def authenticate(self, headers):
        self.calls += 1
        return Identity("human:ben", self.role)


def test_a_streams_sign_in_is_checked_once_across_a_burst_of_polls_and_again_after_the_window():
    now, auth = [100.0], CountingAuth()
    check = E.SignIn(auth, {"authorization": "Bearer ben-test"}, Identity("human:ben", "human"), clock=lambda: now[0])
    for _ in range(50):  # polls and keepalives that come within the window
        check()
    assert auth.calls == 1
    now[0] += E.SIGN_IN_RECHECK_SECONDS - 0.1
    check()
    assert auth.calls == 1
    now[0] += 0.1
    check()
    assert auth.calls == 2


def test_a_changed_role_or_a_refused_sign_in_ends_the_stream_and_is_never_remembered():
    now, auth = [0.0], CountingAuth(role="owner")
    check = E.SignIn(auth, {}, Identity("human:ben", "human"), clock=lambda: now[0])
    with pytest.raises(Problem) as refused:
        check()
    assert refused.value.status == 401 and auth.calls == 1
    # The refusal is not kept: the very next check asks again, and a sign-in that is back in place passes.
    auth.role = "human"
    check()
    assert auth.calls == 2


def test_a_read_with_tasks_and_bots_asks_for_the_viewers_bot_access_once(api, monkeypatch):
    """Task visibility and the bot lines of one read share the viewer's bot access, rather than each asking for it."""
    auth, store = api.app.state.auth, api.app.state.store
    with store.read() as c:
        cursor = E.latest(c)
    post(api, "tasks", {"owner": "ben", "title": "Count the asks", "body": "Please."})
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    real, asked = auth.bot_accesses, []
    monkeypatch.setattr(auth, "bot_accesses", lambda c, who, slugs=None: asked.append(slugs) or real(c, who, slugs))
    who = Identity("human:ben", "human", "ben@acme.example")
    with store.read() as c:
        events, _, _, _ = E.read(
            c,
            auth,
            who,
            cursor,
            topics=("tasks", "bots"),
            task_views=lambda rows, c, who, visible_sql="1": [],
            task_view=None,
        )
    assert [topic for _, topic, _ in events] == ["bots"]
    assert len(asked) == 1


def _task_views(rows, c, who, visible_sql="1"):
    return [{"id": row["id"]} for row in rows]


def test_link_insert_and_delete_events_require_both_resources_for_each_viewer(api):
    from backend.tests.helpers.meeting_task_links import link, record, remove, task

    rid = record(api, {'private': True, 'audience': 'participants'}, 'ben-test')
    t = task(api, private=False)
    store, auth = api.app.state.store, api.app.state.auth

    def read(actor, cursor):
        who = Identity('human:' + actor, 'human', actor + '@acme.example')
        with store.read() as c:
            return E.read(c, auth, who, cursor, topics=('tasks',), task_views=_task_views)[0]

    for linked in (True, False):
        with store.read() as c:
            cursor = E.latest(c)
        if linked:
            link(api, rid, [t['id']], 'ben-test')
        else:
            remove(api, rid, t['id'], 'ben-test')
        for actor in ('ana', 'cara'):
            assert read(actor, cursor) == []  # both read the task, neither reads this meeting
        sent = read('ben', cursor)
        assert len(sent) == 1
        data = sent[0][2]
        assert data['id'] == t['id'] and data['kinds'] == ['meetings']
        change = data['meeting_links'][0]
        assert change['meeting_id'] == rid
        assert bool(change['link']) == bool(change['meeting']) == linked


def test_link_events_recheck_meeting_permissions_and_hide_private_kinds_when_folded(api):
    from backend.tests.helpers.meeting_task_links import link, record, task

    rid = record(api, {'private': True, 'participants': ['cara@acme.example']}, 'ben-test')
    t = task(api, private=False)
    store, auth = api.app.state.store, api.app.state.auth
    cara = Identity('human:cara', 'human', 'cara@acme.example')
    with store.read() as c:
        cursor = E.latest(c)
    link(api, rid, [t['id']], 'ben-test')
    with store.read() as c:
        assert E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0][0][2]['meeting_links']
    with store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.participants',json('[]')) WHERE id=?", (rid,))
    with store.read() as c:
        assert E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0] == []
    with store.transaction() as c:
        c.execute('UPDATE tasks SET title=? WHERE id=?', ('Review the revised plan', t['id']))
    with store.read() as c:
        sent = E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0]
        assert len(sent) == 1 and sent[0][2]['kinds'] == ['update']
        assert 'meeting_links' not in sent[0][2] and rid not in json.dumps(sent)
    # A grant before delivery also applies to the recorded change, without sharing another viewer's result.
    with store.transaction() as c:
        c.execute("UPDATE meetings SET metadata=json_set(metadata,'$.private',json('false')) WHERE id=?", (rid,))
    with store.read() as c:
        sent = E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0]
        assert sent[0][2]['kinds'] == ['meetings', 'update']
        assert sent[0][2]['meeting_links'][0]['meeting_id'] == rid


def test_link_events_hide_tasks_and_remove_rows_when_task_read_access_is_revoked(api):
    from backend.tests.helpers.meeting_task_links import link, record, task

    rid = record(api, token='ben-test')
    t = task(api, owner='human:ben', requester='human:ben', private=True)
    store, auth = api.app.state.store, api.app.state.auth
    cara = Identity('human:cara', 'human', 'cara@acme.example')
    with store.read() as c:
        cursor = E.latest(c)
    link(api, rid, [t['id']], 'ben-test')
    with store.read() as c:
        assert E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0] == []
    with store.transaction() as c:
        c.execute('UPDATE tasks SET private=0 WHERE id=?', (t['id'],))
    with store.read() as c:
        cursor = E.latest(c)
    with store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (t['id'],))
        c.execute('DELETE FROM meeting_task_links WHERE meeting_id=?', (rid,))
    with store.read() as c:
        sent = E.read(c, auth, cara, cursor, topics=('tasks',), task_views=_task_views)[0]
        assert len(sent) == 1
        assert sent[0][2]['gone'] and sent[0][2]['kinds'] == ['update']
        assert 'meeting_links' not in sent[0][2] and rid not in json.dumps(sent)


def test_a_reader_taken_off_a_private_task_is_sent_its_id_alone_and_a_stranger_nothing(api):
    """Codex review 1, finding 1: a reader removed from an already-private task held its row until a poll. They are sent
    the task's id as gone, nothing else of it; someone who could never open it is sent nothing."""
    from backend.tests.helpers.meeting_task_links import task

    t = task(api, title="Review the salary bands", owner="human:ben", requester="human:ben", private=True)
    store, auth = api.app.state.store, api.app.state.auth
    cara, dan = Identity("human:cara", "human", "cara@acme.example"), Identity("human:dan", "human", "dan@acme.example")
    with store.transaction() as c:
        c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps(["human:cara"]), t["id"]))
    with store.read() as c:
        cursor = E.latest(c)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET readers=NULL WHERE id=?", (t["id"],))
    with store.read() as c:
        sent = [d for _, topic, d in E.read(c, auth, cara, cursor, topics=("tasks",), task_views=_task_views)[0]]
        assert [d["id"] for d in sent] == [t["id"]] and sent[0]["gone"] is True
        assert set(sent[0]) <= {"seq", "actor", "at", "id", "kind", "kinds", "gone"}
        assert "salary" not in json.dumps(sent)
        assert E.read(c, auth, dan, cursor, topics=("tasks",), task_views=_task_views)[0] == []


def test_one_read_sends_its_byte_budget_and_the_rest_comes_on_the_next_read(api, monkeypatch):
    """A busy team's backlog is sent in bounded reads, each one's events in order, none lost or repeated."""
    auth, store = api.app.state.auth, api.app.state.store
    with store.read() as c:
        cursor = E.latest(c)
    made = [post(api, "tasks", {"owner": "ben", "title": f"Count item {n}", "body": "Please."})["id"] for n in range(3)]
    monkeypatch.setattr(E, "READ_BYTES", 1)  # every event is over budget, so each read sends exactly one
    who = Identity("human:ben", "human", "ben@acme.example")
    sent, more = [], True
    while more:
        with store.read() as c:
            events, cursor, reset, more = E.read(c, auth, who, cursor, topics=("tasks",), task_views=_task_views)
        assert not reset and len(events) <= 1
        sent += [data["id"] for _, _, data in events]
    assert [task_id for task_id in sent if task_id in made] == made
    assert len(sent) == len(set(sent))


def test_a_consumer_more_changes_behind_than_the_backlog_limit_is_reset(api, monkeypatch):
    auth, store = api.app.state.auth, api.app.state.store
    with store.read() as c:
        cursor = E.latest(c)
    for n in range(3):
        post(api, "tasks", {"owner": "ben", "title": f"Backlog item {n}", "body": "Please."})
    monkeypatch.setattr(E, "BACKLOG_MAX", 2)
    who = Identity("human:ben", "human", "ben@acme.example")
    with store.read() as c:
        events, newest, reset, more = E.read(c, auth, who, cursor, topics=("tasks",), task_views=_task_views)
    assert reset and events == [] and more is False and newest > cursor
    monkeypatch.setattr(E, "BACKLOG_MAX", 5000)
    with store.read() as c:
        _, _, reset, _ = E.read(c, auth, who, cursor, topics=("tasks",), task_views=_task_views)
    assert not reset


# A read prepares only what it can send (task I49): a viewer behind on large run outputs.
KiB = 1024
ANA = Identity("human:ana", "human", "ana@acme.example")


def _write_outputs(api, attempt_id, texts, first=1000):
    with api.app.state.store.transaction() as c:
        for n, text in enumerate(texts):
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,'delta',?,?)",
                (attempt_id, first + n, json.dumps({"delta_kind": "text", "text": text}), H.now()),
            )


def _big(label, size=64 * KiB):
    return label + "x" * (size - len(label))


def _drain(api, cursor, who=ANA, **kw):
    """Every read until nothing more waits, as a round reads (ROUND_ROWS changes at a time): ([events per read], cursor)."""
    store, auth, reads, more = api.app.state.store, api.app.state.auth, [], True
    while more:
        with store.read() as c:
            events, cursor, reset, more = E.read(c, auth, who, cursor, limit=E.ROUND_ROWS, **kw)
        assert not reset
        reads.append(events)
    return reads, cursor


def _texts(reads):
    return [d["output"]["payload"]["text"] for events in reads for _, _, d in events if "output" in d]


def test_a_read_builds_only_the_run_outputs_it_sends_and_decodes_each_once(api, monkeypatch):
    """A viewer behind on large outputs: each read checks and builds only what READ_BYTES carries, and each stored
    payload is decoded once for its privacy checks and its event, not again on every later read."""
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    texts = [_big(f"out-{n:02d}") for n in range(12)]
    _write_outputs(api, attempt["id"], texts)
    decoded, original = [], H._json

    def counted(value, default=None):
        if isinstance(value, str) and len(value) >= 32 * KiB:
            decoded.append(value[:40])
        return original(value, default)

    monkeypatch.setattr(H, "_json", counted)
    reads, _ = _drain(api, cursor, topics=("runs",))
    assert _texts(reads) == texts
    assert len(decoded) == len(texts), f"{len(decoded)} decodes for {len(texts)} outputs"
    for events in reads:
        assert len(events) <= 1 or sum(len(d.encoded) for _, _, d in events) <= E.READ_BYTES


def test_a_capped_drain_never_sends_an_unreadable_row_and_ends_past_every_row(api):
    """Rows this viewer may not read, among outputs that fill many reads: a private task, and an output naming it only
    past its first READ_BYTES. Neither is sent; the cursor ends past every row."""
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    _write_outputs(api, attempt["id"], [_big(f"before-{n}") for n in range(5)])
    secret = post(
        api, "tasks", {"owner": "ben", "title": "Salary bands", "body": "Private.", "private": True}, token="ben-test"
    )
    late_reference = _big("hidden-output", 300 * KiB) + f" see #/task/{secret['id']}"
    _write_outputs(api, attempt["id"], [late_reference, *[_big(f"after-{n}") for n in range(5)]], first=2000)
    reads, end = _drain(api, cursor, topics=("tasks", "runs"), task_views=_task_views)
    with api.app.state.store.read() as c:
        assert end == E.latest(c)
    sent = json.dumps([[d for _, _, d in events] for events in reads])
    assert secret["id"] not in sent and "hidden-output" not in sent
    assert [t[: t.index("x")] for t in _texts(reads)] == [f"before-{n}" for n in range(5)] + [
        f"after-{n}" for n in range(5)
    ]
    assert len(reads) > 3  # the backlog took many capped reads


def test_a_run_output_larger_than_a_read_goes_whole_as_its_first_event(api):
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    huge = _big("huge", 300 * KiB)
    _write_outputs(api, attempt["id"], [huge, "small one", "small two"])
    reads, _ = _drain(api, cursor, topics=("runs",))
    first = [d for _, _, d in reads[0] if "output" in d]
    assert first[0]["output"]["payload"]["text"] == huge
    assert _texts(reads) == [huge, "small one", "small two"]


def test_a_run_state_changing_inside_a_capped_backlog_ends_as_it_stands_and_never_goes_back(api):
    _, _, attempt = setup_attempt(api)
    aid = attempt["id"]
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    for n, state in enumerate(("running", "input", "running", "completed")):
        _write_outputs(api, aid, [_big(f"step-{n}-{k}") for k in range(3)], first=1000 + 10 * n)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET state=? WHERE id=?", (state, aid))
    reads, _ = _drain(api, cursor, topics=("runs",))
    events = [(seq, d) for events in reads for seq, _, d in events]
    assert [seq for seq, _ in events] == sorted({seq for seq, _ in events})
    states = [d["state"] for _, d in events if d.get("run_id") == aid and "state" in d and "output" not in d]
    assert states[-1] == "completed"
    assert len(_texts(reads)) == 12


def test_capped_reads_send_the_same_events_in_the_same_order_as_one_uncapped_read(api, monkeypatch):
    """Tasks, messages and large run outputs mixed: drained in capped reads or in one read, the viewer gets the same
    events with the same seqs and data. A summary that describes now (a run's state, a thread's activity, Needs you)
    is sent once per read as it stands, so capped reads may send earlier ones too; the last of each is the same."""
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    for n in range(3):
        post(api, "tasks", {"owner": "ben", "title": f"Mixed item {n}", "body": "Please."})
        _write_outputs(api, attempt["id"], [_big(f"mixed-{n}-{k}") for k in range(3)], first=1000 + 10 * n)
        post(api, "chat/ops", {"text": f"And one more thing, {n}"})
    kw = dict(topics=("tasks", "messages", "runs", "bots", "needs", "threads"), task_views=_task_views)

    def summary(topic, d):
        return topic in ("threads", "needs", "bots") or topic == "runs" and "output" not in d

    def split(reads):
        rows, last = [], {}
        for events in reads:
            for seq, topic, d in events:
                text = json.dumps(d, sort_keys=True, default=str)
                if summary(topic, d):
                    key = (topic, d.get("kind"), d.get("root_id") or d.get("run_id") or d.get("id"))
                    last[key] = json.loads(text) | {"seq": None, "at": None}
                else:
                    rows.append((seq, topic, text))
        return rows, last

    capped, capped_end = _drain(api, cursor, **kw)
    assert len(capped) > 2
    monkeypatch.setattr(E, "READ_BYTES", 1 << 30)
    monkeypatch.setattr(E, "ROUND_ROWS", 100_000)
    whole, whole_end = _drain(api, cursor, **kw)
    assert len(whole) == 1 and capped_end == whole_end
    assert split(capped) == split(whole)


def test_a_run_output_naming_a_meeting_inside_its_payload_is_sent_as_the_run_page_shows_it(api):
    """Decoding a run output once must not widen the meeting check: like the run page, it reads the stored row, whose
    payload text it does not look inside. A meeting named there leaves the live decision as it was."""
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,1000,'delta',?,?)",
            (
                attempt["id"],
                json.dumps({"delta_kind": "text", "text": "notes", "live_meeting": {"meeting_id": "elsewhere"}}),
                H.now(),
            ),
        )
    reads, _ = _drain(api, cursor, topics=("runs",))
    assert _texts(reads) == ["notes"]
