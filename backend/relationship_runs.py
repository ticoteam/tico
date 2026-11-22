# pyright: strict
"""The shared relationship worker's queue and per-attempt read boundary."""

from dataclasses import dataclass
import re
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any, Callable, cast

from clients import sessions

from . import bot_access as _bot_access, bot_settings as _bot_settings, hubdb as _hubdb, read_cache
from .entities import visible
from .store import Problem

if TYPE_CHECKING:
    from .auth import Auth, Identity

# The queue, registry and access helpers use untyped database rows.
H: Any = _hubdb
bot_access: Any = _bot_access
bot_settings: Any = _bot_settings

BOT = _hubdb.RELATIONSHIP_WORKER
ACTOR = "bot:" + BOT
REF = "relationship_run"
WAKE_TASK_LIMIT = 25
ROW_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
FILE_ID = r"file-[0-9a-f]{24}"
READ = re.compile(
    rf"/api/v2/(?:tasks/{ROW_ID}|pages/{FILE_ID}|threads/{ROW_ID}(?:/timeline)?"
    rf"|files/{FILE_ID}(?:/versions(?:/[0-9]+)?)?)"
)


@dataclass(frozen=True)
class Scope:
    entity: str
    room: str
    source: str
    sponsor: str


def ensure_worker(c: Connection, owner_id: str) -> None:
    """Create the company's hidden bot once. Its owner assigns its computer."""
    if H.bot(c, BOT):
        return
    if H.resolve_bot(c, BOT):
        raise Problem("conflict", "The relationship worker's slug is already in use", 409)
    H.sync_registry(c, {BOT: {"display_name": "Relationship Worker"}}, {})
    access = bot_access.stored({level: bot_access.audience({}) for level in bot_access.LEVELS})
    c.execute(
        "INSERT INTO bot_config(bot,owner_id,access,created_by) VALUES(?,?,?,?)", (BOT, owner_id, access, H.TICO_ACTOR)
    )
    bot_settings.write(c, BOT, {"repo_access_mode": "own", "stateless": True, "fallback": {"off": True}})


def enqueue(c: Connection, auth: "Auth", who: "Identity", entity_id: str, prompt: str, *, thread_id: str = "") -> str:
    """Queue a server-owned wake. A source thread grants only its root and is checked again at read time."""
    from . import rules, threads as native_threads

    threads: Any = native_threads

    if who.role not in ("owner", "human") or not _live(c, entity_id) or not visible.visible(c, who.actor, entity_id):
        raise Problem("not_found", "Relationship not found", 404)
    source: dict[str, Any] = {}
    if thread_id:
        root = threads.root_of(c, auth.conversation(c, who, thread_id))
        if not root or root.get("entity_id") != entity_id or root.get("audience") != "entity" or root.get("deleted_at"):
            raise Problem("not_found", "Relationship thread not found", 404)
        source = root
    context = f"Relationship {entity_id}."
    if source:
        context += f" Source thread {source['id']}."
    ensure_worker(c, H.actor_id(auth.owner_identity(c).actor))
    room = H.open_conversation(c, H.TICO_ACTOR, [ACTOR], kind="notice", room_key=REF, allow_held=True, announce=False)
    return str(
        H._write_message(
            c,
            H.TICO_ACTOR,
            ACTOR,
            f"{prompt}\n\n{context} Return suggested memory or status changes in your answer for review.",
            room,
            "notice",
            {
                REF: {
                    "entity": entity_id,
                    "source": source.get("id", ""),
                    "sponsor": who.actor,
                }
            },
            None,
            None,
            decided=rules.Decision(rules.WAKE, REF),
        )["id"]
    )


def queued_scope(bot: str, message: dict[str, Any] | None, room: dict[str, Any] | None) -> Scope | None:
    if bot != BOT or not message or not room or room.get("room_key") != REF:
        return None
    if message.get("sender_id") != H.TICO_ACTOR or message.get("recipient_id") != ACTOR:
        return None
    refs: Any = message.get("refs")
    if not isinstance(refs, dict):
        return None
    ref: Any = cast(dict[str, Any], refs).get(REF)
    if not isinstance(ref, dict):
        return None
    stored = cast(dict[str, Any], ref)
    entity, source, sponsor = (stored.get(key) for key in ("entity", "source", "sponsor"))
    if not isinstance(entity, str) or not entity or not isinstance(sponsor, str) or not sponsor:
        return None
    if not isinstance(source, str):
        return None
    return Scope(entity, room["id"], source, sponsor)


def claimable(c: Connection, auth: "Auth", bound: Scope | None) -> bool:
    if not bound or not _live(c, bound.entity):
        return False
    try:
        sponsor = auth.identity_for_actor(c, bound.sponsor)
        if not visible.visible(c, sponsor.actor, bound.entity):
            return False
        if bound.source:
            root = auth.conversation(c, sponsor, bound.source)
            return source_viewer(c, auth, bound, root) is not None
    except Problem:
        return False
    return True


def _live(c: Connection, entity: str) -> bool:
    return c.execute("SELECT 1 FROM entities WHERE id=? AND merged_into_id IS NULL", (entity,)).fetchone() is not None


def scope(c: Connection, attempt: str) -> Scope | None:
    def load() -> Scope | None:
        row = c.execute(
            "SELECT a.lane,j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot_id=?",
            (attempt, BOT),
        ).fetchone()
        if row:
            msg = H.message(c, row["message_id"])
            if msg and (bound := queued_scope(BOT, msg, H.conversation(c, msg["conversation_id"]))):
                if row["lane"] == sessions.entity_lane(bound.entity) and _live(c, bound.entity):
                    return bound
        return None

    return read_cache.value(c, (REF, attempt), load, shared=True)


def gate(c: Connection, auth: "Auth", who: "Identity", path: str, method: str) -> None:
    """Use existing object routes, with explicit opt-in for routes that can carry relationship data."""
    read = method in ("GET", "HEAD") and READ.fullmatch(path)
    model_auth = method == "GET" and path == "/api/v2/credential-runtime"
    if (read or model_auth) and auth.relationship_scope(c, who):
        return
    raise Problem("forbidden", "This relationship run cannot use that route", 403)


def task_matches(c: Connection, bound: Scope | None, task: dict[str, Any]) -> bool:
    if not bound:
        return False
    if "entity_id" not in task:
        stored = H.task_untagged(c, task["id"])
        return bool(stored and stored.get("entity_id") == bound.entity)
    return task["entity_id"] == bound.entity


def task_sql(c: Connection, bound: Scope | None) -> str:
    if not bound or not c.execute("SELECT 1 FROM pragma_table_info('tasks') WHERE name='entity_id'").fetchone():
        return "0"
    return "entity_id=" + bot_access.q(bound.entity)


def page_matches(c: Connection, bound: Scope | None, page: dict[str, Any]) -> bool:
    if not bound or page["conversation_id"]:
        return False
    if task := page["task_id"]:
        return task_matches(c, bound, H.task_untagged(c, task) or {"id": task})
    return (
        c.execute(
            "SELECT 1 FROM page_links WHERE page_id=? AND target=? AND role='memory'",
            (page["id"], sessions.entity_lane(bound.entity)),
        ).fetchone()
        is not None
    )


def source_viewer(c: Connection, auth: "Auth", bound: Scope, root: dict[str, Any]) -> "Identity | None":
    if (
        root["id"] != bound.source
        or root.get("entity_id") != bound.entity
        or root.get("audience") != "entity"
        or (root.get("root_id") or root["id"]) != root["id"]
    ):
        return None
    try:
        return auth.identity_for_actor(c, bound.sponsor)
    except Problem:
        return None


def wake_context(c: Connection, auth: "Auth", who: "Identity", bound: Scope) -> str:
    """Project current access into the claimed wake so it can open its records by id."""
    from . import files

    page = c.execute(
        "SELECT f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id "
        "WHERE l.target=? AND l.role='memory' AND l.target LIKE 'entity:%' AND f.state<>'deleted'",
        (sessions.entity_lane(bound.entity),),
    ).fetchone()
    context: list[str] = []
    file_visible = cast(Callable[..., bool], files.file_visible)
    if page and file_visible(c, auth, who, page, {}):
        context.append(f"Memory page {page['id']}.")

    ids: list[str] = []
    omitted = 0
    task_row = cast(Callable[..., object], auth.task_row)
    for row in c.execute(
        "SELECT * FROM tasks WHERE entity_id=? AND status='open' ORDER BY created_at DESC,id DESC",
        (bound.entity,),
    ):
        try:
            task_row(c, who, dict(row))
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            continue
        if len(ids) < WAKE_TASK_LIMIT:
            ids.append(str(row["id"]))
        else:
            omitted += 1
    if ids:
        context.append("Open tasks " + ", ".join(ids) + (f" ({omitted} more)." if omitted else "."))
    return " ".join(context)
