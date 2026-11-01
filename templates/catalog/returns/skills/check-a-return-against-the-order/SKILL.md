---
name: check-a-return-against-the-order
description: Check a return request against the written policy and the order record, in a fixed order of questions, and decide allowed, not allowed or a human's call, with each fact cited.
---
# Check a return against the order

When to use: for every return, exchange or refund request, before any reply or refund is prepared.

Steps:
1. Find the order: the order number from the request, matched to the order record (export, system, or the detail attached to the request). If nothing matches, ask the customer for the order number in the reply, and do not refuse.
2. Ask the questions in this order, and write each answer with its source:
   a. Is it inside the window? Count from the delivery date on the order record, not the order date. Use the longer of the
      policy window and any statutory window. Note the rule used.
   b. Is the item one the policy accepts? Final-sale and personalised items are checked against the policy. A statutory right
      can still apply: a final-sale label alone does not remove the EU withdrawal right (`knowledge/returns-practice.md`).
   c. Is the condition acceptable? Use the policy's words. Photos from the request count as evidence.
   d. Has this order line already been returned or refunded? Check the ledger for that line: the quantity returned and the amount
      refunded so far. Customer return history is a separate pattern check (`skills/flag-a-return-pattern/SKILL.md`), not a reason
      to refuse this return.
   e. Who pays the return shipping under the policy?
   f. Is it a refund, an exchange or store credit, and does the policy allow that for this case?
3. Write pass, fail or unknown for each check in `knowledge/policy-checks.md`, with the fact behind it.
4. Decide: allowed (every check passes), not allowed (a check clearly fails, with the policy line), or a human's call (the policy is silent, a safety issue, a dispute, or the evidence conflicts).
5. Before refusing anything, check `knowledge/returns-practice.md`. A consumer right applies on top of the policy in some markets, and where it applies it overrides a policy line that would refuse the request.
6. Record the decision, the rule, the amount if any (from `skills/work-out-a-refund-amount/SKILL.md`) and the sources in `knowledge/ledger.md`.

Checks before you finish:
- Each question has an answer with its source. None is answered from memory.
- A refusal cites the policy line or the statutory rule.
- A consumer right still inside its window is not refused on the policy alone, and a statutory right is not overridden by a
  policy line.
- The decision and the rule are in the ledger.

Example (invented, for illustration): "Order A-1042, delivered 2026-09-12; request 2026-09-25. Window: 14 days from delivery under the shop's policy (section 2); day 13, inside. Final sale: no. Condition: photos show the item unworn with its tags. Already refunded on this line: none. Shipping: paid by the shop for faulty items only, so the customer pays here. Decision: allowed. Refund amount: see the worked example in the refund skill."
