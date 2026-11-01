# Review and delivery practice

Reference for the thresholds in `knowledge/measures.md` and for the review checks. Each source carries the date it
was read. Thresholds the team sets are marked as the team's own.

## Review speed

- Google's engineering practices, reviewer guide, "speed" (google.github.io/eng-practices/review/reviewer/speed.html,
  read 2026-11-01): "One business day is the maximum time it should take to respond to a code review request." The
  page says the response should come first thing the next morning. For a large change, it advises asking the author
  to split it into smaller changes that build on each other. If the change cannot be split and review time is short,
  comment on the overall design and send it back.
- The stuck thresholds in onboarding (2 working days with no review, 5 working days open) are the team's defaults,
  not Google's numbers. The owner sets them.

## Change size

- Google's engineering practices, "Small CLs" (google.github.io/eng-practices/review/developer/small-cls.html, read
  2026-11-01): a change should be one self-contained change that does one thing. The page says "100 lines is usually
  a reasonable size for a CL, and 1000 lines is usually too large," and that the judgement is the reviewer's. It adds
  that a change spread across many files is larger than its line count suggests.

## Merge gates

- GitHub's status-checks documentation, read as a search excerpt (2026-11-01): a skipped job reports success and does
  not block a merge, and a third-party check can report differently. Test the exact branch-protection setup, not a
  description of it.
- A gate that times out is reported as skipped and named in the summary. It is never reported as passed.

## Delivery measures

- DORA's guide on the four keys (dora.dev/guides/dora-metrics-four-keys, last updated 2026-01-05, read 2026-11-01)
  describes change lead time, deployment frequency, change fail rate, failed deployment recovery time and deployment
  rework rate. It gives no numeric benchmarks. Each team sets its own targets.

## Open questions

- No source read gave a benchmark for review wait by team size. The team sets its own.

## Sources

- google.github.io/eng-practices/review/reviewer/speed.html, read 2026-11-01.
- google.github.io/eng-practices/review/developer/small-cls.html, read 2026-11-01.
- GitHub status-checks documentation, search excerpt, 2026-11-01. Not read in full.
- dora.dev/guides/dora-metrics-four-keys, last updated 2026-01-05, read 2026-11-01.
