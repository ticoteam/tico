---
name: decide-a-beta-exit
description: Recommend extend, graduate or end for a beta at or past its exit date, using the exit criteria written when it started and counted evidence, with the owner named as the decider.
---
# Decide a beta exit

When to use: a beta reaches its exit date, or a beta participant's feedback is overdue. The output is a
recommendation on the task. The owner decides and the bot does not change the beta's status.

Steps:
1. Read the beta's row in `knowledge/betas.md`: its exit criteria, its participants, start dates and feedback due dates.
2. If the exit criteria are missing from the row, say so. Do not invent them. The recommendation is to write them
   before any decision.
3. For each criterion, write the number that shows it: how many participants used the feature, how many gave feedback,
   how many blocking defects are open. Write the number, not the impression.
4. Chase overdue feedback once. Ask the internal owner of the beta to send one reminder with `hub task ask <id>`, or
   record the participant as not responding. Do not write to customers yourself.
5. Choose one recommendation:
   - Extend: the new exit date, and every criterion still unmet, each with its current number.
   - Graduate: all criteria met, and the release it moves to.
   - End: a criterion failed or the need is gone. Say what happens to each participant.
6. Write the reason in one or two lines that cite the numbers.
7. Name the decider: the owner of the beta, from the row or the roadmap. Name no one else.
8. Update the beta's row with the recommendation and the date. Leave its status as it is until the owner decides.

Checks before you finish:
- Every beta at or past its exit date has a recommendation on the task.
- Each criterion is marked with a number, and the criteria were not rewritten after the fact.
- The decider is named.
- The recommendation contains no message to participants that a person has not chosen to send.

Example (generic, invented):
- Beta: shared calendar view. Exit date 2026-10-15. Criteria written 2026-09-01: five accounts use it weekly; no open
  blocking defects.
- Numbers on 2026-10-15: four accounts used it weekly in the last two weeks, and one blocking defect is open. Two of
  eight participants have not given feedback; the owner was asked once.
- Recommendation: extend to 2026-11-05. Two criteria open: weekly use is at four of five, and one blocking defect is open. Decider: the Head of
  Product, the beta owner.
