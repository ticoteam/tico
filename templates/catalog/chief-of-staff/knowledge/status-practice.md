# Status and stalled practice

How this bot judges goal colours, stalled items and the Monday agenda. Vendor guides disagree on the numbers, so the
team sets its own thresholds in `knowledge/rhythm.md`. This file records what the sources say and the conventions the
bot uses until the team chooses.

## Colour and confidence scales

- Scales differ by tool. One check-in guide maps a 0 to 10 confidence score to green at 8 to 10, yellow at 4 to 7 and
  red at 0 to 3. Another tool defaults to green at 80 percent of progress or more, yellow at 70 to 79 and red below 70.
  A third offers Low, Medium and High confidence levels whose ranges the admin can change. Record the sources' numbers
  as they are; do not average them.
- Lattice's help article steers check-ins toward how confident the team is about an objective, not only where its
  progress stands. Keep both: a reading shows where a goal is, a confidence shows where it is going.
- A goal with no reading this week is "no reading". It is never "on track".

## Yellow that stays yellow

- One tool's check-in guide advises no more than two yellow (at risk) check-ins in a row. Its changelog describes a
  warning on a third. The two pages give different limits, so the bot flags a goal that is yellow for a third check-in
  in a row, and `knowledge/rhythm.md` holds the team's number.

## Stall signals

- A goal or task counts as stalled when its confidence falls, it has had no change for two check-ins, or a milestone
  has been missed.
- The day counts in `knowledge/rhythm.md` (default 10 for goals, 7 for tasks) are the team's threshold. Use them with
  the check-in count, not in place of it.

## Monday agenda

- Decisions come first, each item has one owner, and any overflow is named rather than cut quietly. The meeting
  length sets how many items fit.
- Claims that a silent pre-read saves meeting time come from vendors. They are not measured here.
- A large retailer's practice of a written memo read in silence at the start of a meeting is reported secondhand. It
  was not checked against a primary source.

## Limits

- The sources are vendor help pages, blog posts and one tool's changelog, read on 2026-11-01 as search results, not
  as full pages. No independent study has tested colour scales against outcomes. The thresholds here are conventions
  to choose from, not facts.

## Sources

- 2026-11-01 (search result): Teamflect, "How to Run OKR Check-In Meetings": https://teamflect.com/blog/performance-management/how-to-run-okr-check-ins
- 2026-11-01 (search result): MangoApps, OKR hub app FAQ: https://www.mangoapps.com/help/okr_hub_app_faq
- 2026-11-01 (search result): Synergita support, check-in and confidence levels: https://support.synergita.com/support/solutions/articles/4000208523
- 2026-11-01 (search result): Tability, check-ins best practices: https://guides.tability.io/docs/tutorials/track-progress-with-check-ins/check-ins-best-practices
- 2026-11-01 (search result): Tability changelog, consecutive at-risk warnings: https://feedback.tability.io/changelog/consecutive-at-risk-warnings
- 2026-11-01 (search result): Lattice help, check-in confidence: https://help.lattice.com/hc/en-us/articles/4429889110423
