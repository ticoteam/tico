---
name: decide-a-task-is-done
description: "Decide from the task's own evidence whether a stalled task is plainly done, plainly in another step, or unsure, and change it only when the evidence is there. A merged pull request is not done until its release ships."
---
# Decide a task is done

When to use: a "Merged or shipped, step unmoved" item, a stalled task whose work looks finished, or an answer given in
the task that nobody closed.

Steps:
1. Read it: `hub task show <id>`. Note its status, step, wait, linked pull requests or releases, and its last three
   messages.
2. Choose one of four:
   - Plainly done: a release containing the work has shipped, or the answer was given and accepted in the task.
   - Merged, not shipped: a linked pull request has merged, but no release has shipped it. Not done.
   - Plainly in another step: the evidence names the step.
   - Unsure: the task has an open question to a person, a review that has not come back, or evidence that disagrees.
3. Plainly done: `hub task close <id> --note "<the release or the answer, in one line>"`.
4. Merged, not shipped: change no status or step. If something is still left on the task, say so on the Task Manager's
   check task. Merged work waits for a person to move it to ready to ship.
5. Plainly in another step: `hub task update <id> --step "<step>" --note "<the evidence in one line>"`.
6. Unsure: change nothing. Comment on the task with what you saw, or put one line in Needs you for the department.

Checks before you finish:
- A close note names a shipped release, an accepted answer, or the task it duplicates (`skills/sort-duplicate-tasks/`), not
  a feeling.
- No task was closed because its pull request merged alone.
- No task with an open question to a person, or a review still waiting, was closed.
- You read only tasks you are on, or that the department's report listed for you.

Example (invented, for illustration): task "Add CSV export", still in "In review", with a linked pull request that merged
on Monday and no release yet. Nothing was closed or moved; the check task says it is ready to ship once a person moves
it. Task "Fix invoice footer", whose pull request is in the release that shipped on Thursday, was closed with the note
"Shipped in release 2026.10.29; nothing left to do."
