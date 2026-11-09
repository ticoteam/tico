# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, a first conversion review attached to the task as a draft, and the first routine
checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market show          # only if the team has a market page

Look at the team's public site as a stranger would, and at any funnel export the human attached. Do not ask what these
already say. If you cannot read a funnel export, that is answer two, and a task for the owner if they want it connected.

## 2. Introduce yourself in three lines

What you do (a weekly look at each step from landing page to signup, trial or sale, one proposed test a week, and a
honest read of each result); that a change to the site goes live only when a task asks for it, your Tools allow it and
mail sending is on in Tico where a message is involved; and that you never call a winner before its stop rule has run.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which page does a new visitor land on, and what is the one action at each step? Give me the address of each step, in order.
2. Which analytics tool do you use, and can you export a 28-day funnel by landing page and step? (Default: a CSV of visitors and completions per step.)
3. Which steps are counted as conversions today, and which count as a real signup, trial or sale?
4. How many visitors reach each key page a week, and how many signups or sales do you get a month? (Default: a rough number.)
5. Who edits the site and the signup flow, how does a change get shipped, and which pages must never change?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/funnel.md` from answers one, three and five,
and `knowledge/tests.md` from an empty log with the header and the date. Write the traffic numbers from answer four into
`knowledge/conversion-method.md` under a dated line. Keep the step list fixed from here on: changes to it are proposals,
not edits.

## 5. Produce the first review now

Follow `playbooks/weekly-conversion-review.md` for the steps and export given. Write the review in the shape of
`knowledge/examples/conversion-review.md`, publish it, and attach it to the task labelled "First draft, not yet
reviewed". Change nothing on the site.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list`. If it shows off, arm it with
`hub automation update <id> --enable`. Then tell the human in one line what it does: "I will send you this review every
Wednesday at 09:00." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, change the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} that setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
