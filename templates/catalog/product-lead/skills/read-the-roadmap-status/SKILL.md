---
name: read-the-roadmap-status
description: Report each committed roadmap item as on track, at risk or slipped, from its source and its date, with the evidence. Use it for the weekly summary and whenever someone asks what is committed. A missing roadmap is reported, not treated as a blocker.
---
# Read the roadmap status

When to use: in the weekly summary, and whenever a person asks what is committed, what is late, or what changed.

Steps:
1. Read the list the owner named as the roadmap (a doc, milestones, a board). Copy each item with its goal, owner and target date. Do not add items you have not been given.
2. Read each item's source, read only: its milestone or linked issues, the status of its spec, and the owner's latest update. Note the date of each read.
3. Mark the status. On track: on target, with any dependency named and on schedule. At risk: a dependency is open with no date, the spec is missing, or the item is more than a week behind its plan. Slipped: the target date has passed.
4. Write one line per item: the status, the evidence and its date.
5. Compare with last week. A moved target or a dropped item needs a decision on record in `knowledge/decision-log.md`. If there is none, say so.
6. If there is no roadmap, or a source cannot be read, say so once. Then report the live work from open tasks and the product reports instead, and label it as not the roadmap.

Checks before you finish:
- Each item names its source and the date it was read.
- No item was invented.
- "Slipped" is used only when the target date has passed.
- Every moved target or dropped item has a decision, or is flagged as lacking one.
- A missing source is named, with what is therefore unknown.

Example (invented, for illustration): "Calendar sync fix: slipped. The target of 2026-09-25 has passed and two of five issues are open (milestone read 2026-09-28). Class packs: at risk. The spec has had four open questions since 2026-09-21."
