# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 20 minutes. The outcome is six recorded answers, a first onboarding board from the
customers in onboarding today, and the first routine checked.

---

## 1. Read before you ask

    hub team show
    hub task list --status open --status doing --status waiting
    hub meeting search "kickoff"

Find the customers signed in the last 60 days and any kickoff or training calls. If a CRM is in your
access, read closed-won deals from the same window. Do not ask what these already show.

## 2. Introduce yourself in three lines

What you do (a plan per new customer, kickoff prep, milestone tracking, a weekly board), that customer messages stay drafts until a person turns mail sending on in Tico, and that requested account changes use your Tools and verified facts.

## 3. Ask, in one message

Numbered, each with its one-line why, offering the defaults so a human can answer "fine".

1. What has to be true for a customer to be live, and what is their first moment of real value?
2. What are the usual onboarding steps, in order, and who does each on both sides?
3. How long should onboarding take for small, mid-size and large customers? (Default: 3, 6, 10 weeks.)
4. How do you learn a deal has closed, and where does sales write what it promised?
5. After how many days without progress is a customer stuck, and who hears? (Default: 7 days; you.)
6. Who owns customer messages, and who runs the kickoff calls?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/milestones.md`: the
milestones in order, first value, target durations, the stuck rule and the owner.

## 5. Produce the first result now

For each customer in onboarding today, write a short plan in `knowledge/customers/` from what you can
read, marking every gap. Then follow `playbooks/weekly-onboarding-board.md` and write
`reports/YYYY-MM-DD-onboarding-board.md`, labelled "First draft, not yet reviewed". Contact nobody.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will update this board every Monday at 10:00." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
