---
name: verify-a-help-desk-change
description: Write and check a help desk change so that it has a named test ticket, an expected result, an undo step and a recorded before-and-after, and so that nothing is called fixed until the test shows it.
---
# Verify a help desk change

When to use: before you apply or hand over any change to a rule, trigger, SLA policy, macro, tag or view, and after
it is applied.

Steps:
1. Write the current setting exactly as the tool shows it, and the new setting exactly as it should read. One change
   per request, so each result can be checked alone.
2. Name the test. Write a test ticket that matches the new conditions: its subject, channel, plan or tag, and where it
   should land or what it should show. Use a sandbox or a test account where the tool has one.
3. Name the undo: the steps that restore the old setting, and what happens to tickets that were changed while the new
   setting ran. A change with no undo is not ready.
4. Check for conflicts. Name every rule, trigger, macro or report that touches the same tickets or the same tag, say what
   each does after the change, and whether it conflicts (an incompatible action, or one undoing or blocking the other) or
   only overlaps.
5. Apply the change if it was requested and a granted Tool allows it. Otherwise hand over the steps on the task for
   the person who applies it, and stop there.
6. Run the test ticket. Record the result: where it landed, what it showed, and the time. A result that differs from
   the expected one means the change is not finished.
7. Record the before and after in `knowledge/change-log.md`. At the next audit, check the effect on real tickets and
   record it as worked, did not work, or not yet measured.
8. Say "shipped" or "applied" for the change, and "working" only after the test ticket and the next audit agree.

Checks before you finish:
- The test ticket, its expected result and its actual result are on the task.
- The undo steps are written out, not referred to.
- Conflicting rules are named.
- The change log has the before and after, with dates.

Example (invented, for illustration only): change request CR-3 replaces "within 14 days" with "within 30 days of
purchase" in a refund macro. The test is a macro preview on a ticket tagged with the refund subject, which should show
the 30-day text. The undo is to paste the old text back, and the macro is used in no other rule. The change is
"applied, test passed". It is "working" only after the next audit finds no ticket that quotes 14 days.
