---
name: read-what-is-missing
description: Read why a task stopped in its stage, from what tico task show lists as missing to leave it, whose turn it is and who tried, and say so when a requirement cannot be read instead of calling it met.
---
# Read what is missing

When to use: before every decision on a task you were woken about.

Steps:
1. Run `tico task show <id>`. Under "To leave <stage>" each requirement says Met, Waiting (on a person, Jev, GitHub or
   another task) or Not met, with what is missing. Write down each one that is not met.
2. Note whose turn it is (the Turn line), who tried and how many times, and when the task last changed.
3. Read the owner's last notes in the task's conversation: what it tried, and what it says stops it.
4. Open the evidence a requirement points to (a page, a pull request, a filed task). If you cannot open it, write
   "not read" and do not treat it as met.
5. Sort the stop into one of: wrong or missing owner, earlier stage's work wrong, something no longer needed, a wait on
   the outside world, no longer wanted, needs a person's decision, or a broken bot or tool. That picks the decision in
   `playbooks/decide-a-stuck-task.md`.

Checks before you finish:
- The requirement was read in this run, not taken from an earlier one.
- Slow (still changing) is told apart from stuck (no change since the last try).
