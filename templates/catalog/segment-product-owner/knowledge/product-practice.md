# Product practice for a segment

The standing rules behind the weekly review, the ranking and every brief. The thresholds are this team's defaults. The owner
may change them at setup, and each change is dated in `knowledge/segment.md`.

## Activation

- One activation event per segment, named in `knowledge/segment.md`: the first action a new account takes that the
  segment's own customers say made the product worth using. Choose it from what the usage export shows predicts
  retention, not from what the team hopes it means.
- An account activates once. Count the first time only.
- Activation is read over a window from sign-up (default 14 days). A cohort is read only after its whole window has passed
  for every account in it.

## Retention

- Retention is the share of activated accounts still active at day 30 and day 90 (defaults). "Active" is defined in
  `knowledge/segment.md` in the product's own terms.
- A retention share the cohort is too young for is written as "not yet", never as 0%.
- A change in retention is a trend only when two cohorts of the same length, both past their window, show it.

## Counting accounts

- Count distinct accounts. One account that raises a problem in three tickets and on a call is one account.
- Keep the source of every count: the export or the view, the tasks, the calls, and the dates they cover.
- A customer's segment comes only from the rule in `knowledge/segment.md`. A customer with no segment is counted in the
  totals as unassigned, and is never placed in the segment by guess.
- Minimum cohort size (default 30 accounts): below it, show counts only and say "too few for a rate".

## Ranking problems

- A problem is ranked when at least 3 distinct accounts raise it, from at least 2 sources, in the last 30 days.
- Rank by cost. With a usage export: the activation gap (activation among accounts that hit the problem, minus activation
  among those that did not) times the accounts hit. Without one: the accounts hit, marked "evidence only".
- A request for something the product lacks is listed under requests with its count. It is not ranked above a failure.
- Re-rank at every review. A problem that drops out is kept with its last rank and the date it dropped.

## Briefs

- A brief answers four questions: what the problem is and who has it, what the evidence says, what done looks like in a
  form a stranger could check, and how we will know, with a baseline and a read date.
- A brief defines done. It does not choose the solution.
- One open brief per problem. More evidence is a comment on that brief's task.
- A decline is an answer. It is recorded with its reason and the date to look again.
- Threshold to write a brief (default): the problem is in the top three for two reviews running, and either its activation
  gap is at least 10 points, or, with no usage export, at least 5 distinct accounts are on record.

## Sources

- 2026-11-06: Tico repo docs/growth.md, read for this template: the rule that a blank is not a zero, and the cohort and
  channel definitions that the segment figures must agree with.
- 2026-11-06: Tico repo hubcli command help (`hub task`, `hub meeting`, `hub update`, `hub kpi`): the commands this
  practice names.
- The counting, ranking and brief thresholds above are this team's defaults. No outside benchmark is cited.
