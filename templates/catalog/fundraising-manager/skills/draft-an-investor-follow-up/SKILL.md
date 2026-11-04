---
name: draft-an-investor-follow-up
description: Write a follow-up draft to an investor or prospective investor that uses only facts from the record, carries no terms, leaves marked blanks, and ends with the one message a person must approve or send.
---
# Draft an investor follow-up

When to use: after a meeting, a call or a pipeline change, when a follow-up or an update is due for an investor.

Steps:
1. Read the investor's row in `knowledge/pipeline.md` and the last two threads or meeting notes about them. Note the date of
   the last contact and what they asked for.
2. List what was promised, by whom and by when, from the record. A promise nobody wrote down becomes a blank for the
   person to fill.
3. Draft in the person's voice, short: a line that names one thing the investor said, the material they asked for (with
   its data room link once checked), and the next step with a date.
4. Strip terms. Remove any valuation, amount, allocation, round date or comparison with other investors. Where the investor
   asks about terms, leave a marked blank: "[terms: to be confirmed by the person]".
5. Put every figure and every blank's source in a short note under the draft, not in the message itself.
6. Put the draft on the task with the recipient named. The send is one approval per message, requested with
   `hub approval request --kind send` (see AGENT.md, Sending).

Checks before you finish:
- No terms, valuation, amount, allocation or round date appear in the draft.
- Each figure has a source and a date in the note under it.
- The draft asks for nothing the data room cannot supply.
- The draft is for one investor only.

Example (invented, for illustration): "Draft to Cedar Angels: thanks for Tuesday's call on retention. You asked for the churn
cohort table; it goes in the data room once Ravi signs it off (owner Ravi, due 2026-10-09). Next step: a 30-minute follow-up
in the week of 12 October. [terms: to be confirmed by the person]". Note under the draft: "Source: meeting notes 2026-10-06;
data room checklist, row 4."
