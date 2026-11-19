# Incident review template

Write the review within the time set in `knowledge/engineering.md` (default two working days), while the timeline is
fresh. Build it from the incident task's comments, not from memory. Publish it as a page on the task.

## The blameless rule

The review asks what the system allowed, not who slipped. A teammate, bot or person, acted on what they could see at
the time. If a mistake was easy to make, the finding is that it was easy to make. Write "the retry job had its own
query" and never "the builder forgot the check". A review that blames gets worse information next time.

## The page

```
Incident review: <title>
Date: <date>      Severity: SEV<n>      Lasted: <start> to <all clear>, UTC
Written by: <teammate>      Status: Draft | Final

1. Summary
   Two or three plain sentences: what customers saw, for how long, what fixed it.

2. Impact
   Who was affected and how many, from sources (link each).
   Start of harm, time detected, time mitigated, time resolved.
   Time to detect   = detected minus start.
   Time to mitigate = mitigated minus detected.
   Money, data or work lost, if any, and what was done about it.
   Anything not known is written as "not known", never estimated.

3. Timeline
   A chart or table of UTC time and event, copied from the task comments:
   first sign, detection, each update sent, each action tried, mitigation, fix released, all clear.
   Mark any stretch nobody can account for; that gap is itself a finding.

4. Causes
   One row per cause or contributing factor.
   | Cause | Kind | Status | Evidence |
   Kind: trigger (what set it off) | underlying cause | why it was not caught earlier | why it took this long to fix
   Status: confirmed (evidence exists) | suspected | ruled out
   For each confirmed cause ask "why" until you reach something about the system that can be changed.

5. What went well
   What limited the harm or sped things up. Keep these.

6. What went poorly
   What slowed detection, mitigation or the fix. Systems and process only.

7. Actions
   | # | Action | Kind | Owner | Task | Due | Done when |
   Kind: Detect (we would have known sooner) | Mitigate (less harm or faster recovery) | Prevent (it cannot happen)
   Every confirmed cause and contributing factor has at least one action.
   If detection or recovery was slow, at least one action is Detect or Mitigate, not only Prevent.

8. Lessons
   One to three sentences worth carrying to other parts of the product.
```

## Rules for actions

- Each action changes the system: a check, an alarm, a design change, a runbook step that a bot will follow. "Be
  more careful" and "improve monitoring" are not actions.
- Each has one owner (a teammate, not a team), a calendar date, and a "done when" that someone could test.
- Search for an existing task before filing a new one.
- File each with `tico task file --parent <incident task id> --owner <owner> "<action>"`.
- Choose the strongest kind of fix available, in this order: change the design so the mistake cannot be made; make
  it a type or automatic check; add a test; write it down. That ordering is the `turn-corrections-into-gates` skill.

## Before marking the review Final

- Is every number linked to a source?
- Is every cause labelled confirmed, suspected or ruled out?
- Does every confirmed cause have an action?
- Does any sentence name a teammate as a cause? Rewrite it.
- Would someone who was not there understand what happened from the summary and timeline alone?
