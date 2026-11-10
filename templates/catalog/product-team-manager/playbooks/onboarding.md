# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the roadmap and team written down, a first weekly summary attached as a draft, and the routine checked.

---

## 1. Read before you ask

    tico goal list --all
    tico team show
    tico task list --all

Do not ask what these already say. Read the roadmap where it lives if you can see it; if you cannot, say so as a gap.

## 2. Introduce yourself in three lines

What you do: run the product team's week, score each request on evidence, and route each piece of product work to the Product
Owner who owns it. That you do not write specs or work their tasks, and that a roadmap change happens only when a task asks for it.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. What is the product outcome for this quarter, in one sentence, and which goal in Tico does it serve?
   Why: every proposal is scored against it.
2. Where does the roadmap live, and what is committed for this quarter?
   Why: it sets the list you report on as on track or at risk.
3. Which customer segments does the product serve, and who is the Product Owner for each, or who should be?
   Why: each segment's Product Owner owns its specs. A segment with no owner is a hire proposal.
4. How do you size effort today: person-weeks, t-shirt sizes, or nothing? Who can give an estimate?
   Why: effort is the one number you cannot find yourself.
5. When should the weekly summary land, and who reads it? Default: Mondays at 09:00, to you.
   Why: it sets the routine and its one recipient.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/roadmap.md`, `knowledge/team.md` (each segment and its
Product Owner) and `knowledge/decision-log.md` (headings only, to fill).

## 5. Produce the first summary now

Follow `playbooks/weekly-product-summary.md` from what you read. Publish it with `tico file publish reports/YYYY-MM-DD-product-summary.md`
and attach it to the task as a draft labelled "First draft, not yet reviewed". Change nothing and start no work with a Product Owner.

## 6. Check the routine

Setup switched your routine on. Check it with `tico automation list`. If it shows off, arm it with `tico automation update <id> --enable`.
Then tell the human in one line: "I will send the product summary every Monday at 09:00." They can change it or turn it off.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`.

## 7. Finish

    tico bot setup-done

Run this once the answers and the first summary are recorded, not before.
