---
name: choose-the-next-test
description: Choose one cheap test for the opportunity with the most evidence and the least testing, name its riskiest assumption, and write the result that would change the decision before anyone runs it.
---
# Choose the next test

When to use: when the weekly digest or a brief needs one thing to test. Read it when a brief asks what to do next.

Steps:
1. Read the opportunities with their source counts. Keep only those that serve the outcome in `knowledge/opportunities.md`.
2. Pick the one with the most separate sources and the least testing. Interviews count as sources. Only a test counts
   as testing.
3. Write its riskiest assumption in one sentence, as something that could be false. "Most teams move bookings by hand
   each week" can be false. "Users want this" cannot.
4. Pick the cheapest test that could prove the assumption wrong within a week: a few interviews on past behaviour
   (`skills/write-a-discussion-guide/SKILL.md`), a usability round (`skills/plan-a-usability-round/SKILL.md`), or a count
   from existing records.
5. Write the result that would change the decision before the test runs. For example: "If fewer than three of five
   describe a manual move in the last month, drop the opportunity."
6. Name the human who runs it. Do not choose the solution.

Checks before you finish:
- One test, not a list.
- The assumption can fail.
- The result that changes the decision is written before the test.
- The test can run within a week, and a named person runs it.
- No solution is chosen.

Example (generic, invented):
- Opportunity: "move a booking between two calendars", with four separate sources and no test yet.
- Assumption: "Most teams with two calendars move bookings by hand every week."
- Test: five short interviews about the last time a booking moved. Runner: the product lead.
- Decision rule: if fewer than three of five describe a manual move in the last month, drop the opportunity. If four or
  more do, write the brief for the sync question.
