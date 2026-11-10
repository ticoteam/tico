# Weekly outbound read-out

Schedule: Mondays at 08:30 team time (routine `weekly-outbound-readout`), after setup. Also run by hand. Budget 40 minutes.
The outcome is one page for the last seven days, set beside the seven before: meetings booked against the target; sends,
replies and positive replies by sequence and step; the guardrails; and one change for a person to decide.

---

## 1. Read where things stand

    tico task show <id>
    tico goal list

Then `knowledge/outbound-method.md`, last week's read-out, and `knowledge/sequences.md`. Name the meetings target at the top of
the read-out.

## 2. Count from the log, then reconcile

From `knowledge/sequences.md`, for both windows: unique contacts sent to, sends by step, replies of any kind, positive replies,
meetings booked, opt-outs, bounces and complaints. Reconcile the send count with the mail or CRM export. If they differ, the export
is the figure and the gap is named in the read-out.

## 3. Read by sequence and by step

Read `skills/read-a-sequence-by-step/SKILL.md` first. Every rate uses messages sent as its denominator, and every sequence uses the
same window. A positive reply is one that confirms interest. Opt-outs, wrong-person replies and auto-replies are not positive.

## 4. Read the guardrails beside each result

Opt-out rate, bounce rate and spam complaints for each sequence, each against its threshold in `knowledge/outbound-method.md`. A
sequence over a guardrail is reported first, whatever its meetings.

## 5. Choose one change to test

One change only, written as a proposal on the task: the hypothesis in one sentence, the control (the sequence as it runs now), a
random 50/50 split of the same contacts, the primary metric (positive replies per unique send, at 7 days), the stop rule set before
the test starts, and the date of the readout. The test runs only when a person approves it and a granted Tool allows it. Do not change
the copy of a live sequence without that approval.

## 6. Write and publish

Write `reports/YYYY-MM-DD-outbound-readout.md` in the shape of `knowledge/examples/outbound-weekly-readout.md`, then run
`tico file publish reports/YYYY-MM-DD-outbound-readout.md`. Log the meetings figure as a KPI reading: `tico kpi list` names the KPIs
there are, and `tico kpi log <kpi id> <value> --period-start <date> --period-end <date>` records the reading. Commit, then
`tico task done <id> -m "..." --version N` with the headline and the path.

## When the log is missing

If `knowledge/sequences.md` has no lines for part of the window, say which days are missing. Ask once with
`tico task ask <id> --to <person> "<question>" --version N`, and finish with what you have. Never fill a gap from last week's
numbers.
