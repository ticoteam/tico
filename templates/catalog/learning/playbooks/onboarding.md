# Setup

Schedule: runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes.
Inputs: the task, the roster, the Librarian's answer on required training, and any completion export attached. Outcome: five
recorded answers, the mandatory training list, a first tracker from real completion records or a first role plan, and the first
routine checked.

Done when the five answers are in `state.md`, `knowledge/mandatory.md` is written, the first result is attached to the task labelled
as a first draft, the routine is checked and logged in `memory/decisions.md`, and `hub bot setup-done` has run.

---

## 1. Read before you ask

    hub task show <id>
    hub team show
    hub doc ask "Which training is mandatory here and what does the level guide say?"

Check the roster's roles and start dates, any required-training policy the Librarian can cite, and whether a completion export is
attached. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (learning plans per role, the mandatory training tracker, the new-manager curriculum, budget requests), that you never
mark training complete without a record or spend outside the stated rules, and that training records never judge a person.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record those
and use the defaults for the rest, saying which you used.

1. Which training is mandatory, for whom, and how often (security awareness, harassment prevention, safety, data protection, anything
   your industry requires)? Becomes the tracker. Each course gets its audience, frequency and due date.
2. Where are completion records kept (a learning platform, certificates, a spreadsheet)? Can you give me an export? Nothing is
   marked complete without a record.
3. Which role should get the first learning plan, and is there a level guide for it? Plans are built from the skills the level
   guide asks for, one role at a time.
4. Who are the managers in their first year, and who owns the training budget? Sets who the new-manager curriculum is for and how
   spending is tracked.
5. Which day should the weekly training tracker land, and for whom? (Default: Thursdays 09:00, the HR owner.) Sets the routine and
   its readers.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/mandatory.md` (course, audience by role, frequency,
provider, due rule). Start `knowledge/new-manager.md` if there are first-year managers.

## 5. Produce the first result now

Follow `playbooks/weekly-training-tracker.md` on the completion export, or `playbooks/build-a-role-plan.md` for the first role.
Enroll, buy and send nothing. Label it "First draft, not yet reviewed" and attach it to the task.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id>
--enable`) and tell the human in one line what it does: "I will write this tracker every Thursday at 09:00 for the HR owner." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to leave it
off, adjust `knowledge/` and the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you have
runs on its own. Run it once the answers and the first result are recorded, not before.
