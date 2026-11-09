# Draft the company update

Schedule: the first of each month at 09:00 team time (routine `draft-the-company-update`), and when a person asks for a
draft. Budget 30 minutes.

Inputs: teammates' weekly updates for the period (`hub update list --kind weekly`), any teammate the list does not show
(`hub update list --bot <slug>`), and the period's first and last day.

Outcome: `reports/company-update-YYYY-MM.md`, a draft the owner reviews. It is a file for review, not an edition.

Done when: the draft is written and published as a file, the task names its path and the next step, and the task is done.

1. Read the period. Note who reported and who did not.
2. If nothing was reported, say so on the task and stop.
3. Write the draft in this order: a headline (one sentence on what the period was); what shipped or finished; what
   customers and partners did, as reported; the reported numbers, each with its source and date; what is next; what is
   blocked and who holds it; and who did not report.
4. Put a source line under each figure: the update it came from and its date. A figure with no source is left out.
5. End with a short "Left out" list: what you did not include and why (no source, private, or not yet decided).
6. Publish it: `hub file publish reports/company-update-<YYYY-MM>.md`. Comment on the task with the path and "Review, then
   draft and approve the edition on the Company page", then `hub task done`.

Write only what the updates say. A number you could not read is "not available", never a guess.
