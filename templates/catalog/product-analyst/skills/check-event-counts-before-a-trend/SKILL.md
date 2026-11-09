---
name: check-event-counts-before-a-trend
description: Check an event count or rate before calling a change a trend, drop or launch effect. Covers a broken event, duplicate sources, time zones, missing versus zero, denominators, the field the product shows, and repeated captures.
---
# Check event counts before a trend

When to use: before you call any change in a count or a rate a drop, a trend or a launch effect, and before a readout says a number "moved".

Steps:
1. Did the event fire at normal volume in the window? A drop to zero, or a sharp fall at one hour, is usually a broken event, a late feed or a missing table until shown otherwise. Compare with a neighbouring event on the same platform.
2. Are two sources counting the same event? Overlapping tables (a web copy and an app copy, or a raw and a cleaned table) double count when summed. Deduplicate by the unique event ID before adding.
3. Are the timestamps in one zone? Stored times are often UTC. Compare whole days in the team's zone, and say which zone was used.
4. Is a missing period missing, or zero? A week with no rows may mean the feed stopped. Report it as missing, do not backfill it, and do not average across it.
5. Is a zero a zero? A zero can mean no calendars were set up, nothing was priced, an export was empty, or a filter removed the rows. Check one of these before reading zero as no use.
6. Is there a denominator? A count at one point in time, with an unknown denominator, is not a rate. Name the denominator, or call it a count.
7. Is it the field the product shows? Use the value the product itself displays, not a similar column that other code writes. If you use a proxy, name it as a proxy and say how far to trust it.
8. Is it one snapshot? One capture is one observation. A trend needs captures on a fixed schedule, and a change between two captures is reported with both dates.
9. Write the checks on the line next to the number, so the reader can see what was ruled out.

Checks before you finish:
- A drop to zero has a stated reason, or it is reported as a broken-event question.
- Overlapping sources are deduplicated, and the method is named.
- The time zone and window are stated.
- Missing periods are shown as missing.
- Every rate has its denominator, and every proxy is named as one.

Example (invented, for illustration): "Mobile bookings fell from 1,240 last week to 0 on 2026-09-28. Web bookings were at normal volume, and the mobile events stop at 18:00 on 2026-09-28, which looks like ingest lag. Not readable for that week, and not a fall in use. Logged as a tracking question; no trend claimed."
