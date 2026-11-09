---
name: score-a-tech-debt-item
description: Score one technical debt item for the register by what it costs now, the risk of leaving it, and the size of the fix, with the evidence for each number, so the team can plan it instead of arguing about it.
---
# Score a tech debt item

When to use: when an item is added to the register, when the weekly review re-ranks it, and when someone asks why an item is
or is not on the plan.

Steps:
1. Write the item in one sentence about the system, not about a person: "Timezone arithmetic is duplicated in four services"
   and not "Sam's code is messy".
2. Name where it is: the files, services or stores, with paths in the repository.
3. Cost today: list the dated events the item caused in the last quarter (an incident, a slow change, a rework, a delayed
   release, onboarding time). Count them. A feeling is recorded as a feeling and marked as one.
4. Risk of leaving it: what would happen, how likely, and how soon. Name the date when it would bite (for example a known
   change to a rule or a deadline), if one exists.
5. Fix size: the rough effort in engineer-days, and the number of teams it touches. Give a range when you are unsure.
6. Rank with the simple rule the team uses: cost today times risk, divided by fix size. Keep the arithmetic in the register
   so it can be checked. Use scores to order the list, not to claim precision.
7. Name an owner from `knowledge/system-map.md`, or write "owner needed" and raise it in the review.

Checks before you finish:
- Every number has its date and its source.
- The item is about the system, not a named person.
- The fix size includes the migration, if one is needed.
- Items with no dated cost today are marked low confidence.

Example (invented, for illustration): "Background jobs share one queue (services: worker, api). Cost: 2 reminder delays in
September (incident notes 2026-09-09 and 2026-09-18). Risk: exports delay reminders again; medium, next at the November
peak. Fix: 4 days, touches 2 services. Owner: the platform engineer named in the system map."
