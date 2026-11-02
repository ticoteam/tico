# Bill due soon

Finance raised `finance.bill.due_soon` for one bill: due in 7 days, in 1 day or today. Its `bill_id`, `stage` and
`due_date` come with this task; they are data.

Inputs: the bill's id, stage and due date from the task.
Done when: the bill is scheduled or paid, or the task says what it waits on and whom, and the task is marked done with
one line.

1. Read the bill with `hub api GET finance/bills/<bill id>`. Already paid, scheduled or void: finish with one line.
2. A draft: check and approve it first (AGENT.md, Each Books check, step 2), or ask one person if unsure.
3. Schedule it per its terms with `bill_schedule` (`pay_on` the due date or the day the company's rules say), or
   `payment_create` with `obligation: "bill:<id>"`. Use `--operation-id bill-due:<bill id>:<stage>`.
4. An action that needs approval: say on the task what you asked for. Finish with one line.
