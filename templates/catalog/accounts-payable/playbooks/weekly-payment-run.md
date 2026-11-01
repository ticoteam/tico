# Weekly payment run proposal

Schedule: Tuesdays at 09:00 team time (routine `weekly-payment-run`), after setup. Budget 35 minutes. The outcome is one payment run
built from matched bills and verified payment details. Nothing is released unless the task asks for it and your Tools allow it.

---

## 1. Bring the register up to date

Log every new bill (see `playbooks/process-a-bill.md`). Mark bills paid since last week from the bank or payables export attached to
the task. A bill you cannot confirm as paid stays open and is named.

## 2. Choose what is due

Due this run: bills whose due date falls before the next run, plus bills with an early-payment discount the team takes (see
`knowledge/payments-fraud.md` for the arithmetic). Everything else waits, and its due date is shown in the "coming up" line.

## 3. Hold what is not ready

Hold each of these, with the reason in one clause:
- missing payment facts;
- no match where a purchase order or contract requires one, or a difference over tolerance;
- a duplicate candidate;
- a vendor whose bank details changed and are not yet verified by callback;
- a disputed bill.

## 4. Check the total

Total by paying account (by name). Compare with the Head of Finance's latest cash figure and minimum line, where readable. If the
run would breach the minimum, say so in the first line and propose which bills could move to the next run without a fee.

## 5. Write the run and hand it over

Write `reports/YYYY-MM-DD-payment-run.md` in the shape of `knowledge/examples/payment-run.md` and publish it with
`hub file publish reports/YYYY-MM-DD-payment-run.md`. Pay the requested, matched lines only when the task asks for it, your Tools
allow it, and the payee's details are verified. Otherwise attach `run.json` to the task and name the access that is missing.
Commit, then `hub task update <id> --status done --note` with the total, the held count and the path.
