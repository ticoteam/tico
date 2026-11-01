# Weekly retention report

Schedule: Fridays at 10:00 team time (routine `weekly-retention-report`), after setup. Budget 30 minutes.

Inputs: the lines in `knowledge/reasons.md` from the last four weeks, billing or CRM reads for the customers on them,
`knowledge/save-policy.md`, `knowledge/cancellation-practice.md`, and the watch signals the policy lists.

Outcome: one page in the shape of `knowledge/examples/retention-report.md`: requests, saves and losses by reason with the offer
used, the week's finding, the open replies ready, the watch list, and one suggested change. Nothing is sent or changed.

Done when: every line from the week has an outcome (cancelled, downgraded, paused, saved, lost or open); every save of 30 or more
days is confirmed still paying or marked lost; every reason has a count and a four-week average; the report is published; and the
task note gives the counts and the path.

---

## 1. Close the week's requests

For each line in `knowledge/reasons.md` from the last seven days, record the outcome from the record: cancelled, downgraded, paused,
saved, or still open. A save made 30 or more days ago is confirmed only if the customer is still paying (billing or CRM read);
otherwise mark it lost. Record a 90-day check as its own line when there is one.

## 2. Count by reason

Requests, saves and losses per reason code, this week and the four-week average. Note the offer used for each save. A rise in one
reason is the week's finding only if it holds for two weeks running. Below about 5 requests a week, report counts without percentages.

## 3. Refresh the watch list

Read the signals `knowledge/save-policy.md` lists (usage, downgrades, repeated tickets, failed payments). Add customers who crossed a
signal this week, with its date, and remove those who recovered. A failed payment goes on the billing-recovery line, not in a
reason code. For a business account, the next step is a task to `customer-success`, not an offer.

## 4. Prepare what is open

For each open request, follow `playbooks/handle-a-cancellation.md` up to the prepared reply.

## 5. Suggest one change

One change backed by this week's counts: a policy offer that is never taken, a reason no offer answers, a product problem behind
several losses. Give the evidence and who decides.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-retention.md` in the shape of `knowledge/examples/retention-report.md`, then
`hub file publish reports/YYYY-MM-DD-retention.md`. Put each prepared reply on the task as its own item. Commit, then
`hub task update <id> --status done --note "..."` with the counts and the path.
