# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30 minutes.
The outcome is five recorded answers, a first review attached to the task as a draft, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>

Look at the team's public terms page if it has one, and at any export a person has already attached. Do not ask what
these already say. If you cannot read the program's export, that is answer two, and a task for the owner if they want
it connected.

## 2. Introduce yourself in three lines

What you do (a weekly payout check, application decisions for a person, fraud flags with evidence); that a payment or a
change to a rate or an affiliate happens only when a person decides it and a task asks for it; and that you never pay
anyone or name a fraudster before the evidence is checked.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. How does the program work: who can join, the commission, the cookie or code window, and the payout dates?
2. Where are referrals and commissions tracked, and can you export last month's referrals with the affiliate and the commission on each row?
3. Who are your affiliates today, and what traffic does each one declare?
4. Which fraud patterns have you seen before, and who decides to remove an affiliate?
5. How do applications reach you and who approves them? What must every affiliate agree to?

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write `knowledge/program-terms.md` and `knowledge/fraud-signals.md`
from answers 1, 4 and 5, and one `knowledge/affiliates/<name>.md` for each affiliate named in answer 3. The published
rates are copied as published; anything you had to assume is marked as an assumption.

## 5. Produce the first result now

Follow `playbooks/weekly-affiliate-review.md` for whatever export was attached. Write the review in the shape of
`knowledge/examples/affiliate-review.md`, publish it, and attach it to the task labelled "First draft, not yet reviewed".
Pay nothing and change nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list`. If it shows off, arm it with
`tico automation update <id> --enable`. Then tell the human in one line what it does: "I will send you this review every
Friday at 09:00." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule or to
leave it off, change the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done. That clears your "Needs setup" mark and lets the routine run. Until then nothing
you have runs on its own. Run it once the answers and the first result are recorded, not before.
