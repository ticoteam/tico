A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Postmortem draft: booking page slow and failing, 2026-09-22

Sample output for Acme, a fictional studio-software team. Every name and figure is invented.
Draft for the incident lead's review. Nothing has been posted or shared.

**Summary:** From 14:02 to 14:47 UTC on 2026-09-22 the booking page returned errors for about a third of visits.
A database migration added an index without a lock timeout and held up writes until it was rolled back at 14:44.
No data was lost.

## Impact
- 45 minutes of degraded bookings. About 31% of booking-page requests failed (source: error dashboard, 14:02 to 14:47).
- 212 failed booking attempts; 168 were retried and succeeded later (source: bookings table, queried by the on-call).
- Revenue effect: not known. Gap.

## Detection
First alert at 14:06 (error-rate alert), four minutes after the start. A customer email arrived at 14:11.

## Timeline (UTC)
| Time | Event | Source |
|---|---|---|
| 14:02 | Migration 0412 started on the primary | deploy log |
| 14:06 | Error-rate alert fires | alert history |
| 14:12 | On-call joins the incident channel; first hypothesis: traffic spike | channel |
| 14:29 | Spike ruled out; lock wait found on the bookings table | channel |
| 14:44 | Migration rolled back | deploy log |
| 14:47 | Error rate back to baseline | dashboard |

## Contributing factors
- The migration ran during business hours; the deploy checklist has no rule about long-running migrations.
- No lock timeout was set, so writes queued instead of failing fast.
- The alert named the symptom, not the table, so the first 17 minutes went on the wrong hypothesis.

## What went well
- The rollback took 2 minutes once the cause was found.

## Where we got lucky
- It was a Tuesday afternoon, not a Saturday morning, when most classes are booked.

## Action items (all proposed, owners to be confirmed)
1. Add a lock timeout to every migration template. Owner: to confirm. Due 2026-10-06. Done when the template is merged.
2. Add a checklist line: migrations that touch large tables run outside business hours. Owner: to confirm. Due 2026-10-06.
3. Add the blocking-query count to the error alert. Owner: to confirm. Due 2026-10-13.

## Could not read
- The customer email, so its exact time is approximate.

## Sources
- Incident channel export (attached to the task), deploy log, alert history, all 2026-09-22
```
