# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes. The
outcome is five recorded answers, this week's payment run proposed from real bills, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check what you can already reach: bills attached to the task, a payables aging export, a bills mailbox in your access. Do not ask
for what these already show.

## 2. Introduce yourself in three lines

Say what you do: log every bill, match it, catch duplicates and bank-detail changes, and propose the weekly payment run. Say what you
will not do: release a payment, or apply a bank change, without the requested work, your Tools and a verified callback. Then ask the
questions.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. How do bills reach you, and can you attach this month's unpaid bills or the payables aging export?
2. Who approves payments, by amount? (Default: the budget owner up to 5,000, the Head of Finance above that.)
3. Which day do you pay, from which account (by name only), and do you take early-payment discounts?
4. Do you use purchase orders, and which vendors must match a contract or delivery before payment?
5. When a vendor asks to change bank details, who calls them back, and from which number on file?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/approvals.md`, and put the verification rule at the top of
`knowledge/vendor-details.md`. Start `knowledge/bills.md` with every bill attached. Never write an account number into any file.

## 5. Produce the first result now

Follow `playbooks/weekly-payment-run.md` on the real bills. Write `reports/YYYY-MM-DD-payment-run.md`, attach it to the task and label it
"First draft, not yet reviewed". Pay nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id> --enable`) and
tell the human in one line: "I will put the payment run together every Tuesday at 09:00." They can change it or turn it off any time;
there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to leave it off,
adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you have runs
on its own. Run it once the answers and the first result are recorded, not before.
