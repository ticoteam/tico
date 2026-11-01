# CI and deploy practice

General reference for the CI health report and the fix plans. Each figure carries its source and the year it was read.
Where a source was read only as a search excerpt, the line says so. The team sets its own thresholds in
`knowledge/thresholds.md`; nothing here is a target for them.

## Delivery measures

- DORA's guide to the four keys (dora.dev, last updated 2026-01-05, read 2026-11-01) lists: change lead time, deployment
  frequency, failed deployment recovery time, change fail rate, and deployment rework rate. The guide gives no numeric
  benchmarks. Use it for definitions, and state the window and the start and end events in each report.
- A deploy counts as a deploy only when the running version is recorded (see the `verify-a-passing-check` skill). A
  deploy job that is green but never reached the service is not a deployment.

## Flaky tests

- Rasheed et al., "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review" (arXiv 2212.00908, 2022,
  651 sources, grey literature included). It organises the work into causes, detection, impact and responses. The
  abstract does not say which response teams use most, so do not quote one as the most common.
- Team threshold, as a starting default: two fail-then-pass flips on the same commit and job within two weeks. The
  threshold is a team choice, so write it in `knowledge/thresholds.md` with its date.
- A test is named only after its log has been read. A candidate whose log cannot be read is "unknown", never counted.

## Required checks and skipped jobs

- GitHub's status-checks reference (docs.github.com, read 2026-11-01, search excerpt only) says a skipped job reports
  success and does not block a merge, even when it is required.
- Some third-party reports describe a skipped required check as blocking, and they conflict with the reference. The
  difference is not explained in the sources read. Test the exact branch-protection setup on a throwaway pull request
  before you rely on either behaviour.
- A common pattern is one final gate job that depends on every other job and always runs. It fails unless each
  dependency succeeded. Confirm it on the team's setup before you make it the only required check.

## Reporting

- Report medians over a stated window, with the window and the number of runs. A median over a few runs is noise.
- Keep the failure cause next to the number it explains, so a reader can see whether the number came from a flaky test,
  an outage or a real slowdown.

## Open questions

- How often should a quarantined test be re-checked before it is fixed or deleted? A team decision.
- Whether the merge check should block on a single gate job or on each job. Test it on the team's setup first.

## Sources

- dora.dev, the four keys guide (dora-metrics-four-keys), read 2026-11-01 (last updated 2026-01-05).
- Rasheed et al., arXiv 2212.00908 (2022), abstract read 2026-11-01.
- GitHub Docs, the status checks reference (docs.github.com), search excerpt read 2026-11-01.
- Third-party write-ups on skipped required checks (years not shown), search excerpts only, conflicting; not verified.
