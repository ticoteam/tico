# DevRel measures

Definitions for the measures the developer pulse and the onboarding routine report. The team sets its own targets. Each
figure carries its source and the year it was read. Where a source was read only as a search excerpt, the line says so.

## Time to first hello world (TTHW)

- There is no standard definition. Vendor guides differ on where the clock starts (arrival at the docs, sign-up) and where
  it stops (a first successful API response, a first non-error response, or a first command that runs). See the sources.
- Before you report it, write down the start event, the end event, the aggregate (median, with a percentile) and what is
  excluded (for example, accounts that never finish).
- Report the count of people who did not finish next to the median. A median that falls because people quit is not an
  improvement.
- Uncertain: any published "five minutes" target is a vendor claim. Quote it only with its source and date.

## Response time to a public question

- CHAOSS "Issue Response Time" (chaoss.community, read 2026-11-01): the time from an issue's opening to its first response
  from a contributor other than the author. Bot replies are excluded. Results can be split by responder role.
- For a discussion or a forum question, use the same structure: start at the posting, end at the first reply from someone
  other than the asker. State the choice in the report.
- GitHub's Issue Metrics action (GitHub Blog, 2023-07-19) reports time to first response, time to close and, for
  discussions, time to answer. Use it to check the figures where the repository is on GitHub.
- No external reply-time benchmark for developer questions was found in the sources read. The two-working-day target in
  `AGENT.md` is the team's own.

## Code samples and CI

- Python's `doctest` runs the `>>>` examples in docstrings and text files, and it requires an exact match of output (Python
  3 library reference, read 2026-11-01). The page does not mention Markdown fenced blocks, so running those needs a separate
  runner or an extraction step.
- Practitioner advice (Write the Docs, 2022 talk on unit-testing the docs, read as a listing only): keep examples inside
  unit tests and run them in CI, so a change that breaks an example fails the build.
- Examples that cannot run in CI (install commands, calls that need a paid account) need an explicit skip with a reason.
  Without one, teams tend to turn the check off. This comes from secondary posts, so treat it as advice, not a measured result.

## Open questions

- What reply time is normal for developer questions on a public forum in 2026? Not found in the sources read.
- How long should a skipped example stay skipped before someone fixes it? A team decision.

## Sources

- Fern, "Reducing time-to-hello-world for enterprise partners" (buildwithfern.com, July 2026): search excerpt only.
- Kompassify, "developer onboarding guide" (undated): search excerpt only.
- Instruqt, glossary entry "time to hello world" (undated): search excerpt only.
- DZone, "What Is TTFHW?" (undated): search excerpt only.
- CHAOSS, "Issue Response Time" (chaoss.community/metric-issue-response-time/), read 2026-11-01.
- GitHub Blog, "Metrics for issues, pull requests, and discussions" (2023-07-19): search excerpt only.
- Python 3 documentation, "doctest" (docs.python.org/3/library/doctest.html), read 2026-11-01.
- Write the Docs, "Unit Test the Docs: Why You Should Test Your Code Examples" (2022 talk): listing only, not watched.
