"""`tico events tail`: follow the company's live event stream from a terminal, one JSON line per event.

What `stripe listen` is for Stripe: a test agent watches a request move through stages without polling. It reads the
same stream the web app does (`GET /api/v2/events`, backend/events.py; docs/custom-frontend.md, Live events), so it
sees only what its credential may read. A stream ends after about fifteen minutes or when the network drops; the tail
reconnects from the last change number it printed (`after`), so nothing is missed in between. A `reset` line says the
tail fell further behind than the server keeps: read what you are watching in full.

Each line is the event's `data` with `event` (its topic) added: for `tasks`, `seq`, `id`, `kind`, `kinds` and `task`
(as the task list shows it, with `pipeline_id`, `stage_id`, `status`, `owner_id`). The global `--fields` picks from
each line: `tico events tail --task T --fields seq,kind,task.stage_id,task.status`.

`--task` is the server's own filter (`task=`); `--pipeline` is applied here, on each task's `pipeline_id`.
"""

import json
import random
import re
import sys
import time

from clients.tico import APIError

RECONNECT_MAX = 30  # seconds between tries once the server keeps failing
READ_TIMEOUT = 60  # the server sends a keepalive every 15 s; this long without a byte is a dead connection
# Frames that only move the change number forward: not printed. (A set, since `topic == "cursor"` reads to the harness
# check of backend/tests/test_harness_capabilities.py as a comparison with the Cursor CLI's name.)
POSITION_ONLY = frozenset({"cursor"})
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class SignInExpired(Exception):
    """The stream said `expired`: the credential no longer works."""


def frames(lines):
    """(id, event, data) for each server-sent event in an iterable of decoded lines; comments and `retry:` skipped."""
    seq, event, data = None, None, []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if event or data:
                yield seq, event or "message", "\n".join(data)
            seq, event, data = None, None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            seq = value
        elif field == "event":
            event = value
        elif field == "data":
            data.append(value)


def task_ids(client, values):
    """Full task ids for `--task` values: a short id or a number (`#18945`) is read once to learn the full id, since
    the server filters on full ids."""
    out = []
    for value in values or []:
        if UUID.fullmatch(value):
            out.append(value)
            continue
        found = client.get("tasks/" + value)
        task = found.get("task") if isinstance(found, dict) and isinstance(found.get("task"), dict) else found
        out.append(task["id"])
    return out


def pipeline_ids(client, values):
    """Pipeline ids for `--pipeline` values, each an id or a name (any case)."""
    if not values:
        return set()
    try:
        listed = client.get("pipelines").get("pipelines") or []
    except APIError:
        listed = []
    out = set()
    for value in values:
        match = [
            p["id"]
            for p in listed
            if value in (p.get("id"), p.get("name")) or value.lower() == str(p.get("name")).lower()
        ]
        out.update(match or [value])
    return out


def keep(topic, data, pipelines, seen):
    """Whether a line passes the client-side `--pipeline` filter. A task's `gone` keeps when it was seen in one."""
    if not pipelines or topic != "tasks":
        return True
    task = data.get("task") if isinstance(data.get("task"), dict) else None
    if task is not None:
        inside = task.get("pipeline_id") in pipelines
        if inside and data.get("id"):
            seen.add(data["id"])
        return inside or data.get("id") in seen  # a task that moved out of the pipeline: its last line is shown
    if data.get("bulk"):
        return True
    return data.get("id") in seen


def tail(
    client,
    *,
    topics=None,
    tasks=None,
    pipelines=None,
    after=None,
    count=None,
    timeout=None,
    emit=None,
    note=None,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """Print events until `count` lines, `timeout` seconds, or Ctrl-C. Returns the number of lines printed. Raises
    SignInExpired, or APIError for a refusal that reconnecting cannot fix (401, 403, 404, 422)."""
    emit = emit or (lambda line: (sys.stdout.write(line + "\n"), sys.stdout.flush()))
    note = note or (lambda text: print("tico: " + text, file=sys.stderr, flush=True))
    ids = task_ids(client, tasks)
    wanted_pipelines = pipeline_ids(client, pipelines)
    topics = topics or (["tasks"] if (ids or wanted_pipelines) else None)
    query = {"topics": ",".join(topics) if topics else None, "task": ",".join(ids) or None}
    deadline = clock() + timeout if timeout else None
    cursor, printed, failures, seen = after, 0, 0, set()

    def left():  # seconds of the timeout still to go; None without one
        return None if deadline is None else deadline - clock()

    def wait_for(seconds):  # a reconnect wait never outlasts the timeout
        remaining = left()
        sleep(seconds if remaining is None else max(0, min(seconds, remaining)))

    def until_deadline(lines):  # keepalives and unprinted frames end the stream at the timeout too
        for raw in lines:
            if deadline is not None and clock() >= deadline:
                return
            yield raw.decode("utf-8", "replace")

    while True:
        remaining = left()
        if remaining is not None and remaining <= 0:
            return printed
        try:
            response = client.stream(
                "events",
                {**query, "after": cursor},
                timeout=READ_TIMEOUT if remaining is None else min(READ_TIMEOUT, remaining),
            )
        except APIError as exc:
            if not exc.retryable:
                raise
            if deadline is not None and clock() >= deadline:
                return printed  # the connect timed out at the requested timeout: no retry to announce
            failures += 1
            wait = min(RECONNECT_MAX, 2 ** min(failures, 5)) + random.random()
            note(f"event stream unavailable ({exc.detail}); trying again in {wait:.0f}s")
            wait_for(wait)
            continue
        try:
            with response:
                for seq, topic, text in frames(until_deadline(iter(response.readline, b""))):
                    failures = 0
                    try:
                        data = json.loads(text) if text else {}
                    except ValueError:
                        data = {"raw": text}
                    if not isinstance(data, dict):
                        data = {"value": data}
                    if topic == "expired":
                        raise SignInExpired("the sign-in behind this stream is no longer valid")
                    if seq and seq.isdigit():
                        cursor = int(seq)
                    if topic == "ready":
                        if cursor is None:
                            cursor = data.get("seq")
                        note("following " + ", ".join(data.get("topics") or ["all topics"]) + " (Ctrl-C to stop)")
                        continue
                    if topic in POSITION_ONLY:
                        continue
                    if topic == "reset":
                        cursor = data.get("seq", cursor)
                    elif not keep(topic, data, wanted_pipelines, seen):
                        continue
                    emit(json.dumps({"event": topic, **data}, separators=(",", ":"), default=str))
                    printed += 1
                    if count and printed >= count:
                        return printed
                    if deadline is not None and clock() >= deadline:
                        return printed
        except SignInExpired:
            raise
        except (OSError, ValueError) as exc:  # a dropped or stalled connection: resume from the cursor
            if deadline is not None and clock() >= deadline:
                return printed  # the read timed out at the requested timeout: not a dropped stream
            note(f"event stream dropped ({type(exc).__name__}); reconnecting after change {cursor}")
            wait_for(1)
            continue
        # The server ends a stream after about fifteen minutes: reconnect from where it stopped.
