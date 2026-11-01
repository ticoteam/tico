A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme spec and launch review, Tue 2026-09-29

Sample output for Acme, a fictional studio-scheduling software team. Every spec and person is
invented. Nothing was filed in GitHub. First draft, not yet reviewed.

**Headline: 3 specs in flight; Class packs is blocked 6 working days on one pricing question (Dana).
Waitlists launch 2026-10-20 is missing 2 of 7 checklist lines.**

## Blocked
- **Class packs** (`specs/class-packs.md`, building). "Do unused credits expire?" open since
  2026-09-21, owner Dana. Engineering has stubbed expiry; the answer changes 2 of 9 acceptance criteria.

## In flight
| Spec | Appetite | Status | Open questions | Scope change this week |
|---|---|---|---|---|
| Waitlists | 3 weeks, set by Lena 2026-09-14 | building, 5 of 8 issues merged, week 3 | 1 (Marco, due 2026-10-01) | none |
| Class packs | 2 weeks | building, week 3, over by 2 days | 1, blocking | none |
| Multi-location switcher | 3 weeks | agreed | 0 | "switch from the mobile app" moved to non-goals, agreed by Lena 2026-09-24 |

## Waitlists launch readiness (target 2026-10-20)
- Done: QA ran the 9 before-release criteria (`issue-triage` test plan 2026-09-26, results linked); the 2 after-launch
  criteria are tagged, with a watcher and a two-week window in the plan; flag plan (first 10 studios, then all, after
  3 days with no rise in support tickets); release notes owner.
- Due: support briefing (Head of Customer Support, 2026-10-13); messaging brief (Product Marketing Manager, 2026-10-09).
- **Missing: help article** for "Join a waitlist" (reported to the Librarian as a task today).
- **Missing: rollback steps.** The flag turns the feature off, but nobody has written who does it or how a member's
  waitlist position is kept. Blocking; asked the owner once on the task.
- Not a checklist line, and no owner yet: the email a studio member receives when a spot opens. Proposed: ask the UX Writer.

## Sample of a criterion from the Waitlists spec
Given a class is full and the member is not booked, when they tap Join waitlist, then they see their
position and the class shows on their schedule as "Waitlisted".

## Could not read
Two pull requests in the mobile repository; it is not in my GitHub access.
```
