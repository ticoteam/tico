---
name: cover-every-state-of-a-screen
description: List every state a screen can be in, from the spec and the states that are missing from it, and write one row per state with its line, its next step and its error, so no state is left for an engineer to invent.
---
# Cover every state of a screen

When to use: before you write a copy table for a screen, or when a review finds a screen with a state missing. Read it
with `playbooks/write-copy-for-a-screen.md`.

Steps:
1. List the states the spec describes. Common ones: empty, loading, one item, many items, each kind of error, success,
   no permission, offline or timed out, first run.
2. Add any state the spec leaves out that the screen can plainly reach, and mark it as a question for the Product
   Manager. Do not write copy for a state whose behaviour is unknown.
3. For each state, write the one line the person needs: where they are, what changed, and what to do next. A state
   with no next step says "none" in the table.
4. For each error state, write the error with `skills/rewrite-an-error-message/SKILL.md`.
5. For each button, check that it says what it does, and that a confirmation after it names the same object.
6. Read the states in the order a person meets them. Each should follow from the one before, and none should say more
   than the state knows: "saved" only after the save has finished.
7. Put each state in the copy table as its own row, with the state in its own column.

Checks before you finish:
- Every state the spec names has a row.
- Each row has a next step, or "none".
- No state is written as a guess; each unknown state is a question.
- No string claims an outcome the state has not reached.
- Every button and its confirmation use the same name for the object.

Example (generic, invented): a class schedule screen.
- Empty week: "No classes this week. Add a class to fill the schedule." Button: "Add class". Next step: add a class.
- Offline: "You are offline. Check your connection and try again." Next step: retry.
- Load failed: "We could not load this week's schedule. Try again." Next step: retry.
- Permission denied: a question for the Product Manager. The spec does not say who sees the schedule.
