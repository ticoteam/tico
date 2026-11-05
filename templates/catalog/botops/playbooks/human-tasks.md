# Tasks you file for a human

A task you file for a human is a "Needs you" item: a record to create, a person to add, a key to paste, a decision to make.
It waits for one condition.

Inputs: the condition the person must settle (a record, a person to add, a key, a decision) and who must settle it.

Done when: one open task exists for that condition, with a title under 10 words and a note under 40 words, and it is closed once the condition is settled.

## Before you file
- Run `hub task list --requester me --status open` and reuse or close what is already there. Never file a second task for the
  same condition. When a superseding task exists, close yours and say by what.
- **Look before you ask.** Find what you can read yourself (analytics such as PostHog, the database, docs, settings) and propose
  defaults. Ask only for what only a person can give: a decision, a payment, a sign-in. Never file a list of inputs to fill in.

## How to write it
Write it for someone busy who reads only the title and the first line.
- **Title:** the action or the question, under 10 words ("Make Arthur an owner of Model Releaser?").
- **Note, under 40 words:** what you need, your recommendation, and what you do if there is no answer. No task IDs, commit SHAs,
  command IDs or file paths there. Put evidence in the body or a report.
- **A waiting task** names the person: `--status waiting --on <person> --note "<what to do>"`. The note is the refusal's `fix` in
  plain words, one sentence plus its `link`, then what you do after ("Turn on GitHub access in Settings? I'll finish the setup
  once it is on."). No command, path or tool name. Tico refuses a waiting note to a person that has them.

## Asking for yes or a choice
Use a card: `hub task question "<question>" --on <person> --parent <task>` (Yes / No, or `--option "LABEL -> WHAT HAPPENS"` for
each pick, with `--why`). The same question for several bots (a backup model for each) is one topic: pass the same `--topic`
(e.g. `backup-model`) so it updates your open card instead of adding one per bot.

## The answer goes on the task
Never tell a person to "message BotOps with ..." or to repeat a phrase in another chat. Their reply on the task is them asking.

## Closing
When the person's answer or your own work settles a task, close it: `hub task close <id> --note "Done: I registered Jira Manager
myself."`. A task left open after its work is done is noise for the human. When it is superseded, close it and say by what.
