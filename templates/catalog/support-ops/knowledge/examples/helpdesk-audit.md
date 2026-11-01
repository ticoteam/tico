A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme help desk audit, Mon 2026-10-05

Sample output for Acme, a fictional software team. Every rule, ticket and figure is
invented. Nothing has been changed in the help desk. First draft, not yet reviewed.

**Headline: one trigger that runs first sent 41 billing tickets to the general queue in September; removing it fixes most misroutes.**

## Findings (by tickets affected)
1. **Misroute, 41 tickets.** Trigger "Keyword: invoice" (added 2026-08-12) runs before "Billing form" and sets the queue to
   General. "Billing form" sets the queue to Billing only while the queue is still empty, so it did not act on these tickets.
   Traced on a test ticket with "invoice" in the subject. The two take incompatible queue actions. Examples: T-2104, T-2119,
   T-2150.
   Change request CR-1.
2. **SLA policy looser than the promise.** Premium plan contract promises first reply in 2 business
   hours; the "Premium" SLA policy is 4 hours. 6 Premium tickets were answered in 2 to 4 hours and showed
   as on time. Change request CR-2. Decides: the head of support.
3. **Stale macro, used 23 times.** "Refund window" says 14 days; the refund policy doc says 30 days
   since 2026-09-01 (Librarian answer 2026-10-05). Change request CR-3.
4. **Tags.** 3 near-duplicates (`refund`, `refunds`, `refund_request`: 212 tickets in total), 17 tags
   unused in 90 days. The September refunds report counts only `refund`. Change request CR-4.

## Change requests (each for review on its own)
- **CR-1.** Admin, Triggers, "Keyword: invoice": deactivate. Check: send a test email with "invoice"
  in the subject; it should land in Billing. Undo: reactivate.
- **CR-2.** Admin, SLA policies, "Premium": first reply target 4h to 2h, business hours unchanged.
- **CR-3.** Macros, "Refund window": replace "within 14 days" with "within 30 days of purchase".
- **CR-4.** Merge `refunds` and `refund_request` into `refund`; update the refunds report filter first.

## Last month's changes
- The new-plan macro (applied 2026-09-08) was used 58 times; no QA flag against it.

## Could not read
- Views: the export does not include them.
```
