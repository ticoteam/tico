# Weekly inbound read-out

Schedule: Mondays at 08:00 team time (routine `weekly-inbound-readout`), after setup. Also run by hand. Budget 40
minutes. The outcome is one page for the last seven days, set beside the seven before, with the speed target, the
rates, the reason each lost lead was lost, and one or two changes for a person to decide.

---

## 1. Read where things stand

    tico task show <id>
    tico goal list

Then `knowledge/inbound-method.md`, last week's read-out, and `knowledge/lead-log.md`. Find the number this role is
measured on (in `tico goal list`, or in the setup answers) and name it at the top of the read-out.

## 2. Count the leads

From `knowledge/lead-log.md`, for the last seven days and the seven before, on the same window:

- Leads arrived, by source.
- The share sorted as a meeting, a question, not a fit, or a stop.
- Speed to lead: the median business minutes from arrival to a reply ready for review, and the share inside target.
  Late leads stay in both figures.
- Lead-to-meeting: calls booked by leads that arrived in the window, within 14 days of arrival. A lead still open at
  14 days is open, not lost.

## 3. Count the closes

Lead-to-close counts only leads that arrived at least one sales cycle ago (the default is 60 days, from the setup
answers). Read each outcome from the CRM (read only) or from the note of whoever took the call on the task. A lead with no
outcome is open. Report closed-won and closed-lost, and the open count beside them, so the rate can be checked.

## 4. Why each lost lead was lost

For each closed-lost lead in the cohort, follow `skills/read-why-an-inbound-lead-was-lost/SKILL.md`: one primary reason
from the list in `knowledge/inbound-method.md`, with a buyer quote or a dated note as evidence. Then count the reasons.

## 5. Choose one or two changes

Rank by how many leads each would have touched. Examples: a qualifying question added to a form, a source routed to a
different owner, a reply pattern that stalls at the same point. Each change says what it is for, which leads it touches,
and how the next read-out will check it. Propose it; do not change a form, a route or a CRM stage without a task that
asks for it.

## 6. Write and publish

Write `reports/YYYY-MM-DD-inbound-readout.md` in the shape of `knowledge/examples/inbound-weekly-readout.md`, then run
`tico file publish reports/YYYY-MM-DD-inbound-readout.md`. Log each figure that has a target as a KPI reading: `tico kpi list`
names the ones there are, and `tico kpi log <kpi id> <value> --period-start <date> --period-end <date>` records one. Commit,
then `tico task done <id> -m "..." --version N` with the headline and the path.

## When the log is missing

If `knowledge/lead-log.md` has no lines for part of the window, say which days are missing and why, if you know. Ask once
with `tico task ask <id> --to <person> "<question>" --version N`, and finish with what you have. Never fill a gap from
memory or from last week's numbers.
