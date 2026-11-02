---
name: answer-from-the-record
description: "Answers a human's question about a task, bot, goal, meeting or person from the record, with links and the source named. Use it before saying what a bot is doing or what something is waiting for."
---

# Answer from the record

## When to use

- A human asks what a bot is doing, what a task is waiting on, or where something stands.
- You are about to state a run, a number, an outcome or a date in a reply.
- The question is a lookup that the server's own answer did not cover.

## Steps

1. Name the thing asked about: a task, a bot, a goal, a meeting or a person.
2. Read the record for it, and stop at the first source that answers:
   - a task: `hub task show <id>`, or `hub task list` for the open ones;
   - a bot: `hub bot status list`;
   - a goal: `hub goal show <id>`;
   - a meeting: `hub meeting search <words>`;
   - a document or market page: `hub doc search <words>`.
3. Answer in two to four lines: the fact, when it was recorded, and a link such as `[the task](#/task/<id>)`, `[the bot](#/bot/<slug>)` or `[the person](#/person/<id>)`. `playbooks/tico-map.md` has the link formats.
4. Name the source. "The task's last note, from Monday" is a source. "I think" is not.
5. If the record is silent, say you could not find it and name the places you looked. Offer one next step: a task for the owner, or a question to the bot.

## Checks before you finish

- Every status, number and date in the reply came from a read in this run.
- Nothing is described as done that you did not see done.
- The reply is four lines or fewer, with no report wrapper and no internal codes.
- Anything not found is said to be not found, never filled in.

## Example

A human asks whether the newsletter draft is finished.

> Not yet. [Draft the newsletter](#/task/example) is open with the owner, last noted on Monday, waiting for the final figures. I have not changed it. Want me to ask them for the figures?

The task and its note are invented for this example.
