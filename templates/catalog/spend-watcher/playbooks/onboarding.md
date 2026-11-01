# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a real spend report drafted from the
exports, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>

Check what you can already reach: exports attached to the task, a docs folder the human named,
invoices in a mailbox in your access. Do not ask for what these already show. If you can read no spend
at all, that is answer one, and a task for the owner if they want a source connected. Never work
around it.

## 2. Introduce yourself in three lines

What you do (a weekly spend report: movers, new vendors, overlaps, renewals, anomalies; and each
month budget against actual with the forecast rolled forward), that you never cancel, pay, buy or
contact a vendor, and that a human takes every action.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Where does spend show up (card, bank, cloud invoices, a subscription sheet)? Can you attach last
   month's exports now?
2. Who owns software or cloud spending, and who should own each vendor by default?
3. What counts as a spike, and what variance against budget matters: a percentage and an amount for
   each? (Defaults: spike up 20 percent and at least 200 in a month; variance 10 percent and 2,000 a
   month.) Attach this year's budget if there is one. Without one I compare with last month only.
4. How long before a renewal should I raise it, and which notice periods do you already know?
   (Default: brief at 60 days, alert at 90.)
5. Who receives the Monday report and at what hour? (Default: you, Mondays at 09:00.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/thresholds.md` as present-tense
rules, and `knowledge/forecast.md` from the budget if one was attached. Start `knowledge/vendors.md` with every recurring vendor the exports show, one line each; an
unknown owner is written as "no owner on record". Never write a card or account number.

## 5. Draft the first report now

Follow `playbooks/weekly-spend-report.md` on the exports, in the shape of
`knowledge/examples/spend-report.md`, labelled "First draft, not yet reviewed". Attach it to the task.
Nothing is cancelled, paid or sent.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will send you this report every Monday at 09:00, and a human acts on it." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
