---
name: judge-whether-a-release-can-roll-back
description: Decide, before a release goes out, whether the previous version can be restored safely after this one, by checking the data changes, the flags, the rollout steps and the rollback triggers, and state the answer as a yes, a no with the blocker, or unknown.
---
# Judge whether a release can roll back

When to use: in every release readiness check, and before any rollout. A rollback is only a plan if the previous version can
run on the data the new version has written.

Steps:
1. List the data changes in the release: new tables or columns, dropped or renamed columns, changed meanings of stored
   values, backfills, and any data the new code writes in a format the old code cannot read.
2. Classify each change:
   - additive and optional (a new nullable column): the old code ignores it, so rollback is safe;
   - destructive (a dropped column, a rewritten value, a deleted row): rollback needs a restore point or a reverse step,
     named and tested before the release;
   - staged (expand, then move the readers, then contract): rollback is safe until the contract step, and that step is a
     release of its own.
3. Check the flags: a flag that turns a feature off reverses behaviour but not schema. Name who can turn it off, and how
   fast.
4. Check the rollout: which installs get the release first, how long each step waits, and the signals watched during the
   wait (error rate, failed jobs, sign-in success, the main flow's success).
5. Write the rollback triggers before the rollout starts: the number or signal that means stop, and who decides. A rollback
   that needs a debate is a rollback that is late.
6. State the answer in one line: "Rollback safe: yes", "no, because <change>, needs <step>", or "unknown, because <what was
   not readable>". Unknown is not yes.

Checks before you finish:
- Every data change has a class in step 2.
- Every destructive change has a restore or reverse step with an owner.
- The rollout waits and the triggers are written down.
- The answer names the release, the commit and the date it was judged.

Example (invented, for illustration): "2.15.0 adds `bookings.reminder_sent_at` (nullable): additive, safe. It also backfills
`reminders.channel` from a text value to an enum: destructive for the old reader. Restore point: a snapshot taken before the
backfill, owner the on-call engineer. Rollback safe: no, until the snapshot is confirmed. Triggers: the sign-in error rate above the
limit in the rollout plan for ten minutes, or any failed reminder job."
