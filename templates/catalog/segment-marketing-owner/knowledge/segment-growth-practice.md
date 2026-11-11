# Segment growth practice

How a segment's new customers and cost per customer are counted and split, how growth tests and asks are run, and where each rule comes from. The standing rules for this bot. Items marked "team default" were set for this template, not taken from Tico's docs; the Growth Manager (`growth-manager`) confirms or changes them at the first review.

## Counts

- **New customer.** One that the counting rule in `knowledge/segment.md` admits, dated by the day that rule names. Each customer counts once.
- **Cost per customer** = (segment spend + segment bot cost) ÷ segment new customers, for the same month. It is blank with no new customers. It is the same formula as a channel's CAC, applied to the segment's share.
- **Goal, floor and ceiling** come from the KPI linked to the segment's goal (`tico goal list --all`, `tico kpi show <id>`). If the goal names no KPI, the review says so and asks the Growth Manager (`growth-manager`).

## Splitting a channel's numbers

For each channel that spends on the segment, use one method, in this order of preference, and write which one:

1. **Direct, actual.** The channel's spend and customers are for this segment alone, such as a campaign or audience named for it. The figure comes from the source `tico growth source list` names for that channel.
2. **Audience split, estimate.** The channel serves more than one segment, and its own targeting report gives a share. Multiply the channel's spend and bot cost by that share.
3. **Share of new customers, estimate.** No targeting split exists. Multiply by the segment's share of that channel's new customers. This assumes the segments cost the same to win, so say so beside the number. It is the least preferred method.

Bot cost is split by the same method as the spend it sits beside.

## Reconciling

- Across all segments plus the no-segment row, new customers add up to each channel's new customers in `tico growth cohort list --month`.
- Spend and bot cost add up to each channel's row the same way.
- A gap is written as a gap, with its size. It is not forced into a segment.

## Customers with no segment

A customer with no segment field is counted in a no-segment row and never dropped. If that row is above 20% of the month's new customers, the reading gives a range: the segment's count is at least its own figure and up to that figure plus the no-segment count. The 20% line is a team default.

## Tests

- A test is an idea task owned by the Growth Manager. Its label says where it is: `growth:ideas`, `growth:running`, `growth:measuring`. It ends done (Kept) or closed (Dropped: failed, inconclusive or stopped). Nothing ends a running test while it runs.
- A test starts only when the Growth Manager (`growth-manager`) picks it at the Monday growth meeting, on a channel at stage `testing` or `scaling` with budget left (`tico growth channel list`). A paused channel gets no new test.
- Team capacity, from the marketing team's growth meeting: at most six running tests across the team, and at most two new tests per channel Owner at a time.
- An idea carries no prediction, hypothesis, score or bet. It says the segment, the change, the channel and the measure. Tico's Marketing tasks have no field for any of the others.
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
- Team defaults in this file (the seven-day read rule, the 20% no-segment line, two open asks per owner, the three split methods' order) were set for this template on 2026-11-06 and are not from Tico's docs.
