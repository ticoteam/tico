# The management P&L layout, the cost of revenue rules and the GAAP mapping

## The layout

Lines down, segments across, then Shared and Total. Keep it to about 20 lines. Detail lives one click down, by vendor.

```
Revenue
  Subscription
  Usage
  Services
  One-off sales
Cost of revenue
  Hosting and infrastructure (production)
  AI and model usage (serving customers)
  Payment processing fees
  Support and onboarding
  Third-party software and data inside the product
  Delivery labour and contractors
Gross profit            and gross margin %, per revenue type and per segment
Direct selling costs    ad spend, commissions, affiliate fees, by segment where tagged
Contribution            labelled "not a GAAP measure"
Operating expenses
  Sales and marketing
  Product and engineering
  General and administrative
Operating income
Below the line          interest, one-time items, taxes
Net income              must equal the book
```

Leave out a line the company does not have. Do not add a line to hold one vendor.

## What goes in cost of revenue

The test: would this cost fall if the company served fewer customers next month, or stop if it stopped delivering? If
yes, it is cost of revenue. If it exists to win customers, build the product or run the company, it is not.

| Cost | Line | Rule |
|---|---|---|
| Production hosting, databases, CDN, monitoring of production | Hosting | In. Staging, CI and development environments are Product and engineering |
| The product's own model calls | AI and model usage | In |
| AI for bots that answer customers, onboard them or deliver the work | AI and model usage, or Support | In |
| AI for bots in sales, marketing, engineering, finance, and Tico's built-in helper bots | The function's operating expense | Out |
| Card and payment processor fees, chargebacks | Payment processing fees | In. Gross the revenue up if payouts arrive net |
| Support and onboarding people and contractors | Support and onboarding | In, when the work is keeping customers running |
| Customer success work that is selling: renewals, upsell, expansion | Sales and marketing | Out |
| Software or data licensed to run inside what the customer gets | Third-party inside the product | In |
| People and contractors who deliver a service the customer paid for | Delivery labour | In |
| Sales commissions, ad spend, affiliate payouts | Direct selling costs | Out of cost of revenue; shown just below gross profit |
| Product management, design, engineering | Product and engineering | Out |
| Rent, finance, legal, insurance, the owner's pay | General and administrative | Out. Do not spread overhead into cost of revenue |

Services are reported apart from subscription, with their own costs and margin.

Benchmarks date quickly and yours matters more. For orientation only: SaaS Capital reports gross margin on software
licence revenue of "generally 80% to 85%" among the companies it lends to and surveys (article dated 2024-10-03).
Products that run on model calls sit well below that: secondary coverage of ICONIQ's 2026 State of AI survey gives an
average AI product gross margin of about 52 percent, as answered by executives, not measured (read 2026-11-18, not
checked against the report itself). Compare a segment with its own last 12 months before any outside number.

## Split rules, written once

Each mixed account gets one rule in `knowledge/pnl-design.md`, in this shape:

| Account | Split by | Source of the shares | Applied since |
|---|---|---|---|
| 5100 AI services | bot, then the bot's role | `spend_by --by bot`, Usage by bot | 2026-10 |
| Hosting vendor | environment tag | the vendor's cost export | 2026-10 |
| Payroll | teammate's team | the team chart | 2026-10 |

A rule changes only with a dated note, and the 12-month history is restated the same way.

## Allocating shared delivery costs to segments

In order of preference:
1. **Direct tag.** The cost carries the segment's class, project or location.
2. **A usage measure.** Requests, tasks, storage, tickets, seats by segment, from the system that counts them.
3. **Revenue share.** Only when nothing better exists.

Label methods 2 and 3 "allocated" on the page. Never allocate operating expenses to segments: it invites arguments about
the allocation and hides which segment covers its own direct costs.

## The GAAP view

A small table, second on the page, for a lender, an investor or the accountant. US GAAP captions:

| Management line | GAAP caption |
|---|---|
| Revenue, all types | Revenue |
| All cost of revenue lines | Cost of revenue |
| Direct selling costs; Sales and marketing | Sales and marketing |
| Product and engineering | Research and development |
| General and administrative | General and administrative |
| Below the line | Other income and expense; Income taxes |

Then list each difference between the two views with its amount. The usual ones:

- **Items shown below the line.** Management shows a one-time item under operating income; GAAP usually keeps it in an
  operating caption.
- **Allocations.** Management allocates shared delivery costs to segments; GAAP has no segment columns for most
  private companies.
- **Timing choices.** If management shows annual plans as monthly amounts but the book is on a cash basis, the two
  differ by the unearned part. Name it.
- **Costs the company capitalises** (some software development, equipment). Management may expense what GAAP spreads.
- **Stock compensation, depreciation, amortization** when management shows an "adjusted" figure.

Rules:
- Any figure that is not a GAAP figure is labelled "not a GAAP measure" where it appears.
- Each one is reconciled to the nearest GAAP figure on the same page: start from GAAP net income or operating income,
  add and subtract the named items, arrive at the management figure.
- The management view and the GAAP view end at the same net income unless a listed difference explains the gap.
- GAAP itself is the accountant's field. This table maps; it does not certify. Statements that go to a lender, an
  investor or an auditor are checked by a CPA. IFRS reporters use their own captions; ask the accountant.

## What this P&L is not

- **Not a tax return.** Tax uses its own rules for timing, depreciation and what is deductible. Hand the tax preparer
  the book and the GAAP view, not this page.
- **Not the forecast.** It reports what happened. `forecast-cash-and-budget` says what will.
- **Not a cash report.** Profit and cash differ by timing. The cash figure at the top is the bank's, not the P&L's.
