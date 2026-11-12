# Weekly engineering summary

Schedule: Mondays at 09:00 team time (routine `weekly-engineering-summary`), once the routine is switched on. Budget 30 minutes.

Inputs: your pipelines' open tasks (`tico task list --status open`; each row has its stage and its Turn line), the snoozed
ones (`tico task list --snoozed only`), the tasks you were woken about this week (`tico task list --member me`), the open
pull requests (`gh pr list -R <repo> --state open`), `knowledge/pipeline.md` and last week's summary.

Outcome: one page for your manager on how the Engineering pipeline moved this week and what needs a person.

Done when: the summary is published, every task that stopped this week shows its decision, everything that waits on a person
names the person and since when, and the task is done with its headline.

1. Read where things stand: last week's summary, then the lists above. Check a doubtful task with `tico task show <id>`.
2. Write `reports/YYYY-MM-DD-engineering-summary.md` in this order, as in `knowledge/examples/engineering-summary.md`:
   - the headline: one line on how the pipeline moved, and the one decision that matters, if any;
   - tasks per stage, with the Late count;
   - each task that stopped this week, with why and the decision made;
   - tasks on ice;
   - what waits on a person, and since when;
   - the five pull requests open longest, each with its link, its age and its state;
   - repeats worth a proposal: the same stop three times, with the tasks that showed it;
   - a hire proposal, only where a stage has nobody who can do its work;
   - what was not read.
3. A repeat or a hire is a proposal on a task to your manager, filed once: `tico task create --owner <your manager>
   --title "Proposal: <the change>" --body "<the tasks that showed it>"`. Never applied by you.
4. Publish with `tico file publish`, and finish the task with the headline, the path and what you could not read.

You decide nothing new here: a task that stopped already woke you when it stopped. Write only what the tasks and pull requests
say. A task you could not read is "not read", not clean. No person is named as the cause of a stop.
