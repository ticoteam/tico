A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme CI health, week to Tue 2026-09-29

Sample output for Acme, a fictional studio-software team. Every workflow, test and name is invented.
Nothing was rerun, canceled or changed. First draft, not yet reviewed.

**Headline: the merge check failed for infrastructure reasons on 3% of runs (last fortnight 1%); its median rose to
18 min 40 s from 14 min 10 s. One fix saves about 6 minutes a run.**

## Flaky tests, handed to the QA Owner
| Test | Flips (14 days) | Example runs | Suspected cause |
|---|---|---|---|
| `test_booking_reminder_sends_at_7am` | 6 | 88121, 88164 | timezone: passes except 23:00-00:00 UTC |
| `e2e/checkout.spec: applies promo` | 3 | 88127, 88159 | waits on a fixed 2 s sleep |
| `test_export_csv_large` | 2 (was 5) | 88150, 88172 | shared temp dir; the 2026-09-23 fix halved it |

The QA Owner decides fix or quarantine for each.

## Slowest jobs (median, 14 days vs previous 14)
- `ci.yml / test-api`: 11 min 30 s (was 7 min 50 s). Dependency install is not cached since the lockfile
  moved to `api/` on 2026-09-17.
- `ci.yml / e2e`: 6 min 10 s (flat).

## Main branch red
- 2026-09-24 10:12 to 15:40 (5 h 28 min): first bad run 88140, commit a41c9e0. Cause in the log: the
  payment sandbox rate limit. Fixed by a retry wrapper in 88190.

## Faults
- Payment sandbox rate limit: 4 runs, all on 2026-09-24, the first at 88140. The same rate limit as the red streak above.

## Deploys
- 7 deploys from `deploy.yml` (last fortnight 5), 0 failed.

## Fix plans
1. **Cache the api install** (`ci.yml`, job `test-api`): point the cache key at `api/package-lock.json`.
   Expected: about 6 minutes off every run. Check: the step "Restore cache" reports a hit. Owner: the Engineering Team
   Manager, who routes it to the owner of that area.
2. **Cache the browser install** (`ci.yml`, job `e2e`): key the cache on the browser version. Expected: about 1 minute off
   each e2e run. Check: the "Install browsers" step reports a cache hit. Owner: the Engineering Manager.
3. **Split the e2e job across two runners** (`ci.yml`, job `e2e`) by spec file. Expected: the job's median falls from
   6 min 10 s to about 3 min 30 s. Check: the median over the next 14 days. Owner: the Engineering Manager.

## Could not read
- Logs older than the repository's retention for 4 runs on 2026-09-15; counted as unknown, not green.

## Sources
- gh run list and run logs, web-app and api, 2026-09-15 to 2026-09-29; knowledge/thresholds.md (2026-09-15)
```
