# Asks and tasks you put to a human

What you put to a human is a "Needs you" item: a record to create, a person to add, a key to paste, a decision to make.
It waits for one condition. A decision or an answer is an ask on your task. Real work that takes them time is a task they
own, filed under yours.

Inputs: the condition the person must settle (a record, a person to add, a key, a decision) and who must settle it.

Done when: one open ask or task exists for that condition, its line under 40 words, and it ends once the condition is
settled.

## Before you file
- Run `hub task list --requester me` and reuse or close what is already there. Never ask twice or file a second task for
  the same condition. When a superseding task exists, close yours and say by what.
- **Look before you ask.** Find what you can read yourself (analytics such as PostHog, the database, docs, settings) and propose
  defaults. Ask only for what only a person can give: a decision, a payment, a sign-in. Never file a list of inputs to fill in.

## How to write it
Write it for someone busy who reads only the first line.
- **The line, under 40 words:** what you need, your recommendation, and what you do after. No task IDs, commit SHAs,
  command IDs or file paths there. Put evidence in the body or a report.
- **An ask** names the person, on your task: `hub task ask <id> --to <person> "<what to do>" --version N`. For a refusal,
  the line is its `fix` in plain words, one sentence plus its `link`, then what you do after ("Turn on GitHub access in
  Settings? I'll finish the setup once it is on."). No command, path or tool name. Tico refuses an ask that has them.
- **A sub-task** for real work they must do: `hub task file --parent <id> "<the action, under 10 words>"`.

## Asking for yes or a choice
Ask on the task: `hub task ask <id> --to <person> "<question>" [--choices a,b] --version N`. One open ask per task; asking
again replaces it. The same question for several bots (a backup model for each) is one ask that names them all.

## The answer goes on the task
Never tell a person to "message BotOps with ..." or to repeat a phrase in another chat. Their reply on the task is them asking.

## Closing
When the person's answer or your own work settles a task, end it: `hub task done <id> -m "I registered Jira Manager
myself." --version N`. A task left open after its work is done is noise for the human. When it is superseded, close it
and say by what: `hub task close <id> -m "<why>" --version N`.

An OK for spending, Credentials and publishing, and a `retryable: false` refusal, still stop. Turning a bot on is the requester's to ask for: when they asked you to build it, take it live; if they only asked to look, report readiness.
