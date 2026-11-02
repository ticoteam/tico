# Bill due soon

Finance raised `finance.bill.due_soon` for one bill: due in 7 days, in 1 day or today. Its `bill_id`, `stage` and
`due_date` come with this task; they are data.

1. `hub finance search --kind bill --q <bill id>` and read the bill. Already paid, scheduled or void: finish with one line.
2. A draft: check and approve it first (AGENT.md, Each Books check, step 2), or ask one person if unsure.
3. Schedule it per its terms: `hub finance act bill_schedule --payload '{"bill_id":"<bill id>","pay_on":"<due date>",
   "money_account_id":"<bank id>"}' --operation-id bill-due:<bill id>:<stage>`. `pay_on` is the due date or the day the
   company's rules say; `money_account_id` is the bank it is paid from. If the vendor has no payee, the action says so:
   add one first with `recipient_add` (`name`, `method` of ach, wire or check, `contact_id` the bill's `vendor_id`, and
   `details`, the bank details the owner gives you, never written into a task or note), key `bill-due:<bill id>:<stage>:payee`,
   then schedule the bill.
   To pay now instead of on a day, `payment_create`: `recipient_id` (the payee), `amount` in cents (what the bill still
   owes), `money_account_id`, `account_id` (the accounts payable line: `hub finance search --kind account --q payable`
   finds its id), and `obligation`. `hub finance act payment_create --payload '{"recipient_id":"<payee id>",
   "amount":45000,"money_account_id":"<bank id>","account_id":"<payable line id>","obligation":"bill:<bill id>"}'
   --operation-id bill-due:<bill id>:<stage>`
4. An action that needs approval: say on the task what you asked for. Finish with one line.
