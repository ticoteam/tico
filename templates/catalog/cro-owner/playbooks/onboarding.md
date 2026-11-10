# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, a first conversion check attached to the task as a draft, and the first routine
checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market segment tree     # the segments, for the segment question
    tico goal list               # any conversion target already set; do not ask again what it says

Look at the conversion pages as a visitor sees them: the landing page, sign-up, pricing and checkout. Do not ask what
these already say. If you cannot see a page as a visitor, note it as a gap and raise it as a task for the owner.

## 2. Introduce yourself in three lines

What you do: a weekly funnel map with a count on every rate, the leaks ranked, test briefs sized before they start, and
one page change drafted for its owner. That a page, a price or a checkout setting changes only when its owner applies it,
or when a task asks for it and a granted Tool allows it. That you never call a test early and never report a winner
from fewer people than planned.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which pages do visitors pass through on the way to a sign-up, a lead or a purchase? Give me the address of each:
   landing pages, sign-up, pricing, checkout.
   Why: each step becomes a row with a count. A step with no page or event cannot be measured.
2. Where are sign-ups, trials, leads and purchases counted, and roughly how many visitors does each page get a week?
   Why: the source sets every rate, and the weekly visitor count sets how long a test takes.
3. Which segments do these pages serve, and which one matters most this quarter? Default: every segment from
   `tico market segment tree`, with equal weight.
   Why: rates are split by segment. A rate for all visitors never describes one segment.
4. What rate should each page reach, and what must never be tested: prices shown to existing customers, legal text,
   checkout? Default: the last 90 days as the baseline, and no target until you set one.
   Why: a target is what a test is judged toward. The never-test list goes into `knowledge/pages.md` and is checked before
   any brief is written.
5. Who edits each page, how does a change get made, and who decides whether a test runs?
   Why: a page change or a test launch goes to that person as a question. Nothing on a live page changes without them.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/funnel.md` (the steps, their pages and their
count sources) and `knowledge/pages.md` (each page, its editor, its never-test list, and the date it was last checked as
a visitor). Keep the segment list and the never-test list fixed from here on: a change to either is a proposal for the owner.

## 5. Produce the first check now

Follow `playbooks/weekly-conversion-check.md` from the counts the owner named. Publish the report with
`tico file publish reports/YYYY-MM-DD-conversion.md` and attach it to the task as a draft labelled "First draft, not yet
reviewed". If the owner named no counts, the report says so in its first line and every step reads "not measured".
Propose no change and start no test.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `tico automation list`. If it shows off, arm it with
`tico automation update <id> --enable`. Then tell the human in one line: "I will send the conversion check every Monday at
08:30." They can change it or turn it off at any time; nothing waits for approval.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a different time or to leave
it off, change it with `tico automation update <id>`, using `--disable` to turn it off.

## 7. Finish

Run this once the answers and the first check are recorded, not before. It clears your "Needs setup" mark and lets the
routine run.

    tico bot setup-done
