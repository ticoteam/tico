---
name: define-a-metric-as-a-query
description: Define a metric as one sentence, one query and one denominator before it is reported, and record it so the same word means the same count every week. Use it whenever a definition is first used or changed.
---
# Define a metric as a query

When to use: before a number is reported for the first time, whenever a metric's definition changes, and when two sources give different counts for the same word.

Steps:
1. Write the metric in one sentence with its unit and window, for example "an active studio is a studio with at least one booking in the calendar week (Monday to Sunday, team time)". A definition that needs more than one sentence is two metrics.
2. State the inclusions and exclusions: test accounts, internal staff, deleted or suspended accounts, trials. Say which are left in and why.
3. Choose the event or events and the unique key each one is counted by. If two sources hold the same event, pick one, or deduplicate by event ID before counting.
4. Write the query against the team's database, with the time zone set in the query rather than assumed, and the window fixed by step 1.
5. Write the denominator as a query of its own: who could use the thing (plan, platform, flag, signup week).
6. Sanity-check the query against a known total (last week's signups, the active-studios count the admin shows) and say how close it is. A difference you cannot explain stops the definition.
7. Save the query in `queries/`, and record the definition in `knowledge/definitions.md`: the rule, the query file, the date, and the last number it produced.
8. If the definition changes, announce it and restate the last number under both definitions, so the change is visible.

Checks before you finish:
- The definition fits in one sentence with its unit and window.
- The denominator is named and is a query, not a judgement.
- The time zone is set in the query.
- Each event is counted once, by its unique key.
- The sanity check is shown, with the known total it was compared with.

Example (invented, for illustration): "Activated studio: a studio that created a class and had at least one booking within seven days of its signup. Denominator: studios that signed up in the same calendar week. Time zone: team time, set in the query. Sanity check: signups in the week match the admin export within one (212). Saved as `queries/YYYY-MM-DD-activation.sql`; recorded in `knowledge/definitions.md` with the date."
