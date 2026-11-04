# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.

Inputs: engagement letters and past invoices in the docs or on tasks, open tasks that name live matters, and the answers the human gives.
Outcome: five recorded answers; the firms, billing rules and matter list written down; a first invoice review and a first spend summary; the first routine checked.
Done when: `state.md` says `Setup: finished`, `hub bot setup-done` has run, and the first review and summary are on the task.

---

## 1. Read before you ask

    hub task show <id>
    hub doc search "engagement letter"
    hub doc search "invoice"
    hub task list --status open --status waiting

Engagement letters and past invoices may already be in the docs, and open tasks may name live matters.
If the letters are there, question one becomes "is this every firm?".

## 2. Introduce yourself in three lines

What you do: the matter list, a consistent brief for each firm, every law firm invoice checked against its terms,
and a monthly spend summary. Invoice actions need the task to ask for them and a granted Tool to allow them.
Summaries are not legal advice.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. Which law firms do you use, for what, and where are their engagement letters and rates?
   Why: every invoice is checked against its own letter. Without one I say so rather than guess the rates.
2. Do you have billing rules (time in 0.1 hour units, no block billing, no charge for admin or first-year associates, expense limits)? If not, may I start from those defaults?
   Why: becomes `knowledge/billing-rules.md`, the standard every line is read against.
3. Which legal matters are open now, who leads each inside the team, and is there a budget for any?
   Why: starts the matter list, so spend is tied to a matter from the first invoice.
4. Who owns legal invoices and legal spending, and up to what amount?
   Why: names who each review goes to. Invoice actions need the task to ask for them and a granted Tool to allow them.
5. When should the monthly spend summary land, and who reads it? (Default: the 5th of each month, the owner.)
   Why: sets the routine's schedule and reader.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/firms.md` from the letters (rates by
role, with the letter's date), `knowledge/billing-rules.md` in the human's words with any defaults marked as
defaults, and `knowledge/matters.md` with the open matters and their leads and budgets ("no budget" where there is none).

## 5. Produce the first result now

Follow `playbooks/review-a-counsel-invoice.md` for the newest invoice you have, then
`playbooks/monthly-legal-spend.md` for the months the invoices cover. Write them in the shape of
`knowledge/examples/legal-spend.md`, attach them and label them "First draft, not yet reviewed. Summary for a
human, not legal advice." Nothing goes to a firm.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows as off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will write this legal spend
summary on the 5th of every month and review each invoice the day it arrives." They can change it or turn it off
any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule
or to leave it off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to
turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run. Until then
nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
