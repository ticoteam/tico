# Bug report practice

What published guidance says about reproducing a problem, writing it up for engineers, and recording a known
error with its workaround. The team's own product facts live in `diagnosis.md` and `workarounds.md`. This file
records general practice, with the source for each point.

## Reproduction

- The steps matter most. A report must let a developer reproduce the problem on their own machine, from the
  steps alone. Mozilla's bug writing guidelines say so directly.
- Say how often the problem happens: every time, sometimes, or once. Mozilla's guidance uses those three
  categories.
- Give the expected result and the actual result after the steps. Keep facts apart from guesses, and label
  a guess as a guess.
- For a problem that seems to affect many people, retest in a clean profile or account, and then isolate the
  one setting that changes the result.
- A "could not reproduce" report helps only when it adds information the engineers do not have yet. Say what
  you tried, and what evidence would change the answer: a request id, an export, a screen recording, a version.

## Known errors and workarounds

- A known error is a problem whose cause or workaround is documented, so that support can give the same answer
  each time. Atlassian's problem management documentation describes keeping a workaround on the record until the
  permanent fix ships.
- Secondary sources disagree on whether a workaround alone makes a problem a known error, or whether a
  documented root cause is needed. This template does not settle that. It records the workaround as tested or
  untested, and records the cause only when someone has confirmed it.

## Sources

- 2026-11-01: Mozilla, "Bug writing guidelines" (undated; read in full): steps to reproduce, frequency, expected
  and actual results, facts apart from guesses, retesting in a clean profile, and what a "could not reproduce"
  report should add.
- 2026-11-01: Atlassian, "Problem management" (Confluence Server documentation, undated; search excerpt read, not
  the full page): a known error record holds a workaround until a permanent fix is shipped.
- 2026-11-01: Wikipedia, "Known error" (undated; search excerpt read): definition, and the disagreement about
  workaround versus root cause. Not used as an authority.
