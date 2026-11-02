# Daily delivery check

Schedule: daily at 09:00 team time (routine `daily-delivery-check`), after setup. Also run by hand.
Budget 30 minutes. The outcome is each open parent updated with what merged, what waits and what each blocker needs.
Nothing is sent outside the team.

---

## Inputs

- Open parent tasks you own: `hub task list --status open --status doing --status waiting`.
- Each parent's tree, `hub task tree <id>`, and the task links on each child.
- The pull requests on each child: `gh pr view <number> -R <repo>` and `gh pr checks <number> -R <repo>`.
- Your last check's comment on each parent, so you report movement, not a repeat.

## Steps

1. **Walk the trees.** For each open parent, list its children and the pull requests linked to each.
2. **Look for the signs that need action.** Failing checks, conflicts, requested changes, review comments with no
   reply, a branch or worktree with no activity, and a child with no activity for two working days.
3. **Nudge the engineer once, with the item and its link,** as a comment on the child task with `hub task comment`.
   Do not repeat a nudge when nothing has changed since the last check.
4. **Name what needs a decision.** A dependency across owners, a scope question or a date change goes to the
   requester with `hub task ask`, with the options.
5. **Update the parent.** Completed children, merged pull requests (merged, not released), the remaining blockers,
   and the next action for each.
6. **Finish.** `hub task update <id> --status done --note` for the check: the parents checked, and any source you could not read.

## Output

One comment per open parent, in the shape of `knowledge/examples/delivery.md`: a progress line, the children with
their state and links, the blockers with owner and next action, and the decisions needed.

## Done when

- Each open parent has a comment from today.
- Each child has a state (open, in review, merged or blocked) with its link, or is named as unreadable.
- No nudge repeats one already sent with nothing new.
