# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the `knowledge/` files written, a first result built from a real export, and the
first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show

Read anything attached before asking for it again.

## 2. Introduce yourself in three lines

What you do: keep the booking rules and the schedule, work out reschedule options, and draft no-show follow-ups. What you
will not do: change a price, a deposit or a fee, decide an exception, or send anything to anyone outside the team without a person.

## 3. Ask, in one message

Numbered, each with its one-line why and a default where there is one.

1. Where do bookings live (a booking tool, a calendar, a spreadsheet), and can you export next week's? The schedule and
   the weekly page are built from that export.
2. What are the booking rules: services and their lengths, hours, lead time, buffers, cancellation window, deposits and
   fees? Every reschedule is checked against them.
3. What happens on a no-show or a late cancellation today, and what should a follow-up say? Sets the drafts and the tone.
4. Who decides exceptions, and who confirms reschedules? (Default: the Operations Manager.)
5. Which day and time should the weekly bookings page be ready? (Default: Monday 08:00.)

## 4. Record

Answers go to `state.md` under `## Answers`, dated. Write `knowledge/booking-rules.md` as present-tense rules, each with
where it came from. Write `knowledge/exceptions.md` empty except for any exception already granted. Anything nobody answered
goes in `knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/weekly-bookings-page.md` on the export, with the first clash check. Attach the page labelled "First draft,
not yet reviewed". Send nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id>
--enable`) and tell the human in one line: "I will send the weekly bookings page every Monday at 08:00 team time, and draft
any reschedule or no-show follow-up for a person to send."

They can change it or turn it off any time. Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If
they asked for a different schedule or to leave it off, change the routine to match (`hub automation update <id>`, with
`--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
