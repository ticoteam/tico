# The weekly review page

One HTML page, readable in ten minutes, built from records. Publish it with:

```text
tico page publish reports/weekly-<YYYY-MM-DD>.html --title "Weekly review" --cadence weekly --as-of <YYYY-MM-DD>
```

`<YYYY-MM-DD>` is the Sunday the week ended. Use the same title every week so the pages form one series.

## Layout, top to bottom

1. **Headline.** One sentence: the most important thing that changed. Then three counts: numbers off target,
   commitments done of total, open issues (with last week's count).

2. **Needs you.** Only if something waits on a person: each open OK or question, who it waits on, since when, and a
   link. If nothing waits, leave the section out.

3. **Scorecard.** A table, one row per number.

   | Number | Owner | Target | This week | 8 weeks | Source | |
   |--------|-------|--------|-----------|---------|--------|-|
   | | | | value, or "not read" | small trend | command or Tool | colour |

   Colour the last cell green, yellow, red or gray. Never colour by text alone: write the word too. Under the table,
   one line per red or yellow row with the owner's reason.

4. **Goals.** One row per quarterly goal: name, owner, grade so far (0 to 1, or "ungraded"), pace (ahead, on, behind),
   date of the last check-in. Mark any with no check-in this week.

5. **Last week's commitments.** A list, each marked done or not done, with the task link. The count as a fraction.

6. **Issues worked.** For each of the three: the problem in one sentence, the cause found, the ending (decided, to do,
   dropped) with the owner, date and task link.

7. **Decisions.** Each decision made this week in one line, marked two-way or one-way, with how to undo it or the OK
   it carries.

8. **Next week's commitments.** Each with one owner and one date. These are scored next week.

9. **Watch.** At most three things not yet issues: a deadline inside 30 days, a number drifting, a renewal.

10. **Could not read.** Every source that failed or was stale this run, and who was asked to fix it. If everything was
    read, write "All sources read."

## Style

- Numbers in tables, right-aligned, with units. Dates written out (8 March 2026).
- Every figure has its source and period within reach: in the row, or in a tooltip.
- Small charts beat adjectives. An eight-week trend per row is enough. No decoration.
- No narrative of activity. If a sentence does not help someone decide or act, cut it.
- Names: a teammate's display name and role, never a nickname for a bot.
- The page must read correctly on a phone: the scorecard may scroll sideways, nothing else should.

## If there is a meeting

Default: none. Call one only when an issue needs two or more people in the same conversation. Then:

- Send the page as the pre-read at least one working day before.
- The agenda is the issues, at most five, each with minutes. The minutes add up to the meeting length minus five.
- Start with the scorecard for five minutes: off-target numbers only, no discussion. Discussion happens in the issue.
- End with the commitments read back: who, what, by when. Each becomes a task before the meeting closes.
- Items not reached go under "Carried over" on the next agenda, never silently dropped.
- Thirty minutes at most for the weekly. Longer conversations get their own meeting on one issue.

## Monthly and quarterly

- **Monthly:** on the first review after the month's books close, add a section with the month's numbers from
  `tico finance report pnl` and `budget_vs_actual`, and the budget lines over by more than the Finance Owner's limit.
- **Quarterly:** replace sections 4 and 8 with the quarter's grades and next quarter's goals. See
  `goals-and-key-results.md`.
