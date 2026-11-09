---
name: name-the-checks-that-gate-merging
description: Name which checks gate merging, hand each failing or flaky gate to the DevOps Engineer, and keep a check that is not required out of the gate list. Use it before the gate list is updated, or before a check is blamed for a stalled merge.
---
# Name the checks that gate merging

When to use: before the gate list is updated, before a stalled merge is blamed on a check, and when the list of gates
may have changed.

Steps:
1. Start from `knowledge/merge-gates.md` (written at setup): the checks the team says are required to merge, per repository.
2. Confirm them against the pull requests: `gh pr checks <n> -R <repo>` for a sample of merged pull requests in the window. A
   check that the pull requests required and passed is a gate. A check that appears only as informational is not.
3. A check that is in the setup list but absent from the pull requests is "not confirmed", and the report says so. Do not
   count it as a gate or as a non-gate until the owner confirms it.
4. For each gate, cite the DevOps Engineer's failure and flake figures for it, with the date of its report. Do not count runs
   yourself. If it has no figures for a gate, say so.
5. A check that is not required is listed as "not a gate". Its failures never count as a stalled merge.
6. Hand each failing or flaky gate to the DevOps Engineer as a task, with its run ids and the test name from the log. Do not
   quarantine, skip, rerun or repair it.
7. When a merge stalled, name the gate that was red on the default branch and the run. Do not name the author of the change.

Checks before you finish:
- Every gate has a source: the setup list, the pull request check results, or both.
- A non-required check is never listed as a gate.
- Each failing or flaky gate handed over has its runs listed by id, commit and date.

Example (generic): "Gates: `test-unit` (required, confirmed on 6 of 6 sampled pull requests) and `lint` (required, confirmed).
`e2e-smoke` is not required, so it is not a gate. `test-unit` goes to the DevOps Engineer, flaky on runs 8870112, 8871004 and
8871390, with its figures cited from that report."
