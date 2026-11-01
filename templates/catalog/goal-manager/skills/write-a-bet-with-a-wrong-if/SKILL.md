---
name: write-a-bet-with-a-wrong-if
description: "Drafts one strategy bet with what it rules out and a wrong-if the record can check. Use it when drafting or rewriting a bet for a strategy proposal, or when a tripped bet needs a new wrong-if."
---

# Write a bet with a wrong-if

## When to use

- You are drafting a `strategy_draft` proposal, or rewriting one bet in it.
- A bet's wrong-if tripped, and the owner's choice is to keep the bet with a new wrong-if.

## Steps

1. Read the evidence first: `hub strategy show` for the published bets, then `hub doc search --market "<topic>"` and `hub market show <id>` for the pages the bet rests on.
2. Write the statement as one sentence: a decision the team has made that could turn out wrong. "We sell to small teams first" is a bet. "We care about customers" is not.
3. Write `rules_out`: what the team gives up by choosing it. If you cannot name anything, the statement is not a bet yet. Rewrite it or drop it.
4. Write `why` in one or two sentences, and put the market ids it rests on in `cites`.
5. Write one wrong-if, in this order of preference:
   - a KPI with a threshold: `{"kind": "kpi", "kpi_id": "<id>", "direction": "above", "threshold": 4, "check_by": "2027-03-31"}`;
   - a market entity: `{"kind": "thesis", "market_id": "<id>", "check_by": "2027-03-31"}`;
   - a dated sentence, marked as not yet measurable, with the KPI that would make it measurable named in the claim.
6. Check the wrong-if has not already tripped: read the latest reading or the market delta. A condition that is already crossed is not a wrong-if.
7. Set `status` to `active` only when the bet has a rules-out and a wrong-if. Keep three to five bets in the draft, and no more than seven active.

## Checks before you finish

- Every number in the bet came from a reading, a market page or the owner. None came from memory.
- The wrong-if is observable: a threshold or a dated check, not a feeling.
- `rules_out` names something real that the team gives up.
- The bet does not set a goal, a KPI target or a bot's task. Those are separate proposals.

## Example

A bet for a made-up scheduling product:

- statement: "We sell to small teams before large ones."
- rules_out: "Enterprise contracts and custom integrations for the next year."
- wrong_if: `{"kind": "kpi", "kpi_id": "<id>", "direction": "below", "threshold": 8, "check_by": "2027-03-31"}`, meaning monthly trial-to-paid conversion below 8 percent.

The segment, the threshold and the date are invented. The owner sets the real threshold.
