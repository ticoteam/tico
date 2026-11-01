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

What you do (checklists, the inspection log, re-dos by cause, scores per crew with their job counts), what you will not
do (discipline or message a crew member), and that requested work uses your Tools.

## 3. Ask, in one message

Numbered, each with its one-line why and a default where there is one.

1. What jobs do your crews do, and what does a good one look like for each? Is there a checklist today? Becomes the
   checklists. Every item needs a pass standard and a photo, or it cannot be scored.
2. How are jobs inspected today: who checks, how many jobs, and where are results recorded? Can you export last month's?
   Becomes the inspection log and the first scores.
3. How do you find out about a re-do today (a complaint, a callback, an inspection), and what does one cost? Starts re-do
   tracking, with cost in hours.
4. Who owns quality and decides what to do about a crew with low scores? (Default: the Operations Manager.) Scores go to
   that person. I never act on a crew myself.
5. Which day should the weekly quality page be ready? (Default: Monday 09:00.) Sets the routine's schedule.

## 4. Record

Answers go to `state.md` under `## Answers`, dated. Write the `knowledge/` files listed in `AGENT.md`, with
`knowledge/quality-method.md` copied from the standing method. Anything nobody answered goes in
`knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/weekly-quality-page.md`. Mark any crew with fewer than 10 jobs as not yet reliable. Attach the page
labelled "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id>
--enable`) and tell the human in one line: "I will send the weekly quality page every Monday at 09:00 team time."

They can change it or turn it off any time; there is nothing to approve. Record it in `memory/decisions.md` and set
`state.md` to `Setup: finished`. If they asked for a different schedule or to leave it off, change the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
