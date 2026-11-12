# pyright: strict
"""The few fields a run's status line reads from a big output event, kept beside the event when it arrives.

The status line under a chat (thread_bot_status._step) decides its one word from a running turn's newest STEP_EVENTS
delta, message and tool events. A tool event can carry a large output and a message a long text, and decoding those
in full to read a status and a label cost as much as the outputs weighed. So when an event's stored payload is longer
than SMALL characters, `record` keeps its brief in `attempt_event_briefs` (its migration
20261112081510_attempt_event_briefs): what the replay (turns.reply_parts) reads,
with a text reduced to whether it is blank and a digest of its stripped words. The status line reads the brief instead
of the payload; a smaller event is cheap to decode and gets none.

A brief only ever describes the payload it was made from: rewriting an event's payload (a scrubbed credential, a held
reply wiped, a revoked page) or deleting the event drops its brief, and a row with no brief is read from its payload,
as one written before briefs existed is. So a brief can be missing, never stale.

A tool's label is kept as `tool_label` made it when the event arrived: after a release that changes the wording, a run
already going shows the old label for that call until its next one.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any, cast

Brief = dict[str, Any]

SMALL = 900  # payload characters: a smaller event is decoded whole; 60 of them stay under 64 KiB
LABEL_CHARS = 400
KEY_CHARS = 200
STATUS_CHARS = 64
KINDS = ("delta", "message", "tool")


def digest(text: str) -> str:
    """The stripped text's fingerprint: two texts the replay finds equal once stripped share it."""
    return hashlib.blake2b(text.strip().encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


def info(kind: str, payload: object, keep_text: bool = False) -> Brief | None:
    """What the replay reads from one event, or None when a field is too odd to keep (the payload is read instead).

    delta: {"t": 1} for a thought, else {"b": blank, "h": digest}; message: {"b", "h"}; tool: {"s": its status as the
    replay compares it, "i": its item id, "p": 1 for a subagent's call, "l": its label}. `keep_text` adds the text
    itself ("x"), for an event read whole."""
    from .turns import tool_label  # pyright: ignore[reportUnknownVariableType]  (turns is not typed yet)

    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    if kind == "delta" and fields.get("delta_kind") == "thought":
        return {"t": 1}
    if kind in ("delta", "message"):
        text = str(fields.get("text", ""))
        out: Brief = {"b": 0 if text.strip() else 1}
        if keep_text:
            out["x"] = text
        elif not out["b"]:
            out["h"] = digest(text)
        return out
    if kind != "tool":
        return None
    key = fields.get("item_id")
    if isinstance(key, str) and len(key) > KEY_CHARS or not isinstance(key, (str, int, float, bool, type(None))):
        return None
    label = str(cast(Any, tool_label)(fields))
    if len(label) > LABEL_CHARS:
        return None
    status = str(fields.get("status") or fields.get("state") or fields.get("phase") or "").lower()
    called: Brief = {"s": status[:STATUS_CHARS], "i": key, "l": label}
    if fields.get("parent_id"):
        called["p"] = 1
    return called


def record(c: sqlite3.Connection, aid: str, events: Iterable[tuple[int, str, str, object]]) -> None:
    """Keep the brief of each big delta, message or tool event just stored: `events` is [(seq, kind, payload as
    stored, payload as a value)]."""
    rows: list[tuple[str, int, str]] = []
    for seq, kind, stored, value in events:
        if kind not in KINDS or len(stored) <= SMALL:
            continue
        brief = info(kind, value)
        if brief is not None:
            rows.append((aid, seq, json.dumps(brief, separators=(",", ":"))))
    if rows:
        c.executemany("INSERT OR REPLACE INTO attempt_event_briefs(attempt_id,seq,brief) VALUES(?,?,?)", rows)
