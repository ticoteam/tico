"""Catch up (spec 3.6, the thread helper): a bounded summary of what is new in a thread, or its subtree, for the person
who asks. The Assistant acts with that person's rights, and its reply lands in the person's own Assistant DM, so the
summary is theirs alone.

What is summarized: the messages the person may read (`threads.readable`, per message) that are newer than their read
point in their thread and came after they joined it and after the threads migration, from people and bots, minus their
own posts, the system notices (cards, follow-ups) and history written back (imports, replays). A sub-thread has its own
read point, or none yet, so joining the tree is the floor for all of it: what is old to the person is never new. The
newest messages that fit MAX_BYTES are kept, counted as the lines the model reads (each one clipped to LINE_CHARS, then
encoded), and anything older is named as omitted, not read. A person the reader may not see is named only to someone in
the same thread (`teammate_access.Known`). Anyone else's lines say UNNAMED, before the model reads them, since a summary
can't be cleaned afterwards.

The model call happens outside any transaction (reads, then the call, then the write), so a slow model never holds
the write lock. Before the reply is written, every message the summary read is checked again: one the person can no
longer read is a 409, and nothing is posted, because the summary may already carry its text. With no model available,
the reply is a short excerpt of the newest messages instead.
"""
import logging

from . import hubdb as H

LOG = logging.getLogger("tico.thread_catchup")
SCOPES = ("subtree", "thread")
MAX_MESSAGES = 40        # messages summarized at most
MAX_BYTES = 12000        # the transcript's size at most (the model's input stays small)
SCAN_LIMIT = 300         # rows read before the readability check, newest first
LINE_CHARS = 400         # one message's text in the transcript
SUMMARY_CHARS = 1200     # the reply's summary at most
EXCERPT_LINES = 5        # lines in the fallback excerpt
SYSTEM_REFS = ("card", "follow_up", "carries", "earlier_messages", "import", "replay")   # system notices and history: never summarized
UNNAMED = "Someone"      # the author of a line, when the reader may not be told who


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "..."


def _name(c, actor, names):
    if not actor:
        return UNNAMED
    if actor not in names:
        if H.is_human(actor):
            names[actor] = H.person_name(c, H.actor_id(actor)) or actor
        else:
            bot = H.bot(c, H.actor_id(actor))
            names[actor] = (bot or {}).get("display_name") or H.actor_id(actor)
    return names[actor]


def _read_from(c, viewer, thread):
    """Where this person's new messages start: (the last sequence number the threads migration carried over, when they
    joined this thread's tree). A person a thread never added, as on a company thread, has no join: "" passes all."""
    from . import threads
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (threads.MIGRATION_KEY,)).fetchone()
    carried = int((H._json(row[0], {}) or {}).get("seq") or 0) if row else 0
    root, names = thread.get("root_id") or thread["id"], viewer.member_names(c)
    joined = c.execute("SELECT MIN(created_at) FROM thread_members WHERE thread_id=? AND actor IN (%s)"
                       % ",".join("?" * len(names)), (root, *names)).fetchone()[0]
    if not joined:
        follow = c.execute("SELECT created_at FROM thread_followers WHERE thread_id=? AND actor=? AND reason='joined'",
                           (root, viewer.actor)).fetchone()
        joined = follow[0] if follow else ""
    return carried, joined


def transcript(c, viewer, thread, scope, me):
    """The messages to summarize, oldest first, from one snapshot: {"messages": [...], "left_out": n, "through": seq}.
    Each message carries its `line`, the text the model reads, and the budget counts those lines."""
    from . import threads
    if scope == "thread":
        where, args = "m.conversation_id=?", [thread["id"]]
    else:
        where, args = "cv.path LIKE ?", [thread["path"] + "%"]
    carried, joined = _read_from(c, viewer, thread)
    rows = c.execute(
        "SELECT m.id, m.conversation_id, m.seq, m.from_actor, m.body, m.created, m.refs_json FROM messages m "
        "JOIN conversations cv ON cv.id=m.conversation_id "
        "LEFT JOIN thread_followers f ON f.thread_id=m.conversation_id AND f.actor=? "
        f"WHERE {where} AND m.seq>MAX(COALESCE(f.last_read_seq, 0), ?) AND m.created>=? AND m.from_actor<>? "
        "AND m.kind IN ('say','ask','answer','notice') AND m.deleted_at IS NULL "
        "ORDER BY m.seq DESC LIMIT ?", [me, *args, carried, joined, me, SCAN_LIMIT]).fetchall()
    names, known = {}, viewer.known(c)
    kept, size, through, left_out = [], 0, 0, 0
    for row in rows:
        refs = H._json(row["refs_json"], {}) or {}
        if any(key in refs for key in SYSTEM_REFS) or not threads.readable(c, viewer, row["id"]):
            continue
        through = max(through, row["seq"] or 0)
        author = known.name(row["from_actor"], row["conversation_id"])
        line = f"{_name(c, author, names)}: {_clip(row['body'], LINE_CHARS)}"
        cost = len(line.encode("utf-8")) + 1
        if len(kept) >= MAX_MESSAGES or size + cost > MAX_BYTES:
            left_out += 1
            continue
        kept.append({**dict(row), "line": line})
        size += cost
    kept.reverse()
    return {"messages": kept, "left_out": left_out, "through": through}


def summarize(writer, title, lines):
    """The summary: the model's, when a model answers, else a short excerpt of the newest lines."""
    if writer and lines:
        system = ("You summarize what is new in one team thread for one of its members. Use only the messages you are "
                  "given, in plain sentences, at most six short bullets. Do not advise and do not invent. Answer as "
                  'JSON: {"summary": "..."}.')
        try:
            answer = writer(system, f"Thread: {title}\n\n" + "\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - a provider failure degrades to the excerpt; the catch-up still arrives
            LOG.warning("catch-up summary failed: %s", type(exc).__name__)
            answer = None
        text = answer.get("summary") if isinstance(answer, dict) else None
        if isinstance(text, str) and text.strip():
            return _clip(text, SUMMARY_CHARS)
    return "\n".join(f"- {line}" for line in lines[-EXCERPT_LINES:]) if lines else ""


def prepare(c, auth, who, thread_id, scope):
    """The thread's transcript for `who`, read in one snapshot. A thread the person cannot read is a 404."""
    from . import threads
    viewer = threads.viewer(auth, who)
    thread = dict(threads.require(c, viewer, thread_id))
    got = transcript(c, viewer, thread, scope, who.actor)
    return {"thread": thread, "scope": scope, **got, "lines": [m["line"] for m in got["messages"]]}


def post(c, auth, who, settings, plan, summary):
    """Reply in the person's Assistant DM. Its own permission check: the person must still read the thread and every
    message the summary read."""
    from . import assistant, threads
    from .store import Problem
    viewer = threads.viewer(auth, who)
    threads.require(c, viewer, plan["thread"]["id"])
    if any(not threads.readable(c, viewer, m["id"]) for m in plan["messages"]):
        raise Problem("changed", "Part of this thread changed while it was read. Catch up again.", 409)
    info = assistant.availability(c, settings, who)
    if not info["available"]:
        raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
    room = assistant.chat_room(c, settings, who)
    count = len(plan["messages"])
    title = plan["thread"]["subject"] or "Thread"
    body = f"Catch-up: {title}, {count} new\n\n{summary}"
    if plan["left_out"]:
        body += f"\n\n{plan['left_out']} older not included."
    sources = [m["id"] for m in plan["messages"]]
    refs = {"catch_up": {"thread_id": plan["thread"]["id"], "scope": plan["scope"], "through_seq": plan["through"],
                         "messages": count, "sources": sources}}
    posted = threads.post(c, H.bot_actor(info["bot"]), room["id"], body, kind="say", refs=refs, to_actor=who.actor,
                          via="assistant")
    H.event(c, who.actor, "thread.catch_up", plan["thread"]["id"],
            {"scope": plan["scope"], "messages": count, "room": room["id"]})
    return {"thread_id": plan["thread"]["id"], "scope": plan["scope"], "messages": count,
            "left_out": plan["left_out"], "posted": posted}


def install(app, store, auth, mutate):
    from fastapi import Request
    from pydantic import Field
    from .models import Contract
    from .store import Problem
    from . import updates

    class CatchUp(Contract):
        scope: str = Field(default="subtree", max_length=10)

    @app.post("/api/v2/threads/{thread_id}/catch-up")
    def catch_up(request: Request, thread_id: str, body: CatchUp):
        """A bounded summary of what is new in a thread (scope `subtree`: its sub-threads too), posted to the caller's
        Assistant DM. Nothing new is a reply with `posted` null and `left_out` 0; a reply with `posted` null and
        `left_out` above 0 means the new messages were too long for the budget, and nothing is written."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human") or not H.is_human(who.actor) or who.via:
            raise Problem("forbidden", "Catch up is for a person, in their own Assistant", 403)
        if body.scope not in SCOPES:
            raise Problem("scope", "Catch up covers the thread or its subtree", 422)
        settings = store.settings
        with store.read() as c:
            plan = prepare(c, auth, who, thread_id, body.scope)
            writer = updates.writer_for(c, settings, store)
        if not plan["messages"]:
            words = ("Nothing new since you last read it" if not plan["left_out"]
                     else f"{plan['left_out']} new messages are too long to summarize here")
            return {"thread_id": thread_id, "scope": body.scope, "messages": 0, "left_out": plan["left_out"],
                    "posted": None, "words": words}
        summary = summarize(writer, plan["thread"]["subject"] or "Thread", plan["lines"])
        return mutate(request, body, lambda c: post(c, auth, who, settings, plan, summary))
