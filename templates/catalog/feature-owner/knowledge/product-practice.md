# Product practice for an area

The standing rules behind the weekly review, the ranking and every pitch. The thresholds are this team's defaults. The owner
may change them at setup, and each change is dated in `knowledge/area.md`.

## Activation

- One activation event per area, named in `knowledge/area.md`: the first action a new account takes that the
  area's own customers say made the product worth using. Choose it from what the usage export shows predicts
  retention, not from what the team hopes it means.
- An account activates once. Count the first time only.
- Activation is read over a window from sign-up (default 14 days). A cohort is read only after its whole window has passed
  for every account in it.

## Retention

- Retention is the share of activated accounts still active at day 30 and day 90 (defaults). "Active" is defined in
  `knowledge/area.md` in the product's own terms.
- A retention share the cohort is too young for is written as "not yet", never as 0%.
- A change in retention is a trend only when two cohorts of the same length, both past their window, show it.

## Counting accounts

- Count distinct accounts. One account that raises a problem in three tickets and on a call is one account.
- Keep the source of every count: the export or the view, the tasks, the calls, and the dates they cover.
- A customer's area comes only from the rule in `knowledge/area.md`. A customer in no area is counted in the
  totals as unassigned, and is never placed in the area by guess.
- Minimum cohort size (default 30 accounts): below it, show counts only and say "too few for a rate".

## Ranking problems

- A problem is ranked when at least 3 distinct accounts raise it, from at least 2 sources, in the last 30 days.
- Rank by cost. With a usage export: the activation gap (activation among accounts that hit the problem, minus activation
  among those that did not) times the accounts hit. Without one: the accounts hit, marked "evidence only".
- A request for something the product lacks is listed under requests with its count. It is not ranked above a failure.
- Re-rank at every review. A problem that drops out is kept with its last rank and the date it dropped.

## Pitches

- A pitch is 280 characters at most: the problem in the customer's words, who has it, and the fix in one line. The
  evidence goes in a comment below it; what done looks like and how we will know go in the spec.
- One open task per problem. More evidence is a comment on that task.
- A decline is an answer. It is recorded with its reason and the date to look again.
- Threshold to write a pitch (default): the problem is in the top three for two reviews running, and either its activation
  gap is at least 10 points, or, with no usage export, at least 5 distinct accounts are on record.

## Sources

- 2026-11-06: Tico repo docs/growth.md, read for this template: the rule that a blank is not a zero, and the cohort and
  channel definitions that the area's figures must agree with.
- 2026-11-06: Tico repo hubcli command help (`tico task`, `tico meeting`, `tico update`, `tico kpi`): the commands this
  practice names.
- The counting, ranking and pitch thresholds above are this team's defaults. No outside benchmark is cited.
