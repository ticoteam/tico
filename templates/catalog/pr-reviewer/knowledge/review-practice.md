# Review practice

Standing reference for code review: size, comments, what blocks a merge, verdicts and the failure modes of review itself. The
team's own standard is in `standards.md` and its repeated mistakes in `patterns.md`. This file holds general practice and the
sources behind it. Where the two disagree, the team's written standard wins, and the disagreement goes in `decisions.md`.

## What blocks a merge

- Block only on what ships a bug, breaks a rule the repository states, or costs money or data to undo. In practice that means:
  auth and permissions, billing, data loss or deletion, a migration that locks a large table, a removed field that clients still
  send, a job that is unsafe to run twice.
- Everything else is a suggestion, a question or a nit. Labelled comments make this visible to the author (Conventional Comments
  labels such as issue, suggestion, question, nitpick, praise).
- Many blocking items on one change is a signal to talk first, and often to split the change. Say that once, at the top.

## Size

- Google's guidance on small changes says there are no hard rules; about 100 changed lines is usually reasonable, and about 1,000
  is usually too large. Small changes are reviewed sooner and conflict less (Google Engineering Practices, "Small CLs", read as a
  summary, 2026-11-01). The team's line in `standards.md` overrides this default.
- A 2006 industrial study of code reviews (SmartBear, with Cisco) found that defect detection fell in larger reviews and in reviews
  that went faster than the reviewer could read. Its thresholds are second-hand here and are not quoted.
- A change that mixes a refactor with new behaviour should be split: the refactor reviews as "no behaviour change", and the rest
  reviews on its own terms.

## Comments

- Say why, and aim at the code, not the author. A comment that names a consequence ("this returns the old total when the list is
  empty") is a finding. "This looks off" is not.
- Mark what is optional. Google's comment guidance asks reviewers to label nits and optional points so that required changes
  stand out (Google Engineering Practices, "How to write code review comments", read as a summary, 2026-11-01).
- Approve once the change improves the code's health, even if it is not perfect. Waiting for a perfect change delays the author
  and the codebase (Google Engineering Practices reviewer guide, read as a snippet from a secondary summary).

## Verdicts and the head commit

- A verdict belongs to one commit. A push after the review, or a merge into the base branch, makes the verdict stale. Record the
  commit you read, and re-read when the head moves.
- A check that never ran is "not checked", not green. A skipped build, or a status API that returned an error, is not a pass.
- Do not say "ready to merge" while blocking items or failing checks remain.

## Candidates are not findings

- A scanner rule or a pattern match is a candidate. Trace it to the line, the input and the consequence before it becomes a
  comment. A rule that is wrong on most of its hits is noise, however sure it sounds. Track each rule's precision against human
  decisions, and change a trigger only after reading its misses.
- Review can fail by rubber-stamping as well as by noise. Recent work on LLM-assisted review reports that false positives
  eroded reviewer trust, and no study found a direct measure of rubber-stamping (arXiv 2505.16339, 2025, read as a snippet). Read
  the code yourself, and say what you did not read.

## Cross-change risk

- Two open pull requests that touch the same data, route or config can each pass when reviewed alone. Before a verdict on a risky
  change, list the other open changes on the same files.
- Read the base branch's commits since the branch point for the same area. A change can be correct against the old base and wrong
  against the new one.

## Who reviews

- GitHub takes reviewers from the CODEOWNERS file on the base branch, and a branch rule can require an owner's approval (GitHub
  Docs, "About code owners", read as a snippet, 2026-11-01). A risky path with no owner is a question for the team, not a silent
  review.
- DORA's 2025 report, read through secondary coverage, says review becomes the bottleneck as code generation speeds up, and that
  rework rate is now tracked as a delivery metric. Use it as context for review latency, not as a target.

## Sources

- Google Engineering Practices, "Small CLs", google.github.io/eng-practices/review/developer/small-cls.html (fetched as a summary,
  2026-11-01; undated).
- Google Engineering Practices, "How to write code review comments", google.github.io/eng-practices/review/reviewer/comments.html
  (fetched as a summary, 2026-11-01; undated).
- Google Engineering Practices, reviewer guide, google.github.io/eng-practices/review/reviewer/ (snippet only, through a secondary
  summary).
- SmartBear, Cisco code review case study, 2006, read through secondary summaries only.
- "Rethinking Code Review Workflows with LLM Assistance", arXiv 2505.16339 (2025), read as a snippet.
- GitHub Docs, "About code owners" (undated; snippet).
- DORA, "State of AI-assisted Software Development" 2025, read through a secondary article (devops.com, 2025).
