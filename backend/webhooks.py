"""Outgoing webhooks: Tico tells an address a person chose when something happens.

An endpoint belongs to a person (`created_by`), who sees through it what they could see in Tico: `publish` builds one
message per distinct owner, and a task they cannot read produces nothing for them. A bot cannot make an endpoint. It
asks, a person approves, and the person owns what is made (`requested_by` names the bot). The Strategy Bot sends
nothing.

`publish` runs in the caller's write transaction and only inserts: one `webhook_events` row for the event and one
`webhook_sends` row for each endpoint that should hear of it, so a rolled-back change tells nobody. The workers in
backend/webhook_delivery.py post them after the commit. A producer calls it with one line and it never raises on that
path: a failure is rolled back to a savepoint, logged, and the producer's own write goes on."""
import base64
import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import timezone
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import Field

from . import destinations, egress
from . import hubdb as H
from . import models as M
from . import webhook_delivery as D
from .models import Contract
from .store import Problem
from .webhooks_schema import SCHEMA  # noqa: F401  (hubdb.MIGRATIONS applies it)

log = logging.getLogger(__name__)

MAX_PER_EVENT = 20              # endpoints one event reaches; the rest are skipped and Health says so
MAX_PER_PERSON = 50
MAX_DATA = 64 * 1024
LISTED = 100
MAX_LIST_BYTES = 256 * 1024
LOG_LISTED = 100
GRACE = D.GRACE_HOURS
CUSTOM = re.compile(r"custom\.[a-z0-9][a-z0-9_.-]{0,62}")
PATTERN = re.compile(r"\*|[a-z][a-z0-9_]*(\.[a-z0-9_]+)*\.\*|[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
FALLBACK_EXAMPLE = {"example": True, "message": "A test event from Tico for Acme"}
EXAMPLES_AT = "acme.example"

MAX_HEADERS = 10
MAX_HEADER_VALUE = 1000
MAX_FILTER = 50
REFUSED_HEADERS = ("host", "content-length", "content-type", "transfer-encoding", "connection")
HEADER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
# Why an endpoint is paused after its address changed and its secret header values were cleared: it stays paused until
# each one is typed again or removed (`update`), and sends nothing without them (webhook_delivery.deliver).
NEEDS_VALUE = "A secret header needs its value again: the address changed and its saved value was cleared"
REPLAY_BATCH = 500
DISABLE_DAYS = 5
DISABLE_SPAN_HOURS = 12

_state = {"auth": None, "base": "", "settings": None}


class Refused(Exception):
    """An event that cannot be sent. `reason` is plain words for whoever asked."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def use(auth, base="", settings=None):
    """The sign-in rules and the address app links start with, once the app is built."""
    _state["auth"], _state["base"], _state["settings"] = auth, (base or "").rstrip("/"), settings


# ------------------------------------------------------------------------------------------------- names and shapes
def _catalog():
    from . import event_catalog
    return event_catalog


def canonical(name):
    """The catalog's name for `name`, or None when it is not an event."""
    name = str(name or "").strip()
    catalog = _catalog()
    if catalog:
        return catalog.canonical(name)
    return name if CUSTOM.fullmatch(name) or PATTERN.fullmatch(name) and "*" not in name else None


def matches(pattern, name):
    return pattern == "*" or pattern == name or (pattern.endswith("*") and name.startswith(pattern[:-1]))


def subscribed(events, name):
    return any(matches(p, name) for p in events)


def _outbound(catalog, name):
    """Whether an endpoint can hear `name`: not an incoming webhook's own event, nor one only rules see."""
    event = catalog.EVENTS.get(name) if catalog else None
    return event.outbound if event else not name.startswith("webhook.")


def clean_events(events):
    """A subscription list: catalog names, `group.*` patterns or `*`, each once, aliases in their catalog spelling."""
    out = []
    catalog = _catalog()
    for item in events or []:
        item = str(item).strip()
        if not item or not PATTERN.fullmatch(item) and not CUSTOM.fullmatch(item):
            raise Problem("webhook", f"{item or 'An empty name'} is not an event name", 422)
        if "*" not in item:
            name = canonical(item)
            if not name:
                raise Problem("webhook", f"{item} is not an event Tico sends", 422)
            if not _outbound(catalog, name):
                raise Problem("webhook", f"{name} is not sent to webhooks: use a rule with forward", 422)
            item = name
        elif item != "*" and catalog and not item.startswith("custom.") and not any(
                matches(item, n) for n, e in catalog.EVENTS.items() if e.outbound):
            raise Problem("webhook", f"{item} matches no event Tico sends", 422)
        if item not in out:
            out.append(item)
    if not out:
        raise Problem("webhook", "Choose at least one event", 422)
    return out


def example_for(name):
    catalog = _catalog()
    event = catalog.EVENTS.get(canonical(name) or "") if catalog else None
    return dict(event.example) if event and event.example else dict(FALLBACK_EXAMPLE)


def version_of(name):
    catalog = _catalog()
    event = catalog.EVENTS.get(name) if catalog else None
    return event.version if event else 1


def links_for(type_, subject, data):
    """Where to look at the thing, in the app and over the API. Only what is known is given."""
    base, group = _state["base"], type_.split(".")[0]
    app = api = ""
    if group == "task":
        app, api = f"#/task/{subject}", f"/api/v2/tasks/{subject}"
    elif group == "approval" and data.get("task_id"):
        app, api = f"#/task/{data['task_id']}", f"/api/v2/tasks/{data['task_id']}"
    elif group == "bot":
        app, api = f"#/bot/{subject}", f"/api/v2/bots/{subject}"
    elif group in ("meeting", "strategy", "market", "health"):
        app = {"meeting": "#/meetings", "strategy": "#/strategy", "market": "#/market", "health": "#/health"}[group]
    out = {}
    if app:
        out["app"] = f"{base}/{app}" if base else app
    if api:
        out["api"] = f"{base}{api}"
    return out


def envelope(type_, subject, data, timestamp):
    return {"type": type_, "version": version_of(type_), "timestamp": timestamp, "data": data,
            "links": links_for(type_, subject, data)}


def body_of(env):
    # A delivered body names a run as the API does (backend/runs.py): its attempt_id keys read run_id.
    from .runs import public_json
    return public_json(json.dumps(env, separators=(",", ":"), ensure_ascii=False))


# ----------------------------------------------------------------------------------------------------- who may read
def _identity(c, actor, cache):
    if actor not in cache:
        try:
            cache[actor] = _state["auth"].identity_for_actor(c, actor)
        except (Problem, AttributeError):
            cache[actor] = None
    return cache[actor]


def _left(c, actor, cache):
    """Whether `actor` is a person who has left or lost sign-in (auth.identity_for_actor refuses them). Such a person's
    endpoint hears nothing: no public task, no bot, no company-wide event. Without auth (a bare database) nobody is."""
    return bool(_state.get("auth")) and str(actor).startswith("human:") and _identity(c, actor, cache) is None


def _admin(c, actor, cache):
    who = _identity(c, actor, cache)
    return bool(who and (who.role == "owner" or _state["auth"].bot_admin(who)))


def _task_readable(c, actor, task_id, cache):
    row = H.task(c, task_id) if task_id else None
    if not row:
        return False
    auth = _state["auth"]
    who = _identity(c, actor, cache) if auth else None
    if auth and who:
        try:
            auth.task_row(c, who, row)
            return True
        except Problem:
            return False
    return H.task_private_readable(c, actor, row)


def view_for(c, actor, type_, subject, data, cache):
    """`data` as this person may have it, or None when none of it is theirs. The event's own rules, one per group:
    a task by the task's privacy, an approval by who decides it, a Needs-you item by who it is for, a bot by who may
    read it; Health and inbound mail are for owners and admins; the rest is company-wide."""
    if _left(c, actor, cache):
        return None
    group = type_.split(".")[0]
    if group == "task":
        return data if _task_readable(c, actor, subject, cache) else None
    # Whatever the event is called, a subject that is a task is that task's to give: a rule or a person may send a
    # custom event about one.
    if H.task(c, subject) and not _task_readable(c, actor, subject, cache):
        return None
    if group == "approval":
        row = c.execute("SELECT decision_owner, task_id FROM approvals WHERE id=?", (subject,)).fetchone()
        if not row or not (actor == row["decision_owner"] or _admin(c, actor, cache)):
            return None
        return data if not row["task_id"] or _task_readable(c, actor, row["task_id"], cache) else None
    if group == "needs_you":
        return data if data.get("to") == actor else None
    if group == "bot":
        auth, who = _state["auth"], _identity(c, actor, cache)
        if auth and who:
            return data if auth.bot_access(c, who, subject)["read"] else None
        return data
    if group in ("health", "inbound", "webhook"):
        return data if _admin(c, actor, cache) or not _state["auth"] else None
    return data


# ------------------------------------------------------------------------------------------------------------ publish
def listening(c):
    """Whether any endpoint exists, on or paused: the one cheap query a producer pays when there is none. A paused
    endpoint still has the events kept, so "Send missed" can bring it up to date when it is turned on."""
    return c.execute("SELECT 1 FROM webhooks WHERE deleted_at IS NULL LIMIT 1").fetchone() is not None


def _is_strategy_bot(actor):
    from . import strategy_bot
    return actor == H.bot_actor(strategy_bot.SLUG)


def _refresh(c):
    if hasattr(c, "after_commit"):
        c.after_commit.append(D.ring)
    else:
        D.ring()


def _event_row(c, event_id, type_, subject, data, actor, skipped=0, only=""):
    payload = json.dumps(data, separators=(",", ":"), default=str)
    if len(payload) > MAX_DATA:
        payload = json.dumps({"truncated": True, "bytes": len(payload)})
    c.execute("INSERT OR IGNORE INTO webhook_events(id,type,subject,version,payload,actor,skipped,created_at,only_id) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (event_id, type_, str(subject), version_of(type_), payload, actor, skipped,
                                             H.now(), only))


def _queue(c, hook, event_id, type_, subject, data, at=None):
    """One send for one endpoint; 0 when that endpoint already has this event. `at` is the event's own time."""
    stamp = H.now()
    if len(json.dumps(data, default=str)) > MAX_DATA:
        data = {"truncated": True}
    body = body_of(envelope(type_, str(subject), data, at or stamp))
    return c.execute("INSERT OR IGNORE INTO webhook_sends(id,event_id,webhook_id,type,subject,body,state,attempts,"
                     "next_attempt_at,created_at) VALUES(?,?,?,?,?,?,'ready',0,?,?)",
                     ("wh_" + uuid.uuid4().hex[:24], event_id, hook["id"], type_, str(subject), body, stamp, stamp)).rowcount


def _event_id(type_, subject, revision):
    return f"{type_}:{subject}:{revision}" if revision is not None else "we_" + uuid.uuid4().hex[:20]


def publish(c, type, subject, data, actor, *, only=None, revision=None):
    """Queue `type` about `subject` for the endpoints that should hear of it; the number of sends queued.

    Inside the caller's write transaction, inserts only. Without `only` it never raises: the event is kept in the
    journal while any endpoint exists (paused ones too, for "Send missed"), and every enabled endpoint subscribed to
    `type` gets a message built through its owner's read view, at most MAX_PER_EVENT of them. With
    `only` (an endpoint id) it sends to that one and raises Refused when it cannot: the endpoint is missing, paused
    or deleted, the sender is the Strategy Bot or a bot whose send switch is off, or the endpoint's owner may not read
    the subject. `revision` makes the event id repeatable, so a retried write queues nothing twice."""
    if only is not None:
        return _publish_only(c, str(type), str(subject), data or {}, actor, only, revision)
    try:
        name = canonical(type)
        # One indexed query when nobody listens, which is nearly always.
        every = c.execute("SELECT * FROM webhooks WHERE deleted_at IS NULL ORDER BY created_at, id").fetchall()
        if not name or not every or _is_strategy_bot(actor):
            return 0
        hooks = [h for h in every if h["enabled"] and subscribed(json.loads(h["events"]), name)]
        c.execute("SAVEPOINT webhook_publish")
        try:
            queued = _fan_out(c, hooks, name, str(subject), data or {}, actor, revision)
            c.execute("RELEASE webhook_publish")
        except Exception:
            c.execute("ROLLBACK TO webhook_publish")
            c.execute("RELEASE webhook_publish")
            raise
        if queued:
            _refresh(c)
        return queued
    except Exception:
        log.warning("publish of %s failed; the change goes on without it", type, exc_info=True)
        return 0


def _fan_out(c, hooks, name, subject, data, actor, revision):
    event_id = _event_id(name, subject, revision)
    hooks = _filtered(c, hooks, name, subject, data, actor)
    # Who may read it is settled before the per-event cap, so endpoints that can not have it never use up the places.
    cache, views, eligible = {}, {}, []
    for hook in hooks:
        owner = hook["created_by"]
        if owner not in views:
            views[owner] = view_for(c, owner, name, subject, data, cache)
        if views[owner] is not None:
            eligible.append(hook)
    kept, skipped = eligible[:MAX_PER_EVENT], max(0, len(eligible) - MAX_PER_EVENT)
    _event_row(c, event_id, name, subject, data, actor, skipped)
    return sum(_queue(c, hook, event_id, name, subject, views[hook["created_by"]]) for hook in kept)


def _publish_only(c, type_, subject, data, actor, only, revision):
    hook = c.execute("SELECT * FROM webhooks WHERE id=?", (only,)).fetchone()
    if not hook or hook["deleted_at"]:
        raise Refused("That endpoint does not exist")
    if not hook["enabled"]:
        raise Refused("That endpoint is paused")
    if _is_strategy_bot(actor):
        raise Refused("The Strategy Bot does not send webhooks")
    if H.is_bot(actor):
        from . import mail_settings
        if not (mail_settings.stored(c, H.actor_id(actor)) or {}).get("outbound_send"):
            raise Refused("This bot may not send outside: its send switch is off")
    name = canonical(type_)
    if not name:
        raise Refused(f"{type_} is not an event Tico sends")
    _actor_may_use(c, actor, hook, name)
    if len(json.dumps(data, default=str)) > MAX_DATA:
        raise Refused("The data is over 64 KB")
    if view_for(c, hook["created_by"], name, subject, data, {}) is None:
        raise Refused("The person who owns that endpoint may not read this")
    event_id = _event_id(name, subject, revision)
    _event_row(c, event_id, name, subject, data, actor, only=only)
    queued = _queue(c, hook, event_id, name, subject, data)
    if queued:
        _refresh(c)
    return queued


# ----------------------------------------------------------------------------------------------------- endpoint filters
def _facts(c, name, subject, data, actor):
    """What an event is about, for an endpoint's filter: the bots in it (a task's owner and whoever acted), the task's
    owner (whose department counts) and the task's type. A key is missing when the event has no such thing, and a
    filter on a missing key lets the event through: a release note is not a department's."""
    data = data if isinstance(data, dict) else {}
    row = None
    for guess in (subject, (data.get("approval") or {}).get("task"), (data.get("item") or {}).get("task")):
        row = H.task(c, str(guess)) if guess and isinstance(guess, str) else None
        if row:
            break
    if row:
        who = {a for a in (row.get("owner_id"), actor) if a and H.is_bot(a)}
        return {"bots": who, "owner": row.get("owner_id"), "type": row.get("type_id")}
    if name.split(".")[0] == "bot":
        return {"bots": {H.bot_actor(subject)}}
    return {}


def passes(c, filt, facts):
    """Whether an event with these facts is inside an endpoint's filter. An empty list means all."""
    if not filt:
        return True
    if filt.get("bots") and "bots" in facts and not facts["bots"] & set(filt["bots"]):
        return False
    if filt.get("task_types") and "type" in facts and facts["type"] not in filt["task_types"]:
        return False
    if filt.get("departments") and "owner_id" in facts and _state.get("settings") is not None:
        from . import task_rules
        try:
            mine = task_rules.department_of(c, _state["settings"], facts["owner_id"])
        except Exception:
            mine = None
        if mine not in filt["departments"]:
            return False
    return True


def filter_of(hook):
    try:
        return json.loads(hook["filter"] or "{}")
    except (KeyError, IndexError, ValueError):
        return {}


def _filtered(c, hooks, name, subject, data, actor):
    facts = None
    out = []
    for hook in hooks:
        filt = filter_of(hook)
        if filt:
            facts = _facts(c, name, subject, data, actor) if facts is None else facts
            if not passes(c, filt, facts):
                continue
        out.append(hook)
    return out


class FilterIn(Contract):
    departments: list[str] = Field(default_factory=list, max_length=MAX_FILTER)
    bots: list[str] = Field(default_factory=list, max_length=MAX_FILTER)
    task_types: list[str] = Field(default_factory=list, max_length=MAX_FILTER)


def clean_filter(c, filt):
    """The stored form of a filter: only the lists that hold something, each name once, in the same spelling the
    events carry (bots as `bot:<id>`, task types by id). An unknown department or task type is refused."""
    if not filt:
        return {}
    out = {}
    if filt.departments:
        known = None
        if _state.get("settings") is not None:
            from . import task_rules
            known = {d["id"] for d in task_rules.departments(_state["settings"])}
        for item in filt.departments:
            if known is not None and item not in known:
                raise Problem("webhook", f"{item} is not a department", 422)
        out["departments"] = list(dict.fromkeys(filt.departments))
    if filt.bots:
        named = [b.removeprefix("bot:") for b in filt.bots if b.strip()]
        out["bots"] = list(dict.fromkeys(H.bot_actor((H.resolve_bot(c, b) or {}).get("id", b)) for b in named))
    if filt.task_types:
        ids = []
        for item in filt.task_types:
            found = H.type_get(c, item)
            if not found:
                raise Problem("webhook", f"No task type {item}", 422)
            if found["id"] not in ids:
                ids.append(found["id"])
        out["task_types"] = ids
    return out


# ------------------------------------------------------------------------------------------------- custom headers
class HeaderIn(Contract):
    name: str = Field(min_length=1, max_length=64)
    value: str | None = Field(default=None, max_length=MAX_HEADER_VALUE)
    secret: bool = False


def _context(hook_id, name):
    return f"{D.sealed_as(hook_id)}:header:{name}"


def clean_headers(c, vault, hook_id, items, kept=()):
    """The stored list for an endpoint's custom headers: lowercase names, a secret's value sealed. A secret sent
    without a value keeps the one it has. Refused: more than ten, a name Tico sets itself (`webhook-*`, host,
    content-length, content-type, transfer-encoding, connection), and a line break anywhere."""
    if len(items) > MAX_HEADERS:
        raise Problem("webhook", f"At most {MAX_HEADERS} custom headers", 422)
    before = {h["name"]: h for h in kept}
    out = []
    for item in items:
        name = item.name.strip().lower()
        if not HEADER_NAME.fullmatch(name):
            raise Problem("webhook", f"{item.name!r} is not a header name", 422)
        if name.startswith("webhook-") or name in REFUSED_HEADERS:
            raise Problem("webhook", f"Tico sets the {name} header itself", 422)
        if any(h["name"] == name for h in out):
            raise Problem("webhook", f"The {name} header is listed twice", 422)
        value = item.value
        if value is not None and any(ch in value for ch in "\r\n\x00"):
            raise Problem("webhook", f"The {name} header holds a line break", 422)
        if item.secret and value in (None, ""):
            old = before.get(name)
            if not old or not old["secret"]:
                raise Problem("webhook", f"Give the {name} header a value", 422)
            out.append(dict(old))
        elif value in (None, ""):
            raise Problem("webhook", f"Give the {name} header a value", 422)
        elif item.secret:
            out.append({"name": name, "secret": True, "value": vault.cipher.seal(c, _context(hook_id, name), value)})
        else:
            out.append({"name": name, "secret": False, "value": value})
    return out


def headers_of(hook):
    try:
        return json.loads(hook["headers"] or "[]")
    except (KeyError, IndexError, ValueError):
        return []


def open_headers(c, vault, hook_id, stored):
    """The headers to send: a secret's value opened. Name to value. A secret whose value was cleared has none to send
    (`needs_value`), so the send waits instead (webhook_delivery.deliver)."""
    return {h["name"]: (vault.cipher.unseal(c, _context(hook_id, h["name"]), h["value"]) if h["secret"] else h["value"])
            for h in stored if not h.get("needs")}


def needs_value(stored):
    """The secret headers whose value was cleared when the address changed: the endpoint sends nothing until each is
    typed again or removed."""
    return [h["name"] for h in stored if h.get("secret") and h.get("needs")]


def _actor_may_use(c, actor, hook, name):
    """The same test as may_send, keyed by actor string: a caller that reaches publish without the API (a rule, a
    producer) still cannot aim at someone else's endpoint. Bots were checked for their send switch by the caller."""
    if actor == H.KEEPER:
        return
    if H.is_bot(actor):
        if name and not subscribed(json.loads(hook["events"]), name):
            raise Refused("That endpoint does not listen for this event")
        return
    if not _state.get("auth"):
        return
    if hook["created_by"] != actor and not _admin(c, actor, {}):
        raise Refused("Only the person who made this endpoint, or an admin, may send to it")


def check_usable(c, actor, endpoint_id, name=None):
    """When a rule is saved: the endpoint it names exists and `actor` may send to it (and, for a bot, to `name`).
    Raises Problem with the reason in plain words."""
    hook = c.execute("SELECT * FROM webhooks WHERE id=? AND deleted_at IS NULL", (endpoint_id,)).fetchone()
    if not hook:
        raise Problem("webhook", f"No webhook endpoint {endpoint_id}: see hub webhook list", 422)
    try:
        _actor_may_use(c, actor, hook, name)
    except Refused as exc:
        raise Problem("webhook", exc.reason, 422) from exc


# ------------------------------------------------------------------------------------------------------ endpoint rights
def is_person(who):
    return who.role in ("owner", "human")


def is_admin(who):
    auth = _state["auth"]
    return who.role == "owner" or bool(auth and is_person(who) and auth.bot_admin(who))


def may_manage(who, hook=None):
    """People make endpoints and manage their own; owners and admins manage all. A bot manages none."""
    if not is_person(who):
        raise Problem("forbidden", "A person manages webhook endpoints; a bot asks and a person approves", 403)
    if hook is not None and hook["created_by"] != who.actor and not is_admin(who):
        raise Problem("forbidden", "Only the person who made this endpoint, or an admin, may change it", 403)


def may_send(c, who, hook, name):
    """Who may send a custom event to an endpoint: a person who manages it; a bot only with its send switch on, and
    only to an endpoint that listens for that name; never the Strategy Bot."""
    if is_person(who):
        may_manage(who, hook)
        return
    if who.role != "bot" or _is_strategy_bot(who.actor):
        raise Problem("forbidden", "Only people and bots with sending switched on send webhooks", 403)
    from . import mail_settings
    if not (mail_settings.stored(c, H.actor_id(who.actor)) or {}).get("outbound_send"):
        raise Problem("forbidden", "Sending is off for this bot; a person turns it on in its mail settings", 403)
    if not subscribed(json.loads(hook["events"]), name):
        raise Problem("forbidden", "That endpoint does not listen for this event", 403)


def _row(c, hook_id):
    row = c.execute("SELECT * FROM webhooks WHERE id=? AND deleted_at IS NULL", (hook_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Webhook endpoint not found", 404)
    return row


def _reach(c, who, hook_id):
    """The endpoint when this caller may see it: its owner, an admin, or a bot (which sees it without its address)."""
    row = _row(c, hook_id)
    if row["created_by"] != who.actor and not is_admin(who) and who.role != "bot":
        raise Problem("not_found", "Webhook endpoint not found", 404)
    return row


# --------------------------------------------------------------------------------------------------------- the shapes
def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/…"


def counts(c, ids):
    """Per endpoint over seven days: delivered, failed, and the sends still to go."""
    if not ids:
        return {}
    since = H.shift(H.now(), days=-7)
    marks = ",".join("?" * len(ids))
    out = {i: {"delivered": 0, "failed": 0, "pending": 0} for i in ids}
    for row in c.execute(f"SELECT webhook_id, state, count(*) n FROM webhook_sends WHERE webhook_id IN ({marks}) AND "
                         f"(state IN ('ready','sending') OR next_attempt_at>=?) GROUP BY webhook_id, state", (*ids, since)):
        key = {"delivered": "delivered", "failed": "failed", "ready": "pending", "sending": "pending"}.get(row["state"])
        if key:
            out[row["webhook_id"]][key] += row["n"]
    return out


def view(hook, who, tally=None):
    mine = is_person(who) and (hook["created_by"] == who.actor or is_admin(who))
    grace = hook["secret_prev_until"] if hook["secret_prev"] and (hook["secret_prev_until"] or "") > H.now() else None
    return {"id": hook["id"], "name": hook["name"], "url": hook["url"] if mine else _origin(hook["url"]),
            "host": hook["host"], "events": json.loads(hook["events"]), "enabled": bool(hook["enabled"]),
            "paused_reason": hook["paused_reason"], "rate_per_hour": hook["rate_per_hour"],
            "allow_http": bool(hook["allow_http"]), "created_by": hook["created_by"],
            "requested_by": hook["requested_by"] or None, "created_at": hook["created_at"], "updated_at": hook["updated_at"],
            "revision": hook["revision"], "old_secret_until": grace, "can_change": mine,
            "filter": {"departments": [], "bots": [], "task_types": [], **filter_of(hook)},
            "headers": [{"name": h["name"], "secret": h["secret"],
                         **(({"set": False, "needs_value": True} if h.get("needs") else {"set": True}) if h["secret"]
                            else {"value": h["value"]})}
                        for h in headers_of(hook)] if mine else [],
            "disabled_at": hook["disabled_at"], "disabled_reason": hook["disabled_reason"] or None,
            "last_7_days": tally or {"delivered": 0, "failed": 0, "pending": 0}}


def send_view(row):
    return {"id": row["id"], "webhook_id": row["webhook_id"], "event_id": row["event_id"], "type": row["type"],
            "subject": row["subject"], "state": row["state"], "attempts": row["attempts"],
            "last_status": row["last_status"], "last_error": row["last_error"],
            "next_attempt_at": row["next_attempt_at"] if row["state"] == "ready" else None, "created_at": row["created_at"],
            "delivered_at": row["delivered_at"]}


def listing(c, who, limit=LISTED, offset=0):
    """Endpoints the caller may see, newest last, paged and byte-capped. `more` says another page waits."""
    limit = max(1, min(int(limit or LISTED), LISTED))
    sql, args = "SELECT * FROM webhooks WHERE deleted_at IS NULL", []
    if who.role != "bot" and not is_admin(who):
        sql, args = sql + " AND created_by=?", [who.actor]
    rows = c.execute(sql + " ORDER BY created_at, id LIMIT ? OFFSET ?", (*args, limit + 1, offset)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    tally = counts(c, [r["id"] for r in rows])
    items, spent = [], 0
    for n, row in enumerate(rows):
        item = view(row, who, tally.get(row["id"]))
        spent += len(json.dumps(item))
        if items and spent > MAX_LIST_BYTES:
            more, rows = True, rows[:n]
            break
        items.append(item)
    out = {"items": items, "more": more}
    if more:
        out["next_offset"] = offset + len(items)
    return out


# ------------------------------------------------------------------------------------------------------ the operations
class WebhookCreate(Contract):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(default_factory=lambda: ["*"], max_length=60)
    rate_per_hour: int = Field(default=600, ge=1, le=100000)
    allow_http: bool = False
    filter: FilterIn | None = None
    headers: list[HeaderIn] = Field(default_factory=list, max_length=MAX_HEADERS)


class WebhookUpdate(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    events: list[str] | None = Field(default=None, max_length=60)
    rate_per_hour: int | None = Field(default=None, ge=1, le=100000)
    allow_http: bool | None = None
    filter: FilterIn | None = None
    headers: list[HeaderIn] | None = Field(default=None, max_length=MAX_HEADERS)
    expected_revision: int | None = None
    # When the address moves to another destination while secret headers keep their saved values: "keep" sends them
    # to the new address, "clear" removes them (named "needs value") and pauses the endpoint until they are typed again.
    credentials: str | None = Field(default=None, max_length=10)


class WebhookSend(Contract):
    type: str = Field(min_length=1, max_length=64)
    data: dict = Field(default_factory=dict)
    subject: str = Field(default="", max_length=200)


class WebhookExample(Contract):
    type: str = Field(default="", max_length=64)


class Pause(Contract):
    reason: str = Field(default="", max_length=200)


class WebhookSince(Contract):
    since: str = Field(min_length=1, max_length=40)


class WebhookReplay(WebhookSince):
    after: str = Field(default="", max_length=200)      # with `since`: where the last round stopped (its next_since / next_after)


def new_secret():
    return D.PREFIX + base64.b64encode(secrets.token_bytes(32)).decode()


def check_address(url, allow_http):
    """The host of `url` when an endpoint may hold it. Plain http is for a receiver on this computer while it is
    built, and only with the endpoint's switch on."""
    try:
        parts = egress.check(url, allow_http=True)
    except egress.Blocked as exc:
        raise Problem("webhook", exc.reason, 422) from exc
    if parts.scheme == "http":
        if parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise Problem("webhook", "Plain http is allowed only to this computer (localhost)", 422)
        if not allow_http:
            raise Problem("webhook", "Plain http needs the owner's \"Allow plain http to this computer\" switch", 422)
    return parts.hostname.lower()


def owner_only_http(who, allow_http):
    if allow_http and who.role != "owner":
        raise Problem("forbidden", "Only the owner turns on plain http to this computer", 403)


def _spec(body):
    return {"name": body.name, "url": body.url, "events": clean_events(body.events), "rate_per_hour": body.rate_per_hour,
            "allow_http": body.allow_http, "filter": body.filter.model_dump() if body.filter else {}}


def _insert(c, vault, who, spec, owner, requested_by="", headers=()):
    host = check_address(spec["url"], spec["allow_http"])
    if c.execute("SELECT count(*) FROM webhooks WHERE created_by=? AND deleted_at IS NULL", (owner,)).fetchone()[0] >= MAX_PER_PERSON:
        raise Problem("webhook", f"One person has at most {MAX_PER_PERSON} webhook endpoints", 409)
    hook_id, secret, now = "ep_" + uuid.uuid4().hex[:12], new_secret(), H.now()
    filt = clean_filter(c, FilterIn(**spec["filter"]) if spec.get("filter") else None)
    stored = clean_headers(c, vault, hook_id, headers)
    c.execute("INSERT INTO webhooks(id,name,url,host,enabled,events,secret,rate_per_hour,allow_http,created_by,"
              "requested_by,created_at,updated_at,filter,headers) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)",
              (hook_id, spec["name"], spec["url"], host, json.dumps(spec["events"]),
               vault.cipher.seal(c, D.sealed_as(hook_id), secret), spec["rate_per_hour"], int(spec["allow_http"]),
               owner, requested_by, now, now, json.dumps(filt), json.dumps(stored)))
    H.event(c, who.actor, "webhook.created", hook_id, {"name": spec["name"], "host": host, "for": owner})
    return {**view(c.execute("SELECT * FROM webhooks WHERE id=?", (hook_id,)).fetchone(), who), "secret": secret}


def _approval_payload(spec):
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    return {"to": spec["url"], "cc": "", "subject": f"Create webhook endpoint {spec['name']}", "body_sha256": digest,
            "mailbox": "webhooks", "webhook": spec}


def create(c, vault, who, body):
    """A person's endpoint, with its secret in the answer (shown once). A bot's request files a `send` approval
    instead and the answer says so. Asking again with the same details after a person approved it makes the endpoint,
    owned by that person, and spends the approval."""
    spec = _spec(body)
    if is_person(who):
        owner_only_http(who, spec["allow_http"])
        return _insert(c, vault, who, spec, who.actor, headers=body.headers)
    if who.role != "bot" or _is_strategy_bot(who.actor):
        raise Problem("forbidden", "Only people and bots ask for webhook endpoints", 403)
    if body.headers:
        raise Problem("forbidden", "A person sets custom headers; a bot asks without them", 403)
    payload = _approval_payload(spec)
    digest = H.payload_hash(payload)
    approved = c.execute("SELECT * FROM approvals WHERE payload_hash=? AND requested_by_id=? AND kind='send' AND "
                         "decision='approved' AND consumed_at IS NULL ORDER BY decided_at LIMIT 1",
                         (digest, who.actor)).fetchone()
    if approved:
        H.approval_consume(c, who.actor, approved["id"])
        return _insert(c, vault, who, spec, approved["decided_by_id"], requested_by=who.actor)
    open_ = c.execute("SELECT id FROM approvals WHERE payload_hash=? AND decision IS NULL", (digest,)).fetchone()
    owner_only_http(who, spec["allow_http"])
    check_address(spec["url"], False)
    try:
        aid = open_["id"] if open_ else H.approval_request(c, who.actor, "send", payload)["id"]
    except H.Refused as exc:
        raise Problem("approval", str(exc), 422) from exc
    return {"approval": {"id": aid, "status": "pending"}, "webhook": None}


def carried(before, after):
    """The secret headers whose saved value an edit keeps as it was: the credentials a new address would receive."""
    old = {h["name"]: h["value"] for h in before if h.get("secret") and not h.get("needs") and h.get("value")}
    return [h["name"] for h in after if h.get("secret") and not h.get("needs") and old.get(h["name"]) == h.get("value")]


def destination_change(c, hook, url, kept, vault=None):
    """What moving `hook` to `url` does to the secret headers it keeps (`kept`, names): None when none is kept or the
    destination is the same (scheme, host, port, path and query: shared receivers tell recipients apart by path),
    else the safe old and new destination (backend/destinations.py)."""
    if not kept:
        return None
    key = vault.cipher.key(c) if vault is not None and getattr(vault, "configured", False) else b""
    return destinations.change(hook["url"], url, path_counts=True, key=key)


UPDATE_PATH = re.compile(r"/api/v2/webhooks/([^/]+)/update")


def kept_by(before, items):
    """The saved secret headers a headers list (`items`, as sent) keeps with their saved value: a secret listed
    without a value keeps the one it has (`clean_headers`). With no list, every saved secret is kept. The same answer
    `carried` gives after the save, read before it, so a card and the save agree on what a new address receives."""
    if items is None:
        return carried(before, before)
    saved = set(carried(before, before))
    out = []
    for item in items:
        item = item if isinstance(item, dict) else getattr(item, "__dict__", {})
        name = str(item.get("name") or "").strip().lower()
        if item.get("secret") and item.get("value") in (None, "") and name in saved and name not in out:
            out.append(name)
    return out


def pending_change(c, who, hook_id, body, vault=None):
    """For BotOps' Confirm card (backend/app.py `person_card`): (endpoint, destination change or None, kept secret
    header names) that `body`, an update asked for `who`, would make, without saving. Refused at once when `who` may not
    change the endpoint, as the tap would be. A secret listed without a value keeps its saved one, as the save does."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    body = body if isinstance(body, dict) else {}
    url = body.get("url") or hook["url"]
    items = body.get("headers")
    kept = kept_by(headers_of(hook), items if isinstance(items, list) or items is None else [])
    return hook, destination_change(c, hook, url, kept, vault), kept


def update(c, who, hook_id, body, vault=None):
    """Change an endpoint. A new address for another destination while secret headers keep their saved values is
    held (`destinations.held`) until the body says `credentials: "keep"` (they go to the new address) or `"clear"`
    (their values are removed, their names kept as needing a value, and the endpoint pauses until each is typed again
    or removed); either leaves one `integration.destination_changed` event. Any address change needs
    `expected_revision`, so the decision is about the endpoint as the editor saw it."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    if body.expected_revision is not None and body.expected_revision != hook["revision"]:
        raise Problem("version_conflict", "This endpoint changed; refresh before saving", 409)
    option = destinations.choice(body.credentials)
    name = body.name if body.name is not None else hook["name"]
    url = body.url if body.url is not None else hook["url"]
    allow = bool(hook["allow_http"] if body.allow_http is None else body.allow_http)
    if allow != bool(hook["allow_http"]):
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner turns plain http to this computer on or off", 403)
    events = clean_events(body.events) if body.events is not None else json.loads(hook["events"])
    host = check_address(url, allow)
    if url != hook["url"] and body.expected_revision is None:
        raise Problem("expected_revision", "Changing the address needs expected_revision: the revision you read", 422)
    filt = clean_filter(c, body.filter) if body.filter is not None else filter_of(hook)
    before = headers_of(hook)
    headers = before
    if body.headers is not None:
        if vault is None:
            raise Problem("webhook", "Headers cannot be changed here", 422)
        headers = clean_headers(c, vault, hook_id, body.headers, headers)
    kept = carried(before, headers)
    moved = destination_change(c, hook, url, kept, vault)
    if moved and not option:
        raise destinations.held("webhook", moved, kept, hook["revision"], hook["name"])
    destinations.require_tap(who, moved)
    if moved and option == destinations.CLEAR:
        headers = [{"name": h["name"], "secret": True, "value": "", "needs": True} if h["name"] in kept else h
                   for h in headers]
    enabled, reason = hook["enabled"], hook["paused_reason"] or ""
    if needs_value(headers):
        if enabled or reason == NEEDS_VALUE:
            enabled, reason = 0, NEEDS_VALUE              # nothing goes out without the header (Product: pause)
        # An endpoint already paused or turned off for another reason keeps that reason, so typing the values again
        # never turns back on what someone paused on purpose.
    elif reason == NEEDS_VALUE:
        enabled, reason = 1, ""                           # every cleared value was typed again or removed
    c.execute("UPDATE webhooks SET name=?, url=?, host=?, events=?, rate_per_hour=?, allow_http=?, "
              "filter=?, headers=?, enabled=?, paused_reason=?, revision=revision+1, updated_at=? WHERE id=?",
              (name, url, host, json.dumps(events), body.rate_per_hour or hook["rate_per_hour"], int(allow),
               json.dumps(filt), json.dumps(headers), enabled, reason, H.now(), hook_id))
    H.event(c, who.actor, "webhook.updated", hook_id, {"name": name})
    if moved:
        destinations.record(c, who, "webhook", hook_id, moved, kept, option, hook["revision"] + 1)
    if enabled and not hook["enabled"]:
        _refresh(c)
    return view(_row(c, hook_id), who, counts(c, [hook_id]).get(hook_id))


def rotate(c, vault, who, hook_id):
    """A new secret; the old one keeps signing beside it for a day, so the receiver can switch without a gap."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    old = vault.cipher.unseal(c, D.sealed_as(hook_id), hook["secret"])
    secret = new_secret()
    c.execute("UPDATE webhooks SET secret=?, secret_prev=?, secret_prev_until=?, revision=revision+1, updated_at=? WHERE id=?",
              (vault.cipher.seal(c, D.sealed_as(hook_id), secret), vault.cipher.seal(c, D.sealed_as(hook_id, True), old),
               H.shift(H.now(), hours=GRACE), H.now(), hook_id))
    H.event(c, who.actor, "webhook.rotated", hook_id, {})
    return {**view(_row(c, hook_id), who, counts(c, [hook_id]).get(hook_id)), "secret": secret}


def pause(c, who, hook_id, reason=""):
    hook = _row(c, hook_id)
    may_manage(who, hook)
    c.execute("UPDATE webhooks SET enabled=0, paused_reason=?, revision=revision+1, updated_at=? WHERE id=?",
              (reason or f"Paused by {who.actor}", H.now(), hook_id))
    H.event(c, who.actor, "webhook.paused", hook_id, {})
    return view(_row(c, hook_id), who, counts(c, [hook_id]).get(hook_id))


def resume(c, who, hook_id):
    hook = _row(c, hook_id)
    may_manage(who, hook)
    if needs_value(headers_of(hook)):
        raise Problem("needs_value", "Type the value of " + ", ".join(needs_value(headers_of(hook))) + " again, or remove "
                      "it, before turning this endpoint back on", 409)
    c.execute("UPDATE webhooks SET enabled=1, paused_reason='', disabled_at=NULL, disabled_reason='', failing_since=NULL, "
              "revision=revision+1, updated_at=? WHERE id=?", (H.now(), hook_id))
    H.event(c, who.actor, "webhook.resumed", hook_id, {})
    _refresh(c)
    return view(_row(c, hook_id), who, counts(c, [hook_id]).get(hook_id))


def delete(c, who, hook_id):
    """Gone for good: its pending sends are cancelled and its secrets cleared. Finished sends stay in the log until
    the cleanup removes them."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    cancelled = c.execute("UPDATE webhook_sends SET state='canceled', last_error='The endpoint was deleted', "
                          "next_attempt_at=?, claimed_at=NULL WHERE webhook_id=? AND state IN ('ready','sending')",
                          (H.now(), hook_id)).rowcount
    c.execute("UPDATE webhooks SET deleted_at=?, enabled=0, secret='', secret_prev='', headers='[]', updated_at=? WHERE id=?",
              (H.now(), H.now(), hook_id))
    H.event(c, who.actor, "webhook.deleted", hook_id, {"cancelled": cancelled})
    return {"id": hook_id, "deleted": True, "cancelled": cancelled}


def send_custom(c, who, hook_id, body):
    """A custom event to one endpoint, by a person or by a bot with sending on."""
    hook = _row(c, hook_id)
    name = body.type.strip()
    if not CUSTOM.fullmatch(name):
        raise Problem("webhook", "A custom event is named custom.<something> in lowercase", 422)
    may_send(c, who, hook, name)
    try:
        queued = publish(c, name, body.subject or name, body.data, who.actor, only=hook_id)
    except Refused as exc:
        raise Problem("webhook", exc.reason, 409) from exc
    return {"queued": queued, "type": name}


def send_example(c, who, hook_id, body):
    """The catalog's example for an event, or a plain test message, sent now to one endpoint."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    name = canonical(body.type) if body.type else "custom.example"
    if not name:
        raise Problem("webhook", f"{body.type} is not an event Tico sends", 422)
    if not hook["enabled"]:
        raise Problem("webhook", "That endpoint is paused", 409)
    queued = _queue(c, hook, "we_" + uuid.uuid4().hex[:20], name, "example", example_for(name))
    _refresh(c)
    return {"queued": queued, "type": name}


def sends(c, who, hook_id, state=None, limit=LOG_LISTED, offset=0):
    _reach(c, who, hook_id)
    if who.role == "bot":
        raise Problem("forbidden", "A person reads an endpoint's send log", 403)
    limit = max(1, min(int(limit or LOG_LISTED), LOG_LISTED))
    sql, args = "SELECT * FROM webhook_sends WHERE webhook_id=?", [hook_id]
    if state:
        sql, args = sql + " AND state=?", [*args, state]
    rows = c.execute(sql + " ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (*args, limit + 1, offset)).fetchall()
    more = len(rows) > limit
    out = {"items": [send_view(r) for r in rows[:limit]], "more": more}
    if more:
        out["next_offset"] = offset + limit
    return out


def one_send(c, who, hook_id, send_id):
    _reach(c, who, hook_id)
    if who.role == "bot":
        raise Problem("forbidden", "A person reads an endpoint's send log", 403)
    row = c.execute("SELECT * FROM webhook_sends WHERE id=? AND webhook_id=?", (send_id, hook_id)).fetchone()
    if not row:
        raise Problem("not_found", "Send not found", 404)
    attempts = c.execute("SELECT n,created_at,timestamp_sent,status,ms,response_head,error FROM webhook_attempts WHERE send_id=? "
                         "ORDER BY n DESC LIMIT 50", (send_id,)).fetchall()
    return {**send_view(row), "attempt_log": [dict(a) for a in attempts]}


def retry(c, who, hook_id, send_id):
    """Send a failed, cancelled or delivered send again now (a delivered one is a resend). Its id and body stay, so a
    receiver that kept the first still drops the repeat."""
    hook = _row(c, hook_id)
    may_manage(who, hook)
    row = c.execute("SELECT * FROM webhook_sends WHERE id=? AND webhook_id=?", (send_id, hook_id)).fetchone()
    if not row:
        raise Problem("not_found", "Send not found", 404)
    if row["state"] in ("ready", "sending"):
        raise Problem("webhook", "That send is still being tried", 409)
    if row["state"] == "withheld":
        raise Problem("webhook", "A rehearsal copy sends nothing", 409)
    c.execute("UPDATE webhook_sends SET state='ready', attempts=0, next_attempt_at=?, claimed_at=NULL, last_error='' WHERE id=?",
              (H.now(), send_id))
    H.event(c, who.actor, "webhook.retried", send_id, {"webhook": hook_id})
    _refresh(c)
    return send_view(c.execute("SELECT * FROM webhook_sends WHERE id=?", (send_id,)).fetchone())


# ------------------------------------------------------------------------------------------------------ operator tools
def _since(value):
    """A day (2026-10-20) or a time as the stored stamp, or a 422."""
    text = str(value or "").strip()
    try:
        if len(text) == 10:
            text += "T00:00:00+00:00"
        at = H.parse_ts(text)
    except Exception:
        at = None
    if at is None:
        raise Problem("webhook", "Give a date like 2026-10-20 or a time like 2026-10-20T14:00:00Z", 422)
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _working(c, who, hook_id):
    hook = _row(c, hook_id)
    may_manage(who, hook)
    if not hook["enabled"]:
        raise Problem("webhook", "That endpoint is off; turn it on first", 409)
    return hook


def recover_failed(c, who, hook_id, since):
    """Every send that gave up since `since` goes out again, same ids and bodies (a receiver that kept the first one
    still drops the repeat). At most REPLAY_BATCH at a time; `more` says run it again."""
    _working(c, who, hook_id)
    at = _since(since)
    ids = [r["id"] for r in c.execute("SELECT id FROM webhook_sends WHERE webhook_id=? AND state='failed' AND next_attempt_at>=? "
                                      "ORDER BY next_attempt_at LIMIT ?", (hook_id, at, REPLAY_BATCH + 1))]
    more, ids = len(ids) > REPLAY_BATCH, ids[:REPLAY_BATCH]
    for send_id in ids:
        c.execute("UPDATE webhook_sends SET state='ready', attempts=0, next_attempt_at=?, claimed_at=NULL, last_error='' WHERE id=?",
                  (H.now(), send_id))
    H.event(c, who.actor, "webhook.recovered", hook_id, {"since": at, "sends": len(ids)})
    if ids:
        _refresh(c)
    return {"webhook": hook_id, "since": at, "recovered": len(ids), "more": more}


def replay_missing(c, who, hook_id, since, after=""):
    """Events since `since` that this endpoint should have heard and has no send for (it was added late, was paused
    and cleared, or a filter changed) become sends now. The same tests as a live event: its subscription, its
    filter, and what its owner may read. Events are kept 30 days; an explicit send to another endpoint is not replayed.

    A round looks at REPLAY_BATCH events, whether or not they turn into sends. When more wait, `next_since` and
    `next_after` are where that round stopped: give them back as `since` and `after` and the next round starts there,
    so events this endpoint may not have never hold the rest up."""
    hook = _working(c, who, hook_id)
    at = _since(since)
    events, filt = json.loads(hook["events"]), filter_of(hook)
    where, params = ("created_at>=?", [at]) if not after else ("(created_at>? OR (created_at=? AND id>?))", [at, at, after])
    rows = c.execute(f"SELECT * FROM webhook_events e WHERE {where} AND (only_id='' OR only_id=?) AND NOT EXISTS("
                     "SELECT 1 FROM webhook_sends s WHERE s.event_id=e.id AND s.webhook_id=?) ORDER BY created_at, id LIMIT ?",
                     (*params, hook_id, hook_id, REPLAY_BATCH + 1)).fetchall()
    more, rows = len(rows) > REPLAY_BATCH, rows[:REPLAY_BATCH]
    cache, queued = {}, 0
    for row in rows:
        if not subscribed(events, row["type"]):
            continue
        try:
            data = json.loads(row["payload"])
        except ValueError:
            continue
        if filt and not passes(c, filt, _facts(c, row["type"], row["subject"], data, row["actor"])):
            continue
        shown = view_for(c, hook["created_by"], row["type"], row["subject"], data, cache)
        if shown is not None:
            queued += _queue(c, hook, row["id"], row["type"], row["subject"], shown, at=row["created_at"])
    H.event(c, who.actor, "webhook.replayed", hook_id, {"since": at, "sends": queued})
    if queued:
        _refresh(c)
    return {"webhook": hook_id, "since": at, "checked": len(rows), "queued": queued, "more": more,
            "next_since": rows[-1]["created_at"] if more else None, "next_after": rows[-1]["id"] if more else None}


def enable(c, who, hook_id):
    """Turn an endpoint back on. When Tico had turned it off for failing, the answer offers to send again what it
    held back: `recover.since` is the day the failing run began."""
    hook = _row(c, hook_id)
    offer = None
    if hook["disabled_at"]:
        since = hook["failing_since"] or H.shift(hook["disabled_at"], days=-DISABLE_DAYS)
        failed = c.execute("SELECT count(*) FROM webhook_sends WHERE webhook_id=? AND state='failed' AND next_attempt_at>=?",
                           (hook_id, since)).fetchone()[0]
        offer = {"since": since, "failed": failed}
    shown = resume(c, who, hook_id)
    return {"webhook": shown, "recover": offer}


def auto_disable(c, now=None):
    """The hourly check: an endpoint that has been failing since `failing_since` (its first failed attempt, cleared by
    any 2xx) for five days, with its latest attempt at least twelve hours after that start (one burst is not a dead
    endpoint), is turned off. It keeps its queue. One audit row, one Health line (health_summary) and one Needs-you
    line to its owner, once, since a disabled endpoint is no longer looked at. Names the endpoints."""
    now = now or H.now()
    window = H.shift(now, days=-DISABLE_DAYS)
    off = []
    for hook in c.execute("SELECT * FROM webhooks WHERE enabled=1 AND deleted_at IS NULL AND failing_since<=?", (window,)).fetchall():
        row = c.execute("SELECT count(*) n, max(created_at) latest FROM webhook_attempts WHERE webhook_id=? AND created_at>=?",
                        (hook["id"], hook["failing_since"])).fetchone()
        if not row["n"] or row["latest"] <= H.shift(hook["failing_since"], hours=DISABLE_SPAN_HOURS):
            continue
        reason = f"Every delivery failed for {DISABLE_DAYS} days"
        c.execute("UPDATE webhooks SET enabled=0, disabled_at=?, disabled_reason=?, paused_reason=?, revision=revision+1, "
                  "updated_at=? WHERE id=?", (now, reason, reason, now, hook["id"]))
        H.event(c, H.KEEPER, "webhook.auto_disabled", hook["id"], {"name": hook["name"], "attempts": row["n"]})
        try:
            c.execute("SAVEPOINT webhook_disable")
            task = H.task_create(c, H.KEEPER, f"Webhook {hook['name']} was turned off",
                                 f"Every attempt to reach it failed for {DISABLE_DAYS} days, so Tico stopped sending. "
                                 f"Fix the address, then turn it on in Settings > Webhooks or `hub webhook enable {hook['id']}`; "
                                 f"Tico then offers to send again what it held back.", hook["created_by"], lint=False)
            c.execute("UPDATE webhooks SET notify_task=? WHERE id=?", (task["id"], hook["id"]))
            c.execute("RELEASE webhook_disable")
        except Exception:
            c.execute("ROLLBACK TO webhook_disable")
            c.execute("RELEASE webhook_disable")
            log.warning("Could not file the Needs-you line for webhook %s", hook["id"], exc_info=True)
        off.append(hook["id"])
    return off


# ------------------------------------------------------------------------------------------------------------- Health
def health_summary(c):
    """What Health shows for outgoing webhooks, from the rows: sends that gave up in the last day, endpoints failing
    more than half their attempts, and events that skipped endpoints past the per-event cap."""
    day = H.shift(H.now(), hours=-24)
    names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM webhooks WHERE deleted_at IS NULL")}
    if not names:
        return {"failed": [], "flaky": [], "skipped": 0, "disabled": []}
    failed = [names[r["webhook_id"]] for r in c.execute(
        "SELECT DISTINCT webhook_id FROM webhook_sends WHERE state='failed' AND next_attempt_at>=?", (day,)) if r["webhook_id"] in names]
    flaky = []
    for r in c.execute("SELECT webhook_id, count(*) n, sum(CASE WHEN status BETWEEN 200 AND 299 THEN 0 ELSE 1 END) bad "
                       "FROM webhook_attempts WHERE created_at>=? GROUP BY webhook_id", (day,)):
        if r["webhook_id"] in names and r["n"] >= 5 and r["bad"] * 2 > r["n"] and names[r["webhook_id"]] not in failed:
            flaky.append(names[r["webhook_id"]])
    skipped = c.execute("SELECT count(*) FROM webhook_events WHERE skipped>0 AND created_at>=?", (day,)).fetchone()[0]
    off = [r["name"] for r in c.execute("SELECT name FROM webhooks WHERE disabled_at IS NOT NULL AND deleted_at IS NULL")]
    return {"failed": failed, "flaky": flaky, "skipped": skipped, "disabled": off}


# ---------------------------------------------------------------------------------------------------------- the routes
def install(app, settings, store, auth, mutate):
    use(auth, settings.public_url, settings)

    def once(request, body, fn):
        """A write whose answer carries a secret goes through the vault's receipt (Vault.change): the idempotency row
        keeps a keyed fingerprint of the request and the answer without the secret. A replay answers with
        `replayed: true` and no secret, so the page asks for a new one instead of showing a stale one."""
        shown = {}

        def run(c):
            result = fn(c)
            if result.get("webhook") and "secret" in result["webhook"]:
                shown["secret"] = result["webhook"].pop("secret")
            return result
        out = request.app.state.vault.change(request.state.identity, request.url.path,
                                             request.headers.get("idempotency-key"), body, lambda c, who: None, run)
        if out.get("webhook") is not None:
            out = {**out, "webhook": {**out["webhook"], "replayed": not shown, **({"secret": shown["secret"]} if shown else {})}}
        return out

    def person_or_bot(who):
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "Only people and bots use webhook endpoints", 403)

    @app.get("/api/v2/webhooks")
    def webhook_list(request: Request, limit: int = LISTED, offset: int = 0):
        """The caller's endpoints (an admin sees everyone's), with 7-day delivered and failed counts. Paged."""
        who = request.state.identity
        person_or_bot(who)
        with store.read() as c:
            return listing(c, who, limit, offset)

    @app.post("/api/v2/webhooks")
    def webhook_create(request: Request, body: WebhookCreate):
        """A new endpoint. The answer's `secret` is shown only now. A bot's call files an approval instead and the
        answer says `approval`; asking again after a person approved it makes the endpoint."""
        who = request.state.identity
        person_or_bot(who)

        def make(c):
            made = create(c, request.app.state.vault, who, body)
            return made if "approval" in made else {"webhook": made}
        return once(request, body, make)

    @app.get("/api/v2/webhooks/{hook_id}")
    def webhook_show(request: Request, hook_id: str):
        """One endpoint and its last ten attempts."""
        who = request.state.identity
        person_or_bot(who)
        with store.read() as c:
            hook = _reach(c, who, hook_id)
            shown = view(hook, who, counts(c, [hook_id]).get(hook_id))
            recent = [] if who.role == "bot" else [dict(r) for r in c.execute(
                "SELECT send_id,n,created_at,status,ms,error FROM webhook_attempts WHERE webhook_id=? ORDER BY created_at DESC, n DESC LIMIT 10",
                (hook_id,))]
            return {"webhook": shown, "recent_attempts": recent}

    @app.post("/api/v2/webhooks/{hook_id}/update")
    def webhook_update(request: Request, hook_id: str, body: WebhookUpdate):
        """Change the name, address, events, rate or (for the owner) the plain-http switch."""
        return mutate(request, body, lambda c: {"webhook": update(c, request.state.identity, hook_id, body,
                                                                  request.app.state.vault)})

    @app.post("/api/v2/webhooks/{hook_id}/rotate")
    def webhook_rotate(request: Request, hook_id: str, body: M.Empty):
        """A new secret, shown once; the old one keeps signing beside it for 24 hours."""
        return once(request, body, lambda c: {"webhook": rotate(c, request.app.state.vault, request.state.identity, hook_id)})

    @app.post("/api/v2/webhooks/{hook_id}/pause")
    def webhook_pause(request: Request, hook_id: str, body: Pause):
        """Stop sending; what is queued waits."""
        return mutate(request, body, lambda c: {"webhook": pause(c, request.state.identity, hook_id, body.reason)})

    @app.post("/api/v2/webhooks/{hook_id}/resume")
    def webhook_resume(request: Request, hook_id: str, body: M.Empty):
        return mutate(request, body, lambda c: {"webhook": resume(c, request.state.identity, hook_id)})

    @app.post("/api/v2/webhooks/{hook_id}/enable")
    def webhook_enable(request: Request, hook_id: str, body: M.Empty):
        """Turn an endpoint back on. After Tico turned it off for failing, the answer offers `recover` with the day to
        send again from."""
        return mutate(request, body, lambda c: enable(c, request.state.identity, hook_id))

    @app.post("/api/v2/webhooks/{hook_id}/recover")
    def webhook_recover(request: Request, hook_id: str, body: WebhookSince):
        """Send again every send that gave up since a date."""
        return mutate(request, body, lambda c: recover_failed(c, request.state.identity, hook_id, body.since))

    @app.post("/api/v2/webhooks/{hook_id}/replay")
    def webhook_replay(request: Request, hook_id: str, body: WebhookReplay):
        """Send the events since a date that this endpoint should have had and has no send for. At most 500 events a
        round; when `more`, send `next_since` and `next_after` back as `since` and `after` for the next round."""
        return mutate(request, body, lambda c: replay_missing(c, request.state.identity, hook_id, body.since, body.after))

    @app.post("/api/v2/webhooks/{hook_id}/delete")
    def webhook_delete(request: Request, hook_id: str, body: M.Empty):
        """Delete the endpoint and cancel what is waiting to be sent."""
        return mutate(request, body, lambda c: delete(c, request.state.identity, hook_id))

    @app.post("/api/v2/webhooks/{hook_id}/send")
    def webhook_send(request: Request, hook_id: str, body: WebhookSend):
        """A custom event (custom.<name>) to one endpoint."""
        return mutate(request, body, lambda c: send_custom(c, request.state.identity, hook_id, body))

    @app.post("/api/v2/webhooks/{hook_id}/example")
    def webhook_example(request: Request, hook_id: str, body: WebhookExample):
        """Send an event type's example to one endpoint."""
        return mutate(request, body, lambda c: send_example(c, request.state.identity, hook_id, body))

    @app.get("/api/v2/webhooks/{hook_id}/sends")
    def webhook_sends(request: Request, hook_id: str, state: str = "", limit: int = LOG_LISTED, offset: int = 0):
        """The send log, newest first: type, subject, state, attempts, last status, next attempt."""
        who = request.state.identity
        person_or_bot(who)
        with store.read() as c:
            return sends(c, who, hook_id, state or None, limit, offset)

    @app.get("/api/v2/webhooks/{hook_id}/sends/{send_id}")
    def webhook_send_one(request: Request, hook_id: str, send_id: str):
        """One send with its attempts."""
        who = request.state.identity
        person_or_bot(who)
        with store.read() as c:
            return {"send": one_send(c, who, hook_id, send_id)}

    @app.post("/api/v2/webhooks/{hook_id}/sends/{send_id}/retry")
    def webhook_retry(request: Request, hook_id: str, send_id: str, body: M.Empty):
        """Send a failed, cancelled or delivered send again."""
        return mutate(request, body, lambda c: {"send": retry(c, request.state.identity, hook_id, send_id)})
