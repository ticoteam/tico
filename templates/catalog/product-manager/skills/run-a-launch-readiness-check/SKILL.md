---
name: run-a-launch-readiness-check
description: Walk each launch checklist line for a release due within four weeks, mark it done with evidence, due with a date or missing with an owner, and bring every missing line to its owner.
---
# Run a launch readiness check

When to use: in the weekly spec review for each launch due within four weeks, and before any release goes to customers.

Steps:
1. Read `knowledge/launch-checklist.md` and the rollout section of the spec.
2. For each line, check the evidence, not the status word:
   - QA: a test run against the before-release criteria, with its date and the result for each. After-launch criteria have no result yet: the line is done only with a named watcher and a window for each of them.
   - Support: the briefing has been given to the people who answer, not only written.
   - Release notes: the text exists and matches the released behaviour.
   - Messaging: the draft exists and the Product Marketing Manager has approved it.
   - Help content: the Librarian has confirmed the article is current, or a task exists for the gap.
   - Rollout: the first audience, the flag, the metric that will be watched, and the size of each widening step.
   - Rollback: the steps a person can carry out to turn the change off or revert it, and who can do them.
3. Mark each line done (with its evidence), due (with its date), or missing (with its owner).
4. A missing line with no named owner is a launch blocker. Name the owner in the review and ask them once, on the task.
5. Check the widening plan. Start with a small share of users or one account type, watch the metric, then widen.
   Write what each step must show before the next one. Automatic rollback on a failed check is better than a person
   noticing later.
6. Check external dependencies. A partner or vendor that needs notice has a date, and the date they were told is recorded.
7. Record the check in the review. A change to the launch date is the owner's decision, not a result of this check.

Checks before you finish:
- Every line is done with evidence, due with a date, or missing with a named owner.
- No line is marked done from a plan or a promise. The evidence is a test run, a sent briefing, or a flag setting.
- The rollback is written as steps a person can carry out, with a name against them.
- The launch date appears only inside the team.

Example (generic, invented): "Waitlists, launch in three weeks. QA: done, 12 of 12 before-release criteria run on 2026-10-28 (run
linked); the 2 after-launch criteria have a named watcher for two weeks. Support briefing: due 2026-11-12, owner Head of Customer Support. Rollback: missing, no owner. Blocking.
Asked the owner once on the task."
