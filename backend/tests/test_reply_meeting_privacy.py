"""A message that names a live meeting the reader cannot read, or that replies to one, is hidden on every read path.

Two shapes, both in a room the reader can read: a row whose refs name the meeting (refs.live_meeting), and a reply
to such a row at any depth. Four read paths must agree on each message of the chain: the SQL list (POST /api/v2/sql),
the conversation route, the thread timeline and the per-message check (GET /api/v2/messages/{id}). Each runs once
before and once after the settled-row index is swept, because a settled row skips the Python walk.
"""

import pytest

from backend import read_deps
from backend import task_privacy
from backend.store import H
from backend.tests.helpers.api import headers, post  # noqa: F401  (fixtures and helpers)
from backend.tests.helpers.sql import query, world  # noqa: F401  (fixture)


def private_meeting(api):
    """Ana's private meeting: Ben and Cara are not in it."""
    body = {"title": "Synthetic restricted planning", "visibility": "private"}
    return post(api, "live-meetings", body, token="ana-test")["id"]


def chain(api, room, refs_by_depth):
    """A reply chain in `room`: the root carries `refs_by_depth[0]`, each reply the next entry. Returns the ids root first."""
    store, ids = api.app.state.store, []
    with store.transaction() as c:
        for depth, refs in enumerate(refs_by_depth):
            ids.append(
                H.say(
                    c,
                    "human:ben",
                    "human:ana",
                    f"Synthetic meeting chain message {depth}",
                    conversation_id=room,
                    refs=refs,
                    in_reply_to=ids[-1] if ids else None,
                )["id"]
            )
    return ids


def visible_sql(api, ids, token):
    marks = ",".join("?" for _ in ids)
    rows = query(api, f"SELECT id FROM messages WHERE id IN ({marks})", token, params=ids)["rows"]
    return {row[0] for row in rows}


def visible_route(api, room, ids, token):
    r = api.get(f"/api/v2/conversations/{room}/messages", headers=headers(token))
    assert r.status_code == 200, r.text
    return {m["id"] for m in r.json()["messages"]} & set(ids)


def visible_timeline(api, room, ids, token):
    r = api.get(f"/api/v2/threads/{room}/timeline", headers=headers(token))
    assert r.status_code == 200, r.text
    return {m["id"] for m in r.json()["messages"] if not m.get("hidden")} & set(ids)


def visible_page(api, room, ids, token):
    """The page function itself, as the conversation route, the task page and the chat callers read it."""
    store = api.app.state.store
    who = store.settings.test_identities[token]
    with store.read() as c:
        return {m["id"] for m in task_privacy.page(c, who, room)["messages"]} & set(ids)


def visible_task_page(api, tid, ids, token):
    r = api.get(f"/api/v2/tasks/{tid}", headers=headers(token))
    assert r.status_code == 200, r.text
    return {m["id"] for m in r.json().get("messages", [])} & set(ids)


def visible_message(api, ids, token):
    return {mid for mid in ids if api.get(f"/api/v2/messages/{mid}", headers=headers(token)).status_code == 200}


def visible_everywhere(api, room, ids, token, tid=None):
    """The read paths' answers, which must agree: the SQL list, the conversation route, the thread timeline, the
    per-message check, the page function and, for a task's room, the task page."""
    answers = {
        "sql": visible_sql(api, ids, token),
        "route": visible_route(api, room, ids, token),
        "timeline": visible_timeline(api, room, ids, token),
        "message": visible_message(api, ids, token),
        "page": visible_page(api, room, ids, token),
    }
    if tid is not None:
        answers["task"] = visible_task_page(api, tid, ids, token)
    mismatched = {key: sorted(answer) for key, answer in answers.items() if answer != answers["sql"]}
    assert not mismatched, (sorted(answers["sql"]), mismatched)
    return answers["sql"]


@pytest.mark.parametrize("settled", [False, True])
def test_meeting_row_and_its_replies_hide_from_a_reader_who_cannot_read_the_meeting(api, world, settled):
    mid = private_meeting(api)
    room = world["coo_task"]["conversation_id"]
    meeting = {"live_meeting": {"meeting_id": mid}}
    # A row in the room that names the meeting, and replies to it at depth 1, 2 and 4 (beyond the SQL levels).
    named = chain(api, room, [meeting, None])
    deep = chain(api, room, [meeting, None, None, None, None])
    # The meeting is named only at the root: its replies carry no refs of their own.
    ancestor = chain(api, room, [meeting, None, None])
    # The room is readable by the reader and holds no meeting: every row stays visible.
    plain = chain(api, room, [None, None, None])
    # A meeting open to the team is read by Cara too: its rows and replies stay visible.
    team = post(api, "live-meetings", {"title": "Synthetic team planning"}, token="ana-test")["id"]
    open_named = chain(api, room, [{"live_meeting": {"meeting_id": team}}, None])
    if settled:
        with api.app.state.store.read() as c:
            read_deps.sweep(c)

    # Cara can read the room but not the meeting. Every row that names the meeting or replies to one is hidden on
    # every path; the plain chain is unchanged.
    tid = world["coo_task"]["id"]
    assert visible_everywhere(api, room, named, "cara-test", tid) == set()
    assert visible_everywhere(api, room, deep, "cara-test", tid) == set()
    assert visible_everywhere(api, room, ancestor, "cara-test", tid) == set()
    assert visible_everywhere(api, room, plain, "cara-test", tid) == set(plain)
    # Ana owns the meeting: she reads all of it.
    assert visible_everywhere(api, room, named, "ana-test", tid) == set(named)
    assert visible_everywhere(api, room, deep, "ana-test", tid) == set(deep)
    assert visible_everywhere(api, room, plain, "ana-test", tid) == set(plain)
    assert visible_everywhere(api, room, open_named, "cara-test", tid) == set(open_named)


def reply_to(api, room, parent):
    with api.app.state.store.transaction() as c:
        return H.say(
            c,
            "human:ben",
            "human:ana",
            "Synthetic reply to a meeting message",
            conversation_id=room,
            refs=None,
            in_reply_to=parent,
        )["id"]
