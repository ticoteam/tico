---
name: write-acceptance-criteria
description: Write acceptance criteria that an engineer and QA can check, one user-visible behaviour each, with the edge cases the feature touches and a tag for when each can be checked.
---
# Write acceptance criteria

When to use: each acceptance section of a spec, and each change to one. Read it before you write the first criterion.

Steps:
1. Start from the user's outcome in one sentence: who does what and what they get. Write criteria for that outcome,
   not for the internal change. A table, a flag or an endpoint is not an outcome.
2. Write one Given/When/Then per behaviour. Given holds the state (signed in, class full, empty list, no permission,
   offline). When is one action the user takes. Then is a result the user sees or a record they can check.
3. List the states the feature touches: empty, one item, many, the limit (full, maximum length), permission denied,
   offline or timed out, a request sent twice, two people acting at once, a time-zone or daylight-saving boundary,
   the first and the last day of a period. Write a criterion for each one the feature touches. Write "not in scope"
   for the rest, so nobody has to guess.
4. Replace vague words. "Fast" becomes a time at a stated load. "Easy" becomes a number of steps or a named test.
   "Simple" is deleted.
5. Tag each criterion: check before release (a test or a run before the release) or check after launch (visible only
   with real data or real people). An after-launch check names the person who watches it and for how long.
6. Give each criterion a number, so the test plan and the review can refer to it.
7. Write one "Not proved yet" line under the criteria. Say "nothing" if that is true.

Checks before you finish:
- Each criterion has one When and one observable Then.
- No criterion describes something the user cannot see.
- No "fast", "easy", "simple" or "intuitive" without a number or a test.
- Each criterion carries a check tag.
- The "Not proved yet" line exists.

Example (generic, invented):
- AC-3. Given a class is full and the member is not booked, When they tap Join waitlist, Then they see their position
  in the waitlist and the class shows as Waitlisted on their schedule. Check: before release.
- AC-4. Given a waitlisted member, When a place opens, Then they receive one notice with a booking window of 15
  minutes. Check: after launch, watched by the support lead for the first 50 notices.
- Not proved yet: whether the notice reaches members whose mail filters hold it back.
