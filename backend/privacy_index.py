"""Request-local provenance for SQL and fleet reads; never cached across requests.

Unrelated historical blobs are not loaded. Small source maps are loaded only when a
query touches that kind of content, and recursive lookups have bounded LRU caches.
"""
from collections import defaultdict
from functools import cached_property, lru_cache
import re

from .store import H, Problem
from . import row_clock
from . import task_privacy as privacy
from .live_privacy import ROOM_PREFIX, Sources as MeetingSources


TASK_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TASK_IDS = re.compile("(?=(" + TASK_ID.pattern + "))")


class ReadIndex:
    def __init__(self, c, who, rooms=False):
        privacy.snapshot(c)
        self.c, self.who = c, who
        self.principals = privacy.actor(who)
        self.meetings = MeetingSources(c)
        # Read private rows by their partial index. Ordinary task identifiers and
        # room references load lazily, without task bodies or room participants.
        self.private = {r["id"]: dict(r) for r in row_clock.parties(c)}
        # A private task's answer depends only on its parties, guest and private flag, and many tasks share them
        # (one bot owns most). Ask once per distinct set rather than once per task.
        answers = {}
        self.denied = set()
        for tid, row in self.private.items():
            key = (row["owner_id"], row["requester_id"], row["guest"], row["private"])
            if key not in answers:
                answers[key] = privacy.task_readable(c, who, row)
            if not answers[key]:
                self.denied.add(tid)
        self._attempts = self._attachments = self._tags = self._intervals = None
        # Each instance belongs to one consistent read transaction.
        self.message_sources = lru_cache(maxsize=4096)(self._message_sources)
        self.target_message = lru_cache(maxsize=4096)(lambda mid: H.message(c, mid, include_deleted=True))
        self.attempt_sources = lru_cache(maxsize=4096)(self._attempt_sources)
        self.attempt = lru_cache(maxsize=4096)(self.attempt)
        self.plain_room = lru_cache(maxsize=16384)(self._plain_room)
        # The SQL conversation predicate and message provenance share a decision.
        # Otherwise a count revisits the same room for every row and thrashes the
        # smaller neutral meeting-source cache. This cache dies with the snapshot.
        self.conversation = lru_cache(maxsize=16384)(self.conversation)
        self.direct_message = lru_cache(maxsize=16384)(self._direct_message)
        self.direct_parent = lru_cache(maxsize=4096)(self._direct_parent)
        self.string_sources = lru_cache(maxsize=16384)(self._string_sources)
        # Unescaped JSON cannot name a denied task without its literal id.
        # Noncanonical ids keep the full structured check rather than compiling
        # an alternation that grows with the private-task inventory.
        self.uuid_denied = all(TASK_ID.fullmatch(tid) for tid in self.denied)
        # A caller that checks every room (SQL) finds the few rooms with meeting provenance once. Every other
        # room's meeting set is empty, which reads as allowed, so it needs no lookup per room.
        self.bound_rooms = self.meeting_rooms = None
        if rooms:
            self.bound_rooms = {row[0] for row in c.execute(
                "SELECT id FROM conversations WHERE substr(room_key,1,?)=?", (len(ROOM_PREFIX), ROOM_PREFIX))}
            self.meeting_rooms = set(self.bound_rooms)
            if c.execute("SELECT 1 FROM live_meeting_turns LIMIT 1").fetchone():
                # The legacy keeper inputs live_privacy.Sources reads room by room, for every room at once.
                self.meeting_rooms.update(row[0] for row in c.execute(
                    "SELECT DISTINCT m.conversation_id FROM messages m JOIN live_meeting_turns t "
                    "ON t.id=json_extract(m.refs,'$.live_meeting.turn_id') "
                    "AND t.meeting_id=json_extract(m.refs,'$.live_meeting.meeting_id') "
                    "WHERE m.sender_id=? AND m.recipient_id='bot:'||t.bot", (H.KEEPER,)))

    @cached_property
    def task_ids(self):
        # Provenance validates task existence, even when no private task is denied. Every SQL request and task list
        # used to read the whole tasks table here (SELECT id FROM tasks, 207/h on TIDY). The set is shared between
        # requests until a task is added or removed (backend/row_clock.py).
        return row_clock.refs(self.c)[0]

    @cached_property
    def conversation_tasks(self):
        # Message provenance needs only the task reference, never room content. Shared like task_ids: the whole
        # conversations read (156/h on TIDY, 76-121 ms each) ran once per privacy-checked request.
        return row_clock.refs(self.c)[1]

    def known_task(self, tid):
        return tid in self.task_ids

    def references(self, value):
        ids, pending = set(), [value]
        while pending:
            part = pending.pop()
            if isinstance(part, str):
                ids.update(self.string_sources(part) if len(part) <= 4096 else self._string_sources(part))
            elif isinstance(part, dict):
                pending.extend(part.values())
            elif isinstance(part, (list, tuple)):
                pending.extend(part)
        return ids

    def _string_sources(self, value):
        if value[:1] in ("{", "["):
            parsed = H._json(value, None)
            if isinstance(parsed, (dict, list)):
                return frozenset(self.references(parsed))
        direct = value[5:] if value.startswith("task:") else value
        ids = {direct} if direct in self.private else set()
        # Ordinary strings cannot contain URL task references. Retain both paths
        # without running a regex for every repeated progress label.
        if "#/task/" in value or "/tasks/" in value:
            ids.update(tid for tid in privacy.reference_strings(value) if tid in self.private)
        return frozenset(ids)

    def content(self, value):
        return ((not self.denied or not self.denied.intersection(self.references(value)))
                and self.meetings.readable(self.principals, self.meetings.references(value)))

    def conversation(self, cid):
        if self.meeting_rooms is not None and cid not in self.meeting_rooms:
            return True
        return self.meetings.conversation_readable(self.principals, cid)

    def meeting_room(self, cid):
        bound = cid in self.bound_rooms if self.bound_rooms is not None else self.meetings.bound_conversation(cid)
        return bound and self.conversation(cid)

    def unreadable_rooms(self):
        """Rooms whose plain messages a caller may not read: an unreadable meeting room, or a room of a denied or
        unknown task. Small, unlike the rooms themselves."""
        return {cid for cid in self.conversation_tasks if not self._plain_room(cid)}

    def attempts(self):
        if self._attempts is None:
            rows = {r["id"]: {**dict(r), "messages": [r["message_id"]], "tasks": set()} for r in self.c.execute(
                "SELECT a.id,a.bot_id,a.created_at,a.finished_at,a.lease_until,j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id")}
            for aid, mid in self.c.execute("SELECT attempt_id,message_id FROM attempt_inputs"):
                if aid in rows:
                    rows[aid]["messages"].append(mid)
            for tid, aid in self.c.execute("SELECT id,carried_by FROM tasks WHERE carried_by IS NOT NULL"):
                if aid in rows and tid in self.private:
                    rows[aid]["tasks"].add(tid)
            for aid, detail in self.c.execute("SELECT target,detail FROM events WHERE action IN (" + privacy.RUN_TASK_EVENTS_SQL + ")"):
                if aid in rows:
                    rows[aid]["tasks"].update(self.references(H._json(detail, {}) or {}))
            self._attempts = rows
        return self._attempts

    def _attempt_sources(self, aid):
        # A LIMIT or a time window must not load every historical run. Keep the
        # fleet/event interval map separate from these primary-key SQL reads.
        row = self.c.execute(
            "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (aid,)).fetchone()
        if not row:
            return set()
        ids = {r[0] for r in self.c.execute("SELECT id FROM tasks WHERE carried_by=?", (aid,)) if r[0] in self.private}
        for detail, in self.c.execute("SELECT detail FROM events WHERE action IN (" +
                                     privacy.RUN_TASK_EVENTS_SQL + ") AND target=?", (aid,)):
            ids.update(self.references(H._json(detail, {}) or {}))
        mids = [row["message_id"], *(r[0] for r in self.c.execute(
            "SELECT message_id FROM attempt_inputs WHERE attempt_id=?", (aid,)))]
        for mid in mids:
            ids.update(self.sources(self.target_message(mid), include_run=False))
        return frozenset(ids)

    def active_at(self, bot, ts):
        """Interval tree: logarithmic lookup plus the executions actually overlapping ts."""
        if self._intervals is None:
            groups = defaultdict(list)
            for row in self.attempts().values():
                groups[row["bot_id"]].append(row)
            def build(rows):
                if not rows:
                    return None
                middle = len(rows) // 2
                row = rows[middle]
                left, right = build(rows[:middle]), build(rows[middle + 1:])
                end = row["finished_at"] or row["lease_until"]
                return row, end, max(end, left[2] if left else "", right[2] if right else ""), left, right
            self._intervals = {slug: build(sorted(rows, key=lambda r: r["created_at"])) for slug, rows in groups.items()}
        def search(node):
            if not node or node[2] < ts:
                return
            row, end, _, left, right = node
            yield from search(left)
            if row["created_at"] <= ts:
                if ts <= end:
                    yield row["id"]
                yield from search(right)
        return search(self._intervals.get(bot))

    def attempt(self, aid):
        try:
            direct = self._direct_attempt(aid)
            if direct is not None:
                return direct
            return (self.meetings.attempt_readable(self.principals, aid)
                    and not self.denied.intersection(self.attempt_sources(aid)))
        except ValueError:
            return False

    def _direct_attempt(self, aid):
        # Task and meeting provenance use the same root/input rows. Authorize
        # simple roots together; inherited roots retain the existing traversals.
        rows = self.c.execute(
            "SELECT m.conversation_id,m.refs,m.in_reply_to FROM attempts a JOIN jobs j ON j.id=a.job_id "
            "JOIN messages m ON m.id=j.message_id WHERE a.id=? UNION "
            "SELECT m.conversation_id,m.refs,m.in_reply_to FROM attempt_inputs i "
            "JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=?", (aid, aid))
        found = False
        for row in rows:
            found = True
            if row["in_reply_to"]:
                return None
            direct = self._direct_message(row["conversation_id"], row["refs"], include_run=False)
            if direct is None:
                return None
            if not direct:
                return False
        if not found:
            return None
        if any(tid in self.denied for tid, in self.c.execute("SELECT id FROM tasks WHERE carried_by=?", (aid,))):
            return False
        for action, detail in self.c.execute("SELECT action,detail FROM events WHERE action IN (" +
                                            privacy.RUN_TASK_EVENTS_SQL + ") AND target=?", (aid,)):
            data = H._json(detail, {}) or {}
            if not isinstance(data, dict):
                return None
            if self.denied.intersection(self.references(data)):
                return False
            if action == privacy.INPUT_MOVED:
                try:
                    meetings = self.meetings.references(data)
                    if data.get("message_id"):
                        meetings.update(self.meetings.message(self.target_message(data["message_id"]), include_run=False))
                    if not self.meetings.readable(self.principals, meetings):
                        return False
                except Problem:
                    return False
        return True

    def sources(self, msg, *, include_run=True, seen=None):
        if not msg:
            return set()
        msg = dict(msg)
        seen = set() if seen is None else seen
        mid = msg.get("id")
        if mid in seen:
            return set()
        if len(seen) >= 100:
            raise ValueError("Unsupported provenance depth")
        seen.add(mid)
        refs = H._json(msg.get("refs"), {}) or {}
        if not isinstance(refs, dict):
            raise ValueError("Unsupported message provenance")
        ids = self.references(refs)
        conv = {"task_id": self.conversation_tasks.get(msg["conversation_id"])} if msg.get("conversation_id") else None
        # Explicit refs can narrow provenance, never override a room's task.
        for tid in (H._task_ref((conv or {}).get("task_id")), H.message_task_id({"refs": refs}, conv)):
            if tid and not self.known_task(tid):
                raise ValueError("Unknown task provenance")
            if tid in self.private:
                ids.add(tid)
        ancestors = [msg.get("in_reply_to")]
        for key in ("answers", "inputs"):
            if isinstance(refs.get(key), list):
                ancestors.extend(v for v in refs[key] if isinstance(v, str))
        for mid in filter(None, ancestors):
            ids.update(self.sources(self.target_message(mid), include_run=False, seen=seen))
        run = refs.get("run")
        run_id = refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
        if include_run and run_id:
            ids.update(self.attempt_sources(run_id))
        return ids

    def _message_sources(self, mid, cid, refs, reply):
        return self.sources({"id": mid, "conversation_id": cid, "refs": refs, "in_reply_to": reply})

    def message(self, mid, cid, refs, reply):
        if isinstance(refs, (dict, list)):
            refs = H._dump(refs)                # a message view holds refs decoded; the index reads stored text
        try:
            # Most history rows have no inherited provenance. Validate their
            # room once per request, retaining unknown/deleted task denial.
            if refs in (None, "", "{}") and not reply:
                return self.plain_room(cid)
            # Do not retain large arbitrary reference payloads as cache keys.
            direct = (self.direct_message(cid, refs) if len(refs or "") <= 4096
                      else self._direct_message(cid, refs))
            if direct is not None:
                if not direct or not reply or reply == mid:
                    return direct
                parent = self.direct_parent(reply)
                if parent is not None:
                    return parent
            return (self.meetings.message_readable(self.principals,
                    {"id": mid, "conversation_id": cid, "refs": refs, "in_reply_to": reply})
                    and not self.denied.intersection(self.message_sources(mid, cid, refs, reply)))
        except ValueError:
            return False

    def possible_content(self, refs):
        if isinstance(refs, (dict, list)):
            refs = H._dump(refs)
        if "\\" in refs or "live_meeting" in refs:
            return True
        if self.uuid_denied:
            # Canonical task ids use a fixed lexer and set lookup, avoiding an
            # alternation whose cost grows with the number of private tasks.
            if len(refs) <= 4096:
                return bool(self.denied.intersection(TASK_IDS.findall(refs)))
            return bool(self.denied) and any(match[1] in self.denied for match in TASK_IDS.finditer(refs))
        return True

    def _direct_message(self, cid, refs, include_run=True):
        """Authorize non-inherited refs without constructing two recursive closures.

        Inputs and answers still take the full existing traversal.
        A run reuses the existing keyed attempt traversal, including retained inputs.
        Direct refs keep both task and meeting checks, including unknown tasks.
        None means the recursive path must decide, rather than an approval.
        """
        data = H._json(refs, {}) or {}
        if not isinstance(data, dict):
            raise ValueError("Unsupported message provenance")
        run = data.get("run")
        aid = data.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
        if any(isinstance(data.get(key), list) and data[key] for key in ("answers", "inputs")):
            return None
        tid = H.message_task_id({"refs": data}, {"task_id": self.conversation_tasks.get(cid)})
        return (self.plain_room(cid) and (not tid or (self.known_task(tid) and tid not in self.denied))
                and (not self.possible_content(refs or "") or self.content(data))
                and (not include_run or not aid or self.attempt(aid)))

    def _direct_parent(self, mid):
        # A single non-inherited parent has at most two visited messages. Longer
        # chains keep the existing cycle/depth-aware traversal. Parent run refs
        # are not inherited (the recursive path uses include_run=False too).
        msg = self.target_message(mid)
        if not msg:
            return True
        if msg.get("in_reply_to"):
            return None
        return self._direct_message(msg.get("conversation_id"), msg.get("refs"), include_run=False)

    def _plain_room(self, cid):
        tid = H._task_ref(self.conversation_tasks.get(cid))
        return self.conversation(cid) and (not tid or (self.known_task(tid) and tid not in self.denied))

    def tags(self):
        if self._tags is None:
            rows = defaultdict(set)
            for tag, tid in self.c.execute("SELECT tag_id,task_id FROM task_tags"):
                rows[tag].add(tid)
            for target, detail in self.c.execute("SELECT target,detail FROM events INDEXED BY events_tag_attach "
                                                 "WHERE action='tag.attach'"):
                data = H._json(detail, {}) or {}
                # Tag history contains explicit task references, not arbitrary prose.
                rows[target].update(v for v in privacy.reference_strings(data.get("task_id") or data.get("task") or "") if self.known_task(v))
            self._tags = rows
        return self._tags

    def tag(self, tid, template):
        linked = self.tags().get(tid, set())
        return not linked or bool(template) or bool(linked - self.denied)

    def event(self, target, actor, ts, detail):
        if not self.content(H._json(detail, {}) or {}) or not self.content(target):
            return False
        if target in self.tags() and not self.tag(target, False):
            return False
        msg = self.target_message(target)
        if msg and (msg.get("deleted_at") or msg.get("deleted") or self.denied.intersection(self.sources(msg))
                    or not self.meetings.message_readable(self.principals, msg)):
            return False
        if not self.conversation(target):
            return False
        meeting = self.c.execute("SELECT * FROM live_meetings WHERE id=?", (target,)).fetchone()
        if meeting and not self.meetings.readable(self.principals, {target}):
            return False
        if target in self.attempts() and not self.attempt(target):
            return False
        if H.is_bot(actor):
            for aid in self.active_at(H.actor_id(actor), ts):
                if not self.attempt(aid):
                    return False
        return True

    def attachments(self):
        if self._attachments is None:
            rows = defaultdict(lambda: {"tasks": set(), "messages": set(), "attempts": set(), "parents": set()})
            for bid, tid in self.c.execute("SELECT blob_id,task_id FROM task_assets"):
                rows[bid]["tasks"].add(tid)
            for bid, mid in self.c.execute("SELECT blob_id,message_id FROM message_assets"):
                rows[bid]["messages"].add(mid)
            for r in self.c.execute("SELECT v.blob_id,v.poster_blob_id,v.thumb_blob_id,v.attempt_id,f.task_id,f.scope "
                                    "FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id"):
                for bid in filter(None, (r["blob_id"], r["poster_blob_id"], r["thumb_blob_id"])):
                    if r["task_id"]:
                        rows[bid]["tasks"].add(r["task_id"])
                    if r["scope"].startswith("task:"):
                        rows[bid]["tasks"].add(r["scope"][5:])
                    if r["attempt_id"]:
                        rows[bid]["attempts"].add(r["attempt_id"])
            for bid, poster, thumb in self.c.execute("SELECT blob_id,poster_blob_id,thumb_blob_id FROM blob_media"):
                for child in filter(None, (poster, thumb)):
                    rows[child]["parents"].add(bid)
            self._attachments = rows
        return self._attachments

    def blob(self, bid, seen=None):
        seen = set() if seen is None else seen
        if bid in seen:
            return True
        if len(seen) >= 100:
            return False
        seen.add(bid)
        row = self.attachments().get(bid)
        if not row:
            return True
        if self.denied.intersection(row["tasks"]):
            return False
        for mid in row["messages"]:
            msg = self.target_message(mid)
            if (not msg or msg.get("deleted_at") or msg.get("deleted") or self.denied.intersection(self.sources(msg))
                    or not self.meetings.message_readable(self.principals, msg)):
                return False
        return all(self.attempt(aid) for aid in row["attempts"]) and all(self.blob(parent, seen) for parent in row["parents"])
