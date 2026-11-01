---
name: check-a-group-before-reporting
description: Check every group in a survey result against the anonymity threshold before any number, count or quote leaves the working files, merge or suppress small groups, and confirm that no combination of filters can single someone out.
---
# Check a group before reporting

When to use: before any score, participation figure, theme count or comment leaves the working files, for a readout, a manager
note or a task.

Steps:
1. Read the threshold in `knowledge/survey.md`, and the separate rule for comments if it has one. If neither is written, stop
   and ask the owner before you report anything.
2. List every group you intend to show, with its number of responses. Check each against the threshold.
3. For a group below the threshold, pick one action and write it down: merge it with the nearest group of the same kind (for
   example the adjacent team), and name the merged group; or show it as "not shown (fewer than N responses)".
4. Check combinations. A team, a tenure band and a location together can leave one person in a cell. Count the cells you will
   show. A cell below the threshold is suppressed or merged, like any group.
5. For comments, quote one only when it identifies nobody. Otherwise paraphrase the theme. A theme that rests on too few comments
   in a small group is not shown.
6. Record in the working note how many groups were checked, merged and suppressed, so the owner can see the rule was applied.

Checks before you finish:
- Every number shown has its group size beside it.
- No group under the threshold appears, and every merge is named.
- No filter combination shrinks a shown group below the threshold.
- Every quote identifies nobody, or it is paraphrased.
- The raw export is deleted from the working tree.

Example (invented, for illustration): "Q3 pulse, 42 invited, 31 answered. Support 9 answered: merged with Operations (5 answered) to show
'Support and Operations, 14 answered'. Tenure under one year, 4 answered: not shown. Themes: 'workload' raised by 6 comments in
the merged group; shown. One comment named a manager: paraphrased, not quoted."
