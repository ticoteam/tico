"""Watchers: a program in a bot's repository that the runner runs on a schedule with no model (docs/watchers.md).

`bot.yaml` (older: `employee.yaml`) declares them:

    watchers:
      - name: hq-tickets
        run: software/hq-tickets watch
        every: 5m
        timeout: 60s          # optional; 60s by default, 5m at most

The runner parses the declaration and the output protocol below (runner-rs/src/watchers.rs). This module keeps only the
limits the server checks each reported event against (backend/watchers.py).

A watcher prints ordinary lines (a log) and lines that start with `tico-event ` followed by one JSON object:

    tico-event {"op": "task", "key": "hq:TK-AB12CD34", "title": "Support: ...", "body": "..."}
    tico-event {"op": "comment", "key": "hq:TK-AB12CD34", "ref": "msg:7", "text": "..."}
    tico-event {"op": "done", "key": "hq:TK-AB12CD34", "note": "closed at HQ"}

`task` opens a task for the bot if the key has none (once, however often it is printed). `comment` adds a note to that
task and wakes the bot; when the task is finished or closed it opens a new one from `title` and `body`. `done` tells
the bot the thing ended elsewhere. `ref` makes a `comment` idempotent.
"""

MAX_EVENTS = 50
LIMITS = {"key": 200, "ref": 100, "title": 300, "body": 20_000, "text": 8_000, "note": 2_000}
