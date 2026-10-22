### Added
- **System routines in Settings > Routines**: a read-only System section above the bot routines lists every job Tico
  runs on its own timers, in four groups (Checks that suggest, Learning, Reminders and deadlines, Housekeeping), each
  with what it does, its schedule, its last run in words ("9 checked, 2 suggestions", or "Failed: <reason>" in red) and
  its next run. Stale ticket review and nightly learnings also show which route answered Decide (Gateway, company key or
  company AI), "Ran on your own key: Gateway unavailable" when the Gateway did not answer, and this month's AI spend.
  Housekeeping rows say Tico needs them. `GET /api/v2/system-routines`; the Decide fallback order is unchanged.
