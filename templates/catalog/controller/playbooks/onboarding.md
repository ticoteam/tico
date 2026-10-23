# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 30 minutes. The outcome is five recorded answers, the `knowledge/` files written,
a first result built from a real export, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show

Read anything attached before asking for it again.

## 2. Introduce yourself in three lines

What you do, what you will not do, and that requested work uses your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why.

1. Which accounting system holds the books, and can you export the trial balance and the bank and card reconciliations for last month? The review is built from those exports every month.
2. What does your close look like today: who does what, by which working day, and what usually slips? Becomes the close checklist and calendar.
3. Which accounts need a monthly reconciliation, and who does each? Becomes the reconciliation status list.
4. Who reviews the close, and who is your outside accountant? What do they ask for every month? The close summary goes to that human, and their usual requests become checklist steps.
5. On which calendar day of the month should the close review run? (Default: the 3rd, weekends included.) Sets the routine's schedule.

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write the `knowledge/` files listed in `AGENT.md`. Anything
nobody answered goes in `knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/month-end-close-review.md`. Attach it labelled "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "month-end close review: the 3rd of each month at 09:00 team time." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust the routine (`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
