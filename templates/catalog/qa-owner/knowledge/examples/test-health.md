A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme test health, week to Tue 2026-09-29

Sample output for Acme, a fictional studio-software team. Every suite, test and name is invented.
Nothing was rerun, skipped or changed. First draft, not yet reviewed.

**Headline: the merge suite failed for flaky reasons on 9% of runs (last fortnight 4%); its median rose to 18 min 40 s from
14 min 10 s, and one fix saves about 6 minutes a run.**

## Flaky tests
| Test | Flips (14 days) | Example runs | Suspected cause | Decision |
|---|---|---|---|---|
| `test_booking_reminder_sends_at_7am` | 6 | 88121, 88164 | timezone: passes except 23:00-00:00 UTC | fix by Fri, owner API |
| `e2e/checkout.spec: applies promo` | 3 | 88127, 88159 | waits on a fixed 2 s sleep | fix, owner web |
| `test_export_csv_large` | 2 (was 5) | 88150, 88172 | shared temp dir; the 2026-09-23 fix halved it | watch to 2026-10-06 |

## Past deadline
- `test_refund_partial` quarantined 2026-08-18 with ticket ENG-412, deadline 2026-09-01. Still skipped; owner to decide.

## Paths with no test
- Permission change on a shared folder: no test asserts it. Named, not counted as covered.

## Suites (median, 14 days vs previous 14)
- `ci.yml / test-api`: 11 min 30 s (was 7 min 50 s). Test database setup runs twice per job since 2026-09-17.
- `ci.yml / e2e`: 6 min 10 s (flat).

## Product bugs handed over
- `test_invoice_totals_rounding` fails on commit a41c9e0 in runs 88140 and 88141 with a real rounding error. Handed to the
  billing feature owner on task 4411 with the runs and the commit range.

## Fix plans
1. **Freeze the clock in the reminder test** (`api/tests/test_reminders.py`): set the test time to 10:00 in the studio's zone.
   Expected: the flip stops. Check: the test passes 20 runs in a row across the 23:00 hour. Owner: QA Owner, by a branch on task.
2. **Replace the fixed sleep in the promo e2e test** with a wait for the total to change. Expected: no flips. Check: 14 days with
   no flip. Owner: QA Owner.
3. **Drop the test database setup from the second job** in the api suite. Expected: about 2 minutes off each run. Check: the
   suite median over the next 14 days. Owner: the Infrastructure Owner, as a workflow change.

## Could not read
- Logs older than the repository's retention for 4 runs on 2026-09-15; counted as unknown, not green.

## Sources
- gh run list and run logs, web-app and api, 2026-09-15 to 2026-09-29; knowledge/thresholds.md (2026-09-15)
```
