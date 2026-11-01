---
name: verify-an-import-source
description: Label imported records with a source or origin only from each record's own creation history, in batches with explicit date bounds, and stop before writing if the cohort does not match the request.
---
# Verify an import's source

When to use: before you label imported records with a source, origin or campaign field, or before a report groups records by where they came from.

Steps:
1. Fix the cohort with explicit lower and upper bounds in one time zone (for example 2026-07-01 00:00 UTC to 2026-08-01 00:00 UTC). Never use a month name alone.
2. Compare the cohort's size with the number in the request. If they differ by more than a few percent, stop before any write and ask which cohort to use.
3. For each record, read its creation history: the activity that created it, that activity's source and its import identifier. A creation date alone does not identify an import. After a merge, the newest creation activity can belong to another source, so read each activity.
4. Assign a label only where the record's own history supports it. A record with a missing or generic history keeps a blank label and counts as unknown.
5. Write in batches sized to the API's rate limit. Record where you stopped in `state.md`, so a rerun neither repeats nor skips records.
6. Keep a ledger of the activity identifiers and timestamps each label relied on, so a reviewer can check it.
7. Report the distribution by source with the batch's limits. One batch does not show that other records share the same source.

Checks before you finish:
- Each label has a record-level source behind it.
- Unknown stays unknown; it is not folded into the nearest source.
- No import was labelled from its date window alone.
- The cohort size matches the request, or the difference is named.

Example (invented, for illustration): "Cohort 2026-07-01 to 2026-08-01 UTC: 1,160 leads; the request said about 1,200. Batch 1 (150 leads): 96 traced to import A and 41 to import B by their creation history; 13 generic API records left blank. No label was set from dates alone."
