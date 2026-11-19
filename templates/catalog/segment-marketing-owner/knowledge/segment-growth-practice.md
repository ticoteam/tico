# Segment growth practice

How a segment's new customers and cost per customer are counted and split, how growth tests and asks are run, and where each rule comes from. The standing rules for this bot. Items marked "team default" were set for this template, not taken from Tico's docs; the Growth Manager (`growth-manager`) confirms or changes them at the first review.

## Counts

- **New customer.** One that the counting rule in `knowledge/segment.md` admits, dated by the day that rule names. Each customer counts once.
- **Cost per customer** is Growth's, by segment: **direct** is the spend and bot cost of the channels set to this segment ÷ its new customers; **loaded** adds company-wide spend in proportion to the segment's share of new customers. It is blank with no new customers. Read it with `tico growth segment show <slug>`; never split spend by hand.
- **Goal, floor and ceiling** come from the KPI linked to the segment's goal (`tico goal list --all`, `tico kpi show <id>`). If the goal names no KPI, the review says so and asks the Growth Manager (`growth-manager`).

## Customers with no segment

A customer with no segment field is counted in a no-segment row and never dropped. If that row is above 20% of the month's new customers, the reading gives a range: the segment's count is at least its own figure and up to that figure plus the no-segment count. The 20% line is a team default.

## Tests

- A test is an idea task owned by the Growth Manager. Its label says where it is: `growth:ideas`, `growth:running`, `growth:measuring`. It ends done (Kept) or closed (Dropped: failed, inconclusive or stopped). Nothing ends a running test while it runs.
- A test starts only when the Growth Manager (`growth-manager`) picks it at the Monday growth meeting, on a channel at stage `testing` or `scaling` with budget left (`tico growth channel list`). A paused channel gets no new test.
- Team capacity, from the marketing team's growth meeting: at most six running tests across the team, and at most two new tests per channel Owner at a time.
- An idea carries no prediction, hypothesis, score or bet. It says the segment, the change, the channel and the measure. The prediction is written once, as a number, on the Go-live page in Building, by the channel Owner who runs the test; a hypothesis, score or bet is never written in an idea.
- A test is read only after its own window and not in its first seven days. The seven days are a team default.
- Every finished test gets one line in its comment on what was learned and the numbers behind it.

## Asks to channel Owners

- An ask names one outcome, one segment, one date and the spend it may use. An ask that spends money needs the owner's OK on its task before it starts.
- Two open asks per channel Owner from this segment at a time, and one open ask per channel per idea. Both are team defaults.
- A reminder after seven days, as a comment on the same ask. A question to the Growth Manager (`growth-manager`) after 14 days with no answer.

## Message and claims

- Each claim in copy is checked against `tico brand show --section claims` before the copy goes to a channel Owner or outside the team (`skills/test-a-segment-message/SKILL.md`).
- The Design Owner (`design-owner`) adds a missing claim with its evidence. Until then the claim is not used.

## Sources

- 2026-11-06: docs/growth.md in the Tico repository. The CAC formula, the Unknown row, "blank is not zero", the Marketing task steps and the rule that a channel's owner changes its stage and budget.
- 2026-11-06: clients/hubcli.py command help. `tico growth cohort set`, `tico growth channel update --stage` (values idea, testing, scaling, paused), `tico kpi log` period flags, `tico brand check` and `tico brand show --section claims`.
- 2026-11-06: the growth-manager playbook `weekly-meeting.md`. The capacity rule (six Live tests across the team, two new per channel owner) and the growth meeting's Monday 08:00 timing.
- Team defaults in this file (the seven-day read rule, the 20% no-segment line, two open asks per owner) were set for this template on 2026-11-06 and are not from Tico's docs.
