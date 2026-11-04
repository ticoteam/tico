---
name: run-a-pay-equity-check
description: Compare pay between groups of people with like-for-like controls, report only aggregated results for groups of five or more, name the factors that explain a gap, and hand the unexplained remainder to a human as a question rather than a finding.
---
# Run a pay equity check

When to use: when the owner asks for a pay equity check on a group, or on a review cycle, and has attached the data for it.

Steps:
1. Confirm the purpose and the owner who acts on the result, from `state.md`. Use only the attached data. Do not add data
   from elsewhere without the owner's yes.
2. Check the group sizes before any comparison. Any group under five is combined with the nearest group, or left out with a
   note. Do not stack filters (team, tenure, location) that shrink a reported group below five.
3. Compare like with like: the same job family and level first. Then hold location and tenure constant, and say which were
   used.
4. For each comparison, state the gap in average pay after these controls, and the group sizes. Do not use a performance
   rating as a control unless the owner has decided to and written down why.
5. Split the gap into the part the listed factors explain and the remainder. Call the remainder "unexplained" and give its size
   in percent. Name the limits: missing factors, small groups, data quality.
6. Treat an unexplained gap of 5 percent or more as the owner's review trigger, the same level the EU pay transparency rules use
   for a joint assessment. Below it, record the result and the next check date.
7. Write the result as aggregates with the method, then delete the data file from the working tree. Name no individual.

Checks before you finish:
- Every reported group has five or more people.
- The factors held constant, and any left out, are named with the reason.
- The remainder is called "unexplained", with its size and method; it is not called discrimination.
- The result is handed to the named owner as a question for review.
- The data file is deleted, and nothing person-level is in `reports/` or `knowledge/`.

Example (invented, for illustration): "Engineering, level 2 to 3. Group A 38 people, Group B 41 people. Gap in average pay
before controls 6.8 percent. After job family, level, location and tenure: unexplained gap 3.1 percent, below the 5 percent
trigger. Method: linear model on log pay, performance ratings excluded (owner decision recorded 2026-09-30). Limit: tenure
is missing for 4 people in Group A. Next check on the next review cycle."
