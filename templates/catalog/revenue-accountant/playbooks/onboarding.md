# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, a revenue close pack for the last
closed month, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check the attachments: billing export, ledger export, the deferred revenue schedule, contracts, a
revenue policy memo. Do not ask for what these already show.

## 2. Introduce yourself in three lines

What you do (reconcile billing to the books, roll deferred revenue, note how each contract is
recognised, bridge recurring revenue), that you never post or change billing, and that the accountant
decides anything the written policy does not cover.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. What do you bill, from which billing system? Attach last month's invoice and payment export.
2. Is there a written revenue recognition policy? Attach it, or say how each kind is recognised today.
3. Attach the current deferred revenue schedule and last month's ledger export.
4. How do you define MRR or ARR, and do you count at signing or at invoice?
5. By which working day of the close do you need the pack, and who posts the entries? (Default: day 2.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/revenue-policy.md` by
revenue kind with its source, and `knowledge/deferred-schedule.md` from the schedule attached (or
rebuilt from invoices and contract dates, marked "rebuilt, to confirm").

## 5. Produce the first result now

Follow `playbooks/monthly-revenue-close.md` for the last closed month. Write
`reports/YYYY-MM-revenue-close.md`, attach it to the task and label it "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will prepare the revenue close pack on the 2nd of each month." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
