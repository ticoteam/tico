---
name: "check-a-query-before-trusting-it"
description: "Check a Listening source's search before the team relies on it: test each exclusion by flipping it to AND, read a fixed sample of hits, measure the share that fit the search's job, and state the error for the sample size before reporting a rate."
---
# Check a query before trusting it

When to use: you are about to propose a new search source, a source keeps returning the same irrelevant hits, or a weekly precision check is due.

Steps:
1. Write the query's one job in a line: a reply, a competitor move or a content idea. A hit may prove only that job.
2. Check the shape: a main term, a required context term, and a short exclusion list. If the exclusions run to more than a few
   words, the main term is too weak; fix the main term first.
3. For each exclusion, run one pass with it flipped to AND and read what it would drop. If it drops real mentions, narrow it.
4. Read a fixed sample of 100 or 200 hits, drawn the same way each week. Label each as relevant to the job, not relevant, or
   ambiguous. Ambiguous is a label, not a yes.
5. Measure precision: relevant hits divided by hits read. State the error for the sample size: for a rate near 5 percent,
   about 4 points at 100 items and 3 at 200; for a rate near 10 percent, about 6 points at 100. Give the count beside any rate.
6. Write the search you would use and its reason on the task. Sources are edited under Listening; you do not change them, and
   you keep saving everything the current one shows until it is changed.

Checks before you finish:
- Each exclusion was tested by flip, or is marked untested.
- The sample size and its error are written beside the rate.
- Ambiguous items are counted, not dropped.

Example (generic): "A product query keeps matching a sports homonym. Flip test: the exclusion 'league' drops 3 real mentions, so it
is removed and the context term 'software' is required. Weekly sample: 100 hits, 14 relevant, 5 ambiguous, 81 not relevant,
reported as '14 of 100, about 7 points either way'. A second source returned 9 hits all week, 6 relevant: at 9 items the error is
too wide to judge, so it is reported as '6 of 9', not as a percentage."
