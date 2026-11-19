# Raise prices on existing customers

This is the one pricing move that cannot be undone: a customer who leaves over a notice does not come back when the
price does. The bot never changes an existing customer's price on its own. It builds everything below into one
recommendation with its evidence, and the owner decides. One OK covers the notice template, the customer list and
the wave plan; after it, the bot sends exactly those waves at exactly those prices.
The numbers in the illustrations are invented. The thresholds marked "default" are this skill's own and can be
changed in `knowledge/pricing.md`.

## 1. Decide whether to recommend it at all

Recommend a rise for existing customers when at least one is true, with evidence:

- New customers have paid the higher price for at least one full billing period without retention getting worse
  (`price-tests.md`).
- Old plans sit well below today's list price for the same product: a legacy gap.
- The product has gained real value since they bought, and they use it.
- Cost to serve has risen and the plan is under the margin floor.

Do not recommend a rise to hit a quarter's number with no new value to point to, during an unresolved outage or service
problem, or on customers already flagged as likely to leave.

## 2. Size it

Use `skills/size-a-price-change`. For a rise of p (as a fraction of the current price):

```
Break-even revenue churn = p / (1 + p)
```

Illustration: $59 to $69 is p = 10 / 59 = 16.9%. Break-even = 0.169 / 1.169 = 14.5%. If more than 14.5% of the
revenue on that plan leaves because of the rise, the rise lost money.

Write best, expected and worst cases from the company's own history: what happened at the last change, how new
customers reacted to the test, and how price-sensitive each segment has been. Where there is no history, say so and
make the first wave the evidence.

## 3. Read the contracts

Before any list is built, the teammate who keeps contracts reads the price terms for every customer with a signed
agreement: fixed-price periods, caps on increases, notice periods, "most favoured customer" clauses, auto-renewal
terms. A customer whose contract blocks the change is out until renewal. Do not infer terms from the plan name.

Also read every written promise the company has made about price ("price locked for life", launch offers). They are
kept. If one is too costly to keep, that is the owner's decision with a lawyer, not a line in a wave plan.

## 4. Segment

Never one rise for everyone. Split customers on two things, both from data:

- **Value received**: usage against plan, features used, results where they can be measured, tenure.
- **Gap to list**: how far below today's list price they pay.

| | Small gap to list | Large gap to list |
| --- | --- | --- |
| **High value received** | Move to list | Move toward list in steps, with the step cap |
| **Low value received** | Leave, or offer a smaller plan | Offer a smaller plan at list; do not raise into a cancellation |

Then take out: customers with a blocking contract, customers in an open complaint or dispute, customers who joined in
the last 90 days (default), and any the account owner names with a written reason.

**Step cap (default): no customer's bill rises more than 25% in one step.** A larger legacy gap closes over more than
one step, at least one billing year apart.

**Grandfathering** means letting existing customers keep the old price. Use it as a bridge, with an end date: until
the next renewal, or for a fixed number of months. A promise with no end date creates a group of customers the company
can never reprice, on plans it must support forever. Do not make it. If one already exists, it is kept.

## 5. Plan the waves

- **Wave 1: about a tenth of the customers in the change** (default), chosen at random within each segment, not the
  friendliest accounts.
- **Later waves** follow at least two weeks after the first charge of the wave before, each larger than the last.
- **Stop rule (default), written before wave 1:** pause the next wave if the revenue lost from a wave's customers,
  from notice to 30 days after the first higher charge, passes half of the break-even revenue churn. Illustration:
  break-even 14.5%, so pause at 7.25%.
- Largest accounts get a conversation with their account owner before the notice, not after.

## 6. Notice

The notice period is the longest of: the contract, the law where the customer is, and these defaults.

- Monthly plans: at least 30 days before the first higher charge.
- Annual plans: at least 60 days before the renewal at which the price changes. Never mid-term.
- A reminder 7 to 30 days before the first higher charge.

**Law that changes (US default; read November 2026; not legal advice).** A lawyer checks the notice template and the
price-change clause in the terms once before the first send, and again when the places sold in change.

| Place | What to know | Status as read |
| --- | --- | --- |
| US, federal | The Restore Online Shoppers' Confidence Act requires clear terms, consent and a simple way to stop recurring charges for online subscriptions. The FTC's broader "click to cancel" rule was struck down by a federal appeals court in July 2025; the FTC opened a new rulemaking in 2026 | Enforced; rulemaking open, check its status |
| California | The automatic renewal law, as amended from July 1, 2025, requires notice of a change in the fee no less than 7 and no more than 30 days before it takes effect, with how to cancel | In effect |
| New York | Cancelling must be as easy as signing up (from November 2025) | In effect |
| Other US states | Many have their own automatic renewal laws with notice rules | Check each state where consumers are billed |
| UK | A new subscription regime under the Digital Markets, Competition and Consumers Act, with reminder notices and exit rights, was reported as due to start in 2027 | Not yet in force as read; check the date |
| EU | A term that lets a business raise a consumer's price generally needs to give the consumer the right to leave. Country rules add to this | Not re-read for this skill; check locally |

Business customers are governed mainly by their contract. The consumer rules above are the safe default for small
businesses buying online too.

## 7. The message

One message, sent from a named person, by the channel the customer normally hears from the company on, and shown in
the product or account page as well. It has six parts, in this order:

1. **What is changing**: the plan, the old price and the new price, as numbers.
2. **When**: the date of the first higher charge for this customer.
3. **Why**: what the customer has gained since they bought, in specifics they can check. Costs can be mentioned
   honestly; they are not the lead.
4. **What they can do**: stay (nothing to do), switch to a named smaller plan, lock the current price by paying for a
   year now if that offer is in the plan, or cancel, with the link.
5. **Who to talk to**: a real reply address or person.
6. **Nothing else.** No marketing, no survey, no apology that reads as doubt. Plain and direct.

Before the send:

- Support and sales have the facts, the dates, the list of who is affected, and what they may offer. What they may
  offer is the discount policy, not improvisation.
- Sales pay plans do not reward giving the old price away.
- The billing Tool holds the new price against the right customers with the right start date. Check five accounts by
  hand in each segment.
- The price page and terms show the new price and the change clause.

## 8. Watch

Daily, for two billing cycles after each wave's notice, on one page:

- Cancellations and downgrades among notified customers, as revenue and as a share of the wave, against the stop rule.
- Replies and support contacts about the change, grouped by reason.
- Requests for an exception, and what was given.
- Failed payments at the first higher charge.

Compare with customers not yet notified, who are the control. A customer who was leaving anyway is not a loss to the
price rise; the gap between the two groups is.

Handle an objection with the policy: explain the value, offer the smaller plan, offer the annual lock if it is in the
plan. A larger concession follows `skills/match-one-save-offer` and the discount cap. Record every exception.

## 9. Close

When the last wave has passed two billing cycles, publish the result: revenue gained, revenue lost, net against the
sized cases, break-even against actual, exceptions given, and what to do differently. Update
`knowledge/our-prices.md` and `knowledge/pricing.md`. Set the date of the next review.
