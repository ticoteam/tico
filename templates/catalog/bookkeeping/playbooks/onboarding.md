# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a real close status drafted from an
export, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check what you already have: an export attached to the task, files in a docs folder the human
named, receipts in a mailbox in your access. Do not ask for what these already show. If you cannot
read any transactions, that is answer one.

## 2. Introduce yourself in three lines

What you do (propose categories, keep the close checklist, draft the monthly status), that requested entries and reconciliation use your Tools, and that you never give tax advice.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which accounting system are the books in, who keeps them, and how do the transactions reach you (weekly export, bank and card CSVs, read-only access)? Can you attach last month now?
2. Please paste the chart of accounts and any category rules you already use.
3. On which day should the close checklist start, and when should the status reach you? (Default: the 1st, status by the 5th.)
4. Above what amount, or for which kinds of item, must I always ask first?
5. Where do receipts live, and how many days does the owner need to answer a question? (Default: two.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/categories.md` from the chart
and rules, and `knowledge/close-checklist.md` from `playbooks/monthly-close-checklist.md`, marking who
does each line. Never write an account number or credential into either file.

## 5. Work the export now

Check the export with `skills/check-an-export-before-categorising/SKILL.md`. Then follow
`playbooks/categorize-transactions.md`, and draft the status in the shape of `knowledge/examples/close-status.md`, labelled
"First draft, not yet reviewed". Attach it to the task. Nothing is posted and nobody but the requester sees it.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will draft the close status on the 1st of every month, and I post an entry only when a person asks and my Tools allow it." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
