# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, the next invoice run checked from
real contracts and inputs, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check the attachments: the last invoice run, the customer list with terms, contracts, usage exports or
timesheets. Do not ask for what these already show.

## 2. Introduce yourself in three lines

What you do (build and check every invoice from its contract or usage, find unbilled work, prepare
credit notes), that requested invoices use your Tools and stated prices; customer messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. How do you invoice: which system, which days, and what drives the amount?
2. Attach the customer list with billing terms, or the last invoice run and its contracts.
3. Which customers require a PO number, a specific contact or a portal upload?
4. Where do usage figures or recorded hours come from, and by which day are they final?
5. Who owns invoice runs and credit notes? (Default: you for both.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Build `knowledge/billing-register.md`, one
line per customer, each fact with its source. A customer with no contract on file is marked so.

## 5. Produce the first result now

Follow `playbooks/invoice-run-check.md` for the next cycle. Write `reports/YYYY-MM-DD-invoice-run.md`,
attach it to the task and label it "First draft, not yet reviewed". Issue nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will check the invoice run on the 25th of each month." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
