---
name: measure-delivery-without-ranking-people
description: Compute the delivery measures a human chose (shipped count, review wait, stuck pull requests, lead time, deploy frequency, failed deploys, time to restore) by team and repository, with the definition, the period and the source, never by person, and with a missing value marked as unrecorded rather than zero.
---
# Measure delivery without ranking people

When to use: before you compute, compare or publish any delivery measure in the weekly summary.

Steps:
1. Name the measure and its definition from `knowledge/measures.md`, including its start and end points. Review wait
   starts when the review was requested, and ends at the first submitted review. It does not start at the reviewer's
   first look.
2. Name the unit: a repository or a team. A measure is never about a named person, and a per-person breakdown is not
   produced even if asked. If asked, say in one line that measures are by team and repository, and point to the team view.
3. Compute from the source and state the period. For waits, give the median and the longest, with the number of items.
   Say so when the sample is small.
4. A value that cannot be read is "unrecorded", never zero. A quiet week is "no changes merged", not a good week.
5. Lead time runs from the first commit to the release or deploy that carried it. If no release or deploy can be read,
   write "not measurable from the sources I have".
6. Count each pull request, release or deploy once. A pull request that was closed and reopened, or force-pushed, is
   still one item.
7. Compare with the last period only when the definition did not change. If it changed, say so and do not compare.
8. Write each measure in the summary with its source and period.

Checks before you finish:
- Each measure has a definition, a unit, a period and a source.
- No value is zero where it should be unrecorded.
- No measure names a person.
- Any comparison uses the same definition as the period it compares to.

Example (invented, for illustration): Review wait for the web-app repository, 2026-10-20 to 2026-10-26, from 9 pull requests: median 0.8 working days, longest 4 days. Failed deploys: not recorded, because the deploy log was not readable. Written as "unrecorded", not "0".

Reference: DORA's four-keys guide, read 2026-11-01, describes the delivery measures and gives no numeric benchmarks (`knowledge/review-practice.md`).
