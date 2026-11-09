# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 25 minutes.

Outcome: five recorded answers, the policy written as checks, a first audit of a real month, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub doc ask "What is our expense and travel policy?"

Check the attachments for an expense or card export and receipts. If the Librarian finds the written policy, read it and
skip asking for the limits. Ask only about what it does not say.

## 2. Introduce yourself in three lines

What you do (check every expense line against the written policy and give each owner the few lines to question). Say that
requested expense actions use your Tools, and that expenses stay confidential. State facts, never accusations.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where do expenses come from, and can you attach last month's export with receipts?
2. Where is the expense and travel policy, or what are the limits for meals, hotels, flights and gifts?
3. Above what amount must a line have an itemised receipt? (Default: 75.)
4. How many days after spending must a claim be submitted? (Default: 60.)
5. Who owns expense reviews, and who in finance sees the audit? (Default: each manager, and you.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Turn the policy into checks in `knowledge/policy-rules.md`, one
rule per line with the policy section it came from. Write `knowledge/approvers.md`. Anything nobody answered goes in
`knowledge/open-questions.md`, not guessed.

## 5. Produce the first result now

Follow `playbooks/monthly-expense-audit.md` on the month attached. Write `reports/YYYY-MM-expense-audit.md`, attach it to
the task, and label it "First draft, not yet reviewed".

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list`. If it shows off, run
`hub automation update <id> --enable`. Tell the human in one line what it does: "I will audit each month on the 3rd." They can
change it or turn it off any time. There is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different schedule or to
leave it off, change the routine to match (`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. It clears your "Needs setup" mark and lets the routine run. Until then nothing you
have runs on its own. Run it once the answers and the first result are recorded, not before.
