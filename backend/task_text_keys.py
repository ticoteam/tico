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
  That covers a message into a task's conversation by any route (threads, conversations, POST /api/v2/messages
  naming the task), what a bot says with a page or a file (`NOTE_FIELDS`), a many-task change's `why`, and a task
  made from a message (`refuse_text`: the route copies the message's text).
- From Slack or mail (`OUTSIDE`): `threads.post` stores it with each key replaced by `MARK` (`masked`), and the
  author is told where it still is (secret_drop.COPY_ELSEWHERE), on a read-only link too. A Slack edit is masked
  before its message and revision are written (threads.apply_revision), and a Slack event before it is queued
  (slack_threads.Bridge._queue): a stalled or suspended link keeps the queue, never the key.
- A bot's turn reply (execution.complete): a key no grant scrubbed is kept as a mark (`mask_on_task`), the rest of
  the reply as written.

Either leaves one audit row (`ACTION`): the task, the author and the rule, never a value, a hash or part of one.
Suspect-tier matches are out (spec): they stay as typed.
"""

import json
import re
import unicodedata
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
# A task made from a message carries its title here (the message's own text is checked by the route,
# thread_tasks.message_task, which copies it: `refuse_text`). A many-task change (task_bulk_routes), a Needs you batch's
# answers (batch_routes) and a file's note sent as a streamed form (media.task_attach) are checked by their routes,
# which know the tasks: `refuse_text`.
TASK_WRITES = re.compile(r"/api/v2/(?:tasks(?:/.*)?|questions(?:/.*)?|uploads/tasks|threads/messages/[^/]+/task)")
# A file's or a page's own bytes are not task text; what the bot says with it (`NOTE_FIELDS`) is.
NOT_TEXT = re.compile(r"/api/v2/tasks/[^/]+/(?:files|pages)(?:/.*)?")
NOTE_FIELDS = ("note", "changed", "answered")
# A message write that is task text only when its thread is a task's conversation (`task_of_thread`).
# A message or a new task sent with files goes to /api/v2/uploads/... as multipart: its text fields are checked too.
# A conversation's id is its thread's.
THREAD_WRITES = re.compile(r"/api/v2/(?:(?:uploads/)?threads|conversations)/([^/]+)/messages")
MESSAGE_EDITS = re.compile(r"/api/v2/messages/([^/]+)")
# A message sent by its recipient: task text when its body names a task's conversation, a message there it answers,
# or a task in its refs (`_send_target`).
MESSAGE_SENDS = re.compile(r"/api/v2/messages")
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
    (qZqZqZ, kQwerTyuIopz) does not make a key read as a word.

    This is a likelihood, not a proof: no test of letters alone tells every random value from a word. The bar was
    decided on the task at QA (2026-11-18): keep everyday notes, and refuse about 94% of random 12-letter mixed-case
    values and over 99% at 16 letters; a value with a digit or symbol never reaches here. The test pins those rates."""
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
# More digits than a date, a version or a count holds (`max_tokens: 4096`, `key: 20261118`, `sha1-30c1714c9`): a long
# number, or hex or digit groups, is generated (Review on c4b9a23).
DIGITS_MAX = 8
# A date or a time is ordinary however many digits it has (`token_expires_at: 2026-11-18T10:00:00Z`).
DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
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
    if DATE.fullmatch(value):
        return True
    pieces = [p for p in PIECES.split(value) if p]
    if not pieces or not all(_plain(p, len(pieces) > 1) for p in pieces):
        return False
    if sum(ch.isdigit() for ch in value) > DIGITS_MAX:
        return False
    # Short pieces of letters and digits (`ab1_cd2_ef3`, `x7k-m2p-q9z`) and hex groups (`a1b2c3-d4e5f6`) are a
    # generated value cut into groups; a name has one at most (`gpt-4o-mini`, `tasks:list:v2`, `sha1-30c1714c9`).
    coded = [p for p in pieces if _coded(p)]
    return len(coded) < 3 and sum(1 for p in coded if len(p) > 3) < 2


def _coded(piece: str) -> bool:
    """A piece with both letters and digits that is short (3 characters or fewer) or all hex."""
    mixed = any(ch.isdigit() for ch in piece) and any(ch.isalpha() for ch in piece)
    return mixed and (len(piece) <= 3 or bool(SHORT_HEX.fullmatch(piece)))


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
    text = text or ""
    # A mark Tico left already is not a key (`key removed]` reads as a named value): text masked once, by the Slack
    # queue, is carried in and posted unchanged.
    marks = [m.span() for m in re.finditer(re.escape(MARK), secret_drop.normalize(text))] if MARK in text else []
    return [
        {"rule": i["rule"], "start": i["start"], "end": i["end"]}
        for i in secret_drop.scan(text)
        if _kept(i, known) and not any(s < i["end"] and i["start"] < e for s, e in marks)
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
    """What a request's path is, for the check: 'task' (a task write route), 'notes' (a page or a file on a task: only
    what is said with it, `NOTE_FIELDS`), 'thread', 'message' or 'send' (task text only on a task's conversation), or
    None (not checked)."""
    if method not in METHODS:
        return None
    if TASK_WRITES.fullmatch(path):
        return "notes" if NOT_TEXT.fullmatch(path) else "task"
    if THREAD_WRITES.fullmatch(path):
        return "thread"
    if MESSAGE_EDITS.fullmatch(path):
        return "message"
    if MESSAGE_SENDS.fullmatch(path):
        return "send"
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


def _body(raw: bytes, content_type: str) -> Any:
    """A JSON body, a multipart form's text fields, or the text of a body that is neither."""
    if content_type.lower().startswith("multipart/"):
        return _form_fields(raw, content_type)
    try:
        return json.loads(raw or b"null")
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace") if raw else None


def keys_in(
    raw: bytes,
    content_type: str = "",
    known: "Callable[[str], bool] | None" = None,
    fields: "tuple[str, ...] | None" = None,
) -> list[tuple[str, str]]:
    """(field, rule) for each certain-tier key in a JSON body or a multipart form's text fields. A body that is
    neither is checked as one text. With `fields`, only those top-level fields are read (a page's or a file's note)."""
    body = _body(raw, content_type)
    if fields is not None:
        body = (
            {k: v for k, v in cast("dict[str, Any]", body).items() if k in fields} if isinstance(body, dict) else None
        )
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


def _send_target(c: "sqlite3.Connection", body: Any) -> str | None:
    """The task a sent message (POST /api/v2/messages) is task text on: the task whose conversation its
    `conversation_id` is, or whose conversation holds the message it answers (`in_reply_to`), or the task its refs
    name (`refs.task`, `refs.task_id`). None when it names none."""
    from .store import H

    if not isinstance(body, dict):
        return None
    sent = cast("dict[str, Any]", body)
    conversation, answers = str(sent.get("conversation_id") or ""), str(sent.get("in_reply_to") or "")
    task = (task_of_thread(c, conversation) if conversation else None) or (
        task_of_message(c, answers) if answers else None
    )
    if task:
        return task
    named = H.refs_task_id(sent.get("refs"))  # pyright: ignore[reportUnknownMemberType]
    if not named:
        return None
    row = c.execute("SELECT id FROM tasks WHERE id=? OR CAST(number AS TEXT)=? LIMIT 1", (named, named)).fetchone()
    return str(row[0]) if row else None


def target(c: "sqlite3.Connection", kind: str, path: str, body: Any = None) -> str | None:
    """The task a checked write is on, or None when it is not task text (a message outside a task). A task route
    whose task is not found keeps the id it was sent with: the audit row still names what was written to."""
    if kind == "send":
        return _send_target(c, body)
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
    fields = NOTE_FIELDS if kind == "notes" else None
    hits = keys_in(raw, content_type, fields=fields)
    if not hits:
        return
    with store.transaction() as c:
        task = target(c, kind, path, _body(raw, content_type) if kind == "send" else None)
        if task is None:
            return  # a message outside a task's conversation: the Tools chat's own backstop covers its case
        # A task's own id after `token:` is ordinary.
        hits = keys_in(raw, content_type, lambda value: known_id(c, value), fields)
        if not hits:
            return
        audit(c, author, task, [rule for _, rule in hits], "refused")
    raise Problem(CODE, REFUSED, 422, extra={"fields": sorted({field for field, _ in hits if field})})


def refuse_text(store: Any, author: str, task: "str | list[str]", texts: dict[str, str]) -> None:
    """`refusal` for task text a route checks itself rather than the request guard: a task made from a message copies
    that message, a many-task change says its `why` on every task it selects, a file's note sent as a streamed form.
    422 `looks_like_key` and an audit row for each task the text was going on (`task`: one, or the tasks a many-task
    change selected), before anything is stored."""
    from .store import Problem

    if not any(found(t) for t in texts.values()):
        return
    with store.transaction() as c:
        hits = [(f, h["rule"]) for f, t in texts.items() for h in found(t, lambda value: known_id(c, value))]
        if not hits:
            return
        for each in ([task] if isinstance(task, str) else task) or [""]:
            audit(c, author, each, [rule for _, rule in hits], "refused")
    raise Problem(CODE, REFUSED, 422, extra={"fields": sorted({field for field, _ in hits})})


def _spans(text: str) -> tuple[str, list[tuple[int, int]]] | None:
    """`secret_drop.normalize(text)` and, for each of its characters, the span of `text` it came from. A piece is a
    character with the combining marks after it, joined to the piece before it when NFKC composes across the two
    (Hangul conjoining jamo, as macOS writes Korean file names: U+1100 U+1161 become one syllable). None when that
    still does not give the same text: the caller keeps the whole normalized text then."""
    starts = [i for i, ch in enumerate(text) if i == 0 or not unicodedata.combining(ch)]
    groups: list[list[int]] = []
    for start, end in zip(starts, [*starts[1:], len(text)], strict=True):
        if groups:
            a, b = text[groups[-1][0] : groups[-1][1]], text[start:end]
            if secret_drop.normalize(a) + secret_drop.normalize(b) != secret_drop.normalize(a + b):
                groups[-1][1] = end
                continue
        groups.append([start, end])
    flat: list[str] = []
    origin: list[tuple[int, int]] = []
    for start, end in groups:
        out = secret_drop.normalize(text[start:end])
        flat.append(out)
        origin.extend([(start, end)] * len(out))
    joined = "".join(flat)
    return (joined, origin) if joined == secret_drop.normalize(text) else None


def masked(text: str, known: "Callable[[str], bool] | None" = None) -> tuple[str, list[str]]:
    """`text` with each certain-tier key replaced by MARK, and the rules that matched. Only the key's own characters
    change: the detector's offsets are in the normalized text, so each is mapped back to the characters it came from
    (`_spans`) and the rest stays as written (an ellipsis, a superscript, a zero-width joiner). Text whose
    normalization cannot be mapped back is kept normalized, as before."""
    hits = found(text, known)
    if not hits:
        return text, []
    mapped = _spans(text)
    cuts: list[tuple[int, int]]
    if mapped is None:
        flat = secret_drop.normalize(text)
        cuts = [(int(h["start"]), int(h["end"])) for h in hits]
    else:
        flat, origin = text, mapped[1]
        cuts = [(origin[int(h["start"])][0], origin[int(h["end"]) - 1][1]) for h in hits if h["end"] > h["start"]]
    out: list[str] = []
    at: int = 0
    for start, end in cuts:
        if start < at:  # two hits in one source character: one mark covers both
            continue
        out.append(flat[at:start])
        out.append(MARK)
        at = end
    out.append(flat[at:])
    return "".join(out), [h["rule"] for h in hits]


def mask_on_task(c: "sqlite3.Connection", task: str | None, author: str, text: str) -> str:
    """`text` with each certain-tier key as MARK and one audit row, when it goes on `task`; as it came otherwise. For
    text no one types into Tico's request guard: a bot's turn reply (execution.complete), Slack's queue and edits."""
    if not task or not text:
        return text
    out, rules = masked(text, lambda value: known_id(c, value))
    if rules:
        audit(c, author, task, rules, "masked")
    return out


def mask_outside(c: "sqlite3.Connection", thread: dict[str, Any] | None, author: str, text: str, via: str) -> str:
    """`threads.post`'s step for text from outside Tico (`OUTSIDE`) on a task's conversation: stored with each key as
    MARK, one audit row. Any other text is returned as it came."""
    if str(via or "").lower() not in OUTSIDE or not text or not thread:
        return text
    return mask_on_task(c, task_of_thread(c, str(thread.get("id") or "")), author, text)


def told(via: str) -> str:
    """What the author of a masked message is told: where the key still is (the Tools chat's own line)."""
    where = secret_drop.WHERE.get(str(via or "").lower(), "the message you sent")
    return secret_drop.COPY_ELSEWHERE.format(where=where)
