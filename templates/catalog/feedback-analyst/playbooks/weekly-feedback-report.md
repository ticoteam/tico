# Weekly feedback report

Schedule: Mondays at 08:00 team time (routine `weekly-feedback-report`), after setup. Also run by hand on request.
Budget 40 minutes. Nothing is sent to a customer.

Inputs: the last seven days of feedback from every source in your access (tasks and attachments, Support Agent's
digests and known-issues, imported calls, a connected mailbox or channel); `knowledge/themes.md`,
`knowledge/segments.md`, `knowledge/trends.md`; last week's report.

Done when: the report is published with its headline, the log is saved to `knowledge/log/`, `knowledge/trends.md`
holds this week's counts, and the task is finished with the items read and any blocked source named.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/themes.md`, `knowledge/segments.md` and `knowledge/trends.md`. Set `hub bot status set` to one line
naming the report.

## 2. Collect the week

Read the last seven days from every source in your access. Write down each source, how many items it gave, and any
that were blocked or partial. A blocked source is a line in the report.

## 3. Tag

Follow `playbooks/tag-a-batch.md`: one primary theme, a severity and a sentiment for each item, with the segment and
the source and date, and personal details removed. Save the log to `knowledge/log/YYYY-MM-DD.md`.

## 4. Count and compare

Update `knowledge/trends.md`: the count per theme this week and last week. Rank themes by count times severity, and
show three numbers beside each theme: the count, the severity mix, and the segment weight where the human documented
one (otherwise "unweighted").

Run every change through `skills/read-a-change-in-counts/SKILL.md` before you call it new, rising or fading. Fewer than
5 items in a theme is a note, not a trend.

## 5. Check the launches

If a shipped change with a success measure reached 14 days this week, run `playbooks/after-launch-read.md` for it. If
you do not know which changes shipped, ask once on the task with `hub task ask <id>`.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-feedback-report.md` in the shape of `knowledge/examples/weekly-feedback-report.md`:
headline; ranked themes with counts, last week's count, the noise note and severity; two or three anonymised quotes
for each of the top three; what is new; what faded; three suggested actions, each with its evidence and a person to
ask; what was excluded and why; what could not be read; sources. Then:

    hub file publish reports/YYYY-MM-DD-feedback-report.md

## 7. Finish

Commit, then `hub task update <id> --status done --note`: items read, top theme and its change, what is new, and any
source you could not read. Always finish it: an open scheduled task absorbs the next.

## When a source fails

Name it and what is therefore unknown, use the rest, and say so in the headline area of the report. A report built
from one source of three that reads like all feedback is worse than none.
