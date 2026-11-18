# pyright: strict
"""Keys kept out of task text (spec "Keep keys out of task comments and bot memory", task E-607).

Every task text write runs the key detector's certain-tier rules (backend/secret_drop.py, backend/secret_rules.json)
before anything is stored, from any client, person or bot:

- Typed in Tico (web, CLI, MCP, API): the request guard (backend/app.py) calls `refusal` on the buffered body of every
  task write route (`TASK_WRITES`) and of a message write into a task's conversation, with files (multipart: its text
  fields) or without, before any route runs. A name in front of a value does not count when every piece
  of the value reads as ordinary (`ordinary`) or it is an id of this team's (`known_id`), so `max_tokens: 4096` and
  `token: <a task's id>` are ordinary text. A key
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
    from collections.abc import Callable

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
# own shape. Task text is full of such pairs, so there a value whose every piece reads as ordinary (a word, a number,
# a name with a version, short hex) is not a key. A password word, and `env` (an UPPER_CASE= line), keep the detector's
# own test: a password can be any string.
NAMED = ("key_value", "context_space", "bearer")
PIECES = re.compile(r"[-_./:@+,]+")
# A word (letters, see `wordlike`), a number, a word with a short number (OAuth2, v2, build2026), or 3 characters.
# Short hex is a piece of a name (sha1-30c1714c9), never the whole value: `token=a1b2c3d4` is a key.
PLAIN = re.compile(r"[A-Za-z]+|[0-9]+|[A-Za-z]+[0-9]{1,4}|.{1,3}")
HUMPS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
# The 300 letter pairs English words use most (from a public word list). A run of letters is a word when at least
# 80% of its pairs are among them: random letters (a letters-only key) seldom are. Measured: about 97% of random
# 12-letter values and 94% of 8-letter ones fail it, against about 1% of dictionary words and no everyday ones.
PAIRS = frozenset(
    (
        "ab ac ad ae af ag ah ai ak al am an ap ar as at au av aw ay az ba bb be bi bl bo br bs bu ca cc ce ch ci ck "
        "cl co cr cs ct cu cy da dd de dg di dl do dr ds du dy ea eb ec ed ee ef eg eh ei el em en eo ep eq er es et "
        "eu ev ew ex ey fa fe ff fi fl fo fr ft fu ga ge gg gh gi gl gn go gr gs gu gy ha he hi ho hr hs ht hu hy ia "
        "ib ic id ie if ig ik il im in io ip ir is it iu iv iz ja je jo ju ka ke ki kl kn ks la ld le li lk ll lm lo "
        "ls lt lu lv ly ma mb me mi mm mo mp ms mu na nc nd ne nf ng ni nk nl nn no ns nt nu nv ny oa ob oc od oe of "
        "og oi ok ol om on oo op or os ot ou ov ow ox oy pa pe ph pi pl po pp pr ps pt pu qu ra rb rc rd re rf rg ri "
        "rk rl rm rn ro rp rr rs rt ru rv ry sa sc se sh si sk sl sm sn so sp sq ss st su sw sy ta tc te th ti tl to "
        "tr ts tt tu tw ty ua ub uc ud ue uf ug ui ul um un up ur us ut va ve vi vo wa we wh wi wn wo wr ws xe xi xp "
        "xt ya ye yi yl ym yn yp ys za ze zi "
    ).split()
)


def wordlike(letters: str) -> bool:
    """Whether a run of letters reads as words: each camelCase hump of 6+ letters, and the humps together, mostly use
    common letter pairs. Short all-caps humps (XML, ID) are acronyms and left out of the pairs; changing case
    (qZqZqZ, kQwerTyuIopz) does not make a key read as a word."""
    humps = HUMPS.findall(letters)
    # Words in camelCase have long humps (GitHub, McDonald, XMLHttpRequest); random mixed case breaks into many tiny
    # ones (q, Zq, kQ) and runs of capitals. Two tiny mixed humps, or a third run of capitals, is not a word: measured,
    # about 94% of random 12-letter mixed-case values fail here or below, and no product name tried did.
    tiny = sum(1 for h in humps if len(h) <= 2 and not h.isupper())
    if tiny + max(0, sum(1 for h in humps if h.isupper()) - 2) >= 2:
        return False
    good = total = 0
    for hump in humps:
        low = hump.lower()
        if hump.isupper() and len(hump) < 6:
            continue
        hits = sum(low[i : i + 2] in PAIRS for i in range(len(low) - 1))
        if len(low) >= 6 and hits < 0.8 * (len(low) - 1):
            return False
        good += hits
        total += len(low) - 1
    return total < 5 or good >= 0.8 * total


SHORT_HEX = re.compile(r"[0-9a-fA-F]{1,12}")
# Tico's own ids, which notes name all the time. A file id has its own shape; a UUID can be a key too (a Heroku key
# is one), so it is ordinary only when it names a task, a chat, a message, a bot, a person or a run here (`known`).
FILE_ID = re.compile(r"file-[0-9a-f]{24}")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def ordinary(value: str) -> bool:
    """Whether a value found by the word in front of it reads as everyday text, not a key (see PLAIN)."""
    if FILE_ID.fullmatch(value):
        return True
    if UUID.fullmatch(value):
        return False
    pieces = [p for p in PIECES.split(value) if p]
    return bool(pieces) and all(_plain(p, len(pieces) > 1) for p in pieces)


def _plain(piece: str, in_name: bool) -> bool:
    if in_name and SHORT_HEX.fullmatch(piece):
        return True
    if not PLAIN.fullmatch(piece):
        return False
    letters = piece.rstrip("0123456789")
    return wordlike(letters) if letters.isalpha() else True


def known_id(c: "sqlite3.Connection", value: str) -> bool:
    """Whether a UUID is the id of something in this team, so a note naming it is not handing out a key."""
    row = c.execute(
        "SELECT 1 FROM tasks WHERE id=? OR conversation_id=? UNION ALL SELECT 1 FROM conversations WHERE id=? "
        "UNION ALL SELECT 1 FROM messages WHERE id=? UNION ALL SELECT 1 FROM bots WHERE id=? "
        "UNION ALL SELECT 1 FROM humans WHERE id=? UNION ALL SELECT 1 FROM attempts WHERE id=? LIMIT 1",
        (value,) * 7,
    ).fetchone()
    return row is not None


def _kept(item: dict[str, Any], known: "Callable[[str], bool] | None") -> bool:
    if item["tier"] != "certain":
        return False
    if item["rule"] not in NAMED or item.get("type") == "password":
        return True
    value = str(item.get("value") or "")
    if known and UUID.fullmatch(value) and known(value):
        return False
    return not ordinary(value)


def found(text: str, known: "Callable[[str], bool] | None" = None) -> list[dict[str, Any]]:
    """The certain-tier keys in `text`: rule, start and end (offsets in the normalized text). Never the value.
    `known` says whether a UUID names something here (`known_id`)."""
    return [
        {"rule": i["rule"], "start": i["start"], "end": i["end"]}
        for i in secret_drop.scan(text or "")
        if _kept(i, known)
    ]


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


def keys_in(raw: bytes, content_type: str = "", known: "Callable[[str], bool] | None" = None) -> list[tuple[str, str]]:
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
        hits.extend((field, f["rule"]) for f in found(text, known))
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
        hits = keys_in(raw, content_type, lambda value: known_id(c, value))  # a task's own id after `token:`
        if not hits:
            return
        audit(c, author, task, [rule for _, rule in hits], "refused")
    raise Problem(CODE, REFUSED, 422, extra={"fields": sorted({field for field, _ in hits if field})})


def masked(text: str, known: "Callable[[str], bool] | None" = None) -> tuple[str, list[str]]:
    """`text` with each certain-tier key replaced by MARK, and the rules that matched. The text is normalized first
    (the detector's offsets are in the normalized text) only when a key is found."""
    hits = found(text, known)
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
    out, rules = masked(text, lambda value: known_id(c, value))
    if rules:
        audit(c, author, task, rules, "masked")
    return out


def told(via: str) -> str:
    """What the author of a masked message is told: where the key still is (the Tools chat's own line)."""
    where = secret_drop.WHERE.get(str(via or "").lower(), "the message you sent")
    return secret_drop.COPY_ELSEWHERE.format(where=where)
