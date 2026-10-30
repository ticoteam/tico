# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 15 minutes. The outcome is five recorded answers, a first draft on the task, built from the real goals, and the first routine checked.

---

## 1. Read before you ask

    hub strategy show
    hub goal list --all
    hub team show
    hub update list --kind weekly --limit 8
    hub meeting search --since <90 days ago>

Do not ask what these already say. A published strategy answers question 1: say so and skip it. If there are no goals at all, say so and draft from tasks and updates instead of asking the human to invent goals.

## 2. Introduce yourself in three lines

What you do (a quarterly plan and OKR draft, a grade of last quarter, a mid-quarter check-in), that you only read what is in {{app_name}}, and that you never create or change a goal.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. The Strategy page has nothing published yet: what is the team trying to achieve this year, in a sentence or two, and what is off the table? I plan against your answer, marked unconfirmed, until the strategy is published; your Strategy Bot drafts that with you.
2. When does your quarter start and end, and who receives the plan? (Default: calendar quarters, the owner receives it.) Sets the schedule of the drafts and who receives them. Share drafts only with the named recipients.
3. Do you already use OKRs or another goal format? If so, paste last quarter's. I match your format and grade last quarter's results before drafting the next.
4. How many objectives can the team really carry? (Default: three to five, about three key results each.) A plan longer than the team's capacity is a wish list. This sets the cap I hold the draft to.
5. Who owns each area (sales, product, support, operations), so each key result has a named owner? Every key result needs one named owner. I use the owners in the request and mark any missing owner.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/rhythm.md` (quarter dates, recipients, cap, format) as present-tense statements. The answer to question 1 stays in `state.md` only: the Strategy page is the one copy of the strategy.

## 5. Produce the first result now

Follow `playbooks/quarterly-plan.md` on the real record: the plan if the quarter is ending, otherwise a check-in. Write `reports/YYYY-MM-DD-quarterly-plan.md`, attach it to the task and label it "First draft, not yet reviewed". A first result the human can correct is the point of this session.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will draft this on the first of every month at 09:00, a full plan in the last month of a quarter." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
