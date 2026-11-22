# pyright: strict
"""Jira's sender for the outbound gate (backend/outbound_actions.py, docs/outbound-actions.md "Jira").

An action is one write to one issue: `transition` (payload `{"to": "<status name>"}`: the issue moved to that status
through the transition Jira offers to it) or `comment` (payload `{"body": "<text>"}`). Its destination is the issue key
(`PROJ-12`) and its `connection_id` the Jira connection, which must be a record integration with a shared read and write
key (backend/item_sources/jira.py `integration`, `writable`): the same rule, key lookup and deadline-bounded call as the
background reads. Jira is an outside system, so the audience is external; the channel's own default for it is On its own
(record bookkeeping, not a message to people) until the company sets another.

- **Once.** Before the write, the adapter asks whether its effect is already there: the issue already in the status, or
  a comment carrying the action's marker (`marker`, the last line of every comment it writes). Found, it is done with
  no write; `reconcile` uses the same lookup to settle an unknown row done. Only Jira's 404 for the issue settles one
  failed: nothing landed on an issue Jira does not have.
- **Answers.** 429 is a safe rejection (Retry-After, else a minute). Anything that fails before the write (the key, a
  lookup Jira did not answer) is safe too: nothing was sent. Jira's 400, 401, 403 or 404 to a write is a certain
  refusal; any other answer, or none, is unknown.
"""

import hashlib
import json
from typing import Any, cast
from urllib.parse import quote

from . import outbound_actions as OA
from .item_sources import jira as JS
from .store import Problem

CHANNEL = "jira"
OPERATIONS = ("transition", "comment")
MAX_STATUS = 100
MAX_COMMENT = 30_000  # Jira's own limit is 32,767 characters; the marker goes after it
COMMENTS_LOOKED_AT = 100  # the newest comments searched for the marker
RETRY_S = 60.0  # a safe rejection without its own wait
BUSY_S = 5.0
AGENT = "Tico-outbound"
REFUSALS = (400, 401, 403, 404)


def marker(occurrence_key: str) -> str:
    """The short mark at the end of a comment the action writes: how a retry or a reconcile finds it."""
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
    not an issue key, or a connection that is not a record integration with a shared read and write key."""
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
    return "external", key


class _Answer(Exception):
    """Jira answered a call with something other than 2xx."""

    def __init__(self, reply: JS.Reply):
        super().__init__(f"Jira answered {reply.status}")
        self.reply = reply


Connection = tuple[str, dict[str, str], dict[str, str]]


def _connect(store: Any, row: Any) -> Connection:
    """(API address, headers, query) through the connection's read and write key; Unreadable when it no longer has one."""
    vault = OA.vault()
    if vault is None:
        raise RuntimeError("No credential vault")
    with store.transaction() as c:
        return JS.connect(c, vault, str(row["connection_id"]), write=True, agent=AGENT)


def _call(on: Connection, method: str, path: str, query: dict[str, str] | None = None, body: object = None) -> object:
    """One call to `path` under the issue; the JSON answer, or _Answer for anything but 2xx."""
    base, headers, params = on
    reply = JS.call(method, f"{base}{path}", headers, {**params, **(query or {})}, JS.TIMEOUT_S, body)
    if not 200 <= reply.status < 300:
        raise _Answer(reply)
    return reply.body


def _issue(row: Any) -> str:
    return f"/rest/api/3/issue/{quote(str(row['destination']))}"


def _text(node: object) -> str:
    """Every text in an Atlassian Document Format node, joined."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_text(n) for n in cast("list[object]", node))
    if isinstance(node, dict):
        got = cast("dict[str, object]", node)
        return " ".join(t for t in (_text(got.get("text")), _text(got.get("content"))) if t)
    return ""


def _found(on: Connection, row: Any, payload: dict[str, str]) -> dict[str, Any] | None:
    """The action's effect, when Jira already shows it: the issue in the status, or a comment with the marker."""
    if row["operation"] == "transition":
        fields = JS.fields_of(_call(on, "GET", _issue(row), {"fields": JS.FIELDS}), str(row["connection_id"]))
        status = str((fields or {}).get("status") or "")
        return {"status": status} if status.casefold() == payload["to"].casefold() else None
    answer = _call(on, "GET", _issue(row) + "/comment", {"orderBy": "-created", "maxResults": str(COMMENTS_LOOKED_AT)})
    comments = cast("dict[str, object]", answer).get("comments") if isinstance(answer, dict) else None
    mark = marker(str(row["occurrence_key"]))
    for comment in cast("list[object]", comments) if isinstance(comments, list) else []:
        if isinstance(comment, dict) and mark in _text(cast("dict[str, object]", comment).get("body")):
            return {"comment_id": str(cast("dict[str, object]", comment).get("id") or "")}
    return None


def _transition(on: Connection, row: Any, to: str) -> str:
    """The id of the transition Jira offers from the issue's status to `to` (its target status's name, else the
    transition's own name); Refused when it offers none."""
    answer = _call(on, "GET", _issue(row) + "/transitions")
    offered = cast("dict[str, object]", answer).get("transitions") if isinstance(answer, dict) else None
    choices = [cast("dict[str, Any]", t) for t in cast("list[object]", offered or []) if isinstance(t, dict)]
    want = to.casefold()

    def target(t: dict[str, Any]) -> str:
        found = t.get("to")
        return str(cast("dict[str, Any]", found).get("name") or "") if isinstance(found, dict) else ""

    def name(t: dict[str, Any]) -> str:
        return str(t.get("name") or "")

    for spelled in (target, name):  # the status it goes to first, then the transition's own name
        for t in choices:
            if spelled(t).casefold() == want and t.get("id"):
                return str(t["id"])
    names = ", ".join(sorted({target(t) or name(t) for t in choices} - {""})) or "none"
    raise OA.Refused(f"Jira offers no transition to {to!r} from the issue's status (it offers: {names})"[:300])


def document(text: str, mark: str) -> dict[str, Any]:
    """A comment as Jira's document format: a paragraph per blank-line-separated block (its lines kept), then the mark."""
    paragraphs: list[dict[str, Any]] = []
    for block in [*text.split("\n\n"), mark]:
        lines = [line for line in block.split("\n") if line.strip()]
        content: list[dict[str, Any]] = []
        for i, line in enumerate(lines):
            if i:
                content.append({"type": "hardBreak"})
            content.append({"type": "text", "text": line})
        if content:
            paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}


def _safe(error: _Answer) -> OA.SafeRejection:
    return OA.SafeRejection(error.reply.retry_after or RETRY_S, f"Jira answered {error.reply.status}")


def dispatch(store: Any, row: Any) -> Any:
    """The one write, after asking whether its effect is already there: a receipt, a SafeRejection, Refused, or a raise
    (unknown) when the write may have landed."""
    payload = _payload(str(row["operation"]), json.loads(row["payload"] or "{}"))
    try:
        on = _connect(store, row)
    except JS.Unreadable as exc:
        raise OA.Refused(str(exc)) from None
    except Exception:  # noqa: BLE001 - the key could not be read: nothing was sent
        return OA.SafeRejection(RETRY_S, "The connection's key could not be read")
    try:
        already = _found(on, row, payload)
        transition = _transition(on, row, payload["to"]) if not already and row["operation"] == "transition" else ""
    except _Answer as exc:
        if exc.reply.status == 429:
            return _safe(exc)
        if exc.reply.status in REFUSALS:
            raise OA.Refused(f"Jira answered {exc.reply.status} before the write") from None
        return _safe(exc)
    except JS.Busy:
        return OA.SafeRejection(BUSY_S, "Jira calls are busy")
    except OA.Refused:
        raise
    except Exception:  # noqa: BLE001 - a lookup Jira did not answer: nothing was written
        return OA.SafeRejection(RETRY_S, "Jira could not be reached before the write")
    if already:
        return {**already, "already": True}
    if row["operation"] == "transition":
        method, path, body = "POST", _issue(row) + "/transitions", {"transition": {"id": transition}}
    else:
        method, path = "POST", _issue(row) + "/comment"
        body = {"body": document(payload["body"], marker(str(row["occurrence_key"])))}
    base, headers, params = on
    try:
        reply = JS.call(method, f"{base}{path}", headers, params, JS.TIMEOUT_S, body)
    except JS.Busy:
        return OA.SafeRejection(BUSY_S, "Jira calls are busy")
    if reply.status == 429:
        return OA.SafeRejection(reply.retry_after or RETRY_S, "Jira answered 429")
    if reply.status in REFUSALS:
        raise OA.Refused(f"Jira refused the {row['operation']} ({reply.status})")
    if not 200 <= reply.status < 300:
        raise RuntimeError(f"Jira answered {reply.status}")  # it may have acted: unknown
    if row["operation"] == "transition":
        return {"transition_id": transition, "status": payload["to"]}
    got = reply.body
    return {"comment_id": str(cast("dict[str, object]", got).get("id") or "") if isinstance(got, dict) else ""}


def reconcile(store: Any, row: Any) -> "OA.Settled | None":
    """An unknown row settled from Jira's own record: done when its effect is there (`_found`), failed when Jira has no
    such issue (404), else None (no record of it proves nothing: the attempt may still land)."""
    payload = _payload(str(row["operation"]), json.loads(row["payload"] or "{}"))
    try:
        on = _connect(store, row)
        found = _found(on, row, payload)
    except JS.Unreadable:
        return None
    except _Answer as exc:
        if exc.reply.status == 404:
            return OA.Settled("failed", error="Jira has no such issue (404)")
        return None
    return OA.Settled("done", {**found, "reconciled": True}) if found else None


ADAPTER = OA.register(
    OA.Adapter(CHANNEL, prepare, dispatch, reconcile, defaults={"external": "on_their_own", "internal": "on_their_own"})
)
