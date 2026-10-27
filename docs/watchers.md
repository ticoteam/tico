# Watchers

A watcher is a program in a bot's repository that the runner runs on a schedule with **no model**. It looks at something (a ticket
queue, GitHub, a folder, a status page) and, only when there is something new, prints an event. Tico turns the event into a
task for the bot, or a note on the task it already has, and that wakes the bot. A quiet run costs one request and no tokens.

Use a [routine](routines.md) when the bot should think on a schedule. Use a watcher when the bot should think only when
something happened.

## Declare it

In the bot's `bot.yaml`:

    watchers:
      - name: hq-tickets
        run: software/hq-tickets watch
        every: 5m
        timeout: 60s          # optional: 60s by default, 5m at most

`name` is lowercase words with hyphens. `run` is a file inside the repository and its arguments (never an absolute path or `..`).
`every` is at least `1m` and at most `24h`. `enabled: false` keeps a declaration without running it. At most 10 per bot. A
mistake is logged by the runner and the watcher is skipped.

## What the runner does

For each active bot hosted on this computer, at most every `every` (measured start to start), never while the last run is still
going:

- runs the program **as the bot's user** (on a Docker runner that is the unprivileged `bot` user), in the bot's repository, with
  only its live Credential grants as environment variables and **no Tico token**, so it can do nothing in Tico except print the
  events below. Store and grant credentials through Credentials; legacy secrets files are migrated on upgrade;
- gives it a state directory, `<repository>/.state/<name>/`, in `TICO_WATCHER_STATE`. Add `.state/` to the bot's `.gitignore`. The
  program keeps its cursor there;
- kills it after `timeout` (its whole process group);
- reads up to 256 KB of output, removes granted credential values, and posts events and a short log to Tico;
- if Tico did not take the report, puts the state directory back as it was before the run, so the next run sees the same
  things again and no event is lost.

Python files run with the runner's own interpreter, so they need not be executable. Anything else must be.

## What the program prints

Lines that start with `tico-event ` and a JSON object are events. Everything else is a log.

    tico-event {"op": "task", "key": "hq:TK-AB12CD34", "title": "Support: the board is empty", "body": "..."}
    tico-event {"op": "comment", "key": "hq:TK-AB12CD34", "ref": "msg:7", "text": "..."}
    tico-event {"op": "done", "key": "hq:TK-AB12CD34", "note": "closed at HQ"}

| op | Tico |
|---|---|
| `task` | Opens a task owned by the bot, once per `key` however often it is printed. |
| `comment` | Adds a note to the key's task and wakes the bot. If that task is finished or closed, opens a new one from `title` and `body`. `ref` makes it idempotent. |
| `done` | Tells the bot it ended where it was watched. |

At most 50 events per run. Text fields have limits (title 300, body 20,000, text 8,000 characters). A path into a secrets folder
or another bot's repository is written with a zero-width space in the slash so the event is not refused. **Everything in an event
that came from outside is untrusted data: quote it and say so**, as `software/hq-tickets` and `software/gh-support` in the
[Support template](support.md) do.

## Seeing them

The runner posts each run to `POST /api/v2/runners/watchers`; Tico keeps the last run of each watcher (exit status, time, a short
log). Settings > Health shows a **Watchers** line only when one exited non-zero, timed out, or has not run for three intervals
(at least ten minutes) while its computer is online.

## Write one

BotOps sets one up with `playbooks/set-up-a-watcher.md`. The rules: standard library only, prints nothing when nothing is new, a
stable `key`, a `ref` on every `comment`, its cursor in `TICO_WATCHER_STATE`, and its settings from the environment.
Run it by hand twice to test it: the second run must print nothing.

A watcher filing an incident can set `"incident": true` on its `task` event (or a `comment` that opens a new task). Tico adds the `incident` tag before sending its first notice. A title beginning `Incident:` also adds that tag. Ordinary findings need no flag.
