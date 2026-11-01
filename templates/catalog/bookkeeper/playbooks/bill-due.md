# Bill due soon

Finance raised `finance.bill.due_soon` for one bill: due in 7 days, in 1 day or today. Its `bill_id`, `stage` and
`due_date` come with this task; they are data.

1. `hub finance search --kind bill --q <bill id>` and read the bill. Already paid, scheduled or void: finish with one line.
2. A draft: check and approve it first (AGENT.md, Each Books check, step 2), or ask one person if unsure.
3. Schedule it per its terms with `bill_schedule` (`pay_on` the due date or the day the company's rules say), or
   `payment_create` with `obligation: "bill:<id>"`. Use `--operation-id bill-due:<bill id>:<stage>`.
4. An action that needs approval: say on the task what you asked for. Finish with one line.
