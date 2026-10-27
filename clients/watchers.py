"""Watchers: a program in a bot's repository that the runner runs on a schedule with no model (docs/watchers.md).

`bot.yaml` (older: `employee.yaml`) declares them:

    watchers:
      - name: hq-tickets
        run: software/hq-tickets watch
        every: 5m
        timeout: 60s          # optional; 60s by default, 5m at most

This module is the one place the declaration is read, shared by the runner and by `hub` checks, and the one place the
output protocol is parsed. It does no I/O of its own.

A watcher prints ordinary lines (a log) and lines that start with `tico-event ` followed by one JSON object:

    tico-event {"op": "task", "key": "hq:TK-AB12CD34", "title": "Support: ...", "body": "..."}
    tico-event {"op": "comment", "key": "hq:TK-AB12CD34", "ref": "msg:7", "text": "..."}
    tico-event {"op": "done", "key": "hq:TK-AB12CD34", "note": "closed at HQ"}

`task` opens a task for the bot if the key has none (once, however often it is printed). `comment` adds a note to that
task and wakes the bot; when the task is finished or closed it opens a new one from `title` and `body`. `done` tells
the bot the thing ended elsewhere. `ref` makes a `comment` idempotent.
"""
import json
import re
import shlex
from pathlib import PurePosixPath

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DURATION = re.compile(r"^\s*(\d{1,5})\s*([smh])\s*$")
FIELDS = {"name", "run", "every", "timeout", "enabled"}
MIN_EVERY, MAX_EVERY = 60, 24 * 3600
DEFAULT_TIMEOUT, MAX_TIMEOUT = 60, 300
MAX_WATCHERS = 10
EVENT_PREFIX = "tico-event "
OPS = ("task", "comment", "done")
MAX_EVENTS = 50
LIMITS = {"key": 200, "ref": 100, "title": 300, "body": 20_000, "text": 8_000, "note": 2_000}


def seconds(value, default=None):
    """`30s`, `5m`, `2h` (or a bare number of seconds) as seconds."""
    if value in (None, "") and default is not None:
        return default
    if isinstance(value, bool):
        raise ValueError("a duration is like 5m")
    if isinstance(value, (int, float)):
        return int(value)
    found = DURATION.match(str(value))
    if not found:
        raise ValueError("a duration is like 30s, 5m or 2h")
    return int(found.group(1)) * {"s": 1, "m": 60, "h": 3600}[found.group(2)]


def parse(entries):
    """The declared watchers as [{name, argv, every, timeout}], or ValueError naming the first thing wrong.
    `argv[0]` is a path inside the bot's repository; it is never resolved against the system's PATH."""
    if entries in (None, []):
        return []
    if not isinstance(entries, list) or len(entries) > MAX_WATCHERS:
        raise ValueError(f"watchers is a list of at most {MAX_WATCHERS}")
    out, names = [], set()
    for index, entry in enumerate(entries, 1):
        where = f"watcher {index}"
        if not isinstance(entry, dict) or set(entry) - FIELDS:
            raise ValueError(f"{where} has fields other than {', '.join(sorted(FIELDS))}")
        name = str(entry.get("name") or "")
        if not NAME.match(name) or name in names:
            raise ValueError(f"{where}: name is lowercase words with hyphens, once")
        names.add(name)
        where = f"watcher {name}"
        try:
            argv = shlex.split(str(entry.get("run") or ""))
        except ValueError:
            raise ValueError(f"{where}: run has an unclosed quote") from None
        if not argv or len(argv) > 20 or len(str(entry.get("run"))) > 300:
            raise ValueError(f"{where}: run is a file in the repository and its arguments")
        path = PurePosixPath(argv[0])
        if path.is_absolute() or ".." in path.parts or "\\" in argv[0] or len(path.parts) < 1:
            raise ValueError(f"{where}: run starts with a path inside the repository, such as software/{name}")
        try:
            every = seconds(entry.get("every"))
            timeout = seconds(entry.get("timeout"), DEFAULT_TIMEOUT)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        if every is None or not MIN_EVERY <= every <= MAX_EVERY:
            raise ValueError(f"{where}: every is between 1m and 24h")
        if not 1 <= timeout <= MAX_TIMEOUT:
            raise ValueError(f"{where}: timeout is between 1s and 5m")
        if entry.get("enabled", True) is False:
            continue
        out.append({"name": name, "argv": argv, "every": every, "timeout": timeout})
    return out


def events(output):
    """(events, log) from a watcher's output. A line that starts with the event prefix but is not a valid event is
    kept in the log, not sent. At most MAX_EVENTS are returned."""
    found, lines = [], []
    for line in str(output).splitlines():
        if line.startswith(EVENT_PREFIX):
            try:
                event = json.loads(line[len(EVENT_PREFIX):])
            except ValueError:
                event = None
            clean = check(event) if isinstance(event, dict) else None
            if clean and len(found) < MAX_EVENTS:
                found.append(clean)
                continue
        lines.append(line)
    return found, "\n".join(lines)


def check(event):
    """An event with only the known fields, cut to their limits, or None when it is not one."""
    if event.get("op") not in OPS or not isinstance(event.get("key"), str) or not event["key"].strip():
        return None
    clean = {"op": event["op"]}
    if event.get("incident") is True:
        clean["incident"] = True
    for name, limit in LIMITS.items():
        value = event.get(name)
        if isinstance(value, str):
            clean[name] = value[:limit]
    return clean
