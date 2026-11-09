---
name: draft-an-objective-from-a-bet
description: Turn one active bet from the Strategy page into a plain-words objective that names the bet it serves and respects what the bet rules out, or mark the objective as serving no active bet.
---
# Draft an objective from a bet

When to use: when drafting a quarterly plan objective, or checking a draft objective against the published strategy.

Steps:
1. Read `hub strategy show` for the published version: each active bet, what it rules out, and what would prove it wrong.
   If nothing is published, say so and mark the objective "unconfirmed".
2. Pick the bet the objective serves. Write it as one sentence describing an outcome, in plain words. An activity is not an
   objective: "run three webinars" is an activity; "more trials convert to paid" is an outcome.
3. Check the objective against the bet's "rules out" line. If it cuts against that line, do not draft it. Put the question to
   the owner under "Needs a decision".
4. Name the bet and its id on the objective line. If it serves no active bet, mark it "serves no active bet" and write one
   sentence on why it belongs in the plan.
5. Check whether the bet has a KPI-backed goal (`hub goal list --all`). If it has none, its health is gray. Draft a key result
   that could measure it.

Checks before you finish:
- Each objective names one bet, or says it serves none and why.
- No objective is an activity, and none contradicts a "rules out" line.
- Unpublished strategy is marked "unconfirmed" everywhere it is used.

Example (generic, invented): The bet "customers buy the annual plan" rules out discounts deeper than 10 percent. The draft
objective "More studios pay for a full year up front" names that bet. A draft "Run two discount weeks" is rejected: it is an
activity, and it cuts against the rule.
