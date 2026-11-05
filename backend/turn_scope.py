"""Task turns read only context their task's readers may share. Decisions last one snapshot.

Make-page reads are collected per request and committed before its response leaves the server.
Publishing can recheck that log in its own transaction, without trusting the runner's citations.
"""
from contextvars import ContextVar
from dataclasses import dataclass, replace
import re

from . import read_cache, task_privacy as privacy, task_rights
from .auth import Identity
from .store import H, Problem


READS = ContextVar("attempt_reads", default=None)
AUTH = ContextVar("turn_scope_auth", default=None)


@dataclass(frozen=True)
class Scope:
    attempt_id: str
    tasks: frozenset[str]
    make_page: bool = False
    task: str = ""
    who: Identity | None = None


def scope(c, who):
    """None for an ordinary turn; a malformed make-page reference instead gets an empty, closed scope."""
    if not who.attempt_id:
        return None
    privacy.snapshot(c)

    def build():
        row = read_cache.one(c, "SELECT m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id "
                             "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (who.attempt_id,))
        refs = H._json(row[0], {}) if row else {}
        make = isinstance(refs, dict) and "make_page" in refs
        if make:
            target = refs["make_page"]
            task = target.get("task") if isinstance(target, dict) else None
            tasks = privacy.attempt_tasks(c, who.attempt_id)
            valid = isinstance(task, str) and task in tasks
            return Scope(who.attempt_id, frozenset(tasks) if valid else frozenset(), True, task if valid else "", who)
        if who.role == "bot" or who.task_actor and who.via != "assistant":
            tasks = privacy.attempt_tasks(c, who.attempt_id)
            if tasks:
                return Scope(who.attempt_id, frozenset(tasks), who=who)
        return None

    return read_cache.value(c, ("turn_scope", who), build, shared=True)


def project(c, scoped, payload, *, auth=None):
    """Project the reviewed task/source schemas; ordinary text and unknown references stay opaque."""
    from .scope_projection import project as project_sources
    return project_sources(c, scoped, payload, auth=auth)


def project_attempt(c, auth, aid, payload):
    """Runner credentials deliver inputs for a turn; the turn's effective identity fences those inputs."""
    attempt = read_cache.one(c, "SELECT * FROM attempts WHERE id=?", (aid,))
    if not attempt:
        raise Problem("not_found", "Execution not found", 404)
    who = auth.assistant_principal(c, attempt) or Identity("bot:" + attempt["bot_id"], "bot", attempt_id=aid)
    scoped = scope(c, who)
    if scoped and scoped.make_page and isinstance(payload, dict):
        # A claim saved before the boundary changed must not replay its private prompt layers.
        wire = payload.get("attempt")
        if isinstance(wire, dict) and wire.get("id") == aid:
            message = wire.get("message")
            payload = {**payload, "attempt": {**wire, "learned": None,
                       "history": [message] if message else [], "next_run": [], "notes": [],
                       "updates": None, "worktrees": []}}
        elif "messages" in payload:
            payload = {**payload, "messages": []}
    return project(c, scoped, payload, auth=auth)


def tasks(c, auth, who, scoped):
    """Full task checks include bot access, not just private task membership."""
    if not scoped.tasks:
        return ()
    try:
        return tuple(auth.task(c, who, tid) for tid in sorted(scoped.tasks))
    except Problem as exc:
        if exc.status not in (403, 404):
            raise
        return ()


def readers(c, auth, task):
    """Task parties, guests, participants and current managers; no permissions survive this read."""
    def build():
        explicit = {task["owner_id"], task["requester_id"], task.get("guest")}
        explicit.update(r[0] for r in c.execute("SELECT actor FROM task_roles WHERE task_id=? AND role='participant'",
                                              (task["id"],)))
        actors = explicit | {"human:" + r["id"] for r in H.humans(c)} | {"bot:" + r["id"] for r in H.bots(c)}
        out = []
        for actor in sorted(a for a in actors if a):
            if H.is_human(actor):
                try:
                    identity = auth.identity_for_actor(c, actor)
                except Problem:
                    continue
            elif H.is_bot(actor):
                identity = Identity(actor, "bot")
            else:
                continue
            if H.task_private(c, task):
                if actor not in explicit and not any(p in task_rights.private_subjects(c, actor)
                                                      for p in (task["owner_id"], task["requester_id"])):
                    continue
            else:
                try:
                    auth.task_row(c, identity, task)
                except Problem as exc:
                    if exc.status not in (403, 404):
                        raise
                    continue
            out.append(identity)
        return tuple(out)
    return read_cache.value(c, ("turn_scope_readers", task["id"]), build, shared=True)


def public_pin(c, fid):
    return bool(read_cache.one(c, "SELECT 1 FROM pins WHERE item=? AND made_by LIKE 'human:%' "
                                 "AND place IN ('dashboard','growth') LIMIT 1", ("page:" + fid,)))


def meeting_contained(c, row):
    from . import media
    return bool(row and row.get("review_state", "live") == "live" and row["metadata"].get("private") is False
                and media.shared_meeting(row, Identity("", "human"))
                and not read_cache.one(c, "SELECT 1 FROM media_control WHERE meeting_id=? AND deleted_at IS NOT NULL",
                                       (row["id"],)))


def file_contained(c, auth, row, task, who=None):
    from .files import VERSIONS_SQL, _file_visible
    if row["state"] == "deleted":
        return False
    audience = readers(c, auth, task)
    if not audience:
        return False
    if H.task_private(c, task):
        # A grant to this specific run (Counsel's document read) is one of the acting bot's own rights.
        # Other task readers still need their independent grants; no source check recurses into this scope.
        return all(_file_visible(c, auth, who if who and reader.actor == who.actor else reader, row, {})
                   for reader in audience)
    # A file filed on this task follows that task's own full access check, including its bot parties.
    if row["scope"] != "task:" + task["id"]:
        if row["scope"].startswith("conversation:"):
            return False
        filed = H.task(c, row["scope"][5:]) if row["scope"].startswith("task:") else None
        if ((filed and H.task_private(c, filed))
                or (row["task_id"] and H.task_private(c, H.task(c, row["task_id"])))):
            return False
        if not (public_pin(c, row["id"]) or filed and not H.task_private(c, filed)
                and all(H.is_human(filed[k]) for k in ("owner_id", "requester_id"))):
            return False
    for v in read_cache.rows(c, VERSIONS_SQL, (row["id"],)):
        for reader in audience:
            if not privacy.blob_readable(c, reader.actor, v["blob_id"]):
                return False
            if v["attempt_id"] and not privacy.attempt_readable(c, reader.actor, v["attempt_id"]):
                return False
    return True


def on_context(c, kind, ref, tid):
    if kind == "meeting":
        return bool(read_cache.one(c, "SELECT 1 FROM meeting_task_links WHERE task_id=? AND meeting_id=?", (tid, ref)))
    return bool(read_cache.one(c, "SELECT 1 FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.id=? "
                                 "AND p.archived=0 AND f.archived=0 AND (f.scope=? OR EXISTS "
                                 "(SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id))",
                              (ref, "task:" + tid, "task:" + tid)))


def allowed(c, auth, who, kind, row):
    scoped = scope(c, who)
    if scoped is None:
        return True
    carried = tasks(c, auth, who, scoped)
    if not carried:
        return False
    if scoped.make_page and not on_context(c, kind, row["id"], scoped.task):
        return False
    if scoped.make_page:
        from . import task_context
        context = read_cache.value(c, ("make_page_context", who, scoped.tasks), lambda: task_context.listing(
            c, auth, replace(who, attempt_id=""), scoped.task, carried=carried), shared=True)
        if row["id"] not in {item["id"] for item in context["meetings" if kind == "meeting" else "pages"]}:
            return False
    if kind == "meeting":
        return meeting_contained(c, row)
    return all(file_contained(c, auth, row, task, who) for task in carried)


def record(c, who, kind, ref):
    scoped, pending = scope(c, who), READS.get()
    if scoped and scoped.make_page and pending is not None:
        pending[(scoped.attempt_id, kind, ref)] = who


def private_context_allowed(c, who, *, message=None):
    """Make page has only its initiating message, never a person's conversations or learned layers."""
    aid = getattr(who, "attempt_id", "")
    if not aid:
        return True
    if isinstance(who, Identity):
        scoped = scope(c, who)
        if scoped is None or not scoped.make_page:
            return True
    row = read_cache.one(c, "SELECT m.id,m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id "
                           "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (aid,))
    refs = H._json(row["refs"], {}) if row else {}
    return not (isinstance(refs, dict) and "make_page" in refs) or bool(message and row["id"] == message)


def require_private_context(c, who):
    if not private_context_allowed(c, who):
        raise Problem("not_found", "Context not found", 404)


def require_read_route(c, who, method, path):
    """Private context is a 404 for Make page; scoped_routes decides the reviewed shared surfaces."""
    scoped = scope(c, who)
    if not scoped or not scoped.make_page or method not in ("GET", "HEAD"):
        return
    if (re.fullmatch(r"/api/(?:v2/)?(?:messages|conversations|threads|assistant|inbox|notes|learnings|memory)(?:/.*)?", path)
            or re.fullmatch(r"/api/v2/bots/[^/]+/memory(?:/.*)?", path)
            or re.fullmatch(r"/api/v2/tasks/[^/]+/(?:chat|comments|answers)", path)):
        raise Problem("not_found", "Context not found", 404)


def require_meeting(c, who, row, auth=None):
    if scope(c, who) is None:
        return
    auth = auth or AUTH.get()
    if auth is None or not allowed(c, auth, who, "meeting", row):
        raise Problem("not_found", "Meeting not found or unavailable", 404)


def postal_allowed(c, auth, who, bid):
    """A scan grant is an independent source right for every reader of every carried task."""
    from . import postal_mail
    if not read_cache.rows(c, postal_mail.SCANS_SQL, (bid,)):
        return None
    scoped = scope(c, who)
    if scoped is None:
        return None
    carried = tasks(c, auth, who, scoped)
    if scoped.make_page or not carried or postal_mail.scan_access(c, auth, who, bid) is not True:
        return False
    return all(postal_mail.scan_access(c, auth, who if reader.actor == who.actor else reader, bid) is True
               for task in carried for reader in readers(c, auth, task))


def blob_allowed(c, auth, who, bid):
    """None outside task turns; otherwise admit only an in-scope file, meeting or own task attachment."""
    scoped = scope(c, who)
    if scoped is None:
        return None
    carried = tasks(c, auth, who, scoped)
    if not carried:
        return False
    if not all(privacy.blob_readable(c, reader.actor, bid) for task in carried for reader in readers(c, auth, task)):
        return False
    scan = postal_allowed(c, auth, who, bid)
    if scan is not None:
        return scan
    from .files import file_visible
    for row in c.execute("SELECT DISTINCT f.* FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
                         "WHERE v.blob_id=? OR v.poster_blob_id=? OR v.thumb_blob_id=?", (bid, bid, bid)):
        if not row["archived"] and file_visible(c, auth, who, row, {}):
            return True
    from . import context_search
    for r in c.execute("SELECT meeting_id FROM media_assets WHERE blob_id=?", (bid,)):
        try:
            row = context_search.readable(c, who, r[0], auth=auth)
            record(c, who, "meeting", row["id"])
            return True
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
    if scoped.make_page:
        return False
    return all(c.execute("SELECT 1 FROM task_assets WHERE task_id=? AND blob_id=?", (t["id"], bid)).fetchone()
               and all(privacy.blob_readable(c, r.actor, bid) for r in readers(c, auth, t)) for t in carried)


def file_ref_allowed(c, auth, who, ref):
    """Saved refs lose only known rejected sources; unknown refs grant no byte access."""
    if not isinstance(ref, str):
        return True
    fid = ref.rsplit("@", 1)[0]
    row = read_cache.one(c, "SELECT * FROM bot_files WHERE id=?", (fid,))
    if row:
        from .files import file_visible
        return not row["archived"] and file_visible(c, auth, who, row, {})
    if not read_cache.one(c, "SELECT 1 FROM blobs WHERE id=?", (fid,)):
        return True
    return blob_allowed(c, auth, who, fid) is not False


def recheck_reads(c, auth, who):
    """Call under the publishing transaction; a source that moved outside the scope blocks publication."""
    from . import context_search
    from .files import file_visible
    # Validation returns no source content. Inserting the output can move an old source past the list's ten items.
    reset = READS.set(None)
    try:
        for kind, ref in c.execute("SELECT kind,ref FROM attempt_reads WHERE attempt_id=?", (who.attempt_id,)):
            try:
                if kind == "meeting":
                    context_search.readable(c, who, ref, auth=auth)
                    continue
                row = read_cache.one(c, "SELECT * FROM bot_files WHERE id=?", (ref,))
                if row and file_visible(c, auth, who, row, {}):
                    continue
            except Problem as exc:
                if exc.status not in (403, 404):
                    raise
            raise Problem("privacy", "A source is no longer in this task's context", 403)
    finally:
        READS.reset(reset)


def flush(store, auth, pending):
    if not pending:
        return
    def write(c):
        from . import meetings
        for (aid, kind, ref), who in pending.items():
            row = meetings.get(ref, c) if kind == "meeting" else read_cache.one(c, "SELECT * FROM bot_files WHERE id=?", (ref,))
            if not row or not allowed(c, auth, who, kind, row):
                raise Problem("privacy", "A source is no longer in this task's context", 404)
            c.execute("INSERT OR IGNORE INTO attempt_reads(attempt_id,kind,ref,created_at) VALUES(?,?,?,?)",
                      (aid, kind, ref, H.now()))
        return {}
    try:
        store.mutate(next(iter(pending.values())), "attempt.reads", H.new_id(), {"reads": list(pending)}, write)
    except Exception as exc:
        raise Problem("read_log", "The source read could not be recorded", 503, True) from exc
