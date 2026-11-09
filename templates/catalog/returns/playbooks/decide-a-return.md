# Decide a return

Triggered by a task or message asking to return, exchange or refund an order. Budget 10 minutes.

Inputs: the request and any photos, the order record (export, system or the detail attached to the request), `knowledge/policy-checks.md`,
`knowledge/returns-practice.md`, and this customer's returns in the last 90 days from `knowledge/ledger.md`.

Outcome: a decision (allowed, not allowed, or a human's call) with its rule; a reply ready to use; and, when allowed, a refund or label
issued with your Tools, or ready for a person to issue.

Done when: the order is matched to one record; every check in `policy-checks.md` is pass, fail or unknown with its fact; the decision
and its rule are on the task; any refund amount has its steps; the ledger line is written; and the reply is on the task.

---

## 1. Find the order and run the checks

Follow `skills/check-a-return-against-the-order/SKILL.md`. Match the request to one order: number, delivery date, items and variants,
price paid, discounts, and this customer's returns in the last 90 days. No order found is a question to the customer in the reply,
not a refusal.

## 2. Decide which of three

- **Allowed**: every check passes. Issue the requested refund or label with your Tools, or prepare it for a person.
- **Not allowed**: a check clearly fails, and no statutory right applies (`knowledge/returns-practice.md`). Explain the refusal with
  the rule, and whether an exception (store credit, a partial refund) is worth a human's thought.
- **Unclear**: anything unknown, outside the written policy, a damage claim with missing evidence, or an abuse signal. Investigate, ask
  only for the missing facts, and issue nothing.

## 3. Prepare the pieces

The reply: under 120 words, the decision, what happens next (label, where to send, when the refund lands), and the policy line in plain
words. For an allowed return, the label request and the refund amount from `skills/work-out-a-refund-amount/SKILL.md`, naming the order.

## 4. Record

Add the line to `knowledge/ledger.md` with the reason in the customer's words and the product variant. When the payment system confirms
the refund was issued, record the date so the refund clock stops. Commit and finish the task.
