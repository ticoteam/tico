"""Request-local provenance for SQL and fleet reads; never cached across requests.

Unrelated historical blobs are not loaded. Small source maps are loaded only when a
query touches that kind of content, and recursive lookups have bounded LRU caches.
"""
from collections import defaultdict
from functools import lru_cache

from .store import H
from . import task_privacy as privacy


class ReadIndex:
    def __init__(self, c, who):
        privacy.snapshot(c)
        self.c, self.who = c, who
        self.principals = privacy.actor(who)
        tasks = [dict(r) for r in c.execute("SELECT id,requester,owner,private FROM tasks")]
        self.known_tasks = {r["id"] for r in tasks}
        self.private = {r["id"]: r for r in tasks if r["private"] is None or r["private"]}
        self.denied = {tid for tid, row in self.private.items() if not privacy.task_readable(c, who, row)}
        self._attempts = self._attachments = self._tags = self._intervals = None
        # Each instance belongs to one consistent read transaction.
        self.message_sources = lru_cache(maxsize=4096)(self._message_sources)
        self.conversation = lru_cache(maxsize=4096)(lambda cid: H.conversation(c, cid))
        self.target_message = lru_cache(maxsize=4096)(lambda mid: H.message(c, mid, include_deleted=True))
        self.attempt_sources = lru_cache(maxsize=4096)(self._attempt_sources)

    def references(self, value):
        if isinstance(value, str):
            if value[:1] in ("{", "["):
                parsed = H._json(value, None)
                if isinstance(parsed, (dict, list)):
                    return self.references(parsed)
            return {tid for tid in privacy.reference_strings(value) if tid in self.private}
        if isinstance(value, dict):
            return set().union(*(self.references(v) for v in value.values())) if value else set()
        if isinstance(value, (list, tuple)):
            return set().union(*(self.references(v) for v in value)) if value else set()
        return set()

    def content(self, value):
        return not self.denied.intersection(self.references(value))

    def attempts(self):
        if self._attempts is None:
            rows = {r["id"]: {**dict(r), "messages": [r["message_id"]], "tasks": set()} for r in self.c.execute(
                "SELECT a.id,a.bot,a.created,a.finished,a.lease_until,j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id")}
            for aid, mid in self.c.execute("SELECT attempt_id,message_id FROM attempt_inputs"):
                if aid in rows:
                    rows[aid]["messages"].append(mid)
            for tid, aid in self.c.execute("SELECT id,carried_by FROM tasks WHERE carried_by IS NOT NULL"):
                if aid in rows and tid in self.private:
                    rows[aid]["tasks"].add(tid)
            for aid, detail in self.c.execute("SELECT target,detail_json FROM events WHERE action IN (" + privacy.RUN_TASK_EVENTS_SQL + ")"):
                if aid in rows:
                    rows[aid]["tasks"].update(self.references(H._json(detail, {}) or {}))
            self._attempts = rows
        return self._attempts

    def _attempt_sources(self, aid):
        row = self.attempts().get(aid)
        if not row:
            return set()
        ids = set(row["tasks"])
        for mid in row["messages"]:
            ids.update(self.sources(self.target_message(mid), include_run=False))
        return frozenset(ids)

    def active_at(self, bot, ts):
        """Interval tree: logarithmic lookup plus the executions actually overlapping ts."""
        if self._intervals is None:
            groups = defaultdict(list)
            for row in self.attempts().values():
                groups[row["bot"]].append(row)
            def build(rows):
                if not rows:
                    return None
                middle = len(rows) // 2
                row = rows[middle]
                left, right = build(rows[:middle]), build(rows[middle + 1:])
                end = row["finished"] or row["lease_until"]
                return row, end, max(end, left[2] if left else "", right[2] if right else ""), left, right
            self._intervals = {slug: build(sorted(rows, key=lambda r: r["created"])) for slug, rows in groups.items()}
        def search(node):
            if not node or node[2] < ts:
                return
            row, end, _, left, right = node
            yield from search(left)
            if row["created"] <= ts:
                if ts <= end:
                    yield row["id"]
                yield from search(right)
        return search(self._intervals.get(bot))

    def attempt(self, aid):
        try:
            return not self.denied.intersection(self.attempt_sources(aid))
        except ValueError:
            return False

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
        refs = msg.get("refs") or H._json(msg.get("refs_json"), {}) or {}
        if not isinstance(refs, dict):
            raise ValueError("Unsupported message provenance")
        ids = self.references(refs)
        conv = self.conversation(msg["conversation_id"]) if msg.get("conversation_id") else None
        tid = H.message_task_id(msg, conv)
        if tid and tid not in self.known_tasks:
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
        return self.sources({"id": mid, "conversation_id": cid, "refs_json": refs, "in_reply_to": reply})

    def message(self, mid, cid, refs, reply):
        try:
            return not self.denied.intersection(self.message_sources(mid, cid, refs, reply))
        except ValueError:
            return False

    def tags(self):
        if self._tags is None:
            rows = defaultdict(set)
            for tag, tid in self.c.execute("SELECT tag_id,task_id FROM task_tags"):
                rows[tag].add(tid)
            task_ids = self.known_tasks
            for target, detail in self.c.execute("SELECT target,detail_json FROM events WHERE action='tag.attach'"):
                data = H._json(detail, {}) or {}
                # Tag history contains explicit task references, not arbitrary prose.
                rows[target].update(v for v in privacy.reference_strings(data.get("task_id") or data.get("task") or "") if v in task_ids)
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
        if msg and (msg.get("deleted_at") or msg.get("deleted") or self.denied.intersection(self.sources(msg))):
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
            if not msg or msg.get("deleted_at") or msg.get("deleted") or self.denied.intersection(self.sources(msg)):
                return False
        return all(self.attempt(aid) for aid in row["attempts"]) and all(self.blob(parent, seen) for parent in row["parents"])
