# pyright: strict
"""What the meeting window (ui/app/call-window.ts, docs/meet.md "The meeting window") reads and asks of a call beyond
its view (backend/calls.py): the call's notes once it has ended, and ringing someone into it.

Notes: the summary and the action items (each with who took it on) the call posted in its thread (Calls.post_action_items,
backend/call_items.py), read from that one message, so the meeting's own rules decide who reads them, as they do the
message. `pending` while the call still owes them.

Ring: someone who already reads the call's thread (a member of any role, or anyone in a public thread) is rung into the
live call; nothing about their place in the thread changes. Someone who does not read it yet is added to it first, the
way the thread's own Add people does, and then rung.
"""

import json
import urllib.parse
from typing import Any, cast

from fastapi import Request
from pydantic import Field

from . import call_ring as _call_ring
from . import calls as _calls
from . import task_privacy as _privacy
from .api_contract import api_v2
from .models import Contract
from .store import H as _H
from .store import Problem as _Problem
from . import views as _views

# The store and the call modules are not strictly typed: they are used through Any here (as access_requests.py does).
H: Any = _H
Problem: Any = _Problem
C: Any = _calls
call_ring: Any = _call_ring
privacy: Any = _privacy
views: Any = _views
Row = dict[str, Any]


class RingPerson(Contract):
    actor: str = Field(min_length=1, max_length=200)


def _refs(row: Row) -> Row:
    raw: Any = row.get("refs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return cast(Row, raw) if isinstance(raw, dict) else {}


def _listed(body: Any) -> Row:
    """The items and summary written in an items message's body (one posted before notes were kept as data)."""
    items: list[str] = []
    summary: list[str] = []
    into: list[str] | None = None
    for line in str(body or "").splitlines()[1:]:
        if line.strip() == "Summary:":
            into = summary
        elif line.startswith("- "):
            (summary if into is summary else items).append(line[2:].strip())
    return {"summary": summary, "items": [{"title": title, "owner": ""} for title in items if title]}


def notes(c: Any, auth: Any, who: Any, call_id: str) -> Row:
    views.human_only(who)
    auth.domain(who)
    row = C._call(c, call_id)
    if not C.readable(c, who.actor, row["thread_id"]):
        raise Problem("not_found", "Call not found", 404)
    done = bool(row["action_items_posted_at"] or row["action_items_given_up_at"] or row["finalized_at"])
    out: Row = {"pending": row["state"] == "live" or not done, "message_id": None, "summary": [], "items": []}
    found = c.execute(
        "SELECT id FROM messages WHERE conversation_id=? AND created_at>=? AND deleted_at IS NULL "
        "AND body LIKE 'Action items:%' AND json_extract(refs,'$.call.id')=? ORDER BY created_at DESC LIMIT 1",
        (row["thread_id"], row["ended_at"] or row["started_at"], call_id),
    ).fetchone()
    message: Row | None = H.message(c, found[0]) if found else None
    if not message:
        return out
    try:
        privacy.require_message(c, who, message)
        auth.conversation(c, who, message["conversation_id"])
    except Problem:
        return {**out, "pending": False}
    call: Row = _refs(message).get("call") or {}
    kept: Any = call.get("notes")
    shown: Row = cast(Row, kept) if isinstance(kept, dict) else _listed(message.get("body"))
    listed: list[Any] = shown.get("items") or []
    items: list[Row] = []
    for raw in listed:
        item: Row = cast(Row, raw) if isinstance(raw, dict) else {}
        if item.get("title"):
            items.append({"title": str(item["title"]), "owner": str(item.get("owner") or "")})
    said: list[Any] = shown.get("summary") or []
    summary = [s for s in said if isinstance(s, str) and s]
    return {"pending": False, "message_id": message["id"], "summary": summary, "items": items}


def ring(c: Any, auth: Any, who: Any, call_id: str, actor: str) -> tuple[Row, Row]:
    """Ring `actor` into the live call: added to its rung list (the call view's `ring` for them) and to the thread first
    when they cannot read it yet. Answers {actor, added} and the payload the ring hooks take, or None for no hooks."""
    views.human_only(who)
    auth.domain(who)
    row = C._call(c, call_id)
    if not C.readable(c, who.actor, row["thread_id"]):
        raise Problem("not_found", "Call not found", 404)
    if row["state"] != "live":
        raise Problem("call_ended", "This call has ended", 409)
    target = str(H.resolve_actor(c, actor) or actor)
    if not str(target).startswith("human:"):
        raise Problem("not_a_person", "Only a person is rung; add a bot to the call instead", 422)
    added = False
    if not C.readable(c, target, row["thread_id"]):
        from . import threads as _threads

        threads: Any = _threads
        threads.add_member(c, threads.viewer(auth, who), row["thread_id"], target)
        added = True
    rung: list[str] = C._ring_list(row)
    if target not in rung:
        c.execute("UPDATE calls SET ring=?,updated_at=? WHERE id=?", (json.dumps([*rung, target]), H.now(), call_id))
    c.execute(
        "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) VALUES('threads',?,?,?,?,?)",
        ("call.rung", call_id, row["thread_id"], who.actor, H.now()),
    )
    payload: Row = {
        "title": H.actor_name(c, who.actor),
        "body": "# " + str(row["title"]),
        "url": f"/#/thread/{urllib.parse.quote(row['thread_id'])}?call={urllib.parse.quote(call_id)}",
        "tag": "call-" + call_id,
    }
    return {"actor": target, "added": added}, payload


def install_call_notes(app: Any, store: Any, auth: Any, write: Any) -> None:
    @app.get(
        "/api/v2/calls/{call_id}/notes",
        **api_v2(
            "Calls",
            "getCallNotes",
            "An ended call's summary and action items, each with who took it on; pending while they are still owed",
            "CallNotes",
        ),
    )
    def call_notes(request: Request, call_id: str) -> Row:  # pyright: ignore[reportUnusedFunction]
        with store.read() as c:
            return notes(c, auth, request.state.identity, call_id)

    @app.post(
        "/api/v2/calls/{call_id}/ring",
        **api_v2(
            "Calls",
            "ringCallPerson",
            "Ring a person into a live call: someone who reads its thread keeps their place in it; anyone else is added "
            "to the thread first",
            "CallRing",
        ),
    )
    def ring_person(request: Request, call_id: str, body: RingPerson) -> Row:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity
        rung: Row = {}

        def work(c: Any) -> Row:
            answer, rung["payload"] = ring(c, auth, who, call_id, body.actor)
            return answer

        result: Row = write(request, body, work)
        if rung.get("payload"):
            call_ring.ring(app, call_id, [result["actor"]], rung["payload"])
        return result
