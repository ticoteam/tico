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

1. Where does the cap table live today, who changes it, and can you export it with every holder, class and option grant? Becomes knowledge/cap-table.md. I read the cap table and keep it; I never issue anything.
2. What is the option pool: its size, what is granted, what is left, and the plan document it comes from? Sets the pool-left figure every grant and scenario is checked against.
3. When was the last 409A, who did it, and which board meeting approved the grants since? Sets the 409A and approval dates. A grant after an expired 409A or with no approval goes on the open list.
4. Who is your lawyer or equity platform, and who signs off the cap table before it goes to an investor? Every table that leaves the team goes to that human first.
5. Is a round planned? If so, the expected raise, the pre-money range and the pool top-up the investors expect. Gives the first scenario table real numbers.

## 4. Record

Answers to `state.md` under `## Answers`, dated. Write the `knowledge/` files listed in `AGENT.md`. Anything
nobody answered goes in `knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/monthly-cap-table-check.md`. Attach it labelled "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "monthly cap table check: the 5th of each month at 09:00 team time." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust the routine (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
