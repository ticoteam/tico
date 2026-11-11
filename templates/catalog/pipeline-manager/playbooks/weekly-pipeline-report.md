# Weekly pipeline report

Runs Mondays at 09:00 team time (routine `weekly-pipeline-report`), and when a person asks for it on a task. Budget 20
minutes. The outcome is one page your manager can read in two minutes. Nothing is moved by this report.

## Steps
1. Read `knowledge/pipeline.md` and `knowledge/manager-practice.md`.
2. Read the open tasks: `tico task list --status open`. Each row has its stage and its Turn line; keep the rows in your
   pipeline's stages and check a doubtful one with `tico task show <id>`. Read the snoozed ones with
   `tico task list --snoozed only`. A task you could not read is "not read", not clean.
3. Read the tasks you were woken about this week: `tico task list --member me`, and the decision you made on each.
4. Write `reports/YYYY-MM-DD-pipeline.md` in this order: the headline in one line; tasks per stage; each task that
   stopped this week with why and what you decided; tasks on ice; what waits on a person, and since when; the repeats
   worth a proposal; and what was not read. Use `knowledge/examples/pipeline-report.md` for shape.
5. Publish it with `tico file publish reports/YYYY-MM-DD-pipeline.md --task <id>` and finish the routine's task with
   `tico task done <id> -m "<the headline>" --version N`. If one decision needs your manager, put it in the first line.

## Checks before you finish
- Every number says its window and the time it was read.
- Every stopped task names what was missing and the decision made.
- No person is named as the cause of a stop.
