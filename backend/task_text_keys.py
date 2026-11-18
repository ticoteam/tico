# pyright: strict
"""Keys kept out of task text (spec "Keep keys out of task comments and bot memory", task E-607).

Every task text write runs the key detector's certain-tier rules (backend/secret_drop.py, backend/secret_rules.json)
before anything is stored, from any client, person or bot:

- Typed in Tico (web, CLI, MCP, API): the request guard (backend/app.py) calls `refusal` on the buffered body of every
  task write route (`TASK_WRITES`) and of a message write into a task's conversation, with files (multipart: its text
  fields) or without, before any route runs. A name in front of a value counts only when the value looks generated
  (`generated`), so `max_tokens: 4096` is ordinary text. A key
  is refused with 422 `looks_like_key` and one line a bot can act on; the value is never repeated and the task is left
  unchanged at the same version, a hand-off (done with a note, an answer) included. The server keeps no request body
  in its logs (uvicorn's access log is off), and this runs before any route reads one.
- From Slack or mail (`OUTSIDE`): `threads.post` stores it with each key replaced by `MARK` (`masked`), and the
  author is told where it still is (secret_drop.COPY_ELSEWHERE).

Either leaves one audit row (`ACTION`): the task, the author and the rule, never a value, a hash or part of one.
Suspect-tier matches are out (spec): they stay as typed.
"""

import json
import re
from email import policy as email_policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import TYPE_CHECKING, Any, cast

from . import secret_drop

if TYPE_CHECKING:
    import sqlite3

ACTION = "secret_drop.task_text"
CODE = "looks_like_key"
REFUSED = "This looks like a key, so Tico didn't keep it: save it in Tools and refer to it by name."
MARK = "[key removed]"
# Where a write comes from when it was not typed in Tico: stored masked, never refused (the author is not here to
# retype it).
OUTSIDE = ("slack", "email", "mail")
METHODS = ("POST", "PATCH", "PUT")
# Every write under /api/v2/tasks says something on a task, except a file's or a page's own bytes (a file is not task
# text, spec Scope). Questions and messages carry a ❓'s line and its answer.
TASK_WRITES = re.compile(r"/api/v2/(?:tasks(?:/.*)?|questions(?:/.*)?|uploads/tasks)")
NOT_TEXT = re.compile(r"/api/v2/tasks/[^/]+/(?:files|pages)(?:/.*)?")
# A message write that is task text only when its thread is a task's conversation (`task_of_thread`).
# A message or a new task sent with files goes to /api/v2/uploads/... as multipart: its text fields are checked too.
THREAD_WRITES = re.compile(r"/api/v2/(?:uploads/)?threads/([^/]+)/messages")
MESSAGE_EDITS = re.compile(r"/api/v2/messages/([^/]+)")
TASK_ID = re.compile(r"/api/v2/tasks/([^/]+)")


# Rules that find a value by the word in front of it (`max_tokens: 4096`, `auth: OAuth2`, `hotkey: Ctrl-K`), not by its
# own shape. Task text is full of such pairs, so there the value must also look generated, unless the word names a
# password, which can be any string. `env` (an UPPER_CASE= line) keeps the detector's own test.
NAMED = ("key_value", "context_space", "bearer")
RANDOM = re.compile(r"(?=[A-Za-z]*[0-9])(?=[0-9]*[A-Za-z])[A-Za-z0-9]{8,}|(?=[^0-9]*[0-9])[0-9a-fA-F-]{32,}")


def generated(value: str) -> bool:
    """A value that reads as made by a machine: 8+ letters and digits run together, a UUID or long hex, or the suspect
    tier's own test (long, mixed characters). Words, numbers and dotted or dashed names (`--dur-fast`, `tasks:list:v2`)
    are not."""
    return bool(RANDOM.fullmatch(value)) or secret_drop._suspect(value)  # pyright: ignore[reportPrivateUsage]


def _kept(item: dict[str, Any]) -> bool:
    if item["tier"] != "certain":
        return False
    if item["rule"] not in NAMED or item.get("type") == "password":
        return True
    return generated(str(item.get("value") or ""))


def found(text: str) -> list[dict[str, Any]]:
    """The certain-tier keys in `text`: rule, start and end (offsets in the normalized text). Never the value."""
    return [{"rule": i["rule"], "start": i["start"], "end": i["end"]} for i in secret_drop.scan(text or "") if _kept(i)]


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Each string in a JSON body with the field it sits in (`note`, `answer.value`): every field, so a text field a
    route adds later is checked too. Keys of an object are names, not text."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in cast("dict[str, Any]", value).items():
            out.extend(_strings(v, f"{path}.{k}" if path else str(k)))
        return out
    if isinstance(value, list):
        out = []
        for v in cast("list[Any]", value):
            out.extend(_strings(v, path))
        return out
    return []


def scope(method: str, path: str) -> str | None:
    """What a request's path is, for the check: 'task' (a task write route), 'thread' or 'message' (task text only on a
    task's conversation), or None (not checked)."""
    if method not in METHODS:
        return None
    if TASK_WRITES.fullmatch(path):
        return None if NOT_TEXT.fullmatch(path) else "task"
    if THREAD_WRITES.fullmatch(path):
        return "thread"
    if MESSAGE_EDITS.fullmatch(path):
        return "message"
    return None


def _form_fields(raw: bytes, content_type: str) -> dict[str, list[str]]:
    """A multipart body's text fields by name (`text`, `title`, `body`): the files in it are not task text."""
    head = b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n"
    message = BytesParser(policy=email_policy.HTTP).parsebytes(head + raw)
    fields: dict[str, list[str]] = {}
    parts = cast("list[EmailMessage]", list(message.iter_parts())) if message.is_multipart() else []
    for part in parts:
        if part.get_filename() is not None:
            continue
        data: object = part.get_payload(decode=True)
        name = str(cast("object", part.get_param("name", header="content-disposition")) or "")
        if isinstance(data, bytes):
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields


def keys_in(raw: bytes, content_type: str = "") -> list[tuple[str, str]]:
    """(field, rule) for each certain-tier key in a JSON body or a multipart form's text fields. A body that is
    neither is checked as one text."""
    body: Any
    if content_type.lower().startswith("multipart/"):
        body = _form_fields(raw, content_type)
    else:
        try:
            body = json.loads(raw or b"null")
        except (ValueError, UnicodeDecodeError):
            body = raw.decode("utf-8", "replace") if raw else None
    hits: list[tuple[str, str]] = []
    for field, text in _strings(body):
        hits.extend((field, f["rule"]) for f in found(text))
    return hits


def task_of_thread(c: "sqlite3.Connection", thread_id: str) -> str | None:
    """The task whose conversation `thread_id` is, or a sub-thread of: its id, or None."""
    from . import threads

    thread = cast("dict[str, Any] | None", threads.thread_row(c, thread_id))  # pyright: ignore[reportUnknownMemberType]
    if not thread:
        return None
    root = cast("dict[str, Any]", threads.root_of(c, thread) or thread)  # pyright: ignore[reportUnknownMemberType]
    row = c.execute(
        "SELECT id FROM tasks WHERE conversation_id IN (?,?) LIMIT 1", (str(root.get("id") or ""), thread_id)
    ).fetchone()
    return str(row[0]) if row else None


def task_of_message(c: "sqlite3.Connection", message_id: str) -> str | None:
    row = c.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()
    return task_of_thread(c, str(row[0])) if row and row[0] else None


def target(c: "sqlite3.Connection", kind: str, path: str) -> str | None:
    """The task a checked write is on, or None when it is not task text (a message outside a task). A task route
    whose task is not found keeps the id it was sent with: the audit row still names what was written to."""
    if kind == "thread":
        m = THREAD_WRITES.fullmatch(path)
        return task_of_thread(c, m.group(1)) if m else None
    if kind == "message":
        m = MESSAGE_EDITS.fullmatch(path)
        return task_of_message(c, m.group(1)) if m else None
    m = TASK_ID.match(path)
    if not m or m.group(1) in ("move", "owner", "snooze", "dry-run"):
        return ""  # a create, a dry run or a many-task write: no one task yet
    tid = m.group(1)
    row = c.execute("SELECT id FROM tasks WHERE id=? OR CAST(number AS TEXT)=? LIMIT 1", (tid, tid)).fetchone()
    return str(row[0]) if row else tid


def audit(c: "sqlite3.Connection", author: str, task: str, rules: list[str], outcome: str) -> None:
    """One row per refusal or mark: the task, the author and the rule(s). No value, no hash, no partial characters."""
    from .store import H

    H.event(c, author, ACTION, task, {"outcome": outcome, "rules": sorted(set(rules))})  # pyright: ignore[reportUnknownMemberType]


def refusal(store: Any, author: str, method: str, path: str, raw: bytes, content_type: str = "") -> None:
    """The request guard's check: raises 422 `looks_like_key` (and writes its audit row, in its own transaction) when
    a task text write holds a certain-tier key. Nothing else is read or written; the body is never echoed."""
    from .store import Problem

    kind = scope(method, path)
    if not kind:
        return
    hits = keys_in(raw, content_type)
    if not hits:
        return
    with store.transaction() as c:
        task = target(c, kind, path)
        if task is None:
            return  # a message outside a task's conversation: the Tools chat's own backstop covers its case
        audit(c, author, task, [rule for _, rule in hits], "refused")
    raise Problem(CODE, REFUSED, 422, extra={"fields": sorted({field for field, _ in hits if field})})


def masked(text: str) -> tuple[str, list[str]]:
    """`text` with each certain-tier key replaced by MARK, and the rules that matched. The text is normalized first
    (the detector's offsets are in the normalized text) only when a key is found."""
    hits = found(text)
    if not hits:
        return text, []
    flat = secret_drop.normalize(text)
    out: list[str] = []
    at = 0
    for hit in hits:
        out.append(flat[at : hit["start"]])
        out.append(MARK)
        at = hit["end"]
    out.append(flat[at:])
    return "".join(out), [h["rule"] for h in hits]


def mask_outside(c: "sqlite3.Connection", thread: dict[str, Any] | None, author: str, text: str, via: str) -> str:
    """`threads.post`'s step for text from outside Tico (`OUTSIDE`) on a task's conversation: stored with each key as
    MARK, one audit row. Any other text is returned as it came."""
    if str(via or "").lower() not in OUTSIDE or not text or not thread:
        return text
    task = task_of_thread(c, str(thread.get("id") or ""))
    if task is None:
        return text
    out, rules = masked(text)
    if rules:
        audit(c, author, task, rules, "masked")
    return out


def told(via: str) -> str:
    """What the author of a masked message is told: where the key still is (the Tools chat's own line)."""
    where = secret_drop.WHERE.get(str(via or "").lower(), "the message you sent")
    return secret_drop.COPY_ELSEWHERE.format(where=where)
