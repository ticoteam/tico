---
name: "check-a-query-before-trusting-it"
description: "Check a listening query before the team relies on it: test each exclusion by flipping it to AND, read a fixed sample of hits, measure precision on what reached a person, and state the error for the sample size before reporting a rate."
---
# Check a query before trusting it

When to use: a new query goes into the watchlist, a query keeps returning the same irrelevant hits, or a weekly precision check is due.

Steps:
1. Write the query's one job in a line: a reply, a competitor move or a content idea. A hit may prove only that job.
2. Check the shape: a main term, a required context term, and a short exclusion list. If the exclusions run to more than a few
   words, the main term is too weak; fix the main term first.
3. For each exclusion, run one pass with it flipped to AND and read what it would drop. If it drops real mentions, narrow it.
4. Read a fixed sample of 100 or 200 hits, drawn the same way each week. Label each as relevant to the job, not relevant, or
   ambiguous. Ambiguous is a label, not a yes.
5. Measure precision on what reached a person: items acted on divided by items pushed. State the error for the sample size:
   for a rate near 5 percent, about 4 points at 100 items and 3 at 200; for a rate near 10 percent, about 6 points at 100.
   Give the count beside any rate.
6. Write the change in the watchlist and its reason in the digest's coverage line.

Checks before you finish:
- Each exclusion was tested by flip, or is marked untested.
- The sample size and its error are written beside the rate.
- Ambiguous items are counted, not dropped.

Example (generic): "A product query keeps matching a sports homonym. Flip test: the exclusion 'league' drops 3 real mentions, so it
is removed and the context term 'software' is required. Weekly sample: 100 hits, 14 relevant, 5 ambiguous, 81 not relevant.
Precision on the 9 items pushed to a person this week: 6 acted on. At 9 items the error is too wide to judge, so it is reported
as '6 of 9', not as a percentage."
