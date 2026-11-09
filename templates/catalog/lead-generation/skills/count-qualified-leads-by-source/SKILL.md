---
name: count-qualified-leads-by-source
description: Count qualified leads by source for the week and the last four weeks, using the agreed definition and Sales' accept and reject decisions, and report blank sources and too-small samples as they are.
---
# Count qualified leads by source

When to use: before any weekly lead count is stated, and before a source is called better or worse than another.

Steps:
1. Read the definition of qualified from `knowledge/qualified.md`, with the date Sales agreed it. If it has changed in the window, split the count at the change and report both.
2. Take every lead in the export for the window. Keep the source field exactly as recorded. A blank source is "unknown", never "direct" and never reassigned.
3. Remove duplicates within the week. Label repeats from earlier weeks as repeats and count them separately.
4. For each lead, take Sales' decision from the feedback: accepted, rejected, sent back, or awaiting Sales. A lead with no decision is "awaiting Sales", not accepted.
5. Count per source: submitted, accepted, rejected, awaiting Sales. Then count accepted for the last four weeks.
6. Mark any source with fewer than about five accepted leads in four weeks as too small to read. Do not rank it.
7. Write the reject reasons in Sales' words beside the counts, grouped by source.

Checks before you finish:
- Each count names its definition, its window and its source field.
- Accepted, rejected and awaiting Sales add up to the submitted count for each source.
- Unknown sources are a line of their own.
- No count is stated from a lead Sales has not decided on.

Example (invented, for illustration): "Partner referrals, week of 2026-10-27: submitted 12, accepted 9, rejected 2, awaiting 1. Four weeks accepted: 31. Reject reasons (Sales' words): 'wrong role' (2). Definition: knowledge/qualified.md, agreed 2026-09-15."
