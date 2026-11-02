---
name: trace-a-misroute-to-its-rule
description: Find which rule, trigger or automation sent a ticket to the wrong place, in the order the help desk runs them, and show the overlap so the fix names the right rule instead of the one that looks wrong.
---
# Trace a misroute to its rule

When to use: before you say which rule caused a ticket to land in the wrong queue, or before you propose a rule change
to fix a misroute.

Steps:
1. Take the ticket and its history: where it was created, each queue or group it reached, and the time of each move.
   Use the export, the tool's ticket audit, or a sample from `hub file list`. Name the source.
2. List every rule, trigger and automation that can act on that kind of ticket, from the configuration export or
   `knowledge/config-map.md`. Include the ones that are switched off, and say so.
3. Put them in the order the tool runs them (`knowledge/rule-order-and-timers.md`). Say which order you used and
   where you confirmed it.
4. Find the rules whose conditions the ticket met, and trace what each one changed on the ticket before the next one ran.
   If the tool shows which rule fired, use that. If it does not, say so, and test the candidate with a test ticket that
   copies the ticket's conditions. Do not assume the first match explains the result.
5. If two or more rules matched, name all of them. Matching is not conflict: name an overlap as a finding only when the
   rules take incompatible actions, or the test ticket shows one undoing or blocking the other.
6. Count the tickets in the sample that the same rule moved. Group them by rule, not by the queue they ended in.
7. Write the cause as one line: the rule, its position, the condition that matched, and where the ticket should
   have gone. Then the change request, which removes or narrows the rule, goes to `write-a-change-request`.

Checks before you finish:
- Every rule named was read from the export or the config map, with its position.
- The rule that fired is confirmed by the tool, or the test ticket, or the claim says "not shown by the tool".
- Each overlap is named with both rules.
- The count of tickets is from a sample you read, with its date range.

Example (invented, for illustration only): 41 billing tickets in September landed in the general queue. A keyword
rule set up on 2026-08-12 sits above the billing form rule and matches the same subject lines. On a test ticket, it sets the
queue to General first, so the billing form rule, which acts only on an empty queue, did not run. The cause is the keyword
rule's position, its condition and its action. The fix narrows that rule's condition, and the billing form rule is not changed.
