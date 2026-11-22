# pyright: strict
"""Jira's sender for the outbound gate (backend/outbound_actions.py, docs/outbound-actions.md "Jira").

An action is one write to one issue: `transition` (payload `{"to": "<status name>"}`: the issue moved to that status
through the transition Jira offers to it) or `comment` (payload `{"body": "<text>"}`). Its destination is the issue key
(`PROJ-12`) and its `connection_id` the Jira connection, which must be a record integration with a shared read and write
key (backend/item_sources/jira.py `integration`, `writable`): the same rule, key lookup and deadline-bounded call as the
background reads. Jira is an outside system, so the audience is external; the channel's own default for it is On its own
(record bookkeeping, not a message to people) until the company sets another.

- **Who.** A bot (or a person) queues one only while it holds Read and write on the connection (api_tools
  `subject_level`), checked again before each attempt, so a grant taken back stops it; Tico itself always may.
- **Once.** Before the write, the adapter asks whether its effect is already there: the issue already in the status, or
  a comment whose last paragraph is exactly the action's marker (`marker`, the last paragraph of every comment it
  writes). A lookup answer that is cut off or not the shape asked for is no answer: nothing is written then. Found, it is done with
  no write; `reconcile` uses the same lookup to settle an unknown row done, and nothing else settles one: a row it
  cannot prove stays unknown for the owner (Jira's 404 also means the key cannot see the issue).
- **Release close-out.** An action with `closeout: true` in its payload (backend/release_closeout.py) reads the issue
  right before the write (a transition does anyway, a comment adds one read). While the issue is withdrawn (a To Do
  status other than the payload's `ready`), gone (404) or closed in a Done status other than the payload's `done`, it
  waits PAUSED_S and tries again, not counted as a retry: it resumes by itself once the issue is back.
- **Answers.** 429 is a safe rejection (Retry-After, else a minute). Anything that fails before the write (the key, a
  lookup Jira did not answer or answered unreadably) is safe too: nothing was sent. Jira's 400, 401, 403 or 404 to a
  write is a certain refusal; any other answer, or none, is unknown.

`backend/outbound_actions.py` registers the adapter (its end); this module imports it only inside functions, so either
may be imported first.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from .item_sources import jira as JS
from .store import H as _H
from .store import Problem

if TYPE_CHECKING:
    from .outbound_actions import Settled

H: Any = _H
CHANNEL = "jira"
OPERATIONS = ("transition", "comment")
# A record write to a record integration is bookkeeping, not a message to people: On its own until the company says.
DEFAULTS = {"external": "on_their_own", "internal": "on_their_own"}
MAX_STATUS = 100
MAX_COMMENT = 30_000  # Jira's own limit is 32,767 characters; the marker goes after it
COMMENTS_LOOKED_AT = 100  # the newest comments searched for the marker
COMMENTS_PAGE = 20  # read a page at a time, halved while a page is cut off
COMMENTS_READ = 2 * 1024 * 1024  # bytes of a comments page read: one long comment, escaped, still fits
RETRY_S = 60.0  # a safe rejection without its own wait
PAUSED_S = 3600.0  # a release close-out write waits this long while its issue is withdrawn, gone or closed otherwise
BUSY_S = 5.0
AGENT = "Tico-outbound"
REFUSALS = (400, 401, 403, 404)


def _oa() -> Any:
    from . import outbound_actions

    return outbound_actions


def marker(occurrence_key: str) -> str:
    """The short mark ending a comment the action writes, as its own last paragraph: how a retry or a reconcile finds
    it."""
    return f"[tico:{hashlib.sha256(occurrence_key.encode()).hexdigest()[:10]}]"


def _payload(operation: str, payload: Any) -> dict[str, str]:
    """The payload checked for its operation: {"to"} for a transition, {"body"} for a comment. A Problem otherwise."""
    got = cast("dict[str, Any]", payload) if isinstance(payload, dict) else {}
    if operation == "transition":
        to = got.get("to")
        if not isinstance(to, str) or not to.strip() or len(to) > MAX_STATUS:
            raise Problem("validation", 'A Jira transition says the status to move to: {"to": "Done"}', 422)
        return {"to": to.strip()}
    if operation == "comment":
        body = got.get("body")
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_COMMENT:
            raise Problem("validation", f"A Jira comment has a body of 1 to {MAX_COMMENT} characters", 422)
        return {"body": body.strip()}
    raise Problem("validation", f"Jira does {' and '.join(OPERATIONS)}, not {operation!r}", 422)


def prepare(
    c: Any,
    settings: Any,
    actor: str,
    destination: str,
    payload: Any,
    *,
    operation: str,
    connection_id: str,
    task_id: str | None = None,
) -> tuple[str, str]:
    """(external, the issue key as Jira spells it), or a Problem: an unknown operation or payload, a destination that is
    not an issue key, a connection that is not a record integration with a shared read and write key, or an actor other
    than Tico itself without Read and write on it. Run at enqueue and again before each attempt."""
    _payload(operation, payload)
    key = JS.match_key(JS.ISSUE, destination)
    if key is None:
        raise Problem("validation", "A Jira action's destination is the issue key, e.g. PROJ-12", 422)
    if not connection_id:
        raise Problem("validation", "Say the Jira connection (connection_id) the issue is on", 422)
    if not JS.writable(c, connection_id):
        raise Problem(
            "validation",
            "Tico writes to Jira only through a record integration on one Jira site with a shared read and write key",
            422,
        )
    site = cast("dict[str, Any]", payload).get("site") if isinstance(payload, dict) else None
    if site is not None and site != JS.integrations(c, connection_id).get(connection_id):
        raise Problem("connection_sites", f"The connection is no longer on {site}, the site this action was for", 409)
    if actor != H.TICO_ACTOR and _level(c, connection_id, actor) != "read_write":
        raise Problem(
            "forbidden", "Writing to Jira through this connection needs Read and write on it: ask for it in Tools", 403
        )
    return "external", key


def _level(c: Any, connection_id: str, actor: str) -> str | None:
    """`actor`'s level on the exact key a write through the connection goes with (JS.key_of, as JS.connect picks it):
    read_write only with a grant at Read and write on that key; read, or None for no grant on it."""
    from . import api_tools as _api_tools
    from . import credentials as _credentials

    tools: Any = _api_tools
    vault: Any = _credentials
    key = JS.key_of(c, connection_id, write=True)
    grant = vault.effective_grant(c, key["id"], tools.subject_of(c, actor)) if key is not None else None
    if grant is None:
        return None
    return "read" if grant["access_level"] == "read" else "read_write"


def freeze(c: Any, connection_id: str, destination: str) -> dict[str, Any]:
    """The Jira site the action is for, frozen into its payload (and so into what an OK of it shows and binds):
    `prepare` holds each attempt to it."""
    return {"site": JS.integrations(c, connection_id).get(connection_id) or ""}


class _Answer(Exception):
    """Jira answered a lookup with something other than 2xx."""

    def __init__(self, reply: JS.Reply):
        super().__init__(f"Jira answered {reply.status}")
        self.reply = reply


class _Unreadable(Exception):
    """A 2xx lookup answer that was cut off or is not the shape asked for: it tells nothing."""


class _Cut(_Unreadable):
    """A 2xx lookup answer longer than JS.MAX_BODY."""


Connection = tuple[str, dict[str, str], dict[str, str]]


def _connect(store: Any, row: Any, *, write: bool = True) -> Connection:
    """(API address, headers, query) through the connection's shared key (its read and write key for a write); Unreadable
    when it no longer has one."""
    vault = _oa().vault()
    if vault is None or getattr(vault, "store", store) is not store:
        from .credentials import Vault

        vault = Vault(store)
    connection_id = str(row["connection_id"])
    with store.transaction() as c:
        # The actor's level on the very key this write goes with, read in the same transaction that picks it: a key
        # deleted since the permit cannot hand the write to another key the actor was never checked on.
        if write and row["actor"] != H.TICO_ACTOR and _level(c, connection_id, str(row["actor"])) != "read_write":
            raise _oa().Refused("The bot no longer holds Read and write on the key this Jira write goes with")
        return JS.connect(c, vault, connection_id, write=write, agent=AGENT)


def _get(on: Connection, path: str, query: dict[str, str] | None = None, most: int | None = None) -> dict[str, object]:
    """One lookup under the issue (at most `most` bytes of answer read, JS.MAX_BODY by default): Jira's JSON object,
    _Answer for anything but 2xx, _Unreadable for an answer cut off or not an object."""
    base, headers, params = on
    reply = JS.call("GET", f"{base}{path}", headers, {**params, **(query or {})}, JS.TIMEOUT_S, most=most)
    if not 200 <= reply.status < 300:
        raise _Answer(reply)
    if reply.cut:
        raise _Cut(f"Jira's answer to {path} was too long to read")
    if not isinstance(reply.body, dict):
        raise _Unreadable(f"Jira's answer to {path} could not be read")
    return cast("dict[str, object]", reply.body)


def _list(answer: dict[str, object], name: str) -> list[dict[str, Any]]:
    """Every entry of the answer's `name` list, each an object; _Unreadable when one is not."""
    got = answer.get(name)
    if not isinstance(got, list) or not all(isinstance(x, dict) for x in cast("list[object]", got)):
        raise _Unreadable(f"Jira's answer has no readable {name}")
    return [cast("dict[str, Any]", x) for x in cast("list[object]", got)]


def _issue(row: Any) -> str:
    return f"/rest/api/3/issue/{quote(str(row['destination']))}"


# Atlassian Document Format's inline nodes: a paragraph holds only these.
INLINE = frozenset(
    {"text", "hardBreak", "mention", "emoji", "date", "status", "inlineCard", "mediaInline", "placeholder"}
)


def _nodes(value: object, *, inline: bool = False) -> list[dict[str, object]]:
    """A node list whose every node is an object with a type, and every text node has its text; `inline`: none holds
    content of its own and each is an inline kind (a paragraph's children). Else _Unreadable."""
    if not isinstance(value, list):
        raise _Unreadable("A comment on the issue has no readable body")
    nodes: list[dict[str, object]] = []
    for node in cast("list[object]", value):
        got = cast("dict[str, object]", node) if isinstance(node, dict) else {}
        kind = got.get("type")
        bad = not isinstance(kind, str) or (inline and (kind not in INLINE or "content" in got))
        if bad or (got.get("type") == "text" and not isinstance(got.get("text"), str)):
            raise _Unreadable("A comment on the issue has a node that cannot be read")
        nodes.append(got)
    return nodes


def _marked(comment: dict[str, Any], mark: str) -> bool:
    """Whether a comment's last paragraph is one text node that is exactly `mark`. _Unreadable when its body, its last
    block or any node of that block cannot be read: that tells nothing either way."""
    body = comment.get("body")
    blocks = _nodes(cast("dict[str, object]", body).get("content") if isinstance(body, dict) else None)
    if not blocks or blocks[-1].get("type") != "paragraph":
        return False
    inner = blocks[-1].get("content", [])
    nodes = _nodes(inner, inline=True)
    return len(nodes) == 1 and nodes[0].get("type") == "text" and nodes[0].get("text") == mark


def _comment(on: Connection, row: Any) -> dict[str, Any] | None:
    """The comment ending in the action's marker among the newest COMMENTS_LOOKED_AT, read COMMENTS_PAGE at a time. A
    page too long to read is asked again from the same place at half the size, down to one comment; one comment still
    too long, like any entry that cannot be read, is _Unreadable."""
    mark = marker(str(row["occurrence_key"]))
    start, size = 0, COMMENTS_PAGE
    while start < COMMENTS_LOOKED_AT:
        size = min(size, COMMENTS_LOOKED_AT - start)
        query = {"orderBy": "-created", "startAt": str(start), "maxResults": str(size)}
        try:
            answer = _get(on, _issue(row) + "/comment", query, COMMENTS_READ)
        except _Cut:
            if size == 1:
                raise
            size //= 2
            continue
        page = _list(answer, "comments")
        for comment in page:
            if _marked(comment, mark):
                return {"comment_id": str(comment.get("id") or "")}
        total = answer.get("total")
        start += len(page)
        if not page or (isinstance(total, int) and start >= total) or (not isinstance(total, int) and len(page) < size):
            break
    return None


def _fields(on: Connection, row: Any) -> dict[str, Any]:
    """The issue's status as one read shows it (item_sources/jira.py `fields_of`); _Unreadable when it has none."""
    fields = JS.fields_of(_get(on, _issue(row), {"fields": JS.FIELDS}), str(row["connection_id"]))
    if fields is None:
        raise _Unreadable("Jira's answer has no status")
    return fields


def paused(fields: dict[str, Any], raw: dict[str, Any]) -> str:
    """Why a release close-out write waits ('' when it may go): the issue withdrawn (backend/release_closeout.py
    `judged`: a To Do status other than the Ready it was filed in, never with none saved), or closed in a Done status
    other than the action's own `done` target."""
    from . import release_closeout

    state = release_closeout.judged(fields, raw.get("ready"))
    if state == "closed" and str(fields.get("status") or "").casefold() != str(raw.get("done") or "").casefold():
        return f"The issue was closed in Jira as {fields.get('status')}: waiting until it is back"
    return "The issue was withdrawn in Jira: waiting until it is back in Ready" if state == "withdrawn" else ""


def closeout_read(store: Any, target: dict[str, Any]) -> Any:
    """The same live status read and withdrawal test used by the Jira sender, using its read connection."""
    OA = _oa()
    try:
        reason = paused(_fields(_connect(store, target, write=False), target), target)
        if not reason:
            return None
    except _Answer as exc:
        wait = PAUSED_S if exc.reply.status == 404 else exc.reply.retry_after or RETRY_S
        return OA.SafeRejection(wait, "Waiting for the release issue in Jira", counted=False)
    except JS.Busy:
        return OA.SafeRejection(BUSY_S, "Waiting for Jira calls to become available", counted=False)
    except Exception:  # noqa: BLE001 - a read that cannot confirm Jira's status must never allow a post
        return OA.SafeRejection(RETRY_S, "Waiting for the release issue in Jira", counted=False)
    return OA.SafeRejection(PAUSED_S, reason, counted=False)


def _found(
    on: Connection, row: Any, payload: dict[str, str], fields: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """The action's effect, when Jira already shows it: the issue in the status (`fields`, when already read), or a
    comment ending in the marker among the newest COMMENTS_LOOKED_AT."""
    if row["operation"] == "transition":
        status = str((fields or _fields(on, row))["status"])
        return {"status": status} if status.casefold() == payload["to"].casefold() else None
    return _comment(on, row)


def _transition(on: Connection, row: Any, to: str) -> tuple[str, str]:
    """(id, target status) of the transition Jira offers from the issue's status to the status named `to`; Refused when
    it offers none."""
    choices = _list(_get(on, _issue(row) + "/transitions"), "transitions")

    def target(t: dict[str, Any]) -> str:
        found = t.get("to")
        return str(cast("dict[str, Any]", found).get("name") or "") if isinstance(found, dict) else ""

    for t in choices:
        if target(t) and target(t).casefold() == to.casefold() and t.get("id"):
            return str(t["id"]), target(t)
    names = ", ".join(sorted({target(t) for t in choices} - {""})) or "none"
    raise _oa().Refused(f"Jira offers no transition to {to!r} from the issue's status (it offers: {names})"[:300])


def document(text: str, mark: str) -> dict[str, Any]:
    """A comment as Jira's document format: a paragraph per blank-line-separated block (its lines kept), then the mark
    as a paragraph of its own."""
    paragraphs: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip()]
        content: list[dict[str, Any]] = []
        for i, line in enumerate(lines):
            if i:
                content.append({"type": "hardBreak"})
            content.append({"type": "text", "text": line})
        if content:
            paragraphs.append({"type": "paragraph", "content": content})
    paragraphs.append({"type": "paragraph", "content": [{"type": "text", "text": mark}]})
    return {"type": "doc", "version": 1, "content": paragraphs}


def dispatch(store: Any, row: Any) -> Any:
    """The one write, after asking whether its effect is already there: a receipt, a SafeRejection, Refused, or a raise
    (unknown) when the write may have landed."""
    OA = _oa()
    raw = json.loads(row["payload"] or "{}")
    payload = _payload(str(row["operation"]), raw)
    closeout = isinstance(raw, dict) and bool(cast("dict[str, Any]", raw).get("closeout"))
    try:
        on = _connect(store, row)
    except JS.Unreadable as exc:
        raise OA.Refused(str(exc)) from None
    except OA.Refused:
        raise
    except Exception:  # noqa: BLE001 - the key could not be read: nothing was sent
        return OA.SafeRejection(RETRY_S, "The connection's key could not be read")
    try:
        fields = _fields(on, row) if closeout or row["operation"] == "transition" else None
        why = paused(fields, cast("dict[str, Any]", raw)) if closeout and fields else ""
        if why:
            return OA.SafeRejection(PAUSED_S, why, counted=False)
        already = _found(on, row, payload, fields)
        transition = ("", "")
        if not already and row["operation"] == "transition":
            transition = _transition(on, row, payload["to"])
    except _Answer as exc:
        if closeout and exc.reply.status == 404:  # gone, or the key cannot see it now: the close-out waits
            return OA.SafeRejection(PAUSED_S, "Jira no longer shows the issue: waiting until it is back", counted=False)
        if exc.reply.status in REFUSALS:
            raise OA.Refused(f"Jira answered {exc.reply.status} before the write") from None
        return OA.SafeRejection(exc.reply.retry_after or RETRY_S, f"Jira answered {exc.reply.status}")
    except JS.Busy:
        return OA.SafeRejection(BUSY_S, "Jira calls are busy")
    except OA.Refused:
        raise
    except _Unreadable as exc:
        return OA.SafeRejection(RETRY_S, str(exc))
    except Exception:  # noqa: BLE001 - a lookup Jira did not answer: nothing was written
        return OA.SafeRejection(RETRY_S, "Jira could not be reached before the write")
    if already:
        return {**already, "already": True}
    if row["operation"] == "transition":
        path, body = _issue(row) + "/transitions", {"transition": {"id": transition[0]}}
    else:
        path = _issue(row) + "/comment"
        body = {"body": document(payload["body"], marker(str(row["occurrence_key"])))}
    base, headers, params = on
    try:
        reply = JS.call("POST", f"{base}{path}", headers, params, JS.TIMEOUT_S, body)
    except JS.Busy:
        return OA.SafeRejection(BUSY_S, "Jira calls are busy")
    except Exception as exc:  # noqa: BLE001 - it may have landed: unknown, in words that never carry the key
        late = "Timeout" in type(exc).__name__
        raise RuntimeError("Jira could not be reached" + (" (timed out)" if late else "")) from None
    if reply.status == 429:
        return OA.SafeRejection(reply.retry_after or RETRY_S, "Jira answered 429")
    if closeout and reply.status == 404:  # the issue went between the read and the write: nothing written, it waits
        return OA.SafeRejection(PAUSED_S, "Jira no longer shows the issue: waiting until it is back", counted=False)
    if reply.status in REFUSALS:
        raise OA.Refused(f"Jira refused the {row['operation']} ({reply.status})")
    if not 200 <= reply.status < 300:
        raise RuntimeError(f"Jira answered {reply.status}")  # it may have acted: unknown
    if row["operation"] == "transition":
        return {"transition_id": transition[0], "status": transition[1]}
    got = reply.body
    return {"comment_id": str(cast("dict[str, object]", got).get("id") or "") if isinstance(got, dict) else ""}


def reconcile(store: Any, row: Any) -> "Settled | None":
    """An unknown row settled from Jira's own record: done when its effect is there (`_found`), else None. No record of
    it proves nothing (the attempt may still land, and Jira's 404 also means the key cannot see the issue), so it waits
    for the owner's confirm-sent. It only reads: the connection's read key will do."""
    payload = _payload(str(row["operation"]), json.loads(row["payload"] or "{}"))
    try:
        on = _connect(store, row, write=False)
        found = _found(on, row, payload)
    except (JS.Unreadable, _Answer, _Unreadable):
        return None
    return _oa().Settled("done", {**found, "reconciled": True}) if found else None
