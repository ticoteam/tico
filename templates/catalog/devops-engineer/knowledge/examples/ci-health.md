A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme CI health, week to Tue 2026-09-29

Sample output for Acme, a fictional studio-software team. Every workflow, test and name is invented.
Nothing was rerun, cancelled or changed. First draft, not yet reviewed.

**Headline: the merge check failed for flaky reasons on 9% of runs (last fortnight 4%); its median rose to
18 min 40 s from 14 min 10 s. One fix saves about 6 minutes a run.**

## Flaky tests (register: knowledge/flaky-tests.md)
| Test | Flips (14 days) | Suspected cause | Owner | Decision needed |
|---|---|---|---|---|
| `test_booking_reminder_sends_at_7am` | 6, e.g. runs 88121, 88164 | timezone: passes except 23:00-00:00 UTC | API owner | fix or quarantine by Fri |
| `e2e/checkout.spec: applies promo` | 3 | waits on a fixed 2 s sleep | web owner | fix |
| `test_export_csv_large` | 2 (was 5) | shared temp dir; the 2026-09-23 fix halved it | web owner | watch |

## Slowest jobs (median, 14 days vs previous 14)
- `ci.yml / test-api`: 11 min 30 s (was 7 min 50 s). Dependency install is not cached since the lockfile
  moved to `api/` on 2026-09-17.
- `ci.yml / e2e`: 6 min 10 s (flat).

## Main branch red
- 2026-09-24 10:12 to 15:40 (5 h 28 min): first bad run 88140, commit a41c9e0. Cause in the log: the
  payment sandbox rate limit. Fixed by a retry wrapper in 88190.

## Deploys
- 7 deploys from `deploy.yml` (last fortnight 5), 0 failed.

## Fix plans
1. **Cache the api install** (`ci.yml`, job `test-api`): point the cache key at `api/package-lock.json`.
   Expected: about 6 minutes off every run. Check: the step "Restore cache" reports a hit.
2. **Freeze the clock in the reminder test**: set the test's time to 10:00 in the studio's zone instead of
   now. Owner: the API owner.
3. **Replace the fixed sleep in the promo e2e test** with a wait for the total to change. Owner: the web owner.

## Could not read
- Logs older than the repository's retention for 4 runs on 2026-09-15; counted as unknown, not green.

## Sources
- gh run list and run logs, web-app and api, 2026-09-15 to 2026-09-29; knowledge/thresholds.md (2026-09-15)
```
