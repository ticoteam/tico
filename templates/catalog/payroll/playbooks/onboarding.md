# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 25 minutes. The outcome is five recorded answers, a change summary for the next pay
run, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub team show

Check the attachments for a payroll register, a roster or HR export and timesheets, and which HR bots
exist to supply joiners and leavers. Do not ask for what these already show.

## 2. Introduce yourself in three lines

What you do (collect and check every payroll change before cut-off, and compare the register after the
run), that requested payroll changes use your Tools and sourced inputs, and pay stays with the named readers.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Who runs payroll, in which system, on what schedule?
2. When is the cut-off, and who supplies new hires and leavers, pay changes, hours, bonuses, commissions?
3. Can you attach the last payroll register and the current roster or HR export?
4. Who owns pay changes, bonuses and overtime, and how is each change recorded?
5. Who may see the summary? (Default: you and whoever enters payroll.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/payroll-calendar.md`
(the next six pay dates and cut-offs), `knowledge/approvals.md` and `knowledge/baseline.md` from the
register: headcount and gross by pay group, with the register date.

## 5. Produce the first result now

Follow `playbooks/pre-payroll-change-summary.md` for the next pay run. Write
`reports/YYYY-MM-DD-payroll-changes.md`, attach it to the task and label it "First draft, not yet
reviewed". Enter nothing.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub automation list` (if it shows off,
`hub automation update <id> --enable`) and tell the human in one line what it does: "I will check every Monday and write the full summary in each cut-off week." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub automation update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
