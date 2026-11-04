# Invoice run check

Schedule: the 25th of each month at 09:00 team time (routine `invoice-run-check`), after setup. Run again on a billing day if inputs arrive late.
Budget 40 minutes. The outcome is a checked run, ready to issue when the task asks for it and your Tools allow it. Nothing is issued by this playbook alone.

---

## 1. Who is due

From `knowledge/billing-register.md`: every customer with an invoice date in this cycle. Add new contracts signed since the last run (tasks from sales,
closed-won deals where readable).

## 2. Build each invoice

From its source only: the contract's fixed fee and period; the usage export's final figures for the period times the contract rate; recorded hours
times the rate; a signed-off milestone. Prorate a partial period the way the contract says. No final input means held.

## 3. Check each invoice

Run `skills/check-an-invoice-before-issue/SKILL.md` on every invoice. It covers amount against source, rate against contract, entity and contact, PO and
tax, terms and sequence, and duplicates. Mark an invoice ready only when all six pass.

## 4. Find unbilled work

Run `skills/find-unbilled-work/SKILL.md` for the cycle. Each finding goes to `knowledge/unbilled.md` and to the run as a proposed line, held until its owner approves it.

## 5. Write the run and hand it over

Write `reports/YYYY-MM-DD-invoice-run.md` in the shape of `knowledge/examples/invoice-run.md` and publish it with `hub file publish`. Issue the ready invoices
only when the task asks for the issue and your Tools allow it. Customer messages stay drafts until a person turns mail sending on in Tico. Commit, then
`hub task update <id> --status done --note` with ready, held, unbilled and the path.
