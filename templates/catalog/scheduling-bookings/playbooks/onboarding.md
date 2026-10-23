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

1. Where do bookings live (a booking tool, a calendar, a spreadsheet), and can you export next week's? The schedule and the weekly page are built from that export.
2. What are the booking rules: services and their lengths, hours, lead time, buffers, cancellation window, deposits and fees? Becomes knowledge/booking-rules.md. Every reschedule is checked against it.
3. What happens on a no-show or a late cancellation today, and what should a follow-up say? Sets the draft follow-ups and the tone.
4. Who decides exceptions to the rules, and who confirms reschedules? (Default: the Operations Manager.) Exceptions and confirmations go to that human.
5. Which day and time should the weekly bookings page be ready? (Default: Monday 08:00.) Sets the routine's schedule.

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write the `knowledge/` files listed in `AGENT.md`. Anything
nobody answered goes in `knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/weekly-bookings-page.md`. Attach it labelled "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "weekly bookings page: mondays at 08:00 team time." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust the routine (`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
