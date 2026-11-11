# Feedback method

The standing reference for reading customer feedback: which scores to trust, how to count, and how to check coding.
Each claim carries its source and year. The team's own theme list lives in `themes.md`; the team's own counts live in
`trends.md`. This file holds what is true about the method, so each week does not re-derive it.

## Scores and what they can show

- **Customer Effort Score (CES).** It asks how much the customer agrees that the company made it easy to resolve their
  issue. Dixon, Freeman and Toman, "Stop Trying to Delight Your Customers", Harvard Business Review, July-August 2010,
  argued that effort predicts loyalty better than delight or satisfaction. The authors' book, *The Effortless Experience*
  (Dixon, Toman and DeLisi, 2013), repeats the case. The article itself was not read for this file; secondary summaries
  disagree on the author list. Read the article before quoting its figures.
- **CSAT and NPS.** Report each with its answer count and its window. Under about 30 answers one reply moves the score a
  great deal. That threshold is a team rule of thumb, not a published standard.
- **Support volume.** Raw tickets follow the customers who write in. Where the owner gives the active account count, use
  tickets per 100 active accounts, and say which you used.

## Counting

- Count items, not people. One customer with five tickets is five items and one customer. Say both when it matters.
- Remove repeats before counting: the same thread, the same text, a forwarded copy.
- A change between two weeks is noise unless it passes the check in `skills/read-a-change-in-counts/SKILL.md`. The
  square-root rule of thumb assumes items arrive one at a time. A batch import or a single long thread breaks it.
- A quiet week is not a clean week. If a source was silent, the count for it is unknown.

## Coding reliability

- Check the coding. Re-tag ten items at random, cold, and record how many differ from the first tags. Tighten the
  definition when two or more differ.
- Agreement between coders can be stated as percent agreement or as Cohen's kappa, which corrects for chance agreement.
  Say which you report, and set the bar before you check. McHugh (2012, "Interrater reliability: the kappa statistic",
  Biochemia Medica) argues that the common kappa benchmarks are too lenient, and that a kappa near 0.4 should not pass in
  health research. Use the stricter reading as the team's default.
- If a model does a first pass of tagging, a person checks a random sample each week and records the disagreement. This
  is team practice, not a published benchmark.

## Sources

- Dixon, Freeman and Toman, Harvard Business Review, July-August 2010 (secondary summaries read 2026-11-01; article not
  read).
- Dixon, Toman and DeLisi, *The Effortless Experience* (2013) (secondary summaries read 2026-11-01).
- McHugh, M.L., "Interrater reliability: the kappa statistic", Biochemia Medica 22(3), 2012, DOI 10.11613/BM.2012.031
  (abstract read 2026-11-01; benchmark table not read).
- Team practice: the re-tag check, the 30-answer note and the per-100-accounts rule, as set in the playbooks.
