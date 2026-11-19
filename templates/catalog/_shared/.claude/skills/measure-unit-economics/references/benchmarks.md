# Benchmarks: how to use them, and the dated list

## Why benchmarks never set the verdict

Published benchmarks for the same metric disagree, and all of them can be right, because they measure different
things:

- **Different definitions.** Payback on revenue or on gross margin. CAC with or without salaries. NRR over twelve
  months or annualised from one.
- **Different companies.** Almost every public benchmark is for subscription software sold to businesses. A consumer
  service, a marketplace or a shop has different margins and buying cycles.
- **Different years.** Cheap money and expensive money produce different "good" numbers.
- **Different samples.** Survey answers from companies that chose to reply, or one lender's portfolio.

So one widely read guide calls payback beyond 12 months weak, a public skill library treats under 12 as strong and
only over 24 as a concern, and a 2025 survey median sits at about 16. A bot that picks one of these can call the same
company healthy or sick.

## The order of evidence

1. **The company's own history.** This month against the trailing three months, and the same cohort age a year ago.
   Same definition, same business, same data. This sets the verdict word (up, down, flat).
2. **The company's own target**, set by the owner on a goal. This sets "on target" or "off target".
3. **Floors that hold for any business.**
   - Payback must arrive before most of the cohort has gone. If half the cohort has left by the payback month, the
     business is paying to rent customers.
   - Payback must fit inside the cash the company has: a payback longer than the runway cannot be funded by growth.
   - LTV to CAC of 1.0 on gross margin is break-even on acquisition, before every other cost.
   - A projected LTV built on fewer than six observed months is not evidence.
4. **Outside benchmarks**, as context on the method note only.

## Rules for showing a benchmark

- Each one carries: source, year of the data, who was measured, the definition used, and the date it was read.
- Show it only when the definition matches the company's figure, or say exactly how it differs.
- Show it only for a similar kind of company. If none fits, write "no comparable public benchmark" and stop.
- Never borrow a grade word from a benchmark ("strong", "healthy", "anemic", "best in class").
- Refresh this list when a figure is over a year old: search for the newest edition, and replace it with its date.
  If it cannot be refreshed, keep the old one with its year.

## The list (read 2026-11-18)

All of these are for software companies selling subscriptions to businesses unless marked.

| Metric | Figure | Source and year | Sample and definition | Confidence |
| --- | --- | --- | --- | --- |
| LTV to CAC | "Higher than 3" for the best companies | David Skok, "SaaS Metrics 2.0", written about 2013, updated 2016 | Rule of thumb from one investor's portfolio experience; LTV on gross margin | Primary source read; old |
| Months to recover CAC | Under 12; many of the best in 5 to 7 | Same | Same; on gross margin | Primary source read; old |
| CAC payback, median | About 16 months (about 18 the year before) | Benchmarkit, 2025 edition | Survey of a few hundred private software companies; definition not confirmed | Read through secondary coverage only |
| CAC payback by contract size | Under $5,000 a year: 9 months in one summary, 11 in another. $50,000 to $100,000: about 22 months | Same, 2025 | Same | Secondary; the two summaries conflict |
| Net revenue retention, median | 101% | SaaS Capital, 2025 retention benchmarks (published September 2025) | Private software companies over $1M in annual recurring revenue; twelve-month cohort method | Primary source read |
| Gross revenue retention, median | 91% | Same | Same; leaves out upgrades and price rises, cannot pass 100% | Primary source read |
| Which metric to lead with | CAC payback and NRR over LTV to CAC | Kyle Poyar with High Alpha, 2025 SaaS Benchmarks (November 2025) | Survey of 800+ companies; an editorial view, not a figure | Primary source read |
| Net revenue churn warning | Above about 2% a month | Skok, as above | Rule of thumb | Primary source read; old |

Nothing in this list covers consumer subscriptions, services, marketplaces or shops. For those, the company's own
history is the only fair comparison, and the page says so.
