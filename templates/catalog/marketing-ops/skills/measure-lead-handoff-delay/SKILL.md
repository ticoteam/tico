---
name: measure-lead-handoff-delay
description: Measure how long a ready lead waited for its first sales touch, report the median and the slowest with counts, and report the queue rather than any person.
---
# Measure lead handoff delay

When to use: each weekly check, and whenever someone asks whether sales responds fast enough to marketing leads.

Steps:
1. Take the leads that met the ready-for-sales rule in `knowledge/handoff.md` during the window. Use the time the
   rule was met, not the time the lead was created. A lead that met the rule on a Friday afternoon starts there.
2. For each lead, take the first recorded sales touch: a call, an email or a meeting booked by a seller. Use the
   record's own timestamps. Do not use a note added later.
3. Measure the wait in business hours if the handoff rule says so, otherwise in clock hours. Say which.
4. Report the count of leads, the median wait, the slowest wait, and the count still untouched at the
   time of the check.
5. Compare the median with the target in `knowledge/handoff.md`. Read the slowest cases by queue and by source
   (for example, weekend sign-ups). Name the queue, the form or the rule, never a seller.
6. Note what the data cannot show: no timestamp, a lead routed to nobody, or a touch logged in another system.

Checks before you finish:
- The start is the time the lead became ready, not the time it was created.
- No seller's name appears in the delay figures or in the example you quote.
- Untouched leads are listed as a count, with their source.
- Any figure built on a sample says so, with the sample size.

Research note: the most cited study on speed to lead (InsideSales, led by James Oldroyd, 2007) found a phone contact
within five minutes gave about 100 times the odds of reaching a lead, compared with thirty minutes. Later vendor
studies repeat these numbers inexactly. Use the 2007 study as the reason to measure, and use the team's own last
twenty leads as the number.

Example (invented, for illustration): "34 leads became ready for sales in the window. Median wait 26 business hours
against a target of 1 business day; slowest 4 days, from a Friday form. 3 still untouched, all from the weekend
trial form. Queue: weekend trial sign-ups."
