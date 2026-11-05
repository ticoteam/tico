---
name: reproduce-from-a-known-state
description: Reproduce a customer problem from a stated starting state, with every value written down, two tries, and the customer's environment compared where it differs, so that "reproduced" and "could not reproduce" both mean something a developer can check.
---
# Reproduce from a known state

When to use: before you call a problem reproduced, before you write "could not reproduce", and before you hand a
problem to engineering as a bug.

Steps:
1. Write the starting state: the account or test data, the product version, the plan, the browser or client, the
   region and the integration. Use the sandbox where it can show the problem. Otherwise use only the data and access
   granted for this work (AGENT.md, Sending).
2. Write the steps as numbered actions, each with the value you used. A step that says "set the time zone" is not
   enough: write the zone.
3. Run the steps once. Note the result, with the time and the version.
4. Run them a second time from the same starting state. A result that changes between the two runs is itself a
   finding: write it down, and treat the problem as intermittent until more evidence says otherwise.
5. Where the customer's environment differs from yours, change one thing at a time and note each change. Stop when
   the result matches the customer's report or when you have tried the listed differences.
6. Reproduced: the verdict is "bug" or "setup mistake", with the steps and the values. Not reproduced: write what you
   tried, the two results, and the evidence that would change the answer (a request id, an export, a recording, a
   version).
7. Confirm the version the log or bundle came from. A log from before a fix shows the old behaviour, so ask for a
   fresh one and say which version it was.

Checks before you finish:
- The starting state and every value are written down.
- Both tries are recorded, including any difference between them.
- "Could not reproduce" names the tries and the evidence that would change it.
- The version of every log or bundle you relied on is named.

Example (invented, for illustration only): a customer reports that a booking shifts by an hour. The starting state is a
test account set to one zone, with a booking made at 18:00 in another zone. The first run shows 17:00 after sync. The
second run, from the same state, also shows 17:00, so the verdict is "bug, reproduced twice". The customer's own zone
setting was then checked and matches the test. The write-up names the version and the two runs.
