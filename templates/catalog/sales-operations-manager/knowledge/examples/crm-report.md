A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme CRM report, Mon 2026-09-28 (CRM read 2026-09-28 06:31)

Sample output for Acme, a fictional studio-software team. Every record is invented. The CRM was
only read; nothing was changed. First draft, not yet reviewed.

**Headline: open pipeline $184k in 31 deals, up $12k; 11 deals cannot be trusted in the forecast.**

## Pipeline by stage (counts and sums; no probabilities were supplied)
| Stage | Deals | Amount | vs last week |
|---|---|---|---|
| Trial | 12 | $41k | +3 deals |
| Proposal | 9 | $73k | +$8k |
| Negotiation | 6 | $52k | flat |
| Verbal | 4 | $18k | -1 deal (won) |
Won this week: 1 ($6k). Lost: 1 ($4k, reason "price").

## Forecast for Q4 (categories as sellers set them)
Commit $38k (4 deals), best case $29k (5), pipeline $117k (22). Flags: Pine & Stone ($12k) is commit with
no signer named (rule 2 in `knowledge/forecast-rules.md`); Oak Row ($9k) closes 2026-10-09 but is still
"pipeline". Categories are the sellers' and the Sales Manager's to change.

## Unassigned leads (3)
- Cedar Barre (web form 2026-09-27, West region): rule "West to Priya" gives Priya.
- Willow Yoga (event list): existing customer's sister studio; account owner Dana.
- Fern Kids Dance (referral, 2 regions): matches two territory rules. Rule gap: proposal on the task.

## Exceptions (11 deals, 7 older than 30 days)
| Deal | Owner | Problem | Proposed fix | First seen |
|---|---|---|---|---|
| Elm Street Studio, $24k | Dana | Next step empty | Add a dated next step | 2026-08-14 |
| Lakeside Yoga, $9k | Dana | No activity 16 days (limit 14) | Log the last call, or close the deal | 2026-09-26 |
| Pine & Stone, $12k | Priya | Close date pushed 3 times | Reset the date with the buyer, or mark stalled | 2026-07-31 |
| Birch Hill, $7k | Priya | Amount empty | Enter the amount | 2026-09-21 |
| 7 more deals | Dana, Priya | Next step "follow up" (not specific) | Replace with an action and a date | 5 of them first seen before 2026-08-29 |

## Duplicates (monthly check)
2 sure groups, 1 maybe. Sure: "Harbour Pilates" lead and account created 2026-09-12 share a domain.
Proposed survivor: the older record, which holds 3 notes. Requested merges use the CRM Tool and preserve the listed fields.

## Trend
Exceptions older than 30 days: Dana 4, Priya 3. That is a habit, not a typo: propose a Friday five-minute review.

## Could not read
Stage history was unavailable for 2 deals, so their days in stage are not shown.

## Sources
- CRM read 2026-09-28 06:31; `knowledge/hygiene-rules.md`, read 2026-09-28
```
