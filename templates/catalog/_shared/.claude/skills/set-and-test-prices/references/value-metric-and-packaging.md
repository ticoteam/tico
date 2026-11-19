# Value metric and packaging

Three decisions, in this order: what the customer pays per (the value metric), what is in each plan (packaging), and
the number (the price point). Most of the money is in the first two.

## Value metric

### The six tests

Score each candidate metric from 0 to 2 on each test, from data where the data exists. Keep the scores on the page.

| Test | How to check it with data |
| --- | --- |
| It rises as the customer gets more value | Customers with more of the metric stay longer and upgrade more (billing and usage) |
| Bigger customers have more of it | The metric rises with customer size; large customers do not pay the same as small ones |
| The customer can predict the bill | A buyer can work out next month's bill before signing; month-to-month swings are small |
| Billing can count it | The billing Tool or the database already holds an exact, auditable count |
| It is hard to game | Customers cannot cut the count without cutting their own use (shared logins defeat per-seat) |
| Cost to serve rises with it | Heavy users on this metric are the ones who cost the most to serve |

A metric that fails "the customer can predict the bill" needs a remedy before it ships: included amounts, bands,
spending caps, alerts before a limit, or prepaid credits.

### Common metrics and when each fits

| Metric | Fits when | Watch for |
| --- | --- | --- |
| Per seat or user | Each user gets their own value from the product | Shared logins; value that comes from automation, not people; customers keeping seats low |
| Per unit of use (bookings, messages, orders, credits) | Value and cost both rise with use | Bills nobody can predict; customers using less to save money |
| Per unit of scale (locations, properties, contacts) | The customer's size drives value and is stable | Steps that punish growth at the boundary |
| Per outcome (a resolved ticket, a booked job) | The outcome is consistent, clearly caused by the product, countable, and predictable for the buyer | Disputes over what counts; outcomes the product does not control |
| Flat price | A simple product with similar customers | Large customers paying the same as small ones |
| Platform fee plus one metric | A base of value everyone gets, plus use that varies widely | Two things to explain; keep to one variable metric |

Notes that date:

- Mixed pricing (a subscription plus a usage part) was used by 37% of respondents in one 2026 survey of 230+
  software companies, up from 25% a year earlier. Outcome-based pricing was the main model for about 5% in 2025.
  (Kyle Poyar, State of B2B Monetization, read through summaries in November 2026.)
- Where the product runs AI models, cost to serve can be very uneven: coverage of the same survey reports the
  heaviest tenth of users driving 70% to 80% of AI cost at some companies. Read the company's own usage by customer
  before choosing a flat price
  for anything with a real cost per use.

### Choosing

Test several candidates and keep one, or a platform fee plus one. Two variable metrics confuse buyers and double the
billing work. When the best-scoring metric is not the current one, the change is a migration, not a price change: it
needs the owner's OK, a mapping of every existing customer to the new metric, and a rule that nobody's bill rises by
more than the step cap at the switch (see `raise-prices-on-existing-customers.md`).

## Packaging

### Group customers first

Plans are drawn around groups of customers, not around the feature list. Use billing, usage and conversations to find
groups that differ in what they need and in what they would pay. Three groups is usual. For each, write one line: who
they are, the job they hire the product for, their next best choice, and the evidence for what they would pay.

### Sort features per group

For each group, sort each feature into one of three kinds:

- **Must-have**: the reason this group buys. It leads the plan aimed at them.
- **Nice-to-have**: adds some value. It fills the plan out.
- **Unwanted**: the group does not value it, and being made to pay for it puts them off. Leave it out of their plan.

Evidence, strongest first: which features the group actually uses; what they asked for before buying; a forced-choice
survey where customers pick the most and least valuable from short lists.

A feature that a minority values highly and the rest do not is an **add-on**, sold separately, not buried in the top
plan.

### Draw the plans

- **Three plans by default** (good, better, best). Fewer when customers are alike; more only with evidence of a
  fourth group.
- **Each plan is aimed at one group** and is named for it.
- **One or two fences between plans.** A fence is a limit or feature that a bigger or more demanding customer cannot
  do without, so they cannot stay on the cheaper plan. Good fences follow the value metric (number of locations) or a
  need only larger customers have (single sign-on, audit logs, approval flows). A bad fence annoys small customers
  without stopping large ones.
- **The entry plan must be a real product.** It solves the whole job for the smallest group. A crippled entry plan
  loses the customer, it does not upsell them.
- **Annual and monthly.** Offer annual prepay as free months, not as a percentage. It is easier to understand and
  easier to keep out of other discounts.
- **Free plan or free trial.** A free plan is a way to win customers, not a tier: it belongs to the acquisition
  budget, and it is judged by how many free users become paying ones and what they cost to serve. A trial is simpler
  when the product shows its value inside two weeks.

### Check against plan mix

Read where new customers land, by count and by revenue, for the last three months. There is no correct split; the
reading is diagnostic.

- Nearly everyone on the cheapest plan: the fences are too weak, or the middle plan is priced too far above it.
- Nearly everyone on one plan and almost no upgrades: the next plan up offers nothing they need.
- Many customers sitting at a limit without upgrading: the step up is too large. Consider a band in between or an
  add-on.
- Many customers using a small part of their plan: they are overbuying and are at risk of leaving. A smaller plan
  keeps them.
- The top plan is sold only with a discount: its list price is above what that group will pay, or the plan holds
  features they do not want.

Rules of thumb for the gap between plans exist (one widely repeated one puts the cheapest no more than about a
quarter below the middle and the top no more than about half above it) and conflict with each other. Use the
company's own plan mix and test results over any of them.

## Four ways a product's pricing fails

Check a new product or feature against these before pricing it.

1. **Too much in it.** Features nobody asked for are piled in, the price has to cover them all, and no group sees its
   own must-haves clearly.
2. **Priced too low.** The right product for the right market, sold for less than it is worth because nobody asked
   customers what they would pay.
3. **A good thing nobody sells.** A valuable capability exists, and it is given away or never packaged because it
   falls outside the main product.
4. **An answer to a question nobody asked.** A product built without checking that anyone would pay for it.

The common cause is leaving the willingness-to-pay conversation until after the product is built. Have it first.

## The price page

- Show the price in plain text on the page, with the billing period and what the metric is. A price hidden behind
  "contact us" is fine for a plan that is truly negotiated, and for no other.
- Use the buyer's currency where the company sells in more than one market, with a price set for that market.
- Lead with what each plan is for and who it suits, then the three to five differences that matter. Put the full
  comparison table below.
- State the fences and limits exactly. A limit discovered after buying is a refund.
- Keep prices in the page's text, not only in images or scripts, so that search engines and AI assistants that
  compare products can read them.
- After any change, open the page as a buyer and follow it to checkout.
