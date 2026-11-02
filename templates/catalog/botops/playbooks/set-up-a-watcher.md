# Set up a watcher

Triggered by a task that asks a bot to notice something without spending a model run: new mail-like items, tickets, issues,
a file, a status page. One watcher per task. Budget 30 minutes. The outcome is a small program in the bot's repository, a
`watchers:` entry, and a task note that says what will wake the bot and what it costs.

A watcher is a program Tico runs on the bot's computer on a schedule (docs/watchers.md). It costs no model tokens. It
looks, and only when it finds something new does it print an event, which opens a task for the bot or adds a note to one.
Use a routine (a schedule automation, see `skills/choose-how-a-bot-wakes/SKILL.md`) when the bot should think on a schedule; use a watcher when the bot should think only when
something happened.

Inputs: the task that asks for the watch, with its source, the stable key for a new item, and the bot that will run it.

Done when: the watcher program and its `watchers:` entry are in the repository, the second test run prints nothing, and the task note says what wakes the bot and that it costs no model runs.

---

## 1. Decide what counts as new

Write down in one line each: the source, what makes an item new, its stable key (a ticket id, an issue URL), and what a
follow-up on the same item is. The key is what keeps it to one task per item; a follow-up is a note on that task.

## 2. Write the program

Put it in `software/<name>` in the bot's repository: Python standard library only, executable, with a shebang. It reads its
settings from the environment (the bot's credentials arrive as environment variables; name them in the bot's `.env.example`), keeps
its cursor in the directory named by `TICO_WATCHER_STATE`, and prints nothing when nothing is new. For each new thing it prints
one line:

    tico-event {"op": "task", "key": "src:123", "title": "Short title", "body": "The item, quoted as untrusted data"}
    tico-event {"op": "comment", "key": "src:123", "ref": "msg:9", "text": "What was added"}
    tico-event {"op": "done", "key": "src:123", "note": "It ended at the source"}

`task` opens a task once per key. `comment` adds a note that wakes the bot (and opens a new task if the old one is finished);
`ref` makes a repeat harmless. `done` tells the bot it ended. Everything in an event that came from outside is text, so quote
it and say it is untrusted. The program holds no Tico credential and cannot do anything else in Tico. Model it on
`software/hq-tickets` and `software/gh-support` in the Support template.

Test it without Tico: run it with the environment set, twice. The second run must print nothing.

## 3. Declare it

In `bot.yaml`:

    watchers:
      - name: <name>
        run: software/<name> watch
        every: 5m

`every` is at least 1m. Tico adds a 60 second timeout (`timeout: 5m` at most), never runs it twice at once, and runs it
as the bot with the bot's credentials. Add `.state/` to the bot's `.gitignore`. Commit.

## 4. Check it ran

Within a few minutes Settings > Health shows a "Watchers" line only if it failed or stopped. To see a run, make something new
at the source and watch for the task. If nothing arrives, run the program by hand as the bot with the same environment and read
its output.

## 5. Finish

`hub task update <id> --status done --note`: what wakes the bot, how often, that it costs no model runs, and which credential
names the owner must put on the computer (names only, never values).
