---
name: review-routing-corrections
description: Read the routing proposals a person accepted, changed or refused, and decide whether a correction is a one-off note or a rule that belongs in knowledge/routing.md.
---
# Review routing corrections

When to use: once a month, or when a person changes or refuses a routing proposal and the same kind of request has been misrouted before.

Steps:
1. List the routing proposals made in the period from the task history (`hub task list --all`). For each, record the owner proposed, the owner the person chose and the reason given.
2. Count the outcomes: accepted, changed to another owner, refused, no answer. A proposal with no answer is not an acceptance.
3. Group the changes by request type: net-new lead, existing account, data problem, proposal, renewal, or something only a human can take.
4. A correction becomes a rule when the same change repeats with the same reason, or once when the person states a standing rule. Write the rule in `knowledge/routing.md` with its date, its request type and the source task id.
5. A one-off stays a note on its task. Do not write it as a rule.
6. If a rule would send a request to a role that does not exist, do not write it. Route the request to a person and note the gap under Hiring.
7. Report the counts in one line on the weekly summary: proposals, accepted, changed, refused.

Checks before you finish:
- Each rule has its date, request type and source task id.
- A refusal with no reason is recorded as "no reason given", not guessed.
- No rule moves a live negotiation or a price question away from a person.
- No customer is routed as a lead. Each rule that touches accounts names the customer check.

Example (invented, for illustration): "Period 2026-09-01 to 2026-09-28: 9 routing proposals, 6 accepted, 2 changed, 1 no answer. Both changes moved a renewal question from sales to account-manager with the reason 'existing customer'. Second time this period, so a rule is added to knowledge/routing.md with the dates and both task ids."
