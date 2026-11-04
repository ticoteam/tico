---
name: build-a-weekly-brief-from-the-record
description: Build the weekly brief from the goals, updates, tasks and meetings in Tico, cite every line with its date, and name in a closing line every source that could not be read.
---
# Build a weekly brief from the record

When to use: every Friday routine run, or a task that asks for this week's brief.

Steps:
1. Read `knowledge/rhythm.md` and `knowledge/open-loops.md`, and the last brief in `reports/` if there is one. Read
   only what changed since that brief.
2. Read each source once: `hub goal list --all`, `hub goal show <id>` for each red, yellow or unread goal,
   `hub update list --kind weekly`, `hub task list --status open --status doing --status waiting`, and
   `hub meeting search --since <last Friday>`. Name any source that refuses you.
3. Sort what you found into five sections: Needs you, Goals, Stalled, What moved, Watch. Put each item in one
   section only.
4. For each goal, write one line: colour, movement since last week with both readings and dates, the evidence
   (reading, update or meeting, with its date), the next step and its owner. A goal with no reading this week
   says "no reading".
5. Propose a colour where the evidence supports one and leave the choice to the goal's owner. Do not change a
   colour yourself.
6. Open the page with one sentence on how the team is doing this week, then Needs you, then the rest.
7. End with "Could not read": each source you could not read and what that leaves unknown.

Checks before you finish:
- Every line carries a source and a date. A number with no source is left out.
- No goal is called on track without a reading this week.
- Nothing on the exclusion list in `knowledge/rhythm.md` appears, and no private conversation is quoted.
- The brief fits one page. Anything longer is a linked file.

Example (generic, invented): The support goal "Answer every ticket the same day" moved from 89 to 91 percent on the
weekly update of 2026-10-30. Its line gives the colour, the update as evidence, a Friday sweep as the next step,
and the support lead as owner. The launch goal has had no reading since 2026-10-09, so its line says "no reading"
and it appears under Stalled.
