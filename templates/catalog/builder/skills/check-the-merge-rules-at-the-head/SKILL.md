---
name: check-the-merge-rules-at-the-head
description: Before merging a Dev ticket's pull request, read the team's merge rules, confirm that the checks and verdicts cover the exact head commit, and stop when any rule is not clearly met.
---
# Check the merge rules at the head

When to use: before any `gh pr merge`, and whenever a ticket asks you to merge.

Steps:
1. Read the merge rules in `knowledge/standards.md` and Head of Engineering's current rules for the ticket's type. If they are
   not written down, do not merge; ask Head of Engineering on the ticket.
2. Confirm the head commit: `gh pr view <n> -R <repo> --json headRefOid`. Every check and verdict you rely on must name this
   commit. Earlier heads do not count.
3. Read `gh pr checks <n> -R <repo>`. Every required check must pass at this head. A failing or pending required check
   stops the merge.
4. Confirm that the verdicts the rules require (review, QA pass) are on this head, and that no blocking comment is open.
5. Confirm the path rules: a change to sign-in, permissions, customer data, billing, or anything that sends needs the named
   person's approval, as the rules state.
6. Merge with the head pinned, using the strategy the rules name:
   `gh pr merge <n> -R <repo> --squash --match-head-commit <sha>`. Then record on the ticket the merge commit and the head
   you merged.

If any step is not clearly met, do not merge. Write the one missing thing on the ticket, and leave the ticket at its step.
