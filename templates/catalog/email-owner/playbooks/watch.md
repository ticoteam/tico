# Watch

Routine `watch`, on the schedule in `knowledge/channel-playbook.md` ("What Watch checks"). Also run when a task asks.
Budget 30 minutes.

Outcome: every Watching task in Growth Monitoring with your channel tag checked against its range, the upkeep your
playbook allows done, any task whose number left its range moved to Slipping, and this week's KPI reading logged.
This routine's own task is then done.

The same text is shipped to every channel Owner. What is yours (your tag, your KPI, what you check, what you may do
by yourself) is in `knowledge/channel-playbook.md`. Read it first.

---

## 1. Read where things stand

    tico task show <id>
    tico pipeline show growth-monitoring
    tico task list --pipeline growth-monitoring --label <your channel tag> --status open --brief

Each Monitoring task says what the kept thing is (a link), its KPI and range, and the experiment it came from. A task
in Slipping is already being fixed in its own task: leave it to that task.

## 2. Check each Watching task

For each one, read its number as "What Watch checks" in your channel playbook says, from a dated source. Record on the
task, in one comment: the number, the period, the source and the range. A number you could not read is "not read"
with the reason, never a guess, and never out of range on its own: two weeks in a row not read is out of range.

- **In range.** Nothing more. Do not move it.
- **Out of range.** Move it to Slipping with the number, the range and the source in one line:

      tico task move <monitoring id> Slipping -m "<number> for <period> is outside <range> (<source>)" --version N

  That wakes you on the task. The fix is `playbooks/fix-a-slip.md`, in that run, not this one.

## 3. Do the upkeep your playbook allows

"Upkeep you may do by yourself" in your channel playbook lists what needs no person: keeping a kept series or flow
running, small repairs that put a kept thing back the way a person OK'd it. Do only what is on that list, within your
granted Tools. Where no Tool lets you make the change, file it for the person your playbook names, with the exact
change:

    tico task create --owner <person id> --label <your channel tag> --title "Apply: <the change>" --body "<exactly what to change, where, and why>"

Anything not on the list is an idea (`playbooks/find-ideas.md` says how to file one), never upkeep.

## 4. Log the KPI

Read your KPI for the period your playbook names and log it, with its period and source:

    tico kpi log <kpi id> <value> --period-start <date> --period-end <date> --source <system> --evidence "<link or note>"

No source, no reading: say so in the closing note instead.

## 5. Close

    tico task done <id> -m "..." --version N

The note: how many Watching tasks were checked, which moved to Slipping and why, the upkeep done or filed, the KPI
reading, and what could not be read. Finish even on a quiet week: an open routine task gets absorbed into the next run.
