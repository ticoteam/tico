# Test and flaky-test practice

General reference for the test health report and the flaky test register. Each figure carries its source and the year it was
read. Where a source was read only as a search excerpt, the line says so. The team sets its own thresholds in
`knowledge/thresholds.md`; nothing here is a target for them.

## Flaky tests

- Rasheed et al., "Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review" (arXiv 2212.00908, 2022, 651
  sources, grey literature included). It organises the work into causes, detection, impact and responses. The abstract does not
  say which response teams use most, so do not quote one as the most common.
- Team threshold, as a starting default: two fail-then-pass flips on the same commit and job within two weeks. The threshold is a
  team choice, so write it in `knowledge/thresholds.md` with its date.
- A test is named only after its log has been read. A candidate whose log cannot be read is "unknown", never counted.
- Every flaky test in the register has a decision (fix or quarantine), an owner and a deadline. A quarantine with no deadline is
  a skip, and it is reported as one.

## Skipped jobs and required checks

- GitHub's status-checks reference (docs.github.com, read 2026-11-01, search excerpt only) says a skipped job reports success and
  does not block a merge, even when it is required.
- Some third-party reports describe a skipped required check as blocking, and they conflict with the reference. The difference is
  not explained in the sources read. Test the exact branch-protection setup on a throwaway pull request before you rely on either
  behaviour.
- A skipped test is not a pass. Report it as skipped, with its ticket and its age.

## Reporting

- Report medians over a stated window, with the window and the number of runs. A median over a few runs is noise.
- Keep the cause next to the number it explains, so a reader can see whether the number came from a flaky test, an outage or a
  real slowdown.

## Open questions

- How often should a quarantined test be re-checked before it is fixed or deleted? A team decision.
- Whether a must-test path needs a second test on the same behaviour while its first test is quarantined. A team decision.

## Sources

- Rasheed et al., arXiv 2212.00908 (2022), abstract read 2026-11-01.
- GitHub Docs, the status checks reference (docs.github.com), search excerpt read 2026-11-01.
- Third-party write-ups on skipped required checks (years not shown), search excerpts only, conflicting; not verified.
