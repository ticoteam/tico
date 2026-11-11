A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme receivables pack, Mon 2026-09-28 (draft, not yet reviewed)

**Headline: 9 open overdue invoices worth 14,620; 6 reminder drafts ready, 2 held back, 1 needs you.**

Sample output for Acme, a fictional studio-software team. Every customer is invented and every address uses a reserved example domain. Nothing has been sent.

## Aging (as of 2026-09-28, `acme-aging-2026-09-28.csv`)
| Bucket | Invoices | Amount | Change on last week |
|---|---|---|---|
| Not yet due | 14 | 22,310 | +3,100 |
| 1 to 30 days | 6 | 7,420 | -900 |
| 31 to 60 days | 2 | 4,300 | +1,200 |
| 61+ days | 1 | 2,900 | 0 |

DSO 38 days: receivables 36,930 / credit sales 87,300 over the last 90 days x 90 (sales from the invoice register, 2026-09-28).
No unapplied cash found in the export.

## Needs you now
- **Invoice 2041, Elm Street Studio, 2,900, 61 days**: the last step of the ladder. Two reminders went unanswered (2026-08-10, 2026-08-24). A human should call; I have drafted no email.

## Drafts for review
### Invoice 2057, Harbour Pilates, 1,150, 6 days overdue (step 2, friendly)
To: accounts@harbour-pilates.example. Terms: net 30 (`knowledge/customers.md`). No dispute on record.

> Subject: Invoice 2057, due 2026-09-22
>
> Hi Sam, a quick note that invoice 2057 for 1,150 was due on 2026-09-22 and I do not see it as paid yet. The payment link is on the invoice. If it has crossed with your payment, please ignore this, and tell me if anything is holding it up. Thank you, [sender]

68 words. No late fee: none was given in the terms.

### Invoice 2049, Pine & Stone, 2,100, 31 days overdue (step 4, firm)
Draft is on the task. Marked gap: a payment-plan offer they asked about is yours to give.

## Held back
- Invoice 2052, Lakeside Yoga, 850: disputed on 2026-09-15 (`knowledge/customers.md`). Not chased.
- Invoice 2060, Birch Cafe, 400: shows paid in the export read today; check the bank before any reminder.

## Could not read
No mailbox was connected, so I could not check for replies to earlier reminders. Amounts are from the export read 2026-09-28.

## Sources
- `acme-aging-2026-09-28.csv`, read 2026-09-28
- `knowledge/customers.md`, `knowledge/ladder.md`
```
