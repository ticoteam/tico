---
name: add-to-flow
description: Put a question in a person's Flow. Read when a person pings you for questions ("Any questions for me?", from Flow), when your setup task says to run it, or when you need a person's view before you act. Ask at most three plain questions, each its own task; zero is a good result.
---

# Add to Flow

You ask the questions whose answers would change what you do next, and nothing else.

## Who you ask

- **Pinged** (a person's message points you here): ask that person, by the name the message gives, even when they
  are not your manager. Their ping lets you ask them for a day.
- **On your own**: ask your supervisor, your `reports_to` on the chart (`tico team show`).

## Before you ask

Read what is written down first: your area's pages (`tico doc search "<topic>"`), the company strategy
(`tico strategy show`), your own `memory/decisions.md` and the working rules at the top of `memory/learnings.md`, and
your open tasks (`tico task list --owner <you>`). Never ask what is written down, and never ask what is already waiting on
someone: an open ask of yours on the same thing stays as it is.

## What to ask

- Only what would change what you do next. If nothing clears that bar, ask nothing and say so in one line; that is a
  good result.
- At most three, the most valuable first.
- Each question is one decision, in one plain sentence that reads on its own, plus one line on what you will do with
  the answer. Leave it free-form: `--choices` only when it is a real pick between named options.
- If a person has corrected you on the same thing several times, a good question is whether to make it a standing
  rule; on yes, write it at the top of `memory/learnings.md`, and if it should bind the whole stage, say so to whoever
  edits that stage.

Each question is its own new task, yours, with one ask on it:

```
tico task create --title "<the decision, in a few words>" --body "<what you will do with the answer>"
tico task ask <id> --to <person> "<the question>" -m "<what you will do with the answer>" --version N
```

## When you are the one asked (a supervisor bot)

Answer only when a recorded rule or decision covers it, and cite it:
`tico task answer <id> <requirement> "<answer> (per <where it is written>)" --version N`.
Otherwise pass it up exactly as asked: `tico task give <id> <requirement> --to <your manager> --version N` when your
manager is a person. When your manager is a bot, ask it the same sentence on a task of your own, and answer the
original with its reply, saying who answered.

## When the answer comes

Take it as given, then do both of these:

1. **Update what you own.** Whatever the answer changes in your area, change it there, in the section it belongs to:
   the page, the settings, the plan, the rules. The Strategy Owner updates the matching sections of the strategy.
   An answer that only sits in your memory has changed nothing for anyone else. If you may not make the change
   yourself, file it the way your area's changes are normally filed, and say so.
2. **Remember it.** Dated, with who said it: a decision in `memory/decisions.md`, a standing rule at the top of
   `memory/learnings.md`.

Commit, carry on with your work, and mark the question's task done
(`tico task done <id> -m "<what you changed where>" --version N`). Don't follow up on it.
