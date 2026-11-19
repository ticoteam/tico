# Formulas

Every formula here is a division. Before computing one, write down the four things both halves must share: which
customers, which period, which segment, which kind of cost. The numbers used to illustrate are invented.

## Inputs

| Input | What it is | Where it is read |
| --- | --- | --- |
| New customers | Customers whose first successful payment fell in the month | Billing Tool or the product database, one saved query |
| Net revenue | What customers paid, after refunds, credits and discounts, without tax | Same rows; ties to `tico finance report pnl` |
| Acquisition cost | Money and effort spent to win new customers in the month | `tico finance report campaigns`, `spend_by`, Growth cost rows |
| Paid cost | The part of acquisition cost that buys reach: ads, sponsorships, affiliates, promotions | Same, marked paid |
| Gross margin | (Net revenue minus cost of revenue) divided by net revenue | `tico finance report pnl`, `pnl_by` for a segment |
| Channel of a customer | The one channel the attribution rule names | Growth rows, with which rule named it |

Retention spend (win-back offers, loyalty credits, save offers) is never acquisition cost.

## Cost to acquire a customer (CAC)

```
Blended CAC = all acquisition cost in the month / all new customers in the month
Paid CAC    = paid cost in the month / new customers the attribution rule gives to paid channels
```

Show each as money only and, where people and bots are a real share of the cost, with effort. Tico Growth computes
CAC as (spend plus bot cost) divided by new customers, and adds hours when read with effort.

Illustration: cost $18,000, of which $14,000 paid, $1,000 other money, $3,000 people and bots. New customers 240, of
which 120 named to paid channels.

- Blended CAC = 18,000 / 240 = $75.00. Money only = 15,000 / 240 = $62.50.
- Paid CAC = 14,000 / 120 = $116.67.

What each one answers:

- **Blended** answers "does the whole machine work". It flatters paid channels, because free customers share the bill.
- **Paid** answers "what does the next customer we buy cost". It is the one to hold against payback when deciding
  whether to spend more. It is only as good as the attribution rule; when Unknown is large it is too high or too low
  by an unknown amount.
- Paid CAC rising while blended stays flat means organic growth is hiding a paid channel that is wearing out.

By segment, Growth gives two figures. **Direct CAC** uses the segment's own channels and is the one for payback.
**Loaded CAC** adds the segment's share of company-wide spend and is the one for comparing segments.

**Lag.** When the median time from first touch to first payment is more than 30 days, this month's customers were
bought with earlier months' money. Divide the cost of month M minus L by the customers of month M, where L is the
median lag in whole months. Say on the page that the cost is lagged and by how much.

## Gross margin

```
Gross margin % = (net revenue - cost of revenue) / net revenue
```

Cost of revenue is what it takes to deliver to a customer who has already bought: the people who do the work or
support it, hosting, payment fees, AI usage inside the product and by the bots that deliver or support it,
materials. AI used to run the company is not in it. Use the month's figure for that month's
tiles and the trailing three months for projections. Use the segment's own margin where the P&L is tagged by segment;
a company-wide margin on a low-margin segment overstates its LTV.

## Lifetime value (LTV) on gross margin

**Observed** (lead with this):

```
Observed LTV at age m = cumulative net revenue of the cohort through month m / customers the cohort started with
                        x gross margin %
```

Divide by the customers the cohort started with, not the ones still paying. That is how churn gets counted. A
customer who upgraded pays more and raises the top; that is how expansion gets counted.

Illustration: a cohort of 200 paying $120 a month while active, with 100%, 70%, 60%, 55%, 52%, 50% still paying in
months 1 to 6, at 35% gross margin.

| Age (month) | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| Revenue per starting customer | 120.00 | 84.00 | 72.00 | 66.00 | 62.40 | 60.00 |
| Gross margin per starting customer | 42.00 | 29.40 | 25.20 | 23.10 | 21.84 | 21.00 |
| Cumulative gross margin | 42.00 | 71.40 | 96.60 | 119.70 | 141.54 | 162.54 |

Observed LTV at 6 months = $162.54.

**Projected** (always labelled projected):

```
decay = revenue per starting customer in the last observed month / the month before it   (never above 1.0)
revenue in month m+k = revenue in month m x decay^k
Projected LTV = (observed cumulative revenue + projected revenue through month 60) x gross margin %
```

Use the average of the last three month-to-month ratios when the last one is noisy. Stop at 60 months, as Tico Growth
does. In the illustration, decay = 60.00 / 62.40 = 0.9615. Projected revenue for months 7 to 60 is about $1,319.58,
so projected LTV = (464.40 + 1,319.58) x 0.35 = about $624. Three quarters of that has not happened yet, which is why
observed leads and projected is context.

A projection needs at least six observed months and a cohort at or above the minimum size. Below that, write "too
early to project".

**Simple** (a cross-check only): monthly revenue per customer x gross margin % / monthly churn rate. It assumes churn
is the same every month, which is false for almost every business: early churn is higher. If simple and cohort LTV
differ by more than a quarter, trust the cohort figure and say why they differ.

Tico Growth's own observed LTV at 90, 180 and 365 days is net revenue. Multiply by gross margin before it is called
LTV or held against CAC.

## Payback in months

```
Cohort payback = the first age m where cumulative gross margin per starting customer >= that cohort's CAC
Simple payback = CAC / gross margin per customer in month 1
```

Cohort payback counts churn and expansion because it uses what the cohort really paid. Simple payback assumes nobody
leaves and reads short.

Illustration, with the table above:

- Blended CAC $80: cumulative margin passes $80 in month 3 ($96.60). Payback 3 months. 60% of the cohort is still
  paying then.
- Paid CAC $125: passes in month 5 ($141.54). Payback 5 months. 52% still paying.
- Simple payback at $80 = 80 / 42 = 1.9 months. Shown, labelled simple.

Always write the share still paying at the payback month. A cohort that pays back in month 5 with 52% left is a
different business from one that pays back in month 5 with 95% left: the first lives on a shrinking base.

Not there yet: "not yet: 62% of CAC recovered at month 5". Projected never to arrive within 60 months: "does not pay
back at current retention".

**Cash payback.** Where customers prepay (a year up front), cash returns faster than margin is earned. Show it as its
own line: the first month where cumulative cash collected, less the cash cost of serving, reaches CAC. It does not
replace margin payback; it answers a cash question, not a profit one.

## LTV to CAC

```
LTV to CAC = LTV on gross margin / CAC      (same cohort, same segment, same kind of CAC)
```

Always state the age or "projected": "2.0 at 6 months observed" and "7.8 projected" are different claims. A ratio of
1.0 on gross margin means the customer returned exactly what it cost to win them, before any other cost of running
the company. That is break-even on acquisition, not profit.

## Net and gross revenue retention

```
Opening set  = customers paying 12 months ago
NRR = recurring revenue this month from the opening set / recurring revenue the opening set paid 12 months ago
GRR = same, with each customer's revenue capped at what they paid 12 months ago
```

New customers since then are in neither. NRR includes upgrades, added seats and price rises, so it can pass 100%. GRR
leaves them out, so it cannot.

Illustration: 310 accounts paid $37,200 a month in October 2025. In October 2026 those same accounts pay $27,528;
capping each at its old amount gives $25,296. NRR = 27,528 / 37,200 = 74%. GRR = 25,296 / 37,200 = 68%.

Rules:

- Use recurring revenue only. One-off fees, setup and services stay out.
- For a business with no subscriptions, "recurring" is the revenue of repeat orders; say so, and prefer the revenue
  cohort matrix, which shows the same thing without pretending there is a contract.
- If one customer is over 10% of the opening revenue, show the figure with and without them.
- Under 30 accounts in the opening set, show dollars and accounts, not a percentage.
- A monthly figure is the same division over one month. Never multiply a monthly rate by twelve; compound it or, better,
  measure the twelve months directly.

## The comparison against own history

```
Change vs trailing 3 months = this month / average of the three months before it - 1
```

Within 10% either way is flat. Under 5 customers in the month, no verdict. Illustration: blended CAC $75.00 against
an average of $70.67 (68, 70, 74) is up 6%, flat. Paid CAC $116.67 against $104.00 (100, 104, 108) is up 12%, up.
