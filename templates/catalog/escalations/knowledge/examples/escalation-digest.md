A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme escalations, Tue 2026-09-29

Sample output for Acme, a fictional studio-software team. Every customer, ticket and name is invented. No update has been
sent. First draft, not yet reviewed.

**Headline: 4 open escalations (1 sev 1, 2 sev 2, 1 sev 3); 2 customer updates due today, 1 overdue by 3 hours.**

## Due today
- **E-14, sev 1, Harbour Pilates (VIP): members charged twice for September passes.** Owner: the support lead.
  Update was due 2026-09-29 05:30 (every 4 business hours); last sent 2026-09-28 13:10. OVERDUE.
  Engineering: fix merged 2026-09-28 18:40 (issue 482). Refunds of duplicates need the head of support's decision.
  Update ready below.
- **E-12, sev 2, Elm Street Studio: waitlist emails not sending since 2026-09-24.** Owner: the support lead.
  Daily update due 12:00. Engineering acknowledged 2026-09-25; cause found, no date given.

## Waiting
- **E-11, sev 2, Lakeside Yoga: calendar sync drops Sunday classes.** Waiting on customer for an export (asked 2026-09-28).
  Next update due Wed 12:00.
- **E-9, sev 3, Pine & Stone: report totals off by one day.** Engineering has not acknowledged in 2 days. Asked the
  engineering on-call on the task.

## Ready to close
- **E-8, Birch Hill Barre:** customer confirmed the import fix 2026-09-28. Lesson: imports over 5,000 rows time out silently;
  a size check would have caught it before the customer did.

## Updates ready to use (nothing sent)
E-14, to the Harbour Pilates owner, from the support lead:
"Hi, an update on the double charges. Our engineers have fixed the cause and we are checking that no new double charges
occur. We are confirming each affected member's refund and will write again by 14:00 today with the list and timing. The
support lead owns this for you until it is fully resolved."

## Sources
- Support mailbox read 2026-09-29 07:55; engineering issues 482, 479 and 471 read 07:58; case files in `knowledge/cases/`.
```
