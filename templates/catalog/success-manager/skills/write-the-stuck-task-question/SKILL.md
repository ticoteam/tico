---
name: write-the-stuck-task-question
description: Write the one question a person must answer to unblock a stopped task, naming the task, the stage, what is missing and the choices, so they can answer in one tap.
---
# Write the stuck task question

When to use: when only a person can decide what happens to a stopped task, such as closing a task they asked for or
choosing between two owners.

Steps:
1. One sentence, readable on its own: the task, its stage, what is missing and what you need decided.
2. Offer the choices you would accept, so they can pick one: `--choices reassign,close`.
3. Ask it on the task: `tico task ask <id> --to <person> "<the question>" --choices <a,b> --version N`. The task waits
   for their answer, which wakes you once.
4. When the answer comes, make the decision it names, with their answer as the reason.

Checks before you finish:
- One open question per task.
- The question names no person as the cause.
- It fits in one line, under 40 words.
