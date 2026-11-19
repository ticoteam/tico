---
name: write-the-stuck-task-question
description: Write the one question a person must reply to to unblock a stopped task, naming the task, the stage, what is missing and the options, so they can answer in one line.
metadata:
  function: tico
---
# Write the stuck task question

When to use: when only a person can decide what happens to a stopped task, such as closing a task they asked for or
choosing between two owners.

Steps:
1. One sentence, readable on its own: the task, its stage, what is missing and what you need decided.
2. Write the options you would accept into the message, so they can pick one: "Reassign it, close it, or tell me what to do?"
3. Ask it on the task: `tico task ask <id> --to <person> "<the question>"`. Their reply wakes you once.
4. When the reply comes, make the decision it names, with their reply as the reason.

Checks before you finish:
- One open question per task.
- The question names no person as the cause.
- It fits in one line, under 40 words.
