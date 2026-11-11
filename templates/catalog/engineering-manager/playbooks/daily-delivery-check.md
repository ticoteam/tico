# Daily delivery check

Schedule: daily at 09:00 team time (routine `daily-delivery-check`), after setup. Also run by hand.
Budget 30 minutes. The outcome is each open parent updated with what merged, what waits and what each blocker needs, and
a short Pipeline section on the check's own task.
Nothing is sent outside the team.

---

## Inputs

- Open parent tasks you own: `tico task list`.
- Each parent's tree, `tico task tree <id>`, and the task links on each child.
- The pull requests on each child: `gh pr view <number> -R <repo>` and `gh pr checks <number> -R <repo>`.
- Your last check's comment on each parent, so you report movement, not a repeat.
- `knowledge/pipeline.md`, the open tasks (`tico task list --status open`), the snoozed ones
  (`tico task list --snoozed only`) and the tasks you were woken about (`tico task list --member me`).

## Steps

1. **Walk the trees.** For each open parent, list its children and the pull requests linked to each.
2. **Look for the signs that need action.** Failing checks, conflicts, requested changes, review comments with no
   reply, a branch or worktree with no activity, and a child with no activity for two working days.
3. **Nudge the builder once, with the item and its link,** as a comment on the child task with `tico task comment`.
   Do not repeat a nudge when nothing has changed since the last check.
4. **Name what needs a decision.** A dependency across owners, a scope question or a date change goes to the
   requester with `tico task ask <id> --to <person> "<question>" --version N`, with the options.
5. **Update the parent.** Completed children, merged pull requests (merged, not released), the remaining blockers,
   and the next action for each.
6. **Write the Pipeline section** as one comment on the check's own task with `tico task comment`. Keep the rows in the
   Engineering pipeline's stages and check a doubtful one with `tico task show <id>`. In this order: tasks per stage; each
   task that stopped this week with why and the decision made; tasks on ice; what waits on a person, and since when;
   the repeats worth a proposal; and what was not read. A task you could not read is "not read", not clean. No person
   is named as the cause of a stop. Nothing is moved by this section.
7. **Finish.** `tico task done <id> -m "..." --version N` for the check: the parents checked, and any source you could not read.

## Output

One comment per open parent, in the shape of `knowledge/examples/delivery.md`: a progress line, the children with
their state and links, the blockers with owner and next action, and the decisions needed. One Pipeline comment on the
check's own task, in the same example's shape.

## Done when

- Each open parent has a comment from today.
- Each child has a state (open, in review, merged or blocked) with its link, or is named as unreadable.
- No nudge repeats one already sent with nothing new.
- Every task that stopped this week shows what was missing and the decision made.
