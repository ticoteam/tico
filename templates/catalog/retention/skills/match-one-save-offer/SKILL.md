---
name: match-one-save-offer
description: Match at most one save offer to a coded reason from the written policy, checking its limits, and offer none when the policy has none, the limit is used, the customer has decided, or the account belongs to a person.
---
# Match one save offer

When to use: after the reason is coded and before any reply is drafted.

Steps:
1. Look up the reason code in `knowledge/save-policy.md`. Read the offer and its limits: tenure, how often a customer may have it, how much, and for how long.
2. Check this customer's history in `knowledge/reasons.md`. An offer used within the limit means no offer now.
3. Check the customer's state. If they said plainly that they have decided, or asked for the cancellation to be done, no offer is made.
4. Check the account type. A business account at renewal goes to the account owner (`customer-success` or `account-manager`) with no offer from this bot.
5. If the policy has no offer for the reason, the answer is none. Never invent an offer, and never offer a discount the policy does not name.
6. Record the offer, or "none, because" with the reason, on the line.
7. One offer per request at most. If the customer declines, the reply moves to the cancel path. It does not make a second offer.

Checks before you finish:
- The offer is in the written policy and its limits are met.
- One offer at most.
- "None" has its reason on the record.
- No business account at renewal received an offer from this bot.

Example (invented, for illustration): "Coded: price. Policy: one two-month discount per customer per year for a price reason. This customer used one on 2026-03-10 (reasons log), so the limit is met. Offer: none, because the limit is used. Reply: confirm the cancel path and the last billing date."
