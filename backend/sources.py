"""Who wrote a line: the one rule every surface draws its label from (docs/threads.md, "Who wrote it").

`keeper` (hubdb.KEEPER) is the author Tico's server writes as when no person or bot did. It is not a process or a bot,
so a line written as keeper says what made it through `refs.source` (on a message) or `data.source` (on a task's
history row): a built-in's key, `automation:<id>`, or `system:*`. A keeper line with a task in its refs is a task
update. Anything else keeper wrote is a plain system line.

This module changes what people see only. Who wakes a bot, what is delivered and the stored actor stay as they are.
"""
import functools
import json
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar

KEEPER = "keeper"
ASSISTANT_FOR = "assistant-for"          # refs.source of a keeper message the Assistant wrote for a person
TASK_UPDATE = "Task update"
ASSISTANT_DEFAULT = "Assistant"

# The built-in automations a keeper line can name: key -> display name. A write names its key in refs.source.
BUILTINS = {
    "follow-up-timer": "Follow-up timer",
    "earlier-messages": "Earlier messages",
    "task-roundup": "Task roundup",
    "stuck-check": "Stuck check",
    "rule-check": "Rule check",
    "check-health": "Health check",
    "maintenance": "Maintenance",
}

KINDS = ("person", "bot", "assistant_for", "task_update", "builtin", "automation", "system")

_scope = ContextVar("tico_source_scope", default=None)
_assistant = {"name": ASSISTANT_DEFAULT}


def set_assistant_name(name):
    """The install's assistant name (settings.assistant_name), read by every label. Set once at startup."""
    _assistant["name"] = str(name or "").strip() or ASSISTANT_DEFAULT


def assistant_name():
    return _assistant["name"]


@contextmanager
def writing_as(key):
    """Every keeper write inside this block says `key` is what made it (`stamp`, `stamp_data`). A block inside a block
    names its own key. The key is a BUILTINS key, `automation:<id>` or `system:*`."""
    token = _scope.set(key)
    try:
        yield
    finally:
        _scope.reset(token)


def scope():
    """The key the current keeper writes name, or None."""
    return _scope.get()


def scoped(key):
    """A decorator: every keeper write the function makes says `key` (see writing_as)."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with writing_as(key):
                return fn(*args, **kwargs)
        return inner
    return wrap


def automation_key(automation_id):
    return "automation:" + str(automation_id)


def stamp(refs, actor):
    """A message's refs with `source` set to the scope's key, when keeper writes it inside a scope and it names none.
    Refs are returned as they were otherwise."""
    key = _scope.get()
    if actor != KEEPER or not key:
        return refs
    refs = refs if isinstance(refs, dict) else {}
    return refs if "source" in refs else {**refs, "source": key}


def stamp_data(data, actor):
    """A task history row's `data` with `source` set the same way. Data that is not a dict or None stays as it is."""
    key = _scope.get()
    if actor != KEEPER or not key or (data is not None and not isinstance(data, dict)):
        return data
    data = data or {}
    return data if "source" in data else {**data, "source": key}


def _dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            found = json.loads(value)
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}
    return {}


def _named(kind, name="", link=None):
    return {"kind": kind, "name": name or "", "link": link}


def _assistant_for(names, refs, assistant):
    who = str(refs.get("for") or "")
    parts = (names.get(who) or "").split()
    return _named("assistant_for", f"{parts[0]}'s {assistant}" if parts else assistant)


def _source(names, sender, refs, assistant, *, message):
    sender = str(sender or "")
    refs = _dict(refs)
    key = refs.get("source") if isinstance(refs.get("source"), str) else ""
    if sender.startswith("human:"):
        return _named("person", names.get(sender, ""))
    if key == ASSISTANT_FOR or (sender.startswith("bot:") and str(refs.get("for") or "").startswith("human:")):
        return _assistant_for(names, refs, assistant)
    if sender.startswith("bot:"):
        return _named("bot", names.get(sender) or sender.split(":", 1)[1])
    if sender == KEEPER:
        if key.startswith("automation:"):
            aid = key.split(":", 1)[1]
            return _named("automation", names.get(key) or "Automation", f"#/automations/{aid}")
        if key in BUILTINS:
            return _named("builtin", BUILTINS[key])
        if key.startswith("system:"):
            return _named("system")
        if message and refs.get("task"):
            return _named("task_update", TASK_UPDATE)
    return _named("system")


def message_source(names, message, assistant=None):
    """The `source` of a message row (sender_id, refs). `names` maps actor ids (`human:`, `bot:`), `automation:<id>`
    and a person's id (`refs.for`) to display names; a name left out reads as blank, which is how a person the reader
    may not see is hidden."""
    return _source(names, message.get("sender_id"), message.get("refs"),
                   assistant_name() if assistant is None else assistant, message=True)


def actor_source(names, actor, data=None, assistant=None):
    """The `source` of a row that has an actor and no message: a task's history entry (its `data`) or an event (its
    `detail`). A keeper row is a system line unless it names a built-in, an automation or a system key."""
    return _source(names, actor, data, assistant_name() if assistant is None else assistant, message=False)


def source_of(names, row, assistant=None):
    """The `source` of any row this module labels: a message (it has `sender_id`) or an actor's row (`actor`, with
    `data` or `detail`). One dict in, one dict out, with `names` looked up by the caller once for the whole page."""
    if "sender_id" in row:
        return message_source(names, row, assistant)
    return actor_source(names, row.get("actor") or "", row.get("data", row.get("detail")), assistant)


def actors_of(rows):
    """Every actor id and automation key a page of rows needs a name for (`lookup` reads them)."""
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids.add(str(row.get("sender_id") or row.get("actor") or ""))
        refs = _dict(row.get("refs", row.get("data", row.get("detail"))))
        if isinstance(refs.get("for"), str):
            ids.add(refs["for"])
        key = refs.get("source")
        if isinstance(key, str) and key.startswith("automation:"):
            ids.add(key)
    return {i for i in ids if i.startswith(("human:", "bot:", "automation:"))}


AUTOMATION_KEY = re.compile(r"^automation:(.+)$")


def lookup(c, ids):
    """{actor id or automation key: display name} for these ids. A name the store does not know is left out."""
    from .names import lookup as actor_names
    ids = set(ids)
    out = actor_names(c, {i for i in ids if i.startswith(("human:", "bot:"))}) if ids else {}
    found = sorted({m.group(1) for m in (AUTOMATION_KEY.match(i) for i in ids) if m})
    for start in range(0, len(found), 500):
        part = found[start:start + 500]
        try:
            rows = c.execute("SELECT id, name FROM automations WHERE id IN (%s)" % ",".join("?" * len(part)),
                             part).fetchall()
        except sqlite3.Error:     # an install without the automations table labels them plainly
            rows = []
        for row in rows:
            out[automation_key(row[0])] = (row[1] or "").strip()
    return out


def attach(c, rows):
    """Set `source` on each dict row (a message, or an actor's row), one names lookup for the whole list."""
    rows = [r for r in rows if isinstance(r, dict)]
    names = lookup(c, actors_of(rows))
    for row in rows:
        row["source"] = source_of(names, row)
    return rows
