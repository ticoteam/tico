"""Live-meeting provenance survives bot detachment and uses current access on every read."""
import hashlib
import json
from functools import lru_cache

from .store import H, Problem
from . import read_cache

ROOM_PREFIX = "live-meeting:"


def sources(c):
    if not getattr(c, "in_transaction", True):
        c.execute("BEGIN")
    cache = read_cache.cache(c)
    if cache is None:
        return Sources(c)
    key = ("live_meeting_sources",)
    if key not in cache:
        cache[key] = Sources(c)
    return cache[key]


def member(c, actor, meeting):
    if not meeting:
        return False
    if H.is_human(actor):
        return (meeting["visibility"] == "team" or actor == meeting["owner_actor"] or bool(c.execute(
            "SELECT 1 FROM live_meeting_access WHERE meeting_id=? AND actor=?", (meeting["id"], actor)).fetchone()))
    if H.is_bot(actor):
        # Only the connector can delegate private meeting access to a bot.
        return bool(c.execute("SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=? "
                              "AND (?='team' OR joined_by=?)",
                              (meeting["id"], H.actor_id(actor), meeting["visibility"], meeting["owner_actor"])).fetchone())
    return False


class Sources:
    """One read snapshot only; caches contain identifiers, never customer text."""
    def __init__(self, c):
        self.c = c
        self.messages = {}
        self.room = lru_cache(maxsize=4096)(self._room)
        self.conversation = lru_cache(maxsize=4096)(self._conversation)
        self.attempt = lru_cache(maxsize=4096)(self._attempt)
        self.primed = {}
        self.primed_rooms = {}

    def _room(self, cid):
        # The stored row as H.conversation reads it, so a page that read its rooms together does not read each again.
        row = H.conversation(self.c, cid)
        return (row and row["room_key"]) or ""

    def bound_conversation(self, cid):
        return self.room(cid).startswith(ROOM_PREFIX)

    def _conversation(self, cid):
        if cid in self.primed_rooms:
            return self.primed_rooms[cid]
        from . import provenance_clock
        return frozenset(provenance_clock.cached(
            self.c, ("meeting_conversation", cid), lambda: self._conversation_uncached(cid)))

    def _conversation_uncached(self, cid):
        room = self.room(cid)
        if room.startswith(ROOM_PREFIX):
            return frozenset((room[len(ROOM_PREFIX):],))
        # Existing rooms predate the durable room binding. Their original keeper input
        # remains provenance even when its message is deleted or the bot is detached.
        # The sender index scans every keeper notice in the company for each ordinary room, and the room index every
        # message of the room: the meeting-input index holds only the messages that name a meeting turn.
        rows = self.c.execute("SELECT m.refs FROM messages m INDEXED BY messages_live_meeting_input "
                              "JOIN live_meeting_turns t "
                              "ON t.id=json_extract(m.refs,'$.live_meeting.turn_id') "
                              "AND t.meeting_id=json_extract(m.refs,'$.live_meeting.meeting_id') "
                              "WHERE m.conversation_id=? AND json_extract(m.refs,'$.live_meeting.turn_id') IS NOT NULL "
                              "AND m.from_actor=? AND m.to_actor='bot:'||t.bot LIMIT 101",
                              (cid, H.KEEPER)).fetchall()
        return self._legacy_room([row[0] for row in rows])

    def _legacy_room(self, inputs):
        if len(inputs) > 100:
            raise Problem("privacy", "Meeting provenance is too deep to authorize", 403)
        ids = set()
        for refs in inputs:
            ids.update(self.references(H._json(refs, {}) or {}))
        return frozenset(ids)

    def prime_conversations(self, cids):
        """The meeting provenance of many rooms, as _conversation builds it one room at a time: the rooms read in one
        query per 500 and the legacy keeper inputs of the unbound ones in one more, each room capped as its own lookup
        caps it. A room past the cap is left out, so its own check reads it again and refuses as before."""
        from . import provenance_clock
        cids = sorted({cid for cid in cids if cid} - set(self.primed_rooms))
        H.prime_conversations(self.c, cids)
        for part in H.chunks([cid for cid in cids if not self.bound_conversation(cid)]):
            inputs = {cid: [] for cid in part}
            for cid, refs in self.c.execute(
                    "SELECT conversation_id,refs FROM (SELECT m.conversation_id,m.refs,"
                    "row_number() OVER (PARTITION BY m.conversation_id) AS n FROM messages m INDEXED BY messages_live_meeting_input "
                    "JOIN live_meeting_turns t ON t.id=json_extract(m.refs,'$.live_meeting.turn_id') "
                    "AND t.meeting_id=json_extract(m.refs,'$.live_meeting.meeting_id') "
                    "WHERE m.conversation_id IN (%s) AND json_extract(m.refs,'$.live_meeting.turn_id') IS NOT NULL "
                    "AND m.from_actor=? AND m.to_actor='bot:'||t.bot) WHERE n<=101"
                    % ",".join("?" * len(part)), (*part, H.KEEPER)):
                inputs[cid].append(refs)
            for cid, found in inputs.items():
                try:
                    ids = self._legacy_room(found)
                except Problem:
                    continue
                self.primed_rooms[cid] = frozenset(provenance_clock.cached(
                    self.c, ("meeting_conversation", cid), lambda ids=ids: ids))

    def references(self, value):
        if isinstance(value, str):
            if value[:1] in ("{", "["):
                return self.references(H._json(value, None))
            return set()
        if isinstance(value, (list, tuple)):
            return set().union(*(self.references(v) for v in value)) if value else set()
        if not isinstance(value, dict):
            return set()
        ids = set()
        live = value.get("live_meeting")
        if isinstance(live, dict) and isinstance(live.get("meeting_id"), str):
            ids.add(live["meeting_id"])
        retained = value.get("live_meetings")
        if isinstance(retained, list):
            ids.update(v for v in retained if isinstance(v, str))
        for item in value.values():
            if isinstance(item, (dict, list, tuple)):
                ids.update(self.references(item))
        return ids

    def message(self, message, *, include_run=True, seen=None):
        if not message:
            return set()
        message = dict(message)
        root = seen is None
        seen = set() if root else seen
        mid = message.get("id")
        if mid in seen:
            return set()
        if len(seen) >= 100:
            raise Problem("privacy", "Meeting provenance is too deep to authorize", 403)
        refs = message.get("refs") or H._json(message.get("refs"), {}) or {}
        if not isinstance(refs, dict):
            raise Problem("privacy", "Meeting provenance cannot be authorized", 403)
        cache_key = (mid, include_run, message.get("conversation_id"),
               hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).digest(), message.get("in_reply_to"))
        cached = self.messages.get(cache_key)
        # Only complete root traversals are reusable. A nested traversal may have
        # omitted an ancestor already visited by its caller, so never publish it.
        if cached is not None:
            # Complete closures may share ancestors; count their distinct visited ids.
            if len(seen | cached[1]) > 100:
                raise Problem("privacy", "Meeting provenance is too deep to authorize", 403)
            seen.update(cached[1])
            return set(cached[0])
        seen.add(mid)
        ids = self.references(refs)
        if message.get("conversation_id"):
            ids.update(self.conversation(message["conversation_id"]))
        parents = [message.get("in_reply_to")]
        for key in ("answers", "inputs"):
            if isinstance(refs.get(key), list):
                parents.extend(v for v in refs[key] if isinstance(v, str))
        for parent in filter(None, parents):
            ids.update(self.message(H.message(self.c, parent, include_deleted=True), include_run=False, seen=seen))
        run = refs.get("run")
        aid = refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
        if aid and include_run:
            ids.update(self.attempt(aid))
        if root:
            if len(self.messages) >= 4096:
                self.messages.clear()
            self.messages[cache_key] = (frozenset(ids), frozenset(seen))
        return ids

    def _attempt(self, aid):
        if aid in self.primed:
            return self.primed[aid]
        from . import provenance_clock
        return frozenset(provenance_clock.cached(
            self.c, ("meeting_attempt", aid), lambda: self._attempt_uncached(aid)))

    def prime_attempts(self, inputs, moved):
        """The provenance of many runs from rows already read together (task_privacy.prime_attempts): `inputs`
        maps each run to its messages as stored, `moved` to its attempt.input.moved details. A run whose provenance
        cannot be authorized is left out, so its own check reads it again and refuses as before."""
        from . import provenance_clock
        for aid, rows in inputs.items():
            try:
                ids = self._attempt_from(rows, moved.get(aid, ()))
            except Problem:
                continue
            self.primed[aid] = frozenset(provenance_clock.cached(
                self.c, ("meeting_attempt", aid), lambda ids=ids: ids))

    def _attempt_uncached(self, aid):
        rows = self.c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                              "JOIN messages m ON m.id=j.message_id WHERE a.id=? UNION "
                              "SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                              "WHERE i.attempt_id=?", (aid, aid)).fetchall()
        moved = [row[0] for row in self.c.execute(
            "SELECT detail FROM events WHERE action='attempt.input.moved' AND target=?", (aid,))]
        # The walk below reads each input's parents and room one at a time: for a long run they are read together.
        moved_ids = [detail.get("message_id") for detail in (H._json(d, {}) or {} for d in moved)
                     if isinstance(detail, dict) and isinstance(detail.get("message_id"), str)]
        rows = list(rows)
        H.prime_message_rows(self.c, rows + list(H.prime_messages(self.c, moved_ids).values()))
        self.prime_conversations({row["conversation_id"] for row in rows})
        return self._attempt_from(rows, moved)

    def _attempt_from(self, rows, moved):
        ids = set()
        for row in rows:
            ids.update(self.message(row, include_run=False))
        for detail_json in moved:
            detail = H._json(detail_json, {}) or {}
            ids.update(self.references(detail))
            if detail.get("message_id"):
                ids.update(self.message(H.message(self.c, detail["message_id"], include_deleted=True), include_run=False))
        return frozenset(ids)

    def readable(self, actor, ids):
        principals = actor if isinstance(actor, tuple) else (actor,)
        return all(member(self.c, a, self.c.execute("SELECT * FROM live_meetings WHERE id=?", (rid,)).fetchone())
                   for rid in ids for a in principals)

    def conversation_readable(self, actor, cid):
        try:
            return self.readable(actor, self.conversation(cid))
        except Problem:
            return False

    def message_readable(self, actor, message):
        try:
            return self.readable(actor, self.message(message))
        except Problem:
            return False

    def attempt_readable(self, actor, aid):
        try:
            return self.readable(actor, self.attempt(aid))
        except Problem:
            return False


def require_conversation(c, actor, cid):
    index = sources(c)
    if not index.conversation_readable(actor, cid):
        raise Problem("not_found", "Conversation not found", 404)
    # Client-supplied message references can narrow access, never grant room membership.
    return index.bound_conversation(cid)


def private_room(c, room_key):
    """The meeting a conversation's room is, unless that meeting is open to the team, else None."""
    if not (room_key or "").startswith(ROOM_PREFIX):
        return None
    row = c.execute("SELECT id FROM live_meetings WHERE id=? AND visibility<>'team'",
                    (room_key[len(ROOM_PREFIX):],)).fetchone()
    return row["id"] if row else None

