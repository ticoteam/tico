# Discount policy

A discount is a price cut for one customer. Without a written policy, every discount becomes the next customer's
starting point and the list price stops meaning anything. The policy is written in `knowledge/our-prices.md` under
"Discount rules". Each month's measured spread goes in `knowledge/discounts.md`. The limits below are the
defaults the bot proposes. The policy takes effect when the owner OKs it; until then, only offers already published
are given. Any later change to it is a recommendation for the owner, not an edit.

## The policy, as a template

Fill each line from the saved answers; keep it to one page.

1. **Cap.** No discount above 20% off list without the owner's OK.
2. **Something in return.** Every discount is traded for something the company values. Nothing is given for asking.

   | The customer gives | The company may give |
   | --- | --- |
   | Pays a year up front | Two months free (the only reward for annual prepay) |
   | Commits to a longer term | Up to the cap, scaled to the term |
   | Commits to more volume (seats, locations, units) | A published volume band, the same for everyone |
   | A public case study, review or reference call | A one-time credit, not a lasting price cut |
   | Signs by a real date that matters to the company | A one-time credit |

3. **One at a time.** Discounts do not stack. The customer gets the best single one they qualify for.
4. **An end date.** Every discount states when it ends and what the price is afterwards, on the quote and on the
   invoice. "Forever" discounts are not given.
5. **Never below the floor.** No discount takes a sale under the margin floor in `knowledge/pricing.md`.
6. **Who may give what.**

   | Discount | Who decides |
   | --- | --- |
   | In the table above, inside the cap | The teammate handling the customer, bot or person, on their own |
   | Outside the table, inside the cap | The teammate who owns pricing |
   | Above the cap, or below the floor | The owner, by an OK on the task |
   | A public promotion, or any change to these rules | The owner, by an OK on the task |

7. **Written down.** Each discount is recorded on the customer in the billing Tool or CRM with its size, what was
   traded, who gave it, and its end date.
8. **Public promotions** have a start date, an end date, a total budget, and a rule for who qualifies. They end when
   they say they end.
9. **Not a discount:** a smaller plan, removing an add-on, or a longer trial. Offer these first. They keep the list
   price whole.

## Why the defaults are what they are

- **The cap.** Deep discounts bring in customers who value the product less. One pricing-software company's analysis
  of its own customers' data reported that churn rose with the size of the discount, and advised keeping discounts at
  or under 20% (Patrick Campbell, 2020; the company's own data, not independently checked). Test it on the company's
  own customers in the monthly read below, and move the cap on that evidence.
- **Free months for annual.** A fixed offer is easier to explain than a percentage and cannot be argued upward.
- **Credits over price cuts.** A one-time credit ends by itself. A lowered price has to be raised again later.

## Measure it every month

Read from the billing Tool, in the same run, with `skills/read-discounts-as-a-distribution`:

- **The spread, not the average.** How many customers at 0%, 1 to 10%, 11 to 20%, above 20%. A spike at one level is
  the real price for that group.
- **Realised price against list**, per plan, this month against the trailing three months.
- **By who gave it.** One teammate or one channel giving most of the discounts is a training or pay-plan problem.
- **By what was traded.** Discounts with nothing recorded in return are policy breaks. Count them.
- **Did they stay?** Retention and expansion of discounted customers against full-price customers who joined in the
  same months. If discounted customers leave sooner, recommend a lower cap. If they behave the same, the discount
  bought growth; say so.
- **Expired and still applied.** Discounts past their end date that are still on the bill. List them for the owner
  with a recommendation. Ending one raises what an existing customer pays, so it waits for the OK and goes with
  notice.

Put the spread on the pricing page as a bar chart with the cap marked.

## Signs the list price is wrong, not the discounting

- Nearly every deal closes at the same discount: that is the price. Recommend lowering list to it, or holding the
  line and testing.
- Nobody ever asks for a discount: the price may be low. See `willingness-to-pay.md`, rung 1.
- Only the top plan is discounted: the plan holds things that group does not value. See
  `value-metric-and-packaging.md`.

## Honest promotions (US default; read November 2026; check locally)

- A "was" or "regular" price must be a price the product was really offered at, openly, for a reasonably substantial
  recent period. The FTC's Guides Against Deceptive Pricing set this out, and several states enforce their own
  versions more strictly.
- In the EU, an announced price reduction on goods must show the lowest price of the previous 30 days as the
  reference. Not re-read for this skill; check locally.
- A deadline is real. If the offer returns the day after it "ends", it was not a deadline.
- "Free" means free: no charge that starts later without a clear statement and consent before it starts.
- The same offer is open to every customer who meets the stated rule.
