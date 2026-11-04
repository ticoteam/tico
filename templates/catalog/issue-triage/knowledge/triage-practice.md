# Triage practice

Standing reference for issue triage, bug severity, duplicates, stale issues and test plans. The team's own label scheme is in
`labels.md`, its report checklist in `repro-checklist.md`, and its owners in `areas.md`. This file holds general practice and the
sources behind it. Where the two disagree, the team's written rule wins, and the disagreement goes in `decisions.md`.

## Kind, severity and priority are three different things

- Kind says what the report is: a bug, a feature request, a question, or not actionable. Correct a reporter's label when it is
  wrong.
- Severity says how bad the effect is when it happens: the scope of the defect, such as data loss, a blocked flow, or a cosmetic
  fault. Mozilla's defect severity guide describes severity as the scope of a defect's effect, and asks that a new bug start
  unset until someone assesses it (Mozilla Firefox Source Docs, "Defect Severity", read 2026-11-01).
- Priority says when it will be worked on. It is the owner's decision, not triage's. A severe defect can be low priority, and a
  cosmetic one can be high priority for a launch. Do not propose a priority to a reporter.

## Report quality

- A report is actionable when a person who did not write it can run the steps and see the problem: the version or build, the
  environment, the steps from a clean state, the expected and actual results, how often it happens, and evidence (a log, a
  recording, a screenshot with a time).
- Ask for the missing items only. One request with the items named is better than three requests.
- Record how many attempts reproduced it. "Happened once" is a different report from "happens on every save".
- A bug that disappears on the latest release is still a bug in the old one. Say which version the fix is in, if one exists.

## Duplicates and related issues

- Search on the error text, the screen or function, and the trigger before you propose a label. A title match with different steps
  is related, not a duplicate.
- When a fix removes the reported symptom, the open ticket about that symptom is a duplicate of the fix, not a related issue. Name
  the fix's pull request.
- Keep the canonical issue: the one with the steps, the logs and the discussion. Copy across what the duplicate adds.

## Stale issues

- Many repositories use a stale rule. GitHub's stale action, with its defaults, labels an issue after 60 days without activity and
  closes it 7 days after the label, unless new activity removes the label (github.com/actions/stale, read 2026-11-01). Use the
  repository's own rule and its own numbers. Only a maintainer closes an issue.
- A reporter who has not answered a request for information is a waiting item, with the date you asked. Remind once. Then the
  owner decides.

## Test plans and regression

- Rank the changes in a release by likelihood of breaking times the impact if it does. Money, sign-in, data loss and migrations
  rank first; changes with no test files rank high.
- A case a human can follow has preconditions, steps, the expected result and one negative case (wrong input, an expired session, a
  second click). Ten to twenty-five cases cover a release of ordinary size. More than that is a plan nobody runs.
- The regression checklist is the short list of paths that must still work whatever changed. Add to it every flow a recent bug
  broke. An escaped defect (found after release) is a question for the next plan: which case would have caught it?
- A result of "not run" is its own result, with the reason. A plan that says "no failures" after half the cases were skipped has
  hidden the skipped half.
- A check that never fails proves nothing. Before a negative check is trusted, see it fail once on purpose, or read the code path
  that would make it fail.

## Flaky checks

- A check that fails and then passes on a rerun is unstable until proven otherwise. Record it before rerunning, rerun a small fixed
  number of times where the task and Tools allow it, and find the assumption the test makes. See `skills/triage-a-flaky-test/SKILL.md`.
- Quarantining a flaky check needs an owner, an issue and a date. A test that goes quiet with no record is lost coverage.

## Retrospective

- Once a week, check last week's proposals against what happened: which labels stuck, which duplicates were right, which
  requests got an answer, which theme grew. Write the misses in `memory/learnings.md`. A change to a threshold needs a repeated
  miss, not one case.

## Sources

- Mozilla, Firefox Source Docs, "Defect Severity", firefox-source-docs.mozilla.org/bug-mgmt/guides/severity.html (fetched as a
  summary, 2026-11-01). Undated on the page.
- GitHub, actions/stale README, github.com/actions/stale (fetched, defaults quoted, 2026-11-01). Current defaults; the action's
  settings can change, so check the repository's own workflow.
- Google Testing Blog, "Flaky Tests at Google and How We Mitigate Them", 2016. The post's figures were not read for this file;
  not quoted.
- Practitioner guides on bug reports and labels (for example BrowserStack's bug report guide, undated) were read as snippets only.
  They agree with the checklist above but are not cited as standards.
- The test-plan sizes and the regression rules are the team's working practice, not a published benchmark.
