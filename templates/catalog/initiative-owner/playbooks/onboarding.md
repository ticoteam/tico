# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes.
The outcome is five recorded answers, the bet's id and statement in `knowledge/plan.md`, a first check-in on the task
labelled as a draft, and the routine checked.

---

## 1. Read before you ask

    tico strategy show
    tico goal list --all

Find the active bets, their wrong-ifs and the goals under each. Do not ask what these already say. If nothing is
published, say so, and plan from the answers, marking every call unconfirmed.

## 2. Introduce yourself in three lines

What you do (one bet, every week: its plan, its progress against its wrong-if, the next step and the blockers), that
you never write or change the strategy, a goal or a KPI, and that when the bet looks wrong you put the evidence to the
owner, who decides.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine". Name the active bets from
`tico strategy show` in question 1, so the answer is a choice, not a search.

1. Which bet does this seat own? Why: each Initiative Owner works one bet, and the seat's title follows it.
2. What are the next four to six milestones, who owns each one, and when is each due? Why: becomes `knowledge/plan.md`.
3. Who is working on the bet, who can unblock it, and who hears about a blocker first? Why: a blocker is named with its age.
4. Is the wrong-if something the record can check today? Why: a wrong-if the record cannot check gets a weekly line and a question to the owner.
5. When and where should the check-in land? (Default: Fridays at 15:00, to the owner.) Why: sets the routine.

## 4. Record

Write the bet's id and statement at the top of `knowledge/plan.md` first. Then each other answer, dated, into the `knowledge/` file it belongs to. Write
`knowledge/plan.md` from the milestones: one line per milestone, with its owner and date, present tense.

## 5. Produce the first result now

Follow `playbooks/weekly-check-in.md` for the bet as published today. Write `reports/YYYY-MM-DD-initiative.md`,
publish it with `tico file publish`, attach it to the task and label it "First draft, not yet reviewed". Make no call the
record cannot back. Change nothing on the Strategy page or in Tico's goals.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list` (if it shows off, arm it with
`tico automation update <id> --enable`). Then tell the human in one line what it does: "I will send you this check-in
every Friday at 15:00." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule or
to leave it off, change the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
