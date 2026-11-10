---
name: decide-a-task-is-done
description: "Decide from the task's own evidence whether a stalled task is plainly done, plainly not needed, or unsure, and change it only when the evidence is there. A merged pull request is not done until its release ships."
---
# Decide a task is done

When to use: a stalled task whose work looks finished, a task handed to you as Late or stuck, or an answer given in the
task that nobody finished.

Steps:
1. Read it: `tico task show <id>`. Note its version, its stage, what it waits on, what is missing, linked pull requests or
   releases, and its last three messages.
2. Choose one of four:
   - Plainly done: a release containing the work has shipped, or the answer was given and accepted in the task.
   - Merged, not shipped: a linked pull request has merged, but no release has shipped it. Not done.
   - Not needed: the evidence shows the work is no longer wanted, or another task did it.
   - Unsure: the task has an open ask to a person, a review that has not come back, or evidence that disagrees.
3. Plainly done: `tico task done <id> -m "<the release or the answer, in one line>" --version N`.
4. Merged, not shipped: change nothing. If something is still left on the task, say so on the Task Manager's check task.
5. Not needed: `tico task close <id> -m "<the evidence in one line>" --version N`. A task a person asked for is not yours
   to close: ask that person on the task.
6. Unsure: change nothing. Comment on the task with what you saw, or put one line in Needs you for the department.

Checks before you finish:
- A close note names a shipped release, an accepted answer, or the task it duplicates (`skills/sort-duplicate-tasks/`), not
  a feeling.
- No task was closed because its pull request merged alone.
- No task with an open ask to a person, or a review still waiting, was closed.
- You read only tasks you are on, or that the department's report listed for you.

Example (invented, for illustration): task "Add CSV export", still open, with a linked pull request that merged on
Monday and no release yet. Nothing was changed; the check task says it waits for a release. Task "Fix invoice footer",
whose pull request is in the release that shipped on Thursday, was finished with the note "Shipped in release
2026.10.29; nothing left to do."
